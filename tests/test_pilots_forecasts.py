"""Forecasting rebuild F6a: the Forecasts screen's read layer and routes.

Covers ``pilots/forecasts.py`` (``forecasts_view`` from the persisted
snapshot, ``forecast_summary`` from the ledger), the two new read routes on
``api/pilots_api.py``, ``ForecastTracker.latest_gate_decisions``, and the
decision-neutrality of ``engine.advisory.Recommendation.forecast_effects``.
"""
from __future__ import annotations

import ast
import json
import math
import pathlib
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Dict, Sequence
from unittest import mock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from forecasting.forecast_tracker import (
    GATE_REASON_NOT_BETTER,
    GATE_REASON_TOO_FEW,
    MODEL_ARIMA,
    MODEL_GATED_BLEND,
    MODEL_HOLT_WINTERS,
    MODEL_MONTE_CARLO,
    MODEL_NAIVE,
    ForecastTracker,
)
from pilots import forecasts
from settings import settings

_REPO = pathlib.Path(__file__).resolve().parent.parent
AS_OF = datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc)  # a Monday


# ── ledger helpers ───────────────────────────────────────────────────────────


def _seed(
    db: str,
    symbol: str,
    horizon: int,
    days: Sequence[pd.Timestamp],
    model_errors: Dict[str, float],
    naive_err: float = 0.02,
    p0: float = 100.0,
    bands: bool = False,
) -> None:
    """COMPLETED rows: one naive row + one row per model per day. naive's
    |log error| is exactly ``naive_err``; each model's is its entry."""
    rows = []
    for i, d in enumerate(days):
        day = pd.Timestamp(d).strftime("%Y-%m-%d")
        ts = f"{day}T15:00:00+00:00"
        sign = 1.0 if i % 2 == 0 else -1.0
        actual = p0 * math.exp(sign * naive_err)
        rows.append((symbol, MODEL_NAIVE, horizon, ts, p0, actual, None, None, ts, day))
        for model, err in model_errors.items():
            f = actual * math.exp(sign * err)
            lo, hi = (f * 0.9, f * 1.1) if (bands and model == MODEL_MONTE_CARLO) else (None, None)
            rows.append((symbol, model, horizon, ts, f, actual, lo, hi, ts, day))
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.executemany(
            """INSERT INTO forecast_errors
               (symbol, model_name, horizon_days, forecast_ts, forecast_price,
                actual_price, forecast_lower, forecast_upper, recorded_at, forecast_day)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )


def _seed_gate_row(db: str, symbol: str, horizon: int, day: str, fallback: int, admitted: str) -> None:
    ts = f"{day}T15:00:00+00:00"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(
            """INSERT INTO forecast_errors
               (symbol, model_name, horizon_days, forecast_ts, forecast_price,
                recorded_at, forecast_day, gate_fallback, gate_admitted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, MODEL_GATED_BLEND, horizon, ts, 100.0, ts, day, fallback, admitted),
        )


def _days_before(as_of: datetime, horizon: int, n: int) -> pd.DatetimeIndex:
    last_forecast = pd.Timestamp(as_of.date()) - pd.offsets.BDay(horizon + 1)
    return pd.bdate_range(end=last_forecast, periods=n)


@pytest.fixture()
def window_365(monkeypatch):
    monkeypatch.setattr(settings, "FORECAST_SKILL_WINDOW_DAYS", 365)
    monkeypatch.setattr(settings, "FORECAST_NAIVE_GATE_MIN_IMPROVEMENT", 0.005)
    monkeypatch.setattr(settings, "FORECAST_NAIVE_GATE_MIN_OBS", 60)
    monkeypatch.setattr(settings, "FORECAST_NAIVE_GATE_ENABLED", False)


# ── constants pinned to their source ─────────────────────────────────────────


def test_action_tier_cuts_match_strategy_engine_source():
    """ACTION_TIER_CUTS is a literal (importing strategy_engine would put the
    heavy engine on the API path); pin it to strategy_engine.py's PHASE 5."""
    src = (_REPO / "strategy_engine.py").read_text(encoding="utf-8")
    assert re.search(r"if final_score >= 75:", src)
    assert re.search(r"elif 55 <= final_score < 75:", src)
    assert re.search(r"elif 35 <= final_score < 55:", src)
    assert forecasts.ACTION_TIER_CUTS == (75.0, 55.0, 35.0)


