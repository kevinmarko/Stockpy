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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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

logger = logging.getLogger("InvestYo.main")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WATCHLIST_FILE = "watchlist.txt"      # one ticker per line; '#' lines ignored
CREDENTIALS_FILE = "credentials.json" # Google Sheets service-account key
SHEET_NAME = "Stock Dashboard Py"
TAB_NAME_OUTPUT = "FidelityData_Automated"


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


def _build_universe(snapshot: AccountSnapshot) -> List[str]:
    """Return the evaluation universe: held symbols ∪ watchlist, deduped, sorted.

    Held symbols are always included regardless of the watchlist.
    """
    held = set(snapshot.positions.keys())
    watchlist = set(_load_watchlist())
    universe = sorted(held | watchlist)
    logger.info(
        "Universe: %d symbols (%d held, %d watchlist-only).",
        len(universe),
        len(held),
        len(watchlist - held),
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
        from data_engine import DataEngine
        from macro_engine import MacroEngine

        de = DataEngine(fred_key)
        macro_raw = de.fetch_macro_raw()

        me = MacroEngine(data_engine=de)
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
            dto.vix_value,
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
    """Fetch 252-day OHLCV history for all symbols via the market provider.

    Returns a dict symbol → DataFrame.  Failures are dead-lettered per symbol
    so one bad ticker never aborts the pre-compute pass.
    """
    bars: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = market.get_intraday_bars(sym, lookback_days=252)
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
                "Robinhood Shares": float(pos.quantity) if pos else 0.0,
                "Robinhood Avg Cost": float(pos.average_cost) if pos else 0.0,
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

        out_path = str(settings.OUTPUT_DIR / "daily_report.html")
        generate_html_report(portfolio_dicts, regime, out_path, **kw)
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
        logger.warning(
            "Empty symbol universe — nothing to evaluate. "
            "Check WATCHLIST env var or add tickers to %s.",
            WATCHLIST_FILE,
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

    # ── Stage C: Macro context ────────────────────────────────────────────────
    macro_dto = _build_macro_dto()

    # ── Stage D: Context pre-compute (universe-wide, before per-symbol loop) ──
    market = get_provider()
    bars_dict = _fetch_bars_for_universe(symbols, market)
    context_extras = _build_context_extras(symbols, bars_dict, macro_dto)

    # ── Stage E: Per-symbol advisory evaluation ───────────────────────────────
    logger.info("Evaluating %d symbols...", len(symbols))
    for symbol in symbols:
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
            recommendations.append(rec)
            logger.info(
                "  %-6s  %-10s  conviction=%.2f  quality=%-7s  pos=%.1f%%",
                symbol,
                rec.action,
                rec.conviction,
                rec.data_quality,
                rec.suggested_position_pct * 100.0,
            )
        except Exception as exc:
            logger.warning("Advisory failed for %s: %s", symbol, exc)
            errors.append({
                "symbol": symbol,
                "stage": "advisory_evaluate",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

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
    args = parser.parse_args()

    setup_logging()   # configure root logger (file + console, rotating, structured)
    logger.info("InvestYo Quant Platform starting.")
    settings.warn_if_fred_key_leaked(logger)

    # Force-account flag applies only to the FIRST cycle; subsequent iterations
    # use the daily cache regardless.
    _force_next = args.refresh_account
    # Tracks whether a "clean run" push notification has been sent this launch.
    # At most one per launch in --interval mode to avoid notification spam.
    _clean_notified = False

    def _run_cycle() -> None:
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

    if args.interval > 0:
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
