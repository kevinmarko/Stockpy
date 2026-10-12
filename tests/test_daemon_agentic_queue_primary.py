"""
tests/test_daemon_agentic_queue_primary.py -- step 5.3 gate and primary-mode safety
==================================================================================

Step 5.3 of ``.claude/shrink_step5_retire_main_py_implementation_plan.md``:
with ``DAEMON_AGENTIC_QUEUE_MODE=primary`` the daemon's ``AgenticQueueStep``
writes the REAL ``queue_sources/advisory.json`` + ``execution_queue.json``,
sends the cycle summary push, and runs the watch engine (all formerly done by
main.py's ``_run_cycle``).

The step 5.3 gate (``TestGatePrimaryEqualsMainPy``: the daemon in ``primary``
writes the same real files and pushes as ``main.main()`` in ``off``, and with
both writers in ``primary`` the side effects fire once) and main.py's
double-writer guard (``TestMainPyPrimarySkip``) passed before the 2026-10-12
cutover and were archived to ``legacy/tests/`` with ``main.py`` (step 5.5).
``TestPrimaryMatchesGolden`` now pins the daemon's primary output on the frozen
inputs (``tests/advisory_frozen_inputs.py``) against the committed goldens.

Time is frozen (``_NOW``) in compose, queue_builder and watch_engine so the
``generated_at``/``timestamp`` fields match.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List
from unittest import mock

import pandas as pd
import pytest

from tests.test_daemon_advisory_shadow_equivalence import (
    _make_daemon_ctx,
    _queue_ctx,
    _rec,
    _run_daemon_fetch,
    queue_env,  # noqa: F401  (fixture)
)
from tests.advisory_frozen_inputs import (
    _BARS,
    _NOW,
    _QUEUE_GOLDEN,
    _SOURCE_GOLDEN,
    _install_frozen_inputs,
)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D401 - datetime API
        return _NOW if tz is None else _NOW.astimezone(tz)


_WATCH_RULES = """
rules:
  - symbol: "*"
    alert_on: conviction_above
    threshold: 0.80
    priority: high
    label: "High Conviction Alert"
  - symbol: "*"
    alert_on: action_change
    priority: default
