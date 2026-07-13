"""
tests/test_strategy_engine.py
=============================
Owning end-to-end suite for ``strategy_engine.py`` (the core trade-signal
generator). Fills the surfaces that had only indirect coverage from other
files, WITHOUT duplicating them.

Coverage:
  1. TestEvaluateSecurityContract  — the full ``evaluate_security()`` return-dict
     contract (all documented keys + types; buyRange AND sellRange present;
     Score_Components weighted & finite; Symbol/Price echo the bar).
  2. TestSizingWiring              — the Kelly-Target sizing pipeline inside
     evaluate_security: the HMM ``regime_multiplier`` and ``meta_label_composite``
     actually SCALE the sizing (Post = clamp(Pre * regime * meta)); neutral
     multiplier when the HMM is absent; the final clamp to
     ``settings.MAX_POSITION_WEIGHT``.
  3. TestKillSwitchOverlay         — the hard BUY/STRONG BUY -> HOLD override that
     fires when ``macro.killSwitch`` is active (differential test).
  4. TestApplyTacticalRanges       — the pure ``apply_tactical_ranges`` helper's
     buy/hold/reduce branches, Graham cap, support>resistance fallback, stop clamp.
  5. TestGenerateRobinhoodAdvice   — the ``_generate_robinhood_advice`` helper's
     no-position / accumulate / maintain / trim branches and break-even adjustment.
  6. TestSelectOptionsOverlay      — every branch of the ``_select_options_overlay``
     derivatives matrix (covered call / cash-secured put / iron condor /
     defensive covered call / protective collar; yield vs non-yield split).

Files checked to AVOID duplication (their surfaces are deliberately not re-tested here):
  - tests/test_sell_side_range.py       (owns apply_sell_side_range + sellRange schema)
  - tests/test_kelly_no_history.py      (owns _calculate_kelly_sizing vol-target fallback + clamp math)
  - tests/test_kelly_per_strategy.py    (owns per-strategy bootstrap Kelly + path tags)
  - tests/test_regime_multiplier.py     (owns the RegimeMultiplierSignal module in isolation)
  - tests/test_quantitative_models.py   (owns action-signal thresholds, buyRange strings, Kelly values)
  - tests/test_signal_parity.py         (owns refactor-vs-legacy score/action parity)

All tests are fully OFFLINE (every input is supplied; no network). An in-memory
``TransactionsStore`` is injected everywhere per this repo's documented DI pattern
so sizing never depends on the on-disk DB.
"""
from __future__ import annotations

import math
import re
from datetime import datetime

import pytest

from settings import settings
from dto_models import (
    MarketBarDTO,
    FundamentalDataDTO,
    MacroEconomicDTO,
    RobinhoodPositionDTO,
)
from strategy_engine import StrategyEngine, apply_tactical_ranges
from transactions_store import TransactionsStore


# ---------------------------------------------------------------------------
# Determinism fixtures (repo conventions)
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _pin_signal_weights(monkeypatch):
    """Pin SIGNAL_WEIGHTS to the declared defaults so action-signal assertions are
    stable regardless of whatever the local ``.env`` in this checkout has tuned
    (operator-customized weights via the Strategy Matrix tab are a legitimate
    deployment state). Mirrors the fix in tests/test_signal_parity.py and
    tests/test_quantitative_models.py's parity counterparts.
    """
    monkeypatch.setattr(
        settings, "SIGNAL_WEIGHTS", type(settings)(_env_file=None).SIGNAL_WEIGHTS
    )


@pytest.fixture(autouse=True)
def _auto_disable_historical_store(disable_historical_store):
    """Keep any DB-backed engine path off the on-disk store (harmless for the pure
    strategy engine, but matches the repo-wide 'HISTORICAL_STORE_ENABLED trap' guard)."""
    yield


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _engine() -> StrategyEngine:
    """StrategyEngine with an injected in-memory transactions store (empty -> the
    vol-target sizing fallback path, which is deterministic for a given garch_vol)."""
    return StrategyEngine(transactions_store=TransactionsStore(db_url="sqlite:///:memory:"))


