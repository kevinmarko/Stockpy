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
from typing import Any, Dict, List, Optional

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


def _last_invocation_token(command: Dict[str, Any]) -> str:
    """Last whitespace-separated token of a command's ``invocation`` string.

    E.g. ``"python -m prompt_registry list"`` -> ``"list"``.
    """
    invocation = command.get("invocation") or ""
    parts = invocation.split()
    return parts[-1] if parts else ""


def _find_command(commands: List[Dict[str, Any]], token: str) -> Optional[Dict[str, Any]]:
    """First entry in ``commands`` whose name, any alias, or the last
    invocation token case-insensitively matches ``token`` — else ``None``.
    """
    needle = token.lower()
    for command in commands:
        keys = [command.get("name", ""), *command.get("aliases", []), _last_invocation_token(command)]
        if any(str(key).lower() == needle for key in keys):
            return command
    return None


def resolve_command(name: str, subcommand: Optional[str] = None, *, path: Optional[Path] = None) -> Optional[dict]:
    """Resolve a manifest command (and optional subcommand) by name/alias.

    Mirrors the frontend's commandParse.ts::resolveCommand matching rules so
    server-side execution only ever runs a target the manifest actually
    lists -- never a client-supplied path/module string. Case-insensitive
    match against `name`, any `aliases` entry, or the last whitespace token
    of `invocation`. Returns the resolved leaf CommandSpec dict (has
    `.invocation`, ready for argv-building), or None if not found.
    """
    manifest = command_manifest(path)
    command = _find_command(manifest["commands"], name)
    if command is None:
        return None
    if not subcommand:
        return command
    return _find_command(command.get("subcommands", []), subcommand)
