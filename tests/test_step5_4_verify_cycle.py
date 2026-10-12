"""
tests/test_step5_4_verify_cycle.py
==================================
Step 5.4b: ``make verify`` / ``verify.command`` step 3 no longer runs
``import main; main.run_once()``; it runs ``scripts/step5_4_verify_cycle.py``,
one dry-run ``main_orchestrator.main()`` cycle with the agentic queue mode forced
``off`` and every outbound alert suppressed.

These tests drive the helper's ``main(argv)`` in-process against a FAKE
``main_orchestrator`` module (no pipeline, no network, no subprocess), so they
run offline under ``pytest-xdist --dist loadgroup``. Every global they touch
(``sys.modules``, ``sys.meta_path``, ``settings.settings`` attributes,
``alerting.setup_logging``, ``observability.alerts.send_alert``) goes through
``monkeypatch`` and is restored; the helper itself restores what it overrides.

Coverage
--------
TestSafetyOverrides      queue mode forced off (and restored), dry-run passed,
                         daily summary + alerts suppressed, fail closed when an
                         override cannot be applied.
TestLoggingOrder         ``setup_logging`` runs before ``main_orchestrator`` is
                         imported (runtime spy + AST pin).
TestExitCodes            non-zero on PipelineFatalError / any exception /
                         SystemExit; secrets masked; non-zero with an explicit
                         message when the snapshot was not refreshed; zero +
                         briefing on a good run.
TestCheckSnapshot        the freshness check in isolation.
TestSyntheticDataWarning MockDataEngine cycles are called out.
TestMakefileAndCommand   text pins on the Makefile recipe and verify.command.
TestCodeContracts        AST pins on the real orchestrator code the helper's
                         overrides rely on (no import of the pipeline).
"""

from __future__ import annotations

import ast
import importlib.abc
import importlib.machinery
import json
import logging
import os
import re
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import alerting
import settings as settings_module
from scripts import step5_4_verify_cycle as vc

REPO_ROOT = Path(__file__).resolve().parent.parent
HELPER = REPO_ROOT / "scripts" / "step5_4_verify_cycle.py"
MAKEFILE = REPO_ROOT / "Makefile"
VERIFY_COMMAND = REPO_ROOT / "verify.command"


# ---------------------------------------------------------------------------
# Fakes and fixtures
# ---------------------------------------------------------------------------

class PipelineFatalError(RuntimeError):
    """Stands in for ``main_orchestrator.PipelineFatalError`` (same name)."""


class MockDataEngine:
    """Stands in for ``data_engine.MockDataEngine`` (detected by class name)."""


def _ctx(**overrides):
    base = {"stopped": False, "stop_reason": None, "context_extras": {}, "market": object()}
    base.update(overrides)
    return SimpleNamespace(**base)


