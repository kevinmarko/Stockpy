"""pilots/observability.py — Mission-Control summary for the PWA (READ-ONLY).
=============================================================================

Ports the highest-value sections of the retired Streamlit Command
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
1b. **Portfolio heat** — aggregate adverse open-position P&L / total account
   equity, against ``settings.MAX_PORTFOLIO_HEAT``, sourced from the latest
   ``data.historical_store.HistoricalStore.latest_account_snapshot()`` — the
   same two inputs (per-position ``unrealized_pl``, account ``equity``)
   ``execution/risk_gate.py::portfolio_heat_check`` reads to gate live BUY
   orders. See :func:`portfolio_heat_metric`'s docstring for why this does
   NOT reproduce either of the two legacy Streamlit "Portfolio Heat" tiles'
   computations verbatim (both were found to be non-functional in practice).
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
3b. **Forecast skill by symbol** — the per-symbol × per-horizon breakdown the
   portfolio-wide section above doesn't carry (2026-08, closing a confirmed
   gap against the legacy panel). :func:`_forecast_stats_by_symbol` extends
   3's bulk-SQL-aggregate trick one dimension further (``GROUP BY symbol,
   horizon_days, model_name`` instead of just ``model_name``), replacing the
   legacy panel's N-symbols × 4-horizons × 3-calls-per-cell loop
   (``gui/panels/observability.py::_forecast_skill_rows``) with three bulk
   queries regardless of universe size. A requested symbol with zero DB rows
   still gets an entry (pending/completed 0, empty weights) — never silently
   dropped from the table.
4. **Risk gate block log** — last ~100 entries from
   ``output/risk_gate_blocks.jsonl``. Ported verbatim (not imported) from
   ``gui/panels/_shared.py::load_block_log`` per this effort's scope, since
   ``api/pilots_api.py`` never reaches into ``gui.panels.*`` internals.
5. **Circuit breaker dashboard** — the merged kill-switch + risk-gate-block
   severity view from ``gui/panels/gravity_audit.py
   ::_render_circuit_breaker_dashboard``. Unlike section 4 above,
   ``gui/circuit_breakers.py`` is NOT under ``gui.panels.*`` (it imports only
   ``json``/``logging``/``dataclasses``/``datetime``/``pathlib``/``typing`` —
   no streamlit, no heavy engines), so the "don't reach into gui.panels.*
   internals" rationale that justified re-porting ``load_block_log`` in
   section 4 does NOT apply here — this calls
   ``gui.circuit_breakers.collect_circuit_breaker_trips``/``summarise_trips``
   directly rather than adding a THIRD local copy of the same derivation.
   ``gui.daemon_client``/``gui.env_io``/``gui.ai_control_center``/
   ``gui.robinhood_execution_panel`` are already imported directly at
   ``api/pilots_api.py``'s module top, confirming ``gui.*`` (as opposed to
   ``gui.panels.*``) is an established, safe import surface for this
   AST-guarded API (see ``tests/test_pilots_api.py
   ::test_pilots_api_never_imports_heavy_engines``'s ``forbidden_modules``,
   which does not include ``gui``).
6. **System telemetry** — host + current-process CPU/memory/disk, ported
   from ``gui/panels/observability.py::_render_observability_system_telemetry``
   via ``gui.observability_telemetry.collect_system_telemetry()`` (already
   NaN/-1-shaped on missing ``psutil`` — CONSTRAINT #4). Unlike every other
   section here, this is NOT a persisted-artifact read — host resource usage
   is inherently point-in-time, so :func:`system_telemetry_summary` samples
   live on every call and reports no history. Folded into
   ``GET /observability/summary`` as a new ``system_telemetry`` key (cheap,
   scalar-only — same "ride the existing composite" call as sections 1b/5).
6b. **Data latency heatmap** — per-symbol quote fetch-to-ingestion latency
   (2026-08, closing a confirmed gap against the legacy panel). NOT a literal
   port: the legacy ``LatencySampleStore`` lived only in Streamlit
   ``st.session_state``, populated only on a manual "Fetch quotes" click — a
   stateless process has no equivalent session, and CONSTRAINT #4 forbids
   fabricating a history that isn't real (the same reasoning that already
   kept section 10's Heartbeat Age Trend unported). :func:`latency_heatmap_summary`
   instead reads ``market_data_latency.py``'s in-process ring buffer,
   recorded AUTOMATICALLY on every real (non-cache-hit) fetch through
   ``data/market_data.py::CompositeProvider.get_latest_quote`` — strictly
   more useful than the manual-trigger original — gated behind
   ``settings.MARKET_DATA_LATENCY_TRACKING_ENABLED`` (default ``False``).
   Never persisted to disk, so a restarted process honestly reports zero
   samples rather than a stale cross-restart figure.
7. **Log aggregation** — a bounded tail of ``logs/investyo.log``, parsed and
   tallied by level, ported from ``gui/panels/observability.py
   ::_render_observability_error_log`` via ``gui.observability_telemetry``'s
   pure parsing helpers (``read_log_tail``/``parse_log_lines``/
   ``tally_levels``/``classify_log_entry``). Served by its OWN
   ``GET /observability/logs`` endpoint rather than riding the summary
   composite — a log tail is a meaningfully heavier payload than the other
   (scalar) sections and is naturally an on-demand view, not something needed
   on every Mission Control page load. See :func:`log_aggregation`'s
   docstring for the deliberately-narrowed scope (counts, not the legacy
   panel's full per-symbol message drilldown).
8. **Sizing cap-event audit trail** — the last ``limit`` (default 100) durable
   position-sizing guardrail events from ``sizing/cap_audit_store.py``'s
   ``sizing_cap_events`` table, ported from ``gui/panels/observability.py
   ::_render_observability_sizing_cap_audit``. Reuses ``CapAuditStore``/
   ``_row_to_dict`` directly (no reimplementation) via a ``readonly=True``
   database-level read-only engine, matching that store's own convention.
   Degrades to an empty list + a ``reason`` when ``SIZING_CAP_AUDIT_ENABLED``
   is off or the store is unavailable — never raises.
9. **ETF volatility transmission** — the read-only per-symbol diagnostic view
   ported from ``gui/panels/observability.py
   ::_render_observability_etf_transmission``. Reuses
   ``gui.observability_panel_helpers.etf_transmission_rows`` directly (already
   pure/Streamlit-free and unit-tested) against the current state snapshot's
   ``signals`` list, plus the three independent master-switch states
   (``ETF_TRANSMISSION_ENABLED``/``_SIZING_ENABLED``/``_PORTFOLIO_ENABLED``).
10. **Heartbeat age** — the CURRENT orchestrator heartbeat age (seconds) +
    freshness classification, via ``gui.orchestrator_runner.heartbeat_age_seconds``
    and ``gui.observability_panel_helpers.heartbeat_status`` (both already
    reused elsewhere in this module/its sibling GUI panel). Deliberately does
    NOT attempt to reproduce the legacy Streamlit panel's "Heartbeat Age
    Trend" sparkline: that trend is a 60-sample ring buffer held ONLY in
    ``st.session_state`` (``gui.observability_telemetry.HeartbeatTrendStore``)
    — never persisted to disk — so there is no durable series this stateless
    HTTP endpoint could honestly serve. See :func:`heartbeat_summary`'s
    docstring for the full honesty note (``history_available=False`` always,
    with an explanatory ``history_note`` — never a fabricated single-point
    "trend").
11. **Strategy P&L** — realized P&L grouped by ``strategy`` from
    ``transactions_store.TransactionsStore.closed_trades_df()``. The legacy
    Streamlit section (``gui/panels/observability.py`` lines ~276-291) is
    itself DEAD CODE in practice — it groups by a ``strategy_id`` column that
    has never existed on the ``Trade`` model (the real column is
    ``strategy``) and reads a ``realized_pnl`` column that is never stored
    either (every other panel in that same file derives it on the fly from
    ``entry_price``/``exit_price``/``shares``/``side``) — so its
    ``{"realized_pnl", "strategy_id"} <= set(closed.columns)`` guard is always
    False against real data and the section always falls through to "No
    closed trades in transactions store yet.", regardless of how many closed
    trades actually exist. :func:`strategy_pnl_summary` below is the
    FUNCTIONAL version: it derives ``realized_pnl`` the same way
    ``gui/panels/observability.py::_render_observability_equity_curve``
    already does (``(exit_price - entry_price) * shares``, sign-flipped for
    shorts) and groups by the real ``strategy`` column, exposed on the wire as
    ``strategy_id`` for naming consistency with the rest of this API surface
    (``pilots/strategy_health.py`` et al.). Untagged trades (``strategy IS
    NULL``) are grouped under a real ``strategy_id: null`` bucket — never
    dropped, never mislabeled — since a trade's P&L is real money regardless
    of whether it was tagged.

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
from typing import Any, Dict, List, Optional, Tuple

from settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "observability_summary",
    "portfolio_risk_metrics",
    "portfolio_heat_metric",
    "equity_curve_with_drawdown",
    "regime_overlay",
    "portfolio_forecast_skill",
    "forecast_skill_by_symbol_summary",
    "risk_gate_block_log",
    "circuit_breaker_summary",
    "system_telemetry_summary",
    "latency_heatmap_summary",
    "log_aggregation",
    "sizing_cap_audit_summary",
    "etf_transmission_summary",
    "heartbeat_summary",
    "strategy_pnl_summary",
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
# 1b. Portfolio heat — aggregate adverse open-position P&L vs. total equity,
#     against the configured settings.MAX_PORTFOLIO_HEAT ceiling.
# ---------------------------------------------------------------------------


def _empty_portfolio_heat(n_positions: int = 0, reason: Optional[str] = None) -> Dict[str, Any]:
    return {
        "heat_pct": None,
        "max_portfolio_heat": _finite_or_none(settings.MAX_PORTFOLIO_HEAT),
        "over_limit": None,
        "n_positions": n_positions,
        "as_of": None,
        "reason": reason or (
            "No account snapshot yet — run `python3 main.py --refresh-account` "
            "to populate."
        ),
    }


def portfolio_heat_metric() -> Dict[str, Any]:
    """Live "Portfolio Heat" — aggregate adverse open-position P&L as a
    fraction of total account equity — against the configured
    ``settings.MAX_PORTFOLIO_HEAT`` ceiling.

    This intentionally reproduces ``execution/risk_gate.py::portfolio_heat_check``'s
    EXACT formula (the one that actually gates new BUY orders in production):
    ``sum(abs(unrealized_pl) for adverse positions) / account.equity``. It does
    NOT reproduce either of the two legacy Streamlit computations that share
    the same tile label, both of which were found on inspection to be
    non-functional in practice:

    * ``gui/panels/report_viewer.py``'s Report tab builds a ``pos_df`` with
      ``Symbol``/``Kelly Target`` columns and passes it to
      ``evaluation_engine.EvaluationEngine.calculate_portfolio_heat``, but that
      method actually looks for ``position_size``/``stop_loss_pct`` columns —
      neither present — so it always short-circuits to a static ``0.0``.
    * ``gui/panels/observability.py``'s Mission Control panel reads
      ``TransactionsStore.open_trades_df()`` for an ``unrealized_pnl`` column,
      but the ``Trade`` ORM model (``transactions_store.py``) has never had
      that column (or a ``market_value`` one) — the trades table only tracks
      entry/exit price and shares — so that tile's heat/gross/net metrics are
      likewise always "—" in practice today.

    The one place unrealized P&L per position AND total account equity are
    BOTH actually persisted together is the latest ``AccountSnapshot`` written
    by ``data/historical_store.py::HistoricalStore.save_account_snapshot`` —
    each ``PortfolioPosition`` carries ``unrealized_pl``, the snapshot carries
    ``total_equity`` — the same two inputs ``risk_gate.py``'s live gate reads
    from ``RiskContext.open_positions`` / ``account.equity``. This function
    reads that snapshot via ``HistoricalStore(readonly=True)`` (already an
    allowed import for this AST-guarded module — see file docstring).

    Returns the honest empty shape (``heat_pct=None``, never a fabricated
    ``0.0`` — CONSTRAINT #4) when no account snapshot is persisted yet, or its
    ``total_equity`` is missing/non-positive/non-finite. Never raises
    (CONSTRAINT #6).
    """
    try:
        from data.historical_store import HistoricalStore
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("portfolio_heat_metric import failed: %s", exc)
        return _empty_portfolio_heat()

    try:
        snapshot = HistoricalStore(readonly=True).latest_account_snapshot()
    except Exception as exc:  # noqa: BLE001 — dead-letter: cold/unreadable DB
        logger.warning("portfolio_heat_metric: latest_account_snapshot failed: %s", exc)
        return _empty_portfolio_heat()

    if snapshot is None:
        return _empty_portfolio_heat()

    positions = snapshot.positions or {}
    total_equity = _finite_or_none(getattr(snapshot, "total_equity", None))
    if total_equity is None or total_equity <= 0:
        return _empty_portfolio_heat(
            n_positions=len(positions),
            reason=(
                "Total account equity unavailable or non-positive — cannot "
                "compute the heat denominator honestly."
            ),
        )

    try:
        adverse = sum(
            abs(pl)
            for p in positions.values()
            if (pl := _finite_or_none(getattr(p, "unrealized_pl", None))) is not None and pl < 0
        )
        heat_pct = float(adverse) / total_equity
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.warning("portfolio_heat_metric: computation failed: %s", exc)
        return _empty_portfolio_heat(n_positions=len(positions))

    max_heat = _finite_or_none(settings.MAX_PORTFOLIO_HEAT)
    fetched_at = getattr(snapshot, "fetched_at", None)
    return {
        "heat_pct": heat_pct,
        "max_portfolio_heat": max_heat,
        "over_limit": (max_heat is not None and heat_pct > max_heat),
        "n_positions": len(positions),
        "as_of": fetched_at.isoformat() if fetched_at is not None else None,
        "reason": None,
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
        "macro_kill_switch": None,
        "reason": reason,
    }


def regime_overlay(snapshot: Optional[dict]) -> Dict[str, Any]:
    """Current macro-regime telemetry from the persisted state snapshot.

    Fields mirror exactly what ``reporting/state_snapshot.py::write_state_snapshot``
    writes: ``market_regime``, ``vix``, ``sahm_rule``, ``high_yield_oas``,
    ``yield_curve``, ``hmm_risk_on_probability``, ``kill_switch_active``,
    ``macro_regime_gate_enabled``, ``macro_kill_switch``. ``None``/``null`` for
    any field the writer omitted (e.g. no macro DTO that cycle) — never
    fabricated (CONSTRAINT #4). ``macro_kill_switch`` is a pure passthrough of
    the snapshot's own already-computed ``MacroEconomicDTO.killSwitch`` verdict
    -- never re-derived from raw thresholds here.
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
            "macro_kill_switch": snapshot.get("macro_kill_switch"),
            "reason": None,
        }
    except Exception as exc:  # noqa: BLE001 — dead-letter: malformed snapshot
        logger.debug("regime_overlay failed: %s", exc)
        return _empty_regime("State snapshot malformed or unreadable.")


# ---------------------------------------------------------------------------
# 3. Forecast skill — portfolio-wide (all symbols), one horizon.
# ---------------------------------------------------------------------------


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
    from forecasting.forecast_tracker import compute_skill_weights_from_stats

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
        return {"skill_weights": {}, "pending": pending, "completed": completed, "n_by_model": {}}

    model_stats = {
        r[0]: (int(r[1]), float(r[2]) if r[2] is not None else 0.0) for r in skill_rows
    }
    weights = compute_skill_weights_from_stats(model_stats, min_obs)

    return {
        "skill_weights": weights,
        "pending": pending,
        "completed": completed,
        "n_by_model": {name: n for name, (n, _) in model_stats.items()},
    }


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
        stats = {"skill_weights": {}, "pending": 0, "completed": 0, "n_by_model": {}}

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
        "n_by_model": {str(k): v for k, v in stats.get("n_by_model", {}).items()},
        "reason": None if has_data else no_history_reason,
    }


def _forecast_stats_by_symbol(
    db_path: str, symbols: List[str], horizon_days: int, window_days: int, min_obs: int
) -> Dict[str, Dict[str, Any]]:
    """Direct read-only SQL aggregate over ``forecast_errors``, grouped by
    ``(symbol, model_name)`` — the bulk-query sibling of
    :func:`_portfolio_forecast_stats`, replacing the legacy GUI panel's
    N-symbols x 4-horizons x 3-calls-per-cell loop
    (``gui/panels/observability.py::_forecast_skill_rows``) with three
    grouped queries total, regardless of how many symbols are requested.
    Applies the SAME cold-start / inverse-RMSE formula per symbol as
    ``_portfolio_forecast_stats``/``ForecastTracker.get_skill_weights`` —
    just computed in Python from one bulk result set per query instead of
    calling that per-symbol method in a loop.

    Returns ``{symbol: {"skill_weights": {...}, "pending": n, "completed": n}}``
    — a symbol with zero matching rows is simply absent from the dict (the
    caller fills in the honest zero/empty entry), never raises."""
    import sqlite3
    from datetime import datetime, timedelta as _timedelta, timezone

    from db_config import sqlite_readonly_uri
    from forecasting.forecast_tracker import compute_skill_weights_from_stats

    if not symbols:
        return {}

    # Bandit's B608 flags every f-string-built SQL statement below purely on
    # syntax; none of the three actually interpolate untrusted data. Only
    # `placeholders` (a fixed-length string of `?,?,...` derived from
    # len(symbols), never from symbol VALUES) and `_MIN_RMSE`-style column
    # names are interpolated -- every real value flows through the
    # parameterized `?` bindings passed as the second `conn.execute()` arg,
    # same convention as data/historical_store.py. Reviewed false positive.
    placeholders = ",".join("?" for _ in symbols)
    since_iso = (datetime.now(timezone.utc) - _timedelta(days=window_days)).isoformat()
    conn = sqlite3.connect(sqlite_readonly_uri(db_path), uri=True)
    try:
        skill_rows = conn.execute(
            f"""SELECT symbol, model_name, COUNT(*) AS n, AVG(squared_error) AS mse
                FROM forecast_errors
                WHERE horizon_days = ?
                  AND actual_price IS NOT NULL
                  AND forecast_ts  >= ?
                  AND symbol IN ({placeholders})
                GROUP BY symbol, model_name""",  # nosec B608
            (horizon_days, since_iso, *symbols),
        ).fetchall()
        pending_rows = conn.execute(
            f"""SELECT symbol, COUNT(*) FROM forecast_errors
                WHERE horizon_days = ? AND actual_price IS NULL
                  AND symbol IN ({placeholders})
                GROUP BY symbol""",  # nosec B608
            (horizon_days, *symbols),
        ).fetchall()
        completed_rows = conn.execute(
            f"""SELECT symbol, COUNT(*) FROM forecast_errors
                WHERE horizon_days = ? AND actual_price IS NOT NULL AND forecast_ts >= ?
                  AND symbol IN ({placeholders})
                GROUP BY symbol""",  # nosec B608
            (horizon_days, since_iso, *symbols),
        ).fetchall()
    finally:
        conn.close()

    pending_by_symbol = {r[0]: int(r[1]) for r in pending_rows}
    completed_by_symbol = {r[0]: int(r[1]) for r in completed_rows}

    stats_by_symbol: Dict[str, Dict[str, Any]] = {}
    for sym, model_name, n, mse in skill_rows:
        stats_by_symbol.setdefault(sym, {})[model_name] = (int(n), float(mse) if mse is not None else 0.0)

    result: Dict[str, Dict[str, Any]] = {}
    for sym, model_stats in stats_by_symbol.items():
        weights = compute_skill_weights_from_stats(model_stats, min_obs)
        result[sym] = {
            "skill_weights": weights,
            "pending": pending_by_symbol.get(sym, 0),
            "completed": completed_by_symbol.get(sym, 0),
            "n_by_model": {name: n for name, (n, _) in model_stats.items()},
        }

    # A symbol with pending/completed counts but NO model ever cleared
    # min_obs's floor for a weight row still needs its counts surfaced.
    for sym in set(pending_by_symbol) | set(completed_by_symbol):
        result.setdefault(
            sym,
            {
                "skill_weights": {},
                "pending": pending_by_symbol.get(sym, 0),
                "completed": completed_by_symbol.get(sym, 0),
                "n_by_model": {},
            },
        )

    return result


def _skill_from_pooled_stats(n: int, mse: Optional[float], min_obs: int) -> Optional[float]:
    """Raw (un-normalized) inverse-RMSE 'skill' score for one pooled
    (all-models-combined) sub-window — the SAME formula piece
    ``compute_skill_weights_from_stats`` uses per model
    (``1.0 / max(rmse, _MIN_RMSE)``), reusing that module's own
    ``_MIN_RMSE`` floor so the two never drift apart (see
    ``compute_skill_weights_from_stats``'s docstring on the "three copies"
    bug this codebase already hit once).

    Deliberately NOT ``compute_skill_weights_from_stats`` itself: that
    function normalizes across models to relative blend weights (a single
    pooled entry would always normalize to 1.0, discarding the very
    magnitude decay needs to compare across time). This returns the
    pre-normalization absolute skill level instead.

    Returns ``None`` (never a fabricated number, CONSTRAINT #4) when
    ``n < min_obs`` — not enough completed, actualized forecasts in this
    sub-window to trust its RMSE — or when ``mse`` is missing/negative."""
    if n < min_obs or mse is None or mse < 0:
        return None
    from forecasting.forecast_tracker import _MIN_RMSE

    rmse = math.sqrt(mse)
    return 1.0 / max(rmse, _MIN_RMSE)


def _forecast_decay_stats_by_symbol(
    db_path: str, symbols: List[str], horizon_days: int, window_days: int, min_obs: int
) -> Dict[str, Dict[str, Any]]:
    """Per-symbol ``decay_pct``: how much a symbol's pooled (all-models)
    forecast skill has degraded from an older baseline sub-window to the
    most recent sub-window, both carved out of the same ``window_days``.

    Split: the second half of ``window_days`` (most recent) is "recent"; the
    first half is "baseline" — an even split of the SAME window the rest of
    this module already uses, rather than a second independently-tunable
    knob (product judgment call — see task write-up).

    Pools across ALL models per sub-window (not per-model) — decay_pct is a
    single symbol-level headline number; :func:`_forecast_stats_by_symbol`'s
    ``skill_weights`` already carries the per-model breakdown alongside it.

    ``decay_pct = (baseline_skill - recent_skill) / baseline_skill * 100``
    using :func:`_skill_from_pooled_stats` for both — positive means skill is
    degrading (recent RMSE worse than baseline), negative means it improved.
    ``baseline_skill`` is always > 0 when not ``None`` (the ``_MIN_RMSE``
    floor forbids a zero), so this never divides by zero.

    Returns ``{symbol: {"decay_pct": float | None, "decay_reason": str | None}}``.
    A symbol with insufficient completed forecasts in either sub-window gets
    ``decay_pct: None`` with an honest ``decay_reason`` — never a fabricated
    number (CONSTRAINT #4). Never raises (CONSTRAINT #6); any DB/query
    failure degrades every requested symbol to that same honest ``None``."""
    import sqlite3
    from datetime import datetime, timedelta as _timedelta, timezone

    from db_config import sqlite_readonly_uri

    insufficient_reason = (
        f"Fewer than {min_obs} completed forecasts in the recent and/or baseline "
        f"half of the {window_days}d window — not enough history for a reliable "
        "before/after comparison yet."
    )
    fallback = {sym: {"decay_pct": None, "decay_reason": insufficient_reason} for sym in symbols}

    if not symbols:
        return {}

    try:
        now = datetime.now(timezone.utc)
        since_iso = (now - _timedelta(days=window_days)).isoformat()
        mid_iso = (now - _timedelta(days=window_days / 2.0)).isoformat()

        # See _forecast_stats_by_symbol's comment above on the Bandit B608
        # false positive — same convention: only `placeholders` (derived
        # from len(symbols), never symbol VALUES) is interpolated; every
        # real value flows through parameterized `?` bindings.
        placeholders = ",".join("?" for _ in symbols)
        conn = sqlite3.connect(sqlite_readonly_uri(db_path), uri=True)
        try:
            half_rows = conn.execute(
                f"""SELECT symbol,
                           CASE WHEN forecast_ts >= ? THEN 'recent' ELSE 'baseline' END AS half,
                           COUNT(*) AS n,
                           AVG(squared_error) AS mse
                    FROM forecast_errors
                    WHERE horizon_days = ?
                      AND actual_price IS NOT NULL
                      AND forecast_ts  >= ?
                      AND symbol IN ({placeholders})
                    GROUP BY symbol, half""",  # nosec B608
                (mid_iso, horizon_days, since_iso, *symbols),
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — dead-letter (missing DB file, etc.)
        logger.debug("_forecast_decay_stats_by_symbol: bulk query failed: %s", exc)
        return fallback

    by_symbol: Dict[str, Dict[str, Tuple[int, Optional[float]]]] = {}
    for sym, half, n, mse in half_rows:
        by_symbol.setdefault(sym, {})[half] = (int(n), float(mse) if mse is not None else None)

    result: Dict[str, Dict[str, Any]] = {}
    for sym in symbols:
        halves = by_symbol.get(sym, {})
        n_recent, mse_recent = halves.get("recent", (0, None))
        n_baseline, mse_baseline = halves.get("baseline", (0, None))

        recent_skill = _skill_from_pooled_stats(n_recent, mse_recent, min_obs)
        baseline_skill = _skill_from_pooled_stats(n_baseline, mse_baseline, min_obs)

        if recent_skill is None or baseline_skill is None:
            result[sym] = {"decay_pct": None, "decay_reason": insufficient_reason}
        else:
            result[sym] = {
                "decay_pct": (baseline_skill - recent_skill) / baseline_skill * 100.0,
                "decay_reason": None,
            }

    return result


def forecast_skill_by_symbol_summary(
    snapshot: Optional[dict],
    horizon_days: int = 30,
    window_days: Optional[int] = None,
    min_obs: Optional[int] = None,
) -> Dict[str, Any]:
    """Per-symbol forecast-skill table for one horizon — the granular
    breakdown the legacy Streamlit panel showed
    (``gui/panels/observability.py::_render_observability_forecast_skill``)
    that the portfolio-wide :func:`portfolio_forecast_skill` section doesn't
    carry. Symbols come from ``snapshot``'s ``signals`` list, deduped and
    capped to the first 30 — the exact same source and bound the legacy
    panel's own ``_forecast_skill_rows`` loader uses.

    Same ``window_days``/``min_obs`` defaults and the SAME cold-start/
    inverse-RMSE formula as the portfolio-wide section — see
    :func:`_forecast_stats_by_symbol`. Each row also carries a per-symbol
    ``decay_pct`` (see :func:`_forecast_decay_stats_by_symbol`) — the
    forecast-skill-decay signal ``investyo_mcp_server.py::get_model_drift_report``
    reports; a symbol with too little history for a valid before/after split
    gets ``decay_pct: None`` plus an honest ``decay_reason``, never a
    fabricated percentage (CONSTRAINT #4). NOTE: :func:`portfolio_forecast_skill`
    deliberately does NOT get a portfolio-wide decay figure — nothing
    downstream consumes one today (only this per-symbol summary feeds
    ``get_model_drift_report``); see the task write-up for that call.

    Never raises (CONSTRAINT #6); an empty universe or a totally unavailable
    tracker both degrade to an empty ``rows`` list plus an honest ``reason``
    — never a table of fabricated zeros (CONSTRAINT #4)."""
    horizon = int(horizon_days)
    window = int(window_days) if window_days is not None else int(settings.FORECAST_SKILL_WINDOW_DAYS)
    min_o = int(min_obs) if min_obs is not None else int(settings.FORECAST_SKILL_MIN_OBS)

    symbols: List[str] = []
    for s in (snapshot or {}).get("signals", []) or []:
        sym = s.get("symbol") if isinstance(s, dict) else None
        if sym and sym not in symbols:
            symbols.append(str(sym))
    bounded_symbols = symbols[:30]

    no_symbols_reason = "No symbols in the last pipeline snapshot to score."
    no_history_reason = "No forecast history yet — run the pipeline to accumulate it."

    if not bounded_symbols:
        return {
            "horizon_days": horizon,
            "window_days": window,
            "min_obs": min_o,
            "rows": [],
            "reason": no_symbols_reason,
        }

    try:
        from forecasting.forecast_tracker import ForecastTracker

        # Read-only: a GET must never create the table as a side effect.
        tracker = ForecastTracker(readonly=True)
        db_path = tracker._db_path  # noqa: SLF001 — matches portfolio_forecast_skill's identical reuse
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("forecast_skill_by_symbol_summary: ForecastTracker unavailable: %s", exc)
        return {
            "horizon_days": horizon,
            "window_days": window,
            "min_obs": min_o,
            "rows": [],
            "reason": no_history_reason,
        }

    try:
        stats_by_symbol = _forecast_stats_by_symbol(db_path, bounded_symbols, horizon, window, min_o)
    except Exception as exc:  # noqa: BLE001 — dead-letter (missing DB file, etc.)
        logger.debug("forecast_skill_by_symbol_summary: bulk query failed: %s", exc)
        stats_by_symbol = {}

    try:
        decay_by_symbol = _forecast_decay_stats_by_symbol(db_path, bounded_symbols, horizon, window, min_o)
    except Exception as exc:  # noqa: BLE001 — dead-letter (missing DB file, etc.)
        logger.debug("forecast_skill_by_symbol_summary: decay query failed: %s", exc)
        decay_by_symbol = {}

    rows: List[Dict[str, Any]] = []
    any_history = False
    for sym in bounded_symbols:
        stats = stats_by_symbol.get(
            sym, {"skill_weights": {}, "pending": 0, "completed": 0, "n_by_model": {}}
        )
        skill_weights = {
            str(k): w for k, v in stats.get("skill_weights", {}).items() if (w := _finite_or_none(v)) is not None
        }
        pending = int(stats.get("pending", 0) or 0)
        completed = int(stats.get("completed", 0) or 0)
        if skill_weights or pending or completed:
            any_history = True
        decay = decay_by_symbol.get(
            sym,
            {
                "decay_pct": None,
                "decay_reason": "No forecast history yet — run the pipeline to accumulate it.",
            },
        )
        rows.append(
            {
                "symbol": sym,
                "pending": pending,
                "completed": completed,
                "skill_weights": skill_weights,
                "n_by_model": stats.get("n_by_model", {}),
                "decay_pct": _finite_or_none(decay.get("decay_pct")),
                "decay_reason": decay.get("decay_reason"),
            }
        )

    if not any_history:
        return {
            "horizon_days": horizon,
            "window_days": window,
            "min_obs": min_o,
            "rows": [],
            "reason": no_history_reason,
        }

    return {
        "horizon_days": horizon,
        "window_days": window,
        "min_obs": min_o,
        "rows": rows,
        "reason": None,
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
# 5. Circuit breaker dashboard — merged kill-switch + risk-gate-block severity
# view. Calls gui.circuit_breakers directly (lazy import) rather than
# re-deriving the classification here — see module docstring section 5 for
# why this is NOT the same situation as section 4's local load_block_log port.
# ---------------------------------------------------------------------------


def _empty_circuit_breakers(window_hours: int = 24, reason: Optional[str] = None) -> Dict[str, Any]:
    return {
        "trips": [],
        "counts": {"critical": 0, "warning": 0, "total": 0},
        "window_hours": window_hours,
        "reason": reason or "No active circuit-breaker trips.",
    }


def circuit_breaker_summary(window_hours: int = 24) -> Dict[str, Any]:
    """Merged kill-switch + risk-gate-block severity dashboard — the PWA's
    port of ``gui/panels/gravity_audit.py::_render_circuit_breaker_dashboard``.

    Calls ``gui.circuit_breakers.collect_circuit_breaker_trips`` +
    ``summarise_trips`` directly (lazy import, matching this module's own
    convention for ``data.historical_store``/``forecasting.forecast_tracker``)
    against the SAME two files the GUI panel reads:
    ``settings.OUTPUT_DIR / "KILL_SWITCH"`` and
    ``settings.OUTPUT_DIR / "risk_gate_blocks.jsonl"``. The kill switch (when
    active) always sorts first; the remaining trips are deduped to the most
    recent one per ``(check, strategy)`` within ``window_hours`` and sorted
    newest-first — see ``gui/circuit_breakers.py``'s own docstrings for the
    full dedup/classification contract (adding a new breaker means adding a
    check inside ``execution/risk_gate.py`` and tagging it in that module's
    ``_KNOWN_CHECKS``, never editing this function).

    Each trip carries ``severity`` (``"CRITICAL"``/``"WARNING"``),
    ``threshold``/``observed`` (``None`` when the underlying block didn't
    record one — never fabricated, CONSTRAINT #4), and ``triggered_at`` (an
    ISO timestamp string, ``None`` for events that don't carry one, e.g. a
    kill-switch sentinel with no readable mtime). ``counts`` feeds the PWA's
    KPI strip. Returns the honest empty shape (empty ``trips``, zeroed
    ``counts``, a ``reason``) when nothing is tripped, the derivation module
    can't be imported, or the underlying files can't be read. Never raises
    (CONSTRAINT #6)."""
    try:
        from gui.circuit_breakers import collect_circuit_breaker_trips, summarise_trips
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("circuit_breaker_summary import failed: %s", exc)
        return _empty_circuit_breakers(window_hours)

    try:
        trips = collect_circuit_breaker_trips(
            kill_switch_sentinel=settings.OUTPUT_DIR / "KILL_SWITCH",
            block_log_path=settings.OUTPUT_DIR / "risk_gate_blocks.jsonl",
            window=timedelta(hours=window_hours),
        )
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.warning("circuit_breaker_summary: collect_circuit_breaker_trips failed: %s", exc)
        return _empty_circuit_breakers(
            window_hours, reason="Circuit-breaker derivation unavailable."
        )

    try:
        tally = summarise_trips(trips)
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("circuit_breaker_summary: summarise_trips failed: %s", exc)
        tally = {"CRITICAL": 0, "WARNING": 0, "TOTAL": len(trips)}

    entries = [
        {
            "name": t.name,
            "severity": t.severity,
            "summary": t.summary,
            "triggered_at": t.triggered_at.isoformat() if t.triggered_at else None,
            "threshold": _finite_or_none(t.threshold),
            "observed": _finite_or_none(t.observed),
        }
        for t in trips
    ]

    return {
        "trips": entries,
        "counts": {
            "critical": int(tally.get("CRITICAL", 0) or 0),
            "warning": int(tally.get("WARNING", 0) or 0),
            "total": int(tally.get("TOTAL", len(entries)) or 0),
        },
        "window_hours": window_hours,
        "reason": (
            None if entries
            else f"No active circuit-breaker trips in the last {window_hours}h."
        ),
    }


# ---------------------------------------------------------------------------
# 6. System telemetry — host + current-process CPU/memory/disk. Point-in-time
# only (no persisted history — see module docstring section 6). Calls
# gui.observability_telemetry.collect_system_telemetry directly (lazy import,
# same gui.* precedent as circuit_breaker_summary above) rather than
# re-deriving psutil sampling here.
# ---------------------------------------------------------------------------


def _empty_system_telemetry(reason: str) -> Dict[str, Any]:
    return {
        "psutil_available": False,
        "cpu_percent": None,
        "cpu_count_logical": None,
        "load_avg_1m": None,
        "memory_percent": None,
        "memory_used_bytes": None,
        "memory_total_bytes": None,
        "disk_percent": None,
        "disk_used_bytes": None,
        "disk_total_bytes": None,
        "process_rss_bytes": None,
        "process_cpu_percent": None,
        "process_threads": None,
        "sampled_at": None,
        "reason": reason,
    }


def system_telemetry_summary() -> Dict[str, Any]:
    """Host + current-process CPU/memory/disk snapshot — the PWA's port of
    ``gui/panels/observability.py::_render_observability_system_telemetry``.

    Unlike every other section in this module, this is NOT a read of a
    persisted artifact — host resource usage is inherently point-in-time, so
    there is no history to report and none is fabricated (CONSTRAINT #4).
    Every call re-samples via ``gui.observability_telemetry
    .collect_system_telemetry()`` (already backed by ``psutil``, a hard
    ``requirements.txt`` dependency — NOT ``requirements-optional.txt``).

    Reuses that function's existing NaN/-1-shaped fallback
    (``psutil_available=False``) when ``psutil`` is unavailable or sampling
    raises, converting it to this module's ``None``-for-missing convention.
    Never raises (CONSTRAINT #6)."""
    try:
        from gui.observability_telemetry import collect_system_telemetry
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("system_telemetry_summary import failed: %s", exc)
        return _empty_system_telemetry("Telemetry module unavailable.")

    try:
        t = collect_system_telemetry()
    except Exception as exc:  # noqa: BLE001 — dead-letter: sampling failure
        logger.warning("system_telemetry_summary: collect_system_telemetry failed: %s", exc)
        return _empty_system_telemetry("Telemetry sampling failed.")

    if not t.psutil_available:
        return _empty_system_telemetry("psutil is not available in this environment.")

    return {
        "psutil_available": True,
        "cpu_percent": _finite_or_none(t.cpu_percent),
        "cpu_count_logical": t.cpu_count_logical if t.cpu_count_logical >= 0 else None,
        "load_avg_1m": _finite_or_none(t.load_avg_1m),
        "memory_percent": _finite_or_none(t.memory_percent),
        "memory_used_bytes": t.memory_used_bytes if t.memory_used_bytes >= 0 else None,
        "memory_total_bytes": t.memory_total_bytes if t.memory_total_bytes >= 0 else None,
        "disk_percent": _finite_or_none(t.disk_percent),
        "disk_used_bytes": t.disk_used_bytes if t.disk_used_bytes >= 0 else None,
        "disk_total_bytes": t.disk_total_bytes if t.disk_total_bytes >= 0 else None,
        "process_rss_bytes": t.process_rss_bytes if t.process_rss_bytes >= 0 else None,
        "process_cpu_percent": _finite_or_none(t.process_cpu_percent),
        "process_threads": t.process_threads if t.process_threads >= 0 else None,
        "sampled_at": t.sampled_at.isoformat(),
        "reason": None,
    }


# ---------------------------------------------------------------------------
# 6b. Data latency heatmap — per-symbol quote fetch-to-ingestion latency, from
# market_data_latency.py's automatic, in-process ring buffer. See that
# module's docstring for why this is an honest REPLACEMENT for (not a literal
# port of) the legacy Streamlit panel's manual-"click Fetch quotes"-trigger,
# Streamlit-session-local design — samples are recorded automatically on
# every real quote fetch, and clear on process restart rather than never.
# ---------------------------------------------------------------------------


def _empty_latency_heatmap(reason: str) -> Dict[str, Any]:
    return {
        "tracking_enabled": bool(settings.MARKET_DATA_LATENCY_TRACKING_ENABLED),
        "count": 0,
        "p50": None,
        "p95": None,
        "worst_symbol": None,
        "worst_p95": None,
        "rows": [],
        "reason": reason,
    }


def latency_heatmap_summary(limit: int = 200) -> Dict[str, Any]:
    """Per-symbol quote-latency samples recorded since this API process last
    started (``market_data_latency.py``'s in-process ring buffer). Degrades to
    an honest empty shape (CONSTRAINT #4) when tracking is disabled
    (``MARKET_DATA_LATENCY_TRACKING_ENABLED=False``, the default) or no
    samples have been recorded yet in this process — never raises
    (CONSTRAINT #6). Never persisted to disk, so a restarted API process
    honestly reports zero samples rather than serving a stale cross-restart
    figure — the exact honesty framing ``heartbeat_summary`` already uses."""
    tracking_enabled = bool(settings.MARKET_DATA_LATENCY_TRACKING_ENABLED)
    if not tracking_enabled:
        return _empty_latency_heatmap(
            "MARKET_DATA_LATENCY_TRACKING_ENABLED is False — latency samples "
            "are not recorded this process."
        )

    try:
        import market_data_latency
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("latency_heatmap_summary import failed: %s", exc)
        return _empty_latency_heatmap("Latency tracking module unavailable.")

    try:
        samples = market_data_latency.get_ring().samples()
    except Exception as exc:  # noqa: BLE001 — dead-letter: ring read failure
        logger.debug("latency_heatmap_summary: ring read failed: %s", exc)
        return _empty_latency_heatmap("Latency samples unreadable.")

    if not samples:
        return _empty_latency_heatmap(
            "No latency samples yet this process — tracking is on, but no "
            "quote has been fetched yet."
        )

    summary = market_data_latency.summarize_latency(samples)
    # Newest first, matching every other bounded-tail section's convention
    # (risk_gate_block_log, sizing_cap_audit_summary).
    rows = [
        {
            "symbol": s.symbol,
            "source": s.source,
            "quote_timestamp": s.quote_timestamp.isoformat(),
            "ingested_at": s.ingested_at.isoformat(),
            "latency_seconds": round(s.latency_seconds, 3),
            "is_stale": s.is_stale,
        }
        for s in sorted(samples, key=lambda s: s.ingested_at, reverse=True)[:limit]
    ]

    return {
        "tracking_enabled": True,
        "count": summary["count"],
        "p50": _finite_or_none(summary["p50"]),
        "p95": _finite_or_none(summary["p95"]),
        "worst_symbol": summary["worst_symbol"],
        "worst_p95": _finite_or_none(summary["worst_p95"]),
        "rows": rows,
        "reason": None,
    }


# ---------------------------------------------------------------------------
# 7. Log aggregation — bounded tail of logs/investyo.log, parsed + tallied.
# Deliberately its OWN endpoint (GET /observability/logs), not folded into the
# summary composite: unlike the other (cheap, scalar) sections, a log tail is
# a meaningfully heavier payload naturally viewed on-demand rather than on
# every Mission Control page load. Calls gui.observability_telemetry's pure
# parsing helpers directly (lazy import, same gui.* precedent as
# circuit_breaker_summary/system_telemetry_summary above) rather than
# re-deriving log parsing/classification here.
# ---------------------------------------------------------------------------

# Mirrors gui/panels/observability.py's own read_log_tail(..., max_lines=1000)
# call — reads the same bounded tail the legacy panel does.
_LOG_TAIL_READ_LINES = 1000


def _empty_log_aggregation(reason: str, log_path: Optional[str] = None) -> Dict[str, Any]:
    return {
        "log_path": log_path,
        "total_lines": 0,
        "tally": {"CRITICAL": 0, "ERROR": 0, "WARNING": 0, "INFO": 0, "DEBUG": 0, "UNPARSED": 0},
        "systemic_count": 0,
        "symbol_specific_count": 0,
        "entries": [],
        "returned_count": 0,
        "reason": reason,
    }


def log_aggregation(limit: int = 300) -> Dict[str, Any]:
    """Tail + parse + classify ``logs/investyo.log`` (the rotating handler
    ``alerting.setup_logging()`` configures) — the PWA's port of
    ``gui/panels/observability.py::_render_observability_error_log``'s core
    read path.

    Reads the last 1000 raw lines (matching the legacy panel's own
    ``read_log_tail(..., max_lines=1000)`` call), parses each into a typed
    entry, tallies by level over the FULL read tail, and returns the most
    recent ``limit`` entries (oldest-first, matching the legacy panel's
    ``st.code`` rendering order) for the frontend to filter client-side by
    level/substring — mirroring the legacy Streamlit panel's own UX, where a
    ``st.selectbox``/``st.text_input`` re-filters an already-fetched,
    already-parsed list on every rerun rather than re-querying the backend
    per keystroke.

    Deliberately excludes the legacy panel's per-symbol "Contextual Error
    Summary" expander (grouped message lists keyed by ticker) — this reader
    surfaces just the systemic/symbol-specific COUNTS (``systemic_count``/
    ``symbol_specific_count``), which is what a quick mobile diagnostic glance
    needs; the message-level per-ticker drilldown is a desktop-triage
    workflow this endpoint doesn't attempt to reproduce (a scope-narrowing
    call consistent with this item's own "low priority for a remote/mobile
    PWA" framing).

    Returns the honest empty shape (zeroed tally, empty ``entries``, a
    ``reason``) when the log file doesn't exist yet or the module can't be
    imported. Never raises (CONSTRAINT #6)."""
    try:
        from gui.observability_telemetry import (
            classify_log_entry,
            parse_log_lines,
            read_log_tail,
            tally_levels,
        )
        from gui.orchestrator_runner import TELEMETRY_LOG_PATH
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("log_aggregation import failed: %s", exc)
        return _empty_log_aggregation("Log module unavailable.")

    log_path_str = str(TELEMETRY_LOG_PATH)

    try:
        raw_lines = read_log_tail(TELEMETRY_LOG_PATH, max_lines=_LOG_TAIL_READ_LINES)
    except Exception as exc:  # noqa: BLE001 — dead-letter: unreadable file
        logger.warning("log_aggregation: read_log_tail failed: %s", exc)
        return _empty_log_aggregation("Log file unreadable.", log_path_str)

    if not raw_lines:
        return _empty_log_aggregation(f"No log file yet at {log_path_str}.", log_path_str)

    try:
        entries = parse_log_lines(raw_lines)
        tally = tally_levels(entries)
        error_entries = [
            e for e in entries if e.parsed and e.level in ("ERROR", "CRITICAL", "WARNING")
        ]
        systemic_count = sum(1 for e in error_entries if classify_log_entry(e) == "systemic")
        symbol_specific_count = sum(
            1 for e in error_entries if classify_log_entry(e) == "symbol_specific"
        )
        tail = entries[-max(1, int(limit)):]
        rows = [
            {
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "level": e.level or None,
                "logger_name": e.logger_name or None,
                "message": e.message,
                "raw": e.raw,
                "parsed": e.parsed,
            }
            for e in tail
        ]
    except Exception as exc:  # noqa: BLE001 — dead-letter: parse/classify failure
        logger.warning("log_aggregation: parse/classify failed: %s", exc)
        return _empty_log_aggregation("Log parsing failed.", log_path_str)

    return {
        "log_path": log_path_str,
        "total_lines": len(entries),
        "tally": tally,
        "systemic_count": systemic_count,
        "symbol_specific_count": symbol_specific_count,
        "entries": rows,
        "returned_count": len(rows),
        "reason": None,
    }


# ---------------------------------------------------------------------------
# 8. Sizing cap-event audit trail — durable history from sizing/cap_audit_store.py.
# Reuses CapAuditStore directly (no reimplementation) — see module docstring
# section 8.
# ---------------------------------------------------------------------------


def _empty_sizing_cap_audit(reason: str) -> Dict[str, Any]:
    return {
        "events": [],
        "count": 0,
        "capped_count": 0,
        "audit_enabled": bool(settings.SIZING_CAP_AUDIT_ENABLED),
        "escalation_enabled": bool(settings.SIZING_CAP_ESCALATION_ENABLED),
        "escalation_threshold_cycles": int(settings.SIZING_CAP_ESCALATION_THRESHOLD_CYCLES),
        "escalation_factor": _finite_or_none(settings.SIZING_CAP_ESCALATION_FACTOR),
        "reason": reason,
    }


def sizing_cap_audit_summary(limit: int = 100) -> Dict[str, Any]:
    """Last ``limit`` durable position-sizing guardrail events (newest first),
    the PWA's port of ``gui/panels/observability.py
    ::_render_observability_sizing_cap_audit``.

    Reuses ``sizing.cap_audit_store.CapAuditStore.get_recent`` directly (a
    ``readonly=True`` database-level read-only engine, matching that store's
    own convention) — this function does not re-derive the row shape.

    Returns the honest empty shape (CONSTRAINT #4) — never fabricated — when
    ``settings.SIZING_CAP_AUDIT_ENABLED`` is off (the durable log isn't being
    written this run), the store is unavailable, or no events have been
    recorded yet. Never raises (CONSTRAINT #6): ``CapAuditStore.get_recent``
    already degrades to ``[]`` on any DB error internally, but the
    construction call itself is also guarded here.
    """
    if not settings.SIZING_CAP_AUDIT_ENABLED:
        return _empty_sizing_cap_audit(
            "SIZING_CAP_AUDIT_ENABLED is False — the durable cap-event log is "
            "not being written this run."
        )

    try:
        from sizing.cap_audit_store import CapAuditStore

        events = CapAuditStore(readonly=True).get_recent(limit=limit)
    except Exception as exc:  # noqa: BLE001 — dead-letter: import/construction failure
        logger.debug("sizing_cap_audit_summary: CapAuditStore unavailable: %s", exc)
        return _empty_sizing_cap_audit("Sizing cap-event audit store unavailable.")

    if not events:
        return _empty_sizing_cap_audit("No cap events recorded yet — they accumulate as cycles run.")

    capped_count = sum(1 for e in events if e.get("was_capped"))
    return {
        "events": events,
        "count": len(events),
        "capped_count": capped_count,
        "audit_enabled": True,
        "escalation_enabled": bool(settings.SIZING_CAP_ESCALATION_ENABLED),
        "escalation_threshold_cycles": int(settings.SIZING_CAP_ESCALATION_THRESHOLD_CYCLES),
        "escalation_factor": _finite_or_none(settings.SIZING_CAP_ESCALATION_FACTOR),
        "reason": None,
    }


# ---------------------------------------------------------------------------
# 9. ETF volatility transmission — read-only diagnostic view.
# Reuses gui.observability_panel_helpers.etf_transmission_rows directly — see
# module docstring section 9.
# ---------------------------------------------------------------------------


def _empty_etf_transmission(reason: str) -> Dict[str, Any]:
    return {
        "rows": [],
        "measurement_enabled": bool(settings.ETF_TRANSMISSION_ENABLED),
        "sizing_enabled": bool(settings.ETF_TRANSMISSION_SIZING_ENABLED),
        "portfolio_enabled": bool(settings.ETF_TRANSMISSION_PORTFOLIO_ENABLED),
        "reason": reason,
    }


def etf_transmission_summary(snapshot: Optional[dict]) -> Dict[str, Any]:
    """Per-symbol ETF volatility-transmission telemetry + the three
    independent master-switch states, the PWA's port of
    ``gui/panels/observability.py::_render_observability_etf_transmission``.

    Reuses ``gui.observability_panel_helpers.etf_transmission_rows`` directly
    (already pure/Streamlit-free and unit-tested) against ``snapshot``'s
    ``signals`` list. Returns the honest empty shape (CONSTRAINT #4) —
    never a table of fabricated nulls — when ``ETF_TRANSMISSION_ENABLED`` is
    off or no symbol in the snapshot has any ETF-transmission coverage yet.
    Never raises (CONSTRAINT #6)."""
    measurement_on = bool(settings.ETF_TRANSMISSION_ENABLED)
    if not measurement_on:
        return _empty_etf_transmission(
            "ETF_TRANSMISSION_ENABLED is False — measurement columns are not "
            "computed this cycle."
        )

    try:
        from gui.observability_panel_helpers import etf_transmission_rows
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("etf_transmission_summary import failed: %s", exc)
        return _empty_etf_transmission("ETF transmission helper module unavailable.")

    try:
        signals = (snapshot or {}).get("signals", []) or []
        rows = etf_transmission_rows(signals)
    except Exception as exc:  # noqa: BLE001 — dead-letter: malformed snapshot
        logger.debug("etf_transmission_summary: row extraction failed: %s", exc)
        return _empty_etf_transmission("ETF transmission telemetry unreadable.")

    return {
        "rows": rows,
        "measurement_enabled": True,
        "sizing_enabled": bool(settings.ETF_TRANSMISSION_SIZING_ENABLED),
        "portfolio_enabled": bool(settings.ETF_TRANSMISSION_PORTFOLIO_ENABLED),
        "reason": None if rows else (
            "No symbols have ETF-transmission coverage in the last snapshot yet."
        ),
    }


# ---------------------------------------------------------------------------
# 10. Heartbeat age — CURRENT sample + freshness classification only. See
# module docstring section 10 for why no trend/history is served here.
# ---------------------------------------------------------------------------

_NO_HEARTBEAT_HISTORY_NOTE = (
    "The legacy Streamlit \"Heartbeat Age Trend\" sparkline is a 60-sample "
    "ring buffer held only in st.session_state — never persisted to disk — "
    "so there is no durable history for this endpoint to serve honestly. "
    "Only the current sample is real."
)


def heartbeat_summary() -> Dict[str, Any]:
    """Current orchestrator heartbeat age (seconds) + freshness label.

    Sourced from ``gui.orchestrator_runner.heartbeat_age_seconds()`` (reads
    ``output/heartbeat.txt``'s mtime — written only by
    ``main_orchestrator.py``'s async heartbeat task) and classified via
    ``gui.observability_panel_helpers.heartbeat_status`` — both already
    reused elsewhere in this codebase, not re-derived here.

    ``history_available`` is always ``False`` here — see
    :data:`_NO_HEARTBEAT_HISTORY_NOTE` / module docstring section 10 for why
    a durable trend can't be honestly served (CONSTRAINT #4: no fabricated
    single-point "trend"). Never raises (CONSTRAINT #6)."""
    try:
        from gui.observability_panel_helpers import heartbeat_status
        from gui.orchestrator_runner import heartbeat_age_seconds
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("heartbeat_summary import failed: %s", exc)
        return {
            "age_seconds": None,
            "status": None,
            "history_available": False,
            "history_note": _NO_HEARTBEAT_HISTORY_NOTE,
            "reason": "Heartbeat helper module unavailable.",
        }

    try:
        age = heartbeat_age_seconds()
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("heartbeat_summary: heartbeat_age_seconds failed: %s", exc)
        age = None

    if age is None:
        return {
            "age_seconds": None,
            "status": heartbeat_status(float("nan")),
            "history_available": False,
            "history_note": _NO_HEARTBEAT_HISTORY_NOTE,
            "reason": (
                "No heartbeat file yet — output/heartbeat.txt is written only "
                "by main_orchestrator.py's async heartbeat task."
            ),
        }

    age_f = _finite_or_none(age)
    return {
        "age_seconds": age_f,
        "status": heartbeat_status(age_f if age_f is not None else float("nan")),
        "history_available": False,
        "history_note": _NO_HEARTBEAT_HISTORY_NOTE,
        "reason": None,
    }


# ---------------------------------------------------------------------------
# 11. Strategy P&L by strategy — see module docstring section 11 for why this
# is the FUNCTIONAL replacement of the legacy Streamlit section (which is
# dead code against real data: it groups by a column, "strategy_id", that
# doesn't exist on the Trade model).
# ---------------------------------------------------------------------------


def _empty_strategy_pnl(reason: str) -> Dict[str, Any]:
    return {"rows": [], "total_realized_pnl": None, "reason": reason}


def strategy_pnl_summary() -> Dict[str, Any]:
    """Realized P&L grouped by strategy, from
    ``transactions_store.TransactionsStore.closed_trades_df()``.

    Derives ``realized_pnl`` per closed trade exactly the way
    ``gui/panels/observability.py::_render_observability_equity_curve``
    already does — ``(exit_price - entry_price) * shares``, sign-flipped for
    a ``short`` — since ``realized_pnl`` is not itself a stored column on the
    ``Trade`` model. Groups by the REAL ``strategy`` column (exposed on the
    wire as ``strategy_id`` for naming consistency with the rest of this API
    — see module docstring section 11). Untagged trades (``strategy`` is
    ``None``/empty) are grouped under one ``strategy_id: null`` row — real
    money, never dropped or mislabeled (CONSTRAINT #4).

    Returns the honest empty shape when the store is unavailable or has no
    closed trades yet. Never raises (CONSTRAINT #6)."""
    try:
        from transactions_store import TransactionsStore
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("strategy_pnl_summary import failed: %s", exc)
        return _empty_strategy_pnl("Transactions store unavailable.")

    try:
        closed = TransactionsStore(readonly=True).closed_trades_df()
    except Exception as exc:  # noqa: BLE001 — dead-letter: cold/unreadable DB
        logger.warning("strategy_pnl_summary: closed_trades_df failed: %s", exc)
        return _empty_strategy_pnl("Transactions store unavailable.")

    required = {"entry_price", "exit_price", "shares", "exit_ts"}
    if closed is None or closed.empty or not required.issubset(set(closed.columns)):
        return _empty_strategy_pnl("No closed trades in the transactions store yet.")

    try:
        df = closed.dropna(subset=["entry_price", "exit_price", "shares"]).copy()
        if df.empty:
            return _empty_strategy_pnl("No closed trades with complete price/quantity fields yet.")

        side = df["side"].fillna("long").astype(str).str.lower() if "side" in df.columns else "long"
        sign = side.map(lambda s: -1.0 if s == "short" else 1.0) if hasattr(side, "map") else 1.0
        df["_realized_pnl"] = (df["exit_price"] - df["entry_price"]) * df["shares"] * sign
        strategy_col = df["strategy"] if "strategy" in df.columns else None
        df["_strategy_id"] = (
            strategy_col.where(strategy_col.notna() & (strategy_col.astype(str).str.strip() != ""), None)
            if strategy_col is not None
            else None
        )

        grouped = df.groupby("_strategy_id", dropna=False)["_realized_pnl"].agg(["sum", "count"])
        rows = [
            {
                "strategy_id": (None if (isinstance(idx, float) and idx != idx) or idx is None else str(idx)),
                "realized_pnl": _finite_or_none(row["sum"]),
                "trade_count": int(row["count"]),
            }
            for idx, row in grouped.iterrows()
        ]
        rows.sort(key=lambda r: (r["realized_pnl"] is None, -(r["realized_pnl"] or 0.0)))
        total = _finite_or_none(df["_realized_pnl"].sum())
    except Exception as exc:  # noqa: BLE001 — dead-letter: computation failure
        logger.warning("strategy_pnl_summary: computation failed: %s", exc)
        return _empty_strategy_pnl("Strategy P&L computation failed.")

    return {"rows": rows, "total_realized_pnl": total, "reason": None}


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------


def observability_summary(
    *,
    equity_range: str = "1Y",
    horizon_days: int = 30,
    snapshot: Optional[dict] = None,
) -> Dict[str, Any]:
    """Bundle all FOURTEEN Mission-Control sections into one payload for
    ``GET /observability/summary``. Each section degrades independently
    (CONSTRAINT #6) — a failure in one never blocks the others. Log
    aggregation (section 7) is deliberately NOT part of this composite — see
    :func:`log_aggregation`'s docstring — and is served by its own
    ``GET /observability/logs`` endpoint instead."""
    return {
        "portfolio_risk": portfolio_risk_metrics(),
        "portfolio_heat": portfolio_heat_metric(),
        "equity_curve": equity_curve_with_drawdown(equity_range),
        "regime": regime_overlay(snapshot),
        "forecast_skill": portfolio_forecast_skill(horizon_days),
        "forecast_skill_by_symbol": forecast_skill_by_symbol_summary(snapshot, horizon_days),
        "risk_gate_blocks": risk_gate_block_log(),
        "circuit_breakers": circuit_breaker_summary(),
        "system_telemetry": system_telemetry_summary(),
        "latency_heatmap": latency_heatmap_summary(),
        "sizing_cap_audit": sizing_cap_audit_summary(),
        "etf_transmission": etf_transmission_summary(snapshot),
        "heartbeat": heartbeat_summary(),
        "strategy_pnl": strategy_pnl_summary(),
    }
