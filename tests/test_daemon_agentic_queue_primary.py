"""
tests/test_daemon_agentic_queue_primary.py -- step 5.3 gate and primary-mode safety
==================================================================================

Step 5.3 of ``.claude/shrink_step5_retire_main_py_implementation_plan.md``:
with ``DAEMON_AGENTIC_QUEUE_MODE=primary`` the daemon's ``AgenticQueueStep``
writes the REAL ``queue_sources/advisory.json`` + ``execution_queue.json``,
sends main.py's summary push, and runs main.py's watch engine; main.py's
``_run_cycle`` skips all of that.

The gate (``TestGatePrimaryEqualsMainPy``) reuses 5.2's frozen-input harness
(``tests/test_run_once_advisory_golden.py`` + the daemon-side helpers in
``tests/test_daemon_advisory_shadow_equivalence.py``) and drives the REAL
``main.main()`` single-run cycle, not a copy of its queue block:

1. ``main.main()`` with the mode ``off`` into ``OUTPUT_DIR`` A: the pre-5.3
   writer, with its pushes recorded;
2. the daemon's ``AsyncDataFetchStep`` -> ``AdvisoryOverlayStep`` ->
   ``AgenticQueueStep`` with the mode ``primary`` into ``OUTPUT_DIR`` B;
3. B's ``advisory.json``, ``execution_queue.json``,
   ``execution_queue_notified.json`` and ``watch_state.json`` must equal A's
   byte for byte, and the pushes (summary, watch alerts, new-intent) must be
   the same;
4. then both writers run with the mode ``primary`` against one fresh
   ``OUTPUT_DIR`` C: main.py writes nothing and pushes nothing, the daemon
   writes everything once, and the pushes equal step 1's -- so the side
   effects fire once, not twice.

Time is frozen (``_NOW``) in compose, queue_builder and watch_engine so the
``generated_at``/``timestamp`` fields match.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import threading
import time
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
from tests.test_run_once_advisory_golden import (
    _BARS,
    _NOW,
    _QUEUE_GOLDEN,
    _SOURCE_GOLDEN,
    _install_frozen_inputs,
    _recommendation_bytes,
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
def primary_env(tmp_path, monkeypatch):
    """5.2's frozen inputs plus frozen time, a real watch-rules file, recorded
    pushes and a main.py whose run result is captured."""
    import main
    from pipeline import agentic_queue
    from settings import settings

    out_a = _install_frozen_inputs(tmp_path, monkeypatch)
    rules = tmp_path / "watch_rules.yaml"
    rules.write_text(_WATCH_RULES, encoding="utf-8")
    monkeypatch.setattr(settings, "WATCH_RULES_FILE", str(rules), raising=False)
    monkeypatch.setattr(settings, "NTFY_DASHBOARD_URL", None, raising=False)
    monkeypatch.setattr(settings, "ADVISORY_REUSE_PIPELINE_COMPUTE", False, raising=False)
    monkeypatch.setattr(settings, "LLM_COMMENTARY_ENABLED", False, raising=False)
    for mod in ("execution.compose", "execution.queue_builder", "watch_engine"):
        monkeypatch.setattr(f"{mod}.datetime", _FrozenDatetime)
    _prepare_output_dir(out_a, out_a)

    pushes: List[tuple] = []

    def _notify(title, message, priority="default"):
        pushes.append((title, message, priority))
        return True

    monkeypatch.setattr("alerting.notify", _notify)
    monkeypatch.setattr(main, "notify", _notify)  # main.py binds it at import
    captured: dict = {"html_report_calls": 0}
    real_run_once = main.run_once

    def _run_once(force_account=False):
        try:
            captured["result"] = real_run_once(force_account=force_account)
        finally:
            main._reset_macro_engine_cache()
        return captured["result"]

    def _html_report(result, macro_dto=None):
        captured["html_report_calls"] += 1

    monkeypatch.setattr(main, "run_once", _run_once)
    monkeypatch.setattr(main, "_write_html_report", _html_report)
    monkeypatch.setattr(main, "setup_logging", lambda: None)
    monkeypatch.setattr(main, "_load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["main.py"])
    agentic_queue.reset_clean_push_memory()
    yield out_a, pushes, captured
    agentic_queue.reset_clean_push_memory()


def _run_main_main() -> None:
    import main

    with pytest.raises(SystemExit):
        main.main()


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


class TestGatePrimaryEqualsMainPy:
    def test_primary_real_files_and_side_effects_equal_main_py(self, tmp_path, monkeypatch, primary_env):
        from pipeline import agentic_queue
        from settings import settings

        out_a, pushes, captured = primary_env

        # 1. main.py, mode off (today's writer).
        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "off", raising=False)
        _run_main_main()
        main_result = captured["result"]
        assert not main_result.errors
        assert captured["html_report_calls"] == 1
        main_files = _files(out_a)
        main_pushes = list(pushes)
        # Fixture sanity: every side effect actually happened on main.py's side.
        assert main_files["queue_sources/advisory.json"] == _SOURCE_GOLDEN.read_bytes()
        assert main_files["execution_queue.json"] == _QUEUE_GOLDEN.read_bytes()
        assert main_files["execution_queue_notified.json"] != b"<absent>"
        titles = [p[0] for p in main_pushes]
        assert titles[0] == "InvestYo ✓ Refresh Complete"
        assert any("Conviction" in t for t in titles), titles
        assert any("INTC" in t for t in titles) and any("JNJ" in t for t in titles), titles
        assert any(t.startswith("InvestYo — ") for t in titles), titles

        # 2. the daemon, mode primary, into a separate OUTPUT_DIR B.
        out_b = _prepare_output_dir(tmp_path / "output_b", out_a)
        monkeypatch.setattr(settings, "OUTPUT_DIR", out_b, raising=False)
        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "primary", raising=False)
        agentic_queue.reset_clean_push_memory()
        pushes.clear()
        ctx = _run_daemon_primary(monkeypatch, main_result.macro_dto)

        # 3. same recommendations, same real files byte for byte, same pushes.
        assert _recommendation_bytes(ctx.recommendations) == \
            _recommendation_bytes(main_result.recommendations)
        assert _files(out_b) == main_files
        assert _normalise_pushes(pushes) == _normalise_pushes(main_pushes)
        assert not (out_b / "shadow").exists(), "primary must not write the shadow queue"

    def test_side_effects_fire_once_when_both_writers_run(self, tmp_path, monkeypatch, primary_env):
        from pipeline import agentic_queue
        from settings import settings

        out_a, pushes, captured = primary_env
        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "off", raising=False)
        _run_main_main()
        expected_files = _files(out_a)
        expected_pushes = _normalise_pushes(pushes)
        main_result = captured["result"]

        # Both writers, mode primary, ONE shared OUTPUT_DIR C.
        out_c = _prepare_output_dir(tmp_path / "output_c", out_a)
        monkeypatch.setattr(settings, "OUTPUT_DIR", out_c, raising=False)
        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "primary", raising=False)
        agentic_queue.reset_clean_push_memory()
        pushes.clear()
        before = _files(out_c)

        _run_main_main()
        assert pushes == [], "main.py must push nothing in primary"
        assert _files(out_c) == before, "main.py must write no queue/watch file in primary"
        assert captured["html_report_calls"] == 1, "no daily_report.html/snapshot in primary"

        _run_daemon_primary(monkeypatch, main_result.macro_dto)
        assert _files(out_c) == expected_files
        assert _normalise_pushes(pushes) == expected_pushes
        # Exactly one summary push and one new-intent push, not two of each.
        assert sum(t.startswith("InvestYo ✓") for t, _, _ in pushes) == 1
        assert sum(t.startswith("InvestYo — ") for t, _, _ in pushes) == 1

    def test_watch_state_diff_is_empty(self, tmp_path, monkeypatch, primary_env):
        """The plan's watch gate, stated on its own: for the same inputs the
        daemon's watch_state.json and alerts equal main.py's."""
        from pipeline import agentic_queue
        from settings import settings

        out_a, pushes, captured = primary_env
        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "off", raising=False)
        _run_main_main()
        main_state = json.loads((out_a / "watch_state.json").read_text())
        main_watch = [p for p in pushes if _is_watch_push(p)]

        out_b = _prepare_output_dir(tmp_path / "output_b", out_a)
        monkeypatch.setattr(settings, "OUTPUT_DIR", out_b, raising=False)
        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "primary", raising=False)
        agentic_queue.reset_clean_push_memory()
        pushes.clear()
        _run_daemon_primary(monkeypatch, captured["result"].macro_dto)
        daemon_state = json.loads((out_b / "watch_state.json").read_text())
        daemon_watch = [p for p in pushes if _is_watch_push(p)]

        assert daemon_state == main_state
        assert sorted(daemon_state) == sorted(r.symbol for r in captured["result"].recommendations)
        assert daemon_watch == main_watch and len(main_watch) >= 3


# ---------------------------------------------------------------------------
# AgenticQueueStep primary behaviour (no main.py run needed)
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


# ---------------------------------------------------------------------------
# main.py's double-writer guard and the retired report
# ---------------------------------------------------------------------------

class TestMainPyPrimarySkip:
    def _result(self):
        import main
        from data.robinhood_portfolio import AccountSnapshot

        now = datetime.now(timezone.utc)
        return main.RunResult(
            snapshot=AccountSnapshot(positions={}, buying_power=0.0, total_equity=1000.0,
                                     total_dividends=0.0, fetched_at=now),
            recommendations=[_rec("AAPL")], errors=[{"symbol": "X", "stage": "s"}],
            started_at=now, finished_at=now, duration_seconds=0.0, macro_dto=None,
        )

    @pytest.mark.parametrize("mode, skipped", [("primary", True), ("shadow", False), ("off", False)])
    def test_run_cycle_skips_queue_watch_push_and_report_only_in_primary(
        self, monkeypatch, tmp_path, caplog, mode, skipped,
    ):
        import main
        from settings import settings

        calls: List[str] = []
        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", mode, raising=False)
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
        monkeypatch.setattr(sys, "argv", ["main.py"])
        monkeypatch.setattr(main, "run_once", lambda force_account=False: self._result())
        monkeypatch.setattr(main, "_write_html_report", lambda *a, **k: calls.append("report"))
        monkeypatch.setattr(main, "notify", lambda *a, **k: calls.append("push"))
        monkeypatch.setattr(main, "setup_logging", lambda: None)
        monkeypatch.setattr(main, "_load_dotenv", lambda *a, **k: None)
        monkeypatch.setattr("watch_engine.evaluate_watch_rules",
                            lambda *a, **k: (calls.append("watch"), ([], {}))[1])
        monkeypatch.setattr("execution.compose.write_advisory_source",
                            lambda *a, **k: calls.append("source"))
        monkeypatch.setattr("execution.compose.compose_and_emit",
                            lambda *a, **k: calls.append("compose"))
        with caplog.at_level("WARNING"), pytest.raises(SystemExit):
            main.main()
        if skipped:
            assert calls == []
            assert "the orchestrator daemon owns the execution queue" in caplog.text
        else:
            assert calls == ["push", "watch", "source", "compose", "report"]


class TestMcpReportUnderPrimary:
    def test_generate_html_report_does_not_run_main_py_in_primary(self, monkeypatch, tmp_path):
        import subprocess

        import investyo_mcp_server as srv
        from settings import settings

        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "primary", raising=False)
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
        (tmp_path / "daily_report.html").write_text("<html>stale</html>", encoding="utf-8")
        ran = mock.MagicMock()
        monkeypatch.setattr(subprocess, "run", ran)
        text = srv.generate_html_report("p")
        ran.assert_not_called()
        assert "retired" in text and "daily_report_dashboard.html" in text
        assert "HTML report generated" not in text


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
