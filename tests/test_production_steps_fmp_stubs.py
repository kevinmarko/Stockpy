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
    FMP_FEED_MIN_RESERVATION_SECONDS,
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
    _fmp_next_feed_deadline,
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

    def test_time_consumed_by_analyst_reduces_budget_for_subsequent_feeds(self):
        """Dynamic deadline check: advancing monotonic time past the deadline
        during earlier feed causes subsequent feeds to hit budget_exhausted."""
        import time
        import uuid
        from settings import settings

        sym = f"DYN_SYM_{uuid.uuid4().hex[:8]}"
        df = _df([
            {"Symbol": sym, "sector": "Technology", "Price": 100.0},
        ])
        start_time = 1000.0
        shared_deadline = 1010.0  # 10 second total budget

        current_time = [start_time]

        def fake_monotonic():
            return current_time[0]

        with patch.object(settings, "FMP_ANALYST_ENABLED", True), \
             patch.object(settings, "FMP_EARNINGS_ENABLED", True), \
             patch.object(settings, "FMP_INSIDER_ENABLED", True), \
             patch("time.monotonic", side_effect=fake_monotonic), \
             patch("data.fmp_feeds_company.fetch_analyst_snapshot", return_value={"target_consensus": 150.0}) as mock_analyst, \
             patch("data.fmp_feeds_company.fetch_earnings_rows") as mock_earnings:

            # First feed runs at t=1000, succeeds
            _apply_fmp_analyst(df, deadline=shared_deadline)
            assert mock_analyst.call_count >= 1

            # Time advances past shared deadline (e.g. analyst took 15 seconds)
            current_time[0] = 1015.0

            # Subsequent feed immediately skips network because deadline passed
            _apply_fmp_earnings(df, deadline=shared_deadline)
            mock_earnings.assert_not_called()


