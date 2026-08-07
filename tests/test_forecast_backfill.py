"""
tests/test_forecast_backfill.py
================================
Unit tests for the Multi-Horizon Forecast Backfill & Meta-Labeling Engine.
"""

import json
from pathlib import Path
import pytest
import pandas as pd
import numpy as np

import pilots.watchlist_writer as watchlist_writer
from ml.forecast_backfill import AgenticForecastBackfiller
from settings import settings


@pytest.fixture(autouse=True)
def _isolate_output_dir(tmp_path, monkeypatch):
    """Every test in this file that calls export_results() must never write
    into the real, operator-facing output/ directory. AgenticForecastBackfiller
    reads settings.OUTPUT_DIR live (not a cached module-level path), so
    monkeypatching it here is sufficient -- without this, running this file
    clobbers the live output/agentic_forecast_summary.json that
    GET /pilots/forecast_backfill serves verbatim, which is exactly how a
    ZZZZ_NOT_REAL synthetic-fallback ticker used to leak into the webapp's
    Forecast Backfill screen after a local test run.

    Same reasoning applies to step_1_fetch_data's 3-strike ticker-drop path
    (record_fetch_failures): it defaults to
    pilots.watchlist_writer.DEFAULT_WATCHLIST_PATH ("watchlist.txt", CWD-
    relative) and a sibling watchlist_failures.json when no explicit path is
    passed -- exactly what every call in this file does. Left unpatched, a
    ZZZZ_NOT_REAL-style test run would silently rewrite the operator's real
    watchlist.txt / watchlist_failures.json in the repo root."""
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(watchlist_writer, "DEFAULT_WATCHLIST_PATH", tmp_path / "watchlist.txt")


@pytest.mark.parametrize(
    "bad_horizon",
    [-1, 0, 3651, 1.5, "10", True, "../../etc/passwd"],
)
def test_backfiller_rejects_invalid_horizons(bad_horizon):
    """`horizons` ends up in a model filename that gets opened for writing
    (ml/forecast_backfill.py's meta_{model_type}_{h}d.pkl) -- CodeQL flagged
    this as uncontrolled data in a path expression, since it is reachable
    from POST /pilots/forecast_backfill/run's request body. Every horizon
    must be constrained to a small positive int before it ever reaches a
    path, regardless of caller (API, CLI script, or direct construction)."""
    with pytest.raises(ValueError):
        AgenticForecastBackfiller(horizons=[10, bad_horizon])


def test_default_start_date_is_lookback_years_before_end_date():
    """When start_date isn't supplied, it must be computed as
    FORECAST_BACKFILL_LOOKBACK_YEARS back from end_date -- not a fixed
    calendar-date literal (which would grow the window unbounded on every
    future re-run instead of rolling forward)."""
    engine = AgenticForecastBackfiller(end_date="2026-06-15")
    expected = (pd.Timestamp("2026-06-15") - pd.DateOffset(years=settings.FORECAST_BACKFILL_LOOKBACK_YEARS))
    assert engine.start_date == expected.strftime("%Y-%m-%d")


def test_explicit_start_date_overrides_the_default():
    engine = AgenticForecastBackfiller(start_date="2010-01-01", end_date="2026-06-15")
    assert engine.start_date == "2010-01-01"


def test_backfiller_initialization():
    """Verify parameters are loaded from settings defaults with zero hardcoded values."""
    engine = AgenticForecastBackfiller()
    assert engine.horizons == settings.FORECAST_BACKFILL_HORIZONS
    assert engine.momentum_window == settings.FORECAST_BACKFILL_MOMENTUM_WINDOW
    assert engine.vol_short_window == settings.FORECAST_BACKFILL_VOL_SHORT_WINDOW
    assert engine.vol_long_window == settings.FORECAST_BACKFILL_VOL_LONG_WINDOW
    assert engine.rsi_window == settings.FORECAST_BACKFILL_RSI_WINDOW
    assert engine.macd_fast == settings.FORECAST_BACKFILL_MACD_FAST
    assert engine.macd_slow == settings.FORECAST_BACKFILL_MACD_SLOW
    assert engine.vol_ratio_window == settings.FORECAST_BACKFILL_VOL_RATIO_WINDOW
    assert engine.train_split == settings.FORECAST_BACKFILL_TRAIN_SPLIT
    assert engine.n_estimators == settings.FORECAST_BACKFILL_N_ESTIMATORS
    assert engine.max_depth == settings.FORECAST_BACKFILL_MAX_DEPTH


