"""Child process for ONE Robinhood device-approval login attempt.

Launched exclusively by :mod:`data.robinhood_login` as
``python3 -m data.robinhood_login_worker --mode {connect|refresh} --creds-fd N --events-fd M``,
with:
  - stdin redirected to ``/dev/null`` (so robin_stocks' SMS/email fallback,
    which calls a blocking ``input()``, raises ``EOFError`` instead of
    hanging forever waiting for a line that will never come);
  - a fresh, single-flight process group (so the parent can kill this
    process AND anything it spawns on a deadline, via ``os.killpg``);
  - two extra file descriptors passed in by the parent via ``pass_fds``, at
    whatever fd numbers the OS happened to assign (passed explicitly as CLI
    args rather than a fixed convention like "always fd 3/4" -- avoiding any
    need for a fragile ``dup2``/``preexec_fn`` dance to relocate them):
    ``--creds-fd`` (read) carries candidate credentials as one JSON line,
    then EOF; ``--events-fd`` (write) carries this worker's own NDJSON
    progress events back to the parent.

Never passes credentials via argv (visible to any local user via ``ps``) or
the environment (visible via ``/proc``/``ps -E``) — only the anonymous pipe.
Never logs credential values anywhere; on failure, only the exception TYPE
is reported (CONSTRAINT #3).

Exit codes: 0 success, 2 no credentials available, 3 stdin-fallback hit
(``EOFError`` — the account needs an SMS/email code this flow can't supply),
4 login/fetch raised some other exception.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from typing import TextIO, Tuple

# Recognized ONLY as UI-facing phase advances -- never as the authoritative
# success/failure signal, which always comes from the login call's own
# control flow (return value / exception) below. Pinned against the
# installed robin_stocks source by tests/test_robinhood_login_worker.py, so
# a library upgrade that changes these strings fails CI loudly instead of
# silently leaving the UI's progress phase stuck forever.
_PHRASES: Tuple[Tuple[str, str], ...] = (
    ("Verification required, handling challenge", "authenticating"),
    ("Check robinhood app for device approvals", "awaiting_approval"),
    ("Verification successful", "verifying"),
    ("Workflow status approved", "verifying"),
)


class _PhraseTap(io.TextIOBase):
    """`redirect_stdout` target during the login call. Scans robin_stocks'
    bare `print()` output for known phase phrases and forwards each match as
    a structured event on the real events stream. The raw text itself is
    ALWAYS dropped -- it is the one channel that could ever carry account
    detail, and it carries no information the phase events don't already
    convey."""

    def __init__(self, emit) -> None:
        super().__init__()
        self._emit = emit

    def write(self, s: str) -> int:
        for needle, phase in _PHRASES:
            if needle in s:
                self._emit({"event": "phase", "phase": phase})
        return len(s)


def _make_emitter(events_fh: TextIO):
    def emit(obj: dict) -> None:
        events_fh.write(json.dumps(obj) + "\n")
        events_fh.flush()

    return emit


def _read_credentials(creds_fh: TextIO) -> Tuple[str, str]:
    """One JSON line on the credentials fd, then EOF. An empty/whitespace-only
    payload means "use whatever is already configured" -- the ``refresh``
    mode, where credentials are already in ``.env``."""
    raw = creds_fh.readline()
    if not raw.strip():
        from settings import settings

        return (
            (settings.RH_USERNAME or "").strip(),
            (settings.RH_PASSWORD or "").strip(),
        )
    payload = json.loads(raw)
    return str(payload.get("username", "")).strip(), str(payload.get("password", "")).strip()


def _ingest_orders_best_effort(emit) -> None:
    """Best-effort: fetch + durably persist the operator's real Robinhood
    filled-order history during a ``refresh`` login, reusing the real
    ``robin_stocks`` session already established in this process.

    Four non-negotiable properties (see the introducing PR / implementation
    plan for the full rationale):

    1. Its OWN try/except, covering everything below -- an orders-ingest
       failure must NEVER flip the worker's terminal ``result`` event to
       ``ok: false`` for an account-snapshot refresh that already succeeded.
       Nothing in this function is allowed to raise out of it.
    2. Called strictly AFTER ``rp.fetch_account_snapshot(force=True)`` by
       the caller -- never allowed to delay or endanger the artifact the
       parent process is actually blocking on.
    3. Only reached for ``mode == "refresh"`` (see ``_run`` below) --
       ``connect`` mode only verifies credentials, on a tighter deadline.
    4. Bounded by ``settings.RH_ORDER_INGEST_BUDGET_SECONDS`` -- full-history
       pagination plus one ``get_symbol_by_url`` network call per
       unresolved instrument could otherwise approach
       ``RH_LOGIN_DEADLINE_SECONDS`` and get the whole worker SIGKILLed
       mid-ingest, after the snapshot was already written but before this
       worker's own terminal ``result`` event -- turning a successful
       refresh into a reported timeout. On exhaustion, whatever resolved so
       far is still persisted; unresolved orders are skipped, same as any
       other unresolvable instrument.
    """
    import time

    from settings import settings

    if not settings.BROKER_TRADE_INGEST_ENABLED:
        return

    emit({"event": "phase", "phase": "fetching_orders"})
    try:
        from data.broker_fills_store import BrokerFillsStore
        from data.robinhood_orders import _default_symbol_resolver, fetch_filled_orders

        deadline = time.monotonic() + max(0, settings.RH_ORDER_INGEST_BUDGET_SECONDS)

        try:
            seed = BrokerFillsStore(readonly=True).instrument_symbol_map()
        except Exception:  # noqa: BLE001 - a cold/missing store just means no seed
            seed = {}

        inner_resolver = _default_symbol_resolver(
            seed=seed, max_network_resolutions=settings.RH_ORDER_SYMBOL_RESOLVE_MAX
        )

        def _time_bounded_resolver(url: str):
            if time.monotonic() > deadline:
                return None
            return inner_resolver(url)

        fills = fetch_filled_orders(force=True, symbol_resolver=_time_bounded_resolver)

        store = BrokerFillsStore()
        counts = store.record_fills(fills)

        newly_resolved = getattr(inner_resolver, "newly_resolved", None)
        if newly_resolved:
            try:
                store.record_instrument_symbols(newly_resolved)
            except Exception:  # noqa: BLE001 - resolver cache write is best-effort
                pass

        emit({
            "event": "log",
            "message": (
                f"Ingested {counts.get('inserted', 0)} new fill(s) "
                f"({len(fills)} fetched, {counts.get('divergent', 0)} corrected)."
            ),
        })
    except Exception as exc:  # noqa: BLE001 - MUST NEVER propagate (see docstring)
        emit({"event": "log", "message": f"orders ingest failed: {type(exc).__name__}"})


def _run(mode: str, creds_fd: int, emit) -> int:
    username, password = _read_credentials(os.fdopen(creds_fd, "r", encoding="utf-8", closefd=True))
    if not username or not password:
        emit({"event": "result", "ok": False, "code": "no_credentials"})
        return 2

    # Unlocks LoginMode.device_approval in data.robinhood_portfolio._login_with
    # -- set ONLY here, so that codepath structurally cannot run outside this
    # isolated, killable, stdin-redirected child.
    os.environ["RH_LOGIN_WORKER"] = "1"

    from data import robinhood_session

    emit({"event": "started", "pid": os.getpid()})

    try:
        with contextlib.redirect_stdout(_PhraseTap(emit)):
            from data import robinhood_portfolio as rp

            if mode == "refresh":
                # Runs the REAL login + full account fetch inside this
                # process (robin_stocks' auth state lives only in the
                # process that logged in) and persists the result to the
                # JSON cache + DB -- the parent reads it back from there
                # rather than this worker serializing a snapshot over the
                # pipe. _fetch_live_snapshot's own RH_LOGIN_WORKER branch
                # already wraps its internal _login_with call with
                # ensure_session_pickle()/backup_session_pickle() -- do NOT
                # duplicate those calls here, only the "connect" branch
                # below (which calls _login_with directly, bypassing
                # _fetch_live_snapshot) needs to own them itself.
                emit({"event": "phase", "phase": "fetching_snapshot"})
                rp.fetch_account_snapshot(force=True)
                _ingest_orders_best_effort(emit)
            else:
                # "connect": verify the candidate credentials and establish
                # a trusted device session. No snapshot fetch -- the caller
                # only asked to verify a login works, not to pull data.
                robinhood_session.ensure_session_pickle()
                rp._login_with(username, password, mode="device_approval")
                robinhood_session.backup_session_pickle()
    except EOFError:
        # stdin=/dev/null: robin_stocks' sms/email challenge branch hit a
        # blocking input() and got immediate EOF instead of hanging forever.
        # An honest, distinguishable failure -- this account needs a code
        # this flow cannot supply.
        emit({"event": "result", "ok": False, "code": "challenge_unsupported"})
        return 3
    except Exception as exc:  # noqa: BLE001 - report TYPE only, never str(exc)
        emit({"event": "result", "ok": False, "code": "auth_failed", "exc_type": type(exc).__name__})
        return 4

    emit({"event": "result", "ok": True})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("connect", "refresh"), required=True)
    parser.add_argument("--creds-fd", type=int, required=True)
    parser.add_argument("--events-fd", type=int, required=True)
    args = parser.parse_args(argv)

    events_fh = os.fdopen(args.events_fd, "w", encoding="utf-8", closefd=True)
    emit = _make_emitter(events_fh)
    try:
        return _run(args.mode, args.creds_fd, emit)
    except Exception as exc:  # noqa: BLE001 - never let an unhandled error escape silently
        emit({"event": "result", "ok": False, "code": "auth_failed", "exc_type": type(exc).__name__})
        return 4
    finally:
        events_fh.close()


if __name__ == "__main__":
    sys.exit(main())
