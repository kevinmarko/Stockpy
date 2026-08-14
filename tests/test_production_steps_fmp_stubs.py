"""
tests/test_production_steps_fmp_stubs.py
========================================
Unit tests for the four Financial Modeling Prep column writers in
``pipeline/production_steps.py`` -- ``_apply_fmp_analyst`` /
``_apply_fmp_earnings`` / ``_apply_fmp_insider`` / ``_apply_fmp_sector``.

As of wave 0 these are complete, working NO-OPS: every ``FMP_*_ENABLED`` gate
defaults ``False``, so each function NaN-fills its columns and returns having
performed zero I/O. The fetch/populate logic lands in wave 1.

What this file pins is the property that has to hold FOREVER, not just today:
**with the gates off, these eight columns are NaN and no network call is
made.** That is the "flag-off is byte-identical" guarantee for this whole
series, and it is exactly what a wave-1 agent could accidentally break by
hoisting a fetch above its gate check.

Deliberately targets the module-level functions directly rather than going
through ``StrategyEvalStep.run()`` (which imports ``main_orchestrator`` and its
full heavy engine chain at call time) -- the same rationale as
``tests/test_production_steps_sector_heat.py``.

Offline: no network, no marks. ``requests`` is patched at the ``Session``
layer, which is what ``requests.get``/``requests.post`` funnel through, so the
"no I/O" assertion holds regardless of which requests-level helper a future
implementation reaches for.
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from pipeline.production_steps import (
    _FMP_ANALYST_COLUMNS,
    _FMP_EARNINGS_COLUMNS,
    _FMP_ECON_CALENDAR_COLUMNS,
    _FMP_INSIDER_COLUMNS,
    _FMP_SECTOR_COLUMNS,
    _apply_fmp_analyst,
    _apply_fmp_earnings,
    _apply_fmp_econ_calendar,
    _apply_fmp_insider,
    _apply_fmp_sector,
)

# (writer, its columns) -- parametrized so a future sixth feed added without a
# test fails here rather than silently shipping ungated.
_WRITERS = [
    pytest.param(_apply_fmp_analyst, _FMP_ANALYST_COLUMNS, id="analyst"),
    pytest.param(_apply_fmp_earnings, _FMP_EARNINGS_COLUMNS, id="earnings"),
    pytest.param(_apply_fmp_insider, _FMP_INSIDER_COLUMNS, id="insider"),
    pytest.param(_apply_fmp_sector, _FMP_SECTOR_COLUMNS, id="sector"),
    pytest.param(_apply_fmp_econ_calendar, _FMP_ECON_CALENDAR_COLUMNS, id="econ_calendar"),
]

_ALL_FMP_COLUMNS = (
    tuple(_FMP_ANALYST_COLUMNS)
    + tuple(_FMP_EARNINGS_COLUMNS)
    + tuple(_FMP_INSIDER_COLUMNS)
    + tuple(_FMP_SECTOR_COLUMNS)
    + tuple(_FMP_ECON_CALENDAR_COLUMNS)
)


def _df(rows=None):
    if rows is None:
        rows = [
            {"Symbol": "AAPL", "sector": "Technology", "Price": 200.0},
            {"Symbol": "XOM", "sector": "Energy", "Price": 110.0},
        ]
    return pd.DataFrame(rows)


class TestGatesOffIsANoOp:
    """Verifies that with capability gates False, the writers NaN-fill all declared
    columns, make zero I/O, and never raise."""

    @pytest.fixture(autouse=True)
    def _gates_off(self, monkeypatch):
        from settings import settings
        monkeypatch.setattr(settings, "FMP_ANALYST_ENABLED", False)
        monkeypatch.setattr(settings, "FMP_EARNINGS_ENABLED", False)
        monkeypatch.setattr(settings, "FMP_INSIDER_ENABLED", False)
        monkeypatch.setattr(settings, "FMP_SECTOR_SNAPSHOT_ENABLED", False)
        monkeypatch.setattr(settings, "FMP_ECON_CALENDAR_ENABLED", False)

    @pytest.mark.parametrize("writer,columns", _WRITERS)
    def test_columns_are_created_and_all_nan(self, writer, columns):
        df = _df()
        writer(df)
        for col in columns:
            assert col in df.columns, f"{writer.__name__} did not create {col!r}"
            assert df[col].isna().all(), (
                f"{col!r} is not NaN with the gate off -- a fabricated default "
                "(CONSTRAINT #4)."
            )

    @pytest.mark.parametrize("writer,columns", _WRITERS)
    def test_no_network_call(self, writer, columns):
        """Zero I/O with the gate off. Patched at ``Session.request``, the
        chokepoint every ``requests`` helper funnels through."""
        df = _df()
        with patch("requests.sessions.Session.request") as mock_request:
            writer(df)
        mock_request.assert_not_called()

    @pytest.mark.parametrize("writer,columns", _WRITERS)
    def test_never_raises_on_an_empty_universe(self, writer, columns):
        """CONSTRAINT #6. The columns must still exist -- ``DashboardSchema``
        requires every declared column to be present."""
        df = pd.DataFrame([])
        writer(df)  # must not raise
        for col in columns:
            assert col in df.columns

    @pytest.mark.parametrize("writer,columns", _WRITERS)
    def test_never_raises_without_the_symbol_or_sector_column(self, writer, columns):
        """A dashboard frame missing ``Symbol``/``sector`` entirely must
        degrade to an all-NaN column, never raise."""
        df = pd.DataFrame([{"SomethingElse": 1.0}])
        writer(df)  # must not raise
        for col in columns:
            assert col in df.columns
            assert df[col].isna().all()

    def test_all_five_together_leave_all_ten_columns_nan(self):
        """The composed no-op: running all five back to back (the order
        ``StrategyEvalStep.run`` uses) leaves all ten columns NaN."""
        df = _df()
        with patch("requests.sessions.Session.request") as mock_request:
            _apply_fmp_analyst(df)
            _apply_fmp_earnings(df)
            _apply_fmp_insider(df)
            _apply_fmp_sector(df)
            _apply_fmp_econ_calendar(df)
        mock_request.assert_not_called()
        for col in _ALL_FMP_COLUMNS:
            assert col in df.columns
            assert df[col].isna().all()


class TestColumnContract:
    def test_every_declared_column_exists_in_config_column_schema(self):
        """The eight columns these writers produce must all be real
        ``COLUMN_SCHEMA`` keys -- a column with no schema slot is silently
        dropped before reaching the Sheet (the exact failure
        ``tests/test_config.py`` was written to catch)."""
        import config

        schema_keys = set(config.get_internal_keys())
        missing = set(_ALL_FMP_COLUMNS) - schema_keys
        assert not missing, f"FMP writer columns absent from COLUMN_SCHEMA: {sorted(missing)}"

    def test_the_four_column_groups_are_disjoint(self):
        """No column may be written by two feeds -- the second would silently
        overwrite the first."""
        assert len(set(_ALL_FMP_COLUMNS)) == len(_ALL_FMP_COLUMNS)

    def test_earnings_writer_does_not_own_the_shared_earnings_date_column(self):
        """``Earnings_Date`` is the EXISTING news-catalyst column; the FMP
        earnings feed becomes a SECOND source for it rather than declaring it
        as one of its own NaN-filled columns. If it were in
        ``_FMP_EARNINGS_COLUMNS``, the unconditional NaN-fill at the top of
        ``_apply_fmp_earnings`` would blank a date the Finnhub path had
        already resolved this cycle -- a regression, not a feature."""
        assert "Earnings_Date" not in _FMP_EARNINGS_COLUMNS

    def test_gate_off_earnings_writer_leaves_a_preexisting_earnings_date_intact(self):
        from settings import settings
        df = _df()
        df["Earnings_Date"] = ["2026-08-01", ""]
        with patch.object(settings, "FMP_EARNINGS_ENABLED", False):
            _apply_fmp_earnings(df)
        assert list(df["Earnings_Date"]) == ["2026-08-01", ""]


class TestWiredIntoStrategyEvalStep:
    def test_all_four_are_called_from_strategy_eval_step_run(self):
        """Static check: a writer that exists but is never called is a column
        that silently stays NaN forever. Kept as source inspection rather than
        a full ``StrategyEvalStep.run()`` invocation, which would pull in
        ``main_orchestrator``'s whole heavy engine chain."""
        import inspect

        import pipeline.production_steps as ps

        src = inspect.getsource(ps.StrategyEvalStep.run)
        for name in (
            "_apply_fmp_analyst",
            "_apply_fmp_earnings",
            "_apply_fmp_insider",
            "_apply_fmp_sector",
            "_apply_fmp_econ_calendar",
        ):
            assert f"{name}(" in src, f"{name} is never called from StrategyEvalStep.run"

    def test_fmp_writers_run_after_the_earnings_date_writeback(self):
        """Ordering contract: ``_apply_fmp_earnings`` is a SECOND source for
        the shared ``Earnings_Date`` column, so it must run AFTER the
        news-catalyst write-back that populates it -- otherwise the
        news-catalyst assignment would overwrite FMP's value every cycle."""
        import inspect

        import pipeline.production_steps as ps

        src = inspect.getsource(ps.StrategyEvalStep.run)
        assert src.index("ctx.dashboard_df['Earnings_Date'] = \"\"") < src.index(
            "_apply_fmp_earnings("
        )


