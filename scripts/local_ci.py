#!/usr/bin/env python3
"""Run, on this machine, the checks GitHub Actions used to run.

GitHub Actions is no longer used for this repo (operator decision, 2026-10-29), so
nothing outside the working tree checks a change before it is merged. This script is
the replacement: it runs the same commands the old workflows ran, one after another,
and prints a pass/fail table. It is a convenience wrapper, not a new gate: every step
is a command you could type yourself, and ``--list`` shows exactly which.

What each step reproduces
-------------------------
    step              was (workflow / job)                      command
    ----------------  ----------------------------------------  --------------------------------
    lint              ci.yml / test shard 1 / Lint (ruff)       ruff check . --select=F821,...
    offline           ci.yml / test shards 1-4                  pytest -m "not network and not slow"
    coverage          ci.yml / test (offline suite)             coverage report  (fail_under in .coveragerc)
    slow              ci.yml / test (slow suite)                pytest -m "slow and not network"
    webapp-typecheck  ci.yml / webapp (Pilots PWA)              npm run typecheck
    webapp-test       ci.yml / webapp (Pilots PWA)              npm run test
    webapp-build      ci.yml / webapp (Pilots PWA)              npm run build
    bandit            ci.yml / bandit (SAST)                    bandit -r . -x ... -ll -ii
    pip-audit         ci.yml / security (pip-audit)             pip-audit --desc
    ml-audit          ml_leakage_audit.yml                      stockpy_codebase_auditor.py
    ml-lookahead      ml_leakage_audit.yml                      pytest <3 leakage test files>
    nightly           nightly.yml                               pytest (no marker filter)

Deliberately NOT reproduced
---------------------------
* CodeQL (a GitHub-hosted analysis), the Supabase preview check, and
  ``auto-update-pr-branches.yml`` (a GitHub-only branch updater) have no local form.
* Sharding. One machine runs the whole offline suite in one go. ``scripts/ci_shard.py``
  is still there if you want to split the files across several machines or terminals.
* ``npm ci``. The webapp steps assume ``webapp/node_modules`` is installed; if it is not,
  they fail (loudly) rather than silently skip.

Fail-closed behaviour
---------------------
A step whose tool is missing, whose command cannot start, or that exceeds its timeout is
an ERROR/TIMEOUT, never a skip. The only legitimate skips are shown with their reason:
``--skip``/``--no-coverage`` (you asked), an unmet dependency (``coverage`` after a failed
``offline``), and the path-gated ML-leakage steps when git says nothing watched changed
(and when git cannot say, they RUN).

Exit codes: 0 every selected step passed or was legitimately skipped; 1 at least one
step failed, errored or timed out; 2 bad usage.

Examples::

    python scripts/local_ci.py --list
    python scripts/local_ci.py --profile quick           # everything except the long suites
    python scripts/local_ci.py --only lint,ml-audit      # just those steps
    python scripts/local_ci.py --no-coverage --fail-fast # the full set, without coverage
    make local-ci ARGS="--profile quick"
"""

from __future__ import annotations

import argparse
import fnmatch
import shutil
import subprocess
import sys
import sysconfig
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence, TextIO

REPO_ROOT = Path(__file__).resolve().parent.parent

# `python scripts/local_ci.py` puts scripts/ on sys.path[0], not the repo root, so
# `from scripts._bootstrap import bootstrap` (see _entrypoint) would not resolve.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PASS, FAIL, ERROR, TIMEOUT, SKIPPED = "PASS", "FAIL", "ERROR", "TIMEOUT", "SKIPPED"
BAD_STATUSES = frozenset({FAIL, ERROR, TIMEOUT})

PROFILES = ("pr", "quick", "nightly")

# Paths that gate the ML-leakage steps; copied from ml_leakage_audit.yml's `paths:` filter.
# fnmatch's `*` also matches `/`, so "ml/*" covers everything below ml/.
ML_WATCHED_PATHS = (
    "ml/*",
    "sizing/*",
    "scripts/*forecast_backfill*",
    "forecasting_engine.py",
    "cnn_lstm_worker.py",
)