@pytest.mark.network
def test_forecast_backfill_end_to_end_pipeline(tmp_path):
    """Test full 6-step forecast backfill pipeline using synthetic data.

    Marked network (2026-08): despite `use_fmp=False`, step_1_fetch_data()
    still falls back to CompositeProvider -- a REAL yfinance call, not a
    synthetic-data path (this engine has no synthetic-data generator at
    all). Left unmarked, this test was exposed to real Yahoo Finance rate
    limiting ("Too Many Requests") whenever run alongside the rest of the
    suite, deselected from the "not network" fast/offline gate.

    Strategy identifiers are read dynamically from `signals.registry.
    global_registry` / `engine.active_strategies` rather than hardcoded
    ("TSMOM"/"CSMOM") -- the whole point of the registry-driven pipeline is
    that a new SignalModule with `meta_label_features` declared is picked
    up automatically, and a test hardcoded to two strategy names would
    silently stop covering a third one added later (as it already did once:
    see the module docstring change accompanying this test rewrite).
    """
    from signals.registry import global_registry

    tickers = ["AAPL", "MSFT", "AMZN", "NVDA"]
    horizons = [10, 30, 60, 90]

    engine = AgenticForecastBackfiller(
        tickers=tickers,
        start_date="2018-01-01",
        end_date="2022-01-01",
        horizons=horizons,
        n_estimators=10,
        max_depth=3,
        use_fmp=False,  # force synthetic/fallback
    )

    # Step 1: Data fetching
    prices = engine.step_1_fetch_data()
    assert not prices.empty
    assert set(tickers).issubset(set(prices.columns))

    # Step 2: Technical features
    features = engine.step_2_calculate_technical_features()
    assert not features.empty
    for col in ["Vol_20", "Vol_50", "RSI_14", "MACD", "Vol_Ratio"]:
        assert col in features.columns

    # Step 3: Primary signals -- at minimum the two baseline momentum
    # strategies must always make it through (their required_features are
    # a subset of what step 2 always computes).
    signals = engine.step_3_generate_primary_signals()
    assert {"timeseries_momentum", "cross_sectional_momentum"}.issubset(set(engine.active_strategies))
    for name in engine.active_strategies:
        assert f"{name}_Signal" in signals.columns
    assert set(signals["timeseries_momentum_Signal"].dropna().unique()).issubset({-1.0, 1.0})

    # Step 4: Meta-targets
    targets = engine.step_4_create_meta_targets()
    for name in engine.active_strategies:
        for h in horizons:
            assert f"{name}_Target_{h}d" in targets.columns

    # Step 5: Backtrain meta labelers -- only strategies that declare
    # meta_label_features actually train (see step_5's own skip-with-warning
    # for a strategy that declares none). A declared-trainable strategy can
    # still legitimately end up with zero rows surviving step 5's dropna
    # (insufficient real market-data history for some registered-but-
    # untested module), so `metrics.keys()` is asserted as a SUBSET of what
    # could train, not an exact match -- but timeseries_momentum (a plain
    # per-row signal) AND cross_sectional_momentum (the two-phase,
    # per-date-pre_compute signal _run_cross_sectional_module exists for --
    # see step_3_generate_primary_signals) are both asserted to train
    # successfully across every horizon unconditionally, since both are
    # known-reliable given this test's real tickers/date range.
    trainable = [
        name for name in engine.active_strategies
        if getattr(global_registry.get(name), "meta_label_features", [])
    ]
    assert {"timeseries_momentum", "cross_sectional_momentum"}.issubset(trainable)
    metrics = engine.step_5_backtrain_meta_labelers()
    candidate_keys = {
        f"{name}_{h}d"
        for name in trainable
        for h in (getattr(global_registry.get(name), "meta_label_horizons", None) or horizons)
    }
    assert set(metrics.keys()).issubset(candidate_keys)
    assert {f"timeseries_momentum_{h}d" for h in horizons}.issubset(metrics.keys())
    assert {f"cross_sectional_momentum_{h}d" for h in horizons}.issubset(metrics.keys())
    for model_key, m in metrics.items():
        assert "accuracy" in m
        assert "auc" in m
        assert "n_train" in m
        assert m["n_train"] > 0

    # Step 6: Continuous inference backfill
    backfill_df = engine.step_6_execute_backfill()
    for name in trainable:
        for h in (getattr(global_registry.get(name), "meta_label_horizons", None) or horizons):
            assert f"{name}_Meta_Prob_{h}d" in backfill_df.columns

    # Export results
    out_df, summary = engine.export_results(filename="test_backfill_output.csv")
    assert not out_df.empty
    assert summary["total_rows"] == len(out_df)
    assert set(summary["metrics"].keys()) == set(metrics.keys())