def _engine_warm_vol_target() -> StrategyEngine:
    """StrategyEngine whose store is warmed to the WS3 scale-in ceiling.

    Seeds 30 all-winning closed trades: the aggregate payoff ratio b is
    undefined (no losses) so sizing still takes the vol-target fallback, but with
    n_trades=30 the WS3 cold-start scale-in factor is exactly 1.0 -- i.e. the
    pre-regime weight equals the UN-scaled vol-target weight. Used by the sizing-
    wiring tests, whose assertions verify the regime-multiplier/clamp arithmetic
    layered on top of a full (un-ramped) vol-target weight."""
    store = TransactionsStore(db_url="sqlite:///:memory:")
    ts = datetime.now()
    for _i in range(30):
        _tid = store.record_trade(
            symbol="SEED", side="long", entry_ts=ts, entry_price=100.0, shares=1.0
        )
        store.close_trade(_tid, exit_ts=ts, exit_price=110.0)
    return StrategyEngine(transactions_store=store)


def _bar(ticker: str = "JNJ", close: float = 157.50) -> MarketBarDTO:
    # High/low chosen wide enough that the DTO does not clamp open/close.
    return MarketBarDTO(datetime.now(), ticker, close - 2.5, close + 0.5, close - 3.0, close, 4_500_000)


def _fund(sector: str = "Healthcare") -> FundamentalDataDTO:
    return FundamentalDataDTO(
        ticker="JNJ", company_name="Johnson & Johnson", sector=sector,
        pe_ratio=16.5, pb_ratio=1.45, book_value=110.00, eps_trailing=9.50,
        dividend_yield=0.0310, dividend_growth_rate=0.065, payout_ratio=0.52,
    )


def _macro_riskon(vix: float = 15.0, hmm=None) -> MacroEconomicDTO:
    # Low credit spread + non-inverted curve => RISK ON. VIX/hmm are the knobs
    # the sizing/killSwitch tests turn without disturbing the base regime.
    return MacroEconomicDTO(
        yield_curve_10y_2y=0.45, high_yield_oas=2.50, inflation_rate=2.10,
        nominal_10y=4.0, vix_value=vix, hmm_risk_on_probability=hmm,
    )


# Regexes locking in the tactical-range string contracts.
_BUY_ZONE_RE = re.compile(r"^Buy Zone: \$([0-9]+\.[0-9]{2}) - \$([0-9]+\.[0-9]{2})$")
_HOLD_RANGE_RE = re.compile(r"^Hold Range: \$([0-9]+\.[0-9]{2}) - \$([0-9]+\.[0-9]{2})$")
_TRIM_RE = re.compile(r"^Trim @ \$([0-9]+\.[0-9]{2}) \| Stop @ \$([0-9]+\.[0-9]{2})$")


# ===========================================================================
# 1. evaluate_security full return contract
# ===========================================================================
class TestEvaluateSecurityContract:
    """Pins the complete public shape of evaluate_security()'s return dict."""

    _EXPECTED_KEYS = {
        "Symbol", "Price", "Action Signal", "Advice", "Actionable Advice Signal",
        "Score", "Kelly Target", "Score_Components", "Meta_Label_Composite",
        "Regime_Multiplier", "Kelly_Target_Pre_Regime", "Kelly_Target_Post_Regime",
        "GARCH_Vol", "Option Strategy", "buyRange", "sellRange",
        "Robinhood Shares", "Robinhood Avg Cost", "Robinhood Dividends",
        "Robinhood Advice", "Strategy Explainer Notes",
    }

    def _run(self):
        return _engine().evaluate_security(
            bar=_bar(), fundamentals=_fund(), macro=_macro_riskon(),
            forecast_price=168.00, trend_strength=72.0, atr=2.50, garch_vol=0.20,
        )

    def test_returns_full_key_set(self):
        out = self._run()
        assert self._EXPECTED_KEYS.issubset(out.keys()), (
            f"missing keys: {self._EXPECTED_KEYS - set(out.keys())}"
        )

    def test_core_field_types(self):
        out = self._run()
        assert isinstance(out["Symbol"], str) and out["Symbol"] == "JNJ"
        assert isinstance(out["Action Signal"], str)
        assert isinstance(out["Score"], int)
        assert isinstance(out["Kelly Target"], float)
        assert isinstance(out["Score_Components"], dict)
        assert isinstance(out["Strategy Explainer Notes"], str)

    def test_buy_and_sell_ranges_present_and_nonempty(self):
        out = self._run()
        assert isinstance(out["buyRange"], str) and out["buyRange"]
        assert isinstance(out["sellRange"], str) and out["sellRange"]

    def test_price_echoes_bar_close(self):
        out = self._run()
        assert out["Price"] == pytest.approx(157.50)

    def test_score_components_are_finite_floats(self):
        out = self._run()
        comps = out["Score_Components"]
        assert all(isinstance(v, float) and math.isfinite(v) for v in comps.values())

    def test_action_signal_is_a_known_bucket(self):
        out = self._run()
        assert out["Action Signal"] in {"STRONG BUY", "BUY", "HOLD", "RISK REDUCE"}


