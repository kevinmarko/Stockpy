"""scripts/local_ci.py: the local replacement for the GitHub Actions workflows.

GitHub Actions is no longer used (2026-10-29), so this runner is the only thing standing
between a change and a merge. These tests pin the properties that make it trustworthy as a
gate: the step table mirrors the old jobs, a failing/missing/slow step makes the exit code
non-zero (fail closed, CONSTRAINT #6), skips are limited to the documented legitimate
reasons and are always labelled, and ``--dry-run``/``--list`` never execute anything.

Nothing here runs a real subprocess: a fake runner stands in for it and ``shutil.which`` is
stubbed so the tests do not depend on npm/uvx being installed.
"""

from __future__ import annotations

import io
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import local_ci  # noqa: E402


def _steps(coverage: bool = True) -> list[local_ci.Step]:
    return local_ci.build_steps(python="/usr/bin/python3", coverage=coverage,
                                site_packages="/venv/lib/python3.12/site-packages")


class FakeRunner:
    """Records every command; returns exit code ``codes.get(<step first arg>, 0)``."""

    def __init__(self, codes: dict[str, int] | None = None, raises: dict[str, Exception] | None = None):
        self.codes = codes or {}
        self.raises = raises or {}
        self.calls: list[tuple[str, ...]] = []

    def _key(self, cmd) -> str:
        """Map a command back to its step id, using the table the CLI itself builds
        (``sys.executable`` + this environment's site-packages), with and without coverage."""
        argv = list(cmd)
        if argv and argv[0] == "env":  # steps may run under `env KEY=VALUE ...`: look through it
            argv = argv[1:]
            while argv and "=" in argv[0] and not argv[0].startswith("/"):
                argv = argv[1:]
        text = " ".join(argv)
        for coverage in (True, False):
            for step in local_ci.build_steps(python=sys.executable, coverage=coverage,
                                             site_packages=sysconfig.get_paths()["purelib"]):
                if " ".join(step.cmd) == text:
                    return step.id
        return text

    def __call__(self, cmd, timeout, cwd) -> int:
        self.calls.append(tuple(cmd))
        key = self._key(cmd)
        if key in self.raises:
            raise self.raises[key]
        return self.codes.get(key, 0)


@pytest.fixture(autouse=True)
def _tools_exist(monkeypatch):
    monkeypatch.setattr(local_ci.shutil, "which", lambda name: f"/usr/bin/{name}")


def _run(argv, *, runner=None, changed=lambda base: []):
    out = io.StringIO()
    runner = runner or FakeRunner()
    code = local_ci.main(argv, runner=runner, changed=changed, out=out)
    return code, out.getvalue(), runner


def _statuses(text: str) -> dict[str, str]:
    """Parse the summary block: step id -> status."""
    block = text.split("== summary", 1)[1]
    found = {}
    for line in block.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in {local_ci.PASS, local_ci.FAIL, local_ci.ERROR,
                                            local_ci.TIMEOUT, local_ci.SKIPPED}:
            found[parts[0]] = parts[1]
    return found


# --------------------------------------------------------------------------- the step table


