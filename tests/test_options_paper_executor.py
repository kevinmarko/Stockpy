"""Tests for execution/options_paper_executor.py."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

from data.paper_account_store import PaperAccountStore
from execution.options_paper_executor import OptionsPaperExecutor, _calculate_default_expiration
from ml.options_meta_labeler import OptionsMetaLabeler, OptionsTradeFeatureRow, global_options_meta_labeler


def test_calculate_default_expiration():
    exp = _calculate_default_expiration(30)
    assert len(exp) == 10
    assert exp.count("-") == 2


def test_get_actionable_directives_filters_cash_and_wait():
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    mock_directive_cash = {
        "Strategy": "Cash",
        "Action": "Wait",
        "Integrity_OK": True,
        "IVR_Proxy": 30.0,
    }
    mock_directive_pcs = {
        "Strategy": "Put Credit Spread",
        "Action": "Open",
        "Integrity_OK": True,
        "IVR_Proxy": 65.0,
        "True_IVR": 65.0,
        "Net_Premium": 1.50,
        "Trend_Bias": "Bullish",
        # Top-level Short_Strike/Long_Strike/Short_Delta -- always set by the
        # real technical_options_engine.py::build_premium_directive whenever
        # short/long legs exist (see lines ~1172-1177). A prior version of
        # this fixture omitted these, which is exactly why the Bug 1
        # serving-time feature gap (vrp/vix/short_delta/credit_to_width_ratio
        # never copied into the actionable item dict) shipped undetected.
        "Short_Strike": 150.0,
        "Long_Strike": 145.0,
        "Short_Delta": -0.30,
        "Legs": [
            {"Strike": 150.0, "Side": "Short", "Delta": -0.30},
            {"Strike": 145.0, "Side": "Long", "Delta": -0.15},
        ],
    }

    with patch("execution.options_paper_executor._directive_for_symbol") as mock_fetch:
        mock_fetch.side_effect = lambda sym, **kwargs: mock_directive_pcs if sym == "AAPL" else mock_directive_cash
        directives = executor.get_actionable_directives(
            symbols=["AAPL", "SPY"],
            vrp=0.035,
            macro_dto=MagicMock(vix=22.5, market_regime="NORMAL"),
        )

    assert len(directives) == 1
    assert directives[0]["symbol"] == "AAPL"
    assert directives[0]["strategy"] == "Put Credit Spread"
    assert directives[0]["net_premium"] == 1.50

    # Bug 1 fix: the four real Stage 4 ML Meta-Labeler inference features must
    # actually be populated from the live vrp/macro_dto/directive data, not
    # silently left absent (which would trigger the model's hardcoded
    # constant defaults on every live prediction).
    assert directives[0]["vrp"] == 0.035
    assert directives[0]["vix"] == 22.5
    assert directives[0]["short_delta"] == pytest.approx(0.30)
    assert directives[0]["credit_to_width_ratio"] == pytest.approx(1.50 / 5.0)


def test_get_actionable_directives_no_short_leg_never_fabricates_derived_features():
    """A directive with no short leg (Short_Strike/Long_Strike/Short_Delta all
    absent -- e.g. a pure long debit structure) must yield short_delta=None
    and credit_to_width_ratio=None, never a fabricated value."""
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    mock_directive_debit = {
        "Strategy": "Bull Call Spread",
        "Action": "Open",
        "Integrity_OK": True,
        "IVR_Proxy": 65.0,
        "True_IVR": 65.0,
        "Net_Premium": -1.20,
        "Trend_Bias": "Bullish",
        "Legs": [
            {"Strike": 150.0, "Side": "Long", "Type": "Call", "Delta": 0.40},
            {"Strike": 155.0, "Side": "Long", "Type": "Call", "Delta": 0.25},
        ],
        # No Short_Strike / Long_Strike / Short_Delta top-level keys at all.
    }

    with patch("execution.options_paper_executor._directive_for_symbol") as mock_fetch:
        mock_fetch.return_value = mock_directive_debit
        directives = executor.get_actionable_directives(
            symbols=["AAPL"],
            vrp=0.03,
            macro_dto=MagicMock(vix=20.0, market_regime="NORMAL"),
        )

    assert len(directives) == 1
    assert directives[0]["short_delta"] is None
    assert directives[0]["credit_to_width_ratio"] is None


def test_get_actionable_directives_vrp_and_vix_present_but_none_when_unresolvable():
    """Calling with vrp=None/macro_dto=None must yield vrp/vix keys explicitly
    present with value None -- never omitted from the item dict."""
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    mock_directive_pcs = {
        "Strategy": "Put Credit Spread",
        "Action": "Open",
        "Integrity_OK": True,
        "IVR_Proxy": 65.0,
        "True_IVR": 65.0,
        "Net_Premium": 1.50,
        "Trend_Bias": "Bullish",
        "Short_Strike": 150.0,
        "Long_Strike": 145.0,
        "Short_Delta": -0.30,
        "Legs": [
            {"Strike": 150.0, "Side": "Short", "Delta": -0.30},
            {"Strike": 145.0, "Side": "Long", "Delta": -0.15},
        ],
    }

    with patch("execution.options_paper_executor._directive_for_symbol") as mock_fetch:
        mock_fetch.return_value = mock_directive_pcs
        directives = executor.get_actionable_directives(symbols=["AAPL"], vrp=None, macro_dto=None)

    assert len(directives) == 1
    assert "vrp" in directives[0]
    assert "vix" in directives[0]
    assert directives[0]["vrp"] is None
    assert directives[0]["vix"] is None


def test_execute_strategy_directives_dry_run():
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    directives = [
        {
            "symbol": "AAPL",
            "strategy": "Put Credit Spread",
            "action": "Open",
            "net_premium": 1.50,
            "target_dte": 30,
            "legs": [
                {"strike": 150.0, "side": "sell", "type": "put", "ratio_qty": 1.0, "price": 2.20},
                {"strike": 145.0, "side": "buy", "type": "put", "ratio_qty": 1.0, "price": 0.70},
            ]
        }
    ]

    result = executor.execute_strategy_directives(directives=directives, dry_run=True)
    assert result["executed_count"] == 1
    assert result["executed"][0]["dry_run"] is True

    # In dry run, store is untouched
    assert len(store.get_open_positions()) == 0


def test_execute_strategy_directives_live_fill():
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    directives = [
        {
            "symbol": "AAPL",
            "strategy": "Put Credit Spread",
            "action": "Open",
            "net_premium": 1.50,
            "target_dte": 30,
            "legs": [
                {"strike": 150.0, "side": "sell", "type": "put", "ratio_qty": 1.0, "price": 2.20},
                {"strike": 145.0, "side": "buy", "type": "put", "ratio_qty": 1.0, "price": 0.70},
            ]
        }
    ]

    result = executor.execute_strategy_directives(directives=directives, dry_run=False, max_notional_per_order=2500.0)
    assert result["executed_count"] == 1
    assert result["skipped_count"] == 0
    assert result["failed_count"] == 0

    positions = store.get_open_positions()
    assert len(positions) == 2
    short_leg = next(p for p in positions if "$150.00" in p.symbol)
    long_leg = next(p for p in positions if "$145.00" in p.symbol)
    assert short_leg.qty < 0
    assert long_leg.qty > 0


def test_execute_strategy_directives_deduplication():
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    directives = [
        {
            "symbol": "AAPL",
            "strategy": "Put Credit Spread",
            "action": "Open",
            "net_premium": 1.50,
            "target_dte": 30,
            "legs": [
                {"strike": 150.0, "side": "sell", "type": "put", "ratio_qty": 1.0, "price": 2.20},
                {"strike": 145.0, "side": "buy", "type": "put", "ratio_qty": 1.0, "price": 0.70},
            ]
        }
    ]

    # First execution succeeds
    res1 = executor.execute_strategy_directives(directives=directives, dry_run=False)
    assert res1["executed_count"] == 1

    # Second execution skips duplicate symbol
    res2 = executor.execute_strategy_directives(directives=directives, dry_run=False)
    assert res2["executed_count"] == 0
    assert res2["skipped_count"] == 1
    assert "already exists" in res2["skipped"][0]["reason"]


# ---------------------------------------------------------------------------
# Stage 4 ML Meta-Labeler warm-up on construction (F3 audit fix)
# ---------------------------------------------------------------------------
#
# ``global_options_meta_labeler`` (ml/options_meta_labeler.py) is a true
# module-level singleton shared across the entire pytest session. Tests below
# that mutate its ``.model``/``.model_path``/``.n_samples`` MUST restore the
# original state afterward so they don't leak into unrelated tests (including
# the ones above, which rely on the singleton staying at its untouched,
# ``self.model is None`` default so the honest 0.65/1.0x fallback keeps their
# assertions order-independent).

@pytest.fixture
def reset_meta_labeler_singleton():
    """Snapshots and restores the global_options_meta_labeler singleton's state."""
    saved = dict(global_options_meta_labeler.__dict__)
    try:
        yield global_options_meta_labeler
    finally:
        global_options_meta_labeler.__dict__.clear()
        global_options_meta_labeler.__dict__.update(saved)


