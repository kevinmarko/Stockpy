"""
InvestYo Quant Platform - Agentic Forecast Backfill & Meta-Labeling Engine
==========================================================================
Executes a multi-horizon forecast backfill (default 10, 30, 60, 90 days) and
meta-labeling pipeline for every ``SignalModule`` registered in
``signals.registry.global_registry`` (dynamic, not hardcoded to any specific
strategy) that (a) has all of its ``required_features`` present in the
technical DataFrame this engine computes and (b) declares a non-empty
``meta_label_features`` class attribute.

Per-strategy feature/horizon schemas: a module opts into meta-labeling by
declaring ``meta_label_features: List[str]`` (which of the engine's computed
technical columns it trains/infers on) and, optionally, ``meta_label_horizons:
List[int]`` (defaults to ``SignalModule``'s base-class default, currently
[10, 30, 60, 90], when a module doesn't override it) on the class itself —
see ``signals/base.py``. This engine reads both dynamically (steps 3-6 below)
instead of special-casing any one strategy's feature list or horizon set, so
a new SignalModule gains meta-labeling support just by declaring these two
attributes, with no change needed here.

Not every registered SignalModule is reachable from this pipeline. Two
notable, deliberate exclusions: pairs-trading (``signals/pairs_trading.py``)
operates on a *pair* of price series and produces its own multi-column
output (spread, z-score, hedge ratio, ...) — it is a plain function, not a
``SignalModule`` subclass, and is not registered in ``global_registry`` at
all (see that module's own docstring: "Advisory analytics only — not wired
into the per-ticker SignalAggregator"). Options-selling directives
(``technical_options_engine.py``) are likewise not a per-ticker
``SignalModule``. Wiring either into this per-ticker-row pipeline would need
its own pair-selection / contract-selection plumbing, not just a features
list — a real follow-up, not something a class-attribute declaration alone
can cover.

Key design features:
- Zero hardcoded numbers: All parameters are sourced from `settings.py` or explicit arguments.
- Sourced via Financial Modeling Prep (FMP) via `data/fmp_client.py` / `FMPProvider` / `CompositeProvider`.
- Vectorized pandas/numpy technical feature engineering and signal calculation.
- Combinatorial Purged Cross-Validation (`validation/purged_cv.py`) for the reported OOS accuracy/AUC.
- Out-of-sample forecast confidence probability backfilling.
- Model persistence to `ml/models/meta_<model>_<horizon>d.pkl`.
"""

from __future__ import annotations

import json
import logging
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, roc_auc_score

from settings import settings

logger = logging.getLogger("ML.ForecastBackfill")

_MODELS_DIR = Path(__file__).parent / "models"
_MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Upper bound is arbitrary (10 years of trading days) -- the point is just to
# constrain `h` to a small positive integer before it is ever interpolated
# into a model filename (`f"meta_{model_type}_{h}d.pkl"`) that gets opened
# for writing. `AgenticForecastBackfiller` is reachable from an HTTP request
# body (api/pilots_api.py's POST /pilots/forecast_backfill/run), which
# already validates this at the Pydantic layer -- this is a second,
# independent check (never trust a single validation layer) that also covers
# every other caller (the CLI script, tests, future callers).
_MAX_HORIZON_DAYS = 3650


def _validate_horizons(horizons: List[int]) -> List[int]:
    for h in horizons:
        if isinstance(h, bool) or not isinstance(h, int) or not (0 < h <= _MAX_HORIZON_DAYS):
            raise ValueError(
                f"forecast horizon must be a positive integer (days) <= {_MAX_HORIZON_DAYS}, got {h!r}"
            )
    return list(horizons)


