"""
tests/test_forecast_rebuild_f2.py
=================================
Forecasting rebuild F2 ("safety guards"). See
``.claude/forecasting_rebuild_implementation_plan.md``.

* ``apply_forecast_guards``: the k * sigma_GARCH * sqrt(h) clamp and the
  input-price check on synthetic inputs (drop, renormalize, all-dropped,
  missing sigma, disabled settings);
* ``ForecastingEngine.generate_forecast`` wiring: a blown-up model is dropped
  from the published blend but its raw row is still recorded; all-dropped
  falls back to the existing "no model output" path; a wrong Monte Carlo
  anchor is dropped along with its band;
* byte-identical output when no guard trips;
* the static blend renormalizes when Monte Carlo is missing;
* Prophet's horizon is in trading (business) days, with a zero-lookahead
  perturbation check;
* ``forecast_alignment`` scores a missing / fallback forecast as 0 in both
  the per-row and the vectorized path.
"""
from __future__ import annotations

import math
from typing import Dict, List

import numpy as np
import pandas as pd
import pytest

import forecasting_engine
from forecasting.forecast_tracker import ForecastTracker
from forecasting_engine import (
    GUARD_REASON_CLAMP,
    GUARD_REASON_INPUT_PRICE,
    ForecastingEngine,
    apply_forecast_guards,
)
from settings import settings
from signals.forecast_alignment import ForecastAlignmentSignal


# ============================================================================
# Helpers
# ============================================================================

HORIZONS = (10, 30, 60, 90)
# Sector "Unknown" -> target_days 60 (already in HORIZONS).
TERM_STRUCTURE = {10: 0.30, 30: 0.30, 60: 0.30, 90: 0.30}


def _price_series(n: int = 260, seed: int = 0, start: float = 20.0, drift: float = 0.0003) -> pd.Series:
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    prices = start * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
    return pd.Series(prices, index=dates, name="Close")


@pytest.fixture(autouse=True)
def _fast_deterministic_engine(monkeypatch):
    """No Prophet / TensorFlow fits unless a test opts in; guard settings at
    their documented defaults regardless of the local .env."""
    monkeypatch.setattr(forecasting_engine, "PROPHET_AVAILABLE", False)
    monkeypatch.setattr(forecasting_engine, "TENSORFLOW_AVAILABLE", False)
    monkeypatch.setattr(settings, "FORECAST_CLAMP_SIGMA_K", 4.0)
    monkeypatch.setattr(settings, "FORECAST_INPUT_PRICE_TOLERANCE", 0.05)
    monkeypatch.setattr(settings, "FORECAST_USE_GARCH_SIGMA", True)
    monkeypatch.setattr(settings, "FORECAST_MC_RANDOM_SEED", 42)
    monkeypatch.setattr(settings, "FORECAST_SKILL_WEIGHTING_ENABLED", False)
    monkeypatch.setattr(settings, "BERT_LLA_ENABLED", False)


class _CapturingTracker(ForecastTracker):
    """A ForecastTracker (isinstance check in the engine) that never touches
    a DB: it records what the engine would have written."""

    def __init__(self):  # noqa: D401 - deliberately skip the DB setup
        self.recorded: Dict[int, Dict[str, float]] = {}
        self.bounds: Dict[int, object] = {}

    def update_actuals(self, *a, **k):
        return 0

    def get_skill_weights(self, *a, **k):
        return {}

    def record_forecasts(self, symbol, horizon_days, model_forecasts, forecast_ts, model_bounds=None):
        self.recorded.setdefault(int(horizon_days), {}).update(model_forecasts)
        self.bounds[int(horizon_days)] = model_bounds


def _run(engine, history, current_price=None, **kw):
    row = pd.Series({"Symbol": "TST", "sector": "Unknown"})
    price = float(history.iloc[-1]) if current_price is None else current_price
    return engine.generate_forecast(
        row, price, history_series=history,
        precomputed_garch_term_structure=kw.pop("term_structure", TERM_STRUCTURE), **kw,
    )


def _equal_results(a: dict, b: dict) -> bool:
    if set(a) != set(b):
        return False
    for k in a:
        x, y = a[k], b[k]
        if isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y):
            continue
        if x != y:
            return False
    return True


# ============================================================================
# apply_forecast_guards (pure math)
# ============================================================================