def _train_synthetic_meta_labeler(model_path: Path):
    """Trains and persists a real OptionsMetaLabeler with a strong, learnable opinion.

    Mirrors tests/test_options_meta_labeler.py::test_train_and_predict's synthetic
    distribution: bullish/high-IVR/high-VRP put credit spreads win, bearish/low-IVR/
    negative-VRP ones lose. Returns (labeler, good_cand, bad_cand).
    """
    np.random.seed(42)
    labeler = OptionsMetaLabeler(model_path=model_path)
    samples = []
    for _ in range(50):
        samples.append(
            OptionsTradeFeatureRow(
                strategy="Put Credit Spread",
                ivr=60.0 + np.random.uniform(0, 30),
                vrp=0.03 + np.random.uniform(0, 0.03),
                vix=18.0 + np.random.uniform(0, 5),
                trend_bias=1.0,
                target_dte=35,
                credit_to_width_ratio=0.30,
                short_delta=0.25,
                outcome_win=1,
            )
        )
        samples.append(
            OptionsTradeFeatureRow(
                strategy="Put Credit Spread",
                ivr=10.0 + np.random.uniform(0, 15),
                vrp=-0.02 + np.random.uniform(0, 0.01),
                vix=35.0 + np.random.uniform(0, 10),
                trend_bias=-1.0,
                target_dte=35,
                credit_to_width_ratio=0.15,
                short_delta=0.45,
                outcome_win=0,
            )
        )
    res = labeler.train(samples)
    assert res["samples"] == 100

    good_cand = {
        "strategy": "Put Credit Spread",
        "ivr": 75.0,
        "vrp": 0.04,
        "vix": 19.0,
        "trend_bias": 1.0,
        "target_dte": 35,
        "credit_to_width_ratio": 0.32,
        "short_delta": 0.25,
    }
    bad_cand = {
        "strategy": "Put Credit Spread",
        "ivr": 12.0,
        "vrp": -0.02,
        "vix": 38.0,
        "trend_bias": -1.0,
        "target_dte": 35,
        "credit_to_width_ratio": 0.12,
        "short_delta": 0.45,
    }
    return labeler, good_cand, bad_cand


