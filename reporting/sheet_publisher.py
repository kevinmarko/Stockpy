"""Google Sheets publish path — extracted verbatim from main.py.

Owns the mapping of a ``RunResult`` (advisory recommendations + dead-letter
errors) to the Google Sheets output tab, including the row builder, the
best-effort write, and the conditional-formatting rules. Auth is centralized
through :mod:`reporting.sheets_client` (``get_service_account_client``); the
Sheet is a best-effort sink — a missing client silently skips the write and any
error is logged, never propagated.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import config
from reporting.sheets_client import (
    get_service_account_client,
    SHEET_NAME,
    TAB_NAME_OUTPUT,
)

from typing import TYPE_CHECKING, Optional, List, Dict, Any

if TYPE_CHECKING:
    from main import RunResult
    from data.robinhood_portfolio import AccountSnapshot, PortfolioPosition
    from data.market_data import MarketDataProvider
    from engine.advisory import Recommendation

logger = logging.getLogger(__name__)


def rec_to_sheet_row(
    rec: "Recommendation",
    snapshot: "AccountSnapshot",
    price: float,
) -> dict:
    """Map one Recommendation + position to a Sheet-compatible row dict.

    Columns not derivable from advisory output are left as "" or 0.  The row
    uses internal column keys from config.COLUMN_SCHEMA; get_rename_mapping()
    translates them to display headers before writing.
    """
    ki = rec.key_indicators
    pos: Optional["PortfolioPosition"] = snapshot.positions.get(rec.symbol)

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
        # docs/CONFIG_SCHEMA_PLAN.md Phase C1: was keyed "Dividend Yield",
        # which matches neither COLUMN_SCHEMA's key nor header and was
        # silently dropped by write_recommendations()'s column filter.
        # config.COLUMN_SCHEMA already has a slot for this exact value —
        # {"header": "Div Yield", "key": "Div Yield", "format": "percent"} —
        # so this maps onto the existing key rather than adding a new column.
        "Div Yield": round(_f("dividend_yield"), 4),

        # ── Execution ranges ──────────────────────────────────────────────────
        # Now computed every cycle by StrategyEngine.evaluate_security() and
        # carried on the Recommendation (see engine/advisory.py's "buy_range"/
        # "sell_range" fields) — previously discarded before reaching this row.
        "buyRange": rec.buy_range,
        "sellRange": rec.sell_range,
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
        # docs/CONFIG_SCHEMA_PLAN.md Phase C1: "Advisory_Action" and
        # "Advisory_Rationale" were REMOVED here (they were dead code —
        # already-silently-dropped duplicates, never wired to a column):
        #   - Advisory_Action was byte-identical to the "Action Signal"
        #     column above (both read rec.action verbatim).
        #   - Advisory_Rationale was the untruncated rec.rationale, already
        #     surfaced (truncated) via "Advice" (rec.rationale[:200]) and
        #     "Strategy Explainer Notes" (rec.rationale[:150]) below.
        # The remaining three carry genuinely new information not exposed
        # by any other column and now have real config.COLUMN_SCHEMA slots
        # under "# --- ADVISORY METADATA ---" ("Score" and "Forecast_30_Pct"
        # above, in the Signal/Forecast sections, likewise now map onto
        # real ADVISORY METADATA columns instead of being silently dropped).
        "Advisory_Conviction": round(rec.conviction, 4),
        "Advisory_Position_Pct": round(rec.suggested_position_pct, 6),
        "Advisory_Data_Quality": rec.data_quality,

        # ── Placeholders for full-pipeline columns not produced by advisory ──
        "Strategy Explainer Notes": rec.rationale[:150] if rec.rationale else "",
        "Macro Status": "",
        "HMM_Risk_On_Probability": float("nan"),
    }


def write_recommendations(result: "RunResult", market: Optional["MarketDataProvider"] = None) -> None:
    """Write RunResult to Google Sheets (Stage F).

    Silently skipped when credentials.json is absent.  Errors are caught and
    logged — the Sheet is best-effort; analysis value must not depend on it.
    """
    gc = get_service_account_client()
    if gc is None:
        logger.info("Sheet write skipped — no Google Sheets client available.")
        return

    try:
        import gspread
        from gspread_dataframe import set_with_dataframe

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
            rows.append(rec_to_sheet_row(rec, result.snapshot, price))

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
            apply_conditional_formatting(sh, ws, list(df.columns))
        except Exception as cf_exc:
            logger.warning("Conditional formatting failed (non-critical): %s", cf_exc)

    except Exception as exc:
        logger.error("Sheet write failed: %s", exc)


def apply_conditional_formatting(
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