BANDIT_EXCLUDES = "./tests,./webapp,./.venv,./node_modules,./legacy"
ML_LOOKAHEAD_TESTS = (
    "tests/test_lgbm_no_leakage.py",
    "tests/test_forecasting_lookahead.py",
    "tests/test_forecast_backfill.py",
)


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    cmd: tuple[str, ...]
    timeout: int  # seconds; the step is killed and reported TIMEOUT after this
    mirrors: str  # which old workflow job this reproduces
    profiles: tuple[str, ...]
    needs: tuple[str, ...] = ()  # step ids that must PASS first, when they are selected
    only_if_paths: tuple[str, ...] = ()  # run only if a changed file matches; empty = always
    skip_reason: str = ""  # non-empty = reported SKIPPED with this reason


@dataclass(frozen=True)
class Result:
    id: str
    status: str
    seconds: float
    detail: str = ""


Runner = Callable[[Sequence[str], int, Path], int]
ChangedFiles = Callable[[str], Optional[list[str]]]


def build_steps(*, python: str, coverage: bool, site_packages: str) -> list[Step]:
    """The step table. Pure: no I/O, so tests can pin it."""
    offline = [
        python, "-m", "pytest", "-m", "not network and not slow", "-q", "--tb=short",
        "-n", "auto", "--dist", "loadgroup", "--timeout=180", "--timeout-method=thread",
    ]
    if coverage:
        offline += ["--cov", "--cov-report=", "--cov-fail-under=0"]
    return [
        Step("lint", "ruff genuine-bug rules",
             (python, "-m", "ruff", "check", ".", "--select=F821,F822,F823,E9"),
             300, "ci.yml / test shard 1 / Lint (ruff)", ("pr", "quick")),
        Step("offline", "offline test suite (not network, not slow)",
             tuple(offline), 7200, "ci.yml / test shards 1-4", ("pr",)),
        Step("coverage", "coverage floor (fail_under in .coveragerc)",
             (python, "-m", "coverage", "report", "--skip-covered"),
             600, "ci.yml / test (offline suite)", ("pr",), needs=("offline",),
             skip_reason="" if coverage else "--no-coverage given"),
        Step("slow", "slow-marked test suite",
             (python, "-m", "pytest", "-m", "slow and not network", "-q", "--tb=short",
              "-n", "auto", "--timeout=600", "--timeout-method=thread"),
             3600, "ci.yml / test (slow suite)", ("pr",)),
        Step("webapp-typecheck", "webapp typecheck",
             ("npm", "--prefix", "webapp", "run", "typecheck"),
             600, "ci.yml / webapp (Pilots PWA)", ("pr", "quick")),
        Step("webapp-test", "webapp unit tests (Vitest)",
             ("npm", "--prefix", "webapp", "run", "test"),
             900, "ci.yml / webapp (Pilots PWA)", ("pr", "quick")),
        Step("webapp-build", "webapp production build",
             ("npm", "--prefix", "webapp", "run", "build"),
             900, "ci.yml / webapp (Pilots PWA)", ("pr", "quick")),
        Step("bandit", "bandit SAST (medium+ severity, medium+ confidence)",
             ("uvx", "bandit", "-r", ".", "-x", BANDIT_EXCLUDES, "-ll", "-ii"),
             900, "ci.yml / bandit (SAST)", ("pr", "quick")),
        Step("pip-audit", "dependency vulnerability audit of this environment",
             ("uvx", "pip-audit", "--desc", "--path", site_packages),
             900, "ci.yml / security (pip-audit); needs network", ("pr",)),
        Step("ml-audit", "ML data-leakage codebase auditor",
             (python, "scripts/auditor/stockpy_codebase_auditor.py", "--root", ".",
              "--fail-on", "HIGH"),
             900, "ml_leakage_audit.yml", ("pr", "quick"), only_if_paths=ML_WATCHED_PATHS),
        Step("ml-lookahead", "ML lookahead/leakage tests",
             (python, "-m", "pytest", *ML_LOOKAHEAD_TESTS, "-q"),
             1800, "ml_leakage_audit.yml", ("pr", "quick"), only_if_paths=ML_WATCHED_PATHS),
        Step("nightly", "full suite, no marker filter (network + slow)",
             (python, "-m", "pytest", "-q", "--tb=short", "-n", "auto", "--timeout=600",
              "--timeout-method=thread"),
             10800, "nightly.yml (hits live upstreams; a red run can be upstream flake)",
             ("nightly",)),
    ]


