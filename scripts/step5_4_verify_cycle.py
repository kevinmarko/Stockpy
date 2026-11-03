"""
scripts/step5_4_verify_cycle.py
===============================
The "one dry-run cycle" step of ``make verify`` and ``verify.command`` (step 3 of 3).

Before step 5.4 that step ran ``import main; main.run_once()``. ``main.py`` is
archived (step 5.5), so it now runs ONE pipeline cycle the way
``python3 main_orchestrator.py --dry-run`` does::

    asyncio.run(main_orchestrator.main(dry_run=True, strict=False))

and then proves the cycle really ran before anything prints "verify complete".

Safety: this is a verification step, not a production run
----------------------------------------------------------
Two parts of the daemon's cycle ignore ``--dry-run`` and would act on the
operator's live setup (a deployment cut over to ``DAEMON_AGENTIC_QUEUE_MODE=
primary`` writes the real queue from the daemon; before cutover ``shadow`` only
writes review copies, and ``off`` writes nothing): in ``primary``
``AgenticQueueStep`` would write the REAL ``execution_queue.json`` and
``watch_state.json`` and send pushes, and the end-of-cycle daily summary
would post to Slack / email / the alert file. ``main.run_once()`` did neither.
So, in this process only, and restored on exit, this helper:

* forces ``settings.DAEMON_AGENTIC_QUEUE_MODE = "off"`` (the step then writes
  nothing and sends nothing), ``settings.SYMBOL_RATING_ENABLED = False`` (the
  daemon records at most ONE durable rating cycle per trading day; a verify run
  must not spend it, least of all on synthetic data) and
  ``settings.SIZING_CAP_AUDIT_ENABLED = False`` (durable cap-audit rows);
* wraps ``main_orchestrator.fetch_account_snapshot`` so it never starts a live
  Robinhood login (``allow_live_fetch=False``: cached snapshot only). With
  ``ROBINHOOD_AUTO_REFRESH_ENABLED`` on and a cache older than 20 h the default
  call would prompt the operator's phone for device approval and block, and a
  headless script must never do that (CLAUDE.md);
* replaces ``main_orchestrator._heartbeat`` with a no-op, because the daemon
  never refreshes ``heartbeat.txt``: a file left behind by this run would go
  stale and make the heartbeat panel show "Stale" instead of "No heartbeat";
* replaces ``main_orchestrator._dispatch_daily_summary`` with a no-op that
  logs the suppression (and records the cycle's ``RunContext`` so the helper
  can say why a cycle stopped early or fell back to synthetic data);
* replaces ``observability.alerts.send_alert`` (the one function every alert
  channel goes through, including the daily summary) with a logging no-op, so
  a risk-gate or sizing-cap alert raised during the cycle cannot page the
  operator either;
* runs with ``dry_run=True``. ``RunContext.force_account`` carries that flag
  to ``BrokerExecutionStep`` -> ``_execute_broker_orders(dry_run)`` ->
  ``OrderManager(dry_run=...)``, which stamps ``intent.dry_run`` and returns
  before the broker call, so no paper order is placed. (The pre-trade risk gate
  runs BEFORE that interception and appends to ``risk_gate_blocks.jsonl`` when
  it blocks an order; that audit line is not suppressed.)

If any override cannot be applied the cycle is NOT run (fail closed).

Logging order
-------------
``main_orchestrator`` calls ``logging.basicConfig`` at import, and
``alerting.setup_logging()`` returns immediately when the root logger already
has handlers. So ``setup_logging()`` runs BEFORE ``main_orchestrator`` is
imported (and this module never imports it at module scope), otherwise the
rotating log file would never be attached. ``main.py`` had no import-time
``basicConfig``, which is why the old code could import first.

Exit codes (fail closed)
------------------------
    0  the cycle completed AND ``state_snapshot.json`` was refreshed by it
    1  the cycle raised (``PipelineFatalError`` or anything else, including a
       ``SystemExit``), or the safety overrides could not be applied
    3  the cycle returned without raising but is NOT proven to have refreshed
       the snapshot (kill switch active, stopped early, snapshot unwritable, or
       the end-of-cycle hook was never reached so a fresh file cannot be
       attributed to this run): an explicit "cycle did not refresh the
       snapshot: <reason>" line is printed so a cycle that did not run can never
       be reported as a pass
    4  the cycle ran and refreshed the snapshot but proves nothing about live
       data: it used synthetic ``MockDataEngine`` data, or the snapshot holds no
       signals. The wiring ran; "the live pipeline works" is NOT established, so
       ``make verify`` must not say it passed

What it still does (the old ``run_once()`` step did not)
--------------------------------------------------------
It rewrites ``state_snapshot.json`` (plus its ``history/`` copy), the HTML
dashboards and ``dead_letter.json`` under ``OUTPUT_DIR`` (the old ``run_once()``
step did none of these; it did write ``progress.json``, as this cycle does),
stamps ``last_data_refresh.txt``, updates the local DB / caches and fetches
market data. A ``MockDataEngine`` cycle (no ``FRED_API_KEY``, or no data came
back) exits 4: the cycle ran, but it proves the wiring, not live data, and
because ``StateSnapshotStep`` ignores the synthetic flag (a known, unfixed
pre-existing gap) it has also overwritten the snapshot and dashboards with that
data. It is detected from the captured ``RunContext``
(``context_extras['data_is_synthetic']`` or a ``MockDataEngine`` market).
Nothing checks whether a daemon cycle is in flight: if one is, its snapshot
could satisfy the freshness check (the missing end-of-cycle hook above catches
only the case where this run never finished) and the two cycles share
``progress.json``.

Usage
-----
    python scripts/step5_4_verify_cycle.py
    make verify            # step 3 of 3
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import json
import logging
import re
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("step5_4_verify_cycle")

EXIT_OK = 0
EXIT_CYCLE_FAILED = 1
EXIT_SNAPSHOT_NOT_REFRESHED = 3
EXIT_UNVERIFIED = 4

#: Settings forced for this process only (restored on exit). Every name must exist
#: on ``Settings``; a missing one fails closed (the cycle is not run).
_FORCED_QUEUE_MODE = "off"
_FORCED_SETTINGS = {
    "DAEMON_AGENTIC_QUEUE_MODE": _FORCED_QUEUE_MODE,
    "SYMBOL_RATING_ENABLED": False,
    "SIZING_CAP_AUDIT_ENABLED": False,
}
_SNAPSHOT_NAME = "state_snapshot.json"
# Filesystems stamp mtimes with a coarse clock (and some with 1 s granularity),
# so an mtime a hair before our own start time is not proof of a stale file.
# The snapshot's own ``timestamp`` field (fine-grained, written by the cycle) is
# the exact check; mtime only has to rule out a clearly older file.
_MTIME_SLACK_SECONDS = 2.0

_SECRET_QUERY_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|token|password|passwd|secret|auth)=([^&\s'\"]+)"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")


# ---------------------------------------------------------------------------
# Secret scrubbing (the cycle's exceptions can carry URLs with API keys)
# ---------------------------------------------------------------------------

def _secret_values() -> List[str]:
    """Non-trivial values of every secret setting (``shared.env_io.SECRET_KEYS``)."""
    try:
        from settings import settings
        from shared.env_io import SECRET_KEYS

        values: List[str] = []
        for key in SECRET_KEYS:
            value = getattr(settings, key, None)
            if isinstance(value, str) and len(value) >= 6:
                values.append(value)
        # Longest first so a value that contains another is masked whole.
        return sorted(set(values), key=len, reverse=True)
    except Exception:  # noqa: BLE001 - scrubbing must never raise
        return []


def scrub(text: str) -> str:
    """Mask configured secret values and ``key=value`` / ``Bearer`` credentials."""
    out = str(text)
    for value in _secret_values():
        out = out.replace(value, "***")
    out = _SECRET_QUERY_RE.sub(lambda m: f"{m.group(1)}=***", out)
    return _BEARER_RE.sub(lambda m: f"{m.group(1)} ***", out)


# ---------------------------------------------------------------------------
# Snapshot freshness
# ---------------------------------------------------------------------------

@dataclass
class SnapshotCheck:
    """Result of :func:`check_snapshot`. ``reason`` is set iff ``ok`` is False."""

    ok: bool
    reason: Optional[str] = None
    timestamp: Optional[str] = None
    n_signals: Optional[int] = None


def _fmt_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def check_snapshot(output_dir: Any, started_epoch: float) -> SnapshotCheck:
    """Did a cycle that started at ``started_epoch`` (``time.time()``) write the snapshot?

    Fails closed: a missing, older, unreadable or timestamp-less snapshot is
    "not refreshed". Never raises.
    """
    path = Path(output_dir) / _SNAPSHOT_NAME
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return SnapshotCheck(False, f"{path} does not exist (nothing wrote a snapshot)")
    except OSError as exc:
        return SnapshotCheck(False, f"cannot stat {path}: {type(exc).__name__}")
    if mtime + _MTIME_SLACK_SECONDS < started_epoch:
        return SnapshotCheck(
            False,
            f"{path.name} is stale: last written {_fmt_epoch(mtime)}, "
            f"before this run started {_fmt_epoch(started_epoch)}",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        stamp = str(payload["timestamp"])
        written = datetime.fromisoformat(stamp)
        if written.tzinfo is None:
            written = written.replace(tzinfo=timezone.utc)
        signals = payload.get("signals")
        n_signals = len(signals) if isinstance(signals, list) else None
    except Exception as exc:  # noqa: BLE001 - any parse problem is "not proven fresh"
        return SnapshotCheck(
            False, f"{path.name} is unreadable or has no valid timestamp ({type(exc).__name__})"
        )
    if written.timestamp() < started_epoch:
        return SnapshotCheck(
            False,
            f"{path.name} is stale: its timestamp {stamp} is before this run "
            f"started {_fmt_epoch(started_epoch)}",
        )
    return SnapshotCheck(True, None, stamp, n_signals)


# ---------------------------------------------------------------------------
# Safety overrides + the cycle
# ---------------------------------------------------------------------------

@dataclass
class _Observation:
    """What the suppression hooks saw during the cycle."""

    ctx: Any = None
    summary_suppressed: bool = False
    alerts_suppressed: int = 0


def _load_orchestrator() -> Any:
    """Configure logging, THEN import ``main_orchestrator`` (see module docstring)."""
    from alerting import setup_logging

    setup_logging()
    return importlib.import_module("main_orchestrator")


@contextlib.contextmanager
def _forced_safe_mode(orchestrator: Any, obs: _Observation) -> Iterator[None]:
    """Apply the in-process safety overrides; restore every one on exit.

    Everything that can fail (attribute lookups, the alerts import) runs before
    anything is changed, so a failure leaves the process untouched and the
    caller never runs an unprotected cycle.
    """
    from settings import settings
    import observability.alerts as alerts_mod

    saved_settings = {name: getattr(settings, name) for name in _FORCED_SETTINGS}
    saved_dispatch = orchestrator._dispatch_daily_summary
    saved_send_alert = alerts_mod.send_alert
    saved_fetch_snapshot = orchestrator.fetch_account_snapshot
    # Optional: a cosmetic guard, not a safety one, so a renamed heartbeat task
    # must not stop the verify run.
    saved_heartbeat = getattr(orchestrator, "_heartbeat", None)

    def _suppressed_dispatch(ctx: Any) -> None:
        obs.ctx = ctx
        obs.summary_suppressed = True
        logger.info("verify cycle: end-of-cycle daily summary suppressed (nothing was sent).")

    def _suppressed_send_alert(*args: Any, **kwargs: Any) -> None:
        obs.alerts_suppressed += 1
        logger.info("verify cycle: alert suppressed (nothing was sent): %s", scrub(str(args[:2])))

    def _cache_only_fetch_account_snapshot(*args: Any, **kwargs: Any) -> Any:
        kwargs["allow_live_fetch"] = False
        return saved_fetch_snapshot(*args, **kwargs)

    async def _no_heartbeat(*args: Any, **kwargs: Any) -> None:
        return None

    for name, value in _FORCED_SETTINGS.items():
        setattr(settings, name, value)
    orchestrator._dispatch_daily_summary = _suppressed_dispatch
    orchestrator.fetch_account_snapshot = _cache_only_fetch_account_snapshot
    if saved_heartbeat is not None:
        orchestrator._heartbeat = _no_heartbeat
    alerts_mod.send_alert = _suppressed_send_alert
    try:
        yield
    finally:
        for name, value in saved_settings.items():
            setattr(settings, name, value)
        orchestrator._dispatch_daily_summary = saved_dispatch
        orchestrator.fetch_account_snapshot = saved_fetch_snapshot
        if saved_heartbeat is not None:
            orchestrator._heartbeat = saved_heartbeat
        alerts_mod.send_alert = saved_send_alert


def _cycle_not_refreshed_reason(check: SnapshotCheck, obs: _Observation) -> str:
    """Best explanation for a cycle that returned without refreshing the snapshot."""
    ctx = obs.ctx
    if ctx is None:
        detail = check.reason or (
            "a snapshot newer than this run exists, but it cannot be attributed to "
            "this run (a daemon cycle may have written it)"
        )
        return f"the cycle returned without reaching its end-of-cycle hook ({detail})"
    if getattr(ctx, "stopped", False):
        stop = getattr(ctx, "stop_reason", None) or "no reason recorded"
        hint = (
            "; deactivate with: python -m execution.kill_switch --deactivate"
            if stop == "kill_switch"
            else ""
        )
        return f"the cycle stopped early (stop_reason={stop}){hint} - {check.reason}"
    return str(check.reason)


def _print_briefing(output_dir: Any) -> None:
    """Print ``scripts.daily_briefing`` for ``OUTPUT_DIR`` (its default dir is the repo's)."""
    try:
        from scripts.daily_briefing import generate_briefing

        print(generate_briefing(Path(output_dir)))
    except Exception as exc:  # noqa: BLE001 - the briefing is a courtesy, never a gate
        print(f"(daily briefing unavailable: {type(exc).__name__})")


def _used_synthetic_data(ctx: Any) -> bool:
    """True when the cycle ran on ``MockDataEngine`` data (either way it can get there).

    ``AsyncDataFetchStep`` sets ``context_extras['data_is_synthetic']`` only on
    its empty-data fallback branch; with no ``FRED_API_KEY`` it picks
    ``MockDataEngine`` up front WITHOUT setting that flag, so the engine itself
    is checked too.
    """
    if ctx is None:
        return False
    if (getattr(ctx, "context_extras", None) or {}).get("data_is_synthetic"):
        return True
    market = getattr(ctx, "market", None)
    return any(cls.__name__ == "MockDataEngine" for cls in type(market).__mro__)


def _print_disclosure(output_dir: Any, obs: _Observation, *, refreshed: bool) -> None:
    print()
    print("--- what this verify cycle did ---")
    print(
        f"  Forced for this process only: DAEMON_AGENTIC_QUEUE_MODE={_FORCED_QUEUE_MODE} "
        "(no execution_queue.json / watch_state.json / pushes), dry-run (no paper orders), "
        "symbol-rating and cap-audit recording off, no live Robinhood login (cached account "
        f"snapshot only), no heartbeat file, daily summary suppressed, {obs.alerts_suppressed} "
        "alert(s) suppressed."
    )
    print(
        "  A completed cycle rewrites state_snapshot.json (+ history/), the HTML dashboards, "
        "dead_letter.json and progress.json under "
        f"{output_dir}, stamps last_data_refresh.txt, updates the local DB / caches, fetches "
        "market data, and appends to risk_gate_blocks.jsonl if the pre-trade risk gate blocks an "
        "order. The old run_once() verify step did not rewrite the snapshot, dashboards or "
        "dead_letter.json. last_data_refresh.txt can make the daemon's timer skip its next pull "
        "within DATA_FRESHNESS_TTL_SECONDS."
    )
    if not refreshed:
        print(
            "  This run stopped before it wrote the snapshot; only the steps before that "
            "(data fetch etc.) touched their files."
        )
    if obs.ctx is None:
        print("  (could not inspect the cycle's data source: end-of-cycle hook not reached)")
    elif _used_synthetic_data(obs.ctx):
        print(
            "  WARNING: this cycle ran on SYNTHETIC MockDataEngine data (no FRED key, or no market "
            "data came back). It proves the wiring ran, NOT that live data works, and "
            "StateSnapshotStep ignores that flag, so it has OVERWRITTEN state_snapshot.json and "
            "the dashboards with that data. Fix the data source and run it again."
        )


def _report_cycle_failure(exc: BaseException) -> None:
    from settings import settings

    print(f"\nVERIFY CYCLE FAILED: {type(exc).__name__}: {scrub(str(exc))}", file=sys.stderr)
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print(scrub(tb), file=sys.stderr)
    log_file = Path(settings.LOCAL_DATA_ROOT) / "logs" / "investyo.log"
    print(f"Pipeline log: {log_file}", file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    """Run one verification cycle; return the process exit code (see module docstring)."""
    parser = argparse.ArgumentParser(
        description=(
            "Run ONE dry-run pipeline cycle for make verify / verify.command "
            "(queue mode forced off: no orders, no pushes) and confirm it refreshed "
            "state_snapshot.json."
        )
    )
    parser.parse_args(argv)

    started_epoch = time.time()
    obs = _Observation()
    print("Running one dry-run pipeline cycle (queue mode forced off; no orders, no pushes)...")
    sys.stdout.flush()
    try:
        orchestrator = _load_orchestrator()
        with _forced_safe_mode(orchestrator, obs):
            asyncio.run(orchestrator.main(dry_run=True, strict=False))
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - fail closed on anything
        _report_cycle_failure(exc)
        return EXIT_CYCLE_FAILED

    from settings import settings

    output_dir = settings.OUTPUT_DIR
    check = check_snapshot(output_dir, started_epoch)
    if not check.ok:
        reason = _cycle_not_refreshed_reason(check, obs)
        print(f"VERIFY CYCLE INCOMPLETE - cycle did not refresh the snapshot: {reason}", file=sys.stderr)
        _print_disclosure(output_dir, obs, refreshed=False)
        return EXIT_SNAPSHOT_NOT_REFRESHED

    if obs.ctx is None:
        # A fresh snapshot but the end-of-cycle hook never ran: it cannot be attributed to
        # this run (a daemon cycle may have written it). Fail closed.
        reason = _cycle_not_refreshed_reason(SnapshotCheck(False), obs)
        print(f"VERIFY CYCLE INCOMPLETE - cycle did not refresh the snapshot: {reason}", file=sys.stderr)
        _print_disclosure(output_dir, obs, refreshed=False)
        return EXIT_SNAPSHOT_NOT_REFRESHED

    unverified: Optional[str] = None
    if _used_synthetic_data(obs.ctx):
        unverified = (
            "the cycle ran on SYNTHETIC MockDataEngine data (no FRED_API_KEY, or no market data "
            "came back), so it proves the wiring, not that the live pipeline works"
        )
    elif not check.n_signals:
        unverified = (
            f"{_SNAPSHOT_NAME} holds no signals (n={check.n_signals}), so nothing was evaluated"
        )
    if unverified is not None:
        print(f"VERIFY CYCLE INCOMPLETE - {unverified}.", file=sys.stderr)
        _print_disclosure(output_dir, obs, refreshed=True)
        return EXIT_UNVERIFIED

    print(
        f"VERIFY CYCLE OK - dry-run cycle completed; {_SNAPSHOT_NAME} refreshed "
        f"at {check.timestamp} ({check.n_signals} signals)."
    )
    print()
    _print_briefing(output_dir)
    _print_disclosure(output_dir, obs, refreshed=True)
    return EXIT_OK


if __name__ == "__main__":
    # Venv re-exec + .env loading, placed here (not at module top) so importing
    # this module as a library (the tests do) never loads .env into os.environ.
    # See scripts/_bootstrap.py's module docstring.
    from scripts._bootstrap import bootstrap

    bootstrap()
    sys.exit(main())
