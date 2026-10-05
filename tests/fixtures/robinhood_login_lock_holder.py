"""Second-process helper for tests/test_robinhood_login.py::TestCrossProcessLock.

Runs as a REAL separate Python process: points data.robinhood_login's
cross-process lock at ``argv[1]``, starts one ``refresh`` login whose worker is
the stub script (tests/fixtures/robinhood_login_worker_stub.py -- never the
real worker, never Robinhood), prints one JSON line
``{"pid", "job_id", "worker_pid"}`` and then either:

  hold    -- sleeps until the test kills it (the lock stays held for as long
             as this process lives);
  finish  -- waits for the job to reach a terminal state AND for the lock to
             be released, prints ``released``, and exits 0.

The stub's behaviour comes from ``STUB_LOGIN_BEHAVIOR`` in the environment.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

_STUB_PATH = Path(__file__).parent / "robinhood_login_worker_stub.py"


class _PopenProxy:
    def __getattr__(self, name):
        return getattr(subprocess, name)

    def Popen(self, argv, *args, **kwargs):  # noqa: N802 - mirrors subprocess.Popen
        assert argv[1:3] == ["-m", "data.robinhood_login_worker"], argv
        return subprocess.Popen([argv[0], str(_STUB_PATH)] + list(argv[3:]), *args, **kwargs)


def main() -> int:
    lock_dir, action = sys.argv[1], sys.argv[2]

    import data.robinhood_login as rl
    from settings import settings

    settings.RH_LOGIN_DEADLINE_SECONDS = 60
    settings.RH_LOGIN_STARTUP_SECONDS = 20
    settings.RH_LOGIN_GRACE_SECONDS = 1
    rl._lock_dir_override = Path(lock_dir)
    rl.subprocess = _PopenProxy()

    job = rl.start_login("refresh")
    print(json.dumps({
        "pid": __import__("os").getpid(),
        "job_id": job.job_id,
        "worker_pid": job._process.pid,
    }), flush=True)

    if action == "hold":
        time.sleep(3600)
        return 0

    deadline = time.time() + 30
    while time.time() < deadline:
        with job._lock:
            terminal = job.state != "running"
        if terminal and job._xlock_fd is None:
            print("released", flush=True)
            return 0
        time.sleep(0.05)
    print("not-released", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