class TestApplyForecastGuards:
    # daily sigma 0.02, h = 25 -> band = 0.02 * 5 = 0.10 log-return; k = 4 -> 0.40.
    SIGMA = 0.02
    H = 25

    def _anchors(self, last=20.0, mc=20.0):
        return {"arima": last, "holt_winters": last, "monte_carlo": mc, "cnn_lstm": last}

    def test_blown_up_arima_is_dropped_others_kept(self):
        forecasts = {"arima": 20.0 * 1.3e15, "holt_winters": 21.0, "monte_carlo": 20.5}
        kept, drops = apply_forecast_guards(
            forecasts, self._anchors(), 20.0, self.SIGMA, self.H, 4.0, 0.05,
        )
        assert kept == {"holt_winters": 21.0, "monte_carlo": 20.5}
        assert [(n, r) for n, r, _ in drops] == [("arima", GUARD_REASON_CLAMP)]
        # measured value is in band units: |ln(1.3e15)| / 0.10
        assert drops[0][2] == pytest.approx(math.log(1.3e15) / 0.10)

    def test_threshold_is_k_sigma_sqrt_h_both_directions(self):
        edge = 4.0 * self.SIGMA * math.sqrt(self.H)  # 0.40
        inside_up = 20.0 * math.exp(edge * 0.999)
        outside_up = 20.0 * math.exp(edge * 1.001)
        outside_down = 20.0 * math.exp(-edge * 1.001)
        kept, drops = apply_forecast_guards(
            {"a": inside_up, "b": outside_up, "c": outside_down},
            {}, 20.0, self.SIGMA, self.H, 4.0, 0.05,
        )
        assert set(kept) == {"a"}
        assert {n for n, _, _ in drops} == {"b", "c"}

    def test_all_dropped_returns_empty(self):
        kept, drops = apply_forecast_guards(
            {"arima": 1e9, "holt_winters": 1e-9}, self._anchors(), 20.0, self.SIGMA, self.H, 4.0, 0.05,
        )
        assert kept == {}
        assert len(drops) == 2

    def test_missing_sigma_skips_clamp(self):
        forecasts = {"arima": 1e9, "holt_winters": 21.0}
        for sigma in (None, float("nan"), 0.0, -1.0):
            kept, drops = apply_forecast_guards(forecasts, self._anchors(), 20.0, sigma, self.H, 4.0, 0.05)
            assert kept == forecasts
            assert drops == []

    def test_clamp_disabled_when_k_not_positive(self):
        forecasts = {"arima": 1e9}
        for k in (0.0, -4.0):
            kept, drops = apply_forecast_guards(forecasts, self._anchors(), 20.0, self.SIGMA, self.H, k, 0.05)
            assert kept == forecasts and drops == []

    def test_input_price_check_drops_wrong_anchor(self):
        # The 2026-08-14 shape: Monte Carlo seeded at ~$100, symbol at ~$20.
        forecasts = {"arima": 20.4, "monte_carlo": 101.0}
        kept, drops = apply_forecast_guards(
            forecasts, self._anchors(mc=100.0), 20.0, self.SIGMA, self.H, 4.0, 0.05,
        )
        assert kept == {"arima": 20.4}
        assert drops[0][0] == "monte_carlo" and drops[0][1] == GUARD_REASON_INPUT_PRICE
        assert drops[0][2] == pytest.approx(4.0)  # 100/20 - 1

    def test_input_price_tolerance_edge(self):
        kept, _ = apply_forecast_guards({"monte_carlo": 20.0}, {"monte_carlo": 20.0 * 1.049}, 20.0, None, 25, 4.0, 0.05)
        assert "monte_carlo" in kept
        kept, _ = apply_forecast_guards({"monte_carlo": 20.0}, {"monte_carlo": 20.0 * 1.051}, 20.0, None, 25, 4.0, 0.05)
        assert "monte_carlo" not in kept

    def test_input_check_disabled_or_unreferenced(self):
        forecasts = {"monte_carlo": 101.0}
        anchors = {"monte_carlo": 100.0}
        # tolerance <= 0 disables
        assert apply_forecast_guards(forecasts, anchors, 20.0, None, 25, 4.0, 0.0)[0] == forecasts
        # no last close -> nothing to check against
        assert apply_forecast_guards(forecasts, anchors, None, None, 25, 4.0, 0.05)[0] == forecasts
        # a model with no known anchor cannot be checked
        assert apply_forecast_guards(forecasts, {}, 20.0, None, 25, 4.0, 0.05)[0] == forecasts

    def test_input_is_not_mutated(self):
        forecasts = {"arima": 1e9, "holt_winters": 21.0}
        snapshot = dict(forecasts)
        apply_forecast_guards(forecasts, self._anchors(), 20.0, self.SIGMA, self.H, 4.0, 0.05)
        assert forecasts == snapshot


