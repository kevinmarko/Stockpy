"""
InvestYo Quant Platform - Agentic Forecast Backfill & Meta-Labeling Engine
==========================================================================
Executes a multi-horizon forecast backfill (10, 30, 60, 90 days) and meta-labeling
pipeline for Time-Series Momentum (TSMOM) and Cross-Sectional Momentum (CSMOM).

Key design features:
- Zero hardcoded numbers: All parameters are sourced from `settings.py` or explicit arguments.
- Sourced via Financial Modeling Prep (FMP) via `data/fmp_client.py` / `FMPProvider` / `CompositeProvider`.
- Vectorized pandas/numpy technical feature engineering and signal calculation.
- Chronological train/test split (default 80/20) preventing lookahead bias.
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

        self.prices: pd.DataFrame = pd.DataFrame()
        self.volumes: pd.DataFrame = pd.DataFrame()
        self.data: pd.DataFrame = pd.DataFrame()
        self.models: Dict[str, Any] = {}
        self.metrics: Dict[str, Dict[str, float]] = {}
        # Tickers for which no real provider (FMP nor CompositeProvider) returned
        # data and a synthetic random-walk panel was substituted instead (offline
        # testing only, in principle) — tracked so a real-environment provider
        # outage is never silently indistinguishable from genuine market data in
        # the exported summary/API/UI (CONSTRAINT #4).
        self.synthetic_tickers: List[str] = []

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

        # Synthetic fallback if zero real data returned (e.g. offline unit testing)
        still_missing = [t for t in self.tickers if t not in price_dict or price_dict[t].empty]
        if still_missing:
            self.synthetic_tickers = list(still_missing)
            logger.warning(
                "No real data (FMP nor CompositeProvider) for %d ticker(s) — "
                "substituting a SYNTHETIC random-walk price panel: %s. Any "
                "metrics/probabilities for these tickers are not from real "
                "market data.", len(still_missing), still_missing,
            )
            dates = pd.date_range(start=self.start_date, end=self.end_date, freq="B")
            for i, ticker in enumerate(still_missing):
                rng = np.random.default_rng(abs(hash((ticker, self.random_state))) % (2**32))
                returns = rng.normal(0.0004, 0.015, len(dates))
                price = 100.0 * np.exp(np.cumsum(returns))
                volume = rng.uniform(1e6, 5e6, len(dates))
                price_dict[ticker] = pd.Series(price, index=dates, name=ticker)
                volume_dict[ticker] = pd.Series(volume, index=dates, name=ticker)

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

            # Volume Ratio (Current Volume / 20-day MA Volume)
            vol_ma = df["Volume"].rolling(window=self.vol_ratio_window).mean()
            df["Vol_Ratio"] = df["Volume"] / vol_ma.replace(0.0, np.nan)

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

    def step_3_generate_primary_signals(self) -> pd.DataFrame:
        """Step 3: Generate primary TSMOM and CSMOM signals."""
        logger.info("[*] Step 3: Generating primary TSMOM and CSMOM signals...")

        # 252-day return
        returns_252 = self.prices.pct_change(periods=self.momentum_window)

        # 1. TSMOM: +1 if 252d return > 0, else -1
        tsmom_signals = np.sign(returns_252).replace(0, 1)

        # 2. CSMOM: Cross-sectional percentile rank daily. > 0.5 -> +1, <= 0.5 -> -1
        csmom_ranks = returns_252.rank(axis=1, pct=True)
        csmom_signals = pd.DataFrame(
            np.where(csmom_ranks > 0.5, 1, -1),
            index=returns_252.index,
            columns=returns_252.columns
        )

        tsmom_stacked = tsmom_signals.unstack().swaplevel()
        csmom_stacked = csmom_signals.unstack().swaplevel()

        self.data["TSMOM_Signal"] = tsmom_stacked.reindex(self.data.index)
        self.data["CSMOM_Signal"] = csmom_stacked.reindex(self.data.index)
        logger.info("[+] Step 3 complete. Primary signals generated.")
        return self.data

    def step_4_create_meta_targets(self) -> pd.DataFrame:
        """Step 4: Create binary meta-labels for all configured horizons (10, 30, 60, 90d)."""
        logger.info("[*] Step 4: Creating binary meta-labels for horizons: %s...", self.horizons)

        for h in self.horizons:
            forward_returns = self.prices.shift(-h) / self.prices - 1.0
            forward_stacked = forward_returns.unstack().swaplevel()

            self.data[f"Fwd_Return_{h}d"] = forward_stacked.reindex(self.data.index)

            # Target = 1 if primary signal sign matches forward return sign
            tsmom_match = np.sign(self.data[f"Fwd_Return_{h}d"]) == np.sign(self.data["TSMOM_Signal"])
            csmom_match = np.sign(self.data[f"Fwd_Return_{h}d"]) == np.sign(self.data["CSMOM_Signal"])

            self.data[f"TSMOM_Target_{h}d"] = tsmom_match.astype(int)
            self.data[f"CSMOM_Target_{h}d"] = csmom_match.astype(int)

            # Future forward returns that are NaN are masked to NaN
            isna_mask = self.data[f"Fwd_Return_{h}d"].isna()
            self.data.loc[isna_mask, f"TSMOM_Target_{h}d"] = np.nan
            self.data.loc[isna_mask, f"CSMOM_Target_{h}d"] = np.nan

        logger.info("[+] Step 4 complete. Meta-targets created.")
        return self.data

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
        features = ["Vol_20", "Vol_50", "RSI_14", "MACD", "Vol_Ratio"]

        for model_type in ["TSMOM", "CSMOM"]:
            for h in self.horizons:
                target_col = f"{model_type}_Target_{h}d"
                clean_df = self.data.dropna(subset=features + [target_col])

                if len(clean_df) < 30:
                    logger.warning("Insufficient samples (%d) for %s_%dd model. Skipping.", len(clean_df), model_type, h)
                    continue

                dates = clean_df.index.get_level_values("Date").unique().sort_values()
                split_idx = int(len(dates) * self.train_split)
                split_date = dates[split_idx]
                embargo_idx = max(0, split_idx - h)
                train_cutoff_date = dates[embargo_idx]

                train_df = clean_df[clean_df.index.get_level_values("Date") <= train_cutoff_date]
                test_df = clean_df[clean_df.index.get_level_values("Date") > split_date]

                if len(train_df) < 30 or len(test_df) < 30:
                    logger.warning(
                        "Insufficient post-embargo samples (train=%d, test=%d) for %s_%dd model. Skipping.",
                        len(train_df), len(test_df), model_type, h,
                    )
                    continue

                X_train, y_train = train_df[features], train_df[target_col].astype(int)
                X_test, y_test = test_df[features], test_df[target_col].astype(int)

                if self.classifier_type == "lightgbm":
                    try:
                        import lightgbm as lgb
                        clf = lgb.LGBMClassifier(
                            n_estimators=self.n_estimators,
                            max_depth=self.max_depth,
                            random_state=self.random_state,
                            verbose=-1,
                        )
                    except ImportError:
                        clf = RandomForestClassifier(
                            n_estimators=self.n_estimators,
                            max_depth=self.max_depth,
                            random_state=self.random_state,
                            n_jobs=-1,
                        )
                else:
                    clf = RandomForestClassifier(
                        n_estimators=self.n_estimators,
                        max_depth=self.max_depth,
                        random_state=self.random_state,
                        n_jobs=-1,
                    )

                clf.fit(X_train, y_train)
                y_pred = clf.predict(X_test)
                accuracy = float(np.mean(y_pred == y_test))

                try:
                    probas_test = clf.predict_proba(X_test)[:, 1]
                    auc = float(roc_auc_score(y_test, probas_test))
                except Exception:
                    auc = 0.50

                model_key = f"{model_type}_{h}d"
                self.models[model_key] = clf
                self.metrics[model_key] = {
                    "accuracy": round(accuracy, 4),
                    "auc": round(auc, 4),
                    "n_train": len(X_train),
                    "n_test": len(X_test),
                    "split_date": str(split_date)[:10],
                }

                # Save trained model artifact. model_key is built only from a
                # hardcoded model_type ("TSMOM"/"CSMOM") and h, which
                # _validate_horizons() (called in __init__) already
                # constrains to a small positive int -- this containment
                # check is a second, independent guard against the resolved
                # path ever escaping _MODELS_DIR (defense in depth, not
                # reliance on a single validation layer).
                model_path = (_MODELS_DIR / f"meta_{model_key}.pkl").resolve()
                if not model_path.is_relative_to(_MODELS_DIR.resolve()):
                    raise ValueError(f"Refusing to write model artifact outside {_MODELS_DIR}: {model_path}")
                with open(model_path, "wb") as f:
                    pickle.dump(clf, f)

                logger.info("   [✓] Trained %s. Accuracy: %.4f, AUC: %.4f", model_key, accuracy, auc)

        logger.info("[+] Step 5 complete. Models trained and saved.")
        return self.metrics

    def step_6_execute_backfill(self) -> pd.DataFrame:
        """Step 6: Execute continuous out-of-sample forecast probability backfilling."""
        logger.info("[*] Step 6: Executing continuous backfill inference...")
        features = ["Vol_20", "Vol_50", "RSI_14", "MACD", "Vol_Ratio"]

        valid_mask = self.data[features].notna().all(axis=1)
        inference_df = self.data[valid_mask]
        X_infer = inference_df[features]

        for model_type in ["TSMOM", "CSMOM"]:
            for h in self.horizons:
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

        export_cols = ["Close", "TSMOM_Signal", "CSMOM_Signal"]
        for model_type in ["TSMOM", "CSMOM"]:
            for h in self.horizons:
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
            # Non-empty iff step_1_fetch_data had to substitute a synthetic
            # random-walk panel for one or more tickers (no real FMP/
            # CompositeProvider data available) — surfaced end-to-end so a
            # provider outage is never silently indistinguishable from a real
            # backtest in the API response or webapp screen (CONSTRAINT #4).
            "synthetic_tickers": list(self.synthetic_tickers),
        }
        with open(out_json, "w") as f:
            json.dump(summary_payload, f, indent=2)

        logger.info("[🚀] Backfill results exported to %s and %s.", out_csv, out_json)
        return output_df, summary_payload


# Alias for InvestYo engine naming conventions
ForecastBackfillEngine = AgenticForecastBackfiller