def test_sell_zone_format_matches_strategy_engine():
    """sell_hi_from_forecast relies on LO = P + 1.5*ATR and HI = max(P + 3*ATR,
    forecast) in apply_sell_side_range, and on its "Sell Zone: $LO - $HI" text."""
    src = (_REPO / "strategy_engine.py").read_text(encoding="utf-8")
    assert "take_profit_lo = params.current_price + (1.5 * params.safe_atr)" in src
    assert "atr_resistance = params.current_price + (3.0 * params.safe_atr)" in src
    assert 'f"Sell Zone: ${take_profit_lo:.2f} - ${take_profit_hi:.2f} "' in src


class TestPureHelpers:
    def test_action_tier(self):
        assert forecasts.action_tier(80) == "STRONG BUY"
        assert forecasts.action_tier(75) == "STRONG BUY"
        assert forecasts.action_tier(74.9) == "BUY"
        assert forecasts.action_tier(55) == "BUY"
        assert forecasts.action_tier(54) == "HOLD"
        assert forecasts.action_tier(35) == "HOLD"
        assert forecasts.action_tier(34.9) == "RISK REDUCE"
        assert forecasts.action_tier(None) is None
        assert forecasts.action_tier(float("nan")) is None

    def test_sell_hi_from_forecast_true_when_forecast_leg_wins(self):
        # P=100, ATR=2: LO=103, ATR leg=106; forecast 112 wins.
        assert forecasts.sell_hi_from_forecast("Sell Zone: $103.00 - $112.00 | Stop @ $95.00", 100.0) is True

    def test_sell_hi_from_forecast_false_when_atr_leg_wins(self):
        assert forecasts.sell_hi_from_forecast("Sell Zone: $103.00 - $106.00 | Stop @ $95.00", 100.0) is False

    def test_sell_hi_rounding_is_tolerated(self):
        # P=100, ATR=1.333: LO=101.9995 -> "102.00", HI=103.999 -> "104.00".
        assert forecasts.sell_hi_from_forecast("Sell Zone: $102.00 - $104.00 | Stop @ $96.67", 100.0) is False

    def test_sell_hi_unknown_cases_are_none(self):
        assert forecasts.sell_hi_from_forecast("Sell Now @ market | Stop @ $95.00", 100.0) is None
        assert forecasts.sell_hi_from_forecast("Sell Zone: $103.00 - $112.00", None) is None
        assert forecasts.sell_hi_from_forecast("Sell Zone: $103.00 - $112.00", 0.0) is None
        assert forecasts.sell_hi_from_forecast("", 100.0) is None
        assert forecasts.sell_hi_from_forecast(None, 100.0) is None
        # HI below the ATR leg is inconsistent with the formula: not guessed.
        assert forecasts.sell_hi_from_forecast("Sell Zone: $103.00 - $104.00", 100.0) is None

    def test_sell_hi_with_thousands_separator(self):
        assert forecasts.sell_hi_from_forecast("Sell Zone: $1,030.00 - $1,120.00", 1000.0) is True


# ── forecasts_view (persisted snapshot) ──────────────────────────────────────


def _signal(**extra):
    sig = {
        "symbol": "AAPL",
        "action": "BUY",
        "score": 70.0,
        "price": 100.0,
        "forecast_10": 101.0, "forecast_30": 112.0, "forecast_60": 115.0, "forecast_90": 100.0,
        "forecast_10_lower": 95.0, "forecast_10_upper": 107.0,
        "forecast_30_lower": 90.0, "forecast_30_upper": 125.0,
        "forecast_60_lower": None, "forecast_60_upper": None,
        "forecast_90_lower": 80.0, "forecast_90_upper": 130.0,
        "forecast_10_is_fallback": 0.0, "forecast_30_is_fallback": 0.0,
        "forecast_60_is_fallback": 0.0, "forecast_90_is_fallback": 1.0,
        "forecast_10_gated_naive": None, "forecast_30_gated_naive": None,
        "forecast_60_gated_naive": None, "forecast_90_gated_naive": None,
        "forecast_is_fallback": 0.0,
        "score_components": {"forecast_alignment": 10.0, "momentum": 5.0},
        "sell_range": "Sell Zone: $103.00 - $112.00 | Stop @ $95.00",
        "advisory_action": "BUY",
        "advisory_forecast_effects": ["bullish_conviction_boost"],
    }
    sig.update(extra)
    return sig


