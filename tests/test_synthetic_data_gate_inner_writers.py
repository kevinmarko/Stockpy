"""
tests/test_synthetic_data_gate_inner_writers.py -- the gate reaches RunPipelineStep's writers
=============================================================================================

Follow-up to ``tests/test_synthetic_data_gate_keyless.py`` (plan Q2 of
``.claude/synthetic_data_gate_keyless_implementation_plan.md``; walkthrough
``.claude/synthetic_data_gate_inner_writers_walkthrough.md``).

``main_orchestrator.run_pipeline`` builds its own inner ``RunContext``, so the
outer cycle's synthetic flag never reached ``MacroStep`` ... ``StrategyEvalStep``.
A ``MockDataEngine`` cycle therefore still wrote: forecast-ledger rows AND
``update_actuals`` (stamping the synthetic price onto REAL past forecasts as
their realized actual), forecast model-cache artifacts, fabricated
``macro_history`` rows (labelled ``source="fred"``) and a warm HMM fit, the
day's PIT feature snapshot, ``dead_letter.json`` (incl. the "cleared"
rewrite), cap-audit and symbol-rating rows, and a sizing-cap alert. Now
``RunPipelineStep`` hands the flag over and each writer skips, while the cycle
still computes.

Every store here is the per-test temp copy from the root ``conftest.py``
isolation fixtures (forecast tracker, historical store, symbol rating, cap
audit, PIT feature store); nothing touches the operator's DB.
"""
from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from data_engine import MockDataEngine
from pipeline import synthetic_gate as sg
from pipeline.context import RunContext

# Two full _main_body cycles per class: keep them on one worker.
pytestmark = pytest.mark.xdist_group("synthetic_gate_inner_writers")

_N_BARS = 260  # > HMMRegimeDetector's 100-row minimum, > Prophet's 30-row minimum
_UNIVERSE = ["AAPL", "MSFT"]
_PREVIOUS_DEAD_LETTER = json.dumps(
    {"run_id": "previous-real-cycle", "entries": [{"symbol": "XYZ", "stage": "strategy"}]}
)
_SEEDED_FORECAST_PRICE = 123.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _price_path(n: int = _N_BARS) -> List[float]:
    rng = np.random.RandomState(7)
    return [float(p) for p in 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, n)))]


class _RealEngineOverMockData:
    """A data engine the gate treats as REAL (not a ``MockDataEngine``
    subclass, so ``data_engine.is_synthetic_provider`` is False) serving the
    exact same bars as the synthetic run, so the two cycles differ only in
    the flag."""

    def __init__(self, inner: MockDataEngine) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _OfflineProvider:
    """``data.market_data.get_provider()`` stand-in: every call fails, as with
    no network (the e2e test's pattern), so the cycle degrades honestly."""

    def get_latest_quote(self, *_a, **_k):
        raise OSError("no network in this test")

    def get_intraday_bars(self, *_a, **_k):
        raise OSError("no network in this test")

    def get_fundamentals(self, *_a, **_k):
        raise OSError("no network in this test")


def _count(db: Path, table: str, where: str = "") -> int:
    if not db.exists():
        return 0
    with sqlite3.connect(db) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            return 0
        return int(conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0])


def _seeded_actual(db: Path) -> Any:
    if _count(db, "forecast_errors") == 0:
        return "missing"
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT actual_price FROM forecast_errors WHERE forecast_price = ?",
            (_SEEDED_FORECAST_PRICE,),
        ).fetchone()
    return row[0] if row else "missing"