def test_executor_construction_loads_real_trained_model(reset_meta_labeler_singleton, tmp_path):
    """OptionsPaperExecutor() must actually load a real trained model file, not just
    leave the singleton at its hardcoded-fallback ``self.model is None`` state."""
    model_path = tmp_path / "options_meta_labeler.pkl"
    _labeler, good_cand, bad_cand = _train_synthetic_meta_labeler(model_path)

    # Simulate a fresh, never-loaded process pointed at the real trained-model file.
    singleton = reset_meta_labeler_singleton
    singleton.model = None
    singleton.model_path = model_path
    singleton.n_samples = 0

    store = PaperAccountStore(db_url="sqlite:///:memory:")
    OptionsPaperExecutor(store=store)

    # The real file's contents were loaded (not merely a non-None sentinel).
    assert singleton.model is not None
    assert singleton.n_samples == 100

    p_good = singleton.predict_probability(good_cand)
    p_bad = singleton.predict_probability(bad_cand)

    # The hardcoded fallback (0.65) would satisfy neither of these -- proves the
    # REAL loaded model, not the fallback, answered both predictions.
    assert p_good > 0.60
    assert p_bad < 0.50
    assert p_good != p_bad


def test_execute_strategy_directives_uses_real_model_for_gating_and_sizing(
    reset_meta_labeler_singleton, tmp_path
):
    """The production execute_strategy_directives() path must gate/size using the
    real loaded model's opinion, not the old always-approve/always-1.0x no-op."""
    model_path = tmp_path / "options_meta_labeler.pkl"
    _labeler, good_cand, bad_cand = _train_synthetic_meta_labeler(model_path)

    singleton = reset_meta_labeler_singleton
    singleton.model = None
    singleton.model_path = model_path
    singleton.n_samples = 0

    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)
    assert singleton.model is not None  # warmed up by construction

    legs = [
        {"strike": 150.0, "side": "sell", "type": "put", "ratio_qty": 1.0, "price": 2.20},
        {"strike": 145.0, "side": "buy", "type": "put", "ratio_qty": 1.0, "price": 0.70},
    ]

    bad_directive = {
        "symbol": "AAPL",
        "action": "Open",
        "net_premium": 1.50,
        "target_dte": 30,
        "legs": legs,
        **bad_cand,
    }
    good_directive = {
        "symbol": "AAPL",
        "action": "Open",
        "net_premium": 1.50,
        "target_dte": 30,
        "legs": legs,
        **good_cand,
    }

    # A directive shaped like the losing training distribution must be rejected by
    # the real model -- this could never happen under the old bug, since the
    # hardcoded 0.65 fallback always clears the 0.52 min-confidence threshold.
    bad_result = executor.execute_strategy_directives(directives=[bad_directive], dry_run=True)
    assert bad_result["executed_count"] == 0
    assert bad_result["skipped_count"] == 1
    assert "Stage 4 ML Meta-Labeler rejected" in bad_result["skipped"][0]["reason"]

    # The real model's sizing multiplier for a directive shaped like the winning
    # training distribution must not be pinned at the old no-op 1.0x.
    ml_score = singleton.score_option_directive(good_directive)
    assert ml_score["approved"] is True
    assert ml_score["sizing_multiplier"] != 1.0
    assert ml_score["sizing_multiplier"] > 1.0

    # Prove this multiplier actually changes production behavior: with the ML gate
    # enabled the real model's >1.0x multiplier must scale contracts away from the
    # raw (ML-disabled) baseline for the exact same directive.
    good_result = executor.execute_strategy_directives(directives=[good_directive], dry_run=True)
    assert good_result["executed_count"] == 1
    contracts_with_ml = good_result["executed"][0]["contracts"]

    with patch("execution.options_paper_executor.settings.OPTIONS_META_LABELER_ENABLED", False):
        baseline_result = executor.execute_strategy_directives(directives=[good_directive], dry_run=True)
    assert baseline_result["executed_count"] == 1
    contracts_baseline = baseline_result["executed"][0]["contracts"]

    assert contracts_with_ml != contracts_baseline