class TestStepTable:
    def test_ids_unique_and_complete(self):
        ids = [s.id for s in _steps()]
        assert len(ids) == len(set(ids))
        assert {"lint", "offline", "coverage", "slow", "webapp-typecheck", "webapp-test",
                "webapp-build", "bandit", "pip-audit", "ml-audit", "ml-lookahead",
                "nightly"} <= set(ids)

    def test_every_step_is_well_formed(self):
        ids = {s.id for s in _steps()}
        for s in _steps():
            assert s.cmd and all(isinstance(a, str) and a for a in s.cmd), s.id
            assert s.timeout > 0, s.id
            assert s.mirrors, s.id
            assert set(s.profiles) <= set(local_ci.PROFILES), s.id
            assert set(s.needs) <= ids, s.id

    def test_commands_match_what_the_old_workflows_ran(self):
        by = {s.id: " ".join(s.cmd) for s in _steps()}
        assert "ruff check . --select=F821,F822,F823,E9" in by["lint"]
        assert '-m not network and not slow' in by["offline"] and "--dist loadgroup" in by["offline"]
        assert "--timeout=180" in by["offline"]
        assert "-m slow and not network" in by["slow"] and "--timeout=600" in by["slow"]
        assert "-r . -x ./tests,./webapp,./.venv,./node_modules,./legacy -ll -ii" in by["bandit"]
        assert "--fail-on HIGH" in by["ml-audit"]
        assert "pip-audit --desc --path /venv/lib/python3.12/site-packages" in by["pip-audit"]
        nightly = next(s for s in _steps() if s.id == "nightly")
        # The nightly job ran the COMPLETE suite: no `-m <marker>` filter after `python -m pytest`.
        assert nightly.cmd[:3] == ("/usr/bin/python3", "-m", "pytest")
        assert "-m" not in nightly.cmd[3:]

    def test_xdist_steps_are_thread_capped_and_the_others_are_not(self):
        """LightGBM starts one OpenMP thread per core in EVERY xdist worker; on a 4-core box,
        4 workers x 4 threads stalled or crashed LightGBM training (reproduced on clean main).
        Exactly the steps that fan out across workers carry the hard cap."""
        steps = {s.id: s for s in _steps()}
        for sid in ("offline", "slow", "nightly"):
            assert "-n" in steps[sid].cmd, f"{sid} is expected to be an xdist step"
            assert steps[sid].env == ("OMP_THREAD_LIMIT=1",), sid
            assert steps[sid].argv[:2] == ("env", "OMP_THREAD_LIMIT=1"), sid
            assert steps[sid].argv[2:] == steps[sid].cmd, sid
        for sid in ("lint", "coverage", "webapp-test", "bandit", "pip-audit", "ml-audit",
                    "ml-lookahead"):
            assert steps[sid].env == () and steps[sid].argv == steps[sid].cmd, sid

    def test_coverage_arguments_only_when_enabled(self):
        on = {s.id: s for s in _steps(coverage=True)}
        off = {s.id: s for s in _steps(coverage=False)}
        assert "--cov" in on["offline"].cmd and not on["coverage"].skip_reason
        assert "--cov" not in off["offline"].cmd
        assert off["coverage"].skip_reason  # labelled skip, not silently dropped

    def test_profiles(self):
        steps = _steps()
        pr = {s.id for s in local_ci.select_steps(steps, profile="pr", only=[], skip=[])}
        quick = {s.id for s in local_ci.select_steps(steps, profile="quick", only=[], skip=[])}
        nightly = {s.id for s in local_ci.select_steps(steps, profile="nightly", only=[], skip=[])}
        assert {"offline", "coverage", "slow", "pip-audit"} <= pr
        assert not ({"offline", "coverage", "slow", "pip-audit", "nightly"} & quick)
        assert {"lint", "bandit", "webapp-test"} <= quick
        assert nightly == {"nightly"}

    def test_only_overrides_profile_and_skip_removes(self):
        steps = _steps()
        only = local_ci.select_steps(steps, profile="quick", only=["offline"], skip=[])
        assert [s.id for s in only] == ["offline"]
        skipped = local_ci.select_steps(steps, profile="quick", only=[], skip=["bandit"])
        assert "bandit" not in {s.id for s in skipped}

    def test_unknown_step_id_is_rejected(self):
        with pytest.raises(ValueError, match="unknown step id"):
            local_ci.select_steps(_steps(), profile="pr", only=["nope"], skip=[])


# --------------------------------------------------------------------------- running


