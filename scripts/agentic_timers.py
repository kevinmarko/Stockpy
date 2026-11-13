"""One-command setup for the agentic Robinhood timers.

``scripts/install_agentic_timers.command`` (the operator-facing installer) calls
this module for everything that isn't a ``launchctl`` call, so the logic can be
tested on any OS. It covers two launchd jobs:

* ``com.investyo.rh-agentic-cycle``: the unattended four-agent cycle, every 15
  minutes (Phase 4);
* ``com.investyo.rh-agentic-review``: Agent 5 (Reviewer), weekdays 16:40 (Phase 5).

Why it exists: the hand-install steps in the RUNBOOK rewrote only the repo path.
The templates' ``PATH`` also carries ``/Users/kevinlee/...`` entries, and the
launchers start ``claude`` from that ``PATH``, so on a Mac where ``claude`` lives
elsewhere every cycle that passed the gate failed. :func:`render_plist` rewrites
the plist structurally (``plistlib``, never a text replace that could corrupt a
repo path) and puts the folder of the real ``claude`` first on ``PATH``.

``--burnin`` (operator-run, typed confirmation, never automatic) sets
``ROBINHOOD_EXECUTION_MODE=review`` and a per-order cap through
``runtime_flags_writer.write_override``. It never sets ``live`` and refuses to
touch a ``live`` setup: going live stays the operator's own settings change.

CLI (``python scripts/agentic_timers.py ...``; exit 0 ok, 1 refused/failed):

* ``find-claude``: print the ``claude`` the timers will use.
* ``render --label L --out FILE [--claude PATH]``: write the installed plist.
* ``check``: print the settings the cycle depends on and what would stop it.
* ``burnin [--cap DOLLARS]``: start the burn-in (asks you to type ``REVIEW``).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

if __name__ == "__main__":  # pragma: no cover - re-exec only when run as a script
    from scripts._bootstrap import bootstrap
    bootstrap()

import argparse  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import plistlib  # noqa: E402
import shutil  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any, Callable, List, Optional, Tuple  # noqa: E402

LABELS: Tuple[str, ...] = ("com.investyo.rh-agentic-cycle", "com.investyo.rh-agentic-review")

#: The path every plist template in scripts/ ships with (install_schedule.command
#: rewrites the same string).
PLACEHOLDER_REPO = "/Users/kevinlee/Desktop/Stockpy"
#: The home folder the templates' PATH entries assume.
PLACEHOLDER_HOME = "/Users/kevinlee"

DEFAULT_BURNIN_CAP = 50.0
CONFIRM_WORD = "REVIEW"
ACTOR = "install_agentic_timers"


class RenderError(ValueError):
    """The template could not be turned into a safe installed plist."""


# ---------------------------------------------------------------------------
# Finding claude
# ---------------------------------------------------------------------------

def claude_candidates(home: Path) -> List[Path]:
    """Usual install locations of the Claude Code CLI, checked after ``PATH``."""
    return [
        home / ".local" / "bin" / "claude",
        home / ".claude" / "local" / "claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
    ]


def find_claude(home: Path, path_env: Optional[str]) -> Optional[Path]:
    """The ``claude`` executable the timers should use, or None if there is none."""
    found = shutil.which("claude", path=path_env) if path_env else None
    if found:
        return Path(found)
    for cand in claude_candidates(home):
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand
    return None


# ---------------------------------------------------------------------------
# Rendering the installed plist
# ---------------------------------------------------------------------------

def _swap_prefix(value: str, old: str, new: str) -> str:
    if value == old or value.startswith(old + "/"):
        return new + value[len(old):]
    return value


def launchd_path(claude_dir: Path, home: Path, template_path: str) -> str:
    """``PATH`` for the job: the real ``claude`` folder first, then the template's
    entries with the placeholder home swapped for the real one, deduplicated."""
    entries = [str(claude_dir)]
    for entry in (template_path or "").split(":"):
        if entry:
            entries.append(_swap_prefix(entry, PLACEHOLDER_HOME, str(home)))
    seen: set = set()
    return ":".join(e for e in entries if not (e in seen or seen.add(e)))


def _strings(obj: Any) -> List[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in _strings(v)]
    return []


def render_plist(template: bytes, *, repo_root: Path, home: Path, claude_dir: Path) -> bytes:
    """The installed plist for this Mac. Raises :class:`RenderError` rather than
    return a job that would point at the placeholder folder."""
    try:
        data = plistlib.loads(template)
    except Exception as exc:
        raise RenderError(f"template is not a valid plist ({type(exc).__name__})") from exc
    if not isinstance(data, dict) or not data.get("Label"):
        raise RenderError("template has no Label")

    repo = str(repo_root)
    for key in ("WorkingDirectory", "StandardOutPath", "StandardErrorPath"):
        if isinstance(data.get(key), str):
            data[key] = _swap_prefix(data[key], PLACEHOLDER_REPO, repo)
    data["ProgramArguments"] = [
        _swap_prefix(a, PLACEHOLDER_REPO, repo) if isinstance(a, str) else a
        for a in data.get("ProgramArguments") or []
    ]
    env = dict(data.get("EnvironmentVariables") or {})
    env["PATH"] = launchd_path(claude_dir, home, str(env.get("PATH") or ""))
    data["EnvironmentVariables"] = env

    # Nothing may still point at the placeholder unless this Mac really uses it.
    if not (repo.startswith(PLACEHOLDER_HOME) or str(home).startswith(PLACEHOLDER_HOME)):
        left = [s for s in _strings(data) if PLACEHOLDER_HOME in s]
        if left:
            raise RenderError(f"placeholder path left in the rendered plist: {left[0]}")
    if not data["ProgramArguments"] or data["ProgramArguments"][0] != f"{repo}/.venv/bin/python3":
        raise RenderError("ProgramArguments does not start with this repo's .venv python")
    return plistlib.dumps(data)


# ---------------------------------------------------------------------------
# Readiness (what the cycle depends on)
# ---------------------------------------------------------------------------

@dataclass
class Readiness:
    mode: Optional[str] = None
    cap: Optional[float] = None
    queue_mode: Optional[str] = None
    flags_refresh: Optional[bool] = None
    kill_switch_active: Optional[bool] = None
    #: Things that make every cycle skip or keep the Reviewer NOT READY.
    problems: List[str] = field(default_factory=list)
    #: Informational lines.
    notes: List[str] = field(default_factory=list)


def _finite_positive(x: Any) -> bool:
    try:
        return math.isfinite(float(x)) and float(x) > 0
    except (TypeError, ValueError):
        return False


def assess(*, mode: Any, cap: Any, queue_mode: Any, flags_refresh: Any,
           kill_switch_active: Any) -> Readiness:
    """Pure: turn the current values into problems and notes."""
    r = Readiness(
        mode=str(mode) if mode is not None else None,
        cap=float(cap) if _finite_positive(cap) else None,
        queue_mode=str(queue_mode) if queue_mode is not None else None,
        flags_refresh=flags_refresh if isinstance(flags_refresh, bool) else None,
        kill_switch_active=kill_switch_active if isinstance(kill_switch_active, bool) else None,
    )
    if r.mode not in ("review", "live"):
        r.problems.append(
            f"ROBINHOOD_EXECUTION_MODE is {r.mode or 'unknown'}: every cycle skips (mode_off). "
            "Run with --burnin to set review."
        )
    elif r.mode == "live":
        r.notes.append("Mode is live: orders go to your phone, and only your tap approves one.")
    else:
        r.notes.append("Mode is review: the agents run end to end and send nothing.")
    if r.cap is None:
        r.problems.append(
            "No per-order cap (ROBINHOOD_MAX_NOTIONAL_PER_ORDER): the preflight vetoes every buy "
            "and the Reviewer stays NOT READY. --burnin sets one."
        )
    if r.queue_mode != "primary":
        r.problems.append(
            f"DAEMON_AGENTIC_QUEUE_MODE is {r.queue_mode or 'unknown'}: the daemon doesn't write "
            "execution_queue.json, so every cycle skips (queue_missing)."
        )
    if r.flags_refresh is False:
        r.notes.append(
            "RUNTIME_FLAGS_REFRESH_ENABLED is off: restart the daemon after changing the mode, "
            "or the queue keeps the old mode (the stricter one wins)."
        )
    if r.kill_switch_active is not False:
        r.problems.append(
            "Kill switch is ON: every cycle skips." if r.kill_switch_active
            else "Kill switch state unknown: the cycle gate fails closed and skips."
        )
    return r


def readiness() -> Readiness:
    """:func:`assess` fed from this Mac's live settings and kill switch."""
    try:
        from settings import settings
        from execution.kill_switch import GlobalKillSwitch

        return assess(
            mode=settings.ROBINHOOD_EXECUTION_MODE,
            cap=settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER,
            queue_mode=settings.DAEMON_AGENTIC_QUEUE_MODE,
            flags_refresh=settings.RUNTIME_FLAGS_REFRESH_ENABLED,
            kill_switch_active=GlobalKillSwitch().is_active(),
        )
    except Exception as exc:
        r = Readiness()
        r.problems.append(f"Settings could not be read ({type(exc).__name__}).")
        return r


