"""Unit tests for the static Pilot catalog (``pilots/catalog.py``).

Guards Decision D1: every Pilot's weight keys are REAL ``settings.SIGNAL_WEIGHTS``
keys and every non-None ``validation_strategy_id`` is a REAL ``STRATEGY_REGISTRY``
key — so a Pilot can never advertise an invented module or another strategy's
backtest.
"""
from __future__ import annotations

import re
from pathlib import Path

from scripts.refresh_validations import STRATEGY_REGISTRY
from settings import settings

from pilots import Pilot, get_pilot, list_pilots
from pilots.catalog import PILOTS, OPTIONS_DIRECTIVE_STRATEGY_TO_PILOT_ID


def test_catalog_non_empty():
    assert len(list_pilots()) >= 1
    assert list_pilots() == PILOTS  # marketplace order preserved


def test_pilot_ids_unique():
    ids = [p.id for p in list_pilots()]
    assert len(ids) == len(set(ids)), f"duplicate Pilot ids: {ids}"


def test_pilot_ids_kebab_case():
    for p in list_pilots():
        assert p.id == p.id.lower(), f"{p.id!r} not lowercase"
        assert " " not in p.id and "_" not in p.id, f"{p.id!r} not kebab-case"


def test_weights_keys_are_real_signal_modules():
    valid = set(settings.SIGNAL_WEIGHTS)
    for p in list_pilots():
        if not p.followable:
            continue
        assert p.weights, f"{p.id!r} has empty weights"
        unknown = set(p.weights) - valid
        assert not unknown, f"{p.id!r} references unknown signal modules: {unknown}"
        for w in p.weights.values():
            assert isinstance(w, (int, float))


def test_validation_ids_are_real_or_none():
    valid = set(STRATEGY_REGISTRY)
    for p in list_pilots():
        if p.validation_strategy_id is not None:
            assert p.validation_strategy_id in valid, (
                f"{p.id!r} joins unknown validation strategy "
                f"{p.validation_strategy_id!r}; known: {sorted(valid)}"
            )


def test_categories_are_known():
    allowed = {
        "Momentum", "Mean Reversion", "Factor", "Blend",
        "Macro", "Risk", "Sentiment", "Forecast", "Options",
    }
    for p in list_pilots():
        assert p.category in allowed, f"{p.id!r} has unknown category {p.category!r}"


def test_descriptions_present():
    for p in list_pilots():
        assert p.name.strip()
        assert len(p.description.strip()) >= 10


def test_pilot_is_frozen():
    p = list_pilots()[0]
    try:
        p.id = "mutated"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Pilot dataclass should be frozen/immutable")


def test_get_pilot_round_trips():
    for p in list_pilots():
        assert get_pilot(p.id) is p
    assert get_pilot("does-not-exist") is None


def test_balanced_blend_matches_full_signal_weights():
    blend = get_pilot("balanced-blend")
    assert blend is not None
    assert blend.weights == dict(settings.SIGNAL_WEIGHTS)


def test_at_least_one_validated_pilot():
    # Sanity: the honest join is actually exercised somewhere.
    assert any(p.validation_strategy_id is not None for p in list_pilots())


def test_non_followable_pilots():
    for p in list_pilots():
        if not p.followable:
            assert p.weights == {}, f"{p.id!r} has weights but is not followable"
            assert len(p.description.strip()) > 0, f"{p.id!r} needs a description"


def test_options_directive_strategy_mapping():
    # Keys must be the exact Title-Case strings technical_options_engine.py's
    # generate_strategy_pricing_matrix writes to directive["Strategy"] --
    # NOT the UPPER_SNAKE_CASE structure_type convention used elsewhere
    # (e.g. pilots/multi_leg_pricing.py's "IRON_CONDOR", a different field).
    # execution/options_paper_executor.py looks up a live directive's
    # strategy string against this dict verbatim, so a wrong-cased key here
    # would silently no-op the normalization for every real trade.
    expected_keys = {
        "Put Credit Spread",
        "Call Credit Spread",
        "Iron Condor",
        "Call Debit Spread",
        "Put Debit Spread",
        "Covered Call",
    }
    assert set(OPTIONS_DIRECTIVE_STRATEGY_TO_PILOT_ID.keys()) == expected_keys

    catalog_ids = {p.id for p in list_pilots()}
    for k, v in OPTIONS_DIRECTIVE_STRATEGY_TO_PILOT_ID.items():
        assert v in catalog_ids, f"Mapped value {v!r} for key {k!r} is not a valid Pilot ID"


def test_options_directive_strategy_mapping_matches_technical_options_engine():
    """Drift guard: every key here must be a real Title-Case ``Strategy``
    value technical_options_engine.py actually assigns to a live directive
    (grepped from its own source, not re-typed by hand), so this dict can
    never silently drift out of sync with the live engine's strings again.
    """
    src = Path(__file__).resolve().parent.parent / "technical_options_engine.py"
    text = src.read_text(encoding="utf-8")
    real_strategy_strings = set(re.findall(r'directive\["Strategy"\]\s*=\s*"([^"]+)"', text))

    for key in OPTIONS_DIRECTIVE_STRATEGY_TO_PILOT_ID:
        assert key in real_strategy_strings, (
            f"{key!r} is not a real directive['Strategy'] value emitted by "
            f"technical_options_engine.py; real values: {sorted(real_strategy_strings)}"
        )