class TestRunning:
    def test_all_pass_exits_zero_in_order(self):
        code, text, runner = _run(["--profile", "quick"])
        assert code == 0
        assert "all selected steps passed" in text
        assert [c[0] for c in runner.calls][:1] == [sys.executable]
        quick_ids = [s.id for s in local_ci.select_steps(_steps(), profile="quick", only=[], skip=[])]
        # ml steps are path-gated (changed=[]) so they skip; everything else ran, in table order
        ran = [k for k in (FakeRunner()._key(c) for c in runner.calls)]
        assert ran == [i for i in quick_ids if i not in {"ml-audit", "ml-lookahead"}]

    def test_a_failing_step_makes_the_exit_code_nonzero_and_others_still_run(self):
        runner = FakeRunner(codes={"lint": 1})
        code, text, _ = _run(["--profile", "quick"], runner=runner)
        assert code == 1
        status = _statuses(text)
        assert status["lint"] == local_ci.FAIL and status["bandit"] == local_ci.PASS
        assert "FAILED: lint" in text

    def test_fail_fast_stops_and_labels_the_rest(self):
        runner = FakeRunner(codes={"lint": 1})
        code, text, _ = _run(["--profile", "quick", "--fail-fast"], runner=runner)
        assert code == 1
        status = _statuses(text)
        assert status["lint"] == local_ci.FAIL
        assert all(v == local_ci.SKIPPED for k, v in status.items() if k != "lint")
        assert "stopped by --fail-fast" in text

    def test_timeout_is_a_failure(self):
        runner = FakeRunner(raises={"lint": subprocess.TimeoutExpired(cmd="x", timeout=1)})
        code, text, _ = _run(["--only", "lint"], runner=runner)
        assert code == 1 and _statuses(text)["lint"] == local_ci.TIMEOUT

    def test_a_command_that_cannot_start_is_an_error_not_a_skip(self):
        runner = FakeRunner(raises={"lint": OSError("exec format error")})
        code, text, _ = _run(["--only", "lint"], runner=runner)
        assert code == 1 and _statuses(text)["lint"] == local_ci.ERROR

    def test_missing_tool_is_an_error_not_a_skip(self, monkeypatch):
        monkeypatch.setattr(local_ci.shutil, "which", lambda name: None if name == "uvx" else f"/usr/bin/{name}")
        code, text, runner = _run(["--only", "bandit"])
        assert code == 1
        assert _statuses(text)["bandit"] == local_ci.ERROR
        assert "executable not found: uvx" in text
        assert runner.calls == []  # never attempted


class TestEnvPrefix:
    def test_the_runner_receives_the_env_prefixed_command(self):
        code, text, runner = _run(["--only", "offline"])
        assert code == 0
        assert runner.calls[0][:2] == ("env", "OMP_THREAD_LIMIT=1")
        assert "OMP_THREAD_LIMIT=1" in text  # and it is visible in the printed command

    def test_a_serial_step_runs_without_a_prefix(self):
        code, text, runner = _run(["--only", "lint"])
        assert runner.calls[0][0] == sys.executable

    def test_list_shows_the_prefix(self):
        code, text, _ = _run(["--list"])
        assert "env OMP_THREAD_LIMIT=1" in text

    def test_missing_env_program_is_an_error_not_a_skip(self, monkeypatch):
        monkeypatch.setattr(local_ci.shutil, "which",
                            lambda name: None if name == "env" else f"/usr/bin/{name}")
        code, text, runner = _run(["--only", "offline"])
        assert code == 1 and _statuses(text)["offline"] == local_ci.ERROR
        assert "executable not found: env" in text
        assert runner.calls == []


class TestCoverageDependency:
    def test_coverage_runs_after_a_passing_offline(self):
        code, text, runner = _run(["--only", "offline,coverage"])
        assert code == 0
        assert _statuses(text) == {"offline": local_ci.PASS, "coverage": local_ci.PASS}

    def test_coverage_is_skipped_when_offline_fails(self):
        runner = FakeRunner(codes={"offline": 1})
        code, text, _ = _run(["--only", "offline,coverage"], runner=runner)
        status = _statuses(text)
        assert code == 1
        assert status["offline"] == local_ci.FAIL and status["coverage"] == local_ci.SKIPPED
        assert "needs offline to pass first" in text

    def test_coverage_alone_runs_without_offline(self):
        """Re-reporting an existing .coverage file is legitimate: only selected deps are enforced."""
        code, text, _ = _run(["--only", "coverage"])
        assert code == 0 and _statuses(text) == {"coverage": local_ci.PASS}

    def test_no_coverage_is_labelled(self):
        code, text, runner = _run(["--only", "offline,coverage", "--no-coverage"])
        assert code == 0
        assert _statuses(text)["coverage"] == local_ci.SKIPPED
        assert "--no-coverage given" in text
        assert all("--cov" not in c for c in runner.calls)


