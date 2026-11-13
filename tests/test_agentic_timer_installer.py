"""Tests for the agentic Robinhood timer installer.

``scripts/agentic_timers.py`` (logic) + ``scripts/install_agentic_timers.command``
(the thin launchctl wrapper). The hand-install steps rewrote only the repo path,
so the templates' ``/Users/kevinlee/...`` PATH entries survived and a Mac whose
``claude`` lives elsewhere failed every cycle that passed the gate.
"""
from __future__ import annotations

import math
import os
import plistlib
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import agentic_timers as at

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
INSTALLER = SCRIPTS / "install_agentic_timers.command"
TEMPLATES = {label: (SCRIPTS / f"{label}.plist").read_bytes() for label in at.LABELS}

OTHER_REPO = Path("/Users/alex/code/Stockpy")
OTHER_HOME = Path("/Users/alex")
OTHER_CLAUDE = Path("/Users/alex/.npm-global/bin")


def _render(label, *, repo=OTHER_REPO, home=OTHER_HOME, claude_dir=OTHER_CLAUDE):
    return plistlib.loads(at.render_plist(TEMPLATES[label], repo_root=repo, home=home,
                                          claude_dir=claude_dir))


# ---------------------------------------------------------------------------
# render_plist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", at.LABELS)
class TestRender:
    def test_no_placeholder_left_on_another_mac(self, label):
        out = at.render_plist(TEMPLATES[label], repo_root=OTHER_REPO, home=OTHER_HOME,
                              claude_dir=OTHER_CLAUDE)
        assert b"kevinlee" not in out

    def test_paths_point_at_the_real_repo(self, label):
        d = _render(label)
        assert d["WorkingDirectory"] == str(OTHER_REPO)
        assert d["ProgramArguments"][0] == f"{OTHER_REPO}/.venv/bin/python3"
        assert d["StandardOutPath"].startswith(f"{OTHER_REPO}/output/")
        assert d["StandardErrorPath"].startswith(f"{OTHER_REPO}/output/")

    def test_claude_folder_first_on_path_and_home_rewritten(self, label):
        entries = _render(label)["EnvironmentVariables"]["PATH"].split(":")
        assert entries[0] == str(OTHER_CLAUDE)
        assert f"{OTHER_HOME}/.local/bin" in entries
        assert {"/usr/bin", "/bin"} <= set(entries)
        assert len(entries) == len(set(entries))

    def test_schedule_and_launcher_unchanged(self, label):
        src, d = plistlib.loads(TEMPLATES[label]), _render(label)
        assert d["Label"] == src["Label"] == label
        assert d["ProgramArguments"][1:] == src["ProgramArguments"][1:]
        for key in ("StartInterval", "StartCalendarInterval", "RunAtLoad", "ProcessType"):
            assert d.get(key) == src.get(key)
        assert d["RunAtLoad"] is False and not d.get("KeepAlive")

    def test_operator_mac_is_a_noop_except_claude(self, label):
        # On the operator's own paths only PATH changes (claude already there → deduped).
        home = Path(at.PLACEHOLDER_HOME)
        d = _render(label, repo=Path(at.PLACEHOLDER_REPO), home=home,
                    claude_dir=home / ".local" / "bin")
        src = plistlib.loads(TEMPLATES[label])
        assert d["ProgramArguments"] == src["ProgramArguments"]
        assert d["EnvironmentVariables"]["PATH"] == src["EnvironmentVariables"]["PATH"]


def test_render_rejects_garbage():
    with pytest.raises(at.RenderError):
        at.render_plist(b"not a plist", repo_root=OTHER_REPO, home=OTHER_HOME, claude_dir=OTHER_CLAUDE)


def test_render_rejects_a_job_not_running_this_repos_venv():
    tpl = plistlib.loads(TEMPLATES["com.investyo.rh-agentic-cycle"])
    tpl["ProgramArguments"][0] = "/usr/bin/python3"
    with pytest.raises(at.RenderError, match="venv"):
        at.render_plist(plistlib.dumps(tpl), repo_root=OTHER_REPO, home=OTHER_HOME,
                        claude_dir=OTHER_CLAUDE)


def test_render_rejects_a_leftover_placeholder():
    tpl = plistlib.loads(TEMPLATES["com.investyo.rh-agentic-cycle"])
    tpl["EnvironmentVariables"]["EXTRA"] = "/Users/kevinlee/somewhere"
    with pytest.raises(at.RenderError, match="placeholder"):
        at.render_plist(plistlib.dumps(tpl), repo_root=OTHER_REPO, home=OTHER_HOME,
                        claude_dir=OTHER_CLAUDE)


