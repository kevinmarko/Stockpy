"""pilots/observability.py — Mission-Control summary for the PWA (READ-ONLY).
=============================================================================

Ports the four highest-value sections of the retired Streamlit Command
Center's "Observability / Mission Control" tab
(``gui/panels/observability.py``) into a single composite read for the mobile
``GET /observability/summary`` endpoint:

1. **Portfolio risk metrics** — Sharpe / Calmar / Max Drawdown / Max-DD
   duration / CAGR, via ``evaluation_engine.calculate_equity_curve_metrics()``
   fed by ``data.historical_store.HistoricalStore.account_snapshot_history()``.
   This is the ACCOUNT-level equity curve (real Robinhood total_equity
   snapshots) — a different curve from the GUI panel's
   ``TransactionsStore``-derived *realized-trade* equity curve
   (``gui/panels/observability.py::_render_observability_equity_curve``); both
   are legitimate but answer different questions, and this endpoint
   deliberately follows the account-level one per the task's brief.
2. **Equity + drawdown + regime overlay** — the same account equity curve,
   plus a vectorized running peak-to-trough drawdown %, plus the current
   macro-regime telemetry already written to ``output/state_snapshot.json``
   by ``reporting/state_snapshot.py`` (``market_regime``, ``sahm_rule``,
   ``high_yield_oas``, ``yield_curve``, ``hmm_risk_on_probability``).
3. **Forecast skill (portfolio-wide)** — ``forecasting.forecast_tracker
   .ForecastTracker``'s reliability curve and inverse-RMSE skill weights,
   aggregated across ALL symbols for one horizon (not per-symbol like
   ``pilots/forecast_skill.py``, which backs ``GET /symbols/{ticker}/forecast``).
   NOTE: ``ForecastTracker.get_forecast_reliability_curve(symbol=None, ...)``
   genuinely supports a portfolio-wide aggregate, but
   ``ForecastTracker.get_skill_weights(symbol: str, ...)`` does NOT accept
   ``symbol=None`` — it unconditionally calls ``symbol.upper()``. Rather than
   fabricate a "portfolio-wide" formula, :func:`_portfolio_forecast_stats`
   below runs a direct read-only SQL aggregate over ``forecast_errors``
   (mirroring the exact pattern ``gui/panels/observability.py``'s
   ``_forecast_rmse_by_model``/``_forecast_skill_rows`` already use when the
   public tracker API doesn't expose an aggregation a caller needs) and
   applies the SAME cold-start-equal-weight / inverse-RMSE formula
   ``get_skill_weights`` uses internally — just without the per-symbol filter.
4. **Risk gate block log** — last ~100 entries from
   ``output/risk_gate_blocks.jsonl``. Ported verbatim (not imported) from
   ``gui/panels/_shared.py::load_block_log`` per this effort's scope, since
   ``api/pilots_api.py`` never reaches into ``gui.panels.*`` internals.

Design invariants (identical to the rest of the Pilots read layer):

* **Never raises (CONSTRAINT #6)** — every sub-section degrades independently
  to an honest empty/null shape + a ``reason`` string; one section's DB/file
  failure never breaks the other three.
* **Never fabricates (CONSTRAINT #4)** — NaN/undefined statistics are ``None``
  (JSON ``null``), never a guessed/fabricated number. Genuine zeros (e.g. zero
  drawdown on a curve that never dipped, zero blocked orders) stay real zeros.
* Imports ``data.historical_store.HistoricalStore``, ``evaluation_engine``, and
  ``forecasting.forecast_tracker.ForecastTracker`` — none of these are on
  ``api/pilots_api.py``'s AST-guard denylist (only ``processing_engine``,
  ``strategy_engine``, ``forecasting_engine``, ``macro_engine``,
  ``technical_options_engine``, ``main_orchestrator``, ``desktop`` are
  forbidden). Imports are lazy (inside function bodies), matching
  ``pilots/forecast_skill.py``/``pilots/realized.py``'s convention, so a
  missing/broken dependency degrades gracefully instead of breaking import of
  this module (and this whole API) at process start.
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "observability_summary",
    "portfolio_risk_metrics",
    "equity_curve_with_drawdown",
    "regime_overlay",
    "portfolio_forecast_skill",
    "risk_gate_block_log",
]

# Approximate calendar-day windows for the equity-curve zoom, matching
# api/pilots_api.py::get_equity_curve's own _RANGE_DAYS so the two surfaces
# agree on what "1Y" means. Duplicated locally (not imported) per this
# package's established convention of small, self-contained per-module glue
# (see gui/panels/_shared.py's comment on load_block_log for the same call).
_RANGE_DAYS: Dict[str, int] = {
    "1W": 7,
    "1M": 31,
    "3M": 93,
    "6M": 186,
    "1Y": 366,
    "2Y": 731,
}

_NO_SNAPSHOTS_REASON = (
    "No account snapshots yet — run the pipeline to start accumulating equity history."
)


def _finite_or_none(value: Any) -> Optional[float]:
    """Coerce to a finite float, else ``None`` (NaN -> ``null``, CONSTRAINT #4)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------------------
# 1. Portfolio risk metrics (Sharpe / Calmar / MaxDD / MaxDD-duration / CAGR)
# ---------------------------------------------------------------------------


def _empty_portfolio_risk(n_snapshots: int = 0, reason: Optional[str] = None) -> Dict[str, Any]:
    return {
        "sharpe_ratio": None,
        "calmar_ratio": None,
        "max_drawdown": None,
        "max_drawdown_duration_days": None,
        "cagr": None,
        "n_snapshots": n_snapshots,
        "min_snapshots_required": 20,
        "reason": reason or _NO_SNAPSHOTS_REASON,
    }


def portfolio_risk_metrics() -> Dict[str, Any]:
    """Sharpe / Calmar / MaxDD / MaxDD-duration / CAGR over the FULL account
    equity history (not range-zoomed — these are stable, all-history stats).

    Returns the honest empty shape (all ``None``, ``n_snapshots=0``) when the
    DB is cold, unreadable, or has fewer than
    ``evaluation_engine.MIN_SNAPSHOTS_FOR_STATS`` distinct daily snapshots.
    Never raises (CONSTRAINT #6).
    """
    try:
        from data.historical_store import HistoricalStore
        from evaluation_engine import MIN_SNAPSHOTS_FOR_STATS, calculate_equity_curve_metrics
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("portfolio_risk_metrics import failed: %s", exc)
        return _empty_portfolio_risk()

    try:
        store = HistoricalStore(readonly=True)
        equity_df = store.account_snapshot_history()
    except Exception as exc:  # noqa: BLE001 — dead-letter: cold/unreadable DB
        logger.warning("portfolio_risk_metrics: account_snapshot_history failed: %s", exc)
        return _empty_portfolio_risk()

    try:
        metrics = calculate_equity_curve_metrics(equity_df)
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.warning("portfolio_risk_metrics: calculate_equity_curve_metrics failed: %s", exc)
        return _empty_portfolio_risk()

    n_snapshots = int(metrics.get("n_snapshots", 0) or 0)
    reason: Optional[str] = None
    if n_snapshots == 0:
        reason = _NO_SNAPSHOTS_REASON
    elif n_snapshots < MIN_SNAPSHOTS_FOR_STATS:
        reason = (
            f"Only {n_snapshots} snapshot(s) so far — need at least "
            f"{MIN_SNAPSHOTS_FOR_STATS} for stable risk stats."
        )

    return {
        "sharpe_ratio": _finite_or_none(metrics.get("sharpe_ratio")),
        "calmar_ratio": _finite_or_none(metrics.get("calmar_ratio")),
        "max_drawdown": _finite_or_none(metrics.get("max_drawdown")),
        "max_drawdown_duration_days": _finite_or_none(metrics.get("max_drawdown_duration_days")),
        "cagr": _finite_or_none(metrics.get("cagr")),
        "n_snapshots": n_snapshots,
        "min_snapshots_required": MIN_SNAPSHOTS_FOR_STATS,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# 2. Equity curve + drawdown (regime overlay is separate — see regime_overlay)
# ---------------------------------------------------------------------------


def _slice_points_by_range(points: List[Dict[str, Any]], range_key: str) -> List[Dict[str, Any]]:
    """Tail-zoom ``points`` (each carrying a ``date`` ISO string) to the last
    ``_RANGE_DAYS[range_key]`` calendar days. Mirrors
    ``pilots/performance.py::_slice_curve_by_range``'s "honest zoom, never a
    recompute" contract, adapted for the ``{date, equity, drawdown}`` shape.
    An unknown range or unparseable dates return the full series. Never
    returns fewer than 2 points when >= 2 exist (a chart needs two)."""
    days = _RANGE_DAYS.get((range_key or "").upper())
    if not days or len(points) <= 2:
        return points
    try:
        last_day = date.fromisoformat(str(points[-1]["date"]))
        cutoff = last_day - timedelta(days=days)
        sliced = [p for p in points if date.fromisoformat(str(p["date"])) >= cutoff]
    except (ValueError, TypeError, KeyError):
        return points
    if len(sliced) < 2:
        return points[-2:]
    return sliced


def equity_curve_with_drawdown(range_key: str = "1Y") -> Dict[str, Any]:
    """Account equity curve + running peak-to-trough drawdown %, oldest→newest,
    tail-zoomed to ``range_key``.

    Drawdown is computed against the FULL all-time running peak (never reset
    at the zoom boundary — resetting it there would misrepresent a mid-window
    dip as the account's worst drawdown). Multiple same-day snapshots are
    deduped to the last one per day (mirrors
    ``evaluation_engine.calculate_equity_curve_metrics``'s own convention).
    Returns ``{range, points: [], reason}`` — never fabricated — when nothing
    is stored yet. Never raises (CONSTRAINT #6)."""
    try:
        import pandas as pd

        from data.historical_store import HistoricalStore
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("equity_curve_with_drawdown import failed: %s", exc)
        return {"range": range_key, "points": [], "reason": _NO_SNAPSHOTS_REASON}

    try:
        store = HistoricalStore(readonly=True)
        df = store.account_snapshot_history()
    except Exception as exc:  # noqa: BLE001 — dead-letter: cold/unreadable DB
        logger.warning("equity_curve_with_drawdown: account_snapshot_history failed: %s", exc)
        return {"range": range_key, "points": [], "reason": _NO_SNAPSHOTS_REASON}

    if (
        df is None
        or df.empty
        or "fetched_at" not in df.columns
        or "total_equity" not in df.columns
    ):
        return {"range": range_key, "points": [], "reason": _NO_SNAPSHOTS_REASON}

    try:
        d = df.copy()
        d["fetched_at"] = pd.to_datetime(d["fetched_at"], errors="coerce")
        d = d.dropna(subset=["fetched_at", "total_equity"]).sort_values("fetched_at")
        if d.empty:
            return {"range": range_key, "points": [], "reason": _NO_SNAPSHOTS_REASON}

        equity = d["total_equity"].astype(float)
        running_peak = equity.cummax()
        peak_floor = running_peak.clip(lower=1e-9)  # avoid /0 while equity <= 0
        drawdown = (equity - running_peak) / peak_floor

        d = d.assign(_equity=equity.values, _drawdown=drawdown.values)
        d["_date"] = d["fetched_at"].dt.strftime("%Y-%m-%d")
        # Dedupe multiple same-day snapshots to the LAST one per calendar day.
        d = d.drop_duplicates(subset="_date", keep="last")

        points = [
            {
                "date": row["_date"],
                "equity": float(row["_equity"]),
                "drawdown": float(row["_drawdown"]),
            }
            for row in d.to_dict(orient="records")
        ]
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.warning("equity_curve_with_drawdown: computation failed: %s", exc)
        return {"range": range_key, "points": [], "reason": "Equity curve computation failed."}

    sliced = _slice_points_by_range(points, range_key)
    return {
        "range": range_key,
        "points": sliced,
        "reason": None if sliced else "No account snapshots in the selected range.",
    }


# ---------------------------------------------------------------------------
# Regime overlay — sourced from output/state_snapshot.json (already-persisted,
# no live macro fetch). Takes the already-loaded snapshot dict as an argument
# (mirrors pilots/symbols.py::symbol_detail(snapshot, ticker)'s convention)
# rather than loading the file itself, so the caller controls path resolution.
# ---------------------------------------------------------------------------


def _empty_regime(reason: str) -> Dict[str, Any]:
    return {
        "as_of": None,
        "market_regime": None,
        "vix": None,
        "sahm_rule": None,
        "high_yield_oas": None,
        "yield_curve": None,
        "hmm_risk_on_probability": None,
        "kill_switch_active": None,
        "macro_regime_gate_enabled": None,
        "reason": reason,
    }


def regime_overlay(snapshot: Optional[dict]) -> Dict[str, Any]:
    """Current macro-regime telemetry from the persisted state snapshot.

    Fields mirror exactly what ``reporting/state_snapshot.py::write_state_snapshot``
    writes: ``market_regime``, ``vix``, ``sahm_rule``, ``high_yield_oas``,
    ``yield_curve``, ``hmm_risk_on_probability``, ``kill_switch_active``,
    ``macro_regime_gate_enabled``. ``None``/``null`` for any field the writer
    omitted (e.g. no macro DTO that cycle) — never fabricated (CONSTRAINT #4).
    Returns the honest empty shape + ``reason`` when no snapshot exists yet.
    Never raises (CONSTRAINT #6)."""
    if not snapshot:
        return _empty_regime("No state snapshot yet — run the pipeline first.")
    try:
        return {
            "as_of": snapshot.get("timestamp"),
            "market_regime": snapshot.get("market_regime"),
            "vix": _finite_or_none(snapshot.get("vix")),
            "sahm_rule": _finite_or_none(snapshot.get("sahm_rule")),
            "high_yield_oas": _finite_or_none(snapshot.get("high_yield_oas")),
            "yield_curve": _finite_or_none(snapshot.get("yield_curve")),
            "hmm_risk_on_probability": _finite_or_none(snapshot.get("hmm_risk_on_probability")),
            "kill_switch_active": snapshot.get("kill_switch_active"),
            "macro_regime_gate_enabled": snapshot.get("macro_regime_gate_enabled"),
            "reason": None,
        }
    except Exception as exc:  # noqa: BLE001 — dead-letter: malformed snapshot
        logger.debug("regime_overlay failed: %s", exc)
        return _empty_regime("State snapshot malformed or unreadable.")


# ---------------------------------------------------------------------------
# 3. Forecast skill — portfolio-wide (all symbols), one horizon.
# ---------------------------------------------------------------------------

# Mirrors forecasting.forecast_tracker._MIN_RMSE exactly (imported directly
# below to avoid drift; this constant is only a fallback if that import ever
# fails independently of the rest of the module).
_MIN_RMSE_FALLBACK = 0.01


def _portfolio_forecast_stats(
    db_path: str, horizon_days: int, window_days: int, min_obs: int
) -> Dict[str, Any]:
    """Direct read-only SQL aggregate over ``forecast_errors`` for ALL symbols
    at one horizon — reproduces ``ForecastTracker.get_skill_weights``'s exact
    cold-start / inverse-RMSE formula (see module docstring for why this
    can't just call that method with ``symbol=None``). Also computes
    pending/completed counts the same way ``pending_count``/``completed_count``
    do, minus the per-symbol filter those methods require.

    Returns ``{"skill_weights": {}, "pending": 0, "completed": 0}`` on any
    failure (missing DB file, no table yet, etc.) — never raises.
    """
    import sqlite3
    from datetime import datetime, timedelta as _timedelta, timezone

    from db_config import sqlite_readonly_uri
    from forecasting.forecast_tracker import _MIN_RMSE

    since_iso = (datetime.now(timezone.utc) - _timedelta(days=window_days)).isoformat()
    conn = sqlite3.connect(sqlite_readonly_uri(db_path), uri=True)
    try:
        skill_rows = conn.execute(
            """SELECT model_name, COUNT(*) AS n, AVG(squared_error) AS mse
               FROM forecast_errors
               WHERE horizon_days   = ?
                 AND actual_price   IS NOT NULL
                 AND forecast_ts    >= ?
               GROUP BY model_name""",
            (horizon_days, since_iso),
        ).fetchall()
        pending_row = conn.execute(
            """SELECT COUNT(*) FROM forecast_errors
               WHERE horizon_days = ? AND actual_price IS NULL""",
            (horizon_days,),
        ).fetchone()
        completed_row = conn.execute(
            """SELECT COUNT(*) FROM forecast_errors
               WHERE horizon_days = ? AND actual_price IS NOT NULL AND forecast_ts >= ?""",
            (horizon_days, since_iso),
        ).fetchone()
    finally:
        conn.close()

    pending = int(pending_row[0]) if pending_row else 0
    completed = int(completed_row[0]) if completed_row else 0

    if not skill_rows:
        return {"skill_weights": {}, "pending": pending, "completed": completed}

    model_stats = {
        r[0]: (int(r[1]), float(r[2]) if r[2] is not None else 0.0) for r in skill_rows
    }
    if any(n < min_obs for (n, _) in model_stats.values()):
        n_models = len(model_stats)
        weights = {name: 1.0 / n_models for name in model_stats}
    else:
        inv_rmse: Dict[str, float] = {}
        for name, (_, mse) in model_stats.items():
            rmse = math.sqrt(mse) if mse >= 0 else 0.0
            inv_rmse[name] = 1.0 / max(rmse, _MIN_RMSE)
        total = sum(inv_rmse.values())
        if total <= 0:
            n_models = len(inv_rmse)
            weights = {name: 1.0 / n_models for name in inv_rmse}
        else:
            weights = {name: w / total for name, w in inv_rmse.items()}

    return {"skill_weights": weights, "pending": pending, "completed": completed}


def portfolio_forecast_skill(
    horizon_days: int = 30,
    window_days: Optional[int] = None,
    min_obs: Optional[int] = None,
) -> Dict[str, Any]:
    """Portfolio-wide (all-symbol) forecast reliability curve + skill weights
    for one horizon, from ``forecasting/forecast_tracker.py``'s persisted
    ``forecast_errors`` history.

    ``window_days``/``min_obs`` default to ``settings.FORECAST_SKILL_WINDOW_DAYS``
    / ``settings.FORECAST_SKILL_MIN_OBS`` (the same knobs
    ``gui/panels/observability.py``'s Forecast Skill section reads), so this
    endpoint stays consistent with whatever the operator has configured.
    Returns empty collections + an honest ``reason`` when no forecast history
    exists yet. Never raises (CONSTRAINT #6)."""
    horizon = int(horizon_days)
    window = int(window_days) if window_days is not None else int(settings.FORECAST_SKILL_WINDOW_DAYS)
    min_o = int(min_obs) if min_obs is not None else int(settings.FORECAST_SKILL_MIN_OBS)

    no_history_reason = "No forecast history yet — run the pipeline to accumulate it."

    try:
        from forecasting.forecast_tracker import ForecastTracker

        # Read-only: a GET must never create the table as a side effect.
        tracker = ForecastTracker(readonly=True)
        db_path = tracker._db_path  # noqa: SLF001 — read-only path reuse, mirrors
        # gui/panels/observability.py's identical `tracker._db_path` reuse.
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("portfolio_forecast_skill: ForecastTracker unavailable: %s", exc)
        return {
            "horizon_days": horizon,
            "window_days": window,
            "min_obs": min_o,
            "reliability_curve": [],
            "skill_weights": {},
            "pending": 0,
            "completed": 0,
            "reason": no_history_reason,
        }

    reliability: List[Dict[str, Any]] = []
    try:
        curve_df = tracker.get_forecast_reliability_curve(symbol=None, horizon_days=horizon)
        if curve_df is not None and not curve_df.empty:
            for row in curve_df.to_dict(orient="records"):
                reliability.append(
                    {
                        "model_name": str(row.get("model_name") or ""),
                        "horizon_days": int(row.get("horizon_days"))
                        if row.get("horizon_days") is not None
                        else horizon,
                        "bin_center": _finite_or_none(row.get("bin_center")),
                        "mean_pct_error": _finite_or_none(row.get("mean_pct_error")),
                        "count": int(row.get("count") or 0),
                    }
                )
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("portfolio_forecast_skill: reliability curve failed: %s", exc)
        reliability = []

    try:
        stats = _portfolio_forecast_stats(db_path, horizon, window, min_o)
    except Exception as exc:  # noqa: BLE001 — dead-letter (missing DB file, etc.)
        logger.debug("portfolio_forecast_skill: aggregate stats failed: %s", exc)
        stats = {"skill_weights": {}, "pending": 0, "completed": 0}

    skill_weights = {
        str(k): w for k, v in stats.get("skill_weights", {}).items() if (w := _finite_or_none(v)) is not None
    }
    pending = int(stats.get("pending", 0) or 0)
    completed = int(stats.get("completed", 0) or 0)

    has_data = bool(reliability or skill_weights or pending or completed)
    return {
        "horizon_days": horizon,
        "window_days": window,
        "min_obs": min_o,
        "reliability_curve": reliability,
        "skill_weights": skill_weights,
        "pending": pending,
        "completed": completed,
        "reason": None if has_data else no_history_reason,
    }


# ---------------------------------------------------------------------------
# 4. Risk gate block log — ported verbatim from gui/panels/_shared.py's
# load_block_log (per this effort's scope: api/pilots_api.py doesn't reach
# into gui.panels.* internals for anything, so this is a deliberate small
# duplication rather than a cross-package import).
# ---------------------------------------------------------------------------


def risk_gate_block_log(n: int = 100) -> Dict[str, Any]:
    """Last ``n`` risk-gate block entries (newest first) from
    ``output/risk_gate_blocks.jsonl``.

    Returns ``{entries: [], count: 0, reason}`` — never fabricated — when the
    log doesn't exist yet or has no parseable rows. Never raises
    (CONSTRAINT #6); a malformed line is skipped, not fatal."""
    try:
        import json

        log_path = settings.OUTPUT_DIR / "risk_gate_blocks.jsonl"
        if not log_path.exists():
            return {"entries": [], "count": 0, "reason": "No risk-gate blocks logged yet."}
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        rows: List[dict] = []
        for line in lines[-n:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        rows = list(reversed(rows))
        return {
            "entries": rows,
            "count": len(rows),
            "reason": None if rows else "No parseable risk-gate block entries yet.",
        }
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("risk_gate_block_log failed: %s", exc)
        return {"entries": [], "count": 0, "reason": "Risk-gate block log unavailable."}


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------


def observability_summary(
    *,
    equity_range: str = "1Y",
    horizon_days: int = 30,
    snapshot: Optional[dict] = None,
) -> Dict[str, Any]:
    """Bundle all four Mission-Control sections into one payload for
    ``GET /observability/summary``. Each section degrades independently
    (CONSTRAINT #6) — a failure in one never blocks the other three."""
    return {
        "portfolio_risk": portfolio_risk_metrics(),
        "equity_curve": equity_curve_with_drawdown(equity_range),
        "regime": regime_overlay(snapshot),
        "forecast_skill": portfolio_forecast_skill(horizon_days),
        "risk_gate_blocks": risk_gate_block_log(),
    }
