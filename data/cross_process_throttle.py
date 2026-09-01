"""Cross-process minimum-request-spacing throttle.

Why this exists
----------------
``data/fmp_client.py``'s and ``data/edgar_fundamentals.py``'s existing spacing
throttles are plain module-level globals guarded by a ``threading.Lock`` --
safe across every thread within ONE process, but blind to every other OS
process. This repo routinely runs many simultaneous git worktrees on one
machine (see CLAUDE.md's Branch Workflow section); each is an independent
Python process that believes it owns the FULL per-account FMP/SEC request
budget. When several run ``scripts/refresh_validations.py``/backfill scripts
at the same time, their combined request rate can exceed the real shared
limit, producing a timing-dependent, non-deterministic subset of 429/5xx/
timeout failures per run.

This was empirically confirmed as a real production issue, not a theoretical
one, twice independently: ``docs/known_issues/xsec_universe_coverage_concurrency_variance.md``
(2026-08-22) found ``cross_sectional_momentum``/``sector_quality_rank`` runs
under concurrent ``--workers 6`` load hitting FMP's cooldown breaker
(``FMP cooldown active for another 299s/300s after 12/15 consecutive failed
requests``) while bit-identical in isolation -- and closed the *symptom*
(silently swinging ``deployable`` verdicts) with a fail-closed universe-
coverage gate, explicitly disclosing this module's fix as out-of-scope
follow-up work: "a cross-worktree coordination mechanism (e.g. a shared lock
file, a serialized request queue...)". ``docs/VALIDATION_STRATEGY_FIX_LOG.md``'s
2026-08-22 ``lgbm_ranker`` entry independently traced the same mechanism via
DB-timestamp evidence of overlapping ``refresh_validations.py`` sweeps.

What this module does
----------------------
:func:`wait_turn` enforces "at least ``min_interval`` seconds since the last
call against this ``state_path``, across every process on this machine" via
a POSIX advisory file lock (``fcntl.flock``, held across the sleep -- the
same "hold the lock across the sleep, not just the read/write" rule the
existing in-process throttles already use, since releasing before sleeping
would let every waiting process compute the same gap and wake together, a
thundering herd that breaks the limit exactly when concurrency is added) on
a tiny state file recording the last request's ``time.monotonic()``
timestamp.

Two properties make this safe and correct:

- ``flock`` locks are scoped at the OS/kernel level to the FILE, not the
  process -- it serializes every thread of every process holding a
  reference to the same path, not just this one process's threads (which
  the existing ``threading.Lock`` already handled). It is automatically
  released if the holding process dies (crash, ``SIGKILL``), so there is no
  stale-lock cleanup concern, unlike a naive pidfile/mutex scheme.
- ``time.monotonic()`` on POSIX (Linux/macOS -- this repo's only supported
  platforms) is backed by ``CLOCK_MONOTONIC``, a single clock instance
  shared by the whole KERNEL since boot, not a per-process one -- so a
  timestamp persisted by one process and read by another is directly and
  safely comparable. (Unlike ``time.time()``, this also cannot be stepped
  backward by an NTP correction mid-run -- the same NTP-immunity rationale
  ``data/fmp_client.py``'s/``data/edgar_fundamentals.py``'s own in-process
  throttles already rely on, now extended to the cross-process case.)

This is an ADDITIONAL outer layer, not a replacement for the existing
in-process throttles -- both keep their own logic exactly as-is (preserving
every existing fake-clock-based arithmetic test byte-for-byte); each gains
one call to :func:`wait_turn` immediately before issuing the request.

``min_interval <= 0`` is a no-op with ZERO file I/O -- matches every other
``_MIN_REQUEST_INTERVAL_SECONDS=0`` "disable" convention in this codebase,
and is exactly what keeps ``conftest.py``'s ``_no_fmp_throttle_in_tests``
session-wide autouse fixture (which already zeroes
``FMP_MIN_REQUEST_INTERVAL_SECONDS``) safe with no changes needed -- this
module is never touched by ~all of the test suite.

Deliberately stdlib-only, no project imports -- a dependency-free leaf
(matching ``runtime_flags.py``'s "stdlib-only leaf" convention), so it can be
imported from any data-layer module with zero circular-import risk.

Deliberately scoped to the SPACING throttle only. Each process's own
consecutive-failure/cooldown circuit breaker (``data/fmp_client.py``'s
``_fmp_cooldown_until`` etc.) stays process-local -- making that cross-
process too is real added complexity (a shared atomic counter, cross-process
"logged once" semantics) for a secondary concern; the spacing throttle is
the mechanism identified above as causing the joint budget overrun.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX-only; this repo targets macOS/Linux
    fcntl = None  # type: ignore[assignment]

_no_fcntl_warned = False


def wait_turn(state_path: Path, min_interval: float) -> None:
    """Block the calling thread until at least ``min_interval`` seconds have
    elapsed since the last call to this function against ``state_path``,
    across every process on this machine holding a reference to the same
    path. See the module docstring for the full mechanism and rationale.

    Never raises: any I/O failure (permission error, disk full, an
    unparseable state file left by a corrupted prior write) degrades to a
    logged warning and a no-op for that call, rather than blocking a data
    fetch on a rate-limiter implementation detail (CONSTRAINT #6 -- fail
    open here is the correct default, since the WORST case is a few extra
    requests issued slightly too fast, not a crash).
    """
    global _no_fcntl_warned
    if min_interval <= 0:
        return
    if fcntl is None:
        if not _no_fcntl_warned:
            logger.warning(
                "cross_process_throttle.wait_turn: fcntl is unavailable on this "
                "platform (Windows?) -- cross-process spacing is disabled; only "
                "the caller's own in-process throttle applies."
            )
            _no_fcntl_warned = True
        return

    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(state_path), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        logger.warning(
            "cross_process_throttle.wait_turn: could not open state file %s (%s) "
            "-- cross-process spacing skipped for this call.", state_path, exc,
        )
        return

    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            try:
                raw = os.pread(fd, 64, 0).decode("ascii", "replace").strip()
                last = float(raw) if raw else 0.0
            except (OSError, ValueError):
                # A corrupt/partial state file (e.g. a prior crash mid-write)
                # degrades to "no prior request known" rather than raising --
                # the safe default is to NOT throttle on unreadable state.
                last = 0.0

            now = time.monotonic()
            if last > now:
                # `last` was written during a PRIOR boot session. POSIX
                # monotonic clocks (mach_absolute_time on macOS,
                # CLOCK_MONOTONIC on Linux) reset to near-zero on every
                # reboot, so a value written before a reboot always reads as
                # "in the future" to a process running in a new boot session
                # -- something that can never happen validly, since `last` is
                # only ever written from `time.monotonic()` a moment before.
                # Without this guard, `elapsed = now - last` goes deeply
                # negative and the sleep below blocks -- WHILE HOLDING this
                # exclusive lock -- for the full magnitude of the gap.
                # Observed in production: a multi-day sleep (the file's stale
                # timestamp was ~914,497s / ~10.6 days into a prior boot
                # session; the reading process was only ~232,906s / ~2.7 days
                # into the new one, giving an ~681,591s / ~7.9-day sleep)
                # that froze EVERY process on the machine wanting to make an
                # FMP/EDGAR request, since they all queue on this same lock.
                # Treat it identically to a corrupt/unreadable state file: no
                # prior request known. See
                # docs/known_issues/cross_process_throttle_monotonic_clock_reboot_reset.md.
                last = 0.0

            elapsed = now - last
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)

            stamp = f"{time.monotonic():.6f}".encode("ascii")
            os.pwrite(fd, stamp, 0)
            os.ftruncate(fd, len(stamp))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError as exc:
        logger.warning(
            "cross_process_throttle.wait_turn: lock/read/write failed for %s "
            "(%s) -- cross-process spacing skipped for this call.", state_path, exc,
        )
    finally:
        os.close(fd)
