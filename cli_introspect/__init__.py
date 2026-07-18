"""cli_introspect — offline introspection of the platform's argparse CLIs.

Turns each operator-facing ``argparse`` entry point into a JSON **command
manifest** (``command_manifest.json``) that two surfaces consume WITHOUT ever
importing the heavy calculation engines at runtime:

  * ``scripts/generate_shell_completion.py`` → static zsh/bash completions.
  * ``pilots/commands.py`` → ``GET /commands`` → the Pilots PWA command bar.

Why offline: introspecting a parser means importing its module, and the
orchestrators / scripts pull in pandas + the calculation engines. The Pilots
API's AST guard forbids those imports, so introspection is a build step
(``scripts/build_command_manifest.py``) that runs each entry point in an
isolated subprocess, captures the built parser (see ``capture.py``), and emits
the static manifest. Both surfaces then read that flat JSON — never the live
parsers.

Dead-letter, don't crash (CLAUDE.md convention): one un-introspectable entry
point is logged and skipped, never aborting the whole manifest.
"""
from __future__ import annotations

from .schema import (
    ARG_KIND_OPTIONAL,
    ARG_KIND_REQUIRED,
    ARG_KIND_VARIADIC,
    ArgSpec,
    CommandSpec,
    OptionSpec,
)
from .introspect import walk_parser

__all__ = [
    "ARG_KIND_OPTIONAL",
    "ARG_KIND_REQUIRED",
    "ARG_KIND_VARIADIC",
    "ArgSpec",
    "CommandSpec",
    "OptionSpec",
    "walk_parser",
]
