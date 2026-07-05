"""
main.py — InvestYo Quant Platform Entry Point
============================================
Clean sequential orchestrator that runs one full pipeline cycle per refresh.

Pipeline stages (per cycle)
---------------------------
  A. Account snapshot   — Robinhood positions via data.robinhood_portfolio,
                          at-most-once-per-day (daily JSON cache).
  B. Universe build     — held symbols ∪ WATCHLIST env var ∪ watchlist.txt
  C. Macro context      — FRED data + HMM second opinion (degrades to neutral
                          defaults when FRED_API_KEY is not configured)
  D. Context pre-compute— 12-1m cross-sectional momentum ranks + multifactor
                          composites built once for the full universe before
                          the per-symbol loop so advisory signals see real
                          cross-sectional data rather than the 0-score fallback
  E. Per-symbol evaluate— market data + advisory engine; dead-letter error
                          capture per symbol; never aborts the run
  F. Sheet sink         — write RunResult to Google Sheets (skipped when
                          credentials.json is absent)
  G. HTML report        — generate daily HTML report (skipped on IO error)
  H. Run summary        — structured log line; return RunResult

Two-tier refresh cadence
------------------------
  Account tier  : Robinhood snapshot fetched at most once per day via a daily
                  JSON cache at cache/account_snapshot.json.  Use --refresh-account
                  to force a fresh login on this launch; subsequent iterations of
                  --interval mode then resume normal caching.
  Market tier   : prices, bars, indicators, forecasts refreshed on every call to
                  run_once() from the live market-data provider.

NOTE — Double-fetch for pre-compute
  _fetch_bars_for_universe() fetches OHLCV history once for the full universe
  to build the cross-sectional pre-compute context.  engine.advisory.evaluate()
  will then fetch bars again per symbol internally (the market provider does not
  cache bars, only quotes).  This is a known tradeoff accepted for correctness:
  pre-compute requires the full universe before the per-symbol loop, so bars
  must be fetched upfront.  Future optimisation: extend MarketDataProvider with
  a bars cache or pass bars through to advisory via context_extras.
"""

# ---------------------------------------------------------------------------
# Auto-route to the project's .venv interpreter (must be first executable code)
# ---------------------------------------------------------------------------
import sys
import os
import subprocess as _sp

_venv_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin")
_venv_python = os.path.join(_venv_dir, "python3")
if not os.path.exists(_venv_python):
    _venv_python = os.path.join(_venv_dir, "python")
if os.path.exists(_venv_python) and os.path.realpath(sys.executable) != os.path.realpath(_venv_python):
    sys.exit(_sp.call([_venv_python] + sys.argv))

# ---------------------------------------------------------------------------
# Standard-library imports (after venv guarantee)
# ---------------------------------------------------------------------------
import argparse
import logging
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# python-dotenv import (loader is INVOKED inside main() and run_once(), NOT
# at module top)
# ---------------------------------------------------------------------------
# Why not at module top?
#   Module-top invocation would copy every .env value into os.environ on first
#   import, which pollutes the test session: tests/test_settings.py asserts
#   that constructing Settings() with no env returns the documented defaults,
#   and that assertion fails as soon as another test (e.g. test_run_once)
#   imports main and triggers the loader.
#
# Why call it inside both main() AND run_once()?
#   Production launchers (launch.command → `python main.py`) enter through
#   main(), so the loader fires there.  `make verify` and `verify.command`
#   import main and call `main.run_once()` directly without going through
#   main() — so run_once() must also call the loader as a defensive backstop.
#   load_dotenv() is idempotent and fast; the duplicate call is harmless.
#
# Why override=False?
#   So an explicit shell export ALWAYS wins over the .env file.
from dotenv import load_dotenv as _load_dotenv

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
import config
from data.market_data import get_provider, MarketDataProvider
from data.robinhood_portfolio import (
    AccountSnapshot,
    PortfolioPosition,
    fetch_account_snapshot,
)
from dto_models import FundamentalDataDTO, MacroEconomicDTO, MarketBarDTO
from engine.advisory import Recommendation, evaluate as advisory_evaluate
from settings import settings
from signals import global_registry
from signals.base import SignalContext

# ---------------------------------------------------------------------------
# Module-level logger (root logger is configured at runtime by setup_logging()
# inside main() — do NOT call logging.basicConfig() at module level here).
# ---------------------------------------------------------------------------
from alerting import notify, setup_logging, summarize_run
from execution.kill_switch import GlobalKillSwitch

logger = logging.getLogger("InvestYo.main")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WATCHLIST_FILE = "watchlist.txt"      # one ticker per line; '#' lines ignored
CREDENTIALS_FILE = "credentials.json" # Google Sheets service-account key
SHEET_NAME = "Stock Dashboard Py"
TAB_NAME_OUTPUT = "FidelityData_Automated"


# ---------------------------------------------------------------------------
# Per-process MacroEngine reuse (Task A4)
# ---------------------------------------------------------------------------
# _build_macro_dto() is called once per run_once() cycle. In --interval / agent
# loop mode, run_once() is called repeatedly WITHIN THE SAME PROCESS. The
# regime/hmm_regime.py HMMRegimeDetector.fit() gate (retrain_freq_days) is only
# meaningful if the SAME detector instance persists across those cycles --
# constructing a fresh MacroEngine (and therefore a fresh, never-fitted
# HMMRegimeDetector) every cycle makes the gate a no-op and forces a full
# EM refit every single cycle. This module-level cache keeps one MacroEngine
# (and its DataEngine) alive for the lifetime of the process, keyed by the
# FRED API key so a mid-process key rotation still gets a correctly-scoped
# engine instead of silently reusing one built for a stale key.
_MACRO_ENGINE_CACHE: Dict[str, Any] = {}


def _get_macro_engine(fred_key: str):
    """Return a process-lifetime MacroEngine for ``fred_key``, constructing it
    once and reusing it on subsequent calls so the HMMRegimeDetector's
    retrain_freq_days gate is honored across --interval / agent-loop cycles.

    A distinct cache entry per fred_key means rotating the key mid-process
    (rare, but handled) gets a fresh engine/detector rather than silently
    reusing one fit against the old key's data.
    """
    cached = _MACRO_ENGINE_CACHE.get(fred_key)
    if cached is not None:
        return cached

    from data_engine import DataEngine
    from macro_engine import MacroEngine

    de = DataEngine(fred_key)
    me = MacroEngine(data_engine=de)
    _MACRO_ENGINE_CACHE.clear()  # only one key's engine needs to live at a time
    _MACRO_ENGINE_CACHE[fred_key] = me
    return me


def _reset_macro_engine_cache() -> None:
    """Test-only helper: clears the process-lifetime MacroEngine cache so each
    test gets a fresh engine/detector instead of bleeding HMM fit state across
    tests."""
    _MACRO_ENGINE_CACHE.clear()


# ---------------------------------------------------------------------------
# RunResult — immutable container for one full pipeline cycle
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunResult:
    """Immutable result of one run_once() cycle.

    Attributes
    ----------
    snapshot : AccountSnapshot
        Robinhood account snapshot (may be stale; check snapshot.is_stale()).
    recommendations : list[Recommendation]
        Advisory results for every symbol that evaluated successfully.
    errors : list[dict]
        Dead-letter entries for each symbol that failed.  Each dict has keys:
        symbol, stage, error_type, message, timestamp (UTC ISO-8601).
    started_at : datetime  (UTC-aware)
    finished_at : datetime (UTC-aware)
    duration_seconds : float
    """

    snapshot: AccountSnapshot
    recommendations: List[Recommendation]
    errors: List[dict]
    started_at: datetime
    finished_at: datetime
    duration_seconds: float


# ---------------------------------------------------------------------------
# Universe helpers
# ---------------------------------------------------------------------------

