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

# Canonical sentinel-file locations. Tests may override via GlobalKillSwitch(sentinel_file=…, soft_halt_file=…).
KILL_SWITCH_FILE: Path = settings.OUTPUT_DIR / "KILL_SWITCH"
SOFT_HALT_FILE: Path = settings.OUTPUT_DIR / "SOFT_HALT"


class KillSwitchActiveError(RuntimeError):
    """Raised by OrderManager when the global kill switch is active.

    Callers must catch this and abort order submission without retrying.
    """


class GlobalKillSwitch:
    """
    Stateless file-based kill switch and soft-halt guard.

    Every public method is idempotent.  The file system is the single source of
    truth so multiple processes (orchestrator + watchdog) share a consistent view
    without IPC.

    Parameters
    ----------
    sentinel_file : Path | None
        Override the default KILL_SWITCH_FILE (useful for unit tests that
        operate in a temporary directory).
    soft_halt_file : Path | None
        Override the default SOFT_HALT_FILE. If omitted but sentinel_file is given,
        defaults to sentinel_file.parent / "SOFT_HALT".
    """

    def __init__(
        self,
        sentinel_file: Optional[Path] = None,
        soft_halt_file: Optional[Path] = None,
    ) -> None:
        self._path = sentinel_file or KILL_SWITCH_FILE
        self._soft_halt_path = soft_halt_file or (
            self._path.parent / "SOFT_HALT" if sentinel_file else SOFT_HALT_FILE
        )

    def is_active(self) -> bool:
        """Return True if the sentinel file exists."""
        return self._path.exists()

    def is_soft_halt_active(self) -> bool:
        """Return True if the soft-halt sentinel file exists."""
        return self._soft_halt_path.exists()

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
            # No `macro_dto` passed here — this call site has no pipeline
            # `RunResult` in scope. `emit_flatten_proposal` self-sources one
            # via a zero-network cache read (execution.macro_snapshot
            # .load_cached_macro_dto) when none is given, so the risk gate's
            # macro checks still get real (if cache-stale) VIX/Sahm/regime
            # state instead of staying permanently blind.
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

    def activate_soft_halt(self, reason: str = "") -> None:
        """Create the soft-halt sentinel file, halting new BUY / risk-increasing orders.

        Uses atomic write-then-rename. Risk-reducing SELL orders remain permitted.
        """
        self._soft_halt_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        content = f"Soft halt activated at {ts}\n{reason}".strip()
        tmp = self._soft_halt_path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.rename(self._soft_halt_path)
        logger.warning(
            "SOFT HALT ACTIVATED — new BUY order submissions BLOCKED (SELLs permitted). "
            "Reason: %s. File: %s",
            reason or "(no reason given)",
            self._soft_halt_path,
        )

        try:
            from observability.alerts import send_alert
            send_alert(
                "WARNING",
                f"Soft halt ACTIVATED — new BUY orders BLOCKED (SELLs permitted). "
                f"Reason: {reason or '(no reason given)'}",
                extra={"reason": reason, "sentinel_file": str(self._soft_halt_path)},
                dedup_key="soft_halt_activate",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("kill_switch: send_alert on soft halt activation failed (%s)", exc)

    def deactivate_soft_halt(self) -> None:
        """Remove the soft-halt sentinel file, re-enabling BUY order submission."""
        if self._soft_halt_path.exists():
            self._soft_halt_path.unlink()
            logger.info(
                "SOFT HALT DEACTIVATED — BUY order submission re-enabled. "
                "File removed: %s",
                self._soft_halt_path,
            )
        else:
            logger.debug("deactivate_soft_halt() called but soft halt was not active.")

    def soft_halt_reason(self) -> str:
        """Return the reason text stored in the soft-halt sentinel, or '' if inactive."""
        if not self._soft_halt_path.exists():
            return ""
        try:
            return self._soft_halt_path.read_text(encoding="utf-8").strip()
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
    group.add_argument("--activate", action="store_true", help="Activate the global kill switch.")
    group.add_argument("--deactivate", action="store_true", help="Deactivate the global kill switch.")
    group.add_argument("--activate-soft-halt", action="store_true", help="Activate soft halt (block BUYs).")
    group.add_argument("--deactivate-soft-halt", action="store_true", help="Deactivate soft halt.")
    group.add_argument("--status", action="store_true", help="Print current status.")
    parser.add_argument("--reason", default="", help="Reason text (--activate / --activate-soft-halt).")
    args = parser.parse_args()

    ks = GlobalKillSwitch()
    if args.activate:
        ks.activate(reason=args.reason)
        print(f"Kill switch ACTIVATED. File: {ks._path}")
    elif args.deactivate:
        ks.deactivate()
        print(f"Kill switch DEACTIVATED. File removed: {ks._path}")
    elif args.activate_soft_halt:
        ks.activate_soft_halt(reason=args.reason)
        print(f"Soft halt ACTIVATED. File: {ks._soft_halt_path}")
    elif args.deactivate_soft_halt:
        ks.deactivate_soft_halt()
        print(f"Soft halt DEACTIVATED. File removed: {ks._soft_halt_path}")
    elif args.status:
        active = ks.is_active()
        soft_active = ks.is_soft_halt_active()
        print(f"Kill switch (HARD): {'ACTIVE' if active else 'INACTIVE'}")
        if active:
            print(f"Reason: {ks.reason() or '(none stored)'}")
            print(f"File: {ks._path}")
        print(f"Soft halt:          {'ACTIVE' if soft_active else 'INACTIVE'}")
        if soft_active:
            print(f"Reason: {ks.soft_halt_reason() or '(none stored)'}")
            print(f"File: {ks._soft_halt_path}")


if __name__ == "__main__":
    _main()