def _snapshot(*signals, holdings=("AAPL",)):
    return {"timestamp": "2026-11-13T15:00:00+00:00", "holdings": list(holdings), "signals": list(signals)}


class TestForecastsView:
    def test_cold_start(self, window_365):
        out = forecasts.forecasts_view(None)
        assert out["rows"] == []
        assert out["reason"]
        assert out["z"] is None
        assert out["gate"] == {"enabled": False, "min_improvement": 0.005, "min_obs": 60, "window_days": 365}

    def test_row_shape_and_values(self, window_365):
        out = forecasts.forecasts_view(_snapshot(_signal()))
        assert out["reason"] is None
        assert out["as_of"] == "2026-11-13T15:00:00+00:00"
        assert out["horizons"] == [10, 30, 60, 90]
        row = out["rows"][0]
        assert row["symbol"] == "AAPL" and row["held"] is True and row["price"] == 100.0
        h30 = row["horizons"]["30"]
        assert h30["forecast"] == 112.0 and h30["lower"] == 90.0 and h30["upper"] == 125.0
        assert h30["is_fallback"] is False
        assert h30["gated_naive"] is None
        assert h30["z"] is None and h30["err"] is None and h30["err_n"] is None
        assert "F5" in h30["z_reason"]
        assert row["horizons"]["60"]["lower"] is None  # band absent -> null, never 0.0
        assert row["horizons"]["90"]["is_fallback"] is True
        imp = row["impact"]
        assert imp["alignment_points"] == 10.0
        assert imp["action_tier"] == "BUY"
        assert imp["action_tier_without_forecast"] == "BUY"  # 70 - 10 = 60, still BUY
        assert imp["tier_changed_by_forecast"] is False
        assert imp["advisory_effects"] == ["bullish_conviction_boost"]
        assert imp["sell_hi_from_forecast"] is True
        assert "approximate" in imp["tier_note"]

    def test_tier_change_is_reported(self, window_365):
        sig = _signal(score=62.0, score_components={"forecast_alignment": 10.0})
        imp = forecasts.forecasts_view(_snapshot(sig))["rows"][0]["impact"]
        assert imp["action_tier"] == "BUY"
        assert imp["action_tier_without_forecast"] == "HOLD"
        assert imp["tier_changed_by_forecast"] is True

    def test_missing_values_are_null_not_zero(self, window_365):
        sig = {"symbol": "msft", "price": 0.0, "score": None}
        row = forecasts.forecasts_view(_snapshot(sig, holdings=()))["rows"][0]
        assert row["symbol"] == "MSFT" and row["held"] is False
        assert row["price"] is None
        for h in ("10", "30", "60", "90"):
            cell = row["horizons"][h]
            for key in ("forecast", "lower", "upper", "is_fallback", "gated_naive"):
                assert cell[key] is None, (h, key)
        imp = row["impact"]
        for key in ("alignment_points", "score", "action_tier", "action_tier_without_forecast",
                    "tier_changed_by_forecast", "advisory_effects", "sell_hi_from_forecast"):
            assert imp[key] is None, key

    def test_pre_f6a_snapshot_uses_the_30_day_flag(self, window_365):
        sig = {"symbol": "AAPL", "price": 100.0, "forecast_is_fallback": 1.0}
        row = forecasts.forecasts_view(_snapshot(sig))["rows"][0]
        assert row["horizons"]["30"]["is_fallback"] is True
        assert row["horizons"]["10"]["is_fallback"] is None

    def test_garbage_entries_are_skipped_never_raise(self, window_365):
        snap = {"signals": ["x", None, {"symbol": ""}, _signal(score_components="bad", sell_range=5)]}
        out = forecasts.forecasts_view(snap)
        assert [r["symbol"] for r in out["rows"]] == ["AAPL"]
        assert out["rows"][0]["impact"]["alignment_points"] is None
        assert forecasts.forecasts_view({"signals": "nope"})["reason"]

    def test_gate_settings_are_live(self, window_365, monkeypatch):
        monkeypatch.setattr(settings, "FORECAST_NAIVE_GATE_ENABLED", True)
        monkeypatch.setattr(settings, "FORECAST_NAIVE_GATE_MIN_OBS", 40)
        out = forecasts.forecasts_view(_snapshot(_signal()))
        assert out["gate"]["enabled"] is True and out["gate"]["min_obs"] == 40