# ============================================================================
# Blend renormalization over survivors
# ============================================================================

class TestBlendRenormalizesSurvivors:
    def test_skill_weights_renormalize_when_a_weighted_model_is_dropped(self):
        weights = {"arima": 0.5, "holt_winters": 0.3, "monte_carlo": 0.2}
        survivors = {"holt_winters": 21.0, "monte_carlo": 20.0}
        out = ForecastingEngine._blend_with_skill(survivors, weights, "MC", 20.0)
        assert out == pytest.approx(21.0 * 0.3 / 0.5 + 20.0 * 0.2 / 0.5)

    def test_static_blend_without_mc_renormalizes(self):
        out = ForecastingEngine._blend_with_skill({"cnn_lstm": 22.0, "arima": 19.0}, {}, "MC", 20.0)
        assert out == pytest.approx((22.0 * 0.4 + 19.0 * 0.2) / 0.6)
        out = ForecastingEngine._blend_with_skill({"cnn_lstm": 22.0}, {}, "MC", 20.0)
        assert out == pytest.approx(22.0)
        out = ForecastingEngine._blend_with_skill({"holt_winters": 21.5}, {}, "MC", 20.0)
        assert out == pytest.approx(21.5)

    def test_static_blend_with_mc_present_is_unchanged(self):
        # Exact pre-F2 arithmetic when MC is present.
        assert ForecastingEngine._blend_with_skill(
            {"cnn_lstm": 22.0, "arima": 19.0, "monte_carlo": 20.5}, {}, "MC", 20.0,
        ) == 22.0 * 0.4 + 19.0 * 0.2 + 20.5 * 0.4
        assert ForecastingEngine._blend_with_skill(
            {"cnn_lstm": 22.0, "monte_carlo": 20.5}, {}, "MC", 20.0,
        ) == 22.0 * 0.5 + 20.5 * 0.5


# ============================================================================
# generate_forecast wiring
# ============================================================================

