"""
tests/test_forecast_rebuild_f3.py
=================================
Forecasting rebuild F3 ("naive gate in the blend: shadow first"). See
``.claude/forecasting_rebuild_implementation_plan.md``.

* ``compute_naive_gate`` (pure): the >= 0.5% relative median |log error|
  margin, the n >= 60 floor, inverse-error weights, graduated degrade over
  models that did not produce a forecast, naive fallback when nothing is
  admitted, measurement-only names never admitted;
* the gate math on a synthetic ground-truth ledger: 1% better is admitted,
  0.3% is not, n=59 is not;
* zero lookahead: only outcomes that matured before the forecast's trading
  day count, verified by perturbing every row that had not matured yet;
* shadow rows: ``gated_blend`` is recorded at every horizon with the F1
  upsert key and the gate metadata, and never enters skill arithmetic;
* ``FORECAST_NAIVE_GATE_ENABLED`` off: published outputs and ``blend`` rows
  are byte-identical to the engine with the gate removed;
* the flag-on path;
* ``scripts/forecast_skill_report.py --gate``.
"""
from __future__ import annotations

import math
from contextlib import closing
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
import pytest

import forecasting_engine
from forecasting.forecast_tracker import (
    GATE_REASON_NO_STATS,
    GATE_REASON_NOT_BETTER,
    GATE_REASON_NOT_PRODUCED,
    GATE_REASON_TOO_FEW,
    MODEL_ARIMA,
    MODEL_BLEND,
    MODEL_GATED_BLEND,
    MODEL_HOLT_WINTERS,
    MODEL_MONTE_CARLO,
    MODEL_NAIVE,
    NON_BLEND_MODEL_NAMES,
    ForecastTracker,
    compute_naive_gate,
    compute_skill_weights_from_stats,
)
from forecasting_engine import ForecastingEngine
from settings import settings

HORIZONS = (10, 30, 60, 90)
TERM_STRUCTURE = {10: 0.30, 30: 0.30, 60: 0.30, 90: 0.30}


# ============================================================================
# Helpers
# ============================================================================

def _stats(n: int, med: float, naive_med: float) -> Dict[str, object]:
    return {"n": n, "median_abs_log_error": med, "naive_median_abs_log_error": naive_med}


def _price_series(n: int = 260, seed: int = 0, start: float = 50.0) -> pd.Series:
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    prices = start * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))
    return pd.Series(prices, index=dates, name="Close")


