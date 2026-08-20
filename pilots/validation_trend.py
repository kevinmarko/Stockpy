"""pilots/validation_trend.py — cross-strategy validation snapshot, run-over-run
trend, and macro-regime timeline for the Pilots PWA.

Ports ``gui/panels/gravity_audit.py::_render_validation_stress_regime_section``
(lines 332-531 of the legacy Streamlit Command Center's Safety tab) into a
dependency-light read for ``GET /strategy/validation-trend``.

Why this is NOT just ``pilots/strategy_health.py`` again
----------------------------------------------------------
``pilots.strategy_health.strategy_health_rows()`` is scoped to catalog Pilots
only: it iterates ``pilots.catalog.list_pilots()`` and joins each Pilot on its
own ``validation_strategy_id``. A strategy that ``validation.harness`` has
validated but that has not (yet) been wired to any Pilot is therefore
invisible on that screen. This module instead reads EVERY
``reports/*_validation_summary.json`` file on disk regardless of Pilot
mapping — the cross-strategy "how does candidate A compare to candidate B
right now, before I decide whether to promote either one to a Pilot" view the
legacy panel gave an operator. It also surfaces a macro-regime TRANSITION
timeline, a data domain ``strategy_health`` never touches at all.

Three independent sections, matching the legacy panel's own three
sub-sections and its own documented data-availability caveats:

1. **Cross-strategy validation snapshot** — every
   ``reports/*_validation_summary.json`` (one file per strategy, OVERWRITTEN
   every harness run) parsed into a flat row: strategy_id, deployable, pbo,
   dsr, sharpe, max_drawdown, is_options_selling, stress_gate_passed,
   report_date. A malformed file is skipped, not fatal (CONSTRAINT #6).
2. **Run-over-run trend** — ``reports/history/<strategy>_validation_history.jsonl``
   (append-only, one row per past harness run) is PORTED locally rather than
   imported from ``validation.harness.read_validation_history``, whose module
   pulls a much heavier top-level import chain (``yfinance``,
   ``universe_engine``, ``execution.cost_model``, ...) — the exact same
   reasoning ``pilots/strategy_health.py`` documents for its own local port;
   this is a third, independent copy of that tiny, stable, stdlib-only read.
   A strategy needs >= 2 recorded runs before it appears — CONSTRAINT #4,
   never fabricate a trend line from a single point.
3. **Macro regime timeline** — reuses
   ``scripts.snapshot_diff.list_rotated_snapshots``/``load_snapshot``
   (stdlib-only — confirmed by inspection: ``argparse``, ``json``,
   ``logging``, ``dataclasses``, ``datetime``, ``pathlib``, ``typing`` only),
   the SAME ``output/history/`` rotated-snapshot source
   ``gui/panels/observability.py::_render_observability_equity_curve`` and
   ``gui/panels/gravity_audit.py``'s own regime-timeline section already
   read — not reinvented here. Only REGIME TRANSITIONS (rows where the
   regime differs from the immediately preceding rotated snapshot) are
   returned, not every raw snapshot — mirrors the legacy panel's own
   ``.ne(.shift())``-filtered "transition timeline", reimplemented without
   pandas (this module stays stdlib + settings + ``scripts.snapshot_diff``).

Per the legacy panel's own documented limitation (paraphrased, not
reproduced verbatim here): the tail-scenario stress gate's per-scenario
(OCT_2008/FEB_2018/MAR_2020/AUG_2024) breakdown is never persisted to the
JSON summary — only the AGGREGATE ``stress_gate_passed`` boolean is written
by ``ValidationReport.to_summary_dict()``. This module surfaces that
aggregate boolean (already present on every snapshot row) and does not
attempt to reconstruct per-scenario data that was never serialized anywhere.

Honesty (CONSTRAINT #4): every numeric leaf is coerced through
:func:`_clean_float`. A validation-summary JSON file is written with plain
``json.dumps`` (see ``ValidationReport.to_summary_dict()``), so in principle
it could carry a literal ``NaN``/``Infinity`` token — the same class of bug
fixed in ``pilots/live_inventory.py`` for the portfolio-sync cache — that
would otherwise re-serialize as invalid JSON and break the frontend's
``JSON.parse``. Every section reports its own ``reason`` and degrades to an
empty list/dict rather than fabricating data; one strategy's bad file never
aborts the others.

Dead-letter resilient (CONSTRAINT #6): every public function degrades to an
honest empty shape on any failure; nothing here raises.

Dependency-light (pinned by
``tests/test_pilots_strategy_matrix.py::test_pilots_read_helpers_stay_dependency_light``,
parametrized to include ``"validation_trend"``): imports only stdlib +
``settings`` + ``scripts.snapshot_diff`` (itself stdlib-only, confirmed by
inspection) — never ``validation.harness``, never a heavy engine.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import settings
from scripts.snapshot_diff import list_rotated_snapshots, load_snapshot

logger = logging.getLogger(__name__)

__all__ = [
    "cross_strategy_snapshot",
    "validation_history_trend",
    "macro_regime_timeline",
    "validation_trend_snapshot",
]

# Bounds the run-over-run trend series returned per strategy. The persisted
# JSONL history can in principle grow to validation.harness's own
# MAX_VALIDATION_HISTORY_ROWS (1000) entries per strategy; that constant is
# deliberately NOT re-imported here (see module docstring — it would pull
# validation.harness's heavier import chain), so this is a local, independent
# display cap, not an assertion about the writer's own on-disk cap.
_DEFAULT_TREND_LIMIT = 24

_NO_SUMMARIES_REASON = (
    "No reports/*_validation_summary.json files found yet. Run "
    "`python -m validation.harness --strategy <name> --start ... --end ...` "
    "to generate one."
)
_NO_HISTORY_REASON = (
    "No run-over-run history yet. validation/harness.py appends one row per "
    "run to reports/history/<strategy>_validation_history.jsonl; a trend "
    "line appears once a strategy has at least 2 recorded runs."
)


def _clean_float(value: Any) -> Optional[float]:
    """Finite float, else ``None`` — never a NaN/inf JSON literal (CONSTRAINT #4)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _clean_bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _load_validation_summaries(reports_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Glob + parse every ``*_validation_summary.json`` under *reports_dir*
    (default: ``reports/``). Malformed files are skipped, never fatal
    (CONSTRAINT #6)."""
    d = Path(reports_dir) if reports_dir else Path("reports")
    summaries: List[Dict[str, Any]] = []
    if not d.exists():
        return summaries
    for f in sorted(d.glob("*_validation_summary.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                summaries.append(data)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("validation_trend: could not parse %s: %s", f, exc)
    return summaries


def _read_validation_history_rows(
    strategy_id: str,
    history_dir: str = "reports/history",
) -> List[Dict[str, Any]]:
    """Read ``<history_dir>/<strategy_id>_validation_history.jsonl``.

    PORTS (does not import) ``validation.harness.read_validation_history``'s
    read logic verbatim — same filename convention, same tolerant per-line
    JSON parsing, same dead-letter behavior (see module docstring for why).
    """
    safe_name = strategy_id.replace(" ", "_").replace("/", "_")
    target = Path(history_dir) / f"{safe_name}_validation_history.jsonl"
    if not target.exists():
        return []
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("validation_trend: could not read %s: %s", target, exc)
        return []
    rows: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            logger.debug(
                "validation_trend(%s): skipping corrupt history line: %s", strategy_id, exc
            )
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _clean_snapshot_row(s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sid = _clean_str(s.get("strategy_id"))
    if not sid:
        return None
    return {
        "strategy_id": sid,
        "deployable": _clean_bool(s.get("deployable")),
        "pbo": _clean_float(s.get("pbo")),
        "dsr": _clean_float(s.get("dsr")),
        "sharpe": _clean_float(s.get("sharpe")),
        "max_drawdown": _clean_float(s.get("max_drawdown")),
        "is_options_selling": _clean_bool(s.get("is_options_selling")),
        "stress_gate_passed": _clean_bool(s.get("stress_gate_passed")),
        "report_date": _clean_str(s.get("report_date")),
    }


def _load_db_latest_per_strategy(db_url: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Latest recorded run per strategy from the durable ``validation_runs``
    DB table (``validation/validation_history_store.py``) — a lazy,
    function-local import (confirmed dependency-light by inspection: only
    ``sqlalchemy``, ``db_config``, ``settings``, stdlib — deliberately NOT
    ``validation.harness``, whose top-level imports are far heavier; see
    ``validation``'s lack of an ``__init__.py``, which means importing this
    submodule does not transitively pull the package's other, heavier
    submodules). Degrades to ``{}`` on any failure (CONSTRAINT #6) so an
    unavailable/unreachable DB falls back to today's pure-file behavior
    rather than a 500.
    """
    try:
        from validation.validation_history_store import ValidationHistoryStore

        return ValidationHistoryStore(db_url=db_url, readonly=True).get_latest_per_strategy()
    except Exception as exc:  # noqa: BLE001 - dead-letter, never raise
        logger.debug("_load_db_latest_per_strategy failed: %s", exc)
        return {}


def cross_strategy_snapshot(
    reports_dir: Optional[str] = None, db_url: Optional[str] = None
) -> Dict[str, Any]:
    """Every validated strategy's current gate snapshot, regardless of
    whether it's wired to a catalog Pilot.

    Reads the durable ``validation_runs`` DB table FIRST (shared across every
    worktree/API process on this machine, unlike the worktree-local
    ``reports/*_validation_summary.json`` files) and falls back to the file
    snapshot only for a strategy the DB has no row for yet — e.g. one
    validated in this exact environment before this DB-persistence feature
    shipped. See ``validation/validation_history_store.py``'s module
    docstring for why a second, durable home was needed.

    Returns ``{"strategies": [...], "reason": str | None}``. Each row:
    ``{strategy_id, deployable, pbo, dsr, sharpe, max_drawdown,
    is_options_selling, stress_gate_passed, report_date}``. Sorted by
    ``strategy_id`` for a deterministic response. ``reason`` is set (and
    ``strategies`` is ``[]``) only when NEITHER source has anything at all —
    never on a partial/malformed set (a bad file is simply skipped, the
    good ones still render).
    """
    db_latest = _load_db_latest_per_strategy(db_url)

    try:
        raw = _load_validation_summaries(reports_dir)
    except Exception as exc:  # noqa: BLE001 - dead-letter, never raise
        logger.debug("cross_strategy_snapshot failed: %s", exc)
        raw = []

    merged: Dict[str, Dict[str, Any]] = {}
    for s in raw:
        row = _clean_snapshot_row(s)
        if row:
            merged[row["strategy_id"]] = row
    for sid, db_row in db_latest.items():
        row = _clean_snapshot_row(db_row)
        if row:
            merged[sid] = row  # DB wins over a file row for the same strategy

    rows = sorted(merged.values(), key=lambda r: r["strategy_id"] or "")
    return {"strategies": rows, "reason": None if rows else _NO_SUMMARIES_REASON}


def _trend_point(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "report_date": _clean_str(r.get("report_date")),
        "pbo": _clean_float(r.get("pbo")),
        "dsr": _clean_float(r.get("dsr")),
        "sharpe": _clean_float(r.get("sharpe")),
        "max_drawdown": _clean_float(r.get("max_drawdown")),
        "deployable": _clean_bool(r.get("deployable")),
    }


def _load_db_trend_rows(
    strategy_id: str, trend_limit: int, db_url: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Oldest-first trend rows for one strategy from the durable
    ``validation_runs`` DB table, capped at ``trend_limit``. ``[]`` on any
    failure or if the strategy has no DB rows (CONSTRAINT #6) — the caller
    falls back to the JSONL file read in that case."""
    try:
        from validation.validation_history_store import ValidationHistoryStore

        store = ValidationHistoryStore(db_url=db_url, readonly=True)
        rows = store.get_recent(strategy_id, limit=trend_limit)  # most-recent-first
        return list(reversed(rows))  # oldest-first, matching the file read's convention
    except Exception as exc:  # noqa: BLE001 - dead-letter, never raise
        logger.debug("_load_db_trend_rows(%s) failed: %s", strategy_id, exc)
        return []


def validation_history_trend(
    reports_dir: Optional[str] = None,
    history_dir: str = "reports/history",
    trend_limit: int = _DEFAULT_TREND_LIMIT,
    db_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Run-over-run PBO/DSR/Sharpe/MaxDD trend for every strategy with >= 2
    recorded harness runs.

    For each strategy, prefers the durable ``validation_runs`` DB table
    (shared across every worktree/API process on this machine) and falls
    back to the worktree-local ``reports/history/<strategy>_validation_history.jsonl``
    file only when the DB has fewer than 2 rows for that strategy — e.g. a
    strategy validated in this exact environment before this DB-persistence
    feature shipped. See ``validation/validation_history_store.py``'s module
    docstring for why a second, durable home was needed.

    Returns ``{"trend": {strategy_id: [{"report_date","pbo","dsr","sharpe",
    "max_drawdown","deployable"}, ...]}, "reason": str | None}``. Only
    strategies discovered via :func:`cross_strategy_snapshot`'s own strategy
    set (file summaries UNION current DB rows) are checked for history — a
    strategy with no current snapshot anywhere is intentionally NOT
    surfaced, mirroring the legacy panel's own behavior. Oldest-first per
    strategy, capped at ``trend_limit`` most recent runs. ``reason`` is set
    only when NO strategy has 2+ runs yet — a strategy with exactly 1 run is
    silently omitted rather than treated as an error.
    """
    snapshot = cross_strategy_snapshot(reports_dir, db_url)
    strategy_ids = [r["strategy_id"] for r in snapshot["strategies"] if r.get("strategy_id")]

    trend: Dict[str, List[Dict[str, Any]]] = {}
    for sid in strategy_ids:
        db_rows = _load_db_trend_rows(sid, trend_limit, db_url)
        if len(db_rows) >= 2:
            trend[sid] = [_trend_point(r) for r in db_rows]
            continue

        try:
            rows = _read_validation_history_rows(sid, history_dir=history_dir)
        except Exception as exc:  # noqa: BLE001 - one bad strategy must not abort the rest
            logger.debug("validation_history_trend(%s) failed: %s", sid, exc)
            continue
        if len(rows) < 2:
            continue
        tail = rows[-trend_limit:] if trend_limit else rows
        trend[sid] = [_trend_point(r) for r in tail]

    return {"trend": trend, "reason": None if trend else _NO_HISTORY_REASON}


def _regime_sort_key(point: Dict[str, Any]) -> datetime:
    """Best-effort chronological sort key; unparseable timestamps sort first
    (never raises, never drops a point — CONSTRAINT #6)."""
    try:
        return datetime.fromisoformat(point["timestamp"])
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def macro_regime_timeline(output_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Macro-regime TRANSITIONS from the rotated ``output/history/`` snapshots
    — the same source ``gui/panels/observability.py``'s equity-curve regime
    overlay and ``gui/panels/gravity_audit.py``'s own regime-timeline section
    already read (``scripts.snapshot_diff.list_rotated_snapshots``/
    ``load_snapshot``), not reinvented here.

    Returns ``{"transitions": [{"timestamp","market_regime"}, ...],
    "n_rotated_snapshots": int, "reason": str | None}``. Only rows where the
    regime differs from the immediately preceding rotated snapshot are
    returned (a "transition timeline", not every raw snapshot) — mirrors the
    legacy panel's own ``.ne(.shift())`` filter, reimplemented without
    pandas. ``reason`` is set (with ``transitions: []``) when fewer than 2
    rotated snapshots exist yet — this accumulates automatically every
    pipeline/advisory run, never fabricated here.
    """
    try:
        root = Path(output_dir) if output_dir is not None else settings.OUTPUT_DIR
        rotated_paths = list_rotated_snapshots(root)

        points: List[Dict[str, Any]] = []
        for p in rotated_paths:
            snap = load_snapshot(p)
            if not snap:
                continue
            ts = _clean_str(snap.get("timestamp"))
            regime = _clean_str(snap.get("market_regime"))
            if ts and regime:
                points.append({"timestamp": ts, "market_regime": regime})

        n = len(points)
        if n < 2:
            return {
                "transitions": [],
                "n_rotated_snapshots": n,
                "reason": (
                    f"Regime timeline needs >= 2 rotated snapshots in "
                    f"output/history/ — currently {n}. This accumulates "
                    "automatically each time the orchestrator or advisory "
                    "loop runs; not fabricated here."
                ),
            }

        points.sort(key=_regime_sort_key)
        transitions: List[Dict[str, Any]] = []
        prev_regime: Optional[str] = None
        for pt in points:
            if pt["market_regime"] != prev_regime:
                transitions.append(pt)
                prev_regime = pt["market_regime"]

        return {"transitions": transitions, "n_rotated_snapshots": n, "reason": None}
    except Exception as exc:  # noqa: BLE001 - dead-letter, never raise
        logger.debug("macro_regime_timeline failed: %s", exc)
        return {
            "transitions": [],
            "n_rotated_snapshots": 0,
            "reason": "Regime timeline unavailable.",
        }


def validation_trend_snapshot(
    *,
    reports_dir: Optional[str] = None,
    history_dir: str = "reports/history",
    trend_limit: int = _DEFAULT_TREND_LIMIT,
    output_dir: Optional[Path] = None,
    db_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Bundle all three sections into one payload for
    ``GET /strategy/validation-trend``. Each section degrades independently
    (CONSTRAINT #6) — a failure in one never blocks the other two."""
    snapshot = cross_strategy_snapshot(reports_dir, db_url)
    trend = validation_history_trend(reports_dir, history_dir, trend_limit, db_url)
    regime = macro_regime_timeline(output_dir)
    return {
        "strategies": snapshot["strategies"],
        "strategies_reason": snapshot["reason"],
        "trend": trend["trend"],
        "trend_reason": trend["reason"],
        "regime_timeline": regime["transitions"],
        "n_rotated_snapshots": regime["n_rotated_snapshots"],
        "regime_reason": regime["reason"],
    }