class TestGenerateForecastGuards:
    def test_blown_up_arima_dropped_from_blend_but_recorded(self, monkeypatch):
        history = _price_series()
        tracker_bad = _CapturingTracker()
        engine_bad = ForecastingEngine(tracker=tracker_bad)
        monkeypatch.setattr(engine_bad, "forecast_from_arima_fit", lambda fit, h: float(history.iloc[-1]) * 1.3e15)
        bad = _run(engine_bad, history)

        engine_absent = ForecastingEngine()
        monkeypatch.setattr(engine_absent, "forecast_from_arima_fit", lambda fit, h: float("nan"))
        absent = _run(engine_absent, history)

        for h in HORIZONS:
            # The published blend is exactly the blend of the other models.
            assert bad[f"Forecast_{h}"] == absent[f"Forecast_{h}"]
            assert bad[f"Forecast_{h}_Is_Fallback"] is False
            # The raw blown-up row is still recorded for scoring...
            assert tracker_bad.recorded[h]["arima"] == pytest.approx(float(history.iloc[-1]) * 1.3e15)
            # ...and the recorded blend is the guarded one.
            assert tracker_bad.recorded[h]["blend"] == bad[f"Forecast_{h}"]

        stats = engine_bad.pop_guard_stats()
        assert stats["dropped_clamp"] == len(HORIZONS)
        assert engine_bad.pop_guard_stats() == {}  # pop resets

    def test_all_models_dropped_takes_the_no_forecast_path(self, monkeypatch):
        history = _price_series()
        # A tiny k clamps every model (every forecast moves at least a little).
        monkeypatch.setattr(settings, "FORECAST_CLAMP_SIGMA_K", 1e-12)
        tracker = _CapturingTracker()
        engine = ForecastingEngine(tracker=tracker)
        price = float(history.iloc[-1])
        out = _run(engine, history)
        for h in HORIZONS:
            assert out[f"Forecast_{h}"] == price  # existing fallback: today's price
            assert out[f"Forecast_{h}_Is_Fallback"] is True
            assert "blend" not in tracker.recorded[h]  # never score a fallback as a blend
            assert "monte_carlo" in tracker.recorded[h]  # raw rows still recorded
            assert f"Forecast_{h}_Lower" not in out and f"Forecast_{h}_Upper" not in out
        assert engine.pop_guard_stats()["all_dropped_horizons"] == len(HORIZONS)

    def test_missing_garch_sigma_skips_clamp(self, monkeypatch):
        history = _price_series()
        monkeypatch.setattr(settings, "FORECAST_USE_GARCH_SIGMA", False)
        engine = ForecastingEngine()
        blown = float(history.iloc[-1]) * 1.3e15
        monkeypatch.setattr(engine, "forecast_from_arima_fit", lambda fit, h: blown)
        out = _run(engine, history)
        # No GARCH sigma -> clamp skipped -> the blown-up ARIMA reaches the blend
        # (unchanged pre-F2 behavior; the sigma is never invented).
        assert out["Forecast_30"] > 1e12
        stats = engine.pop_guard_stats()
        assert stats.get("dropped_clamp", 0) == 0
        assert stats["sigma_unavailable"] == 1

    def test_wrong_mc_anchor_dropped_with_its_band(self):
        history = _price_series(start=20.0)
        last = float(history.iloc[-1])
        tracker = _CapturingTracker()
        engine = ForecastingEngine(tracker=tracker)
        out = _run(engine, history, current_price=last * 5.0)  # MC seeded at 5x
        for h in HORIZONS:
            recorded = tracker.recorded[h]
            # A wrong-input MC row is NOT recorded (it would score the input
            # bug, not the model -- the 2026-08-14 rows F1's cleanup deletes),
            # and no MC band goes with it.
            assert "monte_carlo" not in recorded
            assert tracker.bounds[h] is None
            others = {k: v for k, v in recorded.items() if k not in ("naive", "blend")}
            assert others  # ARIMA / Holt-Winters still recorded
            expected = ForecastingEngine._blend_with_skill(others, {}, "MC", last * 5.0)
            assert out[f"Forecast_{h}"] == expected
            assert recorded["blend"] == expected
            assert f"Forecast_{h}_Lower" not in out and f"Forecast_{h}_Upper" not in out
        assert engine.pop_guard_stats()["dropped_input_price"] == len(HORIZONS)

    def test_blown_up_cnn_lstm_is_dropped(self, monkeypatch):
        history = _price_series()
        last = float(history.iloc[-1])
        monkeypatch.setattr(forecasting_engine, "TENSORFLOW_AVAILABLE", True)
        engine = ForecastingEngine()
        monkeypatch.setattr(engine, "run_cnn_lstm_forecast",
                            lambda df, horizons, ticker=None: {h: last * 1e12 for h in horizons})
        out = _run(engine, history)
        engine_off = ForecastingEngine()  # TF on, but LSTM produces nothing
        monkeypatch.setattr(engine_off, "run_cnn_lstm_forecast",
                            lambda df, horizons, ticker=None: {h: float("nan") for h in horizons})
        ref = _run(engine_off, history)
        for h in HORIZONS:
            assert out[f"Forecast_{h}"] == ref[f"Forecast_{h}"]


class TestNoGuardTripIsByteIdentical:
    """On a normal series nothing trips, so the output must be identical to
    the guards-disabled path (k <= 0, tolerance <= 0), which runs the exact
    pre-F2 blend over the exact same model outputs."""

    @pytest.mark.parametrize("seed", [0, 1, 7])
    def test_normal_series_identical_with_guards_on_and_off(self, monkeypatch, seed):
        history = _price_series(seed=seed)
        engine_on = ForecastingEngine()
        on = _run(engine_on, history)
        assert engine_on.pop_guard_stats() == {}  # nothing fired

        monkeypatch.setattr(settings, "FORECAST_CLAMP_SIGMA_K", 0.0)
        monkeypatch.setattr(settings, "FORECAST_INPUT_PRICE_TOLERANCE", 0.0)
        off = _run(ForecastingEngine(), history)
        assert _equal_results(on, off)

    def test_fitted_garch_path_identical(self, monkeypatch):
        # Same check without a precomputed term structure (engine fits GARCH).
        history = _price_series(seed=3)
        row = pd.Series({"Symbol": "TST", "sector": "Unknown"})
        price = float(history.iloc[-1])
        engine_on = ForecastingEngine()
        on = engine_on.generate_forecast(row, price, history_series=history)
        # The clamp really was armed (a GARCH sigma existed) and did not fire.
        assert "sigma_unavailable" not in engine_on.pop_guard_stats()
        monkeypatch.setattr(settings, "FORECAST_CLAMP_SIGMA_K", 0.0)
        monkeypatch.setattr(settings, "FORECAST_INPUT_PRICE_TOLERANCE", 0.0)
        off = ForecastingEngine().generate_forecast(row, price, history_series=history)
        assert _equal_results(on, off)