def _seed_ledger(
    db: str,
    symbol: str,
    horizon: int,
    days: Sequence[pd.Timestamp],
    model_errors: Dict[str, float],
    naive_err: float = 0.02,
    p0: float = 100.0,
) -> None:
    """Insert COMPLETED rows: one naive row and one row per model per day.

    The realized log-return alternates +/- ``naive_err`` so naive's
    |log error| is exactly ``naive_err`` every day, and each model's
    |ln(forecast / actual)| is exactly its entry in ``model_errors``.
    """
    rows = []
    for i, d in enumerate(days):
        day = pd.Timestamp(d).strftime("%Y-%m-%d")
        ts = f"{day}T15:00:00+00:00"
        sign = 1.0 if i % 2 == 0 else -1.0
        actual = p0 * math.exp(sign * naive_err)
        rows.append((symbol, MODEL_NAIVE, horizon, ts, p0, actual, (actual - p0) ** 2, ts, day))
        for model, err in model_errors.items():
            f = actual * math.exp(sign * err)
            rows.append((symbol, model, horizon, ts, f, actual, (actual - f) ** 2, ts, day))
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.executemany(
            """INSERT INTO forecast_errors
               (symbol, model_name, horizon_days, forecast_ts, forecast_price,
                actual_price, squared_error, recorded_at, forecast_day)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )


def _tracker(tmp_path, name: str = "ledger.db") -> ForecastTracker:
    return ForecastTracker(db_path=str(tmp_path / name))


def _rows_on(db: str, day: Optional[str] = None) -> List[tuple]:
    """All rows (optionally for one forecast_day), as comparable tuples."""
    with closing(sqlite3.connect(db)) as conn, conn:
        q = """SELECT symbol, model_name, horizon_days, forecast_price, forecast_lower,
                      forecast_upper, actual_price, forecast_day, gate_fallback, gate_admitted
               FROM forecast_errors"""
        args: tuple = ()
        if day is not None:
            q += " WHERE forecast_day = ?"
            args = (day,)
        return sorted(conn.execute(q + " ORDER BY id", args).fetchall(), key=repr)


def _today_et() -> str:
    return pd.Timestamp.now(tz="America/New_York").strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def _deterministic_engine(monkeypatch):
    monkeypatch.setattr(forecasting_engine, "PROPHET_AVAILABLE", False)
    monkeypatch.setattr(forecasting_engine, "TENSORFLOW_AVAILABLE", False)
    monkeypatch.setattr(settings, "FORECAST_CLAMP_SIGMA_K", 4.0)
    monkeypatch.setattr(settings, "FORECAST_INPUT_PRICE_TOLERANCE", 0.05)
    monkeypatch.setattr(settings, "FORECAST_USE_GARCH_SIGMA", True)
    monkeypatch.setattr(settings, "FORECAST_MC_RANDOM_SEED", 42)
    monkeypatch.setattr(settings, "FORECAST_SKILL_WEIGHTING_ENABLED", False)
    monkeypatch.setattr(settings, "FORECAST_SKILL_WINDOW_DAYS", 365)
    monkeypatch.setattr(settings, "FORECAST_SKILL_MIN_OBS", 30)
    monkeypatch.setattr(settings, "FORECAST_TRACKER_DUE_DATE_LOOKUP_ENABLED", False)
    monkeypatch.setattr(settings, "BERT_LLA_ENABLED", False)
    monkeypatch.setattr(settings, "FORECAST_NAIVE_GATE_ENABLED", False)
    monkeypatch.setattr(settings, "FORECAST_NAIVE_GATE_MIN_IMPROVEMENT", 0.005)
    monkeypatch.setattr(settings, "FORECAST_NAIVE_GATE_MIN_OBS", 60)


# ============================================================================
# compute_naive_gate (pure)
# ============================================================================

class TestComputeNaiveGate:
    def test_one_percent_better_is_admitted(self):
        d = compute_naive_gate({MODEL_ARIMA: _stats(60, 0.099, 0.100)}, [MODEL_ARIMA], 0.005, 60)
        assert d["admitted"] == [MODEL_ARIMA]
        assert d["weights"] == {MODEL_ARIMA: 1.0}
        assert d["fallback_to_naive"] is False

    def test_point_three_percent_better_is_not_admitted(self):
        d = compute_naive_gate({MODEL_ARIMA: _stats(60, 0.0997, 0.100)}, [MODEL_ARIMA], 0.005, 60)
        assert d["admitted"] == []
        assert d["rejected"][MODEL_ARIMA] == GATE_REASON_NOT_BETTER
        assert d["fallback_to_naive"] is True
        assert d["weights"] == {}

    def test_margin_edge_is_inclusive(self):
        # median_model <= median_naive * (1 - 0.005), both sides computed alike.
        naive = 0.08
        edge = naive * (1.0 - 0.005)
        assert compute_naive_gate({MODEL_ARIMA: _stats(60, edge, naive)}, [MODEL_ARIMA], 0.005, 60)[
            "admitted"] == [MODEL_ARIMA]
        above = math.nextafter(edge, 1.0)
        assert compute_naive_gate({MODEL_ARIMA: _stats(60, above, naive)}, [MODEL_ARIMA], 0.005, 60)[
            "admitted"] == []

    def test_worse_than_naive_is_not_admitted(self):
        d = compute_naive_gate({MODEL_ARIMA: _stats(500, 0.2, 0.1)}, [MODEL_ARIMA], 0.005, 60)
        assert d["rejected"][MODEL_ARIMA] == GATE_REASON_NOT_BETTER

    def test_n_59_is_not_admitted_n_60_is(self):
        good = 0.05
        d59 = compute_naive_gate({MODEL_ARIMA: _stats(59, good, 0.1)}, [MODEL_ARIMA], 0.005, 60)
        assert d59["admitted"] == [] and d59["rejected"][MODEL_ARIMA] == GATE_REASON_TOO_FEW
        d60 = compute_naive_gate({MODEL_ARIMA: _stats(60, good, 0.1)}, [MODEL_ARIMA], 0.005, 60)
        assert d60["admitted"] == [MODEL_ARIMA]

    def test_nothing_admitted_falls_back_to_naive(self):
        stats = {MODEL_ARIMA: _stats(100, 0.2, 0.1), MODEL_HOLT_WINTERS: _stats(10, 0.01, 0.1)}
        d = compute_naive_gate(stats, [MODEL_ARIMA, MODEL_HOLT_WINTERS, MODEL_MONTE_CARLO], 0.005, 60)
        assert d["fallback_to_naive"] is True
        assert d["weights"] == {} and d["admitted"] == []
        assert d["rejected"] == {
            MODEL_ARIMA: GATE_REASON_NOT_BETTER,
            MODEL_HOLT_WINTERS: GATE_REASON_TOO_FEW,
            MODEL_MONTE_CARLO: GATE_REASON_NO_STATS,
        }

    def test_empty_stats_fall_back(self):
        d = compute_naive_gate({}, [MODEL_ARIMA, MODEL_MONTE_CARLO], 0.005, 60)
        assert d["fallback_to_naive"] is True and d["weights"] == {}

    def test_inverse_error_weights(self):
        stats = {
            MODEL_ARIMA: _stats(80, 0.05, 0.10),
            MODEL_HOLT_WINTERS: _stats(80, 0.075, 0.10),
            MODEL_MONTE_CARLO: _stats(80, 0.15, 0.10),  # worse than naive: out
        }
        d = compute_naive_gate(stats, [MODEL_ARIMA, MODEL_HOLT_WINTERS, MODEL_MONTE_CARLO], 0.005, 60)
        inv_a, inv_h = 1 / 0.05, 1 / 0.075
        assert d["weights"][MODEL_ARIMA] == pytest.approx(inv_a / (inv_a + inv_h))
        assert d["weights"][MODEL_HOLT_WINTERS] == pytest.approx(inv_h / (inv_a + inv_h))
        assert sum(d["weights"].values()) == pytest.approx(1.0)
        assert d["weights"][MODEL_ARIMA] == pytest.approx(0.6)
        assert d["admitted"] == [MODEL_ARIMA, MODEL_HOLT_WINTERS]

    def test_admitted_but_not_produced_renormalizes_survivors(self):
        stats = {MODEL_ARIMA: _stats(80, 0.05, 0.10), MODEL_HOLT_WINTERS: _stats(80, 0.075, 0.10)}
        d = compute_naive_gate(stats, [MODEL_HOLT_WINTERS], 0.005, 60)
        assert d["weights"] == {MODEL_HOLT_WINTERS: 1.0}
        assert d["rejected"][MODEL_ARIMA] == GATE_REASON_NOT_PRODUCED

    def test_measurement_only_names_are_never_admitted(self):
        stats = {name: _stats(1000, 1e-9, 0.1) for name in NON_BLEND_MODEL_NAMES}
        d = compute_naive_gate(stats, list(NON_BLEND_MODEL_NAMES), 0.005, 60)
        assert d["fallback_to_naive"] is True
        assert d["rejected"] == {}  # not even considered

    def test_zero_median_does_not_divide_by_zero(self):
        d = compute_naive_gate({MODEL_ARIMA: _stats(80, 0.0, 0.1), MODEL_HOLT_WINTERS: _stats(80, 0.05, 0.1)},
                               [MODEL_ARIMA, MODEL_HOLT_WINTERS], 0.005, 60)
        assert all(math.isfinite(w) for w in d["weights"].values())
        assert sum(d["weights"].values()) == pytest.approx(1.0)


# ============================================================================
# The gate on a synthetic ground-truth ledger
# ============================================================================

AS_OF = datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc)  # a Monday


def _days_before(as_of: datetime, horizon: int, n: int, gap: int = 1) -> pd.DatetimeIndex:
    """n business days whose h-day outcome matured ``gap`` business days
    before ``as_of``'s trading day (strictly before, as the gate requires)."""
    last_forecast = pd.Timestamp(as_of.date()) - pd.offsets.BDay(horizon + gap)
    return pd.bdate_range(end=last_forecast, periods=n)


class TestGateOnSyntheticLedger:
    def test_ground_truth_margins_and_min_obs(self, tmp_path):
        t = _tracker(tmp_path)
        days = _days_before(AS_OF, 10, 60)
        # naive |log error| = 0.02 every day.
        _seed_ledger(t._db_path, "AAPL", 10, days, {
            MODEL_ARIMA: 0.02 * 0.99,          # 1% better  -> admitted
            MODEL_HOLT_WINTERS: 0.02 * 0.997,  # 0.3% better -> not admitted
            MODEL_MONTE_CARLO: 0.03,           # worse       -> not admitted
        })
        stats = t.naive_gate_stats("AAPL", [10], window_days=365, as_of=AS_OF)
        assert stats["reason"] is None
        by = stats["by_horizon"][10]
        assert by[MODEL_ARIMA]["n"] == 60
        assert by[MODEL_ARIMA]["naive_median_abs_log_error"] == pytest.approx(0.02)
        assert by[MODEL_ARIMA]["median_abs_log_error"] == pytest.approx(0.0198)
        d = compute_naive_gate(by, [MODEL_ARIMA, MODEL_HOLT_WINTERS, MODEL_MONTE_CARLO], 0.005, 60)
        assert d["admitted"] == [MODEL_ARIMA]
        assert d["rejected"][MODEL_HOLT_WINTERS] == GATE_REASON_NOT_BETTER
        assert d["rejected"][MODEL_MONTE_CARLO] == GATE_REASON_NOT_BETTER

    def test_n_59_scored_days_is_not_enough(self, tmp_path):
        t = _tracker(tmp_path)
        _seed_ledger(t._db_path, "AAPL", 10, _days_before(AS_OF, 10, 59), {MODEL_ARIMA: 0.01})
        by = t.naive_gate_stats("AAPL", [10], window_days=365, as_of=AS_OF)["by_horizon"][10]
        assert by[MODEL_ARIMA]["n"] == 59
        d = compute_naive_gate(by, [MODEL_ARIMA], 0.005, 60)
        assert d["fallback_to_naive"] is True
        assert d["rejected"][MODEL_ARIMA] == GATE_REASON_TOO_FEW

    def test_intraday_duplicates_count_once(self, tmp_path):
        # 30 days, each recorded twice (a pre-F1 hourly duplicate, NULL day):
        # n must be 30, not 60.
        t = _tracker(tmp_path)
        days = _days_before(AS_OF, 10, 30)
        _seed_ledger(t._db_path, "AAPL", 10, days, {MODEL_ARIMA: 0.01})
        with closing(sqlite3.connect(t._db_path)) as conn, conn:
            conn.execute(
                """INSERT INTO forecast_errors (symbol, model_name, horizon_days, forecast_ts,
                       forecast_price, actual_price, squared_error, recorded_at, forecast_day)
                   SELECT symbol, model_name, horizon_days, replace(forecast_ts, 'T15', 'T16'),
                          forecast_price, actual_price, squared_error, recorded_at, NULL
                   FROM forecast_errors"""
            )
        by = t.naive_gate_stats("AAPL", [10], window_days=365, as_of=AS_OF)["by_horizon"][10]
        assert by[MODEL_ARIMA]["n"] == 30

    def test_sub_dollar_symbols_are_never_admitted(self, tmp_path):
        t = _tracker(tmp_path)
        _seed_ledger(t._db_path, "PENNY", 10, _days_before(AS_OF, 10, 80), {MODEL_ARIMA: 0.001}, p0=0.5)
        by = t.naive_gate_stats("PENNY", [10], window_days=365, as_of=AS_OF)["by_horizon"][10]
        assert by == {}
        assert compute_naive_gate(by, [MODEL_ARIMA], 0.005, 60)["fallback_to_naive"] is True

    def test_other_symbols_and_horizons_do_not_leak_in(self, tmp_path):
        t = _tracker(tmp_path)
        _seed_ledger(t._db_path, "MSFT", 10, _days_before(AS_OF, 10, 80), {MODEL_ARIMA: 0.001})
        _seed_ledger(t._db_path, "AAPL", 30, _days_before(AS_OF, 30, 80), {MODEL_ARIMA: 0.001})
        by = t.naive_gate_stats("AAPL", [10, 30], window_days=365, as_of=AS_OF)["by_horizon"]
        assert by[10] == {}
        assert by[30][MODEL_ARIMA]["n"] == 80

    def test_window_is_respected(self, tmp_path):
        t = _tracker(tmp_path)
        _seed_ledger(t._db_path, "AAPL", 10, _days_before(AS_OF, 10, 80), {MODEL_ARIMA: 0.001})
        by = t.naive_gate_stats("AAPL", [10], window_days=30, as_of=AS_OF)["by_horizon"][10]
        assert 0 < by[MODEL_ARIMA]["n"] < 30

    def test_read_error_is_reported_never_raised(self, tmp_path):
        t = ForecastTracker(db_path=str(tmp_path / "missing" / "nope.db"))
        out = t.naive_gate_stats("AAPL", [10, 30], window_days=365, as_of=AS_OF)
        assert out["reason"] == "error"
        assert out["by_horizon"] == {10: {}, 30: {}}


# ============================================================================
# Zero lookahead
# ============================================================================

class TestGateHasNoLookahead:
    """The gate for a forecast made on day D may only use outcomes that were
    final before D. Perturbing every row that had NOT matured by then (due on
    D or later, or forecast after D) must leave the gate's stats and decision
    bit-identical."""

    def _ledger_with_future_rows(self, tmp_path, name: str, future_err: float) -> str:
        t = _tracker(tmp_path, name)
        # 60 honest, matured-before-AS_OF days where ARIMA beats naive (admitted)...
        _seed_ledger(t._db_path, "AAPL", 10, _days_before(AS_OF, 10, 60), {MODEL_ARIMA: 0.01})
        # ...plus 65 days whose outcome is due ON or AFTER AS_OF's trading day
        # (actual already stamped, as update_actuals / a later backfill would),
        # and 5 days forecast AFTER AS_OF. These are the future.
        as_of_day = pd.Timestamp(AS_OF.date())
        due_today_or_later = pd.bdate_range(start=as_of_day - pd.offsets.BDay(10), periods=65)
        _seed_ledger(t._db_path, "AAPL", 10, due_today_or_later, {MODEL_ARIMA: future_err})
        after = pd.bdate_range(start=as_of_day + pd.offsets.BDay(1), periods=5)
        _seed_ledger(t._db_path, "AAPL", 10, after, {MODEL_ARIMA: future_err})
        return t._db_path

    def test_future_rows_cannot_move_the_gate(self, tmp_path):
        db_a = self._ledger_with_future_rows(tmp_path, "a.db", future_err=0.001)  # future: ARIMA great
        db_b = self._ledger_with_future_rows(tmp_path, "b.db", future_err=5.0)    # future: ARIMA awful
        sa = ForecastTracker(db_path=db_a).naive_gate_stats("AAPL", [10], 365, as_of=AS_OF)
        sb = ForecastTracker(db_path=db_b).naive_gate_stats("AAPL", [10], 365, as_of=AS_OF)
        assert sa == sb
        assert sa["by_horizon"][10][MODEL_ARIMA]["n"] == 60
        da = compute_naive_gate(sa["by_horizon"][10], [MODEL_ARIMA], 0.005, 60)
        db = compute_naive_gate(sb["by_horizon"][10], [MODEL_ARIMA], 0.005, 60)
        assert da == db
        assert da["admitted"] == [MODEL_ARIMA]

    def test_perturbation_is_not_vacuous(self, tmp_path):
        # Judged far enough in the future that those rows HAVE matured, the
        # awful future rows do change the decision -- so the test above is
        # measuring the cutoff, not a no-op.
        db_b = self._ledger_with_future_rows(tmp_path, "b.db", future_err=5.0)
        later = AS_OF + timedelta(days=120)
        sb = ForecastTracker(db_path=db_b).naive_gate_stats("AAPL", [10], 365, as_of=later)
        assert sb["by_horizon"][10][MODEL_ARIMA]["n"] > 60
        d = compute_naive_gate(sb["by_horizon"][10], [MODEL_ARIMA], 0.005, 60)
        assert d["fallback_to_naive"] is True

    def test_row_due_on_the_as_of_day_is_excluded(self, tmp_path):
        t = _tracker(tmp_path)
        as_of_day = pd.Timestamp(AS_OF.date())
        due_today = [as_of_day - pd.offsets.BDay(10)]
        _seed_ledger(t._db_path, "AAPL", 10, due_today, {MODEL_ARIMA: 0.01})
        assert t.naive_gate_stats("AAPL", [10], 365, as_of=AS_OF)["by_horizon"][10] == {}
        # One trading day later it counts.
        nxt = AS_OF + timedelta(days=1)
        assert t.naive_gate_stats("AAPL", [10], 365, as_of=nxt)["by_horizon"][10][MODEL_ARIMA]["n"] == 1

    def test_engine_passes_its_own_forecast_time_as_as_of(self, tmp_path, monkeypatch):
        seen = {}
        t = _tracker(tmp_path)
        real = ForecastTracker.naive_gate_stats

        def spy(self, symbol, horizons, window_days, as_of, min_price=1.0):
            seen["as_of"] = as_of
            seen["window"] = window_days
            return real(self, symbol, horizons, window_days, as_of, min_price)

        monkeypatch.setattr(ForecastTracker, "naive_gate_stats", spy)
        before = datetime.now(timezone.utc)
        _run_engine(ForecastingEngine(tracker=t))
        after = datetime.now(timezone.utc)
        assert before <= seen["as_of"] <= after
        assert seen["window"] == settings.FORECAST_SKILL_WINDOW_DAYS


# ============================================================================
# Engine: shadow rows, byte-identity, flag-on
# ============================================================================

def _run_engine(engine: ForecastingEngine, history: Optional[pd.Series] = None, symbol: str = "AAPL"):
    history = _price_series() if history is None else history
    row = pd.Series({"Symbol": symbol, "sector": "Unknown"})
    return engine.generate_forecast(
        row, float(history.iloc[-1]), history_series=history,
        precomputed_garch_term_structure=TERM_STRUCTURE,
    )


def _seed_admits_arima_at_h10(db: str, symbol: str = "AAPL") -> None:
    """70 matured days at h=10 where ARIMA beats naive by far (0.005 vs
    0.02); every other model/horizon has no scored history."""
    today = pd.Timestamp(_today_et())
    days = pd.bdate_range(end=today - pd.offsets.BDay(15), periods=70)
    _seed_ledger(db, symbol, 10, days, {MODEL_ARIMA: 0.005, MODEL_MONTE_CARLO: 0.04})


def _results_equal(a: dict, b: dict) -> bool:
    if set(a) != set(b):
        return False
    for k in a:
        x, y = a[k], b[k]
        if isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y):
            continue
        if x != y:
            return False
    return True