def _write_snapshot(out_dir: Path, *, timestamp: str | None = None, n_signals: int = 2) -> Path:
    path = out_dir / "state_snapshot.json"
    path.write_text(
        json.dumps({
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "signals": [{"symbol": f"S{i}"} for i in range(n_signals)],
        }),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def out_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "output"
    d.mkdir()
    monkeypatch.setattr(settings_module.settings, "OUTPUT_DIR", d)
    return d


@pytest.fixture
def spies(monkeypatch):
    """Replace the REAL outbound paths with recorders that raise if reached."""
    import observability.alerts as alerts_mod
    from scripts import daily_briefing

    rec = SimpleNamespace(alert_calls=[], summary_calls=[], briefing_dirs=[])

    def real_send_alert(*args, **kwargs):
        rec.alert_calls.append((args, kwargs))
        raise AssertionError("the real send_alert was reached during the verify cycle")

    def real_send_daily_summary(*args, **kwargs):
        rec.summary_calls.append((args, kwargs))
        raise AssertionError("the real send_daily_summary was reached during the verify cycle")

    def fake_generate_briefing(output_dir=None):
        rec.briefing_dirs.append(output_dir)
        return "BRIEFING-MARKER"

    monkeypatch.setattr(alerts_mod, "send_alert", real_send_alert)
    monkeypatch.setattr(alerts_mod, "send_daily_summary", real_send_daily_summary)
    monkeypatch.setattr(daily_briefing, "generate_briefing", fake_generate_briefing)
    rec.alerts_mod = alerts_mod
    rec.real_send_alert = real_send_alert
    return rec


@pytest.fixture
def install(monkeypatch, out_dir, spies):
    """``install(cycle)`` puts a fake ``main_orchestrator`` in ``sys.modules``.

    ``cycle(mod, rec)`` is the body of the fake ``main()``. The fake's
    ``_dispatch_daily_summary`` mimics the real one (it calls the alerts
    module's ``send_daily_summary``); the cycle looks it up on the module at
    call time, as ``_main_body_impl`` does with its global name.
    """
    monkeypatch.setattr(alerting, "setup_logging", lambda *a, **k: None)

    def _install(cycle, *, with_dispatch: bool = True, with_fetch: bool = True):
        rec = SimpleNamespace(
            calls=0, dry_run=None, strict=None, mode_during=None, original_dispatch=None,
            original_fetch=None, original_heartbeat=None, fetch_calls=[],
            settings_during=None,
        )
        mod = types.ModuleType("main_orchestrator")
        mod.PipelineFatalError = PipelineFatalError

        def original_dispatch(ctx):
            from observability.alerts import send_daily_summary

            send_daily_summary({}, ["a warning"])

        rec.original_dispatch = original_dispatch
        if with_dispatch:
            mod._dispatch_daily_summary = original_dispatch

        def original_fetch(*args, **kwargs):
            rec.fetch_calls.append(kwargs)
            return SimpleNamespace(positions=[])

        async def original_heartbeat(*args, **kwargs):
            raise AssertionError("the real heartbeat task ran during the verify cycle")

        rec.original_fetch = original_fetch
        rec.original_heartbeat = original_heartbeat
        if with_fetch:
            mod.fetch_account_snapshot = original_fetch
        mod._heartbeat = original_heartbeat

        async def fake_main(dry_run=False, strict=False):
            rec.calls += 1
            rec.dry_run = dry_run
            rec.strict = strict
            rec.mode_during = settings_module.settings.DAEMON_AGENTIC_QUEUE_MODE
            s = settings_module.settings
            rec.settings_during = {
                "SYMBOL_RATING_ENABLED": s.SYMBOL_RATING_ENABLED,
                "SIZING_CAP_AUDIT_ENABLED": s.SIZING_CAP_AUDIT_ENABLED,
            }
            await cycle(mod, rec)

        mod.main = fake_main
        monkeypatch.setitem(sys.modules, "main_orchestrator", mod)
        return mod, rec

    return _install


async def good_cycle(mod, rec):
    """A completed cycle: raises an alert mid-way, writes the snapshot, ends with the hook."""
    from observability.alerts import send_alert  # lazy import, like execution/risk_gate.py

    send_alert("CRITICAL", "pre-trade gate blocked something")
    _write_snapshot(settings_module.settings.OUTPUT_DIR)
    mod._dispatch_daily_summary(_ctx())


# ---------------------------------------------------------------------------
# (a)(b)(c) Safety overrides
# ---------------------------------------------------------------------------

class TestSafetyOverrides:
    def test_queue_mode_forced_off_during_cycle_and_restored(self, monkeypatch, install, capsys):
        monkeypatch.setattr(settings_module.settings, "DAEMON_AGENTIC_QUEUE_MODE", "primary")
        _, rec = install(good_cycle)

        code = vc.main([])

        assert code == 0
        assert rec.mode_during == "off"
        # Not leaked: the process-wide singleton is back to what it was.
        assert settings_module.settings.DAEMON_AGENTIC_QUEUE_MODE == "primary"

    def test_queue_mode_restored_when_the_cycle_raises(self, monkeypatch, install, capsys):
        monkeypatch.setattr(settings_module.settings, "DAEMON_AGENTIC_QUEUE_MODE", "primary")

        async def boom(mod, rec):
            raise RuntimeError("cycle blew up")

        _, rec = install(boom)
        assert vc.main([]) == 1
        assert rec.mode_during == "off"
        assert settings_module.settings.DAEMON_AGENTIC_QUEUE_MODE == "primary"

    def test_cycle_runs_dry_run_and_non_strict(self, install, capsys):
        _, rec = install(good_cycle)
        vc.main([])
        assert rec.calls == 1
        assert rec.dry_run is True
        assert rec.strict is False

    def test_daily_summary_and_alerts_are_suppressed_and_hooks_restored(self, install, spies, capsys):
        mod, rec = install(good_cycle)

        code = vc.main([])
        out = capsys.readouterr().out

        assert code == 0
        # Neither the real summary nor the real alert path ran (both would have raised).
        assert spies.summary_calls == []
        assert spies.alert_calls == []
        assert "daily summary suppressed, 1 alert(s) suppressed" in out
        # Everything the helper replaced is back.
        assert mod._dispatch_daily_summary is rec.original_dispatch
        assert spies.alerts_mod.send_alert is spies.real_send_alert

    def test_durable_decision_state_recording_is_off_during_the_cycle_and_restored(self, monkeypatch, install, capsys):
        # The daemon records ONE symbol-rating cycle per trading day (possibly from mock data)
        # and a cap-audit row set per cycle; a verify run must do neither.
        monkeypatch.setattr(settings_module.settings, "SYMBOL_RATING_ENABLED", True)
        monkeypatch.setattr(settings_module.settings, "SIZING_CAP_AUDIT_ENABLED", True)
        _, rec = install(good_cycle)

        assert vc.main([]) == 0
        assert rec.settings_during == {"SYMBOL_RATING_ENABLED": False, "SIZING_CAP_AUDIT_ENABLED": False}
        assert settings_module.settings.SYMBOL_RATING_ENABLED is True
        assert settings_module.settings.SIZING_CAP_AUDIT_ENABLED is True

    def test_account_snapshot_fetch_never_starts_a_live_login(self, install, capsys):
        # AsyncDataFetchStep calls main_orchestrator.fetch_account_snapshot() with defaults; with
        # ROBINHOOD_AUTO_REFRESH_ENABLED on and a stale cache that is a device-approval login.
        async def cycle(mod, rec):
            mod.fetch_account_snapshot()  # exactly how the step calls it
            mod.fetch_account_snapshot(max_age_hours=20.0, allow_live_fetch=True)  # even if asked to
            await good_cycle(mod, rec)

        mod, rec = install(cycle)

        assert vc.main([]) == 0
        assert rec.fetch_calls == [{"allow_live_fetch": False}, {"max_age_hours": 20.0, "allow_live_fetch": False}]
        assert mod.fetch_account_snapshot is rec.original_fetch  # restored

    def test_heartbeat_task_is_a_noop_during_the_cycle_and_restored(self, install, capsys):
        # The daemon never refreshes heartbeat.txt: a file left by this run would go stale and the
        # heartbeat panel would show "Stale" instead of "No heartbeat".
        async def cycle(mod, rec):
            await mod._heartbeat("ignored", interval=60)  # the real one raises in this fake
            await good_cycle(mod, rec)

        mod, rec = install(cycle)

        assert vc.main([]) == 0
        assert mod._heartbeat is rec.original_heartbeat

    def test_cycle_is_not_run_when_the_account_fetch_cannot_be_wrapped(self, monkeypatch, install, capsys):
        monkeypatch.setattr(settings_module.settings, "DAEMON_AGENTIC_QUEUE_MODE", "primary")
        _, rec = install(good_cycle, with_fetch=False)

        code = vc.main([])

        assert code == 1
        assert rec.calls == 0, "an unprotected cycle (live login possible) must never run"
        assert settings_module.settings.DAEMON_AGENTIC_QUEUE_MODE == "primary"

    def test_cycle_is_not_run_when_the_summary_hook_cannot_be_replaced(self, monkeypatch, install, capsys):
        monkeypatch.setattr(settings_module.settings, "DAEMON_AGENTIC_QUEUE_MODE", "primary")
        _, rec = install(good_cycle, with_dispatch=False)

        code = vc.main([])
        err = capsys.readouterr().err

        assert code == 1
        assert rec.calls == 0, "an unprotected cycle must never run"
        assert "VERIFY CYCLE FAILED" in err
        assert settings_module.settings.DAEMON_AGENTIC_QUEUE_MODE == "primary"


# ---------------------------------------------------------------------------
# (d) setup_logging before main_orchestrator is imported
# ---------------------------------------------------------------------------

class TestLoggingOrder:
    def test_setup_logging_runs_before_main_orchestrator_is_imported(self, monkeypatch, out_dir, spies):
        events: list[str] = []
        fake = types.ModuleType("main_orchestrator")
        fake._dispatch_daily_summary = lambda ctx: None
        fake.fetch_account_snapshot = lambda *a, **k: None

        async def fake_main(dry_run=False, strict=False):
            events.append("main")
            _write_snapshot(settings_module.settings.OUTPUT_DIR)
            fake._dispatch_daily_summary(_ctx())

        fake.main = fake_main

        class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
            def find_spec(self, name, path, target=None):
                if name == "main_orchestrator":
                    events.append("import main_orchestrator")
                    return importlib.machinery.ModuleSpec(name, self)
                return None

            def create_module(self, spec):
                return fake

            def exec_module(self, module):
                return None

        # setitem records the original (or its absence) so teardown restores
        # sys.modules exactly; then drop it so the import really happens.
        monkeypatch.setitem(sys.modules, "main_orchestrator", fake)
        del sys.modules["main_orchestrator"]
        monkeypatch.setattr(sys, "meta_path", [_Finder(), *sys.meta_path])
        monkeypatch.setattr(alerting, "setup_logging", lambda *a, **k: events.append("setup_logging"))

        assert vc.main([]) == 0
        assert events == ["setup_logging", "import main_orchestrator", "main"]

    def test_helper_has_no_module_scope_import_of_main_orchestrator(self):
        tree = ast.parse(HELPER.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                assert all(a.name.split(".")[0] != "main_orchestrator" for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "main_orchestrator"

    def test_load_orchestrator_calls_setup_logging_before_importing(self):
        tree = ast.parse(HELPER.read_text(encoding="utf-8"))
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_load_orchestrator"
        )
        setup_lines = [
            n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "setup_logging"
        ]
        import_lines = [
            n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "import_module"
            and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "main_orchestrator"
        ]
        assert setup_lines and import_lines
        assert min(setup_lines) < min(import_lines)


# ---------------------------------------------------------------------------
# (e)(f)(g) Exit codes and honesty
# ---------------------------------------------------------------------------

class TestExitCodes:
    @pytest.mark.parametrize(
        "exc, label",
        [
            (PipelineFatalError("data gathering crashed"), "PipelineFatalError"),
            (RuntimeError("boom"), "RuntimeError"),
            (SystemExit(0), "SystemExit"),
        ],
    )
    def test_cycle_failure_exits_1_and_is_not_reported_as_ok(self, install, spies, capsys, exc, label):
        async def failing(mod, rec):
            raise exc

        install(failing)
        code = vc.main([])
        captured = capsys.readouterr()

        assert code == vc.EXIT_CYCLE_FAILED == 1
        assert "VERIFY CYCLE FAILED" in captured.err
        assert label in captured.err
        assert "VERIFY CYCLE OK" not in captured.out
        assert spies.briefing_dirs == [], "no briefing for a cycle that failed"

    def test_failure_output_masks_secrets(self, monkeypatch, install, capsys):
        monkeypatch.setattr(settings_module.settings, "FRED_API_KEY", "fred-secret-value-123")

        async def leaky(mod, rec):
            raise RuntimeError(
                "GET https://api.example/x?apikey=abcdef123456&y=1 failed; "
                "key=fred-secret-value-123; Authorization: Bearer abcdefghijklmnop"
            )

        install(leaky)
        assert vc.main([]) == 1
        text = capsys.readouterr().err
        for secret in ("fred-secret-value-123", "abcdef123456", "abcdefghijklmnop"):
            assert secret not in text
        assert "***" in text

    def test_no_snapshot_written_exits_3_with_explicit_message(self, install, spies, out_dir, capsys):
        async def writes_nothing(mod, rec):
            mod._dispatch_daily_summary(_ctx())

        install(writes_nothing)
        code = vc.main([])
        captured = capsys.readouterr()

        assert code == vc.EXIT_SNAPSHOT_NOT_REFRESHED == 3
        assert "cycle did not refresh the snapshot" in captured.err
        assert "state_snapshot.json" in captured.err
        assert "VERIFY CYCLE OK" not in captured.out
        assert spies.briefing_dirs == [], "a briefing of a stale snapshot would look current"
        # The disclosure must not claim a snapshot rewrite that did not happen.
        assert "This run stopped before it wrote the snapshot" in captured.out

    def test_stale_snapshot_from_an_earlier_run_is_not_accepted(self, install, out_dir, capsys):
        path = _write_snapshot(out_dir, timestamp=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat())
        old = path.stat().st_mtime - 3 * 3600
        os.utime(path, (old, old))

        async def writes_nothing(mod, rec):
            mod._dispatch_daily_summary(_ctx())

        install(writes_nothing)
        code = vc.main([])
        err = capsys.readouterr().err

        assert code == 3
        assert "cycle did not refresh the snapshot" in err
        assert "stale" in err

    def test_kill_switch_stop_is_named_as_the_reason(self, install, capsys):
        async def kill_switch_cycle(mod, rec):
            mod._dispatch_daily_summary(_ctx(stopped=True, stop_reason="kill_switch"))

        install(kill_switch_cycle)
        code = vc.main([])
        err = capsys.readouterr().err

        assert code == 3
        assert "cycle did not refresh the snapshot" in err
        assert "kill_switch" in err
        assert "execution.kill_switch --deactivate" in err

    def test_good_run_exits_0_prints_outcome_briefing_and_disclosure(self, install, spies, out_dir, capsys):
        install(good_cycle)

        code = vc.main([])
        out = capsys.readouterr().out

        assert code == vc.EXIT_OK == 0
        assert "VERIFY CYCLE OK" in out
        assert "state_snapshot.json refreshed" in out
        assert "(2 signals)" in out
        assert "BRIEFING-MARKER" in out
        # The briefing must read the active OUTPUT_DIR, not its <repo>/output default.
        assert spies.briefing_dirs == [out_dir]
        assert "DAEMON_AGENTIC_QUEUE_MODE=off" in out
        assert "no paper orders" in out
        assert "WARNING" not in out  # real market object, not synthetic

    def test_a_broken_briefing_does_not_fail_a_good_cycle(self, monkeypatch, install, capsys):
        from scripts import daily_briefing

        def broken(output_dir=None):
            raise ValueError("briefing exploded")

        install(good_cycle)
        monkeypatch.setattr(daily_briefing, "generate_briefing", broken)
        code = vc.main([])
        out = capsys.readouterr().out
        assert code == 0
        assert "daily briefing unavailable: ValueError" in out


# ---------------------------------------------------------------------------
# check_snapshot in isolation
# ---------------------------------------------------------------------------

class TestCheckSnapshot:
    def _start(self) -> float:
        import time

        return time.time()

    def test_fresh_snapshot_ok(self, tmp_path):
        start = self._start()
        _write_snapshot(tmp_path, n_signals=3)
        res = vc.check_snapshot(tmp_path, start)
        assert res.ok and res.reason is None and res.n_signals == 3 and res.timestamp

    def test_missing_snapshot(self, tmp_path):
        res = vc.check_snapshot(tmp_path, self._start())
        assert not res.ok and "does not exist" in res.reason

    def test_old_mtime_is_stale(self, tmp_path):
        start = self._start()
        path = _write_snapshot(tmp_path)
        os.utime(path, (start - 600, start - 600))
        res = vc.check_snapshot(tmp_path, start)
        assert not res.ok and "stale" in res.reason

    def test_fresh_mtime_but_old_timestamp_is_stale(self, tmp_path):
        start = self._start()
        _write_snapshot(tmp_path, timestamp=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat())
        res = vc.check_snapshot(tmp_path, start)
        assert not res.ok and "timestamp" in res.reason and "before this run started" in res.reason

    def test_naive_timestamp_is_read_as_utc(self, tmp_path):
        start = self._start()
        naive_utc_now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        _write_snapshot(tmp_path, timestamp=naive_utc_now)
        assert vc.check_snapshot(tmp_path, start).ok

    @pytest.mark.parametrize("body", ["{not json", json.dumps({"signals": []}), json.dumps({"timestamp": "nope"})])
    def test_unreadable_or_timestampless_snapshot_fails_closed(self, tmp_path, body):
        start = self._start()
        (tmp_path / "state_snapshot.json").write_text(body, encoding="utf-8")
        res = vc.check_snapshot(tmp_path, start)
        assert not res.ok and "unreadable or has no valid timestamp" in res.reason


# ---------------------------------------------------------------------------
# Synthetic-data call-out
# ---------------------------------------------------------------------------

class TestUnverifiedCycles:
    """A cycle that ran but proves nothing about live data must not exit 0 / "all steps passed"."""

    @pytest.mark.parametrize(
        "ctx",
        [
            _ctx(context_extras={"data_is_synthetic": True}),
            _ctx(market=MockDataEngine()),
        ],
        ids=["flag", "mock-engine-class"],
    )
    def test_synthetic_data_cycle_exits_4_and_says_it_overwrote_the_snapshot(self, install, capsys, ctx):
        async def mock_cycle(mod, rec):
            _write_snapshot(settings_module.settings.OUTPUT_DIR)
            mod._dispatch_daily_summary(ctx)

        install(mock_cycle)
        code = vc.main([])
        captured = capsys.readouterr()
        assert code == vc.EXIT_UNVERIFIED == 4
        assert "VERIFY CYCLE INCOMPLETE" in captured.err and "SYNTHETIC MockDataEngine" in captured.err
        assert "VERIFY CYCLE OK" not in captured.out
        # The honest consequence (StateSnapshotStep ignores the flag): live files now hold mock data.
        assert "OVERWRITTEN state_snapshot.json" in captured.out

    @pytest.mark.parametrize("n_signals", [0])
    def test_snapshot_with_no_signals_exits_4(self, install, capsys, n_signals):
        async def empty_cycle(mod, rec):
            _write_snapshot(settings_module.settings.OUTPUT_DIR, n_signals=n_signals)
            mod._dispatch_daily_summary(_ctx())

        install(empty_cycle)
        code = vc.main([])
        captured = capsys.readouterr()
        assert code == 4
        assert "holds no signals" in captured.err
        assert "VERIFY CYCLE OK" not in captured.out

    def test_fresh_snapshot_but_end_of_cycle_hook_never_ran_exits_3(self, install, capsys):
        # e.g. the snapshot was written by a daemon cycle in flight, not by this run.
        async def foreign_snapshot_cycle(mod, rec):
            _write_snapshot(settings_module.settings.OUTPUT_DIR)  # never calls the end-of-cycle hook

        install(foreign_snapshot_cycle)
        code = vc.main([])
        captured = capsys.readouterr()
        assert code == vc.EXIT_SNAPSHOT_NOT_REFRESHED == 3
        assert "cannot be attributed to this run" in captured.err
        assert "VERIFY CYCLE OK" not in captured.out

    def test_helper_detects_the_flagless_mock_engine_case(self):
        # No FRED key => the step picks MockDataEngine up front WITHOUT the flag.
        assert vc._used_synthetic_data(_ctx(market=MockDataEngine())) is True
        assert vc._used_synthetic_data(_ctx()) is False
        assert vc._used_synthetic_data(None) is False


# ---------------------------------------------------------------------------
# scrub()
# ---------------------------------------------------------------------------

class TestScrub:
    def test_masks_url_credentials_and_bearer_tokens(self):
        text = vc.scrub("https://h/p?api_key=ABC123&token=XYZ789 and Bearer abcdefghijkl")
        assert "ABC123" not in text and "XYZ789" not in text and "abcdefghijkl" not in text
        assert "api_key=***" in text and "token=***" in text

    def test_leaves_ordinary_text_alone(self):
        assert vc.scrub("cycle stopped early (stop_reason=kill_switch)") == (
            "cycle stopped early (stop_reason=kill_switch)"
        )


# ---------------------------------------------------------------------------
# (h) Makefile and verify.command
# ---------------------------------------------------------------------------

def _logical_recipe(makefile_text: str, target: str) -> list[str]:
    """Recipe lines of ``target`` with backslash continuations joined."""
    lines = makefile_text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"{target}:"))
    body: list[str] = []
    for ln in lines[start + 1:]:
        if ln.startswith("\t"):
            body.append(ln.lstrip("\t"))
        elif ln.strip() == "":
            continue
        else:
            break
    joined: list[str] = []
    buf = ""
    for ln in body:
        if ln.rstrip().endswith("\\"):
            buf += ln.rstrip()[:-1] + " "
        else:
            joined.append((buf + ln).strip())
            buf = ""
    if buf:
        joined.append(buf.strip())
    return joined