def _load_watchlist() -> List[str]:
    """Return uppercase tickers from WATCHLIST env var or watchlist.txt.

    WATCHLIST env var (comma-separated) takes precedence over the file.
    Returns an empty list when neither source is configured.
    """
    env_val = os.environ.get("WATCHLIST", "").strip()
    if env_val:
        return [t.strip().upper() for t in env_val.split(",") if t.strip()]

    wl_path = Path(WATCHLIST_FILE)
    if wl_path.exists():
        tickers = [
            line.strip().upper()
            for line in wl_path.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        logger.info("Loaded %d tickers from %s.", len(tickers), WATCHLIST_FILE)
        return tickers

    return []


def _load_tickers_from_sheet2() -> List[str]:
    """Return tickers from Sheet2 column A of the Google Sheet.

    Used as a last-resort fallback when Robinhood is unavailable and no
    WATCHLIST / watchlist.txt is configured.  Silently returns [] when
    credentials.json is absent, Sheet2 doesn't exist, or any error occurs.
    """
    if not Path(CREDENTIALS_FILE).exists():
        return []
    try:
        import gspread  # type: ignore[import]

        gc = gspread.service_account(filename=CREDENTIALS_FILE)
        sh = gc.open(SHEET_NAME)
        ws = sh.worksheet("Sheet2")
        col_a = ws.col_values(1)  # 1-indexed; returns list of strings
        tickers = [v.strip().upper() for v in col_a if v.strip() and not v.strip().startswith("#")]
        logger.info("Loaded %d tickers from Google Sheet Sheet2 column A.", len(tickers))
        return tickers
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read Sheet2 ticker list: %s", exc)
        return []


def _build_universe(snapshot: AccountSnapshot) -> List[str]:
    """Return the evaluation universe: held symbols ∪ watchlist, deduped, sorted.

    Priority order when building the universe:
      1. Robinhood held positions (always included when available).
      2. WATCHLIST env var or watchlist.txt (always merged in when present).
      3. Google Sheet → Sheet2 column A (fallback only when 1 + 2 are both empty).
    """
    held = set(snapshot.positions.keys())
    watchlist = set(_load_watchlist())
    combined = held | watchlist

    if not combined:
        sheet2 = set(_load_tickers_from_sheet2())
        if sheet2:
            logger.info(
                "Using %d tickers from Sheet2 (Robinhood unavailable, no WATCHLIST configured).",
                len(sheet2),
            )
        combined = sheet2

    universe = sorted(combined)
    logger.info(
        "Universe: %d symbols (%d held, %d watchlist-only).",
        len(universe),
        len(held),
        len(combined - held),
    )
    return universe


# ---------------------------------------------------------------------------
# Macro context (FRED + HMM second opinion)
# ---------------------------------------------------------------------------

def _build_macro_dto() -> MacroEconomicDTO:
    """Fetch FRED macro data and build MacroEconomicDTO with HMM probability.

    Degrades gracefully to neutral defaults when FRED_API_KEY is absent or
    FRED is unreachable.  Never raises.
    """
    fred_key = os.environ.get("FRED_API_KEY", "").strip()
    if not fred_key:
        logger.info("FRED_API_KEY not configured; using neutral macro defaults.")
        return MacroEconomicDTO(
            yield_curve_10y_2y=0.50,
            high_yield_oas=3.50,
            inflation_rate=3.0,
            nominal_10y=4.5,
            vix_value=18.0,
            sahm_rule_indicator=0.0,
        )

    try:
        # Reuse ONE MacroEngine (and therefore one HMMRegimeDetector) across
        # every run_once() cycle within this process -- see _get_macro_engine()
        # docstring / Task A4. This makes the HMM's retrain_freq_days gate
        # meaningful in --interval / agent-loop mode instead of forcing a full
        # refit every single cycle.
        me = _get_macro_engine(fred_key)
        de = me.data_engine
        macro_raw = de.fetch_macro_raw()

        spy_df: Optional[pd.DataFrame] = None
        try:
            spy_raw = de.fetch_technical_raw(["SPY"])
            spy_df = spy_raw.get("SPY")
        except Exception as spy_exc:
            logger.debug("SPY history for HMM unavailable: %s", spy_exc)

        hmm_prob = me.compute_hmm_risk_on_probability(spy_df)

        dto = MacroEconomicDTO(
            yield_curve_10y_2y=float(macro_raw.get("T10Y2Y", 0.5)),
            high_yield_oas=float(macro_raw.get("BAMLH0A0HYM2", 3.5)),
            inflation_rate=float(macro_raw.get("CPIAUCSL_YoY", 2.0)),
            nominal_10y=float(macro_raw.get("DGS10", 4.0)),
            vix_value=float(macro_raw.get("VIXCLS", 18.0)),
            sahm_rule_indicator=float(macro_raw.get("SAHMREALTIME", 0.0)),
            hmm_risk_on_probability=hmm_prob,
        )
        logger.info(
            "Macro DTO built — regime=%s  VIX=%.1f  HMM=%.2f.",
            dto.market_regime,
            dto.vix,
            hmm_prob if hmm_prob is not None else float("nan"),
        )
        return dto

    except Exception as exc:
        logger.warning("Macro DTO construction failed (%s); using neutral defaults.", exc)
        return MacroEconomicDTO(
            yield_curve_10y_2y=0.50,
            high_yield_oas=3.50,
            inflation_rate=3.0,
            nominal_10y=4.5,
            vix_value=18.0,
            sahm_rule_indicator=0.0,
        )


# ---------------------------------------------------------------------------
# Context pre-compute (cross-sectional ranks + multifactor composites)
# ---------------------------------------------------------------------------

def _fetch_bars_for_universe(
    symbols: List[str],
    market: MarketDataProvider,
) -> Dict[str, pd.DataFrame]:
    """Fetch ~450-day OHLCV history for all symbols via the market provider.

    The 12-1m cross-sectional momentum in ``_build_context_extras`` needs
    ``252 + 22 + 1 = 275`` *trading* days; fetching only 252 leaves every
    symbol below that floor, so the xsec rank pass silently yields nothing.
    Request 450 calendar days (~310 trading days) to clear the floor with
    headroom (this also maps yfinance to its "2y" period, avoiding a short
    "1y" pull that tops out near 252 rows).

    Returns a dict symbol → DataFrame.  Failures are dead-lettered per symbol
    so one bad ticker never aborts the pre-compute pass.
    """
    _store = None
    if settings.HISTORICAL_STORE_ENABLED:
        try:
            from data.historical_store import HistoricalStore
            _store = HistoricalStore()
        except Exception as exc:
            logger.warning("HistoricalStore unavailable; using direct provider. %s", exc)

    bars: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            if _store is not None:
                df = _store.get_bars(sym, lookback_days=450, provider=market)
            else:
                df = market.get_intraday_bars(sym, lookback_days=450)
            if df is not None and not df.empty:
                bars[sym] = df
        except Exception as exc:
            logger.debug("Bars pre-fetch skipped for %s: %s", sym, exc)
    logger.info("Pre-fetched bars for %d / %d symbols.", len(bars), len(symbols))
    return bars


def _build_context_extras(
    symbols: List[str],
    bars_dict: Dict[str, pd.DataFrame],
    macro_dto: MacroEconomicDTO,
) -> Dict[str, Any]:
    """Build universe-wide pre-computed signal context for injection into advisory.

    Computes 12-1m cross-sectional momentum ranks and Fama-French multifactor
    composites by running global_registry.run_pre_compute() on a minimal
    universe DataFrame.  The result is passed as context_extras to each
    advisory.evaluate() call so cross-sectional and multifactor signals score
    with real data instead of their neutral-0 fallback.

    Returns an empty dict (and logs a warning) if pre_compute raises.
    """
    SKIP_DAYS = 22       # 1-month skip for Jegadeesh-Titman momentum
    LOOKBACK_DAYS = 252  # 12-month lookback
    REQUIRED = LOOKBACK_DAYS + SKIP_DAYS + 1

    try:
        # ── Step 1: compute 12-1m cross-sectional returns ────────────────────
        xsec_return: Dict[str, float] = {}
        for sym, df in bars_dict.items():
            close = df["Close"].dropna()
            if len(close) < REQUIRED:
                continue
            p_recent = float(close.iloc[-(SKIP_DAYS + 1)])
            p_old = float(close.iloc[-(LOOKBACK_DAYS + 1)])
            if p_old > 0:
                xsec_return[sym] = p_recent / p_old - 1.0

        if xsec_return:
            ret_series = pd.Series(xsec_return)
            xsec_rank_series = ret_series.rank(pct=True, ascending=True)
        else:
            xsec_rank_series = pd.Series(dtype=float)

        # ── Step 2: build a minimal universe DataFrame for pre_compute ────────
        rows = []
        for sym in symbols:
            df = bars_dict.get(sym)
            price = float(df["Close"].iloc[-1]) if df is not None and not df.empty else 0.0
            rows.append({
                "Symbol": sym,
                "Price": price,
                "XSec_12_1M": xsec_return.get(sym, float("nan")),
                "XSec_Momentum_Rank": (
                    float(xsec_rank_series[sym])
                    if sym in xsec_rank_series.index
                    else float("nan")
                ),
            })
        universe_df = pd.DataFrame(rows)

        # ── Step 3: run global_registry.run_pre_compute() ────────────────────
        stub_bar = MarketBarDTO(
            date=datetime.now(),
            ticker="__UNIVERSE__",
            open_price=100.0,
            high_price=100.0,
            low_price=100.0,
            close_price=100.0,
            volume=0,
        )
        stub_fund = FundamentalDataDTO(
            ticker="__UNIVERSE__",
            pe_ratio=None,
            pb_ratio=None,
            dividend_yield=0.0,
            book_value=0.0,
            eps_trailing=0.0,
            dividend_growth_rate=0.0,
            payout_ratio=0.0,
            sector="Unknown",
            company_name="Universe stub",
        )
        shared_ctx = SignalContext(bar=stub_bar, fundamentals=stub_fund, macro=macro_dto)
        global_registry.run_pre_compute(universe_df, shared_ctx)

        logger.info(
            "Context pre-compute: %d xsec ranks, %d multifactor scores.",
            len(shared_ctx.xsec_percentile_ranks),
            len(shared_ctx.multifactor_scores),
        )
        return {
            "xsec_percentile_ranks": shared_ctx.xsec_percentile_ranks,
            "multifactor_scores": shared_ctx.multifactor_scores,
        }

    except Exception as exc:
        logger.warning(
            "Context pre-compute failed (%s); cross-sectional signals will score 0.", exc
        )
        return {}


# ---------------------------------------------------------------------------
# Sheet sink helpers
# ---------------------------------------------------------------------------

def _rec_to_sheet_row(
    rec: Recommendation,
    snapshot: AccountSnapshot,
    price: float,
) -> dict:
    """Map one Recommendation + position to a Sheet-compatible row dict.

    Columns not derivable from advisory output are left as "" or 0.  The row
    uses internal column keys from config.COLUMN_SCHEMA; get_rename_mapping()
    translates them to display headers before writing.
    """
    ki = rec.key_indicators
    pos: Optional[PortfolioPosition] = snapshot.positions.get(rec.symbol)

    def _f(key: str, default: float = 0.0) -> float:
        v = ki.get(key, default)
        if v is None:
            return default
        try:
            f = float(v)
            return default if (f != f) else f  # NaN guard
        except (TypeError, ValueError):
            return default

    forecast_30 = float(rec.forecast) if rec.forecast is not None else 0.0
    forecast_pct = _f("forecast_30d_pct")

    return {
        # ── Core identity ────────────────────────────────────────────────────
        "Symbol": rec.symbol,
        "Price": round(price, 4),

        # ── Signal & advice ──────────────────────────────────────────────────
        "Action Signal": rec.action,
        "Advice": rec.rationale[:200] if rec.rationale else "",
        "Actionable Advice Signal": f"{rec.action}: {rec.strategy}",
        "Score": round(_f("score", 50.0), 2),
        "Kelly Target": round(_f("kelly_raw"), 6),
        "Edge Ratio": 0.0,

        # ── Technical indicators ─────────────────────────────────────────────
        "RSI": round(_f("rsi", 50.0), 2),
        "RSI_2": round(_f("rsi_2", 50.0), 2),
        "MACD_Line": round(_f("macd_line"), 4),
        "ATR": round(_f("atr"), 4),
        "Aroon Oscillator": round(_f("aroon_osc"), 2),
        "Sortino Ratio": round(_f("sortino"), 4),
        "Max Drawdown": round(_f("max_drawdown"), 4),
        "RS vs SPY": round(_f("rs_vs_spy"), 4),
        "GARCH_Vol": round(_f("garch_vol"), 6),

        # ── Forecast ─────────────────────────────────────────────────────────
        "Forecast_30": round(forecast_30, 2),
        "Forecast_30_Pct": round(forecast_pct, 6),

        # ── Dividends ────────────────────────────────────────────────────────
        "Dividend Yield": round(_f("dividend_yield"), 4),

        # ── Execution ranges (not computed by advisory) ──────────────────────
        "buyRange": "",
        "sellRange": "",
        "Option Strategy": "",

        # ── Robinhood position ───────────────────────────────────────────────
        "Robinhood Shares": float(pos.quantity) if pos else 0.0,
        "Robinhood Avg Cost": float(pos.average_cost) if pos else 0.0,
        "Robinhood Dividends": float(pos.dividends_received) if pos else 0.0,
        "Robinhood Advice": (
            f"Hold {pos.quantity:.0f} @ avg ${pos.average_cost:.2f}"
            if pos else "Not held"
        ),

        # ── Advisory overlay ─────────────────────────────────────────────────
        "Advisory_Action": rec.action,
        "Advisory_Conviction": round(rec.conviction, 4),
        "Advisory_Rationale": rec.rationale,
        "Advisory_Position_Pct": round(rec.suggested_position_pct, 6),
        "Advisory_Data_Quality": rec.data_quality,

        # ── Placeholders for full-pipeline columns not produced by advisory ──
        "Strategy Explainer Notes": rec.rationale[:150] if rec.rationale else "",
        "Macro Status": "",
        "HMM_Risk_On_Probability": float("nan"),
    }


def _write_to_sheet(result: RunResult, market: Optional[MarketDataProvider] = None) -> None:
    """Write RunResult to Google Sheets (Stage F).

    Silently skipped when credentials.json is absent.  Errors are caught and
    logged — the Sheet is best-effort; analysis value must not depend on it.
    """
    if not os.path.exists(CREDENTIALS_FILE):
        logger.info("Sheet write skipped — %s not found.", CREDENTIALS_FILE)
        return

    try:
        import gspread
        from gspread_dataframe import set_with_dataframe

        gc = gspread.service_account(filename=CREDENTIALS_FILE)
        sh = gc.open(SHEET_NAME)
        try:
            ws = sh.worksheet(TAB_NAME_OUTPUT)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(
                title=TAB_NAME_OUTPUT,
                rows=500,
                cols=len(config.get_headers()),
            )

        # ── Build rows for successful recommendations ─────────────────────────
        rows: List[dict] = []
        for rec in result.recommendations:
            price = 0.0
            if market is not None:
                try:
                    q = market.get_latest_quote(rec.symbol)
                    price = q.price
                except Exception:
                    pass
            rows.append(_rec_to_sheet_row(rec, result.snapshot, price))

        # ── Dead-letter rows (one per errored symbol) ─────────────────────────
        for err in result.errors:
            rows.append({
                "Symbol": err["symbol"],
                "Action Signal": "ERROR",
                "Advice": (
                    f"[{err['stage']}] {err['error_type']}: "
                    f"{err['message'][:80]}"
                ),
                "Advisory_Data_Quality": "PARTIAL",
            })

        if not rows:
            logger.info("Sheet write skipped — no rows to write.")
            return

        df = pd.DataFrame(rows)

        # Rename internal keys → display headers
        rename_map = config.get_rename_mapping()
        df.rename(columns=rename_map, inplace=True)

        # Ensure all expected columns are present (fill missing with "")
        final_headers = config.get_headers()
        for h in final_headers:
            if h not in df.columns:
                df[h] = ""
        df = df[[h for h in final_headers if h in df.columns]]
        df = df.replace([np.inf, -np.inf], np.nan).fillna("")

        set_with_dataframe(
            ws, df,
            row=1, col=1,
            include_column_header=True,
            resize=True,
        )
        logger.info("Sheet updated: %d rows → '%s'.", len(df), TAB_NAME_OUTPUT)

        # Apply conditional formatting (non-fatal)
        try:
            _apply_conditional_formatting(sh, ws, list(df.columns))
        except Exception as cf_exc:
            logger.warning("Conditional formatting failed (non-critical): %s", cf_exc)

    except Exception as exc:
        logger.error("Sheet write failed: %s", exc)


def _apply_conditional_formatting(
    sh: Any,
    ws: Any,
    headers: List[str],
) -> None:
    """Apply conditional formatting rules to the output Sheet.

    Highlights Action Signal, Dividend Payback Horizon, Leverage Distress
    Factor, and Options IV Edge columns — matching old main.py behaviour.
    """
    sheet_id = ws.id
    requests = []

    # Action Signal colour banding
    if "Action Signal" in headers:
        col_idx = headers.index("Action Signal")
        for text, rgb in [
            ("STRONG BUY",  {"red": 0.20, "green": 0.80, "blue": 0.20}),
            ("BUY",         {"red": 0.70, "green": 0.95, "blue": 0.70}),
            ("HOLD",        {"red": 1.00, "green": 1.00, "blue": 0.80}),
            ("SELL",        {"red": 0.95, "green": 0.70, "blue": 0.70}),
            ("RISK REDUCE", {"red": 0.95, "green": 0.60, "blue": 0.60}),
            ("ERROR",       {"red": 0.90, "green": 0.80, "blue": 0.80}),
        ]:
            requests.append({
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": sheet_id,
                            "startRowIndex": 1, "endRowIndex": 1000,
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1,
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": text}],
                            },
                            "format": {"backgroundColor": rgb},
                        },
                    },
                    "index": 0,
                }
            })

    # Dividend Payback Horizon — green (short) to red (long)
    if "Dividend Payback Horizon" in headers:
        dph_idx = headers.index("Dividend Payback Horizon")
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1, "endRowIndex": 1000,
                        "startColumnIndex": dph_idx, "endColumnIndex": dph_idx + 1,
                    }],
                    "gradientRule": {
                        "minpoint": {
                            "color": {"red": 0.85, "green": 0.95, "blue": 0.85},
                            "type": "NUMBER", "value": "8",
                        },
                        "midpoint": {
                            "color": {"red": 1.0, "green": 1.0, "blue": 0.85},
                            "type": "NUMBER", "value": "11.5",
                        },
                        "maxpoint": {
                            "color": {"red": 0.95, "green": 0.85, "blue": 0.85},
                            "type": "NUMBER", "value": "15",
                        },
                    },
                },
                "index": 0,
            }
        })

    # Leverage Distress Factor — red if < 0.3
    if "Leverage Distress Factor" in headers:
        lev_idx = headers.index("Leverage Distress Factor")
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1, "endRowIndex": 1000,
                        "startColumnIndex": lev_idx, "endColumnIndex": lev_idx + 1,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "NUMBER_LESS",
                            "values": [{"userEnteredValue": "0.3"}],
                        },
                        "format": {
                            "backgroundColor": {"red": 0.98, "green": 0.82, "blue": 0.82}
                        },
                    },
                },
                "index": 0,
            }
        })

    # Options IV Edge — green if > 0
    if "Options IV Edge" in headers:
        opt_idx = headers.index("Options IV Edge")
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1, "endRowIndex": 1000,
                        "startColumnIndex": opt_idx, "endColumnIndex": opt_idx + 1,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "NUMBER_GREATER",
                            "values": [{"userEnteredValue": "0.0"}],
                        },
                        "format": {
                            "backgroundColor": {"red": 0.85, "green": 0.95, "blue": 0.85}
                        },
                    },
                },
                "index": 0,
            }
        })

    if requests:
        sh.batch_update({"requests": requests})