def test_executor_construction_no_model_file_is_honest_and_non_crashing(
    reset_meta_labeler_singleton, tmp_path
):
    """A fresh install with no trained model on disk must not crash construction,
    and must keep the honest 0.65/1.0x fallback (CONSTRAINT #6)."""
    singleton = reset_meta_labeler_singleton
    singleton.model = None
    singleton.model_path = tmp_path / "does_not_exist.pkl"

    store = PaperAccountStore(db_url="sqlite:///:memory:")
    OptionsPaperExecutor(store=store)  # must not raise

    assert singleton.model is None
    assert singleton.predict_probability({"strategy": "Put Credit Spread"}) == 0.65
    assert singleton.get_sizing_multiplier(0.65) == 1.0


def test_execute_strategy_directives_fails_closed_on_ml_scoring_exception(caplog):
    """Bug 2 fix: an exception raised while scoring a directive through the
    Stage 4 ML Meta-Labeler must skip the trade entirely (fail closed) rather
    than silently falling through to full, un-derated size. Logged at
    WARNING, not DEBUG (CONSTRAINT #6)."""
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    directives = [
        {
            "symbol": "AAPL",
            "strategy": "Put Credit Spread",
            "action": "Open",
            "net_premium": 1.50,
            "target_dte": 30,
            "legs": [
                {"strike": 150.0, "side": "sell", "type": "put", "ratio_qty": 1.0, "price": 2.20},
                {"strike": 145.0, "side": "buy", "type": "put", "ratio_qty": 1.0, "price": 0.70},
            ],
        }
    ]

    with patch(
        "ml.options_meta_labeler.global_options_meta_labeler.score_option_directive",
        side_effect=RuntimeError("boom"),
    ), patch("execution.options_paper_executor.settings.OPTIONS_META_LABELER_ENABLED", True), caplog.at_level(
        logging.WARNING, logger="execution.options_paper_executor"
    ):
        result = executor.execute_strategy_directives(directives=directives, dry_run=True)

    assert result["executed_count"] == 0
    assert result["skipped_count"] == 1
    reason = result["skipped"][0]["reason"]
    assert "exception" in reason.lower()
    assert "fail closed" in reason.lower()

    warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("meta-labeler" in m.lower() and "exception" in m.lower() for m in warning_messages)