def _disable_gate_code(monkeypatch):
    """The baseline: the same engine with the F3 gate removed (the stats read
    never happens and the gated forecast is never computed)."""
    def _no_stats(*a, **k):
        raise AssertionError("baseline must not read gate stats")

    monkeypatch.setattr(ForecastTracker, "naive_gate_stats", _no_stats)
    monkeypatch.setattr(
        ForecastingEngine, "_naive_gated_forecast",
        staticmethod(lambda *a, **k: (None, None, False)),
    )


class TestShadowRecording:
    def test_gated_blend_recorded_at_every_horizon_with_upsert_key(self, tmp_path):
        t = _tracker(tmp_path)
        _run_engine(ForecastingEngine(tracker=t))
        _run_engine(ForecastingEngine(tracker=t))  # a second hourly cycle, same day
        rows = [r for r in _rows_on(t._db_path, _today_et()) if r[1] == MODEL_GATED_BLEND]
        assert sorted(r[2] for r in rows) == list(HORIZONS)  # one row per horizon, upserted
        naive = {r[2]: r[3] for r in _rows_on(t._db_path, _today_et()) if r[1] == MODEL_NAIVE}
        for r in rows:
            # Empty ledger: nothing admitted -> gated_blend IS naive, flagged.
            assert r[3] == naive[r[2]]
            assert r[8] == 1 and r[9] == ""

    def test_gated_blend_is_the_admitted_model_and_is_flagged(self, tmp_path):
        t = _tracker(tmp_path)
        _seed_admits_arima_at_h10(t._db_path)
        out = _run_engine(ForecastingEngine(tracker=t))
        today = {(r[1], r[2]): r for r in _rows_on(t._db_path, _today_et())}
        gated10 = today[(MODEL_GATED_BLEND, 10)]
        assert gated10[3] == today[(MODEL_ARIMA, 10)][3]  # only ARIMA admitted
        assert gated10[8] == 0 and gated10[9] == MODEL_ARIMA
        assert gated10[3] != today[(MODEL_BLEND, 10)][3]  # the shadow really differs
        # The published forecast and the blend row are the live (ungated) blend.
        assert out["Forecast_10"] == today[(MODEL_BLEND, 10)][3]
        for h in (30, 60, 90):
            assert today[(MODEL_GATED_BLEND, h)][8] == 1
            assert today[(MODEL_GATED_BLEND, h)][3] == today[(MODEL_NAIVE, h)][3]

    def test_upsert_overwrites_gate_metadata(self, tmp_path):
        t = _tracker(tmp_path)
        ts = datetime.now(timezone.utc)
        t.record_forecasts("AAPL", 10, {MODEL_GATED_BLEND: 100.0}, ts,
                           gate_meta={MODEL_GATED_BLEND: (True, [])})
        t.record_forecasts("AAPL", 10, {MODEL_GATED_BLEND: 101.0, MODEL_ARIMA: 101.0}, ts,
                           gate_meta={MODEL_GATED_BLEND: (False, ["holt_winters", "arima"])})
        rows = {r[1]: r for r in _rows_on(t._db_path)}
        assert len(_rows_on(t._db_path)) == 2
        assert rows[MODEL_GATED_BLEND][3] == 101.0
        assert rows[MODEL_GATED_BLEND][8] == 0
        assert rows[MODEL_GATED_BLEND][9] == "arima,holt_winters"
        assert rows[MODEL_ARIMA][8] is None and rows[MODEL_ARIMA][9] is None

    def test_no_shadow_row_when_gate_stats_unreadable(self, tmp_path, monkeypatch):
        t = _tracker(tmp_path)
        monkeypatch.setattr(ForecastTracker, "naive_gate_stats",
                            lambda *a, **k: {"by_horizon": {}, "reason": "error"})
        _run_engine(ForecastingEngine(tracker=t))
        names = {r[1] for r in _rows_on(t._db_path)}
        assert MODEL_GATED_BLEND not in names
        assert {MODEL_NAIVE, MODEL_BLEND} <= names

    def test_no_tracker_no_gate(self):
        engine = ForecastingEngine()
        out = _run_engine(engine)
        assert not any(k.endswith("_Gated_Naive") for k in out)
        assert not any(k.startswith("gate_") for k in engine.pop_guard_stats())