class TestSigmaSourceFlag:
    def test_return_source_reports_garch_vs_fallback(self, monkeypatch):
        engine = ForecastingEngine()
        df = pd.DataFrame({"Close": _price_series(n=120)})
        sig, src = engine._estimate_daily_sigma_multi_horizon(
            df, 0.01, [10, 30], {10: 0.25, 30: 0.25}, return_source=True,
        )
        assert src is True and sig[10] == pytest.approx(0.25 / math.sqrt(252))
        sig, src = engine._estimate_daily_sigma_multi_horizon(None, 0.01, [10, 30], return_source=True)
        assert src is False and sig == {10: 0.01, 30: 0.01}
        # default call shape unchanged (plain dict)
        assert engine._estimate_daily_sigma_multi_horizon(None, 0.01, [10]) == {10: 0.01}


# ============================================================================
# Prophet horizon in trading days
# ============================================================================

class _FakeProphet:
    """Stand-in for prophet.Prophet recording what the engine asks for; its
    'forecast' is the last fitted value, so the output depends only on data
    the model was actually given."""
    last_future = None
    last_fit_max_ds = None

    def __init__(self, *a, **k):
        self.history_dates = None

    def fit(self, df):
        self._df = df
        self.history_dates = pd.to_datetime(df["ds"])
        _FakeProphet.last_fit_max_ds = self.history_dates.max()
        return self

    def make_future_dataframe(self, periods, freq="D", include_history=True):
        last = self.history_dates.max()
        dates = pd.date_range(start=last, periods=periods + 1, freq=freq)
        dates = dates[dates > last][:periods]
        _FakeProphet.last_future = (periods, freq, dates)
        return pd.DataFrame({"ds": np.concatenate((np.array(self.history_dates), dates))})

    def predict(self, future):
        y = float(self._df["y"].iloc[-1])
        return pd.DataFrame({"ds": future["ds"], "yhat": y, "yhat_lower": y * 0.9, "yhat_upper": y * 1.1})


class TestProphetTradingDayHorizon:
    def test_periods_are_business_days(self, monkeypatch):
        monkeypatch.setattr(forecasting_engine, "PROPHET_AVAILABLE", True)
        monkeypatch.setattr(forecasting_engine, "Prophet", _FakeProphet, raising=False)
        monkeypatch.setattr(settings, "FORECAST_MODEL_PERSISTENCE_ENABLED", False)
        history = _price_series(n=120)
        ForecastingEngine().run_prophet_forecast(history, days_forward=30)
        periods, freq, dates = _FakeProphet.last_future
        assert periods == 30 and freq == "B"
        # The forecast row is the 30th business day after the last bar.
        assert dates[-1] == history.index[-1] + pd.offsets.BDay(30)
        assert dates[-1] < history.index[-1] + pd.Timedelta(days=45)
        assert dates[-1] > history.index[-1] + pd.Timedelta(days=30)  # not 30 calendar days

    def test_real_prophet_forecast_date_and_zero_lookahead(self, monkeypatch):
        """Real Prophet (skipped if not installed). Perturbation test: values
        AFTER the cutoff must not change the forecast made AT the cutoff."""
        prophet = pytest.importorskip("prophet")
        monkeypatch.setattr(forecasting_engine, "PROPHET_AVAILABLE", True)
        monkeypatch.setattr(forecasting_engine, "Prophet", prophet.Prophet, raising=False)
        monkeypatch.setattr(settings, "FORECAST_MODEL_PERSISTENCE_ENABLED", False)

        captured: List[pd.DataFrame] = []
        orig_predict = prophet.Prophet.predict

        def _spy(self, df=None, *a, **k):
            out = orig_predict(self, df, *a, **k)
            captured.append(out)
            return out

        monkeypatch.setattr(prophet.Prophet, "predict", _spy)

        full = _price_series(n=200, seed=11)
        cutoff = 150
        engine = ForecastingEngine()
        base = engine.run_prophet_forecast(full.iloc[:cutoff], days_forward=30)
        assert captured[-1]["ds"].iloc[-1] == full.index[cutoff - 1] + pd.offsets.BDay(30)

        perturbed = full.copy()
        perturbed.iloc[cutoff:] = perturbed.iloc[cutoff:] * 3.0  # future-only shock
        again = engine.run_prophet_forecast(perturbed.iloc[:cutoff], days_forward=30)
        assert again[0] == base[0]  # yhat is a deterministic MAP fit


