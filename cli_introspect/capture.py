"""cli_introspect/capture.py — capture a built parser without running the command.

The problem: most entry points build their ``ArgumentParser`` *inside*
``main()`` / ``_build_parser()`` and immediately call ``parse_args()`` before
doing any real work, and importing them pulls in pandas + the calculation
engines. We need the fully-built parser object but must NOT execute the command
body (which would run the pipeline, hit the network, etc.).

The trick: monkeypatch ``ArgumentParser.parse_args`` / ``parse_known_args`` to
capture ``self`` (the fully-built top-level parser) and raise a sentinel, then
``runpy`` the target as ``__main__``. Every entry point reaches ``parse_args``
right after building its parser, so we get the whole parser tree (subparsers
included) and the command body never runs.

Two halves:

  * **child** (``python -m cli_introspect.capture <kind> <target> <name>
    <invocation>``) — installs the patch, runpys the target, introspects the
    captured parser, prints the CommandSpec as JSON to stdout. Runs in its own
    process so the heavy imports/side effects are isolated and never touch the
    parent (or the API).
  * **parent** (``capture_command``) — subprocesses the child with a timeout and
    parses its stdout JSON. Returns ``None`` on ANY failure (non-zero exit,
    timeout, unparseable output) so the caller can dead-letter that one target.
"""
from __future__ import annotations

import argparse
import json
import logging
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_TIMEOUT = 120


# --------------------------------------------------------------------------- #
# Child half — runs in an isolated subprocess.
# --------------------------------------------------------------------------- #
class _CaptureComplete(Exception):
    """Raised from the patched parse_args to unwind with the built parser."""

    def __init__(self, parser: argparse.ArgumentParser):
        super().__init__("parser captured")
        self.parser = parser


def _install_patch() -> None:
    def _capture(self, *args, **kwargs):  # noqa: ANN001 - argparse signature
        raise _CaptureComplete(self)

    # parse_args delegates to parse_known_args, but scripts call either — patch
    # both so whichever fires first captures the top-level parser.
    argparse.ArgumentParser.parse_args = _capture  # type: ignore[assignment]
    argparse.ArgumentParser.parse_known_args = _capture  # type: ignore[assignment]


def _run_child(kind: str, target: str, name: str, invocation: str) -> int:
    # Import the pure walker BEFORE patching argparse (it only reads the parser).
    from .introspect import walk_parser

    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    _install_patch()
    try:
        if kind == "module":
            runpy.run_module(target, run_name="__main__", alter_sys=True)
        else:
            script = target if Path(target).is_absolute() else str(_REPO_ROOT / target)
            runpy.run_path(script, run_name="__main__")
    except _CaptureComplete as done:
        spec = walk_parser(done.parser, name=name, invocation=invocation)
        sys.stdout.write(json.dumps(spec.to_dict()))
        return 0
    except SystemExit:
        # argparse's own error/--help exit, or the script exiting before we
        # reached parse_args. Nothing captured → let the parent dead-letter it.
        sys.stderr.write(f"{name}: exited before a parser was captured\n")
        return 3
    except BaseException as exc:  # noqa: BLE001 - report, never propagate
        sys.stderr.write(f"{name}: {type(exc).__name__}: {exc}\n")
        return 4

    sys.stderr.write(f"{name}: parse_args was never called\n")
    return 5


# --------------------------------------------------------------------------- #
# Parent half — called by scripts/build_command_manifest.py.
# --------------------------------------------------------------------------- #
def capture_command(
    kind: str,
    target: str,
    name: str,
    invocation: str,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Optional[dict]:
    """Introspect one entry point in a subprocess. ``None`` on any failure."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "cli_introspect.capture", kind, target, name, invocation],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("cli_introspect: %s timed out after %ss — skipped", name, timeout)
        return None

    if proc.returncode != 0 or not proc.stdout.strip():
        detail = (proc.stderr or "").strip().splitlines()
        logger.warning(
            "cli_introspect: %s not introspected (exit %s)%s",
            name,
            proc.returncode,
            f": {detail[-1]}" if detail else "",
        )
        return None

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("cli_introspect: %s produced unparseable output: %s", name, exc)
        return None


if __name__ == "__main__":
    # Manual argv parsing ON PURPOSE — argparse is monkeypatched in-process, so
    # we must not use it to read our own arguments.
    _args = sys.argv[1:]
    if len(_args) != 4:
        sys.stderr.write("usage: python -m cli_introspect.capture <kind> <target> <name> <invocation>\n")
        raise SystemExit(2)
    raise SystemExit(_run_child(_args[0], _args[1], _args[2], _args[3]))
