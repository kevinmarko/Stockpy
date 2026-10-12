"""HTML daily-report adapter (extracted from ``main.py``).

This module is the thin adapter that turns a completed advisory ``RunResult``
into the daily HTML report. All actual HTML rendering is delegated to
``diagnostics_and_visuals.generate_html_report`` — the single rendering source
of truth. Nothing here reimplements any HTML.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from settings import settings
from reporting.state_snapshot import write_state_snapshot, load_snapshot_diff

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids circular import
    from main import RunResult
    from dto_models import MacroEconomicDTO

logger = logging.getLogger(__name__)


def write_html_report(result: "RunResult", macro_dto: "Optional[MacroEconomicDTO]" = None) -> None:
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
        write_state_snapshot(result, macro_dto)
        snapshot_diff = load_snapshot_diff()
        generate_html_report(
            portfolio_dicts, regime, out_path,
            account_summary=account_summary,
            snapshot_diff=snapshot_diff,
            **kw,
        )
        logger.info("HTML report written to %s.", out_path)

    except Exception as exc:
        logger.warning("HTML report failed (non-critical): %s", exc)