# ===========================================================================
# 2. Sizing wiring: regime multiplier + meta composite + clamp
# ===========================================================================
class TestSizingWiring:
    """The Kelly Target pipeline: Pre -> * regime_multiplier * meta_composite -> clamp."""

    @pytest.fixture(autouse=True)
    def _neutralize_meta_registry(self):
        """Order-independence guard: the global singleton
        ``ml.meta_labeling.global_meta_registry`` persists across test files, and
        other suites (e.g. tests/test_main_orchestrator.py's full-pipeline run)
        may register a real MetaLabeler into it. A stray labeler makes
        ``SignalAggregator`` apply the meta hard-gate and force
        ``meta_label_composite`` to 0.0, zeroing Kelly Target and breaking these
        tests' ``Post = Pre * regime * meta`` arithmetic. Snapshot the registry's
        backing dict, clear it so ``meta_label_composite`` stays neutral (1.0),
        then restore the exact prior state afterwards (never leaking OUR change
        either). The registry exposes no ``clear()``; mutate ``_labelers`` in
        place so the singleton identity the aggregator imported is preserved.
        """
        from ml.meta_labeling import global_meta_registry

        snapshot = dict(global_meta_registry._labelers)
        global_meta_registry._labelers.clear()
        try:
            yield
        finally:
            global_meta_registry._labelers.clear()
            global_meta_registry._labelers.update(snapshot)

    def _run(self, *, garch_vol=0.20, hmm=None):
        # Warm store (WS3 scale-in factor 1.0) so the pre-regime weight is the
        # full un-ramped vol-target weight these wiring assertions expect.
        return _engine_warm_vol_target().evaluate_security(
            bar=_bar(), fundamentals=_fund(), macro=_macro_riskon(hmm=hmm),
            forecast_price=168.00, trend_strength=72.0, atr=2.50, garch_vol=garch_vol,
        )

    def test_vol_target_fallback_sets_pre_regime_weight(self):
        """Warm store (scale-in 1.0) + garch_vol=0.20 -> vol-target fallback = 0.10/0.20 = 0.5 (Pre-regime)."""
        out = self._run(garch_vol=0.20, hmm=None)
        assert out["Kelly_Target_Pre_Regime"] == pytest.approx(0.5, rel=1e-6)

    def test_neutral_multiplier_when_hmm_absent(self):
        out = self._run(hmm=None)
        assert out["Regime_Multiplier"] == pytest.approx(1.0)
        assert out["Kelly_Target_Post_Regime"] == pytest.approx(out["Kelly_Target_Pre_Regime"])

    def test_regime_multiplier_scales_kelly_target(self):
        """hmm_risk_on_probability=0.5 -> Regime_Multiplier==0.5, Post = Pre * 0.5."""
        out = self._run(garch_vol=0.20, hmm=0.5)
        assert out["Regime_Multiplier"] == pytest.approx(0.5, rel=1e-6)
        assert out["Kelly_Target_Pre_Regime"] == pytest.approx(0.5, rel=1e-6)
        assert out["Kelly_Target_Post_Regime"] == pytest.approx(0.25, rel=1e-6)
        # Kelly Target is the post-regime figure.
        assert out["Kelly Target"] == pytest.approx(out["Kelly_Target_Post_Regime"])

    def test_meta_composite_is_neutral_and_wired(self):
        """Default registry has no MetaLabelers -> composite is exactly 1.0, and the
        Post value equals the clamped product of BOTH multipliers (identity check)."""
        out = self._run(garch_vol=0.20, hmm=0.5)
        meta = out["Meta_Label_Composite"]
        assert meta == pytest.approx(1.0)
        expected = max(
            0.0,
            min(
                out["Kelly_Target_Pre_Regime"] * out["Regime_Multiplier"] * meta,
                settings.MAX_POSITION_WEIGHT,
            ),
        )
        assert out["Kelly_Target_Post_Regime"] == pytest.approx(expected, rel=1e-9)

    def test_kelly_target_clamped_to_max_position_weight(self):
        """garch_vol=0.01 -> vol-target 0.10/0.01=10 capped at MAX_LEVERAGE(2.0); the
        MAX_POSITION_WEIGHT(1.0) clamp inside _calculate_kelly_sizing brings Pre to 1.0,
        so the surfaced Kelly Target is 1.0 (never 2.0)."""
        out = self._run(garch_vol=0.01, hmm=None)
        assert out["Kelly_Target_Pre_Regime"] == pytest.approx(settings.MAX_POSITION_WEIGHT)
        assert out["Kelly Target"] == pytest.approx(1.0)
        assert out["Kelly Target"] <= settings.MAX_POSITION_WEIGHT + 1e-12

    def test_kelly_target_never_negative(self):
        out = self._run(garch_vol=0.20, hmm=0.0)  # multiplier 0.0 -> Post 0.0
        assert out["Kelly Target"] >= 0.0
        assert out["Kelly_Target_Post_Regime"] == pytest.approx(0.0)