class TestSharedDeadline:
    """Verifies that the optional deadline parameter enforces a shared wall-clock
    budget across _apply_fmp_analyst, _apply_fmp_earnings, and _apply_fmp_insider."""

    def test_pre_exhausted_shared_deadline_skips_processing(self):
        import time
        from settings import settings

        df = _df([
            {"Symbol": "DEADLINE_SYM_1", "sector": "Technology", "Price": 100.0},
            {"Symbol": "DEADLINE_SYM_2", "sector": "Energy", "Price": 50.0},
        ])
        expired_deadline = time.monotonic() - 10.0

        with patch.object(settings, "FMP_ANALYST_ENABLED", True), \
             patch.object(settings, "FMP_EARNINGS_ENABLED", True), \
             patch.object(settings, "FMP_INSIDER_ENABLED", True), \
             patch("data.fmp_feeds_company.fetch_analyst_snapshot") as mock_analyst, \
             patch("data.fmp_feeds_company.fetch_earnings_rows") as mock_earnings, \
             patch("data.fmp_feeds_market.fetch_insider_stats") as mock_insider:

            _apply_fmp_analyst(df, deadline=expired_deadline)
            _apply_fmp_earnings(df, deadline=expired_deadline)
            _apply_fmp_insider(df, deadline=expired_deadline)

            mock_analyst.assert_not_called()
            mock_earnings.assert_not_called()
            mock_insider.assert_not_called()

            for col in _FMP_ANALYST_COLUMNS + _FMP_EARNINGS_COLUMNS + _FMP_INSIDER_COLUMNS:
                assert df[col].isna().all()

    def test_deadline_none_preserves_local_budget(self):
        """When deadline is None, each writer computes its own local budget."""
        from settings import settings

        df = _df()
        with patch.object(settings, "FMP_ANALYST_ENABLED", False):
            # Flag off + deadline None runs cleanly without error
            _apply_fmp_analyst(df, deadline=None)
            for col in _FMP_ANALYST_COLUMNS:
                assert df[col].isna().all()

