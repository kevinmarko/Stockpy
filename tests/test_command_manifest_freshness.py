"""Freshness gate: the committed manifest's strategy_registry /
options_strategy_registry / paper_broker_options_strategy_registry must match
their live registries exactly.

cli_introspect/command_manifest.json is a committed, offline-built artifact
(scripts/build_command_manifest.py). Its ``strategy_registry`` field is the
single source of truth the webapp Commands screen's --strategy/--strategies
pickers read (see pilots/commands.py); ``options_strategy_registry`` is the
same for validation.harness's bulk (--strategies) mode, which only supports
options strategies. ``paper_broker_options_strategy_registry`` is a third,
narrower list -- the STRATEGY_REGISTRY subset that simulates real,
production-gated (VRP/IVR/VIX/trend-bias) options directives via
validation/options_selling_backtest.py, as opposed to
options_strategy_registry's naive/ungated STANDARD_OPTIONS_STRATEGIES shapes
-- driving the Commands screen's separate "paper-broker realistic" quick
action. If a strategy is added to or removed from any of the three live
registries without regenerating the manifest, the webapp silently drifts out
of sync -- these tests catch all three.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.refresh_validations import PAPER_BROKER_OPTIONS_STRATEGIES, STRATEGY_REGISTRY
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


def test_manifest_paper_broker_options_strategy_registry_matches_live_list_exactly():
    data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_strategies = set(data.get("paper_broker_options_strategy_registry", []))
    live_strategies = set(PAPER_BROKER_OPTIONS_STRATEGIES)

    missing_from_manifest = live_strategies - manifest_strategies
    stale_in_manifest = manifest_strategies - live_strategies

    assert not missing_from_manifest and not stale_in_manifest, (
        "cli_introspect/command_manifest.json's paper_broker_options_strategy_registry has "
        "drifted from scripts.refresh_validations.PAPER_BROKER_OPTIONS_STRATEGIES -- "
        "regenerate it with `python scripts/build_command_manifest.py`.\n"
        f"Missing from manifest (in PAPER_BROKER_OPTIONS_STRATEGIES but not the file): {sorted(missing_from_manifest)}\n"
        f"Stale in manifest (in the file but not PAPER_BROKER_OPTIONS_STRATEGIES): {sorted(stale_in_manifest)}"
    )


def test_paper_broker_options_strategies_are_all_real_strategy_registry_entries():
    """Every name in PAPER_BROKER_OPTIONS_STRATEGIES must also be a real
    STRATEGY_REGISTRY key -- this list exists specifically to point the
    webapp at runnable `scripts.refresh_validations --strategies` names, so a
    typo or a removed adapter here must fail loudly rather than silently
    offering a dead strategy name in the UI."""
    unknown = set(PAPER_BROKER_OPTIONS_STRATEGIES) - set(STRATEGY_REGISTRY.keys())
    assert not unknown, (
        f"PAPER_BROKER_OPTIONS_STRATEGIES contains name(s) not in STRATEGY_REGISTRY: {sorted(unknown)}"
    )