# --------------------------------------------------------------------------- selection


def _split_ids(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def select_steps(steps: Sequence[Step], *, profile: str, only: Sequence[str],
                 skip: Sequence[str]) -> list[Step]:
    """Apply --profile / --only / --skip. Raises ValueError on an unknown step id."""
    known = {s.id for s in steps}
    unknown = [i for i in [*only, *skip] if i not in known]
    if unknown:
        raise ValueError(f"unknown step id(s): {', '.join(unknown)} (known: {', '.join(sorted(known))})")
    chosen = [s for s in steps if s.id in only] if only else [s for s in steps if profile in s.profiles]
    return [s for s in chosen if s.id not in skip]


# --------------------------------------------------------------------------- environment


def changed_files(base: str, *, timeout: int = 60) -> Optional[list[str]]:
    """Paths changed vs ``base`` (committed, uncommitted and untracked), or None if git can't say."""
    commands = (
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    paths: set[str] = set()
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
                                  timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        paths.update(line.strip() for line in proc.stdout.splitlines() if line.strip())
    return sorted(paths)


def default_runner(cmd: Sequence[str], timeout: int, cwd: Path) -> int:
    """Run ``cmd`` with inherited stdio so progress is visible; return its exit code."""
    return subprocess.run(list(cmd), cwd=str(cwd), timeout=timeout, check=False).returncode


# --------------------------------------------------------------------------- execution


def _matches_any(paths: Sequence[str], patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(p, pat) for p in paths for pat in patterns)


def run_steps(steps: Sequence[Step], *, runner: Runner, changed: ChangedFiles, base: str,
              all_paths: bool, fail_fast: bool, out: TextIO) -> list[Result]:
    results: list[Result] = []
    by_id: dict[str, Result] = {}
    selected_ids = {s.id for s in steps}
    changed_cache: dict[str, Optional[list[str]]] = {}
    stop = False

    def record(res: Result) -> None:
        results.append(res)
        by_id[res.id] = res
        suffix = f" ({res.detail})" if res.detail else ""
        print(f"   -> {res.status}{suffix}  [{res.seconds:.1f}s]\n", file=out, flush=True)

    for index, step in enumerate(steps, start=1):
        print(f"== [{index}/{len(steps)}] {step.id}: {step.title}\n   mirrors: {step.mirrors}",
              file=out, flush=True)
        if stop:
            record(Result(step.id, SKIPPED, 0.0, "stopped by --fail-fast"))
            continue
        if step.skip_reason:
            record(Result(step.id, SKIPPED, 0.0, step.skip_reason))
            continue
        unmet = [n for n in step.needs if n in selected_ids and by_id.get(n, Result(n, SKIPPED, 0.0)).status != PASS]
        if unmet:
            record(Result(step.id, SKIPPED, 0.0, f"needs {', '.join(unmet)} to pass first"))
            continue
        note = ""
        if step.only_if_paths and not all_paths:
            if "files" not in changed_cache:
                changed_cache["files"] = changed(base)
            files = changed_cache["files"]
            if files is None:
                note = "git could not list changed files; running to be safe"
            elif not _matches_any(files, step.only_if_paths):
                record(Result(step.id, SKIPPED, 0.0, f"no watched path changed vs {base}"))
                continue
        if note:
            print(f"   note: {note}", file=out, flush=True)
        print(f"   $ {' '.join(step.cmd)}", file=out, flush=True)
        started = time.monotonic()
        if shutil.which(step.cmd[0]) is None:
            record(Result(step.id, ERROR, 0.0, f"executable not found: {step.cmd[0]}"))
        else:
            try:
                code = runner(step.cmd, step.timeout, REPO_ROOT)
            except subprocess.TimeoutExpired:
                record(Result(step.id, TIMEOUT, time.monotonic() - started, f"exceeded {step.timeout}s"))
            except OSError as exc:
                record(Result(step.id, ERROR, time.monotonic() - started, f"could not start: {exc}"))
            else:
                elapsed = time.monotonic() - started
                record(Result(step.id, PASS if code == 0 else FAIL, elapsed,
                              "" if code == 0 else f"exit {code}"))
        if fail_fast and results[-1].status in BAD_STATUSES:
            stop = True
    return results


def print_summary(results: Sequence[Result], out: TextIO) -> None:
    width = max((len(r.id) for r in results), default=4)
    print("== summary", file=out)
    for r in results:
        detail = f"  {r.detail}" if r.detail else ""
        print(f"   {r.id:<{width}}  {r.status:<7} {r.seconds:8.1f}s{detail}", file=out)
    bad = [r.id for r in results if r.status in BAD_STATUSES]
    verdict = f"FAILED: {', '.join(bad)}" if bad else "all selected steps passed"
    print(f"== {verdict}", file=out, flush=True)


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="local_ci.py",
        description="Run the checks GitHub Actions used to run, on this machine.",
    )
    p.add_argument("--profile", choices=PROFILES, default="pr",
                   help="pr = everything CI ran on a PR (default); quick = pr minus the long/"
                        "network suites (offline, coverage, slow, pip-audit); nightly = full suite")
    p.add_argument("--only", default="", help="comma-separated step ids; overrides --profile")
    p.add_argument("--skip", default="", help="comma-separated step ids to leave out")
    p.add_argument("--list", action="store_true", help="show the steps and commands, then exit")
    p.add_argument("--dry-run", action="store_true", help="show what would run, run nothing")
    p.add_argument("--fail-fast", action="store_true", help="stop after the first failing step")
    p.add_argument("--no-coverage", action="store_true",
                   help="run the offline suite without coverage (about twice as fast) and skip the floor")
    p.add_argument("--all-paths", action="store_true",
                   help="run the path-gated ML-leakage steps even if no watched file changed")
    p.add_argument("--base", default="origin/main",
                   help="ref to diff against for path-gated steps (default: origin/main)")
    return p


def main(argv: Optional[Sequence[str]] = None, *, runner: Runner = default_runner,
         changed: ChangedFiles = changed_files, out: Optional[TextIO] = None) -> int:
    out = out or sys.stdout
    parser = build_parser()
    args = parser.parse_args(argv)
    steps = build_steps(python=sys.executable, coverage=not args.no_coverage,
                        site_packages=sysconfig.get_paths()["purelib"])
    try:
        selected = select_steps(steps, profile=args.profile, only=_split_ids(args.only),
                                skip=_split_ids(args.skip))
    except ValueError as exc:
        parser.error(str(exc))  # exits 2
    if not selected:
        parser.error("no steps selected")

    if args.list or args.dry_run:
        for s in selected if args.dry_run else steps:
            gate = f"  [only if changed: {', '.join(s.only_if_paths)}]" if s.only_if_paths else ""
            needs = f"  [needs: {', '.join(s.needs)}]" if s.needs else ""
            print(f"{s.id:<17} profiles={','.join(s.profiles):<9} timeout={s.timeout:>5}s  "
                  f"{s.mirrors}{needs}{gate}\n{'':<17} $ {' '.join(s.cmd)}", file=out)
        return 0

    results = run_steps(selected, runner=runner, changed=changed, base=args.base,
                        all_paths=args.all_paths, fail_fast=args.fail_fast, out=out)
    print_summary(results, out)
    return 1 if any(r.status in BAD_STATUSES for r in results) else 0


def _entrypoint() -> int:
    # `bootstrap()` re-execs under .venv's interpreter and loads .env; every scripts/*.py must
    # call it (tests/test_scripts_bootstrap.py). Placed here, not at import time, so importing
    # this module in a test never re-execs.
    from scripts._bootstrap import bootstrap

    bootstrap()
    return main()


if __name__ == "__main__":
    sys.exit(_entrypoint())
