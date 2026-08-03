"""Stub worker for tests/test_robinhood_login.py.

Mimics ``data/robinhood_login_worker.py``'s CLI contract (``--mode``,
``--creds-fd``, ``--events-fd``) closely enough for
``data.robinhood_login.start_login``'s subprocess/pipe plumbing to be
exercised end-to-end, but performs NO real Robinhood network calls and knows
nothing about robin_stocks. Never invoked directly in production -- tests
launch it in place of the real worker by rewriting the ``-m
data.robinhood_login_worker`` argv pair to this script's path (see
tests/test_robinhood_login.py's ``_PopenProxy`` fixture).

Behavior is selected via the ``STUB_LOGIN_BEHAVIOR`` environment variable,
which the child inherits from the parent test process (``start_login``'s
``subprocess.Popen`` call passes no explicit ``env=``, so the child gets the
full parent environment):

  success              -- emit 'started' then a successful 'result', exit 0.
  fail                 -- emit 'started' then a failed 'result'
                          (code='auth_failed'), exit 1.
  hang_after_started    -- emit 'started', then sleep far longer than any
                          test's RH_LOGIN_DEADLINE_SECONDS override. The
                          parent kills this process (via its deadline
                          enforcer or cancel_login()) -- it never exits on
                          its own.
  hang_no_started       -- sleep far longer than any test's
                          RH_LOGIN_STARTUP_SECONDS override WITHOUT ever
                          emitting 'started'. The parent's startup-timeout
                          guard kills this process and reports
                          child_start_failed.
  echo_creds            -- read the one JSON credentials line off
                          --creds-fd and write it VERBATIM to the file at
                          the STUB_ECHO_PATH environment variable (a
                          side-channel for test verification only -- never
                          part of the real events-stream protocol, and
                          never printed to stdout/stderr/a log), then emit
                          a normal 'started' + successful 'result' pair so
                          the job still completes as state='succeeded'.

Exit codes loosely mirror the real worker's convention (0 success, non-zero
otherwise) but are not load-bearing for any test -- job state is derived
entirely from the NDJSON events on --events-fd, exactly as with the real
worker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _make_emitter(events_fh):
    def emit(obj: dict) -> None:
        events_fh.write(json.dumps(obj) + "\n")
        events_fh.flush()

    return emit


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("connect", "refresh"), required=True)
    parser.add_argument("--creds-fd", type=int, required=True)
    parser.add_argument("--events-fd", type=int, required=True)
    args = parser.parse_args(argv)

    behavior = os.environ.get("STUB_LOGIN_BEHAVIOR", "success")

    if behavior == "hang_no_started":
        # Deliberately never touches --events-fd -- the parent's
        # startup-timeout guard is what's under test here.
        time.sleep(3600)
        return 0

    events_fh = os.fdopen(args.events_fd, "w", encoding="utf-8", closefd=True)
    emit = _make_emitter(events_fh)

    try:
        if behavior == "echo_creds":
            creds_fh = os.fdopen(args.creds_fd, "r", encoding="utf-8", closefd=True)
            raw = creds_fh.readline()
            echo_path = os.environ.get("STUB_ECHO_PATH")
            if echo_path:
                with open(echo_path, "w", encoding="utf-8") as fh:
                    fh.write(raw)
            emit({"event": "started", "pid": os.getpid()})
            emit({"event": "result", "ok": True})
            return 0

        emit({"event": "started", "pid": os.getpid()})

        if behavior == "hang_after_started":
            # data.robinhood_login._enforce_deadline's startup-timeout guard
            # keys off job.phase leaving "starting" -- which _drain_events
            # only advances on a 'phase' or 'result' event, NOT on the
            # 'started' event itself (that event exists for other purposes,
            # e.g. surfacing the child's pid). A real worker reaches this
            # point once robin_stocks prints its device-approval phrase; the
            # stub simulates that same "genuinely in progress, now waiting
            # on a human" moment before hanging (never emitting a result).
            emit({"event": "phase", "phase": "awaiting_approval"})
            time.sleep(3600)
            return 0

        if behavior == "fail":
            emit({"event": "result", "ok": False, "code": "auth_failed"})
            return 1

        # "success" (default)
        emit({"event": "result", "ok": True})
        return 0
    finally:
        events_fh.close()


if __name__ == "__main__":
    sys.exit(main())