class TestMakefileAndCommand:
    def test_live_run_recipe_invokes_the_helper_and_chains_with_and(self):
        text = MAKEFILE.read_text(encoding="utf-8")
        recipe = _logical_recipe(text, "_live_run")
        helper_cmds = [ln for ln in recipe if "step5_4_verify_cycle.py" in ln]
        assert len(helper_cmds) == 1
        cmd = helper_cmds[0]
        assert re.search(r"\$\(PYTHON\)\s+scripts/step5_4_verify_cycle\.py\s*&&", cmd)
        # "verify complete" is only reachable through that && chain.
        assert "verify complete" in cmd.split("&&", 1)[1]
        assert all("verify complete" not in ln for ln in recipe if ln is not cmd)

    def test_live_run_recipe_no_longer_uses_run_once_or_main_module(self):
        text = MAKEFILE.read_text(encoding="utf-8")
        recipe = "\n".join(_logical_recipe(text, "_live_run"))
        assert "run_once" not in recipe
        assert "import main" not in recipe
        assert "run_once" not in text

    def test_step_banner_and_header_describe_a_dry_run_cycle(self):
        text = MAKEFILE.read_text(encoding="utf-8")
        recipe = "\n".join(_logical_recipe(text, "_live_run"))
        assert "dry-run" in recipe and "no orders" in recipe
        assert "one live advisory cycle" not in text

    def test_phony_lists_the_helper_targets_and_verify_chain_is_intact(self):
        text = MAKEFILE.read_text(encoding="utf-8")
        phony = next(ln for ln in text.splitlines() if ln.startswith(".PHONY:"))
        for target in ("verify", "test", "ci", "smoke", "_check_env", "_live_run"):
            assert target in phony.split()
        verify_line = next(ln for ln in text.splitlines() if ln.startswith("verify:"))
        assert verify_line.split(":", 1)[1].split() == ["_check_env", "test", "_live_run"]

    def test_verify_command_calls_the_helper_and_propagates_its_status(self):
        text = VERIFY_COMMAND.read_text(encoding="utf-8")
        assert "run_once" not in text
        assert "import main" not in text
        assert re.search(
            r'"\$PYTHON"\s+scripts/step5_4_verify_cycle\.py\s*\nLIVE_STATUS=\$\?', text
        )
        # "all steps passed" only on success; the script exits with the cycle's status.
        assert re.search(r"if \[\[ \$LIVE_STATUS -eq 0 \]\]; then\s*\n\s*echo \"=== verify complete", text)
        assert text.rstrip().endswith('exit "$LIVE_STATUS"')

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable bit")
    def test_verify_command_stays_executable(self):
        # "Gravity AI Review Suite.py" checks this too.
        assert os.access(VERIFY_COMMAND, os.X_OK)

    def test_verify_command_is_valid_bash(self):
        try:
            res = subprocess.run(
                ["bash", "-n", str(VERIFY_COMMAND)], capture_output=True, text=True, timeout=30, check=False
            )
        except FileNotFoundError:  # pragma: no cover - no bash on this machine
            pytest.skip("bash not available")
        assert res.returncode == 0, res.stderr