"""

# Prior watch state: INTC was a BUY and JNJ a HOLD last cycle, so both flip
# this cycle (INTC -> SELL, JNJ -> BUY) and the action_change rule fires.
_PRIOR_WATCH_STATE = {
    "INTC": {"action": "BUY", "conviction": 0.6, "alerted_conviction_above": {},
             "alerted_conviction_below": {}, "timestamp": "2026-07-16T15:00:00+00:00"},
    "JNJ": {"action": "HOLD", "conviction": 0.5, "alerted_conviction_above": {},
            "alerted_conviction_below": {}, "timestamp": "2026-07-16T15:00:00+00:00"},
}

_REAL_FILES = (
    "queue_sources/advisory.json",
    "execution_queue.json",
    "execution_queue_notified.json",
    "watch_state.json",
)


def _prepare_output_dir(path: Path, scan_source: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if Path(scan_source) != path:
        shutil.copy(scan_source / "scan_candidates.json", path / "scan_candidates.json")
    (path / "watch_state.json").write_text(json.dumps(_PRIOR_WATCH_STATE, indent=2),
                                           encoding="utf-8")
    return path


def _files(out: Path) -> dict:
    return {rel: ((out / rel).read_bytes() if (out / rel).exists() else b"<absent>")
            for rel in _REAL_FILES}


def _is_watch_push(push: tuple) -> bool:
    return not push[0].startswith(("InvestYo ✓", "InvestYo ⚠", "InvestYo — "))


def _normalise_pushes(pushes: List[tuple]) -> List[tuple]:
    """Summary pushes carry the run's wall-clock start and duration on their
    first line; everything else must match exactly."""
    out = []
    for title, message, priority in pushes:
        if title.startswith("InvestYo ✓ Refresh Complete"):
            message = "\n".join(message.splitlines()[1:])
        out.append((title, message, priority))
    return out


@pytest.fixture
def daemon_primary_env(tmp_path, monkeypatch):
    """The frozen advisory inputs plus frozen time, a real watch-rules file,
    recorded pushes and a prior watch state, for the daemon in ``primary``."""
    from pipeline import agentic_queue
    from settings import settings

    out = _install_frozen_inputs(tmp_path, monkeypatch)
    rules = tmp_path / "watch_rules.yaml"
    rules.write_text(_WATCH_RULES, encoding="utf-8")
    monkeypatch.setattr(settings, "WATCH_RULES_FILE", str(rules), raising=False)
    monkeypatch.setattr(settings, "NTFY_DASHBOARD_URL", None, raising=False)
    monkeypatch.setattr(settings, "ADVISORY_REUSE_PIPELINE_COMPUTE", False, raising=False)
    monkeypatch.setattr(settings, "LLM_COMMENTARY_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "primary", raising=False)
    for mod in ("execution.compose", "execution.queue_builder", "watch_engine"):
        monkeypatch.setattr(f"{mod}.datetime", _FrozenDatetime)
    _prepare_output_dir(out, out)

    pushes: List[tuple] = []

    def _notify(title, message, priority="default"):
        pushes.append((title, message, priority))
        return True

    monkeypatch.setattr("alerting.notify", _notify)
    agentic_queue.reset_clean_push_memory()
    yield out, pushes
    agentic_queue.reset_clean_push_memory()


def _frozen_macro():
    from pipeline.advisory_inputs import build_macro_dto, reset_macro_engine_cache

    try:
        return build_macro_dto()
    finally:
        reset_macro_engine_cache()


def _run_daemon_primary(monkeypatch, macro_dto) -> Any:
    from pipeline.production_steps import AdvisoryOverlayStep, AgenticQueueStep
    from pipeline.runner import AsyncPipelineRunner

    ctx = _make_daemon_ctx()
    _run_daemon_fetch(ctx, monkeypatch)
    ctx.macro_dto = macro_dto  # "same macro" (see the 5.2 gate)
    ctx.dashboard_df = pd.DataFrame({
        "Symbol": list(ctx.symbols),
        "Price": [float(_BARS[s]["Close"].iloc[-1]) for s in ctx.symbols],
    })
    asyncio.run(AsyncPipelineRunner([
        AdvisoryOverlayStep(),
        AgenticQueueStep(clock=lambda: _NOW),
    ]).run(ctx))
    return ctx


class TestPrimaryMatchesGolden:
    """The daemon in ``primary`` on the frozen inputs writes the REAL
    ``advisory.json`` / ``execution_queue.json`` equal to the committed goldens
    and sends the summary, watch and new-intent pushes. The side-by-side gate
    against ``main.main()`` (step 5.3) is archived in ``legacy/tests/``."""

    def test_primary_real_files_match_golden_with_side_effects(self, daemon_primary_env, monkeypatch):
        out, pushes = daemon_primary_env
        ctx = _run_daemon_primary(monkeypatch, _frozen_macro())

        assert not ctx.errors, ctx.errors
        files = _files(out)
        assert files["queue_sources/advisory.json"] == _SOURCE_GOLDEN.read_bytes()
        assert files["execution_queue.json"] == _QUEUE_GOLDEN.read_bytes()
        assert files["execution_queue_notified.json"] != b"<absent>"
        assert not (out / "shadow").exists(), "primary must not write the shadow queue"
        titles = [p[0] for p in pushes]
        assert titles[0] == "InvestYo ✓ Refresh Complete"
        assert any("Conviction" in t for t in titles), titles
        # INTC (BUY -> SELL) and JNJ (HOLD -> BUY) flip against the prior state.
        assert any("INTC" in t for t in titles) and any("JNJ" in t for t in titles), titles
        assert sum(t.startswith("InvestYo ✓") for t in titles) == 1
        assert sum(t.startswith("InvestYo — ") for t in titles) == 1

    def test_watch_state_covers_the_universe(self, daemon_primary_env, monkeypatch):
        out, pushes = daemon_primary_env
        ctx = _run_daemon_primary(monkeypatch, _frozen_macro())
        state = json.loads((out / "watch_state.json").read_text())
        assert sorted(state) == sorted(r.symbol for r in ctx.recommendations)
        assert state["INTC"]["action"] == "SELL" and state["JNJ"]["action"] == "BUY"
        assert len([p for p in pushes if _is_watch_push(p)]) >= 3


# ---------------------------------------------------------------------------
# AgenticQueueStep primary behaviour
# ---------------------------------------------------------------------------

@pytest.fixture
def penv(queue_env, monkeypatch):  # noqa: F811
    """queue_env with pushes recorded as (title, message, priority)."""
    from pipeline import agentic_queue

    out, _ = queue_env
    pushes: List[tuple] = []

    def _notify(title, message, priority="default"):
        pushes.append((title, message, priority))
        return True

    monkeypatch.setattr("alerting.notify", _notify)
    rules = out.parent / "watch_rules.yaml"
    rules.write_text(_WATCH_RULES, encoding="utf-8")
    monkeypatch.setattr("settings.settings.WATCH_RULES_FILE", str(rules))
    agentic_queue.reset_clean_push_memory()
    yield out, pushes
    agentic_queue.reset_clean_push_memory()


def _primary(step, ctx, out, execution_mode="review", token=None):
    return step.write_queue(ctx, mode="primary", execution_mode=execution_mode,
                            output_dir=out, owner_token=token)


class TestPrimaryWrites:
    def test_primary_writes_the_real_queue_with_side_effects(self, penv, monkeypatch):
        from pipeline import agentic_queue
        from pipeline.production_steps import AgenticQueueStep

        out, pushes = penv
        agentic_queue.reset_clean_push_memory()
        path = _primary(AgenticQueueStep(clock=lambda: _NOW), _queue_ctx(), out)
        assert path == out / "execution_queue.json"
        assert (out / "queue_sources" / "advisory.json").exists()
        assert (out / "execution_queue_notified.json").exists()
        assert (out / "watch_state.json").exists()
        assert not (out / "shadow").exists()
        titles = [p[0] for p in pushes]
        assert "InvestYo ✓ Refresh Complete" in titles
        assert any(t.startswith("InvestYo — ") for t in titles)
        agentic_queue.reset_clean_push_memory()

    def test_clean_summary_push_once_per_eastern_day(self, penv):
        from pipeline import agentic_queue
        from pipeline.production_steps import AgenticQueueStep

        out, pushes = penv
        agentic_queue.reset_clean_push_memory()
        _primary(AgenticQueueStep(clock=lambda: _NOW), _queue_ctx(), out)
        _primary(AgenticQueueStep(clock=lambda: _NOW), _queue_ctx(), out)
        assert [p[0] for p in pushes].count("InvestYo ✓ Refresh Complete") == 1
        agentic_queue.reset_clean_push_memory()

    def test_errors_push_every_cycle_and_no_queue_without_recommendations(self, penv):
        from pipeline.production_steps import AgenticQueueStep

        out, pushes = penv
        (out / "execution_queue.json").write_text("OLD QUEUE", encoding="utf-8")
        ctx = _queue_ctx(recs=[])
        ctx.errors = [{"symbol": "BAD", "stage": "advisory_evaluate"}]
        for _ in range(2):
            assert _primary(AgenticQueueStep(clock=lambda: _NOW), ctx, out) is None
        assert [p[0] for p in pushes] == ["InvestYo ⚠ Errors Detected"] * 2
        assert "BAD (advisory_evaluate)" in pushes[0][1]
        assert (out / "execution_queue.json").read_text(encoding="utf-8") == "OLD QUEUE"
        assert not (out / "queue_sources").exists()
        # The watch engine still ran (main.py runs it on every cycle).
        assert (out / "watch_state.json").exists()

    @pytest.mark.parametrize("kwargs, needle", [
        ({"synthetic": True}, "synthetic"),
        ({"ok": False}, "advisory overlay did not complete"),
        ({"stopped": True}, "cycle stopped"),
    ])
    def test_cycle_skip_reasons_write_and_push_nothing(self, penv, caplog, kwargs, needle):
        from pipeline.production_steps import AgenticQueueStep

        out, pushes = penv
        with caplog.at_level("INFO"):
            assert _primary(AgenticQueueStep(clock=lambda: _NOW), _queue_ctx(**kwargs), out) is None
        assert needle in caplog.text
        assert pushes == []
        assert list(out.iterdir()) == []

    def test_nothing_composable_leaves_the_old_queue(self, penv):
        """compose_and_emit's semantics are kept: HOLD-only recommendations
        rewrite advisory.json but leave the previous execution_queue.json."""
        from pipeline.production_steps import AgenticQueueStep

        out, _ = penv
        (out / "execution_queue.json").write_text("OLD QUEUE", encoding="utf-8")
        ctx = _queue_ctx(recs=[_rec("AAPL", action="HOLD", conviction=0.5)])
        assert _primary(AgenticQueueStep(clock=lambda: _NOW), ctx, out) is None
        assert (out / "execution_queue.json").read_text(encoding="utf-8") == "OLD QUEUE"
        source = json.loads((out / "queue_sources" / "advisory.json").read_text())
        assert source["targets"] == []

    def test_run_captures_mode_and_records_it(self, penv, monkeypatch):
        from pipeline.agentic_queue import MODE_USED_KEY, OWNER_TOKEN_KEY, is_current_queue_writer
        from pipeline.production_steps import AgenticQueueStep
        from settings import settings

        out, _ = penv
        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "primary", raising=False)
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
        ctx = _queue_ctx()
        AgenticQueueStep(clock=lambda: _NOW).run(ctx)
        assert ctx.context_extras[MODE_USED_KEY] == "primary"
        token = ctx.context_extras[OWNER_TOKEN_KEY]
        assert isinstance(token, int)
        assert not is_current_queue_writer(token), "the step releases its token when done"
        assert (out / "execution_queue.json").exists()


class TestRunOwnership:
    def test_closed_cycle_refuses_a_late_claim(self):
        from pipeline.agentic_queue import (
            CYCLE_CLOSED_KEY, claim_queue_writer, close_cycle_queue_writer,
        )

        extras: dict = {}
        close_cycle_queue_writer(extras)
        assert extras[CYCLE_CLOSED_KEY] is True
        assert claim_queue_writer(extras) is None

    def test_a_newer_claim_supersedes_an_older_token(self):
        from pipeline.agentic_queue import (
            claim_queue_writer, commit_guard, is_current_queue_writer, release_queue_writer,
        )

        old = claim_queue_writer()
        new = claim_queue_writer()
        assert not is_current_queue_writer(old) and is_current_queue_writer(new)
        with commit_guard(old) as allowed:
            assert allowed is False
        release_queue_writer(old)  # stale release must not end the newer owner
        assert is_current_queue_writer(new)
        release_queue_writer(new)

    def test_timed_out_step_cannot_write_after_the_cycle_ends(self, penv, monkeypatch):
        """The plan's cycle-time trap: a sync step that times out keeps running
        on its worker thread. Here the step blocks inside the queue build, the
        runner times out, the cycle closes (as main_orchestrator._main_body_impl
        does in its finally), and only THEN does the thread continue."""
        import execution.queue_builder as qb
        from pipeline.agentic_queue import CYCLE_CLOSED_KEY, close_cycle_queue_writer
        from pipeline.production_steps import AgenticQueueStep
        from pipeline.runner import AsyncPipelineRunner
        from settings import settings

        out, pushes = penv
        (out / "execution_queue.json").write_text("OLD QUEUE", encoding="utf-8")
        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "primary", raising=False)
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
        monkeypatch.setattr(settings, "PIPELINE_STEP_TIMEOUT_SECONDS", 0.3, raising=False)
        release = threading.Event()
        step_done = threading.Event()
        closed_when_resumed: list = []
        real_build = qb.build_execution_queue
        ctx = _queue_ctx()

        def _slow_build(*a, **kw):
            release.wait(10)
            closed_when_resumed.append(bool(ctx.context_extras.get(CYCLE_CLOSED_KEY)))
            return real_build(*a, **kw)

        class _Step(AgenticQueueStep):
            def run(self, c):
                try:
                    super().run(c)
                finally:
                    step_done.set()

        monkeypatch.setattr(qb, "build_execution_queue", _slow_build)

        async def _cycle():
            try:
                await AsyncPipelineRunner([_Step(clock=lambda: _NOW)]).run(ctx)
            finally:
                close_cycle_queue_writer(ctx.context_extras)

        # The runner times out at 0.3 s and the cycle closes; the blocked
        # thread resumes at 1.0 s. (asyncio.run joins the worker thread on
        # exit, so it returns only once the thread has finished.)
        timer = threading.Timer(1.0, release.set)
        timer.start()
        try:
            with pytest.raises((asyncio.TimeoutError, TimeoutError)):
                asyncio.run(_cycle())
        finally:
            timer.cancel()
            release.set()
        assert step_done.wait(10)
        assert closed_when_resumed == [True], "the thread must resume only after the cycle closed"
        assert (out / "execution_queue.json").read_text(encoding="utf-8") == "OLD QUEUE"
        assert not any(p[0].startswith("InvestYo — ") for p in pushes)
        assert not (out / "execution_queue.tmp").exists()

    def test_main_body_closes_the_cycle_even_when_the_runner_raises(self, monkeypatch):
        import main_orchestrator
        from pipeline.agentic_queue import CYCLE_CLOSED_KEY
        from pipeline.runner import AsyncPipelineRunner

        seen: dict = {}

        async def _boom(self, ctx, progress=None):
            seen["extras"] = ctx.context_extras
            raise TimeoutError("step timed out")

        monkeypatch.setattr(AsyncPipelineRunner, "run", _boom)
        with pytest.raises(TimeoutError):
            asyncio.run(main_orchestrator._main_body_impl(False, mode="data"))
        assert seen["extras"][CYCLE_CLOSED_KEY] is True

    def test_source_commit_refused_without_ownership(self, tmp_path):
        from execution.compose import write_advisory_source
        from pipeline.agentic_queue import claim_queue_writer, guard_for, release_queue_writer

        token = claim_queue_writer()
        release_queue_writer(token)
        assert write_advisory_source([_rec("AAPL")], output_dir=tmp_path, now=_NOW,
                                     commit_guard=guard_for(token)) is None
        assert not (tmp_path / "queue_sources" / "advisory.json").exists()
        assert not (tmp_path / "queue_sources" / "advisory.tmp").exists()


class TestMcpReportUnderPrimary:
    """Step 5.4a: the MCP ``generate_html_report`` tool no longer special-cases
    primary (and no longer shells out to main.py in any mode). It triggers a daemon
    cycle and reports ``daily_report_dashboard.html`` only when the run refreshed it.
    Full matrix: ``tests/test_investyo_mcp_server.py::TestGenerateHtmlReport``."""

    @staticmethod
    def _wire(monkeypatch, tmp_path, mode, status):
        import subprocess

        import investyo_mcp_server as srv
        from settings import settings
        from shared import daemon_client as dc

        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", mode, raising=False)
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
        ran = mock.MagicMock()
        monkeypatch.setattr(subprocess, "run", ran)
        monkeypatch.setattr(srv, "_cycle_sleep", lambda *_: None)
        triggered = mock.MagicMock(
            return_value=dc.TriggerResponse(ok=True, run_id="run-1", state="queued", error=None)
        )
        monkeypatch.setattr(dc, "trigger_run", triggered)
        monkeypatch.setattr(dc, "get_run_status", lambda run_id, *a, **k: status)
        return srv, ran, triggered

    @pytest.mark.parametrize("mode", ["off", "shadow", "primary"])
    def test_generate_html_report_triggers_a_cycle_in_every_mode(self, monkeypatch, tmp_path, mode):
        import os

        started = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
        status = {"run_id": "run-1", "state": "succeeded", "mode": "full", "error": None,
                  "reason": "manual", "started_at": started.isoformat()}
        srv, ran, triggered = self._wire(monkeypatch, tmp_path, mode, status)
        # A stale legacy report must never count; the fresh dashboard does.
        (tmp_path / "daily_report.html").write_text("<html>stale</html>", encoding="utf-8")
        dash = tmp_path / "daily_report_dashboard.html"
        dash.write_text("<html>fresh</html>", encoding="utf-8")
        os.utime(dash, (started.timestamp() + 60, started.timestamp() + 60))

        text = srv.generate_html_report("p")

        ran.assert_not_called()  # main.py is never run
        triggered.assert_called_once()
        assert "report was refreshed" in text and "daily_report_dashboard.html" in text
        assert "retired" not in text
        # A synthetic-data cycle still renders the report and is still "succeeded"
        # (StateSnapshotStep ignores the marker), so the tool must say it cannot tell.
        assert "Synthetic (MockDataEngine) data detected" in text

    def test_stale_dashboard_after_a_green_cycle_is_not_reported_as_fresh(self, monkeypatch, tmp_path):
        import os

        # In primary a cycle that skips the snapshot step (kill-switch sentinel or
        # other stopped cycle, interval cycle skipped on fresh data) leaves
        # yesterday's dashboard on disk even though the run is "succeeded".
        started = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
        status = {"run_id": "run-1", "state": "succeeded", "mode": "full", "error": None,
                  "reason": "manual", "started_at": started.isoformat()}
        srv, ran, _ = self._wire(monkeypatch, tmp_path, "primary", status)
        dash = tmp_path / "daily_report_dashboard.html"
        dash.write_text("<html>yesterday</html>", encoding="utf-8")
        os.utime(dash, (started.timestamp() - 86400, started.timestamp() - 86400))

        text = srv.generate_html_report("p")

        ran.assert_not_called()
        assert "report not refreshed" in text
        assert "report was refreshed" not in text
        assert "synthetic" not in text.lower()  # synthetic cycles DO render the report


# ---------------------------------------------------------------------------
# State snapshot: the two advisory-only fields arrive with primary
# ---------------------------------------------------------------------------

class TestStateSnapshotStepPassesRecommendationsOnlyInPrimary:
    @pytest.mark.parametrize("mode_used, expect", [("primary", True), ("shadow", False),
                                                   ("off", False), (None, False)])
    def test_kwarg(self, monkeypatch, tmp_path, mode_used, expect):
        import main_orchestrator
        from pipeline.agentic_queue import MODE_USED_KEY
        from pipeline.production_steps import StateSnapshotStep
        from settings import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
        seen: dict = {}
        monkeypatch.setattr(main_orchestrator, "_write_state_snapshot",
                            lambda *a, **kw: seen.update(kw))
        monkeypatch.setattr(main_orchestrator, "generate_html_report", lambda *a, **kw: None)
        ctx = _make_daemon_ctx()
        ctx.dashboard_df = pd.DataFrame({
            "Symbol": ["AAPL"], "Price": [1.0], "Action Signal": ["BUY"], "buyRange": [""],
            "sellRange": [""], "Kelly Target": [0.0], "GARCH_Vol": [0.2],
        })
        ctx.recommendations = [_rec("AAPL")]
        if mode_used is not None:
            ctx.context_extras[MODE_USED_KEY] = mode_used
        StateSnapshotStep().run(ctx)
        assert ("recommendations" in seen) is expect
        if expect:
            assert [r.symbol for r in seen["recommendations"]] == ["AAPL"]