# ---------------------------------------------------------------------------
# HTML report (Stage G)
# ---------------------------------------------------------------------------

def _write_state_snapshot(result: RunResult, macro_dto: Optional[MacroEconomicDTO]) -> None:
    """Persist OUTPUT_DIR/state_snapshot.json + rotate into history/.

    Mirrors ``main_orchestrator._write_state_snapshot`` so the
    ``scripts.snapshot_diff`` reader sees a consistent schema across both
    entry points. Errors are swallowed (CONSTRAINT #6) — the daily report
    must always render.
    """
    try:
        import json
        from datetime import datetime, timezone

        snap = result.snapshot
        positions = getattr(snap, "positions", {}) or {}
        holdings = sorted(
            sym.upper() for sym, p in positions.items()
            if float(getattr(p, "quantity", 0.0) or 0.0) > 0
        )

        signals: List[Dict[str, Any]] = []
        for rec in result.recommendations:
            pos = positions.get(rec.symbol)
            ki = rec.key_indicators or {}
            shares = float(getattr(pos, "quantity", 0.0) or 0.0) if pos else 0.0
            signals.append({
                "symbol": rec.symbol,
                # advisory entry point: action == advisory_action (single source).
                "action": rec.action,
                "advisory_action": rec.action,
                "advisory_conviction": float(rec.conviction or 0.0),
                "advisory_position_pct": float(rec.suggested_position_pct or 0.0),
                "advisory_rationale": rec.rationale or "",
                "kelly_target": float(rec.suggested_position_pct or 0.0),
                "score": float(ki.get("score", 0.0) or 0.0),
                "price": float(getattr(pos, "current_price", 0.0) or 0.0) if pos else 0.0,
                "shares": shares,
                # GUI Strategy Matrix decomposition (additive; consumed by
                # gui/panels/strategy_matrix.py). Scalars sourced from
                # engine.advisory.Recommendation.key_indicators;
                # score_components is the one non-scalar field, carried
                # separately on the Recommendation dataclass (None when the
                # strategy engine failed this cycle — never fabricated).
                "meta_label_composite": float(ki.get("meta_label_composite", 1.0) or 1.0),
                "regime_multiplier": float(ki.get("regime_multiplier", 1.0) or 1.0),
                "kelly_target_pre_regime": ki.get("kelly_target_pre_regime", float("nan")),
                "kelly_target_post_regime": ki.get("kelly_target_post_regime", float("nan")),
                "score_components": rec.score_components or {},
            })

        regime = "UNKNOWN"
        vix = 0.0
        if macro_dto is not None:
            regime = getattr(macro_dto, "market_regime", "UNKNOWN") or "UNKNOWN"
            vix = float(getattr(macro_dto, "vix_value", 0.0) or 0.0)

        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tickers": [r.symbol for r in result.recommendations],
            "holdings": holdings,
            "market_regime": str(regime),
            "vix": vix,
            "kill_switch_active": (settings.OUTPUT_DIR / "KILL_SWITCH").exists(),
            "macro_regime_gate_enabled": settings.MACRO_REGIME_GATE_ENABLED,
            "signals": signals,
        }
        snap_path = settings.OUTPUT_DIR / "state_snapshot.json"
        snap_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        try:
            from scripts.snapshot_diff import rotate_snapshot
            rotate_snapshot(
                snapshot,
                settings.OUTPUT_DIR,
                max_age_days=settings.SNAPSHOT_HISTORY_DAYS,
            )
        except Exception as rot_exc:
            logger.debug("Snapshot rotation skipped: %s", rot_exc)
    except Exception as exc:
        logger.warning("State snapshot write failed (non-critical): %s", exc)