# ===========================================================================
# 3. killSwitch hard overlay override
# ===========================================================================
class TestKillSwitchOverlay:
    """macro.killSwitch forces STRONG BUY/BUY -> HOLD inside evaluate_security."""

    def _run(self, macro):
        return _engine().evaluate_security(
            bar=_bar(), fundamentals=_fund(), macro=macro,
            forecast_price=168.00, trend_strength=72.0, atr=2.50, garch_vol=0.20,
        )

    def test_killswitch_forces_bullish_to_hold(self):
        # Baseline: benign macro on the bullish setup yields an accumulate signal.
        benign = self._run(_macro_riskon(vix=15.0))
        assert benign["Action Signal"] in {"BUY", "STRONG BUY"}, (
            f"baseline should be bullish, got {benign['Action Signal']!r}"
        )
        # Same inputs, VIX=35 -> killSwitch True (vix>30) -> forced HOLD.
        killed = self._run(_macro_riskon(vix=35.0))
        assert killed["Action Signal"] == "HOLD"
        assert "Systemic Risk Overlay" in killed["Advice"]

    def test_killswitch_property_actually_active(self):
        """Sanity: the macro used above truly has killSwitch True (vix>30)."""
        assert _macro_riskon(vix=35.0).killSwitch is True
        assert _macro_riskon(vix=15.0).killSwitch is False


# ===========================================================================
# 4. apply_tactical_ranges (pure helper)
# ===========================================================================
class TestApplyTacticalRanges:
    def test_buy_zone_branch(self):
        out = apply_tactical_ranges("BUY", 100.00, 2.00, 95.00, 110.00, graham_val=0.0)
        m = _BUY_ZONE_RE.match(out)
        assert m is not None, f"unexpected: {out!r}"
        support, resistance = map(float, m.groups())
        assert support == pytest.approx(100.00 - 1.5 * 2.00)   # 97.00
        assert resistance == pytest.approx(100.00 - 0.5 * 2.00)  # 99.00
        assert support < resistance

    def test_strong_buy_uses_same_buy_zone_branch(self):
        out = apply_tactical_ranges("STRONG BUY", 50.00, 1.00, 48.00, 55.00)
        assert _BUY_ZONE_RE.match(out), f"STRONG BUY should share the Buy Zone branch: {out!r}"

    def test_graham_number_caps_resistance(self):
        # graham_val (98.20) < computed resistance (99.00) -> resistance clamped to graham.
        out = apply_tactical_ranges("BUY", 100.00, 2.00, 95.00, 110.00, graham_val=98.20)
        _, resistance = map(float, _BUY_ZONE_RE.match(out).groups())
        assert resistance == pytest.approx(98.20)

    def test_support_above_resistance_falls_back(self):
        # A large graham cap can drag resistance below support -> fallback S=price*0.95, R=price.
        out = apply_tactical_ranges("BUY", 100.00, 2.00, 95.00, 110.00, graham_val=90.00)
        support, resistance = map(float, _BUY_ZONE_RE.match(out).groups())
        assert support == pytest.approx(95.00)   # 100 * 0.95
        assert resistance == pytest.approx(100.00)

    def test_hold_range_uses_chandelier_when_available(self):
        out = apply_tactical_ranges("HOLD", 100.00, 2.00, 96.00, 0.0)
        support, resistance = map(float, _HOLD_RANGE_RE.match(out).groups())
        assert support == pytest.approx(96.00)          # chandelier_long
        assert resistance == pytest.approx(104.00)      # price + 2*ATR

    def test_hold_range_falls_back_to_atr_support(self):
        out = apply_tactical_ranges("HOLD", 100.00, 2.00, 0.0, 0.0)
        support, resistance = map(float, _HOLD_RANGE_RE.match(out).groups())
        assert support == pytest.approx(96.00)          # price - 2*ATR
        assert resistance == pytest.approx(104.00)

    def test_risk_reduce_branch(self):
        out = apply_tactical_ranges("RISK REDUCE", 100.00, 2.00, 94.00, 0.0)
        trim, stop = map(float, _TRIM_RE.match(out).groups())
        assert trim == pytest.approx(100.00 + 0.5 * 2.00)  # 101.00
        assert stop == pytest.approx(94.00)                # chandelier_long

    def test_risk_reduce_stop_falls_back_to_atr(self):
        out = apply_tactical_ranges("RISK REDUCE", 100.00, 2.00, 0.0, 0.0)
        _, stop = map(float, _TRIM_RE.match(out).groups())
        assert stop == pytest.approx(100.00 - 1.0 * 2.00)  # 98.00

    def test_risk_reduce_stop_clamped_to_floor(self):
        # Pathological ATR would drive the stop negative -> clamp to >= $0.01.
        out = apply_tactical_ranges("RISK REDUCE", 1.00, 10.00, 0.0, 0.0)
        _, stop = map(float, _TRIM_RE.match(out).groups())
        assert stop >= 0.01


