"""pilots/run_status.py — file-backed "did the pipeline run?" primitives.
==========================================================================

Small, dependency-light readers for the two liveness files every pipeline
entry point writes: ``output/state_snapshot.json`` (both ``main.py`` and
``main_orchestrator.py``) and ``output/heartbeat.txt`` (``main_orchestrator.py``
ONLY). Exists so ``api/pilots_api.py``'s ``GET /automation/status`` can answer
"did it run, and when" with a NUMBER, not a human sentence.

Why not import ``scripts/preflight_check.py`` directly
--------------------------------------------------------
``scripts/preflight_check.py::check_state_snapshot_fresh`` already has this
exact freshness logic, but it returns a ``CheckResult(name, passed: bool,
reason: str)`` — a pass/fail verdict with a human message, meant for a CLI gate.
An API needs the raw age in seconds (so the PWA can render "captured Ns ago"
and apply its own staleness threshold), and parsing that message string back
into a number would be fragile and pointless when the underlying logic is
~10 lines. So this module PORTS the read logic (same file, same field, same
mtime fallback) rather than importing the CLI script (1400+ lines, argparse,
not meant to be imported as a library from an API process).

Honesty (CONSTRAINT #4): every function here returns ``None`` — never a
fabricated ``0`` or a fake age — when the underlying file is missing,
unreadable, or malformed. Never raises (CONSTRAINT #6).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from settings import settings

logger = logging.getLogger(__name__)

# A standard 5-field cron schedule field: digits, '*', ',', '-', '/' only
# (covers every field in deploy/crontab.txt: "0", "*", "1-5", "*/15", etc).
# Used by parse_crontab to reject stray non-cron text lines that happen to
# split into >=6 whitespace-separated tokens.
_CRON_FIELD_RE = re.compile(r"^[\d*/,-]+$")


def snapshot_age_seconds() -> Tuple[Optional[float], str]:
    """Age of ``output/state_snapshot.json`` in seconds, and how it was derived.

    Returns ``(age_seconds, source)`` where ``source`` is one of:

    * ``"timestamp"`` — the snapshot's own ``"timestamp"`` JSON field was read
      (the normal, precise path — both ``main.py`` and ``main_orchestrator.py``
      write this field at the end of every run).
    * ``"mtime"`` — the JSON field was absent (an older snapshot format) and
      the file's mtime was used as a fallback instead.
    * ``"missing"`` — the file does not exist, or could not be parsed at all.
      ``age_seconds`` is ``None`` in this case — never a fabricated age.

    Ported from ``scripts/preflight_check.py::check_state_snapshot_fresh``'s
    read logic (same file, same field, same mtime fallback) — see this
    module's docstring for why it isn't imported directly.
    """
    snapshot_path = settings.OUTPUT_DIR / "state_snapshot.json"
    if not snapshot_path.exists():
        return None, "missing"
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        ts_str = data.get("timestamp", "")
        if ts_str:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            return age, "timestamp"
        mtime = snapshot_path.stat().st_mtime
        age = (
            datetime.now(timezone.utc) - datetime.fromtimestamp(mtime, tz=timezone.utc)
        ).total_seconds()
        return age, "mtime"
    except Exception as exc:  # noqa: BLE001 - never raise (CONSTRAINT #6)
        logger.debug("run_status.snapshot_age_seconds: could not read snapshot: %s", exc)
        return None, "missing"


# A missing heartbeat is NOT evidence the pipeline is down — see the docstring
# below. Callers should always pair this value with this note when surfacing
# it, rather than rendering a bare "null" that reads as "engine down".
HEARTBEAT_ADVISORY_NOTE = (
    "heartbeat.txt is written only by main_orchestrator.py; advisory runs "
    "(main.py) never write it, so null here does not mean the engine is down "
    "— see pipeline.snapshot_age_seconds for the cross-mode liveness signal."
)


def heartbeat_age_seconds() -> Optional[float]:
    """Age of ``output/heartbeat.txt`` in seconds, or ``None`` if missing/unreadable.

    ``main_orchestrator._heartbeat()`` writes this file as a bare ISO-8601 UTC
    string every 60s — but ONLY when running the full async pipeline.
    ``main.py``'s advisory orchestrator never writes it at all, so ``None`` is
    the ROUTINE, EXPECTED value in advisory mode (the platform's default
    posture — see AGENTS.md), not a failure signal. Never render this as
    "engine down" on its own; pair it with ``HEARTBEAT_ADVISORY_NOTE``.
    """
    hb_path = settings.OUTPUT_DIR / "heartbeat.txt"
    if not hb_path.exists():
        return None
    try:
        ts = datetime.fromisoformat(hb_path.read_text(encoding="utf-8").strip())
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception as exc:  # noqa: BLE001 - never raise (CONSTRAINT #6)
        logger.debug("run_status.heartbeat_age_seconds: could not read heartbeat: %s", exc)
        return None


def read_daemon_json() -> Optional[dict]:
    """Best-effort read of ``output/daemon.json`` (written once at daemon
    startup by ``desktop/orchestrator_daemon.py``) — the restart-honesty
    fallback for ``GET /automation/status``: when the Control API isn't
    reachable (e.g. the daemon process died but the file survives, or it's
    mid-restart), this still has ``pid``/``interval_seconds``/``started_at``
    from the last known-good startup. ``None`` on any failure — never raises.
    """
    path = settings.OUTPUT_DIR / "daemon.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001 - never raise (CONSTRAINT #6)
        logger.debug("run_status.read_daemon_json: could not read daemon.json: %s", exc)
        return None


def read_dead_letter(limit: int = 50) -> dict:
    """Best-effort read of ``output/dead_letter.json`` (written by
    ``pipeline/production_steps.py`` at the end of every pipeline cycle;
    cleared to an empty ``entries`` list on a fully clean run).

    Returns ``{"generated_at": str | None, "entry_count": int, "entries": [...]}``
    where ``entry_count`` is the TRUE total (even when ``entries`` is capped at
    ``limit``) and ``entries`` is truncated to ``limit`` items — this file is
    the bounded, structured alternative to tailing a raw log (CLAUDE.md's
    "never fabricate" + "dead-letter, don't crash" conventions both apply: a
    missing/malformed file degrades to the empty shape below, never raises).
    """
    path = settings.OUTPUT_DIR / "dead_letter.json"
    if not path.exists():
        return {"generated_at": None, "entry_count": 0, "entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        if not isinstance(entries, list):
            entries = []
        return {
            "generated_at": data.get("generated_at"),
            "entry_count": len(entries),
            "entries": entries[:limit],
        }
    except Exception as exc:  # noqa: BLE001 - never raise (CONSTRAINT #6)
        logger.debug("run_status.read_dead_letter: could not read dead_letter.json: %s", exc)
        return {"generated_at": None, "entry_count": 0, "entries": []}


def parse_crontab(path: Optional[Path] = None) -> list:
    """Parse ``deploy/crontab.txt`` into a list of
    ``{"schedule": "0 21 * * 1-5", "command": "...", "comment": "..."}`` dicts.

    Reads the REPO FILE, never shells out to ``crontab -l`` — a subprocess
    call from this API is exactly the RCE-adjacent surface cron/systemd
    *writing* was excluded for elsewhere in this feature; the read side
    deserves the same posture. This means the result reflects the INTENDED
    schedule as checked into the repo, which may differ from what is actually
    installed on a given host — callers must render that as an explicit
    caveat, never claim "installed".

    ``comment`` joins the contiguous run of ``#``-prefixed lines immediately
    above each schedule line (pure box-drawing/separator lines of only ``=``/
    ``─`` characters are skipped) — the human-readable label each entry in
    ``deploy/crontab.txt`` already carries. A blank line resets the buffer, so
    the file's own paragraph breaks are respected. Returns ``[]`` on any
    failure (missing file, permission error) — never raises.
    """
    target = path or (Path(__file__).resolve().parent.parent / "deploy" / "crontab.txt")
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("run_status.parse_crontab: could not read %s: %s", target, exc)
        return []

    entries: list = []
    comment_buf: list = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            comment_buf = []
            continue
        if line.startswith("#"):
            content = line.lstrip("#").strip()
            # Skip pure separator lines (only '=' or box-drawing dash chars).
            if content and not set(content) <= {"=", "─", "-"}:
                content = content.strip("─- ").strip()
                if content:
                    comment_buf.append(content)
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue  # not a 5-field-schedule + command cron line
        if not all(_CRON_FIELD_RE.match(field) for field in parts[:5]):
            continue  # stray text that happens to tokenize into >=6 words
        entries.append(
            {
                "schedule": " ".join(parts[:5]),
                "command": parts[5],
                "comment": " ".join(comment_buf),
            }
        )
        comment_buf = []
    return entries