class TestGatedBlendNeverEntersSkillArithmetic:
    def test_pure_weights_ignore_gated_blend(self):
        base = {MODEL_ARIMA: (80, 4.0), MODEL_HOLT_WINTERS: (80, 1.0), MODEL_NAIVE: (80, 2.0)}
        with_gated = {**base, MODEL_GATED_BLEND: (80, 1e-9)}
        assert compute_skill_weights_from_stats(with_gated, 30) == compute_skill_weights_from_stats(base, 30)
        assert MODEL_GATED_BLEND not in compute_skill_weights_from_stats(with_gated, 30)

    def test_gated_blend_alone_cannot_end_the_cold_start(self):
        cold = {MODEL_ARIMA: (5, 4.0), MODEL_HOLT_WINTERS: (5, 1.0)}
        assert compute_skill_weights_from_stats({**cold, MODEL_GATED_BLEND: (500, 1e-9)}, 30) == \
            compute_skill_weights_from_stats(cold, 30)

    def test_ledger_weights_ignore_gated_blend_rows(self, tmp_path):
        t = _tracker(tmp_path)
        days = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.offsets.BDay(15), periods=40)
        _seed_ledger(t._db_path, "AAPL", 10, days, {MODEL_ARIMA: 0.01, MODEL_HOLT_WINTERS: 0.03})
        before = t.get_skill_weights("AAPL", 10, window_days=365, min_obs=30)
        _seed_ledger(t._db_path, "AAPL", 10, days, {MODEL_GATED_BLEND: 0.0001})
        with closing(sqlite3.connect(t._db_path)) as conn, conn:  # drop the extra naive rows the seeder added
            conn.execute("""DELETE FROM forecast_errors WHERE model_name = 'naive' AND id NOT IN (
                                SELECT MIN(id) FROM forecast_errors WHERE model_name = 'naive'
                                GROUP BY forecast_day)""")
        after = t.get_skill_weights("AAPL", 10, window_days=365, min_obs=30)
        assert after == before
        assert MODEL_GATED_BLEND not in after

    def test_gated_blend_is_never_blended(self, tmp_path):
        weights = {MODEL_GATED_BLEND: 1.0}
        out = ForecastingEngine._blend_with_skill({MODEL_ARIMA: 110.0}, weights, "MC", 100.0)
        assert out == ForecastingEngine._blend_with_skill({MODEL_ARIMA: 110.0}, {}, "MC", 100.0)