# ===========================================================================
# 5. _generate_robinhood_advice (holding-aware helper)
# ===========================================================================
class TestGenerateRobinhoodAdvice:
    def _pos(self, shares=10.0, avg=100.0, divs=50.0) -> RobinhoodPositionDTO:
        return RobinhoodPositionDTO("JNJ", shares=shares, average_cost=avg, total_dividends=divs)

    def test_no_position_defers_to_system_signal(self):
        eng = _engine()
        out = eng._generate_robinhood_advice("STRONG BUY", 104.5, self._pos(shares=0.0))
        assert "No current position" in out

    def test_buy_signal_recommends_accumulate(self):
        eng = _engine()
        # break-even = 100 - (50/10) = 95; price 104.5 -> +10%.
        out = eng._generate_robinhood_advice("BUY", 104.5, self._pos())
        assert "Accumulate more" in out
        assert "%" in out

    def test_strong_buy_also_accumulates(self):
        eng = _engine()
        out = eng._generate_robinhood_advice("STRONG BUY", 104.5, self._pos())
        assert "Accumulate more" in out

    def test_hold_signal_maintains_and_cites_shares(self):
        eng = _engine()
        out = eng._generate_robinhood_advice("HOLD", 104.5, self._pos(shares=10.0))
        assert "Maintain existing" in out
        assert "10" in out  # share count surfaced

    def test_risk_reduce_recommends_trimming(self):
        eng = _engine()
        out = eng._generate_robinhood_advice("RISK REDUCE", 104.5, self._pos())
        assert "trimming" in out.lower()

    def test_break_even_uses_dividend_adjusted_cost(self):
        eng = _engine()
        # With divs=0 -> break-even 100 -> price 100 is flat (~0.00%); with divs=50 -> break-even
        # 95 -> price 100 is +5.26%. The two must differ, proving the dividend adjustment applies.
        flat = eng._generate_robinhood_advice("BUY", 100.0, self._pos(divs=0.0))
        adj = eng._generate_robinhood_advice("BUY", 100.0, self._pos(divs=50.0))
        assert flat != adj


# ===========================================================================
# 6. _select_options_overlay (derivatives matrix)
# ===========================================================================
class TestSelectOptionsOverlay:
    def _select(self, signal, is_uptrend, sector="Technology", price=100.0, atr=2.0):
        eng = _engine()
        bar = _bar("XYZ", price)
        fund = _fund(sector=sector)
        return eng._select_options_overlay(bar, fund, signal, is_uptrend, atr)

    def test_buy_uptrend_non_yield_is_covered_call_delta20(self):
        strat, detail = self._select("BUY", True, sector="Technology")
        assert "OTM Covered Call" in strat and "delta-20" in strat
        assert "$" in detail

    def test_buy_uptrend_yield_asset_is_covered_call_delta15(self):
        strat, _ = self._select("BUY", True, sector="Real Estate (mREIT)")
        assert "OTM Covered Call" in strat and "delta-15" in strat

    def test_buy_downtrend_is_cash_secured_put(self):
        strat, _ = self._select("BUY", False, sector="Technology")
        assert strat == "Cash Secured Put"

    def test_hold_is_iron_condor(self):
        strat, _ = self._select("HOLD", True, sector="Technology")
        assert "Iron Condor" in strat

    def test_risk_reduce_yield_asset_is_defensive_covered_call(self):
        strat, _ = self._select("RISK REDUCE", False, sector="Financial Services")
        assert strat == "Defensive Covered Call"

    def test_risk_reduce_non_yield_is_protective_collar(self):
        strat, _ = self._select("RISK REDUCE", False, sector="Technology")
        assert strat == "Protective Collar"