def _train_meta_labeler_dependent_on_derived_features(model_path: Path) -> OptionsMetaLabeler:
    """Trains a real OptionsMetaLabeler whose win probability depends
    meaningfully on ``short_delta``/``credit_to_width_ratio`` -- the two
    features the Bug 1 fix derives from a production directive's
    Short_Strike/Long_Strike/Net_Premium/Short_Delta -- holding
    ivr/vrp/vix/strategy/target_dte constant across both classes so only the
    derived features can be driving any difference in the model's verdict.
    """
    np.random.seed(7)
    labeler = OptionsMetaLabeler(model_path=model_path)
    samples = []
    for _ in range(60):
        # "Good" profile: low short delta (safely OTM short strike), high
        # credit-to-width ratio (well-compensated spread) -> wins.
        samples.append(
            OptionsTradeFeatureRow(
                strategy="Put Credit Spread",
                ivr=65.0,
                vrp=0.03,
                vix=20.0,
                trend_bias=0.0,
                target_dte=30,
                credit_to_width_ratio=0.40 + np.random.uniform(-0.01, 0.01),
                short_delta=0.15 + np.random.uniform(-0.01, 0.01),
                outcome_win=1,
            )
        )
        # "Bad" profile: high short delta (close to the money), low
        # credit-to-width ratio (poorly compensated) -> loses.
        samples.append(
            OptionsTradeFeatureRow(
                strategy="Put Credit Spread",
                ivr=65.0,
                vrp=0.03,
                vix=20.0,
                trend_bias=0.0,
                target_dte=30,
                credit_to_width_ratio=0.15 + np.random.uniform(-0.01, 0.01),
                short_delta=0.45 + np.random.uniform(-0.01, 0.01),
                outcome_win=0,
            )
        )
    res = labeler.train(samples)
    assert res["samples"] == 120
    return labeler


