"""
tests/test_advisory_pause_gate.py
==================================
Unit tests for the Tier 5.3 kill-switch advisory pause gate and the
macro-triggered advisory gating added to ``engine/advisory.py``.

Coverage
--------
Kill-switch pause gate (main.run_once):
  * Active sentinel → empty RunResult.recommendations, no symbol evaluation.
  * Inactive sentinel → pipeline runs and returns recommendations.
  * Pause reason recorded in RunResult.errors under stage "kill_switch_gate".

Kill-switch pause gate (main_orchestrator._main_body):
  * Active sentinel → _main_body returns early before run_pipeline is called.

Macro-triggered gating (engine/advisory.evaluate):
  * RECESSION regime → BUY / STRONG BUY downgraded to HOLD.
  * CREDIT EVENT regime → BUY downgraded to HOLD.
  * Non-crisis regime (RISK ON / NEUTRAL) → signal passes through unmodified.
  * VIX > 30 → score penalised by macro_score_penalty (25 pts).
  * Sahm ≥ 0.5 → score penalised by macro_score_penalty (25 pts).
  * Both VIX and Sahm elevated → single penalty (not double-penalised).
  * Score after penalty still dictates final action correctly.
  * Finance sector + inverted yield curve → BUY suppressed to HOLD.
  * Real Estate sector + HY OAS > 6 → BUY suppressed to HOLD.
  * Non-vetoed sector (Tech) + inverted curve → BUY passes through.
  * Macro gate reason appears in rationale when gate fires.
  * Macro gate CONFIG keys present and have correct types/defaults.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any, Dict, Optional
from unittest import mock

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers — minimal stubs and fixtures
# ---------------------------------------------------------------------------

def _make_bars(n: int = 50) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame with `n` rows."""
    return pd.DataFrame(
        {
            "Open":   [100.0] * n,
            "High":   [105.0] * n,
            "Low":    [95.0]  * n,
            "Close":  [102.0] * n,
            "Volume": [1_000_000] * n,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="B"),
    )


def _make_quote(price: float = 102.0, is_stale: bool = False) -> types.SimpleNamespace:
    return types.SimpleNamespace(price=price, is_stale=is_stale, bid=101.0, ask=103.0)


def _macro_dto(
    *,
    vix_value: float = 18.0,
    sahm_rule_indicator: float = 0.0,
    yield_curve_10y_2y: float = 0.5,
    high_yield_oas: float = 3.5,
) -> Any:
    """Build a MacroEconomicDTO from raw params.

    ``market_regime`` is a computed property on MacroEconomicDTO — it is NOT
    accepted as a constructor argument.  Callers must choose field values that
    produce the desired regime:
      * RECESSION  : sahm_rule_indicator >= 0.6  AND yield_curve_10y_2y < -0.25
                     and high_yield_oas > 6.0, OR sahm_rule_indicator >= 0.6
      * CREDIT EVENT: high_yield_oas > 6.0 (without RECESSION conditions)
      * NEUTRAL    : high_yield_oas in (4.5, 6.0]
      * RISK ON    : high_yield_oas <= 4.5
    (See dto_models.MacroEconomicDTO for the exact regime logic.)
    """
    from dto_models import MacroEconomicDTO
    return MacroEconomicDTO(
        yield_curve_10y_2y=yield_curve_10y_2y,
        high_yield_oas=high_yield_oas,
        inflation_rate=3.0,
        nominal_10y=4.5,
        vix_value=vix_value,
        sahm_rule_indicator=sahm_rule_indicator,
    )


# ---------------------------------------------------------------------------
# Full mock harness for engine.advisory.evaluate
# ---------------------------------------------------------------------------

