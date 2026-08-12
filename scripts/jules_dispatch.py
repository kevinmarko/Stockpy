"""
scripts/jules_dispatch.py
==========================
CLI front-end for ``data/jules_client.py`` — the single HTTP seam for
Google's Jules coding-agent REST API (see that module's own docstring for
the full design/safety writeup). This script exposes the two real
``jules_client`` functions as argparse subcommands for a human operator to
run from a terminal, alongside the platform's other consumer (the
``investyo_mcp_server.py`` MCP tool).

Subcommands
-----------
``list-sources``
    No arguments. Prints the GitHub repos connected to this Jules account
    (one line per source). Exits 1 with a stderr message on
    ``JulesUnavailable`` — never a raw traceback.

``create-session``
    Dispatches a new Jules session (``AUTO_CREATE_PR`` automation mode —
    see ``data/jules_client.py``'s docstring for why that mode is
    hardcoded, not a CLI flag). This is NOT a dry-run/preview operation: on
    success it opens a real, unsupervised pull request against the target
    repo. Guarded by a required ``--confirm`` flag — omitting it refuses to
    call ``dispatch_session`` at all, exiting 1 with a clear explanation
    on stderr. ``--force`` passes through to ``dispatch_session``'s own
    ``force`` param, overriding its same-UTC-day duplicate-dispatch guard.

Convention notes
-----------------
``main(argv: list[str] | None = None) -> int`` returns an exit code rather
than calling ``sys.exit()`` itself, matching ``scripts/preflight_check.py``'s
own convention (not ``scripts/bug_hunter.py``'s ``sys.exit()``-in-``main()``
style) — this makes ``main()`` directly unit-testable (assert on the
returned int) without any ``pytest.raises(SystemExit)`` machinery. The
module-level ``if __name__ == "__main__":`` block is the only place that
calls ``sys.exit(main())``.

``bootstrap()`` is called inside that same ``if __name__ == "__main__":``
block, not at module top, because this module is also imported as a
library by its own test file (``tests/test_jules_dispatch.py``, which does
``from scripts.jules_dispatch import main``) — a module-top call would fire
the venv-reexec check on every such import, not just when this file is the
actual entry point. Mirrors ``scripts/preflight_check.py``'s identical
placement and reasoning.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Resolve repo root so this script can be ``python scripts/jules_dispatch.py``-ed
# from any working directory without requiring the venv to be on PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data.jules_client import JulesUnavailable, dispatch_session, list_sources


def _cmd_list_sources(args: argparse.Namespace) -> int:
    try:
        result = list_sources()
    except JulesUnavailable as exc:
        print(f"ERROR: could not list Jules sources: {exc}", file=sys.stderr)
        return 1

    sources = result.get("sources", []) if isinstance(result, dict) else []
    if not sources:
        print("No Jules sources connected.")
        return 0

    print(f"Connected Jules sources ({len(sources)}):")
    for source in sources:
        if isinstance(source, dict):
            name = source.get("name", "<unknown>")
        else:
            name = str(source)
        print(f"  - {name}")
    return 0


def _cmd_create_session(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(
            "ERROR: dispatching a Jules session opens a real, unsupervised "
            "pull request against the target repo. Re-run with --confirm to "
            "proceed.",
            file=sys.stderr,
        )
        return 1

    try:
        result = dispatch_session(
            prompt=args.prompt,
            source=args.source,
            branch=args.branch,
            title=args.title,
            force=args.force,
        )
    except JulesUnavailable as exc:
        print(f"ERROR: could not dispatch Jules session: {exc}", file=sys.stderr)
        return 1

    session_name = result.get("name", "<unknown>") if isinstance(result, dict) else "<unknown>"
    print("Jules session dispatched successfully.")
    print(f"  session: {session_name}")
    print(f"  source:  {args.source}")
    print(f"  branch:  {args.branch}")
    print(f"  title:   {args.title}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jules_dispatch.py",
        description=(
            "CLI for Google's Jules coding-agent API — list connected "
            "sources or dispatch a new autonomous coding session."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    list_sources_parser = subparsers.add_parser(
        "list-sources",
        help="List the GitHub repos connected to this Jules account.",
    )
    list_sources_parser.set_defaults(func=_cmd_list_sources)

    create_session_parser = subparsers.add_parser(
        "create-session",
        help=(
            "Dispatch a new Jules session (opens a real PR on success). "
            "Requires --confirm."
        ),
    )
    create_session_parser.add_argument(
        "--prompt", required=True, type=str, help="The task prompt for the Jules session."
    )
    create_session_parser.add_argument(
        "--title", required=True, type=str, help="A short title for the Jules session."
    )
    create_session_parser.add_argument(
        "--source",
        required=True,
        type=str,
        help="Connected source identifier, e.g. sources/github/OWNER/REPO.",
    )
    create_session_parser.add_argument(
        "--branch",
        default="main",
        type=str,
        help="Target branch on the source repo (default: main).",
    )
    create_session_parser.add_argument(
        "--confirm",
        action="store_true",
        default=False,
        help=(
            "Required to actually dispatch — acknowledges that this opens a "
            "real, unsupervised PR on the target repo."
        ),
    )
    create_session_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help=(
            "Override the same-UTC-day duplicate-dispatch guard in "
            "data/jules_client.py's dispatch ledger."
        ),
    )
    create_session_parser.set_defaults(func=_cmd_create_session)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 1

    return args.func(args)


if __name__ == "__main__":
    # Venv re-exec + .env loading -- placed here (not at module top)
    # because this module is also imported as a library by
    # tests/test_jules_dispatch.py; a module-top call would fire the
    # re-exec check on every such import, not just when this file is
    # the actual entry point. See scripts/_bootstrap.py's module
    # docstring for the full rationale.
    from scripts._bootstrap import bootstrap
    bootstrap()
    sys.exit(main())