# ---------------------------------------------------------------------------
# AST pins on the real code the overrides depend on (no import of the pipeline)
# ---------------------------------------------------------------------------

def _parse(rel: str) -> ast.Module:
    return ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"), filename=rel)


def _find_func(tree: ast.AST, name: str, cls: str | None = None):
    scope = tree
    if cls is not None:
        scope = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls)
    return next(
        n for n in ast.walk(scope)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )


class TestRealAgenticQueueStepUnderTheForcedMode:
    """Not a fake: run the production AgenticQueueStep inside the helper's override.

    The other tests prove the helper sets the setting; this one proves the setting is what the
    real step actually obeys, with ``primary`` configured (the mode in which a verify run could do real damage).
    """

    def test_primary_configured_but_nothing_is_written(self, monkeypatch, install, out_dir, capsys, caplog):
        from pipeline.agentic_queue import MODE_USED_KEY
        from pipeline.context import RunContext
        from pipeline.production_steps import AgenticQueueStep

        monkeypatch.setattr(settings_module.settings, "DAEMON_AGENTIC_QUEUE_MODE", "primary")
        seen = {}

        async def cycle(mod, rec):
            mod.telemetry = logging.getLogger("verify-cycle-test-telemetry")  # production_steps proxies it
            ctx = RunContext(
                force_account=True,
                started_at=datetime.now(),
                watchlist_file="watchlist.txt",
                fetch_account_snapshot_fn=lambda *a, **kw: None,
                build_universe_fn=lambda *a: [],
                build_macro_dto_fn=lambda: None,
                get_provider_fn=lambda: None,
                fetch_bars_fn=lambda *a: {},
                build_context_extras_fn=lambda *a: {},
                advisory_evaluate_fn=lambda *a, **kw: None,
            )
            AgenticQueueStep().run(ctx)
            seen["mode_used"] = ctx.context_extras.get(MODE_USED_KEY)
            await good_cycle(mod, rec)

        install(cycle)
        before = {p.name for p in out_dir.rglob("*")}

        with caplog.at_level(logging.DEBUG):
            assert vc.main([]) == 0
        # The step must have taken its "off" branch, not died into its swallow-everything handler.
        assert "Agentic queue step failed" not in caplog.text
        assert "DAEMON_AGENTIC_QUEUE_MODE=off" in caplog.text
        written = {p.name for p in out_dir.rglob("*")} - before
        assert seen["mode_used"] == "off"
        assert not written & {"execution_queue.json", "watch_state.json", "queue_sources", "shadow", "advisory.json"}
        assert settings_module.settings.DAEMON_AGENTIC_QUEUE_MODE == "primary"  # restored