class TestFmpNextFeedDeadline:
    """Regression coverage for the insider-starvation bug fixed 2026-08 (PR #737
    follow-up): the original StrategyEvalStep.run deadline-splitting block gave
    analyst (and partially earnings) a real 15s-floor reservation but passed
    insider the raw, unprotected _fmp_total_deadline -- so whenever
    FMP_MAX_SECONDS_PER_CYCLE was configured at or below ~45s (the webapp
    settings slider permits values as low as 1.0) and analyst+earnings both
    consumed their full reservations, insider's deadline was already in the
    past before its loop started and it silently fetched zero symbols, every
    cycle, forever -- directly contradicting this feature's own documented
    "guarantees earnings and insider get at least 15s" claim.

    These tests drive _fmp_next_feed_deadline -- the single choke point every
    feed's sub-deadline now goes through -- with a fully controlled fake
    monotonic clock, simulating the worst case (every feed consumes its
    entire allotted share) at several representative total-budget sizes.
    """

    @staticmethod
    def _worst_case_shares(total_budget: float, min_reservation: float) -> list[float]:
        """Simulate feeds_left=3,2,1 each consuming their FULL allotted share
        (the worst case that starved insider before this fix) and return the
        wall-clock seconds each of the three feeds actually received."""
        clock = [0.0]

        def fake_monotonic():
            return clock[0]

        shares: list[float] = []
        with patch("time.monotonic", side_effect=fake_monotonic):
            total_deadline = 0.0 + total_budget
            for feeds_left in (3, 2, 1):
                before = clock[0]
                deadline = _fmp_next_feed_deadline(total_deadline, min_reservation, feeds_left)
                shares.append(max(0.0, deadline - before))
                clock[0] = min(deadline, total_deadline)  # this feed uses its FULL share
        return shares

    def test_default_budget_still_splits_evenly_three_ways(self):
        """At the shipped default (120s), nothing should change vs. the
        original design: an even 40/40/40 split in the worst case."""
        analyst, earnings, insider = self._worst_case_shares(120.0, FMP_FEED_MIN_RESERVATION_SECONDS)
        assert analyst == pytest.approx(40.0, abs=1e-6)
        assert earnings == pytest.approx(40.0, abs=1e-6)
        assert insider == pytest.approx(40.0, abs=1e-6)

    def test_small_budget_no_longer_starves_insider_to_zero(self):
        """THE regression test: a 30s total budget (well below the 45s = 3x15s
        floor threshold) used to leave insider with a deadline of exactly the
        already-passed total deadline once analyst+earnings both consumed
        their full reservations -- i.e. insider got 0.0s. It must now get a
        fair, non-zero share."""
        analyst, earnings, insider = self._worst_case_shares(30.0, 15.0)
        assert insider > 0.0, "insider must never be fully starved when budget remains"
        # Below the floor*3 threshold, all three degrade to an equal split.
        assert analyst == pytest.approx(10.0, abs=1e-6)
        assert earnings == pytest.approx(10.0, abs=1e-6)
        assert insider == pytest.approx(10.0, abs=1e-6)

    def test_exact_floor_boundary_guarantees_full_reservation_to_all_three(self):
        """At total_budget == min_reservation * 3 (the exact boundary), every
        feed -- including insider -- gets its full 15s floor, not a moment
        less."""
        analyst, earnings, insider = self._worst_case_shares(45.0, 15.0)
        assert analyst == pytest.approx(15.0, abs=1e-6)
        assert earnings == pytest.approx(15.0, abs=1e-6)
        assert insider == pytest.approx(15.0, abs=1e-6)

    def test_tiny_operator_configured_budget_never_zeroes_out_a_feed(self):
        """The webapp FMP settings slider permits FMP_MAX_SECONDS_PER_CYCLE
        down to 1.0 with no warning about a meaningful floor -- confirm even
        an extreme low value degrades to a fair split rather than starving
        any one feed to exactly zero while budget genuinely remains."""
        for total_budget in (1.0, 3.0, 10.0):
            min_reservation = min(FMP_FEED_MIN_RESERVATION_SECONDS, total_budget)
            shares = self._worst_case_shares(total_budget, min_reservation)
            assert all(s > 0.0 for s in shares), (
                f"budget={total_budget}: every feed must get a non-zero share, got {shares}"
            )
            assert sum(shares) == pytest.approx(total_budget, abs=1e-3)

    def test_unused_budget_still_rolls_over_to_later_feeds(self):
        """Preserve the pre-fix 'unused budget rolls over' property: if
        analyst finishes far under its allotted share, earnings/insider must
        see the larger remaining pool, not just their originally-planned
        even split."""
        clock = [0.0]

        def fake_monotonic():
            return clock[0]

        with patch("time.monotonic", side_effect=fake_monotonic):
            total_deadline = 120.0
            analyst_deadline = _fmp_next_feed_deadline(total_deadline, 15.0, 3)
            assert analyst_deadline == pytest.approx(40.0, abs=1e-6)

            # Analyst finishes after using only 5 of its allotted 40 seconds.
            clock[0] = 5.0
            earnings_deadline = _fmp_next_feed_deadline(total_deadline, 15.0, 2)
            earnings_share = earnings_deadline - clock[0]
            # 115s remain, split evenly two ways -> 57.5s, far more than the
            # 40s it would have gotten under a static up-front split.
            assert earnings_share == pytest.approx(57.5, abs=1e-6)

    def test_already_exhausted_budget_degrades_honestly_not_unfairly(self):
        """If the shared budget is genuinely exhausted (not merely small) by
        the time a later feed is reached, that feed correctly gets a
        zero-second share -- this is honest wall-clock-ceiling-reached
        degradation (CONSTRAINT #6 style), distinct from the unfair
        zero-while-budget-remains starvation this fix closes."""
        clock = [30.0]  # already at/past the total deadline

        def fake_monotonic():
            return clock[0]

        with patch("time.monotonic", side_effect=fake_monotonic):
            deadline = _fmp_next_feed_deadline(30.0, 15.0, 1)
            assert deadline - clock[0] == pytest.approx(0.0, abs=1e-6)

    def test_last_feed_always_receives_one_hundred_percent_of_remaining(self):
        """feeds_left == 1 (insider's position) must always resolve to the
        FULL remaining budget, in both the 'plenty left' and 'too small for
        the floor' regimes -- there is nothing left to reserve for after it."""
        for total_budget, elapsed in [(120.0, 90.0), (20.0, 5.0), (5.0, 1.0)]:
            clock = [elapsed]

            def fake_monotonic(_clock=clock):
                return _clock[0]

            with patch("time.monotonic", side_effect=fake_monotonic):
                remaining = total_budget - elapsed
                deadline = _fmp_next_feed_deadline(total_budget, 15.0, 1)
                assert deadline - elapsed == pytest.approx(remaining, abs=1e-6)


class TestStrategyEvalStepFmpDeadlineWiring:
    """Confirms StrategyEvalStep.run's actual call site (not just the isolated
    _fmp_next_feed_deadline helper) wires feeds_left=3,2,1 in the correct
    analyst -> earnings -> insider order, so the guarantee proven above by the
    helper's own unit tests is genuinely reachable from the real orchestration
    path -- the exact gap that let the original starvation bug ship unnoticed
    (the prior test suite only asserted "_apply_fmp_insider(" appears in the
    source text, never that it receives a protected deadline)."""

    def test_run_source_calls_next_feed_deadline_with_descending_feeds_left(self):
        import inspect

        import pipeline.production_steps as production_steps

        src = inspect.getsource(production_steps.StrategyEvalStep.run)
        assert "_fmp_next_feed_deadline(_fmp_total_deadline, _fmp_min_reservation, 3)" in src
        assert "_fmp_next_feed_deadline(_fmp_total_deadline, _fmp_min_reservation, 2)" in src
        assert "_fmp_next_feed_deadline(_fmp_total_deadline, _fmp_min_reservation, 1)" in src
        # The prior unprotected pattern must not reappear.
        assert "deadline=_fmp_total_deadline)" not in src