@pytest.mark.network
def test_step_6_no_model_produces_nan_not_fabricated_confidence():
    """A horizon/model that never trained (e.g. insufficient samples) must
    leave its Meta_Prob column as NaN, never a fabricated placeholder like
    1.0 (CONSTRAINT #4) -- a fake 100%-confidence value would otherwise be
    indistinguishable from a genuine, trained prediction downstream.

    Marked network (2026-08): calls step_1_fetch_data(), a real yfinance
    call via CompositeProvider -- see test_forecast_backfill_end_to_end_
    pipeline's marker comment for the full rationale."""
    engine = AgenticForecastBackfiller(
        tickers=["AAPL", "MSFT"],
        start_date="2018-01-01",
        end_date="2022-01-01",
        horizons=[10],
        use_fmp=False,
    )
    engine.step_1_fetch_data()
    engine.step_2_calculate_technical_features()
    engine.step_3_generate_primary_signals()
    engine.step_4_create_meta_targets()
    engine.step_5_backtrain_meta_labelers()

    # Simulate "no model trained for this horizon" (e.g. too few samples).
    assert "timeseries_momentum" in engine.active_strategies
    model_key = "timeseries_momentum_10d"
    prob_col = "timeseries_momentum_Meta_Prob_10d"
    engine.models.pop(model_key, None)
    engine.step_6_execute_backfill()

    assert engine.data[prob_col].isna().all()
    assert not (engine.data[prob_col] == 1.0).any()


@pytest.mark.network
def test_dropped_fallback_is_flagged_and_removed(tmp_path):
    """When neither FMP nor CompositeProvider returns data for a ticker, it must
    be dropped from the run and surfaced in the exported summary -- a provider
    outage must never look like a genuine backtest (CONSTRAINT #4).

    Marked network (2026-08): AAPL is expected to succeed as the control
    ticker while ZZZZ_NOT_REAL is expected to fail -- both go through a
    real yfinance call via CompositeProvider. See
    test_forecast_backfill_end_to_end_pipeline's marker comment."""
    engine = AgenticForecastBackfiller(
        tickers=["AAPL", "ZZZZ_NOT_REAL"],
        start_date="2020-01-01",
        end_date="2022-01-01",
        horizons=[10],
        use_fmp=False,
    )
    engine.step_1_fetch_data()
    assert "ZZZZ_NOT_REAL" in engine.dropped_tickers
    assert "ZZZZ_NOT_REAL" not in engine.tickers

    # One miss is a single strike, not yet a permanent removal (see the
    # 3-consecutive-runs test below for the removal path).
    failures_file = tmp_path / "watchlist_failures.json"
    assert json.loads(failures_file.read_text(encoding="utf-8")) == {"ZZZZ_NOT_REAL": 1}

    engine.step_2_calculate_technical_features()
    engine.step_3_generate_primary_signals()
    engine.step_4_create_meta_targets()
    engine.step_5_backtrain_meta_labelers()
    engine.step_6_execute_backfill()
    _, summary = engine.export_results(filename="test_dropped_flag_output.csv")
    assert summary["dropped_tickers"] == ["ZZZZ_NOT_REAL"]


