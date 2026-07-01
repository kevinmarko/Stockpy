"""
tests/conftest.py
===================
Shared pytest fixtures for the tests/ package. Kept deliberately small and
opt-in (no test-suite-wide autouse fixtures here) -- each fixture below is
requested explicitly by the files that need it, either directly as a test
parameter or wrapped in a local `@pytest.fixture(autouse=True)` shim scoped
to just that file/class. This avoids silently changing behavior for the
~150 other test files that never asked for it.
"""

from __future__ import annotations

from unittest import mock

import pytest


@pytest.fixture
def disable_historical_store():
    """Disable settings.HISTORICAL_STORE_ENABLED for the duration of a test.

    Several engines (MacroEngine.compute_hmm_risk_on_probability,
    ProcessingEngine.calculate_fundamental_metrics, main_orchestrator's
    run_pipeline) route reads/writes through the real, on-disk
    HistoricalStore whenever this setting is left at its production
    default (True) -- the well-documented "HISTORICAL_STORE_ENABLED trap"
    (see CLAUDE.md and the item #7a pollution-leak root-cause). Request
    this fixture directly for a single test:

        def test_foo(self, engine, disable_historical_store):
            ...

    or wrap it in a local autouse fixture for an entire file/class whose
    tests all need it:

        @pytest.fixture(autouse=True)
        def _auto_disable_historical_store(disable_historical_store):
            pass
    """
    with mock.patch("settings.settings.HISTORICAL_STORE_ENABLED", False):
        yield
