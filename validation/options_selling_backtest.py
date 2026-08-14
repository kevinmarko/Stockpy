"""
InvestYo Quant Platform - Synthetic VRP Iron Condor Backtest
================================================================
Real, daily strategy-return simulation for the "Volatility Premium Seller"
Pilot (``vrp-premium-selling``, ``pilots/catalog.py``), backing BOTH the
``STRATEGY_REGISTRY["vrp_premium_selling"]`` adapter's full-window return
series AND its ``stress_returns_fn`` for the 4-scenario options-selling
tail-risk gate (``validation/stress_scenarios.py``) — one implementation,
no duplicated simulation logic, per this module's own design.

HONESTY CONTRACT (read before touching thresholds or the P&L math)
--------------------------------------------------------------------
No historical options-chain data exists ANYWHERE in this codebase — the
live ``True_IVR``/chain-derived IV (``volatility/iv_engine.py``) only
accumulates going forward from when the live pipeline first runs. This
module therefore does NOT replay the live signal's literal inputs; it
constructs an honestly-labeled, real-underlying-price-driven PROXY,
following the exact same "narrower proxy, documented" precedent already
used by ``edge-garch``'s ``garch_vol_target`` backtest and
``forecast-aligned``'s bounded-window backtest (see
``scripts/refresh_validations.py``). Every simplifying assumption below is
listed explicitly, not buried:

1. **Proxy True_IVR**: ``TechnicalOptionsEngine.calculate_realized_vol_rank``
   — the rank of the GJR-GARCH day-ahead forecast within the trailing
   252-day rolling 20-day-realized-vol range. This is the SAME fallback
   tier ``technical_options_engine.py::build_premium_directive``'s own
   step 2 already uses in production whenever a real options chain isn't
   available (``IVR_Proxy``) — not a new invention for this backtest.
2. **Proxy VRP**: ``volatility.iv_engine.get_vrp(ticker, current_iv, garch_vol)``
   fed a LONG-horizon (60-day) realized vol as the ``current_iv`` argument
   (a smoother, longer-horizon stand-in for what an options market's IV
   tends to price) and the GJR-GARCH SHORT-horizon forecast as
   ``garch_vol`` — the same function the live pipeline calls, with two
   different-horizon realized-vol estimates substituted for a literal
   chain IV reading. A rising-then-mean-reverting vol regime (GARCH's
   forward point estimate below the smoother trailing level) is what
   produces a positive proxy VRP here.
3. **Real macro gating, VIX and CREDIT EVENT decoupled on purpose**: this
   module reuses real FRED history (``HistoricalStore.get_macro``) and
   constructs a genuine ``dto_models.MacroEconomicDTO`` per cycle-entry
   date — the same class the live pipeline uses, so there is zero drift
   risk from the real regime-classification rules. But VIX (`VIXCLS`, real
   coverage back to 1990) and CREDIT EVENT detection (driven by
   `BAMLH0A0HYM2`, real coverage only from **2023-08-08** — the SAME
   constraint already documented for `macro_regime_pit`) are deliberately
   NOT required together: requiring both would make every stress-test
   window before 2023 (OCT_2008, FEB_2018, MAR_2020) trivially return an
   all-zero/never-traded series — a FALSE PASS of the stress gate, not a
   genuine survival result. Instead, VIX gates for real across the full
   history, while a missing credit-spread reading defaults to "not a
   credit event" (matching `generate_strategy_pricing_matrix`'s own
   missing-macro-data default) rather than blocking the whole cycle — see
   `_reconstruct_macro_for_dates`'s own docstring for the full reasoning.
4. **SPY-only, ~21-trading-day rolling cycles**, matching the majority
   single-symbol-adapter convention (``rsi2_mean_reversion``,
   ``garch_vol_target``, ``sortino_drawdown``) and a monthly option-cycle
   DTE.
5. **Constant entry-sigma per cycle**: the GARCH forecast fixed at cycle
   entry is reused for every day's Black-Scholes mark-to-market within
   that cycle (no daily vol re-forecast) — a real simplification, not
   fabricated data.
6. **No bid/ask spread**: every leg is priced at its theoretical
   Black-Scholes mid; a live fill would be worse.
7. **Gross returns**: no transaction cost is applied here — the harness's
   own ``TieredCostModel`` applies cost on top via the adapter's declared
   ``turnover``, matching every other ``STRATEGY_REGISTRY`` entry.

Nothing here reimplements options pricing or strike selection — every
leg's strike and price comes from the REAL, already-existing
``technical_options_engine.OptionsPricingRecommender`` (the exact class the
live pipeline uses), reused as-is.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

from signals.vrp_premium_selling import (
    IVR_SELL_THRESHOLD,
    VIX_MAX_THRESHOLD,
    VRP_MIN_THRESHOLD,
)
from technical_options_engine import OptionsPricingRecommender, TechnicalOptionsEngine
from volatility.iv_engine import get_vrp

logger = logging.getLogger(__name__)

CYCLE_TRADING_DAYS = 21          # ~1 monthly option cycle
TARGET_DTE = 21                  # matches CYCLE_TRADING_DAYS
# >= 252 (realized-vol-rank window) + 22 (GARCH fit minimum) + margin, so
# every cycle's GARCH fit / IVR-rank computation is strictly causal from its
# very first day.
WARMUP_TRADING_DAYS = 280
LONG_TERM_VOL_WINDOW = 60        # trailing window for the proxy-VRP "IV" leg
# Close a cycle early if the mark-to-market loss exceeds this multiple of
# the credit received — a real, if simplified, risk control; matches the
# stop-loss framing in tests/test_stress_gate.py's own reference fixtures.
STOP_LOSS_CREDIT_MULTIPLE = 2.0
STOP_LOSS_DEBIT_RATIO = 0.50
STOP_LOSS_COVERED_CALL_RATIO = 0.10

_STRATEGY_MAP: Dict[str, str] = {
    "put_credit_spread": "Put Credit Spread",
    "put-credit-spread": "Put Credit Spread",
    "put_credit": "Put Credit Spread",
    "put credit spread": "Put Credit Spread",
    "call_credit_spread": "Call Credit Spread",
    "call-credit-spread": "Call Credit Spread",
    "call_credit": "Call Credit Spread",
    "call credit spread": "Call Credit Spread",
    "iron_condor": "Iron Condor",
    "iron-condor": "Iron Condor",
    "iron condor": "Iron Condor",
    "vrp_premium_selling": "Iron Condor",
    "vrp-premium-selling": "Iron Condor",
    "call_debit_spread": "Call Debit Spread",
    "call-debit-spread": "Call Debit Spread",
    "call_debit": "Call Debit Spread",
    "call debit spread": "Call Debit Spread",
    "put_debit_spread": "Put Debit Spread",
    "put-debit-spread": "Put Debit Spread",
    "put_debit": "Put Debit Spread",
    "put debit spread": "Put Debit Spread",
    "covered_call": "Covered Call",
    "covered-call": "Covered Call",
    "covered call": "Covered Call",
}


def _proxy_ohlcv(close: pd.Series) -> pd.DataFrame:
    """Wraps a real downloaded Close series into the proxy OHLCV shape
    ``TechnicalOptionsEngine``'s methods expect. Reuses the EXACT proxy
    convention ``scripts/refresh_validations.py::_ClosesOnlyDataEngine``
    already established for a closes-only adapter (real Close; a tight
    +/-0.1% band for High/Low; a flat 1e6 share count for Volume) — not a
    new simplification invented here.
    """
    c = close.dropna()
    return pd.DataFrame(
        {"Open": c, "High": c * 1.001, "Low": c * 0.999, "Close": c, "Volume": 1_000_000.0},
        index=c.index,
    )


def _asof_align(series: Optional[pd.Series], index: pd.DatetimeIndex) -> pd.Series:
    """Align *series* (arbitrary date frequency) onto *index* via
    ``pd.merge_asof(direction="backward")`` — never forward-looking. Empty
    input degrades to an all-NaN Series (CONSTRAINT #4). Mirrors
    ``scripts/refresh_validations.py::_asof_align`` exactly, duplicated
    locally (a ~12-line, self-contained helper) rather than imported, to
    avoid a circular import with that module (see module docstring point 3).
    """
    if series is None or series.empty:
        return pd.Series(np.nan, index=index)
    s = series.sort_index()
    aligned = pd.merge_asof(
        pd.DataFrame(index=index), s.rename("value").to_frame(),
        left_index=True, right_index=True, direction="backward",
    )
    aligned.index = index
    return aligned["value"]


# Safe, NON-triggering placeholder values for the three macro inputs this
# module does NOT actually need a real reading for (see the docstring
# below): below every one of MacroEconomicDTO's own RECESSION/CREDIT-EVENT/
# NEUTRAL thresholds, so a missing series can never spuriously fabricate a
# regime that didn't happen. Matches the SAME "unknown degrades to the safe
# default, not a fabricated worst case" posture
# generate_strategy_pricing_matrix's own `getattr(macro_dto, 'market_regime',
# 'RISK ON')` fallback already uses when macro_dto is missing entirely.
_SAFE_YIELD_CURVE = 1.0     # positive/not inverted
_SAFE_CREDIT_SPREAD = 4.0   # below both the 4.5 NEUTRAL and 6.0 CREDIT EVENT thresholds
_SAFE_SAHM = 0.0            # no recession signal


def _reconstruct_macro_for_dates(
    dates,
    vix: Optional[pd.Series],
    t10y2y: Optional[pd.Series],
    credit_spread: Optional[pd.Series],
    unrate: Optional[pd.Series],
) -> Dict[pd.Timestamp, object]:
    """Real ``dto_models.MacroEconomicDTO`` construction at each requested
    date, from real FRED series — the SAME classification logic the live
    ``MacroEconomicDTO`` class performs, just fed historical as-of values
    instead of today's (zero drift risk vs. live regime rules, since the
    class itself is reused unmodified).

    This module's own gate only ever reads TWO things off the constructed
    DTO: ``.vix`` (< 30) and ``.market_regime != "CREDIT EVENT"`` — never
    RECESSION/NEUTRAL/RISK ON, and never ``killSwitch``. Accordingly:

    * ``vix`` is REQUIRED — a date with no real VIX reading returns ``None``
      (gate closed, honestly unknown) rather than a fabricated volatility
      level. VIX has decades of real FRED coverage (`VIXCLS` back to 1990 in
      this platform's `HistoricalStore`), so this should be rare.
    * ``high_yield_oas`` (the ONLY input `market_regime`'s CREDIT-EVENT
      branch actually depends on) uses the real FRED series WHEN AVAILABLE,
      and a safe below-threshold placeholder (`_SAFE_CREDIT_SPREAD`) when
      not. **Real, documented v1 scope limitation, not a bug**: `BAMLH0A0HYM2`
      only has FRED history in this platform's `HistoricalStore` starting
      2023-08-08 (the SAME constraint already documented for
      `macro_regime_pit` in `scripts/refresh_validations.py`) — so
      CREDIT-EVENT detection is only real from 2023-08-08 onward; before
      that, this backtest cannot confirm OR rule out a credit event and
      defaults to NOT blocking (matching production's own missing-data
      default), rather than returning ``None``/gate-closed for the entire
      pre-2023 history — which would have made the OCT_2008/FEB_2018/
      MAR_2020 stress-test windows trivially "pass" by never trading at
      all, a FALSE PASS this module deliberately avoids. VIX still has full
      real coverage through all four stress windows, so the strategy is
      still genuinely exposed to (and, correctly, gated closed by) real VIX
      spikes during each of those events.
    * ``yield_curve_10y_2y``/``sahm_rule_indicator`` feed ONLY the RECESSION
      branch, which this module's gate never reads — real values are used
      when available (decades of coverage for both), a safe placeholder
      otherwise, purely so `MacroEconomicDTO` can always be constructed.
    """
    from dto_models import MacroEconomicDTO

    idx = pd.DatetimeIndex(dates)
    vix_d = _asof_align(vix, idx)
    yc_d = _asof_align(t10y2y, idx)
    oas_d = _asof_align(credit_spread, idx)

    if unrate is not None and not unrate.empty:
        ma3 = unrate.sort_index().rolling(window=3).mean()
        sahm = ma3 - ma3.rolling(window=12).min()
    else:
        sahm = pd.Series(dtype=float)
    sahm_d = _asof_align(sahm, idx)

    out: Dict[pd.Timestamp, object] = {}
    for date, yc, oas, sahm_val, vix_val in zip(idx, yc_d, oas_d, sahm_d, vix_d):
        if pd.isna(vix_val):
            out[date] = None
            continue
        out[date] = MacroEconomicDTO(
            yield_curve_10y_2y=float(yc) if not pd.isna(yc) else _SAFE_YIELD_CURVE,
            high_yield_oas=float(oas) if not pd.isna(oas) else _SAFE_CREDIT_SPREAD,
            inflation_rate=2.0,
            sahm_rule_indicator=float(sahm_val) if not pd.isna(sahm_val) else _SAFE_SAHM,
            vix_value=float(vix_val),
        )
    return out


def _download_spy_closes(start: str, end: str, ticker: str) -> pd.Series:
    """Standalone downloader for the ``stress_returns_fn`` call path, where
    no pre-fetched ``closes`` panel is available (a dated shock window like
    OCT_2008 falls outside whatever window the CLI's own ``--start``/
    ``--end`` requested). Extends the download window backward by
    ``WARMUP_TRADING_DAYS`` (in calendar-day terms, generously padded for
    weekends/holidays) so the very first requested date still has enough
    real trailing history for a causal GARCH fit — never forward.
    """
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        logger.warning("options_selling_backtest: yfinance unavailable: %s", exc)
        return pd.Series(dtype=float)

    warmup_start = (
        pd.Timestamp(start) - pd.Timedelta(days=int(WARMUP_TRADING_DAYS * 1.6))
    ).strftime("%Y-%m-%d")
    try:
        raw = yf.download(ticker, start=warmup_start, end=end, progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return pd.Series(dtype=float)
        close = raw["Close"]
        if isinstance(close, pd.DataFrame):
            close = close[ticker] if ticker in close.columns else close.iloc[:, 0]
        return close.dropna()
    except Exception as exc:  # noqa: BLE001
        logger.warning("options_selling_backtest: price download failed for %s: %s", ticker, exc)
        return pd.Series(dtype=float)


def simulate_options_strategy_returns(
    strategy_name: str,
    start: str,
    end: str,
    *,
    ticker: str = "SPY",
    closes: Optional[pd.Series] = None,
) -> pd.Series:
    """Real, daily strategy-return series for the specified options strategy
    over ``[start, end]`` (inclusive).

    Supports all options strategies generated by
    ``TechnicalOptionsEngine::OptionsPricingRecommender.generate_strategy_pricing_matrix``:
      * ``put_credit_spread`` / "Put Credit Spread"
      * ``call_credit_spread`` / "Call Credit Spread"
      * ``iron_condor`` / ``vrp_premium_selling`` / "Iron Condor"
      * ``call_debit_spread`` / "Call Debit Spread"
      * ``put_debit_spread`` / "Put Debit Spread"
      * ``covered_call`` / "Covered Call"

    Parameters
    ----------
    strategy_name : str
        Strategy name or identifier.
    start : str
        Start date string (YYYY-MM-DD).
    end : str
        End date string (YYYY-MM-DD).
    ticker : str
        Underlying ticker symbol (default: "SPY").
    closes : Optional[pd.Series]
        Pre-downloaded Close price series. If None, downloaded on demand.

    Returns
    -------
    pd.Series
        Daily returns series indexed by date.
    """
    from data.historical_store import HistoricalStore

    target_strategy = None
    if strategy_name:
        clean_name = strategy_name.lower().strip()
        target_strategy = _STRATEGY_MAP.get(clean_name.replace(" ", "_"), _STRATEGY_MAP.get(clean_name, strategy_name))
        if clean_name in ("all", "any", "dynamic", "pricing_matrix"):
            target_strategy = None

    if closes is None:
        closes = _download_spy_closes(start, end, ticker)
    if closes is None or closes.empty:
        return pd.Series(dtype=float)

    ohlcv = _proxy_ohlcv(closes)
    if ohlcv.empty:
        return pd.Series(dtype=float)

    requested_start = pd.Timestamp(start)
    requested_end = pd.Timestamp(end)
    in_window = ohlcv.index[(ohlcv.index >= requested_start) & (ohlcv.index <= requested_end)]
    if in_window.empty:
        return pd.Series(dtype=float)

    store = HistoricalStore()
    vix = store.get_macro("VIXCLS")
    t10y2y = store.get_macro("T10Y2Y")
    credit_spread = store.get_macro("BAMLH0A0HYM2")
    unrate = store.get_macro("UNRATE")

    toe = TechnicalOptionsEngine()

    first_pos = ohlcv.index.get_loc(in_window[0])
    n = len(ohlcv)
    daily_returns: Dict[pd.Timestamp, float] = {}

    pos = first_pos
    while pos < n and ohlcv.index[pos] <= requested_end:
        if pos < WARMUP_TRADING_DAYS:
            daily_returns[ohlcv.index[pos]] = 0.0
            pos += 1
            continue

        cycle_end_pos = min(pos + CYCLE_TRADING_DAYS, n)
        cycle_dates = ohlcv.index[pos:cycle_end_pos]

        trailing = ohlcv.iloc[: pos + 1]
        entry_spot = float(trailing["Close"].iloc[-1])
        entry_date = trailing.index[-1]

        try:
            garch_vol = toe.estimate_gjr_garch_volatility(trailing)
            ivr_proxy = toe.calculate_realized_vol_rank(trailing, garch_vol)
            long_term_returns = trailing["Close"].pct_change().dropna().tail(LONG_TERM_VOL_WINDOW)
            iv_proxy = (
                float(long_term_returns.std() * np.sqrt(252))
                if len(long_term_returns) >= LONG_TERM_VOL_WINDOW
                else float("nan")
            )
            vrp_proxy = get_vrp(ticker, current_iv=iv_proxy, garch_vol=garch_vol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("options_selling_backtest: vol estimate failed at %s: %s", entry_date, exc)
            garch_vol, ivr_proxy, vrp_proxy = float("nan"), float("nan"), float("nan")

        macro_dto = _reconstruct_macro_for_dates(
            [entry_date], vix, t10y2y, credit_spread, unrate
        ).get(entry_date)

        # Determine trend bias from trailing SMA50
        if len(trailing) >= 50:
            sma50 = float(trailing["Close"].tail(50).mean())
        else:
            sma50 = float(trailing["Close"].mean())

        if entry_spot > sma50 * 1.01:
            trend_bias = "Bullish"
        elif entry_spot < sma50 * 0.99:
            trend_bias = "Bearish"
        else:
            trend_bias = "Neutral"

        recommender = OptionsPricingRecommender(stock_price=entry_spot)
        directive = recommender.generate_strategy_pricing_matrix(
            true_ivr=float(ivr_proxy) if not pd.isna(ivr_proxy) else 0.0,
            current_iv=float(garch_vol) if not pd.isna(garch_vol) else 0.20,
            trend_bias=trend_bias,
            target_dte=TARGET_DTE,
            vrp=float(vrp_proxy) if not pd.isna(vrp_proxy) else None,
            macro_dto=macro_dto,
        )

        rec_strategy = directive.get("Strategy", "Cash")
        if (target_strategy is not None and rec_strategy != target_strategy) or rec_strategy == "Cash":
            for d in cycle_dates:
                daily_returns[d] = 0.0
            pos = cycle_end_pos
            continue

        legs = directive.get("Legs", [])
        net_premium = float(directive.get("Net_Premium", 0.0))
        sigma = float(garch_vol) if not pd.isna(garch_vol) and garch_vol > 0 else 0.20

        if rec_strategy == "Put Credit Spread":
            if len(legs) != 2 or net_premium <= 0.0:
                for d in cycle_dates:
                    daily_returns[d] = 0.0
                pos = cycle_end_pos
                continue
            k_short_put = float(legs[0]["Strike"])
            k_long_put = float(legs[1]["Strike"])
            strike_width = abs(k_short_put - k_long_put)
            max_risk = strike_width * 100.0 - net_premium * 100.0
            if max_risk <= 0.0:
                for d in cycle_dates:
                    daily_returns[d] = 0.0
                pos = cycle_end_pos
                continue

            cumulative_pnl = 0.0
            stop_triggered = False
            for i, d in enumerate(cycle_dates):
                if stop_triggered:
                    daily_returns[d] = 0.0
                    continue
                spot_t = float(ohlcv.loc[d, "Close"])
                days_remaining = max(TARGET_DTE - i, 1)
                T = days_remaining / 365.0
                leg_pricer = OptionsPricingRecommender(stock_price=spot_t)
                mtm_short = leg_pricer.black_scholes_pricing_and_greeks(k_short_put, T, sigma, "put")["Price"]
                mtm_long = leg_pricer.black_scholes_pricing_and_greeks(k_long_put, T, sigma, "put")["Price"]
                cost_to_close = mtm_short - mtm_long
                new_cumulative_pnl = (net_premium - cost_to_close) * 100.0
                daily_pnl = new_cumulative_pnl - cumulative_pnl
                daily_returns[d] = daily_pnl / max_risk
                cumulative_pnl = new_cumulative_pnl
                if -cumulative_pnl > STOP_LOSS_CREDIT_MULTIPLE * net_premium * 100.0:
                    stop_triggered = True

        elif rec_strategy == "Call Credit Spread":
            if len(legs) != 2 or net_premium <= 0.0:
                for d in cycle_dates:
                    daily_returns[d] = 0.0
                pos = cycle_end_pos
                continue
            k_short_call = float(legs[0]["Strike"])
            k_long_call = float(legs[1]["Strike"])
            strike_width = abs(k_long_call - k_short_call)
            max_risk = strike_width * 100.0 - net_premium * 100.0
            if max_risk <= 0.0:
                for d in cycle_dates:
                    daily_returns[d] = 0.0
                pos = cycle_end_pos
                continue

            cumulative_pnl = 0.0
            stop_triggered = False
            for i, d in enumerate(cycle_dates):
                if stop_triggered:
                    daily_returns[d] = 0.0
                    continue
                spot_t = float(ohlcv.loc[d, "Close"])
                days_remaining = max(TARGET_DTE - i, 1)
                T = days_remaining / 365.0
                leg_pricer = OptionsPricingRecommender(stock_price=spot_t)
                mtm_short = leg_pricer.black_scholes_pricing_and_greeks(k_short_call, T, sigma, "call")["Price"]
                mtm_long = leg_pricer.black_scholes_pricing_and_greeks(k_long_call, T, sigma, "call")["Price"]
                cost_to_close = mtm_short - mtm_long
                new_cumulative_pnl = (net_premium - cost_to_close) * 100.0
                daily_pnl = new_cumulative_pnl - cumulative_pnl
                daily_returns[d] = daily_pnl / max_risk
                cumulative_pnl = new_cumulative_pnl
                if -cumulative_pnl > STOP_LOSS_CREDIT_MULTIPLE * net_premium * 100.0:
                    stop_triggered = True

        elif rec_strategy == "Iron Condor":
            if len(legs) != 4 or net_premium <= 0.0:
                for d in cycle_dates:
                    daily_returns[d] = 0.0
                pos = cycle_end_pos
                continue
            k_short_put = float(legs[0]["Strike"])
            k_long_put = float(legs[1]["Strike"])
            k_short_call = float(legs[2]["Strike"])
            k_long_call = float(legs[3]["Strike"])
            put_width = abs(k_short_put - k_long_put)
            call_width = abs(k_long_call - k_short_call)
            max_risk = max(put_width, call_width) * 100.0 - net_premium * 100.0
            if max_risk <= 0.0:
                for d in cycle_dates:
                    daily_returns[d] = 0.0
                pos = cycle_end_pos
                continue

            cumulative_pnl = 0.0
            stop_triggered = False
            for i, d in enumerate(cycle_dates):
                if stop_triggered:
                    daily_returns[d] = 0.0
                    continue
                spot_t = float(ohlcv.loc[d, "Close"])
                days_remaining = max(TARGET_DTE - i, 1)
                T = days_remaining / 365.0
                leg_pricer = OptionsPricingRecommender(stock_price=spot_t)
                mtm_short_put = leg_pricer.black_scholes_pricing_and_greeks(k_short_put, T, sigma, "put")["Price"]
                mtm_long_put = leg_pricer.black_scholes_pricing_and_greeks(k_long_put, T, sigma, "put")["Price"]
                mtm_short_call = leg_pricer.black_scholes_pricing_and_greeks(k_short_call, T, sigma, "call")["Price"]
                mtm_long_call = leg_pricer.black_scholes_pricing_and_greeks(k_long_call, T, sigma, "call")["Price"]

                cost_to_close = (mtm_short_put - mtm_long_put) + (mtm_short_call - mtm_long_call)
                new_cumulative_pnl = (net_premium - cost_to_close) * 100.0
                daily_pnl = new_cumulative_pnl - cumulative_pnl
                daily_returns[d] = daily_pnl / max_risk
                cumulative_pnl = new_cumulative_pnl
                if -cumulative_pnl > STOP_LOSS_CREDIT_MULTIPLE * net_premium * 100.0:
                    stop_triggered = True

        elif rec_strategy == "Call Debit Spread":
            net_debit = abs(net_premium)
            if len(legs) != 2 or net_debit <= 0.0:
                for d in cycle_dates:
                    daily_returns[d] = 0.0
                pos = cycle_end_pos
                continue
            k_long_call = float(legs[0]["Strike"])
            k_short_call = float(legs[1]["Strike"])
            max_risk = net_debit * 100.0
            if max_risk <= 0.0:
                for d in cycle_dates:
                    daily_returns[d] = 0.0
                pos = cycle_end_pos
                continue

            cumulative_pnl = 0.0
            stop_triggered = False
            for i, d in enumerate(cycle_dates):
                if stop_triggered:
                    daily_returns[d] = 0.0
                    continue
                spot_t = float(ohlcv.loc[d, "Close"])
                days_remaining = max(TARGET_DTE - i, 1)
                T = days_remaining / 365.0
                leg_pricer = OptionsPricingRecommender(stock_price=spot_t)
                mtm_long = leg_pricer.black_scholes_pricing_and_greeks(k_long_call, T, sigma, "call")["Price"]
                mtm_short = leg_pricer.black_scholes_pricing_and_greeks(k_short_call, T, sigma, "call")["Price"]
                position_value = mtm_long - mtm_short
                new_cumulative_pnl = (position_value - net_debit) * 100.0
                daily_pnl = new_cumulative_pnl - cumulative_pnl
                daily_returns[d] = daily_pnl / max_risk
                cumulative_pnl = new_cumulative_pnl
                if -cumulative_pnl > STOP_LOSS_DEBIT_RATIO * max_risk:
                    stop_triggered = True

        elif rec_strategy == "Put Debit Spread":
            net_debit = abs(net_premium)
            if len(legs) != 2 or net_debit <= 0.0:
                for d in cycle_dates:
                    daily_returns[d] = 0.0
                pos = cycle_end_pos
                continue
            k_long_put = float(legs[0]["Strike"])
            k_short_put = float(legs[1]["Strike"])
            max_risk = net_debit * 100.0
            if max_risk <= 0.0:
                for d in cycle_dates:
                    daily_returns[d] = 0.0
                pos = cycle_end_pos
                continue

            cumulative_pnl = 0.0
            stop_triggered = False
            for i, d in enumerate(cycle_dates):
                if stop_triggered:
                    daily_returns[d] = 0.0
                    continue
                spot_t = float(ohlcv.loc[d, "Close"])
                days_remaining = max(TARGET_DTE - i, 1)
                T = days_remaining / 365.0
                leg_pricer = OptionsPricingRecommender(stock_price=spot_t)
                mtm_long = leg_pricer.black_scholes_pricing_and_greeks(k_long_put, T, sigma, "put")["Price"]
                mtm_short = leg_pricer.black_scholes_pricing_and_greeks(k_short_put, T, sigma, "put")["Price"]
                position_value = mtm_long - mtm_short
                new_cumulative_pnl = (position_value - net_debit) * 100.0
                daily_pnl = new_cumulative_pnl - cumulative_pnl
                daily_returns[d] = daily_pnl / max_risk
                cumulative_pnl = new_cumulative_pnl
                if -cumulative_pnl > STOP_LOSS_DEBIT_RATIO * max_risk:
                    stop_triggered = True

        elif rec_strategy == "Covered Call":
            if len(legs) != 1 or net_premium <= 0.0:
                for d in cycle_dates:
                    daily_returns[d] = 0.0
                pos = cycle_end_pos
                continue
            k_short_call = float(legs[0]["Strike"])
            max_risk = max((entry_spot - net_premium) * 100.0, entry_spot * 10.0)
            if max_risk <= 0.0:
                for d in cycle_dates:
                    daily_returns[d] = 0.0
                pos = cycle_end_pos
                continue

            cumulative_pnl = 0.0
            stop_triggered = False
            for i, d in enumerate(cycle_dates):
                if stop_triggered:
                    daily_returns[d] = 0.0
                    continue
                spot_t = float(ohlcv.loc[d, "Close"])
                days_remaining = max(TARGET_DTE - i, 1)
                T = days_remaining / 365.0
                leg_pricer = OptionsPricingRecommender(stock_price=spot_t)
                mtm_short = leg_pricer.black_scholes_pricing_and_greeks(k_short_call, T, sigma, "call")["Price"]
                stock_pnl = (spot_t - entry_spot) * 100.0
                short_pnl = (net_premium - mtm_short) * 100.0
                new_cumulative_pnl = stock_pnl + short_pnl
                daily_pnl = new_cumulative_pnl - cumulative_pnl
                daily_returns[d] = daily_pnl / max_risk
                cumulative_pnl = new_cumulative_pnl
                if -cumulative_pnl > STOP_LOSS_COVERED_CALL_RATIO * max_risk:
                    stop_triggered = True

        else:
            for d in cycle_dates:
                daily_returns[d] = 0.0

        pos = cycle_end_pos

    idx = pd.DatetimeIndex(sorted(daily_returns.keys()))
    return pd.Series([daily_returns[d] for d in idx], index=idx, dtype=float)


def simulate_put_credit_spread_returns(
    start: str,
    end: str,
    *,
    ticker: str = "SPY",
    closes: Optional[pd.Series] = None,
) -> pd.Series:
    """Convenience wrapper simulating Put Credit Spread returns."""
    return simulate_options_strategy_returns(
        "put_credit_spread", start, end, ticker=ticker, closes=closes
    )


def simulate_call_credit_spread_returns(
    start: str,
    end: str,
    *,
    ticker: str = "SPY",
    closes: Optional[pd.Series] = None,
) -> pd.Series:
    """Convenience wrapper simulating Call Credit Spread returns."""
    return simulate_options_strategy_returns(
        "call_credit_spread", start, end, ticker=ticker, closes=closes
    )


def simulate_vrp_iron_condor_returns(
    start: str,
    end: str,
    *,
    ticker: str = "SPY",
    closes: Optional[pd.Series] = None,
) -> pd.Series:
    """Convenience wrapper simulating VRP Iron Condor returns (backward compatible)."""
    return simulate_options_strategy_returns(
        "iron_condor", start, end, ticker=ticker, closes=closes
    )


def simulate_call_debit_spread_returns(
    start: str,
    end: str,
    *,
    ticker: str = "SPY",
    closes: Optional[pd.Series] = None,
) -> pd.Series:
    """Convenience wrapper simulating Call Debit Spread returns."""
    return simulate_options_strategy_returns(
        "call_debit_spread", start, end, ticker=ticker, closes=closes
    )


def simulate_put_debit_spread_returns(
    start: str,
    end: str,
    *,
    ticker: str = "SPY",
    closes: Optional[pd.Series] = None,
) -> pd.Series:
    """Convenience wrapper simulating Put Debit Spread returns."""
    return simulate_options_strategy_returns(
        "put_debit_spread", start, end, ticker=ticker, closes=closes
    )


def simulate_covered_call_returns(
    start: str,
    end: str,
    *,
    ticker: str = "SPY",
    closes: Optional[pd.Series] = None,
) -> pd.Series:
    """Convenience wrapper simulating Covered Call returns."""
    return simulate_options_strategy_returns(
        "covered_call", start, end, ticker=ticker, closes=closes
    )