@pytest.mark.network
def test_three_consecutive_dropped_runs_permanently_removes_from_watchlist(tmp_path):
    """The 3-strike rule: a ticker missing real data across 3 SEPARATE
    step_1_fetch_data runs (e.g. 3 backfill cycles days apart) is permanently
    removed from watchlist.txt, not just dropped from each individual run.

    Marked network (2026-08): three real step_1_fetch_data() calls, each a
    real yfinance call via CompositeProvider, with AAPL expected to succeed
    as the control ticker. See test_forecast_backfill_end_to_end_pipeline's
    marker comment."""
    watchlist_path = tmp_path / "watchlist.txt"
    watchlist_path.write_text("AAPL\nZZZZ_NOT_REAL\n", encoding="utf-8")

    for i in range(3):
        engine = AgenticForecastBackfiller(
            tickers=["AAPL", "ZZZZ_NOT_REAL"],
            start_date="2020-01-01",
            end_date="2022-01-01",
            horizons=[10],
            use_fmp=False,
        )
        engine.step_1_fetch_data()
        assert "ZZZZ_NOT_REAL" in engine.dropped_tickers, f"run {i}"

    content = watchlist_path.read_text(encoding="utf-8")
    assert "ZZZZ_NOT_REAL" not in content
    assert "AAPL" in content
    assert json.loads((tmp_path / "watchlist_failures.json").read_text(encoding="utf-8")) == {}


@pytest.mark.network
def test_train_test_split_embargoes_overlapping_forward_window(monkeypatch):
    """step_5's per-horizon CombinatorialPurgedCV must be configured so that
    training rows within `h` days of a test block boundary are purged/
    embargoed -- otherwise a target label derived from a forward return that
    extends `h` days past a row's date (see step_4) leaks test-period price
    information into training (the same overlapping-label leakage class
    validation/purged_cv.py and the CNN-LSTM purged split guard elsewhere in
    this codebase).

    step_5 no longer does a naive chronological 80/20 split (that mechanism
    is gone -- see the module's own docstring); it delegates purging/
    embargoing entirely to CombinatorialPurgedCV via a dynamically computed
    `embargo_pct` and a `t1` event-end series (`date + h days`). This test
    therefore verifies step_5 wires those two things correctly, rather than
    re-deriving the old split-and-embargo-by-hand logic that no longer
    exists in the implementation it would be asserting against.

    Marked network (2026-08): calls step_1_fetch_data(), a real yfinance
    call via CompositeProvider. See test_forecast_backfill_end_to_end_
    pipeline's marker comment."""
    import validation.purged_cv as purged_cv_module
    from validation.purged_cv import CombinatorialPurgedCV

    captured_calls = []

    class _RecordingCPCV(CombinatorialPurgedCV):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured_calls.append({"embargo_pct": self.embargo_pct, "t1": None})

        def split(self, X, y=None, t1=None):
            captured_calls[-1]["t1"] = t1
            captured_calls[-1]["dates"] = pd.Series(X.index)
            return super().split(X, y=y, t1=t1)

    horizon = 30
    engine = AgenticForecastBackfiller(
        tickers=["AAPL", "MSFT", "AMZN"],
        start_date="2018-01-01",
        end_date="2022-01-01",
        horizons=[horizon],
        use_fmp=False,
    )
    engine.step_1_fetch_data()
    engine.step_2_calculate_technical_features()
    engine.step_3_generate_primary_signals()
    engine.step_4_create_meta_targets()

    assert "timeseries_momentum" in engine.active_strategies
    # step_5 does `from validation.purged_cv import CombinatorialPurgedCV`
    # as a LOCAL import inside the method body, re-resolved from
    # validation.purged_cv's own namespace on every call -- patching that
    # module attribute (not a nonexistent module-level name on
    # ml.forecast_backfill) is what the local import actually picks up.
    monkeypatch.setattr(purged_cv_module, "CombinatorialPurgedCV", _RecordingCPCV)
    engine.step_5_backtrain_meta_labelers()

    assert captured_calls, "CombinatorialPurgedCV was never constructed/split"
    call = captured_calls[0]

    # 1. embargo_pct must be strictly positive and derived from `horizon`
    # relative to the number of unique dates in the training universe --
    # never the unconditional-leakage default of 0.0.
    assert call["embargo_pct"] > 0.0

    # 2. t1 (event end time) for every row must be exactly `horizon` days
    # after that row's own date -- this is what tells CombinatorialPurgedCV
    # to purge a training row whose forward-return window overlaps a test
    # block, regardless of which of the two touches a chronological
    # "80/20 split" boundary (CPCV has no single such boundary at all).
    t1 = call["t1"]
    assert t1 is not None
    implied_horizon_days = (t1.values - t1.index.values).astype("timedelta64[D]").astype(int)
    assert set(implied_horizon_days) == {horizon}