class TestCodeContracts:
    def test_end_of_cycle_hook_is_called_by_its_module_global_name(self):
        """Replacing ``main_orchestrator._dispatch_daily_summary`` only works if the
        cycle looks it up as a module global when it runs."""
        fn = _find_func(_parse("main_orchestrator.py"), "_main_body_impl")
        calls = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_dispatch_daily_summary"
        ]
        assert len(calls) == 1

    def test_main_signature_and_dry_run_reaches_the_run_context(self):
        tree = _parse("main_orchestrator.py")
        main_fn = _find_func(tree, "main")
        assert isinstance(main_fn, ast.AsyncFunctionDef)
        assert [a.arg for a in main_fn.args.args] == ["dry_run", "strict"]
        impl = _find_func(tree, "_main_body_impl")
        force_account = [
            kw for n in ast.walk(impl) if isinstance(n, ast.Call)
            for kw in n.keywords if kw.arg == "force_account"
        ]
        assert force_account and all(
            isinstance(kw.value, ast.Name) and kw.value.id == "effective_dry_run" for kw in force_account
        )

    def test_broker_step_reads_the_dry_run_flag_from_the_context(self):
        fn = _find_func(_parse("pipeline/production_steps.py"), "run", cls="BrokerExecutionStep")
        assert any(
            isinstance(n, ast.Attribute) and n.attr == "force_account" for n in ast.walk(fn)
        )

    def test_agentic_queue_step_reads_the_mode_from_settings_at_run_time(self):
        fn = _find_func(_parse("pipeline/production_steps.py"), "run", cls="AgenticQueueStep")
        assert any(
            isinstance(n, ast.Constant) and n.value == "DAEMON_AGENTIC_QUEUE_MODE" for n in ast.walk(fn)
        )

    def test_daily_summary_goes_through_the_module_level_send_alert(self):
        fn = _find_func(_parse("observability/alerts.py"), "send_daily_summary")
        assert any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "send_alert"
            for n in ast.walk(fn)
        )

    def test_helper_follows_the_scripts_bootstrap_convention(self):
        tree = ast.parse(HELPER.read_text(encoding="utf-8"))
        imports_bootstrap = any(
            isinstance(n, ast.ImportFrom) and (n.module or "").endswith("_bootstrap")
            and any(a.name == "bootstrap" for a in n.names)
            for n in ast.walk(tree)
        )
        calls_bootstrap = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "bootstrap"
            for n in ast.walk(tree)
        )
        assert imports_bootstrap and calls_bootstrap
