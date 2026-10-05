"""Freshness gate: the committed manifest's strategy_registry must match the
live STRATEGY_REGISTRY exactly.

cli_introspect/command_manifest.json is a committed, offline-built artifact
(scripts/build_command_manifest.py). Its ``strategy_registry`` field is the
single source of truth the webapp Commands screen's --strategy/--strategies
pickers read (see pilots/commands.py). If a strategy is added to or removed
from the live registry without regenerating the manifest, the webapp silently
drifts out of sync -- this test catches it.

The ``options_strategy_registry`` / ``paper_broker_options_strategy_registry``
keys were dropped with the options desk (2026-09, step 4a); a test below pins
their absence.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.refresh_validations import STRATEGY_REGISTRY

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "cli_introspect" / "command_manifest.json"


def test_manifest_strategy_registry_matches_live_registry_exactly():
    data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_strategies = set(data.get("strategy_registry", []))
    live_strategies = set(STRATEGY_REGISTRY.keys())

    missing_from_manifest = live_strategies - manifest_strategies
    stale_in_manifest = manifest_strategies - live_strategies

    assert not missing_from_manifest and not stale_in_manifest, (
        "cli_introspect/command_manifest.json's strategy_registry has drifted from "
        "scripts.refresh_validations.STRATEGY_REGISTRY -- regenerate it with "
        "`python scripts/build_command_manifest.py`.\n"
        f"Missing from manifest (in STRATEGY_REGISTRY but not the file): {sorted(missing_from_manifest)}\n"
        f"Stale in manifest (in the file but not STRATEGY_REGISTRY): {sorted(stale_in_manifest)}"
    )


def test_manifest_has_no_options_registries():
    data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert "options_strategy_registry" not in data
    assert "paper_broker_options_strategy_registry" not in data


def test_manifest_commands_match_targets():
    from cli_introspect.targets import TARGETS
    data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_commands = {cmd["name"] for cmd in data.get("commands", [])}
    # dead_letters is a plain list[str] of TARGETS names (build_command_manifest.py's
    # own type annotation), not a list of dicts -- unlike `commands`.
    manifest_dead_letters = set(data.get("dead_letters", []))
    all_manifest_names = manifest_commands | manifest_dead_letters
    
    target_names = {t.name for t in TARGETS}
    
    missing_from_manifest = target_names - all_manifest_names
    stale_in_manifest = all_manifest_names - target_names
    
    assert not missing_from_manifest and not stale_in_manifest, (
        "cli_introspect/command_manifest.json commands/dead_letters has drifted "
        "from cli_introspect.targets.TARGETS -- regenerate it with "
        "`python3 scripts/build_command_manifest.py`.\n"
        f"Missing from manifest (in TARGETS but not the file): {sorted(missing_from_manifest)}\n"
        f"Stale in manifest (in the file but not TARGETS): {sorted(stale_in_manifest)}"
    )