def _synthetic_engine(tickers, n_days=400, seed=42):
    """A fully offline AgenticForecastBackfiller -- synthetic price/volume
    data assigned directly to engine.prices/engine.volumes, bypassing
    step_1_fetch_data() (and its real network call) entirely. step_2 onward
    only ever reads self.prices/self.volumes, never start_date/end_date, so
    this is a faithful substitute for network-marked construction."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2018-01-01", periods=n_days)
    prices = pd.DataFrame(
        {
            t: 100.0 * np.cumprod(1.0 + rng.normal(0.0002 * (i + 1), 0.01, n_days))
            for i, t in enumerate(tickers)
        },
        index=dates,
    )
    volumes = pd.DataFrame({t: 1_000_000.0 for t in tickers}, index=dates)
    engine = AgenticForecastBackfiller(tickers=list(tickers), horizons=[10], use_fmp=False)
    engine.prices = prices
    engine.volumes = volumes
    return engine


def test_cross_sectional_module_wiring_produces_real_differentiated_per_date_ranks():
    """_run_cross_sectional_module (step 3's per-date two-phase hook replay)
    must actually invoke cross_sectional_momentum's real pre_compute/compute
    -- not silently no-op it. Regression coverage for the bug this was
    introduced to fix: previously, calling compute_vectorized() directly
    with no pre_compute() call left context.xsec_percentile_ranks
    permanently empty, so every ticker's score was flatly 0.0 regardless of
    its actual relative momentum -- indistinguishable from "this signal
    doesn't work" rather than "this signal was never actually invoked
    correctly." A genuinely-differentiated rank -- not just non-null -- is
    what proves pre_compute really ran with real per-ticker return data."""
    tickers = ["AAA", "BBB", "CCC", "DDD"]
    engine = _synthetic_engine(tickers)
    engine.step_2_calculate_technical_features()
    engine.step_3_generate_primary_signals()

    assert "cross_sectional_momentum" in engine.active_strategies
    sig = engine.data["cross_sectional_momentum_Signal"]
    assert sig.notna().any()

    valid_dates = sig.dropna().index.get_level_values("Date").unique().sort_values()
    assert len(valid_dates) > 20
    sample_date = valid_dates[10]
    day_scores = sig.xs(sample_date, level="Date")
    assert len(day_scores) == len(tickers)
    # The four synthetic tickers were constructed with strictly different
    # drift rates, so their cross-sectional rank on any shared date must be
    # differentiated, not a flat constant (which is what the pre-fix no-op
    # produced for every ticker on every date).
    assert day_scores.nunique() > 1


def test_cross_sectional_module_wiring_is_lookahead_free():
    """Perturbing a price strictly AFTER a given date must never change
    that date's cross-sectional rank/score -- the standard no-lookahead
    perturbation test this codebase requires for every indicator/forecaster
    (see CLAUDE.md's 'Every indicator and forecaster must be verified to
    have zero lookahead bias' convention). _compute_xsec_12_1m_wide is
    shift-based (causal by construction); this test verifies step 3's
    per-date pre_compute/compute replay doesn't reintroduce a leak on top
    of that (e.g. by accidentally sharing state across dates)."""
    tickers = ["AAA", "BBB", "CCC", "DDD"]

    baseline = _synthetic_engine(tickers)
    baseline.step_2_calculate_technical_features()
    baseline.step_3_generate_primary_signals()
    sig_before = baseline.data["cross_sectional_momentum_Signal"]

    valid_dates = sig_before.dropna().index.get_level_values("Date").unique().sort_values()
    cutoff_date = valid_dates[10]  # early in the valid range -- leaves room to perturb "the future"

    perturbed = _synthetic_engine(tickers)
    cutoff_pos = perturbed.prices.index.get_loc(cutoff_date)
    # Blow up AAA's price for every date strictly after the cutoff.
    perturbed.prices.iloc[cutoff_pos + 1 :, perturbed.prices.columns.get_loc("AAA")] *= 5.0
    perturbed.step_2_calculate_technical_features()
    perturbed.step_3_generate_primary_signals()
    sig_after = perturbed.data["cross_sectional_momentum_Signal"]

    before_day = sig_before.xs(cutoff_date, level="Date").sort_index()
    after_day = sig_after.xs(cutoff_date, level="Date").sort_index()
    pd.testing.assert_series_equal(before_day, after_day)


def test_forecast_backfill_api_endpoint(monkeypatch, tmp_path):
    """Test API endpoint response from api.pilots_api."""
    from fastapi.testclient import TestClient
    from api.pilots_api import app

    client = TestClient(app, client=("127.0.0.1", 50000))

    # Mock output file
    summary_file = tmp_path / "agentic_forecast_summary.json"
    mock_payload = {
        "status": "completed",
        "horizons": [10, 30, 60, 90],
        "metrics": {"TSMOM_10d": {"accuracy": 0.52, "auc": 0.54, "n_train": 1000}},
        "tickers": ["AAPL", "MSFT"],
    }

    summary_file.write_text(json.dumps(mock_payload))
    monkeypatch.setattr("settings.settings.OUTPUT_DIR", tmp_path)

    res = client.get("/pilots/forecast_backfill")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "completed"
    assert data["horizons"] == [10, 30, 60, 90]


def test_forecast_backfill_run_endpoint_rejects_invalid_horizons(monkeypatch):
    """POST /pilots/forecast_backfill/run's `horizons` reaches a model
    filename that gets opened for writing -- must 422 (Pydantic validation),
    never reach AgenticForecastBackfiller, for an out-of-range or non-integer
    horizon (CodeQL: uncontrolled data in a path expression)."""
    from unittest import mock
    from fastapi.testclient import TestClient
    from api.pilots_api import app
    from settings import settings as live_settings

    client = TestClient(app, client=("127.0.0.1", 50000))
    with mock.patch.object(live_settings, "FOLLOW_API_TOKEN", "cmd-tok"):
        res = client.post(
            "/pilots/forecast_backfill/run",
            json={"horizons": [10, -1]},
            headers={"Authorization": "Bearer cmd-tok"},
        )
    assert res.status_code == 422
