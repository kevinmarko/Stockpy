"""
execution/kill_switch.py
========================
File-based global kill switch for the InvestYo order-execution pipeline.

When the kill switch is active (OUTPUT_DIR/KILL_SWITCH file exists):
  * ``OrderManager.submit_order_with_idempotency`` raises ``KillSwitchActiveError``
    before any pre-trade check or broker call.
  * Human operators or a watchdog script can then flatten open positions
    manually, or set FLATTEN_ON_KILL=true to trigger automatic flattening
    (future extension — currently logs a CRITICAL reminder only).

File format
-----------
The KILL_SWITCH file contains a plain-text reason string written by
``GlobalKillSwitch.activate()``.  It can be empty; the presence of the file
alone is authoritative.

Heartbeat integration
---------------------
``main_orchestrator.py`` writes a timestamp to OUTPUT_DIR/heartbeat.txt every
60 seconds.  An external watchdog can activate this kill switch if the
heartbeat goes stale, then use ``python -m execution.kill_switch --status``
to confirm the state.

CLI usage
---------
  python -m execution.kill_switch --activate [--reason "text"]
  python -m execution.kill_switch --deactivate
  python -m execution.kill_switch --status
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from settings import settings

logger = logging.getLogger(__name__)

# Canonical kill-switch sentinel file location.
KILL_SWITCH_FILE: Path = settings.OUTPUT_DIR / "KILL_SWITCH"


class KillSwitchActiveError(RuntimeError):
    """Raised by OrderManager when the global kill switch is active.

    Callers should catch this and abort order submission without retrying.
    """


class GlobalKillSwitch:
    """
    Stateless file-based kill switch.

    Every method is idempotent and does not hold process state — the file
    system is the single source of truth.  This means multiple processes
    (orchestrator + watchdog) see a consistent view without IPC.

    Parameters
    ----------
    sentinel_file : Path | None
        Override the default KILL_SWITCH_FILE path (useful for tests with
        a temporary directory).
    """

    def __init__(self, sentinel_file: Optional[Path] = None) -> None:
        self._path = sentinel_file or KILL_SWITCH_FILE

    def is_active(self) -> bool:
        """Return True if the kill switch file exists."""
        return self._path.exists()

    def activate(self, reason: str = "") -> None:
        """Create the sentinel file, halting all future order submissions.

        The file is written atomically (write-then-rename) to avoid a
        partial-write race with ``is_active()``.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        content = f"Activated at {ts}\n{reason}\n".strip()
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.rename(self._path)
        logger.critical(
            "KILL SWITCH ACTIVATED — all order submission is now BLOCKED. "
            "Reason: %s. File: %s",
            reason or "(no reason given)",
            self._path,
        )
        if settings.FLATTEN_ON_KILL:
            logger.critical(
                "FLATTEN_ON_KILL=True — a position-flattening routine should be "
                "invoked. Automatic flattening is not yet implemented; "
                "manually close all open positions before re-enabling."
            )

    def deactivate(self) -> None:
        """Remove the sentinel file, re-enabling order submission."""
        if self._path.exists():
            self._path.unlink()
            logger.warning(
                "KILL SWITCH DEACTIVATED — order submission is now re-enabled. "
                "File removed: %s",
                self._path,
            )
        else:
            logger.info("deactivate() called but kill switch was not active.")

    def reason(self) -> str:
        """Return the reason text stored in the sentinel file, or '' if inactive."""
        if not self._path.exists():
            return ""
        try:
            return self._path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="InvestYo Global Kill Switch CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--activate", action="store_true", help="Activate the kill switch.")
    group.add_argument("--deactivate", action="store_true", help="Deactivate the kill switch.")
    group.add_argument("--status", action="store_true", help="Print current kill-switch status.")
    parser.add_argument("--reason", default="", help="Optional reason text (--activate only).")
    args = parser.parse_args()

    ks = GlobalKillSwitch()

    if args.activate:
        ks.activate(reason=args.reason)
        print(f"Kill switch ACTIVATED. File: {ks._path}")
    elif args.deactivate:
        ks.deactivate()
        print(f"Kill switch DEACTIVATED. File removed: {ks._path}")
    elif args.status:
        active = ks.is_active()
        state = "ACTIVE" if active else "INACTIVE"
        print(f"Kill switch: {state}")
        if active:
            print(f"Reason: {ks.reason() or '(none stored)'}")
            print(f"File: {ks._path}")


if __name__ == "__main__":
    _main()
