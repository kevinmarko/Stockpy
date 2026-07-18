"""pilots/commands.py — file-backed reader for the CLI command manifest.

Serves ``cli_introspect/command_manifest.json`` (a committed artifact produced
offline by ``scripts/build_command_manifest.py``) to ``GET /commands``, which
powers the Pilots PWA command bar's autocomplete + validation.

Why a reader, not live introspection: introspecting the argparse parsers means
importing the orchestrators / scripts, which pull in pandas + the calculation
engines — exactly the imports ``api/pilots_api.py``'s AST guard forbids. So the
manifest is built offline and this module only READS the flat JSON, staying on
the same dependency-light footing as ``pilots/run_status.py`` /
``pilots/options.py`` (stdlib only; imports nothing heavy).

Honesty (CONSTRAINT #4/#6): a missing or malformed manifest degrades to an
empty ``commands`` list plus an explanatory ``reason`` — never a fabricated
command list, and never an exception.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Repo-root-relative committed artifact (pilots/ -> repo root -> cli_introspect/).
_DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "cli_introspect" / "command_manifest.json"

_MISSING_REASON = (
    "No command manifest yet — run `python scripts/build_command_manifest.py` "
    "to generate cli_introspect/command_manifest.json."
)
_CORRUPT_REASON = "Command manifest is unreadable or malformed — regenerate it with scripts/build_command_manifest.py."


def _empty(reason: str) -> Dict[str, Any]:
    return {"generated_at": None, "command_count": 0, "commands": [], "reason": reason}


def command_manifest(path: Optional[Path] = None) -> Dict[str, Any]:
    """Return the parsed command manifest, or an honest empty shape.

    Shape (success): ``{generated_at, command_count, dead_letters, commands,
    reason: None}``. On a missing/corrupt/wrong-shaped file: ``{generated_at:
    None, command_count: 0, commands: [], reason: <str>}`` — never raises.
    """
    manifest_path = path or _DEFAULT_MANIFEST
    if not manifest_path.exists():
        return _empty(_MISSING_REASON)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - never raise (CONSTRAINT #6)
        logger.debug("pilots.commands: could not read %s: %s", manifest_path, exc)
        return _empty(_CORRUPT_REASON)

    commands = data.get("commands") if isinstance(data, dict) else None
    if not isinstance(commands, list):
        return _empty(_CORRUPT_REASON)

    return {
        "generated_at": data.get("generated_at"),
        "command_count": len(commands),
        "dead_letters": data.get("dead_letters", []),
        "commands": commands,
        "reason": None,
    }