# ---------------------------------------------------------------------------
# find_claude
# ---------------------------------------------------------------------------

def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_find_claude_prefers_path(tmp_path):
    on_path = _exe(tmp_path / "bin" / "claude")
    _exe(tmp_path / "home" / ".local" / "bin" / "claude")
    assert at.find_claude(tmp_path / "home", str(tmp_path / "bin")) == on_path


def test_find_claude_falls_back_to_usual_folders(tmp_path, monkeypatch):
    home = tmp_path / "home"
    want = _exe(home / ".claude" / "local" / "claude")
    monkeypatch.setattr(at, "claude_candidates", lambda h: [h / ".local/bin/claude", want])
    assert at.find_claude(home, str(tmp_path / "empty")) == want


def test_find_claude_ignores_non_executables(tmp_path, monkeypatch):
    plain = tmp_path / "claude"
    plain.write_text("x")
    monkeypatch.setattr(at, "claude_candidates", lambda h: [plain])
    assert at.find_claude(tmp_path, str(tmp_path / "empty")) is None


# ---------------------------------------------------------------------------
# assess
# ---------------------------------------------------------------------------

GOOD = dict(mode="review", cap=50.0, queue_mode="primary", flags_refresh=True, kill_switch_active=False)


def test_ready_setup_has_no_problems():
    r = at.assess(**GOOD)
    assert r.problems == [] and r.cap == 50.0
    assert any("review" in n for n in r.notes)


@pytest.mark.parametrize("change,needle", [
    ({"mode": "off"}, "mode_off"),
    ({"mode": None}, "mode_off"),
    ({"cap": 0.0}, "No per-order cap"),
    ({"cap": float("nan")}, "No per-order cap"),
    ({"cap": None}, "No per-order cap"),
    ({"queue_mode": "shadow"}, "queue_missing"),
    ({"kill_switch_active": True}, "Kill switch is ON"),
    ({"kill_switch_active": None}, "fails closed"),
])
def test_each_blocker_is_reported(change, needle):
    r = at.assess(**{**GOOD, **change})
    assert len(r.problems) == 1 and needle in r.problems[0]


def test_live_and_refresh_off_are_notes_not_problems():
    r = at.assess(**{**GOOD, "mode": "live", "flags_refresh": False})
    assert r.problems == []
    assert any("tap" in n for n in r.notes) and any("restart the daemon" in n for n in r.notes)


# ---------------------------------------------------------------------------
# start_burnin
# ---------------------------------------------------------------------------

class FakeWriter:
    def __init__(self, fail_on=None, pinned=None):
        self.calls, self.fail_on, self.pinned = [], fail_on, pinned

    def __call__(self, key, value, *, actor=""):
        self.calls.append((key, value, actor))
        if key == self.fail_on:
            return SimpleNamespace(ok=False, reason="refused", applies="refused", applied_value=None)
        applies = "env_pinned" if key == self.pinned else "immediately"
        return SimpleNamespace(ok=True, reason=None, applies=applies, applied_value=value)


def _state(**kw):
    return at.assess(**{**GOOD, "mode": "off", "cap": 0.0, **kw})