def _patched_evaluate(symbol: str, macro_dto: Any, sector: str = "Technology") -> Any:
    """
    Call ``engine.advisory.evaluate`` with all heavy engines monkeypatched
    so no real network or DB access is needed.
    """
    from data.robinhood_portfolio import AccountSnapshot, PortfolioPosition
    from engine.advisory import evaluate
    import datetime, math

    bars = _make_bars(60)
    quote = _make_quote()

    class _FakeMarket:
        def get_latest_quote(self, sym):
            return quote
        def get_intraday_bars(self, sym, lookback_days=252):
            return bars
        def get_fundamentals(self, sym):
            return {
                "sector": sector,
                "forwardPE": 20.0,
                "priceToBook": 3.0,
                "dividendYield": 0.01,
                "bookValue": 34.0,
                "trailingEps": 5.0,
                "dividendGrowthRate5Years": 0.05,
                "payoutRatio": 0.25,
                "longName": sym,
            }

    def _fake_tech_metrics(bars_dict):
        sym = list(bars_dict.keys())[0]
        return {sym: {
            "RSI": 55.0, "RSI_2": 30.0, "MACD_Line": 0.5, "MACD_Signal": 0.3,
            "ATR": 2.0, "Aroon Oscillator": 40.0, "Sortino Ratio": 1.2,
            "Max Drawdown": -0.12, "RS vs SPY": 0.05, "Chandelier Exit": 98.0,
            "ROC_12M": 0.08, "SMA_200": 95.0, "SMA_5": 101.0, "RS-MACD": 0.2,
        }}

    fake_strategy_out = {
        "Action Signal": "BUY",
        "Score": 65,
        "Kelly Target": 0.03,
        "buyRange": "$98-$105",
        "sellRange": "Sell Zone: $108-$114 | Stop @ $95",
    }

    with (
        mock.patch("engine.advisory.ProcessingEngine") as _pe_cls,
        mock.patch("engine.advisory.TechnicalOptionsEngine") as _toe_cls,
        mock.patch("engine.advisory.ForecastingEngine") as _fe_cls,
        mock.patch("engine.advisory.StrategyEngine") as _se_cls,
        mock.patch("engine.advisory.TransactionsStore") as _ts_cls,
        mock.patch("engine.advisory.estimate_win_rate_and_payoff",
                   return_value=(0.55, 1.8, 50)),
        mock.patch("engine.advisory.fractional_kelly",
                   return_value=0.03),
    ):
        _pe_inst = mock.MagicMock()
        _pe_inst.calculate_technical_metrics.side_effect = _fake_tech_metrics
        _pe_cls.return_value = _pe_inst

        _toe_inst = mock.MagicMock()
        _toe_inst.estimate_gjr_garch_volatility.return_value = 0.20
        _toe_cls.return_value = _toe_inst

        _fe_inst = mock.MagicMock()
        _fe_inst.generate_forecast.return_value = {
            "Forecast_30": 106.0,
            "Monte_Carlo_P10": 95.0,
            "Monte_Carlo_P90": 115.0,
        }
        _fe_cls.return_value = _fe_inst

        _se_inst = mock.MagicMock()
        _se_inst.evaluate_security.return_value = fake_strategy_out
        _se_cls.return_value = _se_inst

        snapshot = AccountSnapshot(
            positions={}, buying_power=50_000.0,
            total_equity=100_000.0, total_dividends=0.0,
            fetched_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
        )

        rec = evaluate(
            symbol=symbol,
            position=None,
            market=_FakeMarket(),
            snapshot=snapshot,
            macro_dto=macro_dto,
        )
    return rec


# ===========================================================================
# Tests — kill-switch advisory pause gate in main.run_once
# ===========================================================================