def test_get_actionable_directives_end_to_end_derived_features_drive_ml_decision(
    reset_meta_labeler_singleton, tmp_path
):
    """End-to-end proof (sibling fix already landed -- full version, not the
    fallback substitute): the Bug 1 fix's derived ``short_delta``/
    ``credit_to_width_ratio`` -- sourced from a REAL production-shaped
    directive's Short_Strike/Long_Strike/Net_Premium/Short_Delta via
    get_actionable_directives, not hand-set candidate dict keys -- actually
    drives the Stage 4 ML Meta-Labeler's live approval/sizing decision inside
    execute_strategy_directives."""
    model_path = tmp_path / "options_meta_labeler.pkl"
    _train_meta_labeler_dependent_on_derived_features(model_path)

    singleton = reset_meta_labeler_singleton
    singleton.model = None
    singleton.model_path = model_path
    singleton.n_samples = 0

    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)
    assert singleton.model is not None  # warmed up by construction

    macro_stub = MagicMock(vix=20.0, market_regime="NORMAL")

    def _make_directive(short_delta: float, short_price: float, long_price: float) -> dict:
        net_premium = round(short_price - long_price, 2)
        return {
            "Strategy": "Put Credit Spread",
            "Action": "Open",
            "Integrity_OK": True,
            "IVR_Proxy": 65.0,
            "True_IVR": 65.0,
            "Net_Premium": net_premium,
            "Trend_Bias": "Neutral",
            "Short_Strike": 150.0,
            "Long_Strike": 145.0,
            "Short_Delta": short_delta,
            "Legs": [
                {"Side": "Short", "Type": "Put", "Strike": 150.0, "Price": short_price, "Delta": short_delta},
                {"Side": "Long", "Type": "Put", "Strike": 145.0, "Price": long_price, "Delta": short_delta / 2.0},
            ],
        }

    # Good profile: short_delta=0.15, width=5, net_premium=2.00 -> credit_to_width_ratio=0.40
    good_directive = _make_directive(short_delta=-0.15, short_price=2.50, long_price=0.50)
    # Bad profile: short_delta=0.45, width=5, net_premium=0.75 -> credit_to_width_ratio=0.15
    bad_directive = _make_directive(short_delta=-0.45, short_price=1.00, long_price=0.25)

    with patch("execution.options_paper_executor._directive_for_symbol") as mock_fetch:
        mock_fetch.side_effect = lambda sym, **kwargs: good_directive if sym == "GOOD" else bad_directive
        actionable = executor.get_actionable_directives(
            symbols=["GOOD", "BAD"], vrp=0.03, macro_dto=macro_stub, target_dte=30,
        )

    assert len(actionable) == 2
    good_item = next(i for i in actionable if i["symbol"] == "GOOD")
    bad_item = next(i for i in actionable if i["symbol"] == "BAD")

    # The derived features came from the real production directive shape
    # (Short_Strike/Long_Strike/Net_Premium/Short_Delta), not a hardcoded
    # default or a hand-set candidate key.
    assert good_item["short_delta"] == pytest.approx(0.15)
    assert good_item["credit_to_width_ratio"] == pytest.approx(2.00 / 5.0)
    assert bad_item["short_delta"] == pytest.approx(0.45)
    assert bad_item["credit_to_width_ratio"] == pytest.approx(0.75 / 5.0)

    good_score = singleton.score_option_directive(good_item)
    bad_score = singleton.score_option_directive(bad_item)
    assert good_score["features_resolved"] is True
    assert bad_score["features_resolved"] is True
    assert good_score["prob_win"] > bad_score["prob_win"]
    assert good_score["approved"] is True
    assert bad_score["approved"] is False

    good_result = executor.execute_strategy_directives(directives=[good_item], dry_run=True)
    assert good_result["executed_count"] == 1
    contracts_good = good_result["executed"][0]["contracts"]

    bad_result = executor.execute_strategy_directives(directives=[bad_item], dry_run=True)
    assert bad_result["executed_count"] == 0
    assert bad_result["skipped_count"] == 1
    assert "rejected" in bad_result["skipped"][0]["reason"].lower()

    # Prove the real derived values -- not a hardcoded default -- drove this:
    # with the ML gate disabled, the same good directive must size
    # differently than it did with the gate's real (>1.0x) multiplier applied.
    with patch("execution.options_paper_executor.settings.OPTIONS_META_LABELER_ENABLED", False):
        baseline_result = executor.execute_strategy_directives(directives=[good_item], dry_run=True)
    assert baseline_result["executed_count"] == 1
    contracts_baseline = baseline_result["executed"][0]["contracts"]

    assert contracts_good != contracts_baseline


