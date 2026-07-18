"""pilots/agentic.py — read ``output/agent_state.json`` for the Agentic Trading tab.
=====================================================================================

The Agent Status header on the webapp's Agentic Trading tab (``GET
/agentic/status``) is a COMPOSITE of several sources that already have their
own dependency-light readers imported at ``api/pilots_api.py``'s module top
(``gui.robinhood_execution_panel`` for the execution queue,
``pilots.follows_store.FollowsStore`` for active follows,
``execution.kill_switch.GlobalKillSwitch`` for the kill switch) — the endpoint
composes those directly, the same way ``GET /automation/status`` already
stitches together ``gui.daemon_client`` + ``pilots.run_status`` +
``execution.kill_switch`` inline rather than through one monolithic helper.

The ONE piece with no existing reader is ``output/agent_state.json``
(``engine.advisory_agent.AgentState`` — the advisory-only adaptive-cadence
loop's persisted backlog). This module PORTS the minimal read logic (cycle
count, last-cycle timestamp, backlog size) rather than importing
``engine.advisory_agent`` directly — mirrors ``pilots/run_status.py``'s
documented reasoning: ``engine.advisory_agent``'s own top-level imports are
stdlib-only today (confirmed no heavy-engine import), but importing the
``engine`` package at all is unnecessary risk for three integer/string fields,
and this module must stay stdlib + ``settings`` only per the pilots-endpoint
skill's dependency-light contract. Never raises (CONSTRAINT #6); a missing or
corrupt file degrades to the honest zero/``None`` shape below, never a
fabricated value (CONSTRAINT #4).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["agent_loop_status"]

_FILENAME = "agent_state.json"
_MISSING_REASON = (
    "No agent_state.json yet — the advisory-loop agent (engine/advisory_agent.py) "
    "hasn't completed a cycle since this file was last cleared."
)


def _default_path() -> Path:
    return settings.OUTPUT_DIR / _FILENAME


def agent_loop_status(path: Optional[str] = None) -> Dict[str, Any]:
    """Return ``{cycle_count, last_cycle_iso, backlog_count, reason}`` from the
    advisory-loop agent's persisted state.

    ``last_cycle_iso`` is ``None`` (never a fabricated timestamp) when the
    field is absent or empty. ``backlog_count`` is the number of symbols with
    an open backlog entry (unactioned high-conviction recommendation), not
    the reminders already dispatched. A missing/corrupt file returns
    ``cycle_count=0, last_cycle_iso=None, backlog_count=0`` plus an honest
    ``reason`` — the caller renders this as "agent hasn't run yet", not an
    error.
    """
    p = Path(path) if path else _default_path()
    if not p.exists():
        return {
            "cycle_count": 0,
            "last_cycle_iso": None,
            "backlog_count": 0,
            "reason": _MISSING_REASON,
        }
    try:
        data: Any = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("agent_state.json root is not an object")
        backlog = data.get("backlog", {})
        backlog_count = len(backlog) if isinstance(backlog, dict) else 0
        last_cycle_iso = str(data.get("last_cycle_iso") or "").strip() or None
        return {
            "cycle_count": int(data.get("cycle_count", 0) or 0),
            "last_cycle_iso": last_cycle_iso,
            "backlog_count": backlog_count,
            "reason": None,
        }
    except Exception as exc:  # noqa: BLE001 — dead-letter (CONSTRAINT #6)
        logger.debug("agentic.agent_loop_status: could not read %s: %s", p, exc)
        return {
            "cycle_count": 0,
            "last_cycle_iso": None,
            "backlog_count": 0,
            "reason": "agent_state.json is unreadable or corrupt.",
        }
