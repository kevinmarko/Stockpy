"""Child process for ONE forecast-backfill run.

Launched exclusively by :mod:`ml.forecast_backfill_job` as
``python3 -m ml.forecast_backfill_worker --params-fd N --events-fd M``,
mirroring :mod:`data.robinhood_login_worker`'s isolated-worker contract:
  - stdin redirected to ``/dev/null``;
  - a fresh, single-flight process group (set by the parent's
    ``subprocess.Popen(..., start_new_session=True)`` call, so the parent
    can kill this process AND anything it spawns via ``os.killpg`` on
    cancel/deadline);
  - two extra file descriptors passed in by the parent via ``pass_fds``, at
    whatever fd numbers the OS happened to assign (passed explicitly as CLI
    args, exactly like the login worker, rather than a fixed convention):
    ``--params-fd`` (read) carries the run parameters as one JSON line, then
    EOF; ``--events-fd`` (write) carries this worker's own NDJSON progress
    events back to the parent.

Imports :class:`ml.forecast_backfill.AgenticForecastBackfiller` directly and
drives its 6 ``step_N_*`` methods plus ``export_results()`` itself -- it
does NOT shell out to ``scripts/run_forecast_backfill.py`` (that CLI's
comma-joined-string flags are lossy for ``strategy_ids: list[str]``, and
would add a second process hop for no benefit).

Emits one ``{"event":"phase","phase":<name>,"step":N,"total_steps":7}``
event IMMEDIATELY BEFORE each corresponding step call -- so a poller sees
"about to run step N" while it is actually in flight, not after it already
finished -- then exactly one terminal event:
``{"event":"result","ok":true,"summary":{...},"sample_rows":N}`` or
``{"event":"result","ok":false,"error":str,"error_type":"value_error"|"unexpected"}``.

Per ``AgenticForecastBackfiller``'s own docstrings, only
``step_2_calculate_technical_features`` raises on its own (``ValueError`` on
zero usable tickers) -- steps 1, 3, 4, 5, and 6 all degrade internally
(dead-letter: dropped tickers, skipped/untrained strategies, NaN
probabilities) rather than raising. This worker still wraps the FULL step
sequence in one try/except (never assuming a specific step is the only one
that can ever raise -- e.g. a genuinely unexpected error from any step)
rather than special-casing step 2 alone, and reports the JSON-safe TYPE
distinction (``"value_error"`` vs. ``"unexpected"``) so a poller can tell a
validated, expected failure mode (bad input, no data) apart from a real bug.

Unlike the Robinhood login worker, run parameters and error text here carry
no credential material -- ``str(exc)`` is safe to report on the events
stream (there is no CONSTRAINT #3 concern for a forecast-backfill run).

Exit codes: 0 success, 1 any failure (malformed params, value_error, or
unexpected) -- matches ``scripts/run_forecast_backfill.py``'s existing
0/1 convention.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, TextIO, Tuple

# (phase name, 1-indexed step number) in execution order. `exporting` covers
# export_results() -- not one of AgenticForecastBackfiller's 6 step_N_*
# methods, but still a real, potentially slow (CSV + JSON write) phase a
# poller should see distinctly rather than have the job appear to hang
# between "backfilling" and the terminal result.
_PHASES: Tuple[Tuple[str, int], ...] = (
    ("fetching_data", 1),
    ("technical_features", 2),
    ("primary_signals", 3),
    ("meta_targets", 4),
    ("backtraining", 5),
    ("backfilling", 6),
    ("exporting", 7),
)
_TOTAL_STEPS = len(_PHASES)


def _make_emitter(events_fh: TextIO):
    def emit(obj: dict) -> None:
        events_fh.write(json.dumps(obj) + "\n")
        events_fh.flush()

    return emit


def _read_params(params_fh: TextIO) -> Dict[str, Any]:
    """One JSON line on the params fd, then EOF. An empty/whitespace-only
    payload means "use every AgenticForecastBackfiller constructor default"
    (equivalent to calling it with no keyword arguments at all)."""
    raw = params_fh.readline()
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("forecast_backfill_worker: params payload must be a JSON object")
    return payload


def _run(params: Dict[str, Any], emit) -> int:
    from ml.forecast_backfill import AgenticForecastBackfiller

    def _phase(name: str, step: int) -> None:
        emit({"event": "phase", "phase": name, "step": step, "total_steps": _TOTAL_STEPS})

    try:
        engine = AgenticForecastBackfiller(**params)

        _phase(*_PHASES[0])
        engine.step_1_fetch_data()

        _phase(*_PHASES[1])
        engine.step_2_calculate_technical_features()

        _phase(*_PHASES[2])
        engine.step_3_generate_primary_signals()

        _phase(*_PHASES[3])
        engine.step_4_create_meta_targets()

        _phase(*_PHASES[4])
        engine.step_5_backtrain_meta_labelers()

        _phase(*_PHASES[5])
        engine.step_6_execute_backfill()

        _phase(*_PHASES[6])
        output_df, summary = engine.export_results()

        emit(
            {
                "event": "result",
                "ok": True,
                "summary": summary,
                "sample_rows": len(output_df),
            }
        )
        return 0
    except ValueError as exc:
        emit({"event": "result", "ok": False, "error": str(exc), "error_type": "value_error"})
        return 1
    except Exception as exc:  # noqa: BLE001 - report to the events stream, never let it escape silently
        emit({"event": "result", "ok": False, "error": str(exc), "error_type": "unexpected"})
        return 1


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--params-fd", type=int, required=True)
    parser.add_argument("--events-fd", type=int, required=True)
    args = parser.parse_args(argv)

    events_fh = os.fdopen(args.events_fd, "w", encoding="utf-8", closefd=True)
    emit = _make_emitter(events_fh)
    try:
        try:
            params = _read_params(os.fdopen(args.params_fd, "r", encoding="utf-8", closefd=True))
        except Exception as exc:  # noqa: BLE001 - malformed params payload -> honest failure, never a hang
            emit({"event": "result", "ok": False, "error": str(exc), "error_type": "value_error"})
            return 1

        try:
            return _run(params, emit)
        except Exception as exc:  # noqa: BLE001 - never let an unhandled error escape silently
            emit({"event": "result", "ok": False, "error": str(exc), "error_type": "unexpected"})
            return 1
    finally:
        events_fh.close()


if __name__ == "__main__":
    sys.exit(main())