def _run_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: Any) -> Dict[str, Any]:
    """One real ``_main_body`` cycle (daemon shape: warm engines + injected
    data engine), offline, with every inner writer switched on. Returns what
    each writer left behind."""
    import engine.advisory as adv
    import forecasting.model_persistence as model_persistence
    import main_orchestrator as mo
    import ml.data.store as pit_store
    import ml.lgbm_ranker as lgbm
    import ml.meta_bootstrap as meta_boot
    import observability.alerts as alerts
    import pipeline.advisory_inputs as ai
    import shared.market_hours as market_hours
    from execution.kill_switch import GlobalKillSwitch
    from forecasting.forecast_tracker import ForecastTracker
    from settings import settings

    def _no_network(*_a, **_kw):
        raise OSError("network disabled in this test")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)

    out = tmp_path / "output"
    out.mkdir(parents=True)
    (out / "dead_letter.json").write_text(_PREVIOUS_DEAD_LETTER, encoding="utf-8")
    for key, val in {
        "OUTPUT_DIR": out,
        "ADVISORY_ONLY": True,
        "DAEMON_AGENTIC_QUEUE_MODE": "off",
        # Every inner writer on (the first five are already the defaults).
        "HISTORICAL_STORE_ENABLED": True,
        "PIT_CAPTURE_ENABLED": True,
        "SIZING_CAP_AUDIT_ENABLED": True,
        "SYMBOL_RATING_ENABLED": True,
        "FORECAST_MODEL_PERSISTENCE_ENABLED": True,
        "SIZING_CAP_ALERT_ENABLED": True,
        "SIZING_CAP_ALERT_THRESHOLD_PCT": 0.0,  # always fires on a real cycle
        "FORECAST_MAX_CONCURRENCY": 1,
        # Deterministic Monte Carlo, so two cycles on the same bars compare.
        "FORECAST_MC_RANDOM_SEED": 42,
        "ADVISORY_MAX_CONCURRENCY": 1,
        "FMP_API_KEY": "",
        "FMP_NEWS_ENABLED": False,
    }.items():
        monkeypatch.setattr(settings, key, val, raising=False)

    models_dir = tmp_path / "forecast_models"
    monkeypatch.setattr(model_persistence, "MODELS_DIR", models_dir)
    monkeypatch.setattr(market_hours, "is_us_market_open_now", lambda *_a, **_k: True)
    monkeypatch.setattr(
        ai, "build_universe_detailed",
        lambda snapshot, watchlist_file=None: SimpleNamespace(
            symbols=list(_UNIVERSE), watchlist=[], discovered=[], held=set(),
            recently_closed=set(), default_tickers_is_fallback=False,
        ),
    )
    monkeypatch.setattr("data.market_data.get_provider", lambda: _OfflineProvider())
    monkeypatch.setattr(ai, "fetch_bars_for_universe", lambda symbols, market: {})
    monkeypatch.setattr(ai, "build_context_extras", lambda *a, **k: {})
    monkeypatch.setattr(mo, "fetch_account_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(mo, "_DATA_REFRESH_MARKER", str(tmp_path / "last_data_refresh.txt"))
    monkeypatch.setattr(
        mo, "GlobalKillSwitch", lambda: GlobalKillSwitch(sentinel_file=tmp_path / "KILL_SWITCH"),
    )
    monkeypatch.setattr(mo, "_execute_broker_orders", mock.AsyncMock())

    def _no_model():
        raise FileNotFoundError("no LGBM model in this test")

    monkeypatch.setattr(lgbm.LGBMCrossSectionalRanker, "load_latest", staticmethod(_no_model))
    monkeypatch.setattr(meta_boot, "bootstrap_meta_registry", lambda *a, **kw: None)

    alerts_sent: List[Dict[str, Any]] = []

    def _record_alert(level, message, *a, **kw):
        alerts_sent.append({"level": level, "message": message, "dedup_key": kw.get("dedup_key")})

    monkeypatch.setattr(alerts, "send_alert", _record_alert)

    persist_flags: List[Any] = []
    real_evaluate = adv.evaluate

    def _recording_evaluate(**kw):
        persist_flags.append(kw.get("persist_forecast", "absent"))
        return real_evaluate(**kw)

    monkeypatch.setattr(adv, "evaluate", _recording_evaluate)

    dashboards: List[pd.DataFrame] = []
    real_validate = mo._validate_dashboard

    def _capture_dashboard(df, strict=False):
        dashboards.append(df.copy())
        return real_validate(df, strict=strict)

    monkeypatch.setattr(mo, "_validate_dashboard", _capture_dashboard)

    # A REAL past forecast, already due: a real cycle actualizes it
    # (update_actuals); a synthetic one must leave it pending.
    ForecastTracker().record_forecasts(
        "AAPL", 10, {"arima": _SEEDED_FORECAST_PRICE},
        datetime.now(timezone.utc) - timedelta(days=40),
    )

    engines = mo.EngineContext.build(data_engine=engine)
    assert engines.macro_engine._hmm_detector.last_fit_date is None
    asyncio.run(mo._main_body(effective_dry_run=True, engines=engines, data_engine=engine))

    tracker_db = tmp_path / "isolated_forecast_tracker.db"
    return {
        "forecast_rows": _count(tracker_db, "forecast_errors"),
        "seeded_actual": _seeded_actual(tracker_db),
        "dead_letter": (out / "dead_letter.json").read_text(encoding="utf-8"),
        "rating_rows": _count(tmp_path / "isolated_symbol_rating.db", "symbol_rating_events"),
        "cap_rows": _count(tmp_path / "isolated_cap_audit.db", "sizing_cap_events"),
        "macro_rows": _count(tmp_path / "pytest_isolated_historical_store.db", "macro_history"),
        "pit_files": sorted(p.name for p in Path(pit_store._CACHE_DIR).glob("*")),
        "model_files": sorted(p.name for p in models_dir.glob("*")) if models_dir.exists() else [],
        "hmm_fitted": engines.macro_engine._hmm_detector.last_fit_date is not None,
        "sizing_alerts": [a for a in alerts_sent if a["dedup_key"] == "sizing_cap_threshold"],
        "advisory_persist_flags": persist_flags,
        "dashboard": dashboards[-1] if dashboards else None,
    }


@pytest.fixture
def synthetic_cycle(tmp_path, monkeypatch):
    return _run_cycle(tmp_path, monkeypatch, MockDataEngine(preset_prices=_price_path()))


@pytest.fixture
def real_cycle(tmp_path, monkeypatch):
    return _run_cycle(
        tmp_path, monkeypatch, _RealEngineOverMockData(MockDataEngine(preset_prices=_price_path())),
    )


# ---------------------------------------------------------------------------
# Full _main_body cycle: Mock engine writes nothing, real engine still writes
# ---------------------------------------------------------------------------

class TestFullCycleInnerWriters:
    def test_mock_engine_cycle_leaves_no_inner_writes(self, synthetic_cycle):
        r = synthetic_cycle
        assert r["forecast_rows"] == 1, "only the seeded row: no new forecast-ledger rows"
        assert r["seeded_actual"] is None, "update_actuals stamped a synthetic price onto a real forecast"
        assert r["dead_letter"] == _PREVIOUS_DEAD_LETTER, "dead_letter.json was rewritten"
        assert r["rating_rows"] == 0
        assert r["cap_rows"] == 0
        assert r["macro_rows"] == 0, "fabricated macro history reached macro_history"
        assert r["pit_files"] == []
        assert r["model_files"] == []
        assert r["hmm_fitted"] is False, "the warm HMM detector kept a fit on synthetic bars"
        assert r["sizing_alerts"] == []
        assert r["advisory_persist_flags"] and set(r["advisory_persist_flags"]) == {False}

    def test_real_engine_cycle_still_writes_everything(self, real_cycle):
        # Control: the identical bars through an engine the gate calls real.
        r = real_cycle
        assert r["forecast_rows"] > 1
        assert r["seeded_actual"] not in (None, "missing")
        assert r["dead_letter"] != _PREVIOUS_DEAD_LETTER
        assert json.loads(r["dead_letter"])["entries"] == []
        assert r["rating_rows"] > 0
        assert r["cap_rows"] > 0
        assert r["macro_rows"] > 0
        assert len(r["pit_files"]) == 1 and r["pit_files"][0].startswith("features_")
        assert any(name.startswith("prophet_") for name in r["model_files"])
        assert r["hmm_fitted"] is True
        assert len(r["sizing_alerts"]) == 1
        assert r["advisory_persist_flags"] and set(r["advisory_persist_flags"]) == {True}


class TestComputationUnchanged:
    """Only writes are skipped: the dashboard a synthetic cycle computes
    matches the one the identical data computes on a 'real' cycle."""

    _COLUMNS = (
        "Price", "GARCH_Vol", "ARIMA", "Forecast_10", "Forecast_30", "Forecast_60",
        "Forecast_90", "Action Signal", "Kelly Target", "Score",
    )

    def test_dashboards_match(self, tmp_path, monkeypatch):
        import forecasting_engine

        # Prophet makes Forecast_30 differ between two identical REAL cycles
        # here (measured while writing this test), unrelated to the gate; off
        # in both runs so the comparison isolates the flag. Monte Carlo is
        # seeded in _run_cycle.
        monkeypatch.setattr(forecasting_engine, "PROPHET_AVAILABLE", False)
        synthetic = _run_cycle(tmp_path / "synthetic", monkeypatch, MockDataEngine(preset_prices=_price_path()))
        real = _run_cycle(
            tmp_path / "real", monkeypatch,
            _RealEngineOverMockData(MockDataEngine(preset_prices=_price_path())),
        )
        a = synthetic["dashboard"].set_index("Symbol").sort_index()
        b = real["dashboard"].set_index("Symbol").sort_index()
        assert list(a.index) == list(b.index) == sorted(_UNIVERSE)
        for col in self._COLUMNS:
            assert col in a.columns, col
            if pd.api.types.is_numeric_dtype(a[col]):
                np.testing.assert_allclose(
                    a[col].astype(float).to_numpy(), b[col].astype(float).to_numpy(),
                    rtol=1e-9, equal_nan=True, err_msg=col,
                )
            else:
                assert a[col].tolist() == b[col].tolist(), col


# ---------------------------------------------------------------------------
# Unit level: each seam
# ---------------------------------------------------------------------------

def _inner_ctx() -> RunContext:
    return RunContext(
        force_account=False, started_at=datetime.now(), watchlist_file="watchlist.txt",
        fetch_account_snapshot_fn=lambda *a, **k: None, build_universe_fn=lambda *a, **k: [],
        build_macro_dto_fn=lambda *a, **k: None, get_provider_fn=lambda *a, **k: None,
        fetch_bars_fn=lambda *a, **k: {}, build_context_extras_fn=lambda *a, **k: {},
        advisory_evaluate_fn=lambda *a, **k: None,
    )


class TestPropagation:
    def test_propagate_sets_flag_and_reason_without_logging_or_alerting(self, monkeypatch, caplog):
        sent = []
        monkeypatch.setattr(sg, "send_synthetic_cycle_alert", lambda reason: sent.append(reason))
        ctx = _inner_ctx()
        with caplog.at_level("DEBUG", logger="ProductionPipeline"):
            sg.propagate_synthetic_flag(ctx, sg.REASON_MOCK_ENGINE)
        assert sg.is_cycle_synthetic(ctx)
        assert sg.synthetic_reason(ctx) == sg.REASON_MOCK_ENGINE
        assert sent == [] and caplog.records == []

    @pytest.mark.parametrize("synthetic", [True, False])
    def test_run_pipeline_step_hands_the_flag_to_run_pipeline(self, monkeypatch, synthetic):
        import main_orchestrator as mo
        from pipeline.production_steps import RunPipelineStep

        seen: Dict[str, Any] = {}

        def _fake_run_pipeline(tickers, macro_raw, fund_raw, tech_raw, **kwargs):
            seen.update(kwargs)
            return pd.DataFrame(), None, None

        monkeypatch.setattr(mo, "run_pipeline", _fake_run_pipeline)
        ctx = _inner_ctx()
        if synthetic:
            ctx.context_extras[sg.SYNTHETIC_FLAG_KEY] = True
            ctx.context_extras[sg.SYNTHETIC_REASON_KEY] = sg.REASON_EMPTY_PRICE_FETCH
        RunPipelineStep().run(ctx)
        assert seen["data_is_synthetic"] is synthetic
        assert seen["synthetic_reason"] == (sg.REASON_EMPTY_PRICE_FETCH if synthetic else None)

    def test_run_pipeline_marks_the_inner_context(self, monkeypatch):
        import main_orchestrator as mo
        import pipeline.runner as runner_mod

        seen: Dict[str, Any] = {}

        class _Runner:
            def __init__(self, steps):
                pass

            def run(self, ctx, progress=None):
                seen["synthetic"] = sg.is_cycle_synthetic(ctx)
                seen["reason"] = sg.synthetic_reason(ctx)

        monkeypatch.setattr(runner_mod, "PipelineRunner", _Runner)
        mo.run_pipeline([], {}, {}, {}, data_is_synthetic=True, synthetic_reason=sg.REASON_MOCK_ENGINE)
        assert seen == {"synthetic": True, "reason": sg.REASON_MOCK_ENGINE}
        mo.run_pipeline([], {}, {}, {})
        assert seen == {"synthetic": False, "reason": None}


def _history(n: int = 120) -> pd.DataFrame:
    idx = pd.bdate_range(end=datetime.now(), periods=n)
    close = pd.Series(_price_path(n), index=idx)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": 1_000_000.0}, index=idx)


class TestForecastingEnginePersist:
    @pytest.mark.parametrize("persist", [False, True])
    def test_generate_forecast_ledger_writes_follow_persist(self, tmp_path, persist):
        from forecasting.forecast_tracker import ForecastTracker
        from forecasting_engine import ForecastingEngine

        tracker = ForecastTracker()
        tracker.record_forecasts("AAPL", 10, {"arima": _SEEDED_FORECAST_PRICE},
                                 datetime.now(timezone.utc) - timedelta(days=40))
        hist = _history()
        result = ForecastingEngine(tracker=tracker).generate_forecast(
            {"Symbol": "AAPL", "sector": "Technology"}, float(hist["Close"].iloc[-1]),
            hist["Close"], history_df=hist, persist=persist,
        )
        assert result["Forecast_30"] > 0  # it still forecasts
        db = tmp_path / "isolated_forecast_tracker.db"
        if persist:
            assert _count(db, "forecast_errors") > 1
            assert _seeded_actual(db) is not None
        else:
            assert _count(db, "forecast_errors") == 1
            assert _seeded_actual(db) is None

    @pytest.mark.parametrize("persist", [False, True])
    def test_prophet_cache_save_follows_persist_and_load_still_works(self, tmp_path, monkeypatch, persist):
        import forecasting.model_persistence as model_persistence
        import forecasting_engine
        from forecasting_engine import ForecastingEngine
        from settings import settings

        if not forecasting_engine.PROPHET_AVAILABLE:
            pytest.skip("prophet not installed")
        monkeypatch.setattr(settings, "FORECAST_MODEL_PERSISTENCE_ENABLED", True, raising=False)
        monkeypatch.setattr(model_persistence, "MODELS_DIR", tmp_path / "models")
        hist = _history()["Close"]
        fe = ForecastingEngine()
        first = fe.run_prophet_forecast(hist, 30, ticker="AAPL", persist=persist)
        artifact = tmp_path / "models" / "prophet_AAPL.pkl"
        assert artifact.exists() is persist
        if persist:
            # A synthetic cycle still LOADS a fresh cached model (computation
            # unchanged); it only never saves one. Building a new Prophet would
            # mean the cache was skipped. (Only yhat is compared: Prophet
            # samples its interval bounds at predict time.)
            mtime = artifact.stat().st_mtime_ns

            def _no_refit(*_a, **_k):
                raise AssertionError("refit instead of loading the cached model")

            monkeypatch.setattr(forecasting_engine, "Prophet", _no_refit)
            again = fe.run_prophet_forecast(hist, 30, ticker="AAPL", persist=False)
            assert again[0] == pytest.approx(first[0])
            assert artifact.stat().st_mtime_ns == mtime


class TestMacroEnginePersist:
    def _spy(self) -> pd.DataFrame:
        return MockDataEngine(preset_prices=_price_path()).fetch_technical_raw(["SPY"])["SPY"]

    def test_persist_false_writes_no_macro_history_and_keeps_the_warm_fit(self, tmp_path, monkeypatch):
        from macro_engine import MacroEngine
        from settings import settings

        monkeypatch.setattr(settings, "HISTORICAL_STORE_ENABLED", True, raising=False)
        spy = self._spy()
        me = MacroEngine(data_engine=MockDataEngine())
        synthetic = me.compute_hmm_risk_on_probability(spy, persist=False)
        assert synthetic is not None
        assert me._hmm_detector.last_fit_date is None
        assert _count(tmp_path / "pytest_isolated_historical_store.db", "macro_history") == 0

        # Same numbers as an in-place fit with the store bypassed the same way
        # (HISTORICAL_STORE_ENABLED=False is the pre-existing direct path).
        monkeypatch.setattr(settings, "HISTORICAL_STORE_ENABLED", False, raising=False)
        direct = MacroEngine(data_engine=MockDataEngine()).compute_hmm_risk_on_probability(spy)
        assert synthetic["risk_on_probability"] == pytest.approx(direct["risk_on_probability"])
        assert synthetic["regime_state_label"] == direct["regime_state_label"]

    def test_persist_true_still_writes_and_fits_in_place(self, tmp_path, monkeypatch):
        from macro_engine import MacroEngine
        from settings import settings

        monkeypatch.setattr(settings, "HISTORICAL_STORE_ENABLED", True, raising=False)
        me = MacroEngine(data_engine=MockDataEngine())
        assert me.compute_hmm_risk_on_probability(self._spy()) is not None
        assert me._hmm_detector.last_fit_date is not None
        assert _count(tmp_path / "pytest_isolated_historical_store.db", "macro_history") > 0


class TestAdvisoryForwardsPersist:
    @pytest.mark.parametrize("kwargs, expected", [({}, True), ({"persist_forecast": False}, False)])
    def test_evaluate_passes_persist_to_generate_forecast(self, tmp_path, monkeypatch, kwargs, expected):
        import engine.advisory as adv
        from tests.advisory_frozen_inputs import _FakeMarket, _install_frozen_inputs, _snapshot

        _install_frozen_inputs(tmp_path, monkeypatch)
        seen: List[Any] = []

        class _RecordingForecastingEngine:
            def generate_forecast(self, row, current_price, **kw):
                seen.append(kw.get("persist", "absent"))
                return {"Forecast_30": current_price * 1.02, "Forecast_30_Is_Fallback": False}

        snapshot = _snapshot()
        adv.evaluate(
            symbol="MSFT", position=snapshot.positions.get("MSFT"), market=_FakeMarket(),
            snapshot=snapshot, forecasting_engine=_RecordingForecastingEngine(), **kwargs,
        )
        assert seen == [expected]