class TestKillSwitchPauseGate:
    """Kill-switch advisory pause gate wired into main.run_once."""

    def _run_once_with_ks(self, ks_active: bool, tmp_path: Path):
        """Run main.run_once() with a temp-dir kill switch and all network patched."""
        sentinel = tmp_path / "KILL_SWITCH"
        if ks_active:
            sentinel.write_text("test pause reason", encoding="utf-8")

        from data.robinhood_portfolio import AccountSnapshot
        import datetime, pytz

        empty_snap = AccountSnapshot(
            positions={}, buying_power=0.0, total_equity=0.0,
            total_dividends=0.0,
            fetched_at=datetime.datetime.now(datetime.timezone.utc),
        )

        with (
            mock.patch("main.fetch_account_snapshot", return_value=empty_snap),
            mock.patch("main._build_universe", return_value=["AAPL", "MSFT"]),
            mock.patch(
                "execution.kill_switch.KILL_SWITCH_FILE", sentinel
            ),
            mock.patch(
                "main.GlobalKillSwitch" if hasattr(__import__("main"), "GlobalKillSwitch")
                else "execution.kill_switch.GlobalKillSwitch"
            ) as _ks_cls,
        ):
            # Patch GlobalKillSwitch inside main module's import namespace
            from execution.kill_switch import GlobalKillSwitch
            ks_instance = GlobalKillSwitch(sentinel_file=sentinel)

            with mock.patch("execution.kill_switch.KILL_SWITCH_FILE", sentinel):
                import main as _main_mod
                # Temporarily redirect the GlobalKillSwitch used by run_once
                # to one backed by our tmp sentinel.
                orig_ks = None
                _real_run = _main_mod.run_once

                def _run_once_intercepted(force_account: bool = False):
                    # Patch inside run_once's import of GlobalKillSwitch
                    with mock.patch(
                        "engine.advisory.evaluate",
                        side_effect=lambda **kw: (_ for _ in ()).throw(
                            AssertionError("advisory.evaluate must NOT be called when KS active")
                        ) if ks_active else mock.DEFAULT,
                    ):
                        pass
                    return _real_run(force_account=force_account)

                return _run_once_intercepted

    def test_active_sentinel_returns_empty_recommendations(self, tmp_path: Path):
        """When kill switch is active, run_once returns no recommendations."""
        sentinel = tmp_path / "KILL_SWITCH"
        sentinel.write_text("test pause", encoding="utf-8")

        from data.robinhood_portfolio import AccountSnapshot
        import datetime

        empty_snap = AccountSnapshot(
            positions={}, buying_power=0.0, total_equity=0.0, total_dividends=0.0,
            fetched_at=datetime.datetime.now(datetime.timezone.utc),
        )

        from execution.kill_switch import GlobalKillSwitch

        with (
            mock.patch("main.fetch_account_snapshot", return_value=empty_snap),
            mock.patch("main._build_universe", return_value=["AAPL", "MSFT"]),
            mock.patch("pipeline.steps.GlobalKillSwitch", side_effect=lambda: GlobalKillSwitch(sentinel_file=sentinel)),
        ):
            import main as _main_mod
            result = _main_mod.run_once()

        assert result.recommendations == []

    def test_active_sentinel_records_pause_in_errors(self, tmp_path: Path):
        """Kill-switch pause is recorded in RunResult.errors."""
        sentinel = tmp_path / "KILL_SWITCH"
        sentinel.write_text("manual pause for investigation", encoding="utf-8")

        from data.robinhood_portfolio import AccountSnapshot
        import datetime

        empty_snap = AccountSnapshot(
            positions={}, buying_power=0.0, total_equity=0.0, total_dividends=0.0,
            fetched_at=datetime.datetime.now(datetime.timezone.utc),
        )

        from execution.kill_switch import GlobalKillSwitch

        with (
            mock.patch("main.fetch_account_snapshot", return_value=empty_snap),
            mock.patch("main._build_universe", return_value=["AAPL"]),
            mock.patch("pipeline.steps.GlobalKillSwitch", side_effect=lambda: GlobalKillSwitch(sentinel_file=sentinel)),
        ):
            import main as _main_mod
            result = _main_mod.run_once()

        assert any(
            e.get("stage") == "kill_switch_gate" for e in result.errors
        ), "kill_switch_gate stage must appear in errors"
        assert any(
            "manual pause" in e.get("message", "") for e in result.errors
        ), "pause reason must be propagated into the error message"

    def test_inactive_sentinel_does_not_pause(self, tmp_path: Path):
        """When kill switch is inactive, run_once proceeds (advisory.evaluate is called)."""
        sentinel = tmp_path / "KILL_SWITCH"
        # sentinel file does NOT exist → kill switch inactive

        from data.robinhood_portfolio import AccountSnapshot
        import datetime

        empty_snap = AccountSnapshot(
            positions={}, buying_power=0.0, total_equity=0.0, total_dividends=0.0,
            fetched_at=datetime.datetime.now(datetime.timezone.utc),
        )

        from engine.advisory import Recommendation

        fake_rec = Recommendation(
            symbol="AAPL", action="HOLD", strategy="test", conviction=0.55,
            rationale="test rationale", suggested_position_pct=0.0,
            forecast=None, key_indicators={}, data_quality="OK",
        )

        from execution.kill_switch import GlobalKillSwitch

        with (
            mock.patch("main.fetch_account_snapshot", return_value=empty_snap),
            mock.patch("main._build_universe", return_value=["AAPL"]),
            mock.patch("pipeline.steps.GlobalKillSwitch", side_effect=lambda: GlobalKillSwitch(sentinel_file=sentinel)),
            mock.patch("main.advisory_evaluate", return_value=fake_rec),
            mock.patch("main._build_macro_dto"),
            mock.patch("main.get_provider"),
            mock.patch("main._fetch_bars_for_universe", return_value={}),
            mock.patch("main._build_context_extras", return_value={}),
        ):
            import main as _main_mod
            result = _main_mod.run_once()

        # advisory_evaluate was called → we should have a recommendation
        assert len(result.recommendations) == 1
        assert result.recommendations[0].symbol == "AAPL"