class TestFlagOffIsByteIdentical:
    """FORECAST_NAIVE_GATE_ENABLED=False (the default): the published outputs
    and the ``blend`` rows equal those of the same engine with the gate code
    removed, on a ledger where the gate WOULD change the forecast."""

    @pytest.mark.parametrize("skill_weighting", [False, True])
    @pytest.mark.parametrize("seed", [0, 3, 11])
    def test_published_outputs_and_blend_rows_identical(self, tmp_path, monkeypatch, seed, skill_weighting):
        monkeypatch.setattr(settings, "FORECAST_SKILL_WEIGHTING_ENABLED", skill_weighting)
        seeded = _tracker(tmp_path, "seed.db")
        _seed_admits_arima_at_h10(seeded._db_path)
        db_gate = str(tmp_path / "gate.db")
        db_base = str(tmp_path / "base.db")
        shutil.copy(seeded._db_path, db_gate)
        shutil.copy(seeded._db_path, db_base)
        history = _price_series(seed=seed)

        engine_gate = ForecastingEngine(tracker=ForecastTracker(db_path=db_gate))
        with_gate = _run_engine(engine_gate, history)
        gate_stats = engine_gate.pop_guard_stats()
        assert gate_stats.get("gate_admitted_horizons") == 1  # the gate really ran and admitted

        with monkeypatch.context() as m:
            _disable_gate_code(m)
            baseline = _run_engine(ForecastingEngine(tracker=ForecastTracker(db_path=db_base)), history)

        assert _results_equal(with_gate, baseline)
        today = _today_et()
        gate_rows = [r for r in _rows_on(db_gate, today) if r[1] != MODEL_GATED_BLEND]
        base_rows = _rows_on(db_base, today)
        assert gate_rows == base_rows  # every non-shadow row, blend included
        # Non-vacuous: the shadow row differs from the published blend.
        shadow = {r[2]: r[3] for r in _rows_on(db_gate, today) if r[1] == MODEL_GATED_BLEND}
        assert shadow[10] != with_gate["Forecast_10"]

    def test_default_setting_is_off(self):
        from settings import Settings
        assert Settings.model_fields["FORECAST_NAIVE_GATE_ENABLED"].default is False
        assert Settings.model_fields["FORECAST_NAIVE_GATE_MIN_IMPROVEMENT"].default == 0.005
        assert Settings.model_fields["FORECAST_NAIVE_GATE_MIN_OBS"].default == 60