def _load_snapshot_diff_for_report() -> Optional[Dict[str, Any]]:
    """Return the latest Δ Since Last Run diff dict (or ``None`` if unavailable).

    Always called AFTER ``_write_state_snapshot`` so the just-written
    snapshot is the "curr" side of the comparison; the prior rotated
    snapshot is "prev". Returns ``None`` on any failure or first ever run
    so the report template hides the band entirely.
    """
    try:
        from scripts.snapshot_diff import compute_diff_from_history
        diff = compute_diff_from_history(
            settings.OUTPUT_DIR,
            conviction_delta_threshold=settings.SNAPSHOT_CONVICTION_DELTA_THRESHOLD,
        )
        if diff.prev_ts is None and diff.curr_ts is None:
            return None
        return diff.to_dict()
    except Exception as exc:
        logger.debug("Δ-band diff unavailable: %s", exc)
        return None


def _write_html_report(result: RunResult, macro_dto: Optional[MacroEconomicDTO] = None) -> None:
    """Generate the daily HTML report from RunResult (non-fatal on error)."""
    try:
        from diagnostics_and_visuals import generate_html_report

        portfolio_dicts = []
        for rec in result.recommendations:
            ki = rec.key_indicators
            pos = result.snapshot.positions.get(rec.symbol)
            d = {
                "Symbol": rec.symbol,
                "Action Signal": rec.action,
                "Score": ki.get("score", 0.0) or 0.0,
                "RSI": ki.get("rsi", 0.0) or 0.0,
                "GARCH_Vol": ki.get("garch_vol", 0.0) or 0.0,
                "Forecast_30": float(rec.forecast or 0.0),
                "Max Drawdown": ki.get("max_drawdown", 0.0) or 0.0,
                "Max_Drawdown": ki.get("max_drawdown", 0.0) or 0.0,
                "Kelly Target": ki.get("kelly_raw", 0.0) or 0.0,
                "Advice": rec.rationale or "",
                "Advisory_Action": rec.action,
                "Advisory_Conviction": rec.conviction,
                "Advisory_Rationale": rec.rationale or "",
                "Advisory_Position_Pct": rec.suggested_position_pct,
                "data_quality": rec.data_quality,
                "strategy": rec.strategy,
                # ── Holdings & P&L (source of truth: Robinhood AccountSnapshot,
                #    CONSTRAINT #4). Zero-filled for non-held watchlist symbols
                #    so the report renders "—" rather than fabricating a position.
                "Robinhood Shares": float(pos.quantity) if pos else 0.0,
                "Robinhood Avg Cost": float(pos.average_cost) if pos else 0.0,
                "Robinhood Current Price": float(pos.current_price) if pos else 0.0,
                "Robinhood Market Value": float(pos.market_value) if pos else 0.0,
                "Robinhood Unrealized PL": float(pos.unrealized_pl) if pos else 0.0,
                "Robinhood Unrealized PL Pct": float(pos.unrealized_pl_pct) if pos else 0.0,
                "Robinhood Dividends": float(pos.dividends_received) if pos else 0.0,
                "Company Name": (pos.name if pos else "") or "",
            }
            portfolio_dicts.append(d)

        regime = "NEUTRAL"
        kw: Dict[str, Any] = {}
        if macro_dto is not None:
            regime = macro_dto.market_regime
            kw = {
                "yield_curve": getattr(macro_dto, "yield_curve", 0.5),
                "credit_spread": getattr(macro_dto, "credit_spread", 3.5),
                "sahm_rule": getattr(macro_dto, "sahm_rule_indicator", 0.0),
                "real_yield": getattr(macro_dto, "real_yield", 0.0),
            }

        # ── Portfolio summary band (account-level totals) ───────────────────
        #   Driven by the Robinhood AccountSnapshot — the source of truth for
        #   account state. Wrapped defensively so a degraded/empty snapshot
        #   (e.g. Robinhood down) never aborts report generation; the band is
        #   simply omitted (account_summary=None) when no positions exist.
        account_summary: Optional[Dict[str, Any]] = None
        try:
            snap = result.snapshot
            positions = getattr(snap, "positions", {}) or {}
            total_unrealized_pl = sum(
                float(getattr(p, "unrealized_pl", 0.0) or 0.0) for p in positions.values()
            )
            n_buy = sum(1 for r in result.recommendations if r.action == "BUY")
            n_hold = sum(1 for r in result.recommendations if r.action == "HOLD")
            n_sell = sum(1 for r in result.recommendations if r.action == "SELL")
            fetched_at = getattr(snap, "fetched_at", None)
            account_summary = {
                "total_equity": float(getattr(snap, "total_equity", 0.0) or 0.0),
                "buying_power": float(getattr(snap, "buying_power", 0.0) or 0.0),
                "total_dividends": float(getattr(snap, "total_dividends", 0.0) or 0.0),
                "total_unrealized_pl": total_unrealized_pl,
                "num_positions": len(positions),
                "n_buy": n_buy,
                "n_hold": n_hold,
                "n_sell": n_sell,
                "n_total": len(result.recommendations),
                "fetched_at": (fetched_at.strftime("%Y-%m-%d %H:%M UTC")
                               if fetched_at is not None else "—"),
                "age_hours": float(snap.age_hours()) if hasattr(snap, "age_hours") else 0.0,
                "is_stale": bool(snap.is_stale(24.0)) if hasattr(snap, "is_stale") else False,
            }
        except Exception as summ_exc:  # never let summary build abort the report
            logger.debug("Account summary band skipped: %s", summ_exc)
            account_summary = None

        out_path = str(settings.OUTPUT_DIR / "daily_report.html")
        # Δ Since Last Run band: write+rotate the snapshot for THIS run first,
        # then load the diff against the previously-rotated snapshot. The
        # template hides the band entirely when snapshot_diff is None.
        _write_state_snapshot(result, macro_dto)
        snapshot_diff = _load_snapshot_diff_for_report()
        generate_html_report(
            portfolio_dicts, regime, out_path,
            account_summary=account_summary,
            snapshot_diff=snapshot_diff,
            **kw,
        )
        logger.info("HTML report written to %s.", out_path)

    except Exception as exc:
        logger.warning("HTML report failed (non-critical): %s", exc)