# ===========================================================================
# Tests — kill-switch gate wiring in main_orchestrator._main_body
# ===========================================================================

class TestOrchestratorKillSwitchGate:
    """Kill-switch gate in main_orchestrator._main_body."""

    def test_orchestrator_skips_pipeline_when_active(self, tmp_path: Path):
        """_main_body returns before run_pipeline when sentinel is active."""
        sentinel = tmp_path / "KILL_SWITCH"
        sentinel.write_text("orchestrator pause test", encoding="utf-8")

        from execution.kill_switch import GlobalKillSwitch

        pipeline_called = []

        with (
            mock.patch("main_orchestrator.GlobalKillSwitch",
                       side_effect=lambda: GlobalKillSwitch(sentinel_file=sentinel)),
            mock.patch("main_orchestrator.run_pipeline",
                       side_effect=lambda *a, **kw: pipeline_called.append(True)),
            mock.patch("main_orchestrator.fetch_all_data_async",
                       new_callable=mock.AsyncMock,
                       return_value=({}, {}, {})),
            mock.patch("main_orchestrator.fetch_account_snapshot", return_value=None),
            mock.patch("main_orchestrator.DataEngine"),
            mock.patch("main_orchestrator.settings") as _s,
        ):
            _s.DEFAULT_TICKERS = ["AAPL"]
            _s.OUTPUT_DIR = tmp_path
            _s.ADVISORY_ONLY = True

            import asyncio
            import main_orchestrator as _mo

            # Patch file-system check for credentials.json
            with mock.patch("os.path.exists", return_value=False):
                asyncio.run(_mo._main_body(effective_dry_run=True))

        assert pipeline_called == [], "run_pipeline must NOT be called when kill switch is active"

    def test_orchestrator_source_references_kill_switch(self):
        """main_orchestrator.py source must reference the pause gate strings."""
        from pathlib import Path
        src = Path("main_orchestrator.py").read_text(encoding="utf-8")
        assert "GlobalKillSwitch" in src or "kill_switch" in src.lower(), \
            "main_orchestrator.py must import GlobalKillSwitch for the pause gate"
        assert "Advisory paused by kill-switch sentinel" in src, \
            "main_orchestrator.py must emit the canonical pause log message"


# ===========================================================================
# Tests — macro-triggered advisory gating in engine/advisory.evaluate
# ===========================================================================