class TestFlagOn:
    def test_published_forecast_is_the_gated_one(self, tmp_path, monkeypatch):
        seeded = _tracker(tmp_path, "seed.db")
        _seed_admits_arima_at_h10(seeded._db_path)
        db_off = str(tmp_path / "off.db")
        db_on = str(tmp_path / "on.db")
        shutil.copy(seeded._db_path, db_off)
        shutil.copy(seeded._db_path, db_on)
        history = _price_series(seed=5)
        price = float(history.iloc[-1])

        off = _run_engine(ForecastingEngine(tracker=ForecastTracker(db_path=db_off)), history)
        monkeypatch.setattr(settings, "FORECAST_NAIVE_GATE_ENABLED", True)
        on = _run_engine(ForecastingEngine(tracker=ForecastTracker(db_path=db_on)), history)

        rows_on = {(r[1], r[2]): r for r in _rows_on(db_on, _today_et())}
        # h=10: only ARIMA admitted -> published == ARIMA's forecast == shadow row.
        assert on["Forecast_10"] == rows_on[(MODEL_ARIMA, 10)][3]
        assert on["Forecast_10"] == rows_on[(MODEL_GATED_BLEND, 10)][3]
        assert on["Forecast_10"] != off["Forecast_10"]
        assert on["Forecast_10_Gated_Naive"] is False
        assert on["Forecast_10_Is_Fallback"] is False
        # h=30/60/90: nothing scored -> naive, disclosed as a fallback.
        for h in (30, 60, 90):
            assert on[f"Forecast_{h}"] == price
            assert on[f"Forecast_{h}_Gated_Naive"] is True
            assert on[f"Forecast_{h}_Is_Fallback"] is True
        # The blend row keeps recording the ungated blend (continuity for the
        # side-by-side after the flip).
        for h in HORIZONS:
            assert rows_on[(MODEL_BLEND, h)][3] == off[f"Forecast_{h}"]
        # Everything that is not a Forecast_{h} value or a gate flag is unchanged.
        gate_keys = {f"Forecast_{h}" for h in HORIZONS} | {f"Forecast_{h}_Is_Fallback" for h in HORIZONS} \
            | {f"Forecast_{h}_Gated_Naive" for h in HORIZONS}
        assert _results_equal({k: v for k, v in on.items() if k not in gate_keys},
                              {k: v for k, v in off.items() if k not in gate_keys})

    def test_fallback_scores_neutral_in_forecast_alignment(self):
        from signals.forecast_alignment import ForecastAlignmentSignal
        sig = ForecastAlignmentSignal()
        # A gated-naive Forecast_30 equals the price; flagged as a fallback it
        # is neutral (0), where unflagged it would read as bearish (-10).
        flagged = sig.compute(pd.Series({"current_price": 50.0, "forecast_price": 50.0,
                                         "forecast_is_fallback": True}), context=None)
        unflagged = sig.compute(pd.Series({"current_price": 50.0, "forecast_price": 50.0}), context=None)
        assert flagged.score == 0.0
        assert unflagged.score == -1.0

    def test_flag_on_without_tracker_changes_nothing(self, monkeypatch):
        history = _price_series(seed=2)
        off = _run_engine(ForecastingEngine(), history)
        monkeypatch.setattr(settings, "FORECAST_NAIVE_GATE_ENABLED", True)
        on = _run_engine(ForecastingEngine(), history)
        assert _results_equal(on, off)

    def test_flag_on_unreadable_ledger_publishes_naive(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "FORECAST_NAIVE_GATE_ENABLED", True)
        monkeypatch.setattr(ForecastTracker, "naive_gate_stats",
                            lambda *a, **k: {"by_horizon": {}, "reason": "error"})
        history = _price_series(seed=4)
        engine = ForecastingEngine(tracker=_tracker(tmp_path))
        out = _run_engine(engine, history)
        for h in HORIZONS:
            assert out[f"Forecast_{h}"] == float(history.iloc[-1])
            assert out[f"Forecast_{h}_Is_Fallback"] is True
            assert out[f"Forecast_{h}_Gated_Naive"] is True
        assert engine.pop_guard_stats()["gate_stats_unavailable"] == len(HORIZONS)