def test_burnin_writes_cap_then_review_after_typed_confirmation():
    w = FakeWriter()
    ok, lines = at.start_burnin(None, confirm=lambda p: "REVIEW", current=_state(), writer=w)
    assert ok
    assert w.calls == [("ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 50.0, at.ACTOR),
                       ("ROBINHOOD_EXECUTION_MODE", "review", at.ACTOR)]
    assert all("live" not in str(c[1]) for c in w.calls)


@pytest.mark.parametrize("answer", ["", "review", "yes", "REVIEW please"])
def test_burnin_needs_the_exact_word(answer):
    w = FakeWriter()
    ok, lines = at.start_burnin(None, confirm=lambda p: answer, current=_state(), writer=w)
    assert not ok and w.calls == [] and "unchanged" in lines[0]


def test_burnin_eof_is_not_a_confirmation():
    def boom(_):
        raise EOFError
    w = FakeWriter()
    assert at.start_burnin(None, confirm=boom, current=_state(), writer=w)[0] is False
    assert w.calls == []


def test_burnin_never_touches_a_live_setup():
    w = FakeWriter()
    asked = []
    ok, lines = at.start_burnin(None, confirm=asked.append, current=_state(mode="live"), writer=w)
    assert not ok and w.calls == [] and asked == [] and "live" in lines[0]


def test_burnin_keeps_an_existing_cap_unless_given():
    w = FakeWriter()
    at.start_burnin(None, confirm=lambda p: "REVIEW", current=_state(cap=25.0), writer=w)
    assert w.calls[0][1] == 25.0
    w = FakeWriter()
    at.start_burnin(10, confirm=lambda p: "REVIEW", current=_state(cap=25.0), writer=w)
    assert w.calls[0][1] == 10.0


@pytest.mark.parametrize("cap", [0, -5, math.nan, math.inf])
def test_burnin_rejects_a_bad_cap(cap):
    w = FakeWriter()
    ok, _ = at.start_burnin(cap, confirm=lambda p: "REVIEW", current=_state(), writer=w)
    assert not ok and w.calls == []


def test_burnin_stops_before_review_when_the_cap_write_fails():
    w = FakeWriter(fail_on="ROBINHOOD_MAX_NOTIONAL_PER_ORDER")
    ok, lines = at.start_burnin(None, confirm=lambda p: "REVIEW", current=_state(), writer=w)
    assert not ok and [c[0] for c in w.calls] == ["ROBINHOOD_MAX_NOTIONAL_PER_ORDER"]


def test_burnin_reports_an_env_pinned_value():
    w = FakeWriter(pinned="ROBINHOOD_EXECUTION_MODE")
    ok, lines = at.start_burnin(None, confirm=lambda p: "REVIEW", current=_state(), writer=w)
    assert not ok and "pinned" in lines[-1]


def test_burnin_with_the_real_writer(monkeypatch):
    # The store is redirected per test by the root conftest; register the live
    # values with monkeypatch first so the setattr-applied overrides are undone.
    from settings import settings
    monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "off")
    monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 0.0)
    ok, lines = at.start_burnin(None, confirm=lambda p: "REVIEW", current=_state())
    assert ok, lines
    assert settings.ROBINHOOD_EXECUTION_MODE == "review"
    assert settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER == 50.0


def test_burnin_cli_refuses_without_a_terminal(monkeypatch):
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    assert at.main(["burnin"]) == 1


# ---------------------------------------------------------------------------
# The shell installer
# ---------------------------------------------------------------------------

def _run(*args, env_extra=None):
    env = {**os.environ, "AGENTIC_TIMERS_DRY_RUN": "1", **(env_extra or {})}
    return subprocess.run(["bash", str(INSTALLER), *args], capture_output=True, text=True,
                          env=env, timeout=120, stdin=subprocess.DEVNULL)


def test_installer_is_executable_and_valid_bash():
    assert INSTALLER.stat().st_mode & stat.S_IXUSR
    assert subprocess.run(["bash", "-n", str(INSTALLER)], timeout=30).returncode == 0


def test_installer_covers_exactly_the_two_agentic_labels():
    text = INSTALLER.read_text()
    for label in at.LABELS:
        assert f'"{label}"' in text


@pytest.mark.parametrize("args", [["--bogus"], ["--cap", "50"], ["--cap"], ["--claude"]])
def test_installer_rejects_bad_arguments(args):
    assert _run(*args).returncode == 2


def test_default_installer_still_leaves_the_agents_out():
    text = (SCRIPTS / "install_schedule.command").read_text()
    assert all(label not in text for label in at.LABELS)


def test_uninstall_removes_both(tmp_path):
    for label in at.LABELS:
        (tmp_path / f"{label}.plist").write_text("x")
    res = _run("--uninstall", env_extra={"AGENTIC_TIMERS_TARGET_DIR": str(tmp_path)})
    assert res.returncode == 0, res.stdout + res.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(not (REPO / ".venv" / "bin" / "python3").exists(), reason="needs the repo .venv")
def test_install_dry_run_renders_both(tmp_path):
    fake_claude = _exe(tmp_path / "cli" / "claude")
    target = tmp_path / "LaunchAgents"
    res = _run("--claude", str(fake_claude), env_extra={"AGENTIC_TIMERS_TARGET_DIR": str(target)})
    assert res.returncode == 0, res.stdout + res.stderr
    for label in at.LABELS:
        d = plistlib.loads((target / f"{label}.plist").read_bytes())
        assert d["Label"] == label
        assert d["EnvironmentVariables"]["PATH"].split(":")[0] == str(fake_claude.parent)
        assert d["WorkingDirectory"] == str(REPO)


@pytest.mark.skipif(not (REPO / ".venv" / "bin" / "python3").exists(), reason="needs the repo .venv")
def test_install_refuses_a_missing_claude(tmp_path):
    res = _run("--claude", str(tmp_path / "nope"), env_extra={"AGENTIC_TIMERS_TARGET_DIR": str(tmp_path)})
    assert res.returncode == 1 and not list(tmp_path.glob("*.plist"))