# ── forecast_summary (ledger) ────────────────────────────────────────────────


class TestForecastSummary:
    def test_cold_start_empty_ledger(self, tmp_path, window_365):
        t = ForecastTracker(db_path=str(tmp_path / "l.db"))
        out = forecasts.forecast_summary("aapl", tracker=t, as_of=AS_OF)
        assert out["symbol"] == "AAPL"
        assert out["reason"]
        for h in ("10", "30", "60", "90"):
            cell = out["horizons"][h]
            assert cell["skill"] == []
            assert cell["gate"]["last_decision"] is None
            assert cell["mc_coverage"]["empirical"] is None
            assert cell["published_model"] == "blend"

    def test_missing_db_never_raises(self, tmp_path, window_365):
        t = ForecastTracker(db_path=str(tmp_path / "nope" / "x.db"), readonly=True)
        out = forecasts.forecast_summary("AAPL", tracker=t, as_of=AS_OF)
        assert out["reason"]
        assert out["horizons"]["30"]["skill"] == []

    def test_blank_symbol(self):
        assert forecasts.forecast_summary("  ")["reason"] == "no symbol"

    def test_skill_gate_and_coverage(self, tmp_path, window_365):
        t = ForecastTracker(db_path=str(tmp_path / "l.db"))
        days = _days_before(AS_OF, 10, 60)
        _seed(t._db_path, "AAPL", 10, days, {
            MODEL_ARIMA: 0.02 * 0.99,         # admitted
            MODEL_HOLT_WINTERS: 0.02 * 0.997,  # not by enough
            MODEL_MONTE_CARLO: 0.03,           # worse
        }, bands=True)
        _seed_gate_row(t._db_path, "AAPL", 10, "2026-05-29", 0, "arima")
        out = forecasts.forecast_summary("AAPL", tracker=t, as_of=AS_OF)
        assert out["reason"] is None
        cell = out["horizons"]["10"]
        skill = {r["model"]: r for r in cell["skill"]}
        assert skill[MODEL_ARIMA]["n"] == 60
        assert skill[MODEL_ARIMA]["median_abs_log_error"] == pytest.approx(0.0198)
        assert skill[MODEL_ARIMA]["naive_median_abs_log_error"] == pytest.approx(0.02)
        ev = cell["gate"]["evaluation"]
        assert ev["admitted"] == [MODEL_ARIMA]
        assert ev["rejected"][MODEL_HOLT_WINTERS] == GATE_REASON_NOT_BETTER
        assert ev["rejected"][MODEL_MONTE_CARLO] == GATE_REASON_NOT_BETTER
        assert ev["fallback_to_naive"] is False
        assert cell["gate"]["last_decision"] == {
            "forecast_day": "2026-05-29", "forecast_ts": "2026-05-29T15:00:00+00:00",
            "fallback_to_naive": False, "admitted": ["arima"],
        }
        cov = cell["mc_coverage"]
        assert cov["n"] == 60 and cov["nominal"] == pytest.approx(0.90)
        assert cov["empirical"] is not None
        # Other horizons have nothing: empty, never fabricated.
        assert out["horizons"]["30"]["skill"] == []
        assert out["horizons"]["30"]["gate"]["evaluation"]["admitted"] == []

    def test_too_few_observations_is_rejected_with_reason(self, tmp_path, window_365):
        t = ForecastTracker(db_path=str(tmp_path / "l.db"))
        _seed(t._db_path, "AAPL", 10, _days_before(AS_OF, 10, 20), {MODEL_ARIMA: 0.001})
        ev = forecasts.forecast_summary("AAPL", tracker=t, as_of=AS_OF)["horizons"]["10"]["gate"]["evaluation"]
        assert ev["admitted"] == [] and ev["fallback_to_naive"] is True
        assert ev["rejected"][MODEL_ARIMA] == GATE_REASON_TOO_FEW

    def test_published_model_follows_the_gate_flag(self, tmp_path, window_365, monkeypatch):
        monkeypatch.setattr(settings, "FORECAST_NAIVE_GATE_ENABLED", True)
        t = ForecastTracker(db_path=str(tmp_path / "l.db"))
        out = forecasts.forecast_summary("AAPL", tracker=t, as_of=AS_OF)
        assert out["horizons"]["30"]["published_model"] == MODEL_GATED_BLEND

    def test_a_failing_tracker_degrades_per_call(self, window_365):
        class Boom:
            def __getattr__(self, name):
                def _raise(*a, **k):
                    raise RuntimeError("db gone")
                return _raise
        out = forecasts.forecast_summary("AAPL", tracker=Boom(), as_of=AS_OF)
        cell = out["horizons"]["30"]
        assert cell["skill"] == [] and cell["skill_reason"] == "error"
        assert cell["gate"]["evaluation"] is None and cell["gate"]["reason"] == "error"
        assert cell["mc_coverage"]["reason"] == "error"


