"""Read side of the four-agent Robinhood cycle for the webapp's "Pending approvals" card.

``GET /agentic/approvals`` (``api/pilots_api.py``) returns :func:`build_approvals_view`.
It only reads files the Trader agent and ``execution/agentic_cycle.py`` already
write under ``settings.OUTPUT_DIR``:

* ``execution_approvals.jsonl`` — every approval state the Trader observed;
* ``execution_placed.jsonl`` — what was sent (qty, client_order_id, run_id);
* ``agentic/<run_id>/cycle.json`` and ``theses.json`` — the cycle's status and
  the theses its validator accepted.

Honesty rules (CONSTRAINT #4): a state shown here is the **last state the agent
recorded**, not a live Robinhood read; the response says when it was recorded.
A thesis is quoted only when that run's ``cycle.json`` lists it in
``valid_thesis_ids`` (the validator accepted it); otherwise ``thesis`` is null.
Never raises (CONSTRAINT #6): a missing or corrupt file degrades to empty lists.
This module never contacts Robinhood and never writes anything.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

DECIDED_STATES = ("approved", "declined", "expired", "auto_approved", "unknown")


def _out(output_dir: Optional[Any]) -> Path:
    if output_dir is not None:
        return Path(output_dir)
    from settings import settings
    return Path(settings.OUTPUT_DIR)


def _thesis_for(run_id: Optional[str], coid: Optional[str], out: Path,
                cache: Dict[str, Any]) -> Dict[str, Optional[str]]:
    empty = {"thesis": None, "invalidation": None}
    if not run_id or not coid:
        return empty
    if run_id not in cache:
        from execution.agentic_cycle import read_cycle
        cycle = read_cycle(run_id, out) or {}
        valid = set(cycle.get("valid_thesis_ids") or [])
        theses: Mapping[str, Any] = {}
        if valid:
            try:
                doc = json.loads((out / "agentic" / run_id / "theses.json").read_text(encoding="utf-8"))
                theses = doc.get("theses") or {}
            except Exception:
                theses = {}
        cache[run_id] = (valid, theses)
    valid, theses = cache[run_id]
    t = theses.get(coid) if coid in valid else None
    if not isinstance(t, Mapping):
        return empty
    return {"thesis": str(t.get("thesis") or "") or None,
            "invalidation": str(t.get("invalidation") or "") or None}


def build_approvals_view(output_dir: Optional[Any] = None, *, recent_limit: int = 10) -> Dict[str, Any]:
    """``{pending, recent, last_cycle, n_pending, note}`` for the webapp card."""
    view: Dict[str, Any] = {
        "pending": [],
        "recent": [],
        "n_pending": 0,
        "last_cycle": None,
        "note": ("States are the last ones the Trader agent recorded; the Robinhood app "
                 "is the live source. Approve or decline there."),
    }
    try:
        from execution.agentic_cycle import latest_cycle
        from execution.receipts_store import read_approval_events, read_placed_ledger

        out = _out(output_dir)
        events = read_approval_events(out)
        first: Dict[str, Dict[str, Any]] = {}
        latest: Dict[str, Dict[str, Any]] = {}
        for ev in events:
            aid = str(ev.get("approval_id") or "").strip()
            if not aid:
                continue
            first.setdefault(aid, ev)
            latest[aid] = ev
        placed = {str(r.get("approval_id")): r for r in read_placed_ledger(out) if r.get("approval_id")}
        cache: Dict[str, Any] = {}

        def row(aid: str) -> Dict[str, Any]:
            ev, p = latest[aid], placed.get(aid, {})
            base = {
                "approval_id": aid,
                "symbol": ev.get("symbol") or p.get("symbol"),
                "side": ev.get("side") or p.get("side"),
                "qty": p.get("qty"),
                "state": ev.get("state") or "unknown",
                "submitted_at": first[aid].get("ts"),
                "recorded_at": ev.get("ts"),
                "run_id": p.get("run_id"),
            }
            base.update(_thesis_for(p.get("run_id"), p.get("client_order_id"), out, cache))
            return base

        pending = [row(a) for a, ev in latest.items() if ev.get("state") == "pending"]
        pending.sort(key=lambda r: str(r.get("submitted_at") or ""), reverse=True)
        decided = [row(a) for a, ev in latest.items() if ev.get("state") in DECIDED_STATES]
        decided.sort(key=lambda r: str(r.get("recorded_at") or ""), reverse=True)
        view["pending"] = pending
        view["n_pending"] = len(pending)
        view["recent"] = decided[: max(0, recent_limit)]

        cyc = latest_cycle(out)
        if cyc:
            view["last_cycle"] = {
                k: cyc.get(k) for k in ("run_id", "status", "mode", "launched_by", "started_at",
                                        "finished_at", "failed_stage", "reason")
            }
            summary = cyc.get("summary") if isinstance(cyc.get("summary"), Mapping) else None
            view["last_cycle"]["summary_title"] = summary.get("title") if summary else None
    except Exception as exc:
        logger.warning("agentic_approvals: view unavailable (%s)", exc)
    return view
