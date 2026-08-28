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
# Reviewed false positive (stockpy_codebase_auditor `circular_dependency`,
# 2026-08): this re-export trips the auditor's cycle detector as
# "cli_introspect -> cli_introspect.introspect -> cli_introspect" because any
# `from .submodule import X` in a package's __init__.py creates an implicit
# submodule<->parent edge — inherent to Python packages, not a real cycle.
# introspect.py itself only imports from .schema (never from this package)
# and its own docstring states it is "Pure and side-effect-free", so there is
# no import-time side effect for the cycle to matter for.
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