# ---------------------------------------------------------------------------
# Run summary
# ---------------------------------------------------------------------------

def _log_summary(result: RunResult) -> None:
    """Emit a structured run summary to the module logger."""
    n_ok = len(result.recommendations)
    n_err = len(result.errors)
    buys  = sum(1 for r in result.recommendations if r.action == "BUY")
    holds = sum(1 for r in result.recommendations if r.action == "HOLD")
    sells = sum(1 for r in result.recommendations if r.action == "SELL")

    logger.info(
        "=== RUN SUMMARY ========================================\n"
        "  Duration   : %.2fs  (started %s)\n"
        "  Universe   : %d symbols — %d OK, %d errors\n"
        "  Signals    : BUY=%d  HOLD=%d  SELL=%d\n"
        "  Account    : age=%.2fh  equity=$%.0f  cash=$%.0f  stale=%s\n"
        "========================================================",
        result.duration_seconds,
        result.started_at.strftime("%H:%M:%S UTC"),
        n_ok + n_err, n_ok, n_err,
        buys, holds, sells,
        result.snapshot.age_hours(),
        result.snapshot.total_equity,
        result.snapshot.buying_power,
        result.snapshot.is_stale(max_age_hours=20.0),
    )
    for err in result.errors:
        logger.warning(
            "  DEAD-LETTER  %-8s  stage=%-22s  %s: %s",
            err["symbol"], err["stage"],
            err["error_type"], err["message"][:80],
        )


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def run_once(force_account: bool = False) -> RunResult:
    """Execute one full pipeline cycle and return an immutable RunResult.

    Parameters
    ----------
    force_account : bool
        ``True`` → bypass the daily Robinhood cache and force a live TOTP
        login.  ``False`` (default) → use the cached snapshot when it is
        younger than 20 hours; only re-fetch when the cache has expired.

    Returns
    -------
    RunResult
        Always returned.  Never raises.  Per-symbol failures are collected in
        ``RunResult.errors`` rather than being propagated.

    .env loading contract
    ---------------------
    This function does NOT call load_dotenv() itself — doing so would pollute
    the pytest session (test_run_once.py invokes run_once() many times and
    each call would copy every .env value into os.environ, breaking
    test_settings_defaults).  The caller is responsible for ensuring
    os.environ contains the secrets that downstream modules read via
    os.environ.get(), notably data/robinhood_portfolio.py.

    Standard call sites:
      • main() invokes _load_dotenv() before run_once() — production launch.
      • Makefile target `verify` invokes load_dotenv() in its python -c block
        before calling main.run_once().
      • verify.command invokes load_dotenv() in its python heredoc before
        calling main.run_once().
      • Tests use mock.patch on fetch_account_snapshot etc., so they don't
        need real env vars.
    """
    started_at = datetime.now(timezone.utc)
    recommendations: List[Recommendation] = []
    errors: List[dict] = []

    # ── Stage A: Account snapshot (Robinhood, daily cache) ───────────────────
    snapshot: AccountSnapshot
    try:
        snapshot = fetch_account_snapshot(max_age_hours=20.0, force=force_account)
        age_h = snapshot.age_hours()
        if force_account:
            cache_msg = "force-refreshed"
        elif age_h < 1.0:
            cache_msg = "served from cache (fresh)"
        else:
            cache_msg = f"served from cache (age={age_h:.1f}h)"
        logger.info(
            "Account snapshot %s — equity=$%.0f  positions=%d.",
            cache_msg,
            snapshot.total_equity,
            len(snapshot.positions),
        )
    except Exception as rh_exc:
        logger.warning(
            "Robinhood snapshot unavailable (%s); proceeding with empty account. "
            "Watchlist universe will still be evaluated.",
            rh_exc,
        )
        snapshot = AccountSnapshot(
            positions={},
            buying_power=0.0,
            total_equity=0.0,
            total_dividends=0.0,
            fetched_at=datetime.now(timezone.utc),
        )

    # ── Stage B: Universe ─────────────────────────────────────────────────────
    symbols = _build_universe(snapshot)
    if not symbols:
        # Held positions are empty AND WATCHLIST is unset AND watchlist.txt is
        # absent / empty.  Spell out every possible fix so the user can act
        # without spelunking through the source.
        logger.warning(
            "Empty symbol universe — nothing to evaluate. "
            "Fix one of: (1) set RH_USERNAME / RH_PASSWORD / RH_MFA_SECRET (optional) in "
            ".env so Robinhood positions populate the universe, (2) set the "
            "WATCHLIST env var (e.g. WATCHLIST=SPY,QQQ,AAPL,MSFT), (3) "
            "create %s with one ticker per line, or (4) add tickers to "
            "Sheet2 column A in the '%s' Google Sheet (requires credentials.json).",
            WATCHLIST_FILE,
            SHEET_NAME,
        )
        finished_at = datetime.now(timezone.utc)
        return RunResult(
            snapshot=snapshot,
            recommendations=[],
            errors=[],
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=(finished_at - started_at).total_seconds(),
        )

    # ── Kill-switch advisory pause gate ──────────────────────────────────────
    # Checked after Stage B so the universe is known (for telemetry), but
    # BEFORE Stage C (macro) and the expensive per-symbol pipeline.
    # When the sentinel is active we return an empty RunResult rather than
    # crashing or producing stale recommendations.  The observability dashboard
    # continues reading the last state_snapshot.json written by a normal run.
    _ks = GlobalKillSwitch()
    if _ks.is_active():
        _ks_reason = _ks.reason() or "(no reason recorded)"
        logger.info(
            "Advisory paused by kill-switch sentinel — skipping evaluation cycle. "
            "Reason: %s  |  Universe would have been: %s  |  "
            "Deactivate with: python -m execution.kill_switch --deactivate",
            _ks_reason,
            ", ".join(symbols[:10]) + ("..." if len(symbols) > 10 else ""),
        )
        finished_at = datetime.now(timezone.utc)
        return RunResult(
            snapshot=snapshot,
            recommendations=[],
            errors=[{
                "symbol": "_advisory",
                "stage": "kill_switch_gate",
                "error_type": "AdvisoryPaused",
                "message": f"Kill-switch sentinel active: {_ks_reason}",
                "timestamp": finished_at.isoformat(),
            }],
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=(finished_at - started_at).total_seconds(),
        )

    # ── Stage C: Macro context ────────────────────────────────────────────────
    macro_dto = _build_macro_dto()

    # ── Stage D: Context pre-compute (universe-wide, before per-symbol loop) ──
    market = get_provider()
    bars_dict = _fetch_bars_for_universe(symbols, market)
    context_extras = _build_context_extras(symbols, bars_dict, macro_dto)

    # ── Stage E: Per-symbol advisory evaluation ───────────────────────────────
    # Each evaluate() call is independent (engine.advisory constructs its engines
    # per call; the shared inputs — snapshot, market, macro_dto, context_extras —
    # are read-only during the loop), so the loop parallelizes across a bounded
    # thread pool.  The win is per-symbol network I/O (quote fetch) plus the
    # native-compute sections (numpy/pandas/statsmodels/arch release the GIL).
    # Results are reassembled in the ORIGINAL symbol order so the Sheet/HTML/
    # snapshot output and logs are byte-identical regardless of completion order
    # or worker count.  Dead-letter semantics are preserved exactly: a per-symbol
    # exception becomes an entry in RunResult.errors and never aborts the run.
    logger.info("Evaluating %d symbols...", len(symbols))

    def _eval_one(symbol: str):
        """Return ('ok', Recommendation) or ('err', error_dict) for one symbol.

        Never raises — mirrors the original per-symbol try/except so a single
        bad ticker is dead-lettered, not propagated (CONSTRAINT #6).
        """
        try:
            position = snapshot.positions.get(symbol)
            rec = advisory_evaluate(
                symbol=symbol,
                position=position,
                market=market,
                snapshot=snapshot,
                macro_dto=macro_dto,
                context_extras=context_extras,
            )
            return ("ok", rec)
        except Exception as exc:
            return ("err", {
                "symbol": symbol,
                "stage": "advisory_evaluate",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    workers = max(1, int(getattr(settings, "ADVISORY_MAX_CONCURRENCY", 8)))
    if workers == 1 or len(symbols) <= 1:
        # Sequential path — original, fully-deterministic behavior.
        results_by_symbol = {sym: _eval_one(sym) for sym in symbols}
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(symbols))) as pool:
            # executor.map preserves input order; we still key by symbol below
            # so the assembly pass is order-explicit and robust.
            mapped = pool.map(_eval_one, symbols)
            results_by_symbol = {sym: res for sym, res in zip(symbols, mapped)}

    # Ordered assembly: rebuild recommendations/errors and emit logs in the
    # original symbol order so output is deterministic.
    for symbol in symbols:
        kind, payload = results_by_symbol[symbol]
        if kind == "ok":
            rec = payload
            recommendations.append(rec)
            logger.info(
                "  %-6s  %-10s  conviction=%.2f  quality=%-7s  pos=%.1f%%",
                symbol,
                rec.action,
                rec.conviction,
                rec.data_quality,
                rec.suggested_position_pct * 100.0,
            )
        else:
            logger.warning("Advisory failed for %s: %s", symbol, payload["message"])
            errors.append(payload)

    finished_at = datetime.now(timezone.utc)
    result = RunResult(
        snapshot=snapshot,
        recommendations=recommendations,
        errors=errors,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=(finished_at - started_at).total_seconds(),
    )
    _log_summary(result)
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _read_macro_snapshot_hint() -> Dict[str, Any]:
    """Return ``{"vix": float|None, "market_regime": str|None}`` from the last
    ``output/state_snapshot.json`` write — or empty values when missing.

    Used by ``_run_agent_loop`` to inform the adaptive cadence policy without
    re-running ``_build_macro_dto`` (which would re-hit FRED).  Returns a
    neutral hint dict (both keys ``None``) on any error so the policy falls
    back to its default time-of-day cadence.
    """
    try:
        import json
        snap_path = settings.OUTPUT_DIR / "state_snapshot.json"
        if not snap_path.exists():
            return {"vix": None, "market_regime": None}
        payload = json.loads(snap_path.read_text(encoding="utf-8"))
        vix_raw = payload.get("vix")
        regime_raw = payload.get("market_regime")
        return {
            "vix": float(vix_raw) if vix_raw not in (None, 0, 0.0) else None,
            "market_regime": str(regime_raw) if regime_raw else None,
        }
    except Exception as exc:
        logger.debug("Could not read macro hint from state_snapshot.json: %s", exc)
        return {"vix": None, "market_regime": None}


