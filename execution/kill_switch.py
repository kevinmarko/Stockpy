"""
execution/kill_switch.py
========================
File-based global kill switch for the InvestYo order-execution pipeline.

When the kill switch is active (OUTPUT_DIR/KILL_SWITCH file exists):
  * ``OrderManager.submit_order_with_idempotency`` raises ``KillSwitchActiveError``
    BEFORE any pre-trade check or dedup so the sentinel is impossible to bypass.
  * Human operators or a watchdog script can then flatten open positions
    manually (or set FLATTEN_ON_KILL=true to receive a CRITICAL reminder —
    automatic flattening is a future extension).

File format
-----------
The KILL_SWITCH file stores a plain-text reason written by ``activate()``.
File *presence* is authoritative; content is advisory.

Heartbeat integration
---------------------
``main_orchestrator._heartbeat()`` writes OUTPUT_DIR/heartbeat.txt every 60 s.
An external watchdog can activate this kill switch if the timestamp goes stale,
then confirm state with ``python -m execution.kill_switch --status``.

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

# Canonical sentinel-file location. Tests may override via GlobalKillSwitch(sentinel_file=…).
KILL_SWITCH_FILE: Path = settings.OUTPUT_DIR / "KILL_SWITCH"


class KillSwitchActiveError(RuntimeError):
    """Raised by OrderManager when the global kill switch is active.

    Callers must catch this and abort order submission without retrying.
    """


class GlobalKillSwitch:
    """
    Stateless file-based kill switch.

    Every public method is idempotent.  The file system is the single source of
    truth so multiple processes (orchestrator + watchdog) share a consistent view
    without IPC.

    Parameters
    ----------
    sentinel_file : Path | None
        Override the default KILL_SWITCH_FILE (useful for unit tests that
        operate in a temporary directory).
    """

    def __init__(self, sentinel_file: Optional[Path] = None) -> None:
        self._path = sentinel_file or KILL_SWITCH_FILE

    def is_active(self) -> bool:
        """Return True if the sentinel file exists."""
        return self._path.exists()

    def activate(self, reason: str = "") -> None:
        """Create the sentinel file, halting all future order submissions.

        Uses an atomic write-then-rename pattern to avoid a partial-write race
        with ``is_active()``.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        content = f"Activated at {ts}\n{reason}".strip()
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.rename(self._path)
        logger.critical(
            "KILL SWITCH ACTIVATED — all order submission is now BLOCKED. "
            "Reason: %s. File: %s",
            reason or "(no reason given)",
            self._path,
        )

        # Route the activation through the unified alert dispatcher so an
        # operator is notified out-of-band (Discord/Slack/email/file), not just
        # via a log line. send_alert never raises, but we still guard the whole
        # call so a broken import can never destabilise activation.
        #
        # dedup_key="kill_switch_activate": activate() is idempotent (see the
        # class docstring) and can be called repeatedly while the sentinel is
        # already active (e.g. a watchdog re-asserting it every poll cycle).
        # Without dedup, that would fire an identical CRITICAL alert on every
        # call. The dedup key is intentionally reason-agnostic — "kill switch
        # is active" is the condition being alerted on, not the specific
        # reason text — so a burst of activate() calls with different reason
        # strings inside the window still collapses to one alert.
        try:
            from observability.alerts import send_alert
            send_alert(
                "CRITICAL",
                f"Kill switch ACTIVATED — all order submission BLOCKED. "
                f"Reason: {reason or '(no reason given)'}",
                extra={"reason": reason, "sentinel_file": str(self._path)},
                dedup_key="kill_switch_activate",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("kill_switch: send_alert on activation failed (%s)", exc)

        if settings.FLATTEN_ON_KILL:
            # Replace the old log-only "close positions manually" reminder with a
            # concrete, human-reviewable GATED DRY-RUN proposal. This NEVER
            # places an order — see execution/flatten_proposal.py. Guarded so a
            # proposal-emission failure can never prevent the kill switch from
            # activating (the safety-critical action already completed above).
            try:
                from execution.flatten_proposal import emit_flatten_proposal
                emit_flatten_proposal(
                    reason=reason,
                    output_dir=self._path.parent,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(
                    "kill_switch: flatten-on-kill proposal emission failed (%s). "
                    "Manually close all open positions before reactivating.",
                    exc,
                )

    def deactivate(self) -> None:
        """Remove the sentinel file, re-enabling order submission."""
        if self._path.exists():
            self._path.unlink()
            logger.warning(
                "KILL SWITCH DEACTIVATED — order submission re-enabled. "
                "File removed: %s",
                self._path,
            )
        else:
            logger.info("deactivate() called but kill switch was not active.")

    def reason(self) -> str:
        """Return the reason text stored in the file, or '' if inactive."""
        if not self._path.exists():
            return ""
        try:
            return self._path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""


# ---------------------------------------------------------------------------
# CLI entry point  (python -m execution.kill_switch)
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
    group.add_argument("--status", action="store_true", help="Print current status.")
    parser.add_argument("--reason", default="", help="Reason text (--activate only).")
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
        print(f"Kill switch: {'ACTIVE' if active else 'INACTIVE'}")
        if active:
            print(f"Reason: {ks.reason() or '(none stored)'}")
            print(f"File: {ks._path}")


if __name__ == "__main__":
    _main()