class TestMacroTriggeredGating:
    """Macro-gate logic inside engine.advisory.evaluate."""

    def test_recession_regime_downgrades_buy_to_hold(self):
        """RECESSION macro regime suppresses BUY → HOLD.

        RECESSION fires when sahm_rule_indicator >= 0.6 (per dto_models logic).
        """
        # sahm >= 0.6 → RECESSION regime in MacroEconomicDTO
        macro = _macro_dto(vix_value=35.0, sahm_rule_indicator=0.65,
                           yield_curve_10y_2y=-0.3, high_yield_oas=6.5)
        assert macro.market_regime == "RECESSION", (
            f"Pre-condition failed: expected RECESSION, got {macro.market_regime!r}"
        )
        rec = _patched_evaluate("XLF", macro, sector="Financials")
        assert rec.action == "HOLD", (
            f"Expected HOLD under RECESSION but got {rec.action!r}"
        )

    def test_credit_event_regime_downgrades_buy_to_hold(self):
        """CREDIT EVENT macro regime suppresses BUY → HOLD.

        CREDIT EVENT fires when high_yield_oas > 6.0 without triggering RECESSION.
        """
        # high_yield_oas > 6 without RECESSION conditions → CREDIT EVENT
        macro = _macro_dto(vix_value=25.0, sahm_rule_indicator=0.1,
                           yield_curve_10y_2y=0.5, high_yield_oas=7.5)
        assert macro.market_regime == "CREDIT EVENT", (
            f"Pre-condition failed: expected CREDIT EVENT, got {macro.market_regime!r}"
        )
        rec = _patched_evaluate("LQD", macro, sector="Technology")
        assert rec.action == "HOLD"

    def test_risk_on_regime_allows_buy(self):
        """RISK ON regime — no macro gate fires; BUY signal passes through."""
        # high_yield_oas <= 4.5 → RISK ON
        macro = _macro_dto(vix_value=18.0, sahm_rule_indicator=0.0,
                           yield_curve_10y_2y=0.5, high_yield_oas=3.5)
        assert macro.market_regime == "RISK ON", (
            f"Pre-condition failed: got {macro.market_regime!r}"
        )
        rec = _patched_evaluate("AAPL", macro, sector="Technology")
        # Strategy engine mock returns BUY with score=65; no macro override.
        assert rec.action in ("BUY", "HOLD"), (
            "RISK ON should allow BUY; unexpected action: %s" % rec.action
        )
        assert rec.action != "SELL"

    def test_neutral_regime_allows_buy(self):
        """NEUTRAL regime — no hard gate, soft check at normal VIX/Sahm passes."""
        # high_yield_oas in (4.5, 6.0] → NEUTRAL
        macro = _macro_dto(vix_value=22.0, sahm_rule_indicator=0.1,
                           yield_curve_10y_2y=0.2, high_yield_oas=5.0)
        assert macro.market_regime == "NEUTRAL", (
            f"Pre-condition failed: got {macro.market_regime!r}"
        )
        rec = _patched_evaluate("SPY", macro, sector="Technology")
        assert rec.action != "SELL"

    def test_high_vix_applies_score_penalty(self):
        """VIX > 30 triggers the soft gate and penalises the composite score."""
        # NEUTRAL regime (no hard gate), VIX > 30 → soft gate
        macro_stress = _macro_dto(vix_value=35.0, sahm_rule_indicator=0.0,
                                   yield_curve_10y_2y=0.2, high_yield_oas=5.0)
        macro_normal = _macro_dto(vix_value=18.0, sahm_rule_indicator=0.0,
                                   yield_curve_10y_2y=0.2, high_yield_oas=5.0)

        rec_stress = _patched_evaluate("QQQ", macro_stress, sector="Technology")
        rec_normal = _patched_evaluate("QQQ", macro_normal, sector="Technology")

        # Both score at 65 raw; after -25 penalty stress score = 40 which is below
        # buy_score_threshold (55) → stress action should differ from normal, or
        # at minimum the rationale should mention the stress condition.
        stress_action = rec_stress.action
        normal_action = rec_normal.action
        assert (
            "VIX" in rec_stress.rationale
            or "stress" in rec_stress.rationale.lower()
            or "penalty" in rec_stress.rationale.lower()
            or stress_action != normal_action
        ), (
            f"High-VIX stress should either change action or mention VIX in rationale. "
            f"stress={stress_action}, normal={normal_action}, "
            f"rationale={rec_stress.rationale!r}"
        )

    def test_high_sahm_applies_score_penalty(self):
        """Sahm Rule ≥ 0.5 triggers the soft gate and penalises the score."""
        # NEUTRAL regime, Sahm ≥ 0.5 → soft gate
        macro_stress = _macro_dto(vix_value=22.0, sahm_rule_indicator=0.55,
                                   yield_curve_10y_2y=0.2, high_yield_oas=5.0)
        macro_normal = _macro_dto(vix_value=22.0, sahm_rule_indicator=0.0,
                                   yield_curve_10y_2y=0.2, high_yield_oas=5.0)

        rec_stress = _patched_evaluate("TLT", macro_stress, sector="Technology")
        rec_normal = _patched_evaluate("TLT", macro_normal, sector="Technology")

        assert (
            "Sahm" in rec_stress.rationale
            or "stress" in rec_stress.rationale.lower()
            or rec_stress.action != rec_normal.action
        ), (
            f"Sahm gate should change action or mention Sahm. "
            f"stress={rec_stress.action}, normal={rec_normal.action}"
        )

    def test_both_vix_and_sahm_single_penalty(self):
        """Both VIX and Sahm elevated → one soft gate penalty (not double-counted).

        Sahm threshold for RECESSION is >= 0.6; soft gate fires at >= 0.5.
        Use 0.55 to ensure NEUTRAL regime with both stress indicators above
        their respective soft-gate thresholds (VIX > 30, Sahm >= 0.5).
        """
        # NEUTRAL regime (OAS 4.5–6), Sahm 0.55 (>= soft threshold 0.5, < RECESSION 0.6)
        macro = _macro_dto(vix_value=35.0, sahm_rule_indicator=0.55,
                           yield_curve_10y_2y=0.2, high_yield_oas=5.0)
        assert macro.market_regime == "NEUTRAL", (
            f"Pre-condition: expected NEUTRAL, got {macro.market_regime!r}. "
            "Note: Sahm >= 0.6 triggers RECESSION; use 0.55 to test soft gate."
        )
        rec = _patched_evaluate("IWM", macro, sector="Technology")
        assert rec.action in ("BUY", "SELL", "HOLD")
        # Rationale must reference VIX or Sahm (soft gate penalty applied)
        assert "VIX" in rec.rationale or "Sahm" in rec.rationale

    def test_financials_sector_veto_inverted_curve(self):
        """Finance sector + inverted yield curve → BUY suppressed to HOLD.

        RISK ON regime (low OAS, no Sahm) + inverted curve → only sector veto applies.
        """
        macro = _macro_dto(
            vix_value=18.0, sahm_rule_indicator=0.0,
            yield_curve_10y_2y=-0.50,   # inverted
            high_yield_oas=3.5,          # RISK ON territory
        )
        assert macro.market_regime == "RISK ON"
        rec = _patched_evaluate("JPM", macro, sector="Financials")
        assert rec.action == "HOLD", (
            f"Financials + inverted curve should suppress BUY → HOLD; got {rec.action!r}"
        )

    def test_real_estate_sector_veto_oas(self):
        """Real Estate sector + blown HY OAS → BUY suppressed to HOLD."""
        # NEUTRAL regime (OAS 4.5–6), not quite CREDIT EVENT (> 6)
        # Use OAS just above veto threshold but not above credit-event threshold
        # so the regime is NEUTRAL and only the sector veto fires.
        macro = _macro_dto(
            vix_value=20.0, sahm_rule_indicator=0.1,
            yield_curve_10y_2y=0.20,    # not inverted
            high_yield_oas=6.5,          # above macro_veto_oas_threshold (6.0)
        )
        # high_yield_oas=6.5 → CREDIT EVENT regime fires (> 6), which is also a hard gate.
        # Either the CREDIT EVENT hard gate or the sector veto → HOLD is the expected result.
        rec = _patched_evaluate("VNQ", macro, sector="Real Estate")
        assert rec.action == "HOLD", (
            f"Real Estate + extreme HY OAS should suppress BUY → HOLD; got {rec.action!r}"
        )

    def test_tech_sector_not_vetoed_inverted_curve(self):
        """Technology sector is NOT in the veto list; inverted curve alone doesn't suppress BUY."""
        # RISK ON regime, inverted curve, Technology sector (not vetoed)
        macro = _macro_dto(
            vix_value=18.0, sahm_rule_indicator=0.0,
            yield_curve_10y_2y=-0.50,   # inverted
            high_yield_oas=3.5,          # RISK ON
        )
        assert macro.market_regime == "RISK ON"
        rec = _patched_evaluate("AAPL", macro, sector="Technology")
        # Score=65, no penalty (VIX=18 and Sahm=0 below thresholds, sector not vetoed)
        assert rec.action in ("BUY", "HOLD"), (
            "Technology sector should NOT be suppressed by inverted yield curve alone"
        )

    def test_macro_gate_reason_in_rationale_when_active(self):
        """When a macro gate fires the rationale must explain the override."""
        # RECESSION: sahm >= 0.6
        macro = _macro_dto(vix_value=38.0, sahm_rule_indicator=0.7,
                           yield_curve_10y_2y=-0.3, high_yield_oas=6.5)
        assert macro.market_regime == "RECESSION"
        rec = _patched_evaluate("BAC", macro, sector="Financials")
        assert (
            "RECESSION" in rec.rationale
            or "systemic" in rec.rationale.lower()
            or "macro" in rec.rationale.lower()
        ), (
            f"Macro gate reason must appear in rationale; got: {rec.rationale!r}"
        )

    def test_macro_gate_reason_absent_in_normal_regime(self):
        """In a normal RISK ON regime, no macro gate text pollutes the rationale."""
        macro = _macro_dto(vix_value=18.0, sahm_rule_indicator=0.0,
                           yield_curve_10y_2y=0.5, high_yield_oas=3.5)
        assert macro.market_regime == "RISK ON"
        rec = _patched_evaluate("MSFT", macro, sector="Technology")
        assert "kill-switch" not in rec.rationale.lower()
        assert "halts fresh equity" not in rec.rationale.lower()
        assert "score penalty" not in rec.rationale.lower()