# ---------------------------------------------------------------------------
# execute_earnings_crush_trade: strategy_name param + no-fabrication fix
# ---------------------------------------------------------------------------


def _iron_condor_candidate(symbol="NVDA"):
    return {
        "symbol": symbol,
        "strategy": "Iron Condor",
        "expiration": "2026-08-21",
        "earnings_date": "2026-08-20",
        "legs": [
            {"symbol": f"{symbol} 2026-08-21 $110.00 PUT", "side": "buy", "qty": 1.0, "fill_price": 50.0},
            {"symbol": f"{symbol} 2026-08-21 $115.00 PUT", "side": "sell", "qty": 1.0, "fill_price": 180.0},
            {"symbol": f"{symbol} 2026-08-21 $125.00 CALL", "side": "sell", "qty": 1.0, "fill_price": 200.0},
            {"symbol": f"{symbol} 2026-08-21 $130.00 CALL", "side": "buy", "qty": 1.0, "fill_price": 60.0},
        ],
        "net_credit": 2.70,
    }


def test_execute_earnings_crush_trade_default_strategy_name_is_unchanged():
    """strategy_name=None (the default, matching every pre-existing caller) must
    preserve the exact historical "Earnings Crush" label, regardless of
    candidate["strategy"]."""
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    res = executor.execute_earnings_crush_trade(_iron_condor_candidate(), contracts=1)

    assert res["success"] is True
    assert res["strategy"] == "Earnings Crush"


def test_execute_earnings_crush_trade_explicit_strategy_name_overrides_label():
    """A caller passing strategy_name= gets that label instead of the hardcoded
    "Earnings Crush" -- both in the returned dict and in the parent order's
    strategy_id field (no longer baked into the symbol label)."""
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    res = executor.execute_earnings_crush_trade(
        _iron_condor_candidate(symbol="AAPL"), contracts=1, strategy_name="Vol Mispricing",
    )

    assert res["success"] is True
    assert res["strategy"] == "Vol Mispricing"

    positions = store.get_open_positions()
    assert len(positions) == 4

    orders = store.get_full_orders()
    parent_order = next(o for o in orders if o["order_id"] == res["order_id"])
    assert parent_order["symbol"] == "AAPL"
    assert parent_order["strategy_id"] == "Vol Mispricing"

def test_execute_earnings_crush_trade_never_fabricates_price():
    """A leg with no resolvable fill_price/raw_price must NOT be filled with the
    old fabricated $1.50/$150.00 sentinel -- the trade is refused honestly
    instead (CONSTRAINT #4), and no partial fill is submitted."""
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)
    initial_cash = store.get_account().cash

    candidate = {
        "symbol": "NVDA",
        "strategy": "Iron Condor",
        "expiration": "2026-08-21",
        "legs": [
            {"symbol": "NVDA 2026-08-21 $110.00 PUT", "side": "buy", "qty": 1.0, "fill_price": 50.0},
            # No fill_price, no price/raw_price anywhere on this leg.
            {"symbol": "NVDA 2026-08-21 $115.00 PUT", "side": "sell", "qty": 1.0},
        ],
    }

    res = executor.execute_earnings_crush_trade(candidate, contracts=1)

    assert res["success"] is False
    assert "150" not in res["reason"] and "1.5" not in res["reason"]
    assert "NVDA 2026-08-21 $115.00 PUT" in res["reason"]

    # No partial fill was ever submitted.
    assert store.get_open_positions() == []
    assert store.get_account().cash == initial_cash
