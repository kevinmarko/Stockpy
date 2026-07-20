"""pilots/gravity_audit.py — read-only reader for the LEGACY structural Gravity
Review Suite's last completed run, for the PWA's Safety-tab port.
=============================================================================

``gui/panels/gravity_audit.py`` (the retired Streamlit Command Center's Safety
tab) has TWO independent Gravity-audit surfaces:

1.  The AI Gravity audit runner (``engine.gravity_ai_runner`` — Claude auditor
    + Gemini cross-checker) persists a structured JSON report to
    ``output/gravity_ai_audit.json``. That side is read directly via
    ``gui.gravity_ai_panel`` (already Streamlit-free + dependency-light —
    imported straight into ``api/pilots_api.py``, mirroring how
    ``gui.ai_control_center`` is already imported there) — no reader module
    needed for it.
2.  The legacy, PURELY STRUCTURAL ``Gravity AI Review Suite.py`` (Pandera
    schema conformance, lookahead-bias perturbation, signal-registry health,
    sizing/risk gates — no LLM calls despite the filename) is launched as a
    DETACHED SUBPROCESS by ``gui.orchestrator_runner.launch_gravity_audit()``
    and streamed to a durable log file, ``output/gravity_run.log``
    (``gui.orchestrator_runner.GRAVITY_LOG_PATH``) — truncated only when a NEW
    run is launched, so the last completed (or last in-progress, if read
    mid-run) run's content survives across GUI/API restarts. THIS module
    parses that log's trailing JSON verdict, porting the exact same
    ``_parse_trailing_json`` / ``_derive_step_status`` logic
    ``gui/panels/gravity_audit.py``'s live-tail fragment already uses.

Deliberately READ-ONLY — no trigger endpoint. See the PR that added this
module for the full reasoning; in short: (a) the subprocess launcher/live-log
UX (``gui.orchestrator_runner.launch_gravity_audit`` + ``st.fragment`` polling
every 3s) has no request/response equivalent without new async-job
infrastructure this API doesn't have, (b) the run itself can take up to ~10
minutes (the exact reason the legacy blocking ``subprocess.run(timeout=600)``
was replaced with this detached-process + live-tail pattern in the first
place), and (c) the operator already has a non-mobile trigger path (the
Streamlit Safety tab, or running the suite directly).

Design invariants (identical to the rest of the Pilots read layer):

* **Read-only / persisted-state only** — imports only ``settings`` + stdlib.
* **Honesty (CONSTRAINT #4)** — a missing/unwritten/unparseable log yields
  ``available: False`` + an honest ``reason``, never a fabricated verdict.
* **Never raises (CONSTRAINT #6)** — a missing/corrupt/mid-write log degrades
  to the empty shape.
* **Fail-closed on ambiguity** — a non-empty log with NO parseable trailing
  JSON (e.g. a run still in progress, or one that crashed before printing its
  verdict) reports ``available: False`` rather than guessing pass/fail.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["legacy_audit_status"]

_LOG_FILENAME = "gravity_run.log"
_NO_RUN_REASON = (
    "No Gravity Review Suite run recorded yet — launch it from the desktop "
    "Command Center's Safety tab (`▶️ Run Gravity audit`)."
)
_UNPARSEABLE_REASON = (
    "Could not find a completed audit verdict in the last run's log — a run "
    "may still be in progress, or the last run did not finish."
)


def _default_log_path() -> Path:
    return settings.OUTPUT_DIR / _LOG_FILENAME


def _parse_trailing_json(text: str) -> Optional[dict]:
    """Extract the last top-level JSON object from arbitrary stdout.

    Ported verbatim from ``gui/panels/gravity_audit.py::_parse_trailing_json``
    — kept byte-for-byte identical so both surfaces (desktop GUI, PWA) always
    agree on what counts as a parseable verdict.
    """
    end = text.rfind("}")
    if end == -1:
        return None
    depth = 0
    start = -1
    for i in range(end, -1, -1):
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            depth -= 1
            if depth == 0:
                start = i
                break
    if start == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


def _derive_step_status(key: str, val: Dict[str, Any]) -> Tuple[bool, str]:
    """Best-effort PASS/FAIL derivation across every Gravity step-report shape.

    Ported verbatim from ``gui/panels/gravity_audit.py::_derive_step_status``
    — see that function's docstring for why the fallback fields exist (a
    handful of steps predate the ``status``/``overall_pass`` conventions).
    """
    if "status" in val:
        status = str(val["status"])
        return status.upper().startswith("PASS"), status
    if "overall_pass" in val:
        ok = bool(val["overall_pass"])
        return ok, "PASSED" if ok else "FAILED"
    if key == "step_3_5_discrepancy_analysis":
        conclusion = str(val.get("conclusion", "UNKNOWN"))
        return conclusion == "Perfect Alignment", conclusion
    if key == "step_7_simulation_impact":
        sub_statuses = [
            str(val.get("vector_bt_status", "")),
            str(val.get("backtrader_status", "")),
        ]
        ok = not any("error" in s.lower() for s in sub_statuses if s)
        label = " / ".join(s for s in sub_statuses if s) or "UNKNOWN"
        return ok, label
    return False, "—"


def legacy_audit_status(path: Optional[str] = None) -> Dict[str, Any]:
    """Return the last completed legacy Gravity Review Suite run, or an honest
    empty shape.

    Shape::

        {
            "available": bool,
            "all_passed": bool | None,   # None only when available is False
            "steps": [{"step": str, "passed": bool, "status": str}, ...],
            "reason": str | None,        # present iff available is False
        }

    Never raises (CONSTRAINT #6): a missing file, an unreadable file, or a log
    with no parseable trailing JSON (run in progress / crashed before
    printing its verdict) all degrade to ``available: False`` with a distinct,
    honest ``reason`` — never a fabricated pass/fail (CONSTRAINT #4).
    """
    p = Path(path) if path else _default_log_path()
    try:
        if not p.exists():
            return {"available": False, "all_passed": None, "steps": [], "reason": _NO_RUN_REASON}
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("legacy_audit_status: failed to read %s: %s", p, exc)
        return {
            "available": False,
            "all_passed": None,
            "steps": [],
            "reason": f"Log unreadable: {exc}",
        }

    report = _parse_trailing_json(text)
    if report is None:
        return {"available": False, "all_passed": None, "steps": [], "reason": _UNPARSEABLE_REASON}

    rows: List[Dict[str, Any]] = []
    any_fail = False
    for key, val in report.items():
        if not isinstance(val, dict):
            continue
        ok, status = _derive_step_status(key, val)
        if not ok:
            any_fail = True
        rows.append({"step": key, "passed": ok, "status": status})

    return {
        "available": True,
        "all_passed": (not any_fail) if rows else None,
        "steps": rows,
        "reason": None if rows else "Audit log parsed but contained no step entries.",
    }