class AgenticForecastBackfiller:
    """Multi-horizon forecast backfilling and meta-labeling pipeline engine."""

    def __init__(
        self,
        tickers: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        horizons: Optional[List[int]] = None,
        momentum_window: Optional[int] = None,
        vol_short_window: Optional[int] = None,
        vol_long_window: Optional[int] = None,
        rsi_window: Optional[int] = None,
        macd_fast: Optional[int] = None,
        macd_slow: Optional[int] = None,
        vol_ratio_window: Optional[int] = None,
        train_split: Optional[float] = None,
        n_estimators: Optional[int] = None,
        max_depth: Optional[int] = None,
        random_state: Optional[int] = None,
        classifier_type: Optional[str] = None,
        use_fmp: bool = True,
        strategy_ids: Optional[List[str]] = None,
        theta_c: Optional[float] = None,
    ):
        """Initialize backfill pipeline with parameters sourced from settings.py defaults."""
        self.tickers = tickers or settings.DEFAULT_TICKERS or ["AAPL", "MSFT", "AMZN", "NVDA", "JPM", "JNJ", "XOM", "WMT"]
        self.end_date = end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if start_date:
            self.start_date = start_date
        else:
            lookback_years = getattr(settings, "FORECAST_BACKFILL_LOOKBACK_YEARS", 4)
            self.start_date = (
                pd.Timestamp(self.end_date) - pd.DateOffset(years=lookback_years)
            ).strftime("%Y-%m-%d")
        self.horizons = _validate_horizons(
            horizons or getattr(settings, "FORECAST_BACKFILL_HORIZONS", [10, 30, 60, 90])
        )
        self.momentum_window = momentum_window or getattr(settings, "FORECAST_BACKFILL_MOMENTUM_WINDOW", 252)
        self.vol_short_window = vol_short_window or getattr(settings, "FORECAST_BACKFILL_VOL_SHORT_WINDOW", 20)
        self.vol_long_window = vol_long_window or getattr(settings, "FORECAST_BACKFILL_VOL_LONG_WINDOW", 50)
        self.rsi_window = rsi_window or getattr(settings, "FORECAST_BACKFILL_RSI_WINDOW", 14)
        self.macd_fast = macd_fast or getattr(settings, "FORECAST_BACKFILL_MACD_FAST", 12)
        self.macd_slow = macd_slow or getattr(settings, "FORECAST_BACKFILL_MACD_SLOW", 26)
        self.vol_ratio_window = vol_ratio_window or getattr(settings, "FORECAST_BACKFILL_VOL_RATIO_WINDOW", 20)
        self.train_split = train_split if train_split is not None else getattr(settings, "FORECAST_BACKFILL_TRAIN_SPLIT", 0.80)
        self.n_estimators = n_estimators or getattr(settings, "FORECAST_BACKFILL_N_ESTIMATORS", 100)
        self.max_depth = max_depth or getattr(settings, "FORECAST_BACKFILL_MAX_DEPTH", 5)
        self.random_state = random_state or getattr(settings, "FORECAST_BACKFILL_RANDOM_STATE", 42)
        self.classifier_type = (classifier_type or getattr(settings, "FORECAST_BACKFILL_CLASSIFIER_TYPE", "random_forest")).lower()
        self.use_fmp = use_fmp
        self.strategy_ids = strategy_ids
        self.theta_c = theta_c if theta_c is not None else getattr(settings, "META_LABEL_MIN_CONFIDENCE", 0.5)

        self.prices: pd.DataFrame = pd.DataFrame()
        self.volumes: pd.DataFrame = pd.DataFrame()
        self.data: pd.DataFrame = pd.DataFrame()
        self.models: Dict[str, Any] = {}
        self.metrics: Dict[str, Dict[str, float]] = {}
        # Tickers for which no real provider (FMP nor CompositeProvider) returned
        # data. They are dropped from the run and recorded via the 3-strike rule.
        self.dropped_tickers: List[str] = []

    def step_1_fetch_data(self) -> pd.DataFrame:
        """Step 1: Fetch daily OHLCV price and volume data using FMP or fallback providers."""
        logger.info("[*] Step 1: Fetching historical data for %d tickers...", len(self.tickers))
        price_dict: Dict[str, pd.Series] = {}
        volume_dict: Dict[str, pd.Series] = {}

        # Sourcing via FMP Provider
        if self.use_fmp:
            try:
                from data import fmp_client
                from data.fmp_client import FMPUnavailable

                for ticker in self.tickers:
                    try:
                        payload = fmp_client.historical_eod(
                            ticker,
                            variant="dividend-adjusted",
                            from_date=self.start_date,
                            to_date=self.end_date,
                        )
                        if payload and isinstance(payload, list):
                            df_t = pd.DataFrame(payload)
                            if "date" in df_t.columns and "adjClose" in df_t.columns:
                                df_t["date"] = pd.to_datetime(df_t["date"])
                                df_t.set_index("date", inplace=True)
                                df_t.sort_index(inplace=True)
                                price_dict[ticker] = df_t["adjClose"].rename(ticker)
                                vol_col = "volume" if "volume" in df_t.columns else "unadjustedVolume"
                                if vol_col in df_t.columns:
                                    volume_dict[ticker] = df_t[vol_col].rename(ticker)
                    except Exception as exc:
                        logger.warning("FMP fetch failed for %s: %s", ticker, exc)
            except Exception as exc:
                logger.warning("FMP client unavailable (%s). Falling back to CompositeProvider/Store.", exc)

        # Fallback to CompositeProvider / HistoricalStore if FMP returned partial/no data
        missing_tickers = [t for t in self.tickers if t not in price_dict or price_dict[t].empty]
        if missing_tickers:
            try:
                from data.market_data import CompositeProvider, MarketDataError
                provider = CompositeProvider()
                for ticker in missing_tickers:
                    try:
                        bars = provider.get_intraday_bars(ticker, lookback_days=3000, interval="1d")
                        if isinstance(bars, pd.DataFrame) and not bars.empty and "Close" in bars.columns:
                            price_dict[ticker] = bars["Close"].rename(ticker)
                            if "Volume" in bars.columns:
                                volume_dict[ticker] = bars["Volume"].rename(ticker)
                    except MarketDataError as exc:
                        logger.warning("CompositeProvider intraday/daily bars failed for %s: %s", ticker, exc)
            except Exception as exc:
                logger.warning("CompositeProvider fallback failed: %s", exc)

        # Fallback if zero real data returned
        still_missing = [t for t in self.tickers if t not in price_dict or price_dict[t].empty]

        # Record fetch outcomes for the 3-strike permanent-removal rule
        # (pilots.watchlist_writer.record_fetch_failures) unconditionally --
        # not only when this run had a miss -- so a ticker that succeeds
        # resets its strike counter to zero even on a run where every OTHER
        # ticker failed. Without this, "3 consecutive failures" silently
        # degrades into "3 failures ever", since a stale strike from weeks
        # ago would never be cleared by a later success.
        succeeded = [t for t in self.tickers if t not in still_missing]
        try:
            from pilots.watchlist_writer import record_fetch_failures
            permanently_removed = record_fetch_failures(still_missing, succeeded_symbols=succeeded)
            if permanently_removed:
                logger.warning(
                    "Permanently removed %d ticker(s) from watchlist.txt due to 3 consecutive failures: %s",
                    len(permanently_removed), permanently_removed,
                )
        except Exception as exc:
            logger.warning("Failed to record fetch failures or update watchlist.txt: %s", exc)

        if still_missing:
            self.dropped_tickers = list(still_missing)
            logger.warning(
                "No real data (FMP nor CompositeProvider) for %d ticker(s) — "
                "dropping them from the current run: %s.",
                len(still_missing), still_missing,
            )
            for t in still_missing:
                if t in self.tickers:
                    self.tickers.remove(t)

        self.prices = pd.DataFrame(price_dict).dropna(how="all")
        self.volumes = pd.DataFrame(volume_dict).dropna(how="all")
        logger.info("[+] Step 1 complete. Prices shape: %s", self.prices.shape)
        return self.prices

    def step_2_calculate_technical_features(self) -> pd.DataFrame:
        """Step 2: Vectorized contextual technical feature engineering."""
        logger.info("[*] Step 2: Calculating technical features...")
        features_list: List[pd.DataFrame] = []

        for ticker in self.prices.columns:
            if ticker not in self.volumes.columns:
                continue
            df = pd.DataFrame({
                "Close": self.prices[ticker],
                "Volume": self.volumes[ticker]
            }).dropna()

            if len(df) < max(self.momentum_window, self.vol_long_window):
                continue

            # Daily return
            df["Return"] = df["Close"].pct_change()

            # Volatility (Short & Long rolling standard deviation annualized)
            df["Vol_20"] = df["Return"].rolling(window=self.vol_short_window).std() * np.sqrt(252)
            df["Vol_50"] = df["Return"].rolling(window=self.vol_long_window).std() * np.sqrt(252)

            # RSI (14-day default)
            delta = df["Close"].diff()
            gain = (delta.where(delta > 0, 0.0)).rolling(window=self.rsi_window).mean()
            loss = (-delta.where(delta < 0, 0.0)).rolling(window=self.rsi_window).mean()
            rs = gain / (loss.replace(0.0, np.nan))
            df["RSI_14"] = 100.0 - (100.0 / (1.0 + rs))

            # MACD (12, 26 default)
            ema_fast = df["Close"].ewm(span=self.macd_fast, adjust=False).mean()
            ema_slow = df["Close"].ewm(span=self.macd_slow, adjust=False).mean()
            df["MACD"] = ema_fast - ema_slow
            df["MACD_Line"] = df["MACD"]
            df["MACD_Signal"] = df["MACD_Line"].ewm(span=9, adjust=False).mean()

            # Volume Ratio (Current Volume / 20-day MA Volume)
            vol_ma = df["Volume"].rolling(window=self.vol_ratio_window).mean()
            df["Vol_Ratio"] = df["Volume"] / vol_ma.replace(0.0, np.nan)

            # Additional features for various signals
            df["ROC_12M"] = df["Close"].shift(1) / df["Close"].shift(253) - 1.0
            df["ROC_6M"] = df["Close"].shift(1) / df["Close"].shift(127) - 1.0
            daily_returns = df["Close"].pct_change().shift(1)
            ewma_var = daily_returns.pow(2).ewm(alpha=0.06, adjust=False).mean()
            df["GARCH_Vol"] = np.sqrt(ewma_var * 252.0)
            df["SMA_5"] = df["Close"].rolling(5).mean()
            df["SMA_200"] = df["Close"].rolling(200).mean()
            delta = df["Close"].diff()
            gain_2 = (delta.where(delta > 0, 0.0)).rolling(window=2).mean()
            loss_2 = (-delta.where(delta < 0, 0.0)).rolling(window=2).mean()
            df["RSI_2"] = 100.0 - (100.0 / (1.0 + gain_2 / loss_2.replace(0.0, np.nan)))

            df["Ticker"] = ticker
            features_list.append(df)

        if not features_list:
            raise ValueError("Insufficient history across tickers to compute technical features.")

        concat_df = pd.concat(features_list).reset_index()
        date_col = "index" if "index" in concat_df.columns else "date" if "date" in concat_df.columns else concat_df.columns[0]
        concat_df.rename(columns={date_col: "Date"}, inplace=True)
        concat_df.set_index(["Date", "Ticker"], inplace=True)
        self.data = concat_df
        logger.info("[+] Step 2 complete. Features dataset shape: %s", self.data.shape)
        return self.data

    def _compute_xsec_12_1m_wide(self, skip_days: int = 22, lookback_days: int = 252) -> pd.DataFrame:
        """Cross-sectional 12-1m momentum return, at EVERY historical date.

        Mirrors ``main_orchestrator.py::compute_xsec_momentum_ranks``'s
        formula (``r = price[t-skip_days] / price[t-lookback_days] - 1``,
        default skip=22≈1 month, lookback=252≈12 months) exactly, but
        generalized from a single current-row snapshot to a full,
        vectorized, per-date series via ``shift`` -- lookahead-free by
        construction (every value at row t depends only on rows strictly
        before t), matching this codebase's causal-shift convention.

        Returns
        -------
        pd.DataFrame
            Wide (Date index x Ticker columns), same shape as ``self.prices``.
            NaN wherever either shifted price is unavailable (insufficient
            history for that ticker/date) -- never fabricated.
        """
        return self.prices.shift(skip_days) / self.prices.shift(lookback_days) - 1.0

    def _run_cross_sectional_module(
        self, module, xsec_return_wide: pd.DataFrame, make_context,
    ) -> pd.DataFrame:
        """Run a module whose ``pre_compute`` is overridden across every
        historical date in this backfill window, mirroring the real
        two-phase hook pattern (``signals.registry.SignalRegistry.
        run_pre_compute`` in production): once PER DATE, ``pre_compute()``
        ranks that day's cross-section from a per-date ``Symbol`` +
        ``XSec_12_1M`` universe slice, then ``compute()`` is called once per
        ticker active on that date, exactly as the module itself defines it
        -- this backfiller never bypasses or reimplements the module's own
        ranking logic, only supplies the per-date input its pre_compute
        contract expects.

        A fresh ``SignalContext`` is constructed per date (via
        ``make_context``) so ``context.xsec_percentile_ranks`` from one date
        can never leak into another's rank lookup.
        """
        scores = pd.Series(np.nan, index=self.data.index, dtype=float)
        confidences = pd.Series(0.0, index=self.data.index, dtype=float)
        explanations = pd.Series("", index=self.data.index, dtype=object)

        for date, group in self.data.groupby(level="Date"):
            if date not in xsec_return_wide.index:
                continue
            day_returns = xsec_return_wide.loc[date]
            # Ticker-indexed AND carrying an explicit "Symbol" column,
            # matching main_orchestrator.py's dashboard_df convention that
            # every pre_compute override in this codebase is actually
            # written against. This matters beyond style:
            # CrossSectionalMomentumSignal.pre_compute() re-derives its own
            # index from the "Symbol" column (`.set_index(SYMBOL_COL)`), so
            # it would tolerate a default RangeIndex fine -- but
            # LGBMRankerSignal.pre_compute()'s neutral-fallback branch keys
            # `context.lgbm_scores` off `universe_df.index` directly, with
            # no "Symbol" involved. A plain RangeIndex there would produce
            # integer-keyed scores that compute()'s ticker-string lookup can
            # never match, silently degrading every row to the neutral 0.5
            # rank for the life of the run.
            universe_df = pd.DataFrame(
                {
                    "Symbol": day_returns.index,
                    "XSec_12_1M": day_returns.values,
                },
                index=day_returns.index,
            )
            context = make_context()
            module.pre_compute(universe_df, context)

            for idx, row in group.iterrows():
                ticker = idx[1]  # self.data's index is (Date, Ticker)
                row_with_symbol = row.copy()
                row_with_symbol["Symbol"] = ticker
                out = module.compute(row_with_symbol, context)
                scores[idx] = out.score
                confidences[idx] = out.confidence
                explanations[idx] = out.explanation

        return pd.DataFrame(
            {
                "score": scores,
                "confidence": confidences,
                "explanation": explanations,
                "meta_label_proba": 1.0,
            },
            index=self.data.index,
        )

    def step_3_generate_primary_signals(self) -> pd.DataFrame:
        """Step 3: Generate primary signals dynamically from global_registry.

        Cross-sectional modules (those overriding ``pre_compute`` -- e.g.
        ``cross_sectional_momentum``; detected structurally via
        ``type(module).pre_compute is not SignalModule.pre_compute``, never
        by hardcoding a strategy name) go through
        ``_run_cross_sectional_module``, which replays the real two-phase
        hook pattern once per historical date (see that method's docstring
        and ``_compute_xsec_12_1m_wide`` for the lookahead-free per-date
        cross-sectional input). Every other module keeps the original
        single-shot ``compute_vectorized(self.data, context)`` call --
        unchanged, still the fast path for the common (non-cross-sectional)
        case.
        """
        logger.info("[*] Step 3: Generating primary signals from registry...")
        from signals.registry import global_registry
        from signals.base import SignalContext, SignalModule
        from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
        import numpy as np

        self.active_strategies = []

        # Build dummy context. Two independent construction bugs fixed here:
        # `date` is a required positional arg on MarketBarDTO (no default),
        # and SignalContext's field is `bar`, not `market_bar` -- both were
        # a guaranteed TypeError on every call, not a degraded-but-working
        # path (see signals/base.py's SignalContext dataclass).
        dummy_bar = MarketBarDTO(date=datetime.now(timezone.utc), ticker="DUMMY", open_price=0, high_price=0, low_price=0, close_price=0, volume=0)
        dummy_fundamentals = FundamentalDataDTO(ticker="DUMMY", pe_ratio=0, pb_ratio=0, dividend_yield=0, book_value=0, eps_trailing=0, dividend_growth_rate=0, payout_ratio=0, sector="N/A", company_name="DUMMY", market_cap=0)
        dummy_macro = MacroEconomicDTO(yield_curve_10y_2y=0, high_yield_oas=0, inflation_rate=0, sahm_rule_indicator=0, vix_value=15, hmm_risk_on_probability=None)

        def make_context() -> SignalContext:
            # A fresh SignalContext per call -- xsec_percentile_ranks (and
            # any other per-cycle field) must never be shared/mutated across
            # unrelated calls (different dates, or a cross-sectional module
            # vs. every other module in the same step_3 run).
            return SignalContext(bar=dummy_bar, fundamentals=dummy_fundamentals, macro=dummy_macro)

        context = make_context()
        xsec_return_wide: Optional[pd.DataFrame] = None  # computed lazily, at most once

        for name, module in global_registry.get_all().items():
            if self.strategy_ids and name not in self.strategy_ids:
                continue

            missing = [f for f in module.required_features if f not in self.data.columns]
            if missing:
                logger.debug(f"Skipping {name} due to missing features: {missing}")
                continue

            try:
                needs_precompute = type(module).pre_compute is not SignalModule.pre_compute
                if needs_precompute:
                    if xsec_return_wide is None:
                        xsec_return_wide = self._compute_xsec_12_1m_wide()
                    out_df = self._run_cross_sectional_module(module, xsec_return_wide, make_context)
                else:
                    out_df = module.compute_vectorized(self.data, context)

                if "score" in out_df.columns:
                    signal_col = np.sign(out_df["score"]).replace(0, np.nan)
                    self.data[f"{name}_Signal"] = signal_col

                    features_to_use = getattr(module, "meta_label_features", [])
                    for feat in features_to_use:
                        if feat in out_df.columns:
                            self.data[f"{name}_{feat}"] = out_df[feat]

                    self.active_strategies.append(name)
            except Exception as e:
                logger.warning(f"Error computing vectorized signal for {name}: {e}")

        logger.info(f"[+] Step 3 complete. Primary signals generated for: {self.active_strategies}")
        return self.data

    def step_4_create_meta_targets(self) -> pd.DataFrame:
        """Step 4: Create binary meta-labels for all configured horizons."""
        logger.info("[*] Step 4: Creating binary meta-labels for horizons...")

        from signals.registry import global_registry
        all_horizons = set(self.horizons)
        for name in self.active_strategies:
            module = global_registry.get(name)
            if module and getattr(module, "meta_label_horizons", []):
                all_horizons.update(module.meta_label_horizons)

        for h in all_horizons:
            forward_returns = self.prices.shift(-h) / self.prices - 1.0
            forward_stacked = forward_returns.unstack().swaplevel()

            self.data[f"Fwd_Return_{h}d"] = forward_stacked.reindex(self.data.index)

            for name in self.active_strategies:
                match = np.sign(self.data[f"Fwd_Return_{h}d"]) == np.sign(self.data[f"{name}_Signal"])
                self.data[f"{name}_Target_{h}d"] = match.astype(int)
                
                isna_mask = self.data[f"Fwd_Return_{h}d"].isna() | self.data[f"{name}_Signal"].isna()
                self.data.loc[isna_mask, f"{name}_Target_{h}d"] = np.nan

        logger.info("[+] Step 4 complete. Meta-targets created.")
        return self.data

    def _resolve_meta_features(self, model_type: str, features_raw: List[str]) -> List[str]:
        """Resolve a module's declared ``meta_label_features`` against the
        actual columns of ``self.data``.

        A feature is preferentially resolved to its strategy-namespaced
        column (``f"{model_type}_{feature}"``, written by step 3 for
        module-specific outputs, e.g. a signal's own z-score) and otherwise
        falls back to the shared/global technical column of the same name
        (written by step 2). Any feature that resolves to neither is
        dropped rather than raising -- a module free to declare features
        this engine doesn't (yet) compute, and simply not train/infer on
        the ones it can't resolve.

        Called identically from both step 5 (training) and step 6
        (inference) so the exact same column set/order is used for both --
        a classifier fit on one feature set and queried with a different
        one raises a hard sklearn feature-mismatch error at inference time.
        """
        resolved: List[str] = []
        for f in features_raw:
            namespaced = f"{model_type}_{f}"
            if namespaced in self.data.columns:
                resolved.append(namespaced)
            elif f in self.data.columns:
                resolved.append(f)
        return resolved

    def step_5_backtrain_meta_labelers(self) -> Dict[str, Any]:
        """Step 5: Train multi-horizon Meta-Labeling models on chronological train/test split.

        Each row's target label (``{model_type}_Target_{h}d``) is derived from a
        forward return that looks ``h`` trading days ahead of that row's date
        (see ``step_4_create_meta_targets``). A naive split at ``split_date``
        therefore leaks test-period price information into training: every
        training row within ``h`` days of the boundary has a label computed
        from data that falls inside the test window. This purges/embargoes the
        last ``h`` dates before the split out of the training set — the same
        overlapping-label leakage class this platform already guards against
        elsewhere (``validation/purged_cv.py``, the CNN-LSTM purged train/val
        split in ``forecasting_engine.py``) — so the reported OOS accuracy/AUC
        isn't inflated by boundary leakage.
        """
        logger.info("[*] Step 5: Training meta-labelers across models & horizons...")

        from validation.purged_cv import CombinatorialPurgedCV
        from signals.registry import global_registry

        for model_type in self.active_strategies:
            module = global_registry.get(model_type)
            if not module:
                continue
                
            features_raw = getattr(module, "meta_label_features", [])
            if not features_raw:
                logger.warning("Strategy %s has no meta_label_features defined, skipping training.", model_type)
                continue
                
            resolved_features = self._resolve_meta_features(model_type, features_raw)

            if not resolved_features:
                logger.warning("Could not resolve any features for %s", model_type)
                continue
                
            horizons_raw = getattr(module, "meta_label_horizons", None) or self.horizons

            for h in horizons_raw:
                target_col = f"{model_type}_Target_{h}d"
                
                if target_col not in self.data.columns:
                    continue
                    
                clean_df = self.data.dropna(subset=resolved_features + [target_col]).copy()
                
                # Ensure it's sorted by Date chronologically so CPCV blocks are contiguous in time
                clean_df.sort_index(level="Date", inplace=True)

                if len(clean_df) < 30:
                    logger.warning("Insufficient samples (%d) for %s_%dd model. Skipping.", len(clean_df), model_type, h)
                    continue
                    
                X = clean_df[resolved_features]
                y = clean_df[target_col].astype(int)
                
                # We need to drop MultiIndex for CombinatorialPurgedCV since it expects a single DateTimeIndex.
                # CombinatorialPurgedCV groups sequentially. We will pass a daily index for purging.
                dates_only = clean_df.index.get_level_values("Date")
                X_dates = pd.DataFrame(X.values, index=dates_only, columns=X.columns)

                # 1. Dynamic embargo percentage
                unique_dates = len(np.unique(dates_only))
                # embargo_pct = h / unique_dates ensures the index-based embargo size 
                # (n_samples * pct) roughly equals h days of rows.
                embargo_pct = min(0.10, h / unique_dates) if unique_dates > h else 0.01
                cv = CombinatorialPurgedCV(n_splits=10, n_test_splits=2, embargo_pct=embargo_pct)
                
                # 2. Dynamic purge windows (t1): event ends h days in the future
                t1 = pd.Series(dates_only + pd.Timedelta(days=h), index=dates_only)

                accuracies = []
                aucs = []
                try:
                    for train_idx, test_idx, _ in cv.split(X_dates, y, t1=t1):
                        if len(train_idx) < 10 or len(test_idx) < 10: continue
                        X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
                        X_test, y_test = X.iloc[test_idx], y.iloc[test_idx]

                        clf = RandomForestClassifier(
                            n_estimators=self.n_estimators, max_depth=self.max_depth,
                            random_state=self.random_state, n_jobs=-1,
                        )
                        clf.fit(X_train, y_train)
                        try:
                            probas = clf.predict_proba(X_test)[:, 1]
                            # Use theta_c threshold to determine predict success (1) vs block (0)
                            y_pred_custom = (probas >= self.theta_c).astype(int)
                            accuracies.append(float(np.mean(y_pred_custom == y_test)))
                            aucs.append(float(roc_auc_score(y_test, probas)))
                        except:
                            y_pred = clf.predict(X_test)
                            accuracies.append(float(np.mean(y_pred == y_test)))
                            aucs.append(0.50)
                except Exception as e:
                    logger.warning(f"CPCV failed for {model_type}_{h}d, skipping CV evaluation. {e}")
                
                accuracy = np.mean(accuracies) if accuracies else 0.50
                auc = np.mean(aucs) if aucs else 0.50

                # Train final model on ALL data
                clf_final = RandomForestClassifier(
                    n_estimators=self.n_estimators, max_depth=self.max_depth,
                    random_state=self.random_state, n_jobs=-1,
                )
                clf_final.fit(X, y)

                model_key = f"{model_type}_{h}d"
                self.models[model_key] = clf_final
                self.metrics[model_key] = {
                    "accuracy": round(accuracy, 4),
                    "auc": round(auc, 4),
                    "n_train": len(X),
                    "n_test": 0,
                    "split_date": "CPCV",
                    "is_active": model_type in ["timeseries_momentum", "cross_sectional_momentum", "rsi2_mean_reversion"],
                }

                # Save trained model artifact. model_key is built from
                # model_type (a signals.registry.global_registry strategy
                # name -- a hardcoded string on the SignalModule subclass
                # itself, never user input) and h, which _validate_horizons()
                # (called in __init__) already constrains to a small positive
                # int -- this containment check is a second, independent
                # guard against the resolved path ever escaping _MODELS_DIR
                # (defense in depth, not reliance on a single validation
                # layer).
                #
                # Persist clf_final (fit on ALL data above), not the CV loop's
                # per-fold `clf` local -- that variable holds whichever fold
                # trained last (an evaluation-only model deliberately withheld
                # from part of the data), and would be undefined entirely if
                # every CPCV fold was skipped/failed, since it is never
                # assigned outside the loop body.
                model_path = (_MODELS_DIR / f"meta_{model_key}.pkl").resolve()
                if not model_path.is_relative_to(_MODELS_DIR.resolve()):
                    raise ValueError(f"Refusing to write model artifact outside {_MODELS_DIR}: {model_path}")
                with open(model_path, "wb") as f:
                    pickle.dump(clf_final, f)

                logger.info("   [✓] Trained %s. Accuracy: %.4f, AUC: %.4f", model_key, accuracy, auc)

        logger.info("[+] Step 5 complete. Models trained and saved.")
        return self.metrics

    def step_6_execute_backfill(self) -> pd.DataFrame:
        """Step 6: Execute continuous out-of-sample forecast probability backfilling.

        Per-strategy feature set and horizons are resolved exactly the same
        way step_5 resolved them for training (same helper,
        `_resolve_meta_features`, same `meta_label_horizons` attribute) —
        a classifier queried with a different feature set than it was fit
        on raises a hard sklearn feature-name/count mismatch error, so this
        must never drift from step_5's resolution independently.
        """
        logger.info("[*] Step 6: Executing continuous backfill inference...")
        from signals.registry import global_registry

        for model_type in self.active_strategies:
            module = global_registry.get(model_type)
            features_raw = getattr(module, "meta_label_features", []) if module else []
            horizons_raw = (getattr(module, "meta_label_horizons", None) or self.horizons) if module else self.horizons
            resolved_features = self._resolve_meta_features(model_type, features_raw)

            if not resolved_features:
                # Nothing to infer on for this strategy -- step_5 skipped
                # training it for the same reason, so every probability
                # column is honestly NaN (CONSTRAINT #4) rather than
                # attempting inference against an empty feature frame.
                for h in horizons_raw:
                    self.data[f"{model_type}_Meta_Prob_{h}d"] = np.nan
                continue

            valid_mask = self.data[resolved_features].notna().all(axis=1)
            inference_df = self.data[valid_mask]
            X_infer = inference_df[resolved_features]

            for h in horizons_raw:
                model_key = f"{model_type}_{h}d"
                clf = self.models.get(model_key)

                prob_col = f"{model_type}_Meta_Prob_{h}d"
                if clf is not None and not X_infer.empty:
                    probabilities = clf.predict_proba(X_infer)[:, 1]
                    self.data.loc[valid_mask, prob_col] = probabilities
                else:
                    # No trained model for this horizon (insufficient samples) —
                    # NaN, never a fabricated confidence value (CONSTRAINT #4).
                    # export_results()'s dropna() then correctly excludes these
                    # rows from the exported CSV rather than reporting a fake
                    # 100%-confidence probability.
                    self.data[prob_col] = np.nan

        logger.info("[+] Step 6 complete. Forecast backfill executed.")
        return self.data

    def export_results(self, filename: str = "agentic_forecast_backfill.csv") -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """Export backfilled forecasts dataset and summary JSON metadata."""
        # settings.OUTPUT_DIR is read live here, not cached at module import
        # time, matching every other output-path reader in this codebase
        # (see pilots/dead_letter.py's docstring) -- so tests that
        # monkeypatch settings.OUTPUT_DIR to an isolated tmp_path (the
        # standard technique used throughout tests/) actually isolate this
        # write path too, instead of silently writing into the real,
        # operator-facing output/ directory that api/pilots_api.py's
        # GET /pilots/forecast_backfill serves from.
        output_dir = Path(settings.OUTPUT_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        out_csv = output_dir / filename
        out_json = output_dir / "agentic_forecast_summary.json"

        from signals.registry import global_registry

        # Restrict the exported columns to strategies that actually produced
        # at least one trained model, rather than every module that merely
        # satisfied step 3's required_features check (or even declared
        # meta_label_features -- see step_3_generate_primary_signals's KNOWN
        # GAP docstring for cross_sectional_momentum, which declares features
        # but never trains: its rank lookup misses on every row, so its
        # Signal column is unconditionally NaN). Several registered modules
        # pass step 3's check with an empty required_features list but
        # actually score off SignalContext fields this backfiller's dummy
        # context never populates (real dividend yield, sortino ratio, ...) --
        # their Signal column is likewise unconditionally NaN. Including any
        # such column in the mandatory `dropna()` subset below would zero out
        # every exported row the moment one such module lands in
        # self.active_strategies, silently producing an empty CSV/summary
        # regardless of how much real, trainable data timeseries_momentum et
        # al. actually have.
        def _has_trained_model(name: str) -> bool:
            module = global_registry.get(name)
            horizons_raw = (getattr(module, "meta_label_horizons", None) or self.horizons) if module else self.horizons
            return any(f"{name}_{h}d" in self.models for h in horizons_raw)

        trainable_strategies = [name for name in self.active_strategies if _has_trained_model(name)]

        export_cols = ["Close"] + [f"{m}_Signal" for m in trainable_strategies]
        for model_type in trainable_strategies:
            module = global_registry.get(model_type)
            horizons_raw = (getattr(module, "meta_label_horizons", None) or self.horizons) if module else self.horizons
            for h in horizons_raw:
                prob_col = f"{model_type}_Meta_Prob_{h}d"
                if prob_col in self.data.columns:
                    export_cols.append(prob_col)

        output_df = self.data[export_cols].dropna()
        output_df.to_csv(out_csv)

        summary_payload = {
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tickers": self.tickers,
            "horizons": self.horizons,
            "metrics": self.metrics,
            "total_rows": len(output_df),
            "csv_path": str(out_csv),
            # Non-empty iff step_1_fetch_data dropped tickers due to missing data.
            "dropped_tickers": list(self.dropped_tickers),
        }
        with open(out_json, "w") as f:
            json.dump(summary_payload, f, indent=2)

        logger.info("[🚀] Backfill results exported to %s and %s.", out_csv, out_json)
        return output_df, summary_payload


# Alias for InvestYo engine naming conventions
ForecastBackfillEngine = AgenticForecastBackfiller
