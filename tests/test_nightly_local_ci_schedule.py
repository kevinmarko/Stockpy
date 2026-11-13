"""
tests/test_nightly_local_ci_schedule.py
=======================================
Pins the nightly local-CI launchd timer (scripts/com.investyo.nightly-local-ci.plist)
and its wrapper (scripts/nightly_local_ci.sh), and lints every launchd plist under
scripts/ so a malformed one (for example a "--" inside an XML comment) fails here
instead of at `launchctl load` time on the Mac.

The wrapper is exercised for real with a stub `make` first on PATH, from a copy of
the script in a temp checkout, so the test never runs the real suite and never
writes outside tmp_path.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
PLIST = SCRIPTS / "com.investyo.nightly-local-ci.plist"
WRAPPER = SCRIPTS / "nightly_local_ci.sh"
ALL_PLISTS = sorted(SCRIPTS.glob("*.plist"))


# ---------------------------------------------------------------------------
# Lint for every plist in scripts/
# ---------------------------------------------------------------------------

def test_every_scripts_plist_is_found():
    assert PLIST in ALL_PLISTS
    assert len(ALL_PLISTS) >= 2


@pytest.mark.parametrize("path", ALL_PLISTS, ids=lambda p: p.name)
def test_plist_parses_and_label_matches_filename(path):
    data = plistlib.loads(path.read_bytes())
    assert data["Label"] == path.stem
    assert data["ProgramArguments"], "a launchd job needs ProgramArguments"


# ---------------------------------------------------------------------------
# The nightly timer
# ---------------------------------------------------------------------------

class TestNightlyPlist:
    data = plistlib.loads(PLIST.read_bytes())

    def test_runs_the_wrapper(self):
        assert self.data["ProgramArguments"] == [
            "/bin/bash", "__STOCKPY_REPO__/scripts/nightly_local_ci.sh",
        ]

    def test_once_a_day_outside_market_hours(self):
        cal = self.data["StartCalendarInterval"]
        # One dict, no Weekday key: every day, once.
        assert isinstance(cal, dict) and "Weekday" not in cal and "Day" not in cal
        # Regular US session is 09:30-16:00 ET and the daemon's automatic window is
        # 04:00-20:00 ET; the nightly run must start outside both.
        assert not (4 <= cal["Hour"] < 20)

    def test_never_runs_at_load_or_restarts(self):
        assert self.data["RunAtLoad"] is False
        assert not self.data.get("KeepAlive")
        assert "StartInterval" not in self.data

    def test_no_hard_coded_user_home(self):
        text = PLIST.read_text()
        assert "/Users/" not in text and "/home/" not in text
        assert "~" not in "".join(self.data["ProgramArguments"])
        # Logging is done by the wrapper under LOCAL_DATA_ROOT, not by launchd.
        assert "StandardOutPath" not in self.data and "StandardErrorPath" not in self.data

    def test_not_auto_installed(self):
        for installer in ("install_schedule.command", "install_stack_service.command"):
            assert "nightly-local-ci" not in (SCRIPTS / installer).read_text()

    def test_runbook_documents_install_and_uninstall(self):
        runbook = (REPO / "docs" / "RUNBOOK.md").read_text()
        assert 'sed "s|__STOCKPY_REPO__|$PWD|g" scripts/com.investyo.nightly-local-ci.plist' in runbook
        assert "launchctl load ~/Library/LaunchAgents/com.investyo.nightly-local-ci.plist" in runbook
        assert "launchctl unload ~/Library/LaunchAgents/com.investyo.nightly-local-ci.plist" in runbook


def test_no_github_workflows_directory():
    # Standing policy: no GitHub Actions, ever. The nightly run is local only.
    assert not (REPO / ".github" / "workflows").exists()


# ---------------------------------------------------------------------------
# The wrapper
# ---------------------------------------------------------------------------

def test_wrapper_is_executable_and_valid_bash():
    assert WRAPPER.stat().st_mode & stat.S_IXUSR
    subprocess.run(["bash", "-n", str(WRAPPER)], check=True, timeout=30)


def _run_wrapper(tmp_path: Path, make_exit: int) -> tuple[subprocess.CompletedProcess, Path]:
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    script = checkout / "scripts" / "nightly_local_ci.sh"
    shutil.copy2(WRAPPER, script)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_make = bin_dir / "make"
    fake_make.write_text(
        "#!/bin/bash\n"
        'echo "fake make called with: $*"\n'
        'echo "cwd: $PWD"\n'
        f"exit {make_exit}\n"
    )
    fake_make.chmod(0o755)

    data_root = tmp_path / "data_root"
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "LOCAL_DATA_ROOT": str(data_root),
    }
    proc = subprocess.run(["/bin/bash", str(script)], env=env, capture_output=True,
                          text=True, timeout=60)
    return proc, data_root / "logs" / "nightly_ci"


@pytest.mark.skipif(not Path("/bin/bash").exists(), reason="needs /bin/bash")
@pytest.mark.parametrize("make_exit", [0, 3])
def test_wrapper_runs_nightly_profile_and_logs_under_local_data_root(tmp_path, make_exit):
    proc, log_dir = _run_wrapper(tmp_path, make_exit)

    # The make exit code is the job's exit code (a red nightly run is visible).
    assert proc.returncode == make_exit, proc.stderr

    logs = sorted(log_dir.glob("nightly_ci_*.log"))
    assert len(logs) == 1
    text = logs[0].read_text()
    assert "fake make called with: local-ci ARGS=--profile nightly" in text
    assert f"cwd: {tmp_path / 'checkout'}" in text
    assert f"exit={make_exit}" in text

    assert (log_dir / "latest.log").resolve() == logs[0].resolve()
    status = (log_dir / "last_status").read_text()
    assert f"exit={make_exit}" in status and str(logs[0]) in status

    # Nothing is written inside the checkout.
    assert sorted(p.name for p in (tmp_path / "checkout").rglob("*")) == ["nightly_local_ci.sh", "scripts"]


@pytest.mark.skipif(not Path("/bin/bash").exists(), reason="needs /bin/bash")
def test_wrapper_prunes_logs_older_than_30_days(tmp_path):
    log_dir = tmp_path / "data_root" / "logs" / "nightly_ci"
    log_dir.mkdir(parents=True)
    old = log_dir / "nightly_ci_20200101_223000.log"
    old.write_text("old")
    month_ago = old.stat().st_mtime - 40 * 86400
    os.utime(old, (month_ago, month_ago))
    keep = log_dir / "nightly_ci_20990101_223000.log"
    keep.write_text("recent")

    proc, _ = _run_wrapper(tmp_path, 0)

    assert proc.returncode == 0, proc.stderr
    assert not old.exists()
    assert keep.exists()
