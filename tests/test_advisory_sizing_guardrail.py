"""
tests/test_advisory_sizing_guardrail.py
=========================================
Owning suite for engine/advisory.py's OWN guardrail telemetry:
``_compute_kelly_sizing_detailed()`` and ``Recommendation.sizing_was_capped``/
``.sizing_binding_constraint``.

This is advisory's independent sizing path -- deliberately decoupled from
``settings.MAX_POSITION_WEIGHT`` / ``sizing.position_sizer.size_position()``
(see engine/advisory.py's CONFIG "Advisory-layer position size cap" note) --
but it DOES reuse ``sizing.position_sizer.detect_raw_cap_binding()`` /
``clamp_with_binding()`` internally (CONSTRAINT #7), so this file locks in
that the reuse produces the exact same guardrail decisions the hand-rolled
comparisons it replaced would have, across both the Kelly and vol-target
fallback branches. Mirrors tests/test_kelly_no_history.py's direct-call
pattern (an in-memory TransactionsStore, no mocking of the sizing math
itself).
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine.advisory import ADVISORY_MAX_POSITION_PCT, CONFIG, _compute_kelly_sizing_detailed
from sizing.position_sizer import KELLY_CAP, VOL_TARGET_LEVERAGE
from transactions_store import TransactionsStore


@pytest.fixture
def empty_store() -> TransactionsStore:
    return TransactionsStore(db_url="sqlite:///:memory:")


def _seed_trades(store: TransactionsStore, n_wins: int, win_ret: float, n_losses: int, loss_ret: float) -> None:
    now = pd.Timestamp.now("UTC")
    for i in range(n_wins):
        tid = store.record_trade(symbol="AAPL", side="long", entry_ts=now, entry_price=100.0, shares=10.0)
        store.close_trade(tid, exit_ts=now + pd.Timedelta(days=i), exit_price=100.0 * (1 + win_ret))
    for i in range(n_losses):
        tid = store.record_trade(symbol="AAPL", side="long", entry_ts=now, entry_price=100.0, shares=10.0)
        store.close_trade(tid, exit_ts=now + pd.Timedelta(days=n_wins + i), exit_price=100.0 * (1 - loss_ret))


class TestKellyPathCapDetection:
    def test_kelly_cap_binds_when_edge_saturates_it(self, empty_store):
        """p=0.6, b=2.0 -> full Kelly=(0.6*2-0.4)/2=0.4; half-Kelly=0.2 == CONFIG['kelly_cap']
        exactly -- the formula's own cap saturates. A generous max_pct (well
        above kelly_cap) isolates this from the advisory_max_position_pct
        clamp so ONLY the raw-cap detector fires -- see the next test for the
        (more realistic, since CONFIG['max_single_position_pct']=0.05 <
        kelly_cap=0.20) case where BOTH constraints are candidates."""
        _seed_trades(empty_store, n_wins=18, win_ret=0.10, n_losses=12, loss_ret=0.05)
        final_pct, was_capped, binding = _compute_kelly_sizing_detailed(
            garch_vol=0.20, transactions_store=empty_store, max_pct=0.50,
        )
        assert was_capped is True
        assert binding == KELLY_CAP
        assert final_pct == pytest.approx(CONFIG["kelly_cap"])

    def test_advisory_max_position_pct_binds_when_tighter_than_kelly_cap(self, empty_store):
        """Same edge as above, but max_pct is the binding ceiling since
        CONFIG['max_single_position_pct']=0.05 < the raw Kelly-capped 0.20."""
        _seed_trades(empty_store, n_wins=18, win_ret=0.10, n_losses=12, loss_ret=0.05)
        final_pct, was_capped, binding = _compute_kelly_sizing_detailed(
            garch_vol=0.20, transactions_store=empty_store, max_pct=0.05,
        )
        assert was_capped is True
        assert binding == ADVISORY_MAX_POSITION_PCT
        assert final_pct == pytest.approx(0.05)

    def test_no_binding_when_raw_kelly_is_well_under_both_ceilings(self, empty_store):
        """A weak edge (p just above 0.5, modest payoff) -> half-Kelly well
        below CONFIG['kelly_cap'] and below a generous max_pct."""
        _seed_trades(empty_store, n_wins=16, win_ret=0.02, n_losses=14, loss_ret=0.02)
        final_pct, was_capped, binding = _compute_kelly_sizing_detailed(
            garch_vol=0.20, transactions_store=empty_store, max_pct=0.20,
        )
        assert was_capped is False
        assert binding is None
        assert 0.0 < final_pct < 0.05


class TestVolTargetFallbackCapDetection:
    def test_vol_target_leverage_binds_at_low_realized_vol(self, empty_store):
        """No trade history -> vol-target fallback; target_vol(0.10)/garch_vol(0.01)=10.0,
        saturating the fallback's own max_leverage=2.0."""
        final_pct, was_capped, binding = _compute_kelly_sizing_detailed(
            garch_vol=0.01, transactions_store=empty_store, max_pct=5.0,  # generous max_pct so it isn't the binding constraint
        )
        assert was_capped is True
        assert binding == VOL_TARGET_LEVERAGE
        assert final_pct == pytest.approx(2.0)

    def test_advisory_max_position_pct_binds_on_fallback_path_too(self, empty_store):
        final_pct, was_capped, binding = _compute_kelly_sizing_detailed(
            garch_vol=0.01, transactions_store=empty_store, max_pct=0.05,
        )
        assert was_capped is True
        assert binding == ADVISORY_MAX_POSITION_PCT
        assert final_pct == pytest.approx(0.05)

    def test_no_binding_on_fallback_path_under_both_ceilings(self, empty_store):
        """target_vol(0.10)/garch_vol(0.20) = 0.5 raw -- well under
        max_leverage=2.0, and max_pct=0.75 is generous enough not to clamp it."""
        final_pct, was_capped, binding = _compute_kelly_sizing_detailed(
            garch_vol=0.20, transactions_store=empty_store, max_pct=0.75,
        )
        assert was_capped is False
        assert binding is None
        assert final_pct == pytest.approx(0.5)


class TestCannotSizeDegradesCleanly:
    def test_no_history_and_no_vol_returns_uncapped_zero(self, empty_store):
        final_pct, was_capped, binding = _compute_kelly_sizing_detailed(
            garch_vol=None, transactions_store=empty_store, max_pct=0.05,
        )
        assert final_pct == 0.0
        assert was_capped is False
        assert binding is None