# ============================================================================
# forecast_alignment: missing / fallback -> neutral
# ============================================================================

class TestForecastAlignmentNeutralOnMissing:
    sig = ForecastAlignmentSignal()

    @pytest.mark.parametrize("forecast", [float("nan"), None, 0.0, -1.0])
    def test_per_row_missing_is_zero(self, forecast):
        out = self.sig.compute(pd.Series({"current_price": 20.0, "forecast_price": forecast}), None)
        assert out.score == 0.0

    def test_per_row_fallback_is_zero(self):
        # The engine's fallback forecast IS today's price; pre-F2 that was -10.
        row = pd.Series({"current_price": 20.0, "forecast_price": 20.0, "forecast_is_fallback": True})
        assert self.sig.compute(row, None).score == 0.0

    def test_per_row_real_forecasts_unchanged(self):
        def s(fc, fb=False):
            return self.sig.compute(pd.Series({"current_price": 20.0, "forecast_price": fc,
                                               "forecast_is_fallback": fb}), None).score
        assert s(20.0 * 1.02) == 1.0
        assert s(20.0 * 1.01) == 0.5
        assert s(20.0) == -1.0  # a real flat blend is still bearish, as before
        assert s(19.0) == -1.0
        # Unknown flag (NaN / None / missing key) is not a fallback.
        assert s(19.0, float("nan")) == -1.0
        assert s(19.0, None) == -1.0
        assert self.sig.compute(pd.Series({"current_price": 20.0, "forecast_price": 19.0}), None).score == -1.0

    def test_vectorized_matches_per_row(self):
        df = pd.DataFrame({
            "current_price": [20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 0.0],
            "forecast_price": [0.0, float("nan"), 20.0, 20.0, 20.4, 21.0, 19.0, 21.0],
            "forecast_is_fallback": [False, False, True, False, False, False, np.nan, False],
        }, index=list("ABCDEFGH"))
        vec = self.sig.compute_vectorized(df, None)
        expected = [0.0, 0.0, 0.0, -1.0, 1.0, 1.0, -1.0, 0.0]
        assert list(vec["score"]) == expected
        for i, sym in enumerate(df.index):
            row = df.loc[sym]
            per_row = self.sig.compute(row, None)
            assert per_row.score == vec.loc[sym, "score"], sym
            assert per_row.explanation == vec.loc[sym, "explanation"], sym

    def test_vectorized_without_flag_column(self):
        df = pd.DataFrame({"current_price": [20.0, 20.0], "forecast_price": [0.0, 19.0]})
        assert list(self.sig.compute_vectorized(df, None)["score"]) == [0.0, -1.0]


class TestStrategyEngineThreadsFallbackFlag:
    def test_fallback_flag_reaches_forecast_alignment(self):
        from dto_models import FundamentalDataDTO, MacroEconomicDTO, MarketBarDTO
        from strategy_engine import StrategyEngine
        from datetime import datetime

        bar = MarketBarDTO(datetime(2026, 9, 25), "TST", 19.8, 20.5, 19.5, 20.0, 1_000_000)
        fund = FundamentalDataDTO(
            ticker="TST", company_name="Test Co", sector="Technology",
            pe_ratio=16.5, pb_ratio=1.45, book_value=10.0, eps_trailing=1.0,
            dividend_yield=0.0, dividend_growth_rate=0.0, payout_ratio=0.0,
        )
        macro = MacroEconomicDTO(yield_curve_10y_2y=0.45, high_yield_oas=2.50, inflation_rate=2.10,
                                 nominal_10y=4.0, vix_value=15.0)
        se = StrategyEngine()

        def comp(flag):
            out = se.evaluate_security(bar=bar, fundamentals=fund, macro=macro, forecast_price=20.0,
                                       trend_strength=50.0, atr=0.4, forecast_is_fallback=flag)
            return out["Score_Components"].get("forecast_alignment")

        fb = comp(True)
        real = comp(False)
        assert fb == 0.0
        assert real is not None and real < 0
