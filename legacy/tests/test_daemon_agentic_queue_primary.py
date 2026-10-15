"""
legacy/tests/test_daemon_agentic_queue_primary.py -- step 5.3 gate (main.py side)
=================================================================================

Archived in shrink step 5.5 with ``main.py``: the parts of
``tests/test_daemon_agentic_queue_primary.py`` that drove the real
``main.main()`` cycle.

* ``TestGatePrimaryEqualsMainPy`` -- the step 5.3 gate: the daemon in
  ``primary`` writes the same real queue/watch files and sends the same pushes
  as main.py in ``off``, and with both writers in ``primary`` the side effects
  fire once. It passed before the 2026-10-12 cutover.
* ``TestMainPyPrimarySkip`` -- main.py's ``_run_cycle`` double-writer guard.

The active file keeps the daemon-only primary tests and pins the daemon's
primary output against the committed goldens (``TestPrimaryMatchesGolden``).
These keep their original imports: restore ``main.py`` and
``tests/test_run_once_advisory_golden.py`` (both under ``legacy/``) to run them.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import List

import pytest

from tests.test_daemon_advisory_shadow_equivalence import _rec
from tests.test_daemon_agentic_queue_primary import (
    _WATCH_RULES,
    _FrozenDatetime,
    _files,
    _is_watch_push,
    _normalise_pushes,
    _prepare_output_dir,
    _run_daemon_primary,
)
from tests.test_run_once_advisory_golden import (
    _QUEUE_GOLDEN,
    _SOURCE_GOLDEN,
    _install_frozen_inputs,
    _recommendation_bytes,
)


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