def _run_agent_loop(run_cycle) -> None:
    """Autonomous advisory loop driver.

    Replaces ``--interval N``'s fixed timer with the policy in
    ``engine.advisory_agent``:
      * adaptive cadence (RTH-aware, VIX/regime-adaptive, error back-off)
      * actionable-backlog reminders for high-conviction signals the operator
        has not yet logged a decision for
      * persistent state at ``OUTPUT_DIR/agent_state.json`` so the backlog
        survives restarts

    The ``run_cycle`` callable is the same closure used by ``--interval`` mode;
    it must return the ``RunResult`` of one full cycle.  All ntfy push behavior
    inside ``run_cycle`` is preserved (errors → high-priority push, clean run →
    one default push per launch, watch_engine alerts per cycle).

    SIGINT / SIGTERM are caught and the loop exits cleanly after the current
    cycle plus any post-cycle reminder dispatch — never mid-sleep.
    """
    # Lazy import keeps test imports of main.py cheap.
    from engine.advisory_agent import (  # noqa: PLC0415
        apply_reminder_dispatch,
        compute_backlog_reminders,
        compute_next_run_delay,
        dispatch_backlog_reminders,
        load_agent_state,
        process_run_result,
        save_agent_state,
        update_backlog,
    )

    state_path = settings.OUTPUT_DIR / "agent_state.json"
    state = load_agent_state(state_path)
    logger.info(
        "Agent mode starting — state path=%s loaded backlog=%d cycle_count=%d",
        state_path, len(state.backlog), state.cycle_count,
    )

    _shutdown = False

    def _handle_signal(signum: int, frame: Any) -> None:
        nonlocal _shutdown
        logger.info("Shutdown signal received; finishing current cycle then exiting.")
        _shutdown = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    dashboard_url = os.environ.get("NTFY_DASHBOARD_URL")

    while not _shutdown:
        # ── (1) Run one full advisory cycle (sheet + html + watch_engine) ────
        cycle_started_at = datetime.now(timezone.utc)
        try:
            result = run_cycle()
        except Exception as exc:
            logger.exception("Agent cycle failed unexpectedly: %s", exc)
            result = None

        # ── (2) Update agent state with cycle outcome ────────────────────────
        if result is not None:
            try:
                process_run_result(state, result, datetime.now(timezone.utc))
            except Exception as exc:
                logger.warning("process_run_result failed (%s); skipping", exc)

            # ── (3) Refresh backlog from latest recommendations + decision log
            decision_entries: List[Any] = []
            try:
                from gui.decision_log import read_decisions  # noqa: PLC0415
                decision_entries = read_decisions()
            except Exception as exc:
                logger.debug("decision_log read failed (%s); backlog clear step skipped", exc)
            try:
                update_backlog(
                    state, result.recommendations, decision_entries,
                    datetime.now(timezone.utc),
                )
            except Exception as exc:
                logger.warning("update_backlog failed (%s); skipping", exc)

            # ── (4) Compute and dispatch backlog reminders ───────────────────
            try:
                reminders = compute_backlog_reminders(state, datetime.now(timezone.utc))
                if reminders:
                    dispatch_backlog_reminders(reminders, dashboard_url=dashboard_url)
                    apply_reminder_dispatch(state, reminders, datetime.now(timezone.utc))
                    logger.info(
                        "Agent dispatched %d backlog reminder(s).", len(reminders),
                    )
            except Exception as exc:
                logger.warning("Backlog reminder dispatch failed (%s); skipping", exc)

            # ── (4b) Trade-signal abilities (conviction momentum + price triggers)
            try:
                from engine.trade_signals import (  # noqa: PLC0415
                    detect_conviction_momentum,
                    detect_price_triggers,
                    dispatch_trade_alerts,
                    update_conviction_history,
                )
                recs = result.recommendations
                # Ability A — conviction momentum (cross-cycle trajectory).
                state.conviction_history = update_conviction_history(
                    state.conviction_history, recs,
                )
                mom_alerts, state.momentum_alerted = detect_conviction_momentum(
                    state.conviction_history, recs, state.momentum_alerted,
                )
                # Ability B — stop / take-profit proximity for held positions.
                price_alerts, state.price_trigger_alerted = detect_price_triggers(
                    result.snapshot, recs, state.price_trigger_alerted,
                )
                trade_alerts = mom_alerts + price_alerts
                if trade_alerts:
                    dispatch_trade_alerts(trade_alerts, dashboard_url=dashboard_url)
                    logger.info(
                        "Agent dispatched %d trade alert(s) — momentum=%d price=%d.",
                        len(trade_alerts), len(mom_alerts), len(price_alerts),
                    )
            except Exception as exc:
                logger.warning("Trade-signal abilities failed (%s); skipping", exc)

        # ── (5) Persist state regardless of cycle outcome ─────────────────────
        try:
            save_agent_state(state, state_path)
        except Exception as exc:
            logger.warning("save_agent_state failed (%s); skipping", exc)

        if _shutdown:
            break

        # ── (6) Compute adaptive sleep duration ──────────────────────────────
        macro_hint = _read_macro_snapshot_hint()
        delay_s = compute_next_run_delay(
            datetime.now(timezone.utc),
            state=state,
            vix=macro_hint.get("vix"),
            market_regime=macro_hint.get("market_regime"),
        )
        logger.info(
            "Agent sleeping %ds (cycle #%d, backlog=%d, vix=%s, regime=%s, err_streak=%d).",
            delay_s, state.cycle_count, len(state.backlog),
            macro_hint.get("vix"), macro_hint.get("market_regime"),
            state.consecutive_error_cycles,
        )

        # Sleep in 1-second slices so SIGINT/SIGTERM are caught promptly.
        for _ in range(int(delay_s)):
            if _shutdown:
                break
            time.sleep(1)

    logger.info("Agent loop exited cleanly.")