class TestPathGatedMlSteps:
    ML = ["--only", "ml-audit,ml-lookahead"]

    def test_skipped_when_no_watched_path_changed(self):
        code, text, runner = _run(self.ML, changed=lambda base: ["README.md", "docs/RUNBOOK.md"])
        assert code == 0 and runner.calls == []
        assert set(_statuses(text).values()) == {local_ci.SKIPPED}
        assert "no watched path changed vs origin/main" in text

    @pytest.mark.parametrize("path", ["ml/train_lgbm.py", "sizing/kelly.py",
                                      "scripts/backfill_forecast_backfill_x.py",
                                      "forecasting_engine.py", "cnn_lstm_worker.py"])
    def test_runs_when_a_watched_path_changed(self, path):
        code, text, runner = _run(self.ML, changed=lambda base: ["README.md", path])
        assert code == 0 and len(runner.calls) == 2
        assert set(_statuses(text).values()) == {local_ci.PASS}

    def test_runs_when_git_cannot_say(self):
        """Fail closed: unknown change set means RUN the gate, never skip it."""
        code, text, runner = _run(self.ML, changed=lambda base: None)
        assert len(runner.calls) == 2
        assert "running to be safe" in text

    def test_all_paths_forces_the_run(self):
        code, text, runner = _run(self.ML + ["--all-paths"], changed=lambda base: [])
        assert len(runner.calls) == 2

    def test_base_ref_is_passed_through(self):
        seen = []
        _run(self.ML + ["--base", "origin/dev"], changed=lambda base: seen.append(base) or [])
        assert seen == ["origin/dev"]


class TestListAndDryRun:
    def test_list_shows_every_step_and_runs_nothing(self):
        class Boom:
            def __call__(self, *a, **k):
                raise AssertionError("nothing may execute under --list")

        code, text, _ = _run(["--list"], runner=Boom())
        assert code == 0
        for step in _steps():
            assert step.id in text

    def test_dry_run_shows_selected_commands_and_runs_nothing(self):
        class Boom:
            def __call__(self, *a, **k):
                raise AssertionError("nothing may execute under --dry-run")

        code, text, _ = _run(["--dry-run", "--only", "lint"], runner=Boom())
        assert code == 0 and "ruff check ." in text and "bandit" not in text


class TestUsage:
    def test_unknown_step_is_a_usage_error(self):
        with pytest.raises(SystemExit) as exc:
            _run(["--only", "nosuchstep"])
        assert exc.value.code == 2

    def test_selecting_nothing_is_a_usage_error(self):
        with pytest.raises(SystemExit) as exc:
            _run(["--profile", "quick", "--skip", ",".join(s.id for s in _steps())])
        assert exc.value.code == 2


# --------------------------------------------------------------------------- git helper


class TestChangedFiles:
    def test_unions_committed_uncommitted_and_untracked(self, monkeypatch):
        outputs = iter(["a.py\nb.py\n", "b.py\nc.py\n", "d.py\n"])

        def fake_run(cmd, **kwargs):
            assert "timeout" in kwargs  # repo rule: every subprocess call has a timeout
            return subprocess.CompletedProcess(cmd, 0, stdout=next(outputs), stderr="")

        monkeypatch.setattr(local_ci.subprocess, "run", fake_run)
        assert local_ci.changed_files("origin/main") == ["a.py", "b.py", "c.py", "d.py"]

    def test_none_when_git_fails(self, monkeypatch):
        monkeypatch.setattr(local_ci.subprocess, "run",
                            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 128, stdout="", stderr="bad ref"))
        assert local_ci.changed_files("nope") is None

    def test_none_when_git_is_missing(self, monkeypatch):
        def boom(cmd, **kw):
            raise FileNotFoundError("git")

        monkeypatch.setattr(local_ci.subprocess, "run", boom)
        assert local_ci.changed_files("origin/main") is None