class TestSummaryGateHasNoLookahead:
    """The gate evaluation shown for ``as_of`` may only use outcomes that were
    final before ``as_of``'s trading day (the F3 cutoff). Rows that had not
    matured by then are perturbed (great vs awful); the evaluation and its
    stats must be bit-identical."""

    def _ledger(self, tmp_path, name, future_err):
        t = ForecastTracker(db_path=str(tmp_path / name))
        _seed(t._db_path, "AAPL", 10, _days_before(AS_OF, 10, 60), {MODEL_ARIMA: 0.01})
        as_of_day = pd.Timestamp(AS_OF.date())
        due_later = pd.bdate_range(start=as_of_day - pd.offsets.BDay(10), periods=65)
        _seed(t._db_path, "AAPL", 10, due_later, {MODEL_ARIMA: future_err})
        return t

    def test_future_rows_cannot_move_the_evaluation(self, tmp_path, window_365):
        a = forecasts.forecast_summary("AAPL", tracker=self._ledger(tmp_path, "a.db", 0.001), as_of=AS_OF)
        b = forecasts.forecast_summary("AAPL", tracker=self._ledger(tmp_path, "b.db", 5.0), as_of=AS_OF)
        assert a["horizons"]["10"]["gate"]["evaluation"] == b["horizons"]["10"]["gate"]["evaluation"]
        assert a["horizons"]["10"]["gate"]["evaluation"]["admitted"] == [MODEL_ARIMA]

    def test_perturbation_is_not_vacuous(self, tmp_path, window_365):
        later = AS_OF + timedelta(days=120)
        b = forecasts.forecast_summary("AAPL", tracker=self._ledger(tmp_path, "b.db", 5.0), as_of=later)
        assert b["horizons"]["10"]["gate"]["evaluation"]["fallback_to_naive"] is True


class TestLatestGateDecisions:
    def test_newest_row_per_horizon(self, tmp_path):
        t = ForecastTracker(db_path=str(tmp_path / "l.db"))
        _seed_gate_row(t._db_path, "AAPL", 30, "2026-05-27", 1, "")
        _seed_gate_row(t._db_path, "AAPL", 30, "2026-05-28", 0, "arima,holt_winters")
        _seed_gate_row(t._db_path, "MSFT", 30, "2026-05-29", 1, "")
        out = t.latest_gate_decisions("aapl", [10, 30])
        assert out["reason"] is None
        assert out["by_horizon"][10] is None
        assert out["by_horizon"][30]["forecast_day"] == "2026-05-28"
        assert out["by_horizon"][30]["admitted"] == ["arima", "holt_winters"]
        assert out["by_horizon"][30]["fallback_to_naive"] is False

    def test_pre_f3_ledger(self, tmp_path):
        db = str(tmp_path / "old.db")
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("CREATE TABLE forecast_errors (id INTEGER PRIMARY KEY, symbol TEXT, model_name TEXT,"
                         " horizon_days INTEGER, forecast_ts TEXT, forecast_price REAL)")
        out = ForecastTracker(db_path=db, readonly=True).latest_gate_decisions("AAPL", [30])
        assert out["by_horizon"] == {30: None}
        assert "predates F3" in out["reason"]

    def test_read_failure_is_error_not_raise(self, tmp_path):
        out = ForecastTracker(db_path=str(tmp_path / "no" / "x.db"), readonly=True).latest_gate_decisions(
            "AAPL", [30])
        assert out["by_horizon"] == {30: None}
        assert out["reason"] in ("error", "no forecast ledger yet")


