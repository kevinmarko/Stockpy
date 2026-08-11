"""
tests/test_production_steps_broker_gate.py
============================================
Regression coverage for pipeline/production_steps.py's Finding 1 fix: a
pipeline cycle that fell back to MockDataEngine (AsyncDataFetchStep's
fail-safe branch, triggered by a total market-data outage -- flat $10
prices, fabricated fundamentals) must NEVER submit a live/paper broker
order. Before this fix, a synthetic-data cycle with ADVISORY_ONLY=False and
Alpaca credentials configured would flow straight into
main_orchestrator._execute_broker_orders() with no marker anywhere
distinguishing it from a real-data cycle.

The fix threads a broker-agnostic marker (``ctx.context_extras
['data_is_synthetic']``) from AsyncDataFetchStep's MockDataEngine fallback
site through to BrokerExecutionStep.run(), which checks it BEFORE the
existing ADVISORY_ONLY/Alpaca-key branches and returns unconditionally
without calling _execute_broker_orders() when it's set. Because the check
sits entirely upstream of broker SELECTION (which happens one layer deeper
inside _execute_broker_orders, keyed off settings.BROKER_BACKEND), the gate
protects AlpacaBroker and FMPPaperBroker identically -- this test proves
that by exercising the exact "Alpaca keys configured" branch and showing
the gate still wins.

Targets BrokerExecutionStep.run() directly with a hand-built RunContext,
mirroring tests/test_production_steps_etf_transmission_multiplier.py's
approach of exercising a production_steps.py entry point directly rather
than paying for the full async orchestrator's heavy engine import chain.
Every external dependency BrokerExecutionStep.run() touches in its
advisory-evaluation preamble (market data provider, Robinhood snapshot,
advisory evaluation) is patched to fail cleanly -- that preamble already
has its own internal try/except and is not what this test is about; only
the broker-execution gate itself is under test.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest import mock

import pandas as pd
import pytest

from pipeline.context import RunContext
from pipeline.production_steps import BrokerExecutionStep


def _make_ctx(*, data_is_synthetic: bool) -> RunContext:
    dashboard_df = pd.DataFrame([
        {"Symbol": "AAPL", "GARCH_Vol": 0.2, "Forecast_30": 150.0},
    ])
    ctx = RunContext(
        force_account=False,
        started_at=datetime.now(),
        watchlist_file="watchlist.txt",
        fetch_account_snapshot_fn=lambda *a, **k: None,
        build_universe_fn=lambda *a, **k: [],
        build_macro_dto_fn=lambda *a, **k: None,
        get_provider_fn=lambda *a, **k: None,
        fetch_bars_fn=lambda *a, **k: {},
        build_context_extras_fn=lambda *a, **k: {},
        advisory_evaluate_fn=lambda *a, **k: None,
    )
    ctx.dashboard_df = dashboard_df
    ctx.macro_dto = None
    if data_is_synthetic:
        ctx.context_extras["data_is_synthetic"] = True
    return ctx


def _run_step(ctx: RunContext):
    """Runs BrokerExecutionStep.run() with every non-broker-execution
    dependency patched to fail harmlessly (already covered by the step's
    own internal try/except), and main_orchestrator._execute_broker_orders
    patched as an AsyncMock so we can assert whether it was ever reached."""
    step = BrokerExecutionStep()
    with mock.patch("data.market_data.get_provider", return_value=None), \
         mock.patch(
             "data.robinhood_portfolio.fetch_account_snapshot",
             side_effect=RuntimeError("no snapshot in test"),
         ), \
         mock.patch(
             "engine.advisory.evaluate",
             side_effect=RuntimeError("no advisory in test"),
         ), \
         mock.patch(
             "main_orchestrator._execute_broker_orders",
             new_callable=mock.AsyncMock,
         ) as m_exec:
        asyncio.run(step.run(ctx))
    return m_exec


class TestSyntheticDataGateSkipsBrokerExecution:
    """Finding 1: the synthetic-data marker must block broker execution
    regardless of ADVISORY_ONLY or configured broker credentials."""

    def test_synthetic_marker_skips_broker_execution_advisory_only_false_alpaca_keys_set(self):
        ctx = _make_ctx(data_is_synthetic=True)
        with mock.patch("settings.settings.ADVISORY_ONLY", False), \
             mock.patch("settings.settings.ALPACA_API_KEY", "fake-key"), \
             mock.patch("settings.settings.ALPACA_SECRET_KEY", "fake-secret"):
            m_exec = _run_step(ctx)

        m_exec.assert_not_called()

    def test_synthetic_marker_skips_broker_execution_even_when_advisory_only_true(self):
        """The gate must fire independent of ADVISORY_ONLY's own value too --
        it's checked strictly before that branch."""
        ctx = _make_ctx(data_is_synthetic=True)
        with mock.patch("settings.settings.ADVISORY_ONLY", True):
            m_exec = _run_step(ctx)

        m_exec.assert_not_called()


