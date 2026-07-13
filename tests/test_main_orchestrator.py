"""
tests/test_main_orchestrator.py
================================
Dedicated offline suite for main_orchestrator.py — the async master
orchestrator. Fills GAPS left by scattered incidental coverage rather than
duplicating it.

Deliberately NOT re-covered here (already pinned elsewhere):
  * EngineContext.build engine TYPES / all-None default / run_pipeline(engines=)
    reuse + partial fallback  -> tests/test_engine_context.py
  * PipelineFatalError type + _main_body fetch/pipeline fatal branches
    -> tests/test_pipeline_defatalize.py
  * _main_body data_engine/engines injection wiring
    -> tests/test_main_body_engine_injection.py
  * _validate_dashboard empty/valid/invalid/strict + CLI conversion
    -> tests/test_dashboard_validation.py
  * compute_xsec_momentum_ranks vectorized / lookahead / insufficient-history
    -> tests/test_xsec_momentum.py

Coverage (new surfaces):
  TestSafeFloatOrNone           : _safe_float_or_none float/NaN/None/str coercion.
  TestComputeXsecMomentumRanksEdges: empty dict -> empty Series; missing Close
                                  skipped; p_old<=0 excluded; custom skip/lookback.
  TestFetchAllDataAsync         : fetch_all_data_async returns 3 dicts + injects SPY;
                                  its tech-data task calls DataEngine.fetch_technical_raw_cached()
                                  (HistoricalStore-routed), not fetch_technical_raw() (2026-07).
  TestHeartbeat                 : _heartbeat writes a parseable ISO timestamp;
                                  write failure is swallowed (dead-letter, no raise).
  TestExecuteBrokerOrders       : ADVISORY_ONLY=True no-op (no broker reference);
                                  ADVISORY_ONLY=False best-effort (broker failure
                                  caught, never propagates).
  TestEngineContextBuildWiring  : EngineContext.build wires data_engine into MacroEngine.
  TestRunPipelineOutputContract : full MockDataEngine run — 3-tuple shape,
                                  HMM_Risk_On_Probability column, tactical columns,
                                  shared_context dicts.
  TestRunPipelineStageOrdering  : stages route Macro->Options->Processing->
                                  Forecasting->Strategy in order.
  TestWriteStateSnapshot        : _write_state_snapshot emits parseable JSON with
                                  the documented keys; empty frame still writes.

All network / broker / sheets I/O is offline (MockDataEngine + monkeypatch).
Full-pipeline tests request the shared `disable_historical_store` fixture
(tests/conftest.py) to avoid on-disk DB pollution.
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime
from unittest import mock

import numpy as np
import pandas as pd
import pytest

import main_orchestrator as mo
from main_orchestrator import (
    EngineContext,
    _safe_float_or_none,
    _write_state_snapshot,
    compute_xsec_momentum_ranks,
    fetch_all_data_async,
    run_pipeline,
)
from data_engine import MockDataEngine
from dto_models import MacroEconomicDTO
from macro_engine import MacroEngine


# ---------------------------------------------------------------------------
# Shared offline fixture data (mirrors tests/test_engine_context.py pattern)
# ---------------------------------------------------------------------------

def _fixture_data(tickers=("AAPL",)):
    """Deterministic MockDataEngine inputs for a full run_pipeline() cycle."""
    mock_de = MockDataEngine()
    tk = list(tickers)
    macro_raw = mock_de.fetch_macro_raw()
    fund_raw = mock_de.fetch_fundamentals_raw(tk)
    tech_raw = mock_de.fetch_technical_raw(tk)
    return tk, macro_raw, fund_raw, tech_raw, mock_de


def _make_tech_df(prices, dates=None):
    n = len(prices)
    if dates is None:
        dates = pd.date_range("2022-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Close": prices, "Open": prices, "High": prices, "Low": prices, "Volume": 1000},
        index=dates,
    )


# ===========================================================================
# 1. _safe_float_or_none
# ===========================================================================

class TestSafeFloatOrNone:
    def test_plain_float_passes_through(self):
        assert _safe_float_or_none(3.14) == pytest.approx(3.14)

    def test_numeric_string_is_coerced(self):
        assert _safe_float_or_none("42.5") == pytest.approx(42.5)

    def test_nan_becomes_none(self):
        # CONSTRAINT #4: a genuine NaN must serialise as JSON null, never 0.0.
        assert _safe_float_or_none(float("nan")) is None
        assert _safe_float_or_none(np.nan) is None

    def test_none_becomes_none(self):
        assert _safe_float_or_none(None) is None

    def test_non_numeric_string_becomes_none(self):
        assert _safe_float_or_none("not-a-number") is None

    def test_int_is_coerced_to_float(self):
        out = _safe_float_or_none(7)
        assert out == pytest.approx(7.0)
        assert isinstance(out, float)


# ===========================================================================
# 2. compute_xsec_momentum_ranks — edge cases not in test_xsec_momentum.py
# ===========================================================================

class TestComputeXsecMomentumRanksEdges:
    def test_empty_dict_returns_empty_float_series(self):
        out = compute_xsec_momentum_ranks({})
        assert isinstance(out, pd.Series)
        assert out.empty
        assert out.dtype == float

    def test_ticker_missing_close_column_is_skipped(self):
        # A frame with enough rows but NO 'Close' column must be silently dropped.
        n = 300
        good = _make_tech_df(100.0 + np.arange(n) * 0.1)
        bad = pd.DataFrame({"Open": np.ones(n)}, index=good.index)
        ranks = compute_xsec_momentum_ranks({"GOOD": good, "NOCLOSE": bad})
        assert "GOOD" in ranks.index
        assert "NOCLOSE" not in ranks.index

    def test_none_and_empty_frames_are_skipped(self):
        good = _make_tech_df(100.0 + np.arange(300) * 0.1)
        ranks = compute_xsec_momentum_ranks(
            {"GOOD": good, "NULL": None, "EMPTY": pd.DataFrame()}
        )
        assert list(ranks.index) == ["GOOD"] or "GOOD" in ranks.index
        assert "NULL" not in ranks.index and "EMPTY" not in ranks.index

    def test_non_positive_old_price_excluded(self):
        # p_old <= 0 (division base) must exclude the ticker (guard at line ~134).
        n = 300
        prices = np.full(n, 100.0)
        prices[-253] = 0.0  # the t-252 reference price -> excluded
        with_zero = _make_tech_df(prices)
        good = _make_tech_df(100.0 + np.arange(n) * 0.1)
        ranks = compute_xsec_momentum_ranks({"ZERO": with_zero, "GOOD": good})
        assert "ZERO" not in ranks.index
        assert "GOOD" in ranks.index

    def test_custom_skip_and_lookback_params(self):
        # A shorter window admits a shorter series than the 275-day default.
        n = 120
        a = _make_tech_df(100.0 + np.arange(n) * 0.10)
        b = _make_tech_df(100.0 + np.arange(n) * 0.30)
        # Default (252+22+1=275) would exclude both; custom window admits both.
        default_ranks = compute_xsec_momentum_ranks({"A": a, "B": b})
        assert default_ranks.empty
        custom = compute_xsec_momentum_ranks(
            {"A": a, "B": b}, skip_days=5, lookback_days=60
        )
        assert set(custom.index) == {"A", "B"}
        for v in custom.values:
            assert 0.0 <= v <= 1.0


# ===========================================================================
# 3. fetch_all_data_async
# ===========================================================================

class TestFetchAllDataAsync:
    def test_returns_three_dicts_and_injects_spy(self):
        de = MockDataEngine()
        macro_raw, fund_raw, tech_raw = asyncio.run(
            fetch_all_data_async(de, ["AAPL", "MSFT"])
        )
        assert isinstance(macro_raw, dict) and macro_raw  # non-empty macro snapshot
        assert isinstance(fund_raw, dict)
        assert isinstance(tech_raw, dict)
        # SPY is unioned into the technical universe for the HMM / relative-strength path.
        assert "SPY" in tech_raw
        assert "AAPL" in tech_raw

    def test_requested_tickers_present_in_technical_frames(self):
        de = MockDataEngine()
        _macro, _fund, tech_raw = asyncio.run(fetch_all_data_async(de, ["AAPL"]))
        for tk in ("AAPL", "SPY"):
            assert tk in tech_raw
            assert not tech_raw[tk].empty

    def test_tech_task_calls_fetch_technical_raw_cached_not_fetch_technical_raw(
        self, monkeypatch
    ):
        """2026-07: fetch_all_data_async's tech-data task must call
        DataEngine.fetch_technical_raw_cached() (HistoricalStore-routed
        incremental fetch), NOT the bare fetch_technical_raw() -- closing the
        gap where the full async pipeline refetched ~2 years of OHLCV for
        every ticker every cycle regardless of HistoricalStore's
        incremental-top-up capability. Spies replace both methods entirely
        (no real fetch logic runs) so this isolates purely which method
        fetch_all_data_async invokes."""
        de = MockDataEngine()
        sentinel = {"SPY": pd.DataFrame({"Close": [1.0]})}
        calls = {"cached": 0, "raw": 0}

        def _cached_spy(tickers):
            calls["cached"] += 1
            return sentinel

        def _raw_spy(tickers):
            calls["raw"] += 1
            return sentinel

        monkeypatch.setattr(de, "fetch_technical_raw_cached", _cached_spy)
        monkeypatch.setattr(de, "fetch_technical_raw", _raw_spy)

        _macro, _fund, tech_raw = asyncio.run(fetch_all_data_async(de, ["AAPL"]))

        assert calls["cached"] == 1
        assert calls["raw"] == 0
        assert tech_raw is sentinel


# ===========================================================================
# 4. _heartbeat
# ===========================================================================

class TestHeartbeat:
    def test_writes_parseable_iso_timestamp(self, tmp_path, monkeypatch):
        # Break the infinite loop after the first write by making sleep abort.
        async def _stop_after_first(_interval):
            raise asyncio.CancelledError

        monkeypatch.setattr(mo.asyncio, "sleep", _stop_after_first)

        async def _drive():
            with pytest.raises(asyncio.CancelledError):
                await mo._heartbeat(tmp_path, interval=60)

        asyncio.run(_drive())

        hb = tmp_path / "heartbeat.txt"
        assert hb.exists()
        # The written value must be a valid ISO-8601 UTC timestamp.
        parsed = datetime.fromisoformat(hb.read_text(encoding="utf-8"))
        assert parsed.tzinfo is not None

    def test_write_failure_is_swallowed(self, tmp_path, monkeypatch):
        # An unwritable target dir must not crash the heartbeat loop (CONSTRAINT #6);
        # the except-branch logs a warning and control still reaches sleep().
        bad_dir = tmp_path / "does_not_exist"  # never created -> write_text raises

        async def _stop_after_first(_interval):
            raise asyncio.CancelledError

        monkeypatch.setattr(mo.asyncio, "sleep", _stop_after_first)

        async def _drive():
            # It reaches sleep() (our CancelledError) rather than propagating the
            # FileNotFoundError from the failed write -> proves the write is guarded.
            with pytest.raises(asyncio.CancelledError):
                await mo._heartbeat(bad_dir, interval=60)

        asyncio.run(_drive())
        assert not (bad_dir / "heartbeat.txt").exists()


# ===========================================================================
# 5. _execute_broker_orders
# ===========================================================================

class TestExecuteBrokerOrders:
    def test_advisory_only_is_a_noop_no_broker_reference(self, monkeypatch):
        # Tier 5.1: ADVISORY_ONLY=True quarantines the broker surface entirely —
        # the function returns before importing/constructing any broker.
        monkeypatch.setattr(mo.settings, "ADVISORY_ONLY", True, raising=False)

        broker_ctor = mock.MagicMock(
            side_effect=AssertionError("AlpacaBroker must NOT be constructed under ADVISORY_ONLY")
        )
        with mock.patch("execution.alpaca_broker.AlpacaBroker", broker_ctor):
            # Must complete without raising and without touching the broker ctor.
            asyncio.run(
                mo._execute_broker_orders(pd.DataFrame(), dry_run=True, macro_dto=None)
            )
        broker_ctor.assert_not_called()

    def test_broker_failure_is_caught_and_never_propagates(self, monkeypatch):
        # ADVISORY_ONLY=False reaches the lazy broker imports. Any failure there is
        # best-effort: logged as ERROR, never raised (analysis value not held hostage).
        monkeypatch.setattr(mo.settings, "ADVISORY_ONLY", False, raising=False)

        with mock.patch(
            "execution.alpaca_broker.AlpacaBroker",
            side_effect=RuntimeError("simulated broker connectivity failure"),
        ):
            # Should return None cleanly despite the broker construction blowing up.
            result = asyncio.run(
                mo._execute_broker_orders(pd.DataFrame(), dry_run=False, macro_dto=None)
            )
        assert result is None


# ===========================================================================
# 6. EngineContext.build data_engine wiring (existing test only checks types)
# ===========================================================================

class TestEngineContextBuildWiring:
    def test_build_wires_data_engine_into_macro_engine(self):
        de = MockDataEngine()
        ctx = EngineContext.build(data_engine=de)
        assert isinstance(ctx.macro_engine, MacroEngine)
        # The whole point of warm-keeping MacroEngine is its persistent HMM detector,
        # which needs the injected data_engine for its expanding-window macro history.
        assert ctx.macro_engine.data_engine is de

    def test_build_with_none_data_engine_still_constructs(self):
        ctx = EngineContext.build(data_engine=None)
        assert isinstance(ctx.macro_engine, MacroEngine)
        assert ctx.macro_engine.data_engine is None


# ===========================================================================
# 7. run_pipeline output contract
# ===========================================================================

class TestRunPipelineOutputContract:
    def test_returns_three_tuple_with_documented_shape(self, disable_historical_store):
        tickers, macro_raw, fund_raw, tech_raw, de = _fixture_data()
        result = run_pipeline(tickers, macro_raw, fund_raw, tech_raw, data_engine=de)
        assert isinstance(result, tuple) and len(result) == 3
        final_df, macro_dto, shared_context = result
        assert isinstance(final_df, pd.DataFrame) and not final_df.empty
        assert isinstance(macro_dto, MacroEconomicDTO)
        # shared_context exposes the pre-compute dicts the advisory path consumes.
        assert isinstance(shared_context.xsec_percentile_ranks, dict)
        assert isinstance(shared_context.multifactor_scores, dict)

    def test_hmm_column_present(self, disable_historical_store):
        # HMM_Risk_On_Probability is written for every row (NaN when the HMM
        # second opinion didn't run — as on the deterministic mock history).
        tickers, macro_raw, fund_raw, tech_raw, de = _fixture_data()
        final_df, _macro_dto, _ctx = run_pipeline(
            tickers, macro_raw, fund_raw, tech_raw, data_engine=de
        )
        assert "HMM_Risk_On_Probability" in final_df.columns

    def test_tactical_and_factor_columns_present(self, disable_historical_store):
        tickers, macro_raw, fund_raw, tech_raw, de = _fixture_data()
        final_df, _macro_dto, _ctx = run_pipeline(
            tickers, macro_raw, fund_raw, tech_raw, data_engine=de
        )
        for col in (
            "Action Signal", "Kelly Target", "buyRange", "sellRange",
            "XSec_12_1M", "XSec_Momentum_Rank", "Multifactor_Composite",
            "GARCH_Vol", "True_IVR",
        ):
            assert col in final_df.columns, f"missing expected column: {col}"

    def test_no_data_engine_disables_hmm_second_opinion(self, disable_historical_store):
        # data_engine=None (default) -> macro_dto carries no fabricated HMM prob.
        tickers, macro_raw, fund_raw, tech_raw, _de = _fixture_data()
        _final_df, macro_dto, _ctx = run_pipeline(
            tickers, macro_raw, fund_raw, tech_raw, data_engine=None
        )
        assert macro_dto.hmm_risk_on_probability is None


# ===========================================================================
# 8. run_pipeline stage ordering
# ===========================================================================

class TestRunPipelineStageOrdering:
    def test_stages_execute_in_documented_order(self, disable_historical_store):
        tickers, macro_raw, fund_raw, tech_raw, de = _fixture_data()

        fake_telemetry = mock.MagicMock()
        with mock.patch.object(mo, "telemetry", fake_telemetry):
            run_pipeline(tickers, macro_raw, fund_raw, tech_raw, data_engine=de)

        # Collect the first positional arg of every telemetry.info(...) call.
        infos = [
            c.args[0]
            for c in fake_telemetry.info.call_args_list
            if c.args and isinstance(c.args[0], str)
        ]

        def _first_index(needle: str) -> int:
            for i, msg in enumerate(infos):
                if needle in msg:
                    return i
            raise AssertionError(f"stage banner not logged: {needle!r}")

        macro_i = _first_index("Macro Engine")
        options_i = _first_index("Technical Options Engine")
        processing_i = _first_index("Computational Core")
        forecasting_i = _first_index("Forecasting Engine")
        strategy_i = _first_index("Strategy and Evaluation")

        assert macro_i < options_i < processing_i < forecasting_i < strategy_i


# ===========================================================================
# 9. _write_state_snapshot
# ===========================================================================

class TestWriteStateSnapshot:
    def _redirect_output_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mo.settings, "OUTPUT_DIR", tmp_path, raising=False)

    def test_writes_parseable_snapshot_with_documented_keys(self, tmp_path, monkeypatch):
        self._redirect_output_dir(monkeypatch, tmp_path)
        final_df = pd.DataFrame([
            {
                "Symbol": "AAPL", "Action Signal": "BUY", "Kelly Target": 0.12,
                "Score": 3.4, "Price": 190.0, "Shares": 5.0, "Macro Status": "RISK ON",
                "HMM_Risk_On_Probability": float("nan"), "buyRange": "Buy: $185-$188",
                "sellRange": "Sell Zone: $200-$210", "XSec_12_1M": 0.15,
                "XSec_Momentum_Rank": 0.8, "Multifactor_Composite": 1.1,
            },
        ])
        macro_raw = {"market_regime": "RISK ON", "VIXCLS": 15.0, "T10Y2Y": 0.4,
                     "SAHMREALTIME": 0.1, "BAMLH0A0HYM2": 3.2}

        _write_state_snapshot(macro_raw, final_df, ["AAPL"])

        snap_path = tmp_path / "state_snapshot.json"
        assert snap_path.exists()
        data = json.loads(snap_path.read_text(encoding="utf-8"))
        for key in (
            "timestamp", "tickers", "holdings", "market_regime", "vix",
            "yield_curve", "sahm_rule", "high_yield_oas", "kill_switch_active",
            "macro_regime_gate_enabled", "signals",
        ):
            assert key in data, f"snapshot missing top-level key: {key}"
        assert data["tickers"] == ["AAPL"]
        assert data["holdings"] == ["AAPL"]  # 5 shares held -> surfaced
        assert data["market_regime"] == "RISK ON"
        assert len(data["signals"]) == 1
        sig = data["signals"][0]
        assert sig["symbol"] == "AAPL"
        assert sig["action"] == "BUY"
        # NaN HMM prob is coerced to 0.0 by the float(... or 0.0) guard here.
        assert sig["buy_range"] == "Buy: $185-$188"
        assert sig["sell_range"] == "Sell Zone: $200-$210"
        # _safe_float_or_none passthrough for the factor fields.
        assert sig["xsec_12_1m"] == pytest.approx(0.15)
        assert sig["multifactor_composite"] == pytest.approx(1.1)

    def test_empty_frame_still_writes_snapshot(self, tmp_path, monkeypatch):
        self._redirect_output_dir(monkeypatch, tmp_path)
        _write_state_snapshot({"market_regime": "NEUTRAL"}, pd.DataFrame(), [])
        snap_path = tmp_path / "state_snapshot.json"
        assert snap_path.exists()
        data = json.loads(snap_path.read_text(encoding="utf-8"))
        assert data["signals"] == []
        assert data["holdings"] == []
        assert data["tickers"] == []

    def test_nan_sector_degrades_to_empty_string_not_literal_nan(self, tmp_path, monkeypatch):
        """A missing-fundamentals ticker carries sector=NaN (float) in
        dashboard_df (CONSTRAINT #4 — NaN, never a fabricated value). NaN is
        truthy in Python, so a plain `str(val or "")` fallback does not catch
        it and previously stringified it to the literal text "nan"."""
        self._redirect_output_dir(monkeypatch, tmp_path)
        final_df = pd.DataFrame([
            {"Symbol": "XYZ", "Action Signal": "HOLD", "Price": 10.0,
             "sector": float("nan")},
        ])
        _write_state_snapshot({"market_regime": "NEUTRAL"}, final_df, ["XYZ"])
        data = json.loads((tmp_path / "state_snapshot.json").read_text(encoding="utf-8"))
        assert data["signals"][0]["sector"] == ""

    def test_real_sector_string_passes_through(self, tmp_path, monkeypatch):
        self._redirect_output_dir(monkeypatch, tmp_path)
        final_df = pd.DataFrame([
            {"Symbol": "AAPL", "Action Signal": "HOLD", "Price": 190.0,
             "sector": "Technology"},
        ])
        _write_state_snapshot({"market_regime": "NEUTRAL"}, final_df, ["AAPL"])
        data = json.loads((tmp_path / "state_snapshot.json").read_text(encoding="utf-8"))
        assert data["signals"][0]["sector"] == "Technology"

    def test_score_components_threaded_through(self, tmp_path, monkeypatch):
        """pilots/scoring.py re-blends each symbol's persisted per-module
        score under a Pilot's weight vector by reading score_components —
        it must survive the dashboard_df -> state_snapshot.json round trip
        the same way both writers document it."""
        self._redirect_output_dir(monkeypatch, tmp_path)
        components = {"timeseries_momentum": 7.5, "rsi_extremes": -3.0}
        final_df = pd.DataFrame([
            {"Symbol": "AAPL", "Action Signal": "BUY", "Price": 190.0,
             "Score_Components": components},
        ])
        _write_state_snapshot({"market_regime": "RISK ON"}, final_df, ["AAPL"])
        data = json.loads((tmp_path / "state_snapshot.json").read_text(encoding="utf-8"))
        assert data["signals"][0]["score_components"] == components

    def test_missing_score_components_degrades_to_empty_dict(self, tmp_path, monkeypatch):
        self._redirect_output_dir(monkeypatch, tmp_path)
        final_df = pd.DataFrame([
            {"Symbol": "AAPL", "Action Signal": "HOLD", "Price": 190.0},
        ])
        _write_state_snapshot({"market_regime": "NEUTRAL"}, final_df, ["AAPL"])
        data = json.loads((tmp_path / "state_snapshot.json").read_text(encoding="utf-8"))
        assert data["signals"][0]["score_components"] == {}


# ===========================================================================
# 9. Robinhood account-cache integration (replaces the old uncached
#    RobinhoodClient().login()/.fetch_positions() call every cycle with
#    data.robinhood_portfolio.fetch_account_snapshot()'s three-tier
#    DB -> JSON -> live cache, mirrored via account_snapshot_to_robinhood_positions).
# ===========================================================================

def _ok_fetch_factory(tickers=("AAPL",)):
    async def _ok_fetch(de, tks):
        _ = de, tks
        df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]})
        return {}, {}, {t: df for t in tickers}
    return _ok_fetch


def _inactive_kill_switch():
    return type("K", (), {"is_active": lambda self: False})()


def _fake_run_pipeline_factory(captured):
    def _fake_run_pipeline(tickers, macro_raw, fund_raw, tech_raw, **kwargs):
        captured["tickers"] = list(tickers)
        captured["robinhood_positions"] = kwargs.get("robinhood_positions")
        return pd.DataFrame(), mock.MagicMock(), mock.MagicMock(
            xsec_percentile_ranks={}, multifactor_scores={},
        )
    return _fake_run_pipeline


class TestRobinhoodAccountCacheIntegration:
    """main_orchestrator._main_body_impl's Robinhood holdings integration now
    goes through data.robinhood_portfolio.fetch_account_snapshot() (the same
    three-tier DB->JSON->live cache main.py uses) instead of a fresh,
    uncached RobinhoodClient().login()/.fetch_positions() call every cycle.
    """

    def _common_patches(self, monkeypatch, tmp_path):
        # Force the credentials-absent branch (MockDataEngine path) so we
        # never touch FRED / a real DataEngine.
        monkeypatch.setattr(mo.os.path, "exists", lambda p: False)
        monkeypatch.setattr(mo, "fetch_all_data_async", _ok_fetch_factory())
        monkeypatch.setattr(mo, "GlobalKillSwitch", lambda *a, **k: _inactive_kill_switch())
        monkeypatch.setattr(mo.settings, "OUTPUT_DIR", tmp_path, raising=False)

    def test_successful_snapshot_populates_positions_and_merges_tickers(
        self, monkeypatch, tmp_path
    ) -> None:
        from data.robinhood_portfolio import AccountSnapshot, PortfolioPosition

        self._common_patches(monkeypatch, tmp_path)

        snapshot = AccountSnapshot(
            positions={
                "MSFT": PortfolioPosition(
                    symbol="MSFT", quantity=10.0, average_cost=250.0,
                    current_price=300.0, market_value=3000.0,
                    unrealized_pl=500.0, unrealized_pl_pct=20.0,
                    dividends_received=15.0, name="Microsoft Corp",
                ),
            },
            buying_power=1000.0, total_equity=4000.0, total_dividends=15.0,
            fetched_at=datetime.now(),
        )

        monkeypatch.setattr(mo, "fetch_account_snapshot", lambda: snapshot)

        captured: dict = {}
        monkeypatch.setattr(mo, "run_pipeline", _fake_run_pipeline_factory(captured))

        asyncio.run(mo._main_body_impl(effective_dry_run=True, strict=False))

        rh_positions = captured["robinhood_positions"]
        assert set(rh_positions.keys()) == {"MSFT"}
        dto = rh_positions["MSFT"]
        assert dto.ticker == "MSFT"
        assert dto.shares == 10.0
        assert dto.average_cost == 250.0
        assert dto.total_dividends == 15.0
        # MSFT wasn't in the base ["AAPL"] universe -> must be merged in.
        assert "MSFT" in captured["tickers"]

    def test_snapshot_failure_degrades_to_empty_positions_no_crash(
        self, monkeypatch, tmp_path
    ) -> None:
        """The dead-letter regression test: fetch_account_snapshot() CAN raise
        (live fetch fails AND no cache exists at all, per its own docstring).
        This must never crash _main_body_impl -- rh_positions degrades to {}
        and the pipeline continues exactly as the old failed-.login() path did.
        """
        self._common_patches(monkeypatch, tmp_path)

        def _boom():
            raise RuntimeError("no Robinhood cache and live fetch failed")

        monkeypatch.setattr(mo, "fetch_account_snapshot", _boom)

        captured: dict = {}
        monkeypatch.setattr(mo, "run_pipeline", _fake_run_pipeline_factory(captured))

        # Must not raise.
        asyncio.run(mo._main_body_impl(effective_dry_run=True, strict=False))

        assert captured["robinhood_positions"] == {}
        assert captured["tickers"] == ["AAPL"]

    def test_no_robinhood_client_import_remains(self) -> None:
        """Regression guard: the old uncached data.robinhood_client.RobinhoodClient
        call site is gone -- main_orchestrator now depends only on
        data.robinhood_portfolio's cached fetch_account_snapshot()."""
        assert not hasattr(mo, "RobinhoodClient")
        assert hasattr(mo, "fetch_account_snapshot")
        assert hasattr(mo, "account_snapshot_to_robinhood_positions")