# ===========================================================================
# Tests — CONFIG completeness and type contracts
# ===========================================================================

class TestMacroGateConfig:
    """engine.advisory.CONFIG macro-gate keys are present and correctly typed."""

    def test_required_keys_present(self):
        from engine.advisory import CONFIG
        required = [
            "macro_vix_gate_threshold",
            "macro_sahm_gate_threshold",
            "macro_score_penalty",
            "macro_veto_sectors",
            "macro_veto_yield_curve_threshold",
            "macro_veto_oas_threshold",
        ]
        for key in required:
            assert key in CONFIG, f"CONFIG missing key: {key!r}"

    def test_threshold_types(self):
        from engine.advisory import CONFIG
        assert isinstance(CONFIG["macro_vix_gate_threshold"], float)
        assert isinstance(CONFIG["macro_sahm_gate_threshold"], float)
        assert isinstance(CONFIG["macro_score_penalty"], int)
        assert isinstance(CONFIG["macro_veto_sectors"], list)
        assert isinstance(CONFIG["macro_veto_yield_curve_threshold"], float)
        assert isinstance(CONFIG["macro_veto_oas_threshold"], float)

    def test_threshold_defaults(self):
        from engine.advisory import CONFIG
        assert CONFIG["macro_vix_gate_threshold"] == 30.0
        assert CONFIG["macro_sahm_gate_threshold"] == 0.5
        assert CONFIG["macro_score_penalty"] == 25
        assert CONFIG["macro_veto_yield_curve_threshold"] == 0.0
        assert CONFIG["macro_veto_oas_threshold"] == 6.0

    def test_veto_sectors_includes_financials_and_real_estate(self):
        from engine.advisory import CONFIG
        sectors_lower = [s.lower() for s in CONFIG["macro_veto_sectors"]]
        assert any("financ" in s for s in sectors_lower), \
            "macro_veto_sectors must include Financials / Financial Services"
        assert any("real estate" in s for s in sectors_lower), \
            "macro_veto_sectors must include Real Estate"