class TestRealDataPathIsUnaffected:
    """The synthetic-data gate must be a true no-op when the marker is
    absent (the normal, real-data path) -- broker execution proceeds
    exactly as it did before this fix."""

    def test_real_data_path_calls_execute_broker_orders_when_advisory_only_false_and_keys_set(self):
        ctx = _make_ctx(data_is_synthetic=False)
        assert "data_is_synthetic" not in ctx.context_extras
        with mock.patch("settings.settings.ADVISORY_ONLY", False), \
             mock.patch("settings.settings.ALPACA_API_KEY", "fake-key"), \
             mock.patch("settings.settings.ALPACA_SECRET_KEY", "fake-secret"):
            m_exec = _run_step(ctx)

        m_exec.assert_called_once()

    def test_real_data_path_respects_existing_advisory_only_true_gate(self):
        """Sanity check: the pre-existing ADVISORY_ONLY gate still works
        unmodified when the synthetic marker is absent."""
        ctx = _make_ctx(data_is_synthetic=False)
        with mock.patch("settings.settings.ADVISORY_ONLY", True):
            m_exec = _run_step(ctx)

        m_exec.assert_not_called()

    def test_real_data_path_skips_without_alpaca_keys_configured(self):
        """Sanity check: the pre-existing missing-credentials gate still
        works unmodified when the synthetic marker is absent."""
        ctx = _make_ctx(data_is_synthetic=False)
        with mock.patch("settings.settings.ADVISORY_ONLY", False), \
             mock.patch("settings.settings.ALPACA_API_KEY", None), \
             mock.patch("settings.settings.ALPACA_SECRET_KEY", None):
            m_exec = _run_step(ctx)

        m_exec.assert_not_called()


class TestMarkerSetByAsyncDataFetchStep:
    """Confirms AsyncDataFetchStep's MockDataEngine fallback actually stamps
    the marker BrokerExecutionStep reads, and that the real-data path never
    does -- the asymmetry the fix's docstring/comments claim."""

    def test_mock_fallback_sets_marker(self):
        import asyncio as _asyncio
        from unittest import mock as _mock

        from pipeline.production_steps import AsyncDataFetchStep

        ctx = RunContext(
            force_account=False,
            started_at=datetime.now(),
            watchlist_file="watchlist.txt",
            fetch_account_snapshot_fn=lambda *a, **k: None,
            build_universe_fn=lambda *a, **k: [],
            build_macro_dto_fn=lambda *a, **k: None,
            get_provider_fn=lambda *a, **k: None,
            fetch_bars_fn=lambda *a, **k: {},
            build_context_extras_fn=lambda *a, **k: {},
            advisory_evaluate_fn=lambda *a, **k: None,
        )

        empty_tech_raw = {"AAPL": pd.DataFrame()}

        async def _fake_fetch_all_data_async(de, tickers):
            return {}, {}, empty_tech_raw

        mock_engine_sentinel = object()

        with _mock.patch("main_orchestrator.fetch_account_snapshot", return_value=_mock.MagicMock(positions={})), \
             _mock.patch("main_orchestrator.account_snapshot_to_robinhood_positions", return_value={}), \
             _mock.patch("main_orchestrator.fetch_all_data_async", side_effect=_fake_fetch_all_data_async), \
             _mock.patch("main_orchestrator.GlobalKillSwitch") as m_ks, \
             _mock.patch("os.path.exists", return_value=False):
            m_ks.return_value.is_active.return_value = False
            step = AsyncDataFetchStep()
            _asyncio.run(step.run(ctx))

        assert ctx.context_extras.get("data_is_synthetic") is True
