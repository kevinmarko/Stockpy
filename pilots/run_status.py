"""pilots/run_status.py — file-backed "did the pipeline run?" primitives.
==========================================================================

Small, dependency-light readers for the two liveness files every pipeline
entry point writes: ``output/state_snapshot.json`` (both ``main.py`` and
``main_orchestrator.py``) and ``output/heartbeat.txt`` (``main_orchestrator.
main()`` ONLY — the persistent orchestrator daemon runs the pipeline via
``main_orchestrator._main_body()`` directly, deliberately bypassing ``main()``'s
own per-call heartbeat lifecycle, so heartbeat.txt is permanently absent under
the daemon; see ``heartbeat_age_seconds``'s docstring). Exists so
``api/pilots_api.py``'s ``GET /automation/status`` can answer "did it run, and
when" with a NUMBER, not a human sentence.

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
import os
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
    string every 60s, scheduled ONLY from inside ``main_orchestrator.main()``.
    Neither ``main.py``'s advisory orchestrator NOR the persistent
    orchestrator daemon (``desktop/daemon_runtime.py`` calls
    ``main_orchestrator._main_body()`` directly, not ``main()``) ever writes
    it, so ``None`` is the ROUTINE, EXPECTED value in both advisory mode (the
    platform's default posture — see AGENTS.md) and under the daemon, not a
    failure signal. Never render this as "engine down" on its own; pair it
    with ``HEARTBEAT_ADVISORY_NOTE``. (Considered and rejected as a daemon
    liveness signal for that reason — see ``daemon_pid_alive``/``pid_alive``
    on ``read_daemon_json`` instead, which works under the daemon and is
    exact rather than up-to-60s-stale.)
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


def _pid_alive(pid) -> Optional[bool]:
    """Is ``pid`` a live process on this host? ``True``/``False``, or
    ``None`` when unknowable. Never raises (CONSTRAINT #6); never guesses
    ``False`` from an ambiguous input (CONSTRAINT #4) -- an unparseable or
    absent pid is genuinely unknowable, not evidence of anything.

    Two guards worth explaining, since both are real safety issues rather
    than pedantry:

    * ``pid <= 0`` is rejected before ever reaching ``os.kill``: signal 0
      against pid 0 targets the CALLING PROCESS'S ENTIRE GROUP, and a
      negative pid targets a process group too. A garbage/zero value in a
      hand-edited or corrupted ``daemon.json`` must never turn into a
      process-group-wide signal, even a no-op one (signal 0 sends nothing,
      but the targeting semantics are the hazard, not the signal itself).
    * ``bool`` is rejected explicitly even though ``isinstance(pid, int)``
      alone would accept it -- ``bool`` is a subclass of ``int`` in Python,
      so a JSON ``true`` would otherwise become ``os.kill(1, 0)``: PID 1,
      which is essentially always alive, silently reporting a dead daemon
      as alive from a malformed file.

    Deliberately diverges from ``gui.orchestrator_runner._pid_alive``, which
    returns a plain ``bool`` and maps an unknown ``OSError`` to ``False``.
    That is correct THERE: its caller (``RunHandle.is_running()``) needs a
    bool, and "assume finished" is the safe default for a subprocess
    supervisor deciding whether to reap. It would be WRONG here: a ``False``
    from this function is rendered straight to an operator as "your daemon
    is dead," so an unknown/ambiguous case must stay ``None``, never
    collapse to that stronger claim. Do not "unify" the two.

    PID REUSE: a recycled pid can make this read ``True`` for a daemon that
    is actually gone. Tolerated deliberately, not fixed: this is only ever
    consulted (via ``read_daemon_json``'s ``pid_alive`` key) when the
    Control API is ALREADY unreachable, so a stale ``True`` can never be
    mistaken for a healthy daemon -- it degrades to the honest "a process
    exists but is not answering" rather than a false claim of health.
    Corroborating against the process's actual start time would need
    ``psutil`` or a ``ps`` subprocess, both out of bounds for this
    deliberately dependency-light, subprocess-averse module (see
    ``parse_crontab``'s docstring for the same subprocess-avoidance
    rationale).
    """
    if isinstance(pid, bool) or not isinstance(pid, int):
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user -- still "alive"
    except Exception as exc:  # noqa: BLE001 - never raise (CONSTRAINT #6)
        logger.debug("run_status._pid_alive: could not probe pid %r: %s", pid, exc)
        return None  # unknowable -> None, NEVER a fabricated False
    return True


def read_daemon_json() -> Optional[dict]:
    """Best-effort read of ``output/daemon.json`` (written at daemon startup,
    and again with ``state="stopped"`` at a graceful teardown, by
    ``desktop/orchestrator_daemon.py``) — the restart-honesty fallback for
    ``GET /automation/status``: when the Control API isn't reachable (e.g.
    the daemon process died but the file survives, or it's mid-restart),
    this still has ``pid``/``interval_seconds``/``started_at`` from the last
    known-good startup. ``None`` on any failure (missing file, unreadable,
    not a JSON object) — never raises (CONSTRAINT #6).

    The returned dict is the file's contents PLUS one derived key,
    ``"pid_alive"`` (``True``/``False``/``None`` — see ``_pid_alive``, and
    CONSTRAINT #4: ``None`` means unknowable, never a fabricated ``False``).
    This is added here, on the record, rather than via a second public
    helper a caller could forget to call — a SIGKILLed daemon can never
    perform the terminal write above, so a stale ``state: "running"`` on
    disk is NOT proof of life; ``pid_alive`` is the machine-checked signal
    that covers exactly that gap. Any new consumer of this function
    automatically gets it; there is no separate liveness call to remember.
    """
    path = settings.OUTPUT_DIR / "daemon.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        data["pid_alive"] = _pid_alive(data.get("pid"))
        return data
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


def parse_crontab_status(path: Optional[Path] = None) -> dict:
    """Parse ``deploy/crontab.txt`` into ``{"jobs": [{"title", "description",
    "schedule", "command"}, ...]}`` for ``GET /system/cron-status``.

    A DIFFERENT shape from ``parse_crontab()`` above (which serves
    ``GET /automation/status`` and joins every comment line into one
    ``comment`` string) -- this splits a ``# ── Section Title`` box-drawing
    header line from the free-text description lines that follow it, since
    the Commands screen renders title and description separately.

    Extracted from two near-verbatim, independently-drifted copies that used
    to live in ``api/control_api.py`` and ``api/pilots_api.py`` (the latter
    was the correct one -- see the ``current_title`` reset bug below). Both
    modules now call this single implementation.

    ``current_title`` is intentionally NOT reset after a cron line is
    emitted -- only ``current_desc`` is. Several cron entries commonly share
    one ``# ── Section Title`` header with per-entry description lines below
    it; resetting the title after the first entry would silently fall every
    later entry in that section back to the generic ``"Cron Job"`` label.

    Never raises (CONSTRAINT #6) -- a missing file, a permission error, or any
    other read failure returns ``{"jobs": [], "error": "<reason>"}``."""
    target = path or (Path(__file__).resolve().parent.parent / "deploy" / "crontab.txt")
    if not target.exists():
        return {"jobs": [], "error": "crontab.txt not found"}

    jobs: list = []
    current_title = ""
    current_desc: list = []

    try:
        text = target.read_text(encoding="utf-8")
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("# ──"):
                current_title = line.strip("# ─").strip()
                current_desc = []
            elif (
                line.startswith("# ==")
                or line.startswith("# Install:")
                or line.startswith("# All times")
                or line.startswith("# US Eastern")
            ):
                continue
            elif line.startswith("#"):
                current_desc.append(line.lstrip("#").strip())
            else:
                parts = line.split(maxsplit=5)
                if len(parts) >= 6:
                    jobs.append(
                        {
                            "title": current_title or "Cron Job",
                            "description": " ".join(current_desc),
                            "schedule": " ".join(parts[:5]),
                            "command": parts[5],
                        }
                    )
                    current_desc = []
        return {"jobs": jobs}
    except OSError as exc:  # noqa: BLE001 - dead-letter (CONSTRAINT #6)
        logger.warning("run_status.parse_crontab_status: could not read %s: %s", target, exc)
        return {"jobs": [], "error": "Unable to read crontab schedule"}
