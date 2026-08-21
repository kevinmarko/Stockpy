"""Freshness gate: the committed manifest's strategy_registry /
options_strategy_registry must match their live registries exactly.

cli_introspect/command_manifest.json is a committed, offline-built artifact
(scripts/build_command_manifest.py). Its ``strategy_registry`` field is the
single source of truth the webapp Commands screen's --strategy/--strategies
pickers read (see pilots/commands.py); ``options_strategy_registry`` is the
same for validation.harness's bulk (--strategies) mode, which only supports
options strategies. If a strategy is added to or removed from either live
registry without regenerating the manifest, the webapp silently drifts out of
sync -- these tests catch both directions of that drift.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.refresh_validations import STRATEGY_REGISTRY
from validation.options_harness import STANDARD_OPTIONS_STRATEGIES

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


def test_manifest_options_strategy_registry_matches_live_registry_exactly():
    data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_strategies = set(data.get("options_strategy_registry", []))
    live_strategies = set(STANDARD_OPTIONS_STRATEGIES.keys())

    missing_from_manifest = live_strategies - manifest_strategies
    stale_in_manifest = manifest_strategies - live_strategies

    assert not missing_from_manifest and not stale_in_manifest, (
        "cli_introspect/command_manifest.json's options_strategy_registry has drifted "
        "from validation.options_harness.STANDARD_OPTIONS_STRATEGIES -- regenerate it "
        "with `python scripts/build_command_manifest.py`.\n"
        f"Missing from manifest (in STANDARD_OPTIONS_STRATEGIES but not the file): {sorted(missing_from_manifest)}\n"
        f"Stale in manifest (in the file but not STANDARD_OPTIONS_STRATEGIES): {sorted(stale_in_manifest)}"
    )