# ============================================================================
# scripts/forecast_skill_report.py --gate
# ============================================================================

def _seed_side_by_side(db: str, n_days: int, blend_err: float, gated_err: float,
                       fallback_every: int = 0) -> None:
    today = pd.Timestamp(_today_et())
    days = pd.bdate_range(end=today - pd.offsets.BDay(15), periods=n_days)
    _seed_ledger(db, "AAPL", 10, days, {MODEL_BLEND: blend_err, MODEL_GATED_BLEND: gated_err})
    with closing(sqlite3.connect(db)) as conn, conn:
        for i, d in enumerate(days):
            fb = 1 if fallback_every and i % fallback_every == 0 else 0
            conn.execute(
                "UPDATE forecast_errors SET gate_fallback = ?, gate_admitted = ? "
                "WHERE model_name = 'gated_blend' AND forecast_day = ?",
                (fb, "" if fb else "arima,holt_winters", d.strftime("%Y-%m-%d")),
            )


class TestGateReport:
    def test_side_by_side_metrics(self, tmp_path):
        from scripts.forecast_skill_report import build_gate_report, render_gate_text
        t = _tracker(tmp_path)
        _seed_side_by_side(t._db_path, 20, blend_err=0.03, gated_err=0.01, fallback_every=4)
        rep = build_gate_report([10, 30], window_days=365, db_path=t._db_path)
        h10 = rep["horizons"][0]
        assert h10["n_common_days"] == 20
        assert h10["models"][MODEL_BLEND]["median_abs_log_error"] == pytest.approx(0.03)
        assert h10["models"][MODEL_GATED_BLEND]["median_abs_log_error"] == pytest.approx(0.01)
        assert h10["models"][MODEL_GATED_BLEND]["naive_median_abs_log_error"] == pytest.approx(0.02)
        assert h10["head_to_head"]["pct_beating_naive"] == 1.0  # gated better every day
        assert h10["activity"]["days"] == 20
        assert h10["activity"]["fallback_days"] == 5
        assert h10["activity"]["admitted_counts"] == {"arima": 15, "holt_winters": 15}
        text = render_gate_text(rep)
        assert "blend vs gated_blend vs naive" in text
        assert "fell back to naive on 5 (25.0%)" in text
        assert "gated_blend vs blend: better on 100.0% of 20 days" in text
        # h=30 has nothing: honest n=0.
        assert rep["horizons"][1]["models"] == {}
        assert "n=0, not yet scorable" in text

    def test_pending_only_reports_when_first_rows_mature(self, tmp_path):
        from scripts.forecast_skill_report import build_gate_report, render_gate_text
        t = _tracker(tmp_path)
        _run_engine(ForecastingEngine(tracker=t))  # today's shadow rows, all pending
        rep = build_gate_report(list(HORIZONS), window_days=365, db_path=t._db_path)
        for res in rep["horizons"]:
            assert res["models"] == {}
            assert res["activity"]["days"] == 1
            assert res["activity"]["matured_days"] == 0
            expected_due = (pd.Timestamp(_today_et()) + pd.offsets.BDay(res["horizon_days"])).strftime("%Y-%m-%d")
            assert res["activity"]["first_pending_due"] == expected_due
        text = render_gate_text(rep)
        assert "n=0, not yet scorable" in text
        assert "first gated_blend rows mature ~" in text

    def test_pre_f3_ledger_degrades_honestly(self, tmp_path):
        from scripts.forecast_skill_report import build_gate_report, render_gate_text
        db = str(tmp_path / "old.db")
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("""CREATE TABLE forecast_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, model_name TEXT,
                horizon_days INTEGER, forecast_ts TEXT, forecast_price REAL,
                actual_price REAL, squared_error REAL, forecast_lower REAL,
                forecast_upper REAL, recorded_at TEXT, forecast_day TEXT)""")
        rep = build_gate_report([10], window_days=365, db_path=db)
        assert rep["horizons"][0]["reason"] == "ledger predates F3 (no gated_blend rows yet)"
        assert "n=0, not yet scorable" in render_gate_text(rep)

    def test_cli_gate_flag(self, tmp_path, capsys):
        from scripts.forecast_skill_report import main
        t = _tracker(tmp_path)
        _seed_side_by_side(t._db_path, 5, blend_err=0.03, gated_err=0.01)
        assert main(["--gate", "--db", t._db_path, "--horizon", "10", "--window-days", "365"]) == 0
        out = capsys.readouterr().out
        assert "gated_blend" in out and "gate: OFF (shadow only)" in out