# ── routes ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def client():
    from api import pilots_api

    return TestClient(pilots_api.app, client=("127.0.0.1", 54123))


class TestRoutes:
    def test_forecasts_cold_start_is_200_with_reason(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        r = client.get("/forecasts")
        assert r.status_code == 200
        assert r.json()["rows"] == [] and r.json()["reason"]

    def test_forecasts_reads_the_snapshot(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        (tmp_path / "state_snapshot.json").write_text(json.dumps(_snapshot(_signal())), encoding="utf-8")
        body = client.get("/forecasts").json()
        assert body["rows"][0]["symbol"] == "AAPL"
        assert body["rows"][0]["horizons"]["30"]["forecast"] == 112.0

    def test_corrupt_snapshot_never_500s(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        (tmp_path / "state_snapshot.json").write_text("{not json", encoding="utf-8")
        r = client.get("/forecasts")
        assert r.status_code == 200 and r.json()["reason"]

    def test_read_token_tier(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(settings, "STATE_API_TOKEN", "read-tok", raising=False)
        assert client.get("/forecasts").status_code == 401
        assert client.get("/forecasts", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get("/forecasts", headers={"Authorization": "Bearer read-tok"}).status_code == 200
        assert client.get("/symbols/AAPL/forecast-summary").status_code == 401

    def test_summary_route(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
        t = ForecastTracker(db_path=str(tmp_path / "l.db"))
        with mock.patch.object(forecasts, "forecast_summary", wraps=forecasts.forecast_summary) as spy, \
                mock.patch("forecasting.forecast_tracker.ForecastTracker", return_value=t):
            r = client.get("/symbols/aapl/forecast-summary")
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "AAPL"
        assert set(body["horizons"]) == {"10", "30", "60", "90"}
        spy.assert_called_once()


# ── advisory forecast_effects is decision-neutral ────────────────────────────


class TestForecastEffectsAreDecisionNeutral:
    """The three flags behind Recommendation.forecast_effects are written
    where the rules fire and read ONLY to build forecast_effects, so they can
    never change action/conviction/sizing. (A one-off run of 180 scenarios
    against origin/main's advisory showed every other field identical; see
    .claude/forecasting_rebuild_f6_walkthrough.md.)"""

    _RECORD_ONLY = ("_case_c_fired", "_bullish_boost_fired")

    def _evaluate_fn(self) -> ast.FunctionDef:
        tree = ast.parse((_REPO / "engine" / "advisory.py").read_text(encoding="utf-8"))
        return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "evaluate")

    def test_record_only_flags_are_read_only_inside_forecast_effects(self):
        fn = self._evaluate_fn()
        effects_kw = [
            kw for node in ast.walk(fn) if isinstance(node, ast.Call)
            for kw in node.keywords if kw.arg == "forecast_effects"
        ]
        assert len(effects_kw) == 1
        inside = {id(n) for n in ast.walk(effects_kw[0].value)}
        for name in self._RECORD_ONLY:
            loads = [n for n in ast.walk(fn)
                     if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)]
            assert loads, f"{name} is never read"
            stray = [n.lineno for n in loads if id(n) not in inside]
            assert not stray, f"{name} read outside forecast_effects at lines {stray}"

    def test_bullish_boost_flag_does_not_gate_the_conviction_raise(self):
        """The conviction max() must stay unconditional on the new flag."""
        src = (_REPO / "engine" / "advisory.py").read_text(encoding="utf-8")
        block = src[src.index("_bullish_boost_fired = False"):]
        block = block[: block.index("suggested_exit_pct: float = 0.0")]
        assert 'final_conviction = max(final_conviction, CONFIG["conviction_strong_buy"])' in block
        assert "if _bullish_boost_fired" not in src
