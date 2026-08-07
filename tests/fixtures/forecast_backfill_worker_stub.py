"""Stub worker for tests/test_forecast_backfill_job.py.

Mimics ``ml/forecast_backfill_worker.py``'s CLI contract (``--params-fd``,
``--events-fd``) closely enough for ``ml.forecast_backfill_job.start_job``'s
subprocess/pipe plumbing to be exercised end-to-end, but performs NO real
data fetching, feature engineering, or model training -- knows nothing
about ``ml.forecast_backfill.AgenticForecastBackfiller``. Never invoked
directly in production -- tests launch it in place of the real worker by
rewriting the ``-m ml.forecast_backfill_worker`` argv pair to this script's
path (see tests/test_forecast_backfill_job.py's ``_PopenProxy`` fixture,
which mirrors tests/test_robinhood_login.py's identical technique).

Behavior is selected via the ``BACKFILL_STUB_BEHAVIOR`` environment
variable, which the child inherits from the parent test process
(``start_job``'s ``subprocess.Popen`` call passes no explicit ``env=``, so
the child gets the full parent environment):

  success        -- emit all 7 phase events, then a successful 'result'
                    (a small fixed summary + sample_rows), exit 0.
  value_error    -- emit the first phase event, then a failed 'result' with
                    error_type='value_error', exit 1.
  unexpected     -- emit the first phase event, then a failed 'result' with
                    error_type='unexpected', exit 1.
  hang           -- emit the first phase event, then sleep far longer than
                    any test's FORECAST_BACKFILL_DEADLINE_SECONDS override.
                    The parent kills this process (via its deadline
                    enforcer or cancel_job()) -- it never exits on its own.
  no_result      -- emit the first phase event, then exit immediately
                    WITHOUT ever emitting a 'result' event -- exercises
                    _drain_events' EOF-with-no-result fallback.
  echo_params    -- read the one JSON params line off --params-fd and write
                    it VERBATIM to the file at the STUB_ECHO_PATH
                    environment variable (a side-channel for test
                    verification only -- never part of the real
                    events-stream protocol), then emit a normal successful
                    'result' so the job still completes as
                    state='succeeded'.

Exit codes loosely mirror the real worker's convention (0 success, 1
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

_PHASES = (
    ("fetching_data", 1),
    ("technical_features", 2),
    ("primary_signals", 3),
    ("meta_targets", 4),
    ("backtraining", 5),
    ("backfilling", 6),
    ("exporting", 7),
)


def _make_emitter(events_fh):
    def emit(obj: dict) -> None:
        events_fh.write(json.dumps(obj) + "\n")
        events_fh.flush()

    return emit


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--params-fd", type=int, required=True)
    parser.add_argument("--events-fd", type=int, required=True)
    args = parser.parse_args(argv)

    behavior = os.environ.get("BACKFILL_STUB_BEHAVIOR", "success")

    events_fh = os.fdopen(args.events_fd, "w", encoding="utf-8", closefd=True)
    emit = _make_emitter(events_fh)

    try:
        if behavior == "echo_params":
            params_fh = os.fdopen(args.params_fd, "r", encoding="utf-8", closefd=True)
            raw = params_fh.readline()
            echo_path = os.environ.get("STUB_ECHO_PATH")
            if echo_path:
                with open(echo_path, "w", encoding="utf-8") as fh:
                    fh.write(raw)
            emit({"event": "phase", "phase": "fetching_data", "step": 1, "total_steps": 7})
            emit({"event": "result", "ok": True, "summary": {"status": "completed"}, "sample_rows": 0})
            return 0

        if behavior == "hang":
            emit({"event": "phase", "phase": "fetching_data", "step": 1, "total_steps": 7})
            time.sleep(3600)
            return 0

        if behavior == "no_result":
            emit({"event": "phase", "phase": "fetching_data", "step": 1, "total_steps": 7})
            return 0

        if behavior == "value_error":
            emit({"event": "phase", "phase": "technical_features", "step": 2, "total_steps": 7})
            emit(
                {
                    "event": "result",
                    "ok": False,
                    "error": "Insufficient history across tickers to compute technical features.",
                    "error_type": "value_error",
                }
            )
            return 1

        if behavior == "unexpected":
            emit({"event": "phase", "phase": "backtraining", "step": 5, "total_steps": 7})
            emit({"event": "result", "ok": False, "error": "boom", "error_type": "unexpected"})
            return 1

        # "success" (default) -- emit every phase in order, then a real result.
        for name, step in _PHASES:
            emit({"event": "phase", "phase": name, "step": step, "total_steps": 7})
        emit(
            {
                "event": "result",
                "ok": True,
                "summary": {"status": "completed", "total_rows": 3},
                "sample_rows": 3,
            }
        )
        return 0
    finally:
        events_fh.close()


if __name__ == "__main__":
    sys.exit(main())