# ---------------------------------------------------------------------------
# Starting the burn-in (operator-run, typed confirmation)
# ---------------------------------------------------------------------------

def start_burnin(cap: Optional[float], *, confirm: Callable[[str], str],
                 current: Optional[Readiness] = None,
                 writer: Optional[Callable[..., Any]] = None) -> Tuple[bool, List[str]]:
    """Set review mode and a per-order cap. Returns ``(ok, lines to print)``.

    Never sets ``live``; refuses when the mode already is ``live``. Keeps an
    existing positive cap unless ``cap`` is given. Writes the cap first so review
    never starts without one.
    """
    current = current or readiness()
    if current.mode == "live":
        return False, [
            "The mode is live. --burnin won't change a live setup; set review yourself "
            "if you mean to restart the burn-in."
        ]
    if cap is not None and not _finite_positive(cap):
        return False, ["--cap must be a positive dollar amount."]
    new_cap = float(cap) if cap is not None else (current.cap or DEFAULT_BURNIN_CAP)

    prompt = (f"This sets ROBINHOOD_EXECUTION_MODE=review and ROBINHOOD_MAX_NOTIONAL_PER_ORDER="
              f"{new_cap:g}. Review mode sends no orders.\nType {CONFIRM_WORD} to confirm: ")
    try:
        answer = confirm(prompt)
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if (answer or "").strip() != CONFIRM_WORD:
        return False, ["Not confirmed; settings unchanged."]

    if writer is None:
        from runtime_flags_writer import write_override as writer
    lines: List[str] = []
    for key, value in (("ROBINHOOD_MAX_NOTIONAL_PER_ORDER", new_cap),
                       ("ROBINHOOD_EXECUTION_MODE", "review")):
        res = writer(key, value, actor=ACTOR)
        if not getattr(res, "ok", False):
            lines.append(f"Could not set {key}: {getattr(res, 'reason', None) or 'unknown reason'}.")
            return False, lines
        applies = getattr(res, "applies", "")
        if applies == "env_pinned":
            lines.append(f"{key} is pinned by a shell environment variable, which wins over this "
                         f"setting (live value: {res.applied_value}). Unset it, then run again.")
            return False, lines
        lines.append(f"Set {key}={res.applied_value} ({applies}).")
    return True, lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_readiness(r: Readiness) -> None:
    print(f"  Mode: {r.mode or 'unknown'}   Cap: {('$%g' % r.cap) if r.cap else 'not set'}   "
          f"Queue writer: {r.queue_mode or 'unknown'}   Kill switch: "
          f"{'unknown' if r.kill_switch_active is None else ('ON' if r.kill_switch_active else 'off')}")
    for n in r.notes:
        print(f"  - {n}")
    for p in r.problems:
        print(f"  ! {p}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("find-claude")
    rp = sub.add_parser("render")
    rp.add_argument("--label", required=True, choices=LABELS)
    rp.add_argument("--out", required=True)
    rp.add_argument("--claude", default=None, help="path to the claude executable")
    sub.add_parser("check")
    bp = sub.add_parser("burnin")
    bp.add_argument("--cap", type=float, default=None)
    args = ap.parse_args(argv)
    home = Path.home()

    if args.cmd == "find-claude":
        found = find_claude(home, os.environ.get("PATH"))
        if not found:
            print("claude not found on PATH or in "
                  + ", ".join(str(c) for c in claude_candidates(home)))
            return 1
        print(found)
        return 0

    if args.cmd == "render":
        claude = Path(args.claude) if args.claude else find_claude(home, os.environ.get("PATH"))
        if claude is None or not (claude.is_file() and os.access(claude, os.X_OK)):
            print("claude not found; pass --claude /path/to/claude")
            return 1
        template = (_REPO_ROOT / "scripts" / f"{args.label}.plist").read_bytes()
        try:
            out = render_plist(template, repo_root=_REPO_ROOT, home=home, claude_dir=claude.parent)
        except RenderError as exc:
            print(f"Refusing to install {args.label}: {exc}")
            return 1
        Path(args.out).write_bytes(out)
        print(f"Wrote {args.out} (claude: {claude})")
        return 0

    if args.cmd == "check":
        _print_readiness(readiness())
        return 0

    if args.cmd == "burnin":
        if not sys.stdin.isatty():
            print("--burnin needs a terminal to type the confirmation; settings unchanged.")
            return 1
        ok, lines = start_burnin(args.cap, confirm=input)
        for line in lines:
            print(f"  {line}")
        return 0 if ok else 1
    return 1  # pragma: no cover - argparse requires a command


if __name__ == "__main__":
    sys.exit(main())