def main() -> None:
    """CLI entry point with two-tier refresh support.

    Flags
    -----
    (no flags)
        Run once, write Sheet, generate HTML report, exit 0.  Even if some
        symbols errored (their entries appear in RunResult.errors and as
        ERROR rows in the Sheet).

    --interval N
        Loop: refresh market data every N seconds.  The account tier still
        fetches Robinhood at most once per day — an all-day run logs in once.
        Ctrl-C or SIGTERM exits cleanly after the current cycle completes.

    --agent
        Run the autonomous advisory agent loop.  Replaces ``--interval``'s
        fixed timer with the adaptive cadence + backlog reminder policy in
        ``engine/advisory_agent.py``.  Cadence is RTH/VIX/regime-aware;
        high-conviction signals the operator hasn't logged a decision for
        are re-pinged at 1h / 4h / 24h escalation tiers.  Persistent state
        at ``OUTPUT_DIR/agent_state.json`` survives restarts.  Takes
        precedence over ``--interval``.

    --refresh-account
        Force a fresh Robinhood login on this launch, bypassing the daily
        cache.  For subsequent iterations in --interval mode, normal caching
        resumes (so re-auth happens at most once per launch).
    """
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="InvestYo Quant Platform — advisory pipeline launcher",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Refresh market data every N seconds (0 = run once and exit).",
    )
    parser.add_argument(
        "--refresh-account",
        action="store_true",
        default=False,
        help="Force a fresh Robinhood fetch on this launch (bypasses daily cache).",
    )
    parser.add_argument(
        "--agent",
        action="store_true",
        default=False,
        help=(
            "Run the autonomous advisory agent loop: adaptive cadence based on "
            "market hours / VIX / regime, backlog reminders for high-conviction "
            "signals the operator has not yet logged a decision for, persistent "
            "state across restarts.  Takes precedence over --interval."
        ),
    )
    args = parser.parse_args()

    # Load .env into os.environ before any runtime os.environ.get() call.
    # This is the primary load point when launched as `python main.py`; the
    # call inside run_once() is the defensive backstop for direct imports.
    _load_dotenv(override=False)
    setup_logging()   # configure root logger (file + console, rotating, structured)
    logger.info("InvestYo Quant Platform starting.")
    settings.warn_if_fred_key_leaked(logger)

    # Force-account flag applies only to the FIRST cycle; subsequent iterations
    # use the daily cache regardless.
    _force_next = args.refresh_account
    # Tracks whether a "clean run" push notification has been sent this launch.
    # At most one per launch in --interval mode to avoid notification spam.
    _clean_notified = False

    def _run_cycle() -> RunResult:
        nonlocal _force_next, _clean_notified
        result = run_once(force_account=_force_next)
        _force_next = False  # one-shot; cache resumes from here

        # ── Alerting: compact summary → log + optional push notification ──────
        summary = summarize_run(result)
        logger.info("\n%s", summary)

        if result.errors:
            # High-priority push: list failing symbols and stages.
            err_preview = ", ".join(
                f"{e.get('symbol', '?')} ({e.get('stage', '?')})"
                for e in result.errors[:3]
            )
            suffix = (
                f" +{len(result.errors) - 3} more"
                if len(result.errors) > 3
                else ""
            )
            notify(
                title="InvestYo ⚠ Errors Detected",
                message=(
                    f"{len(result.errors)} symbol(s) failed: "
                    f"{err_preview}{suffix}\n"
                    f"OK={len(result.recommendations)}  "
                    f"Duration={result.duration_seconds:.1f}s"
                ),
                priority="high",
            )
        elif not _clean_notified:
            # One normal-priority notification per launch on a fully clean run.
            notify(
                title="InvestYo ✓ Refresh Complete",
                message=summary,
                priority="default",
            )
            _clean_notified = True
        # ─────────────────────────────────────────────────────────────────────

        # ── Symbol watch alerts (Tier 1.4) — non-fatal ───────────────────────────
        # Evaluated immediately after run_once() so alerts reflect the freshest
        # signal output.  State is loaded from the PREVIOUS run, compared
        # against the CURRENT recommendations, and saved AFTER dispatch.
        # This is the shift-adjusted, no-lookahead contract: no market data
        # is re-fetched inside watch_engine; it only compares already-computed
        # advisory outputs.
        try:
            from watch_engine import (
                dispatch_watch_alerts,
                evaluate_watch_rules,
                load_watch_rules,
                load_watch_state,
                save_watch_state,
            )

            _watch_rules = load_watch_rules(settings.WATCH_RULES_FILE)
            _watch_state_path = settings.OUTPUT_DIR / "watch_state.json"
            _prev_watch_state = load_watch_state(_watch_state_path)
            _watch_alerts, _new_watch_state = evaluate_watch_rules(
                _watch_rules,
                result.recommendations,
                _prev_watch_state,
            )
            dispatch_watch_alerts(
                _watch_alerts,
                dashboard_url=os.environ.get("NTFY_DASHBOARD_URL"),
            )
            # Always save — keeps edge-trigger state current even on quiet runs.
            save_watch_state(_new_watch_state, _watch_state_path)
            if _watch_alerts:
                logger.info(
                    "Symbol watch: %d alert(s) dispatched across %d rule(s).",
                    len(_watch_alerts),
                    len(_watch_rules),
                )
        except Exception as _watch_exc:
            logger.warning(
                "Symbol watch alert evaluation failed (non-critical): %s",
                _watch_exc,
            )
        # ─────────────────────────────────────────────────────────────────────

        # ── Robinhood execution queue (Tier 8) — non-fatal, advisory-only ─────
        # Emits a GATED, DRY-RUN proposed-order queue to
        # output/execution_queue.json for the Claude Code "robinhood-execution"
        # agent to consume.  This NEVER contacts a broker or places an order —
        # the headless pipeline cannot call the Robinhood MCP.  When
        # ROBINHOOD_EXECUTION_MODE=off (the default) nothing is written and this
        # block is a no-op.  The kill-switch advisory-pause gate above already
        # short-circuits run_once(), so a paused cycle emits nothing.
        try:
            from execution.queue_builder import emit_execution_queue  # noqa: PLC0415

            _queue_path = emit_execution_queue(result)
            if _queue_path is not None:
                logger.info("Robinhood execution queue emitted → %s", _queue_path)
        except Exception as _queue_exc:
            logger.warning(
                "Execution queue emit failed (non-critical): %s", _queue_exc,
            )
        # ─────────────────────────────────────────────────────────────────────

        market = get_provider()
        _write_to_sheet(result, market=market)

        # Build macro_dto again cheaply (same result, neutral defaults are fast)
        # to pass macro context to the HTML report template.
        try:
            from dto_models import MacroEconomicDTO as _MDTO

            _macro = _MDTO(
                yield_curve_10y_2y=0.5,
                high_yield_oas=3.5,
                inflation_rate=3.0,
                nominal_10y=4.5,
                vix_value=18.0,
                sahm_rule_indicator=0.0,
            )
        except Exception:
            _macro = None

        _write_html_report(result, macro_dto=_macro)
        return result

    if args.agent:
        _run_agent_loop(run_cycle=_run_cycle)
    elif args.interval > 0:
        _shutdown = False

        def _handle_signal(signum: int, frame: Any) -> None:
            nonlocal _shutdown
            logger.info("Shutdown signal received; finishing current cycle then exiting.")
            _shutdown = True

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        logger.info("Interval mode: market data refreshes every %ds.", args.interval)
        while not _shutdown:
            _run_cycle()
            if _shutdown:
                break
            logger.info(
                "Sleeping %ds until next market-data refresh...", args.interval
            )
            # Sleep in 1-second increments to catch shutdown signals promptly.
            for _ in range(args.interval):
                if _shutdown:
                    break
                time.sleep(1)

        logger.info("Exiting interval loop cleanly.")

    else:
        # Single-run mode
        _run_cycle()

    logger.info("InvestYo Quant Platform finished.")
    sys.exit(0)


if __name__ == "__main__":
    main()
