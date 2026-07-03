"""
tests/test_rationale_verbosity.py — Unit tests for Task 1.5.

Covers:
  - RATIONALE_VERBOSITY=standard (default): produces the exact same
    single-paragraph output as pre-1.5 code — no [A/B/C/D] markers.
  - RATIONALE_VERBOSITY=verbose: appends all four annotated sections.
  - Each verbose section degrades gracefully when its data is absent or None.
  - The `settings.RATIONALE_VERBOSITY` field exists with the correct default.
  - End-to-end evaluate() remains backward-compatible in standard mode.

All network I/O is monkeypatched.  TransactionsStore uses in-memory SQLite.
"""
from __future__ import annotations

import inspect
from typing import Optional
from unittest import mock

import pytest

from engine.advisory import _build_rationale, CONFIG, evaluate
from settings import settings as live_settings
from transactions_store import TransactionsStore


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _default_rationale_kwargs(**overrides) -> dict:
    """Return a fully-specified kwargs dict suitable for _build_rationale()."""
    base = dict(
        symbol="AAPL",
        action="BUY",
        score=70,
        raw_signal="BUY",
        macro_regime="RISK ON",
        forecast_price=105.0,
        current_price=100.0,
        unrealized_pl_pct=0.0,
        dividend_yield=0.01,
        dividends_received=0.0,
        is_holding=False,
        holding_override_reason="",
        rsi=50.0,
        aroon_osc=60.0,
        garch_vol=0.18,
        macro_gate_reason="",
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# TestSettingsField — the new RATIONALE_VERBOSITY setting exists
# ---------------------------------------------------------------------------

class TestSettingsField:
    def test_field_exists(self):
        assert hasattr(live_settings, "RATIONALE_VERBOSITY")

    def test_default_is_standard(self):
        # The default must be "standard" so existing deployments see no change.
        assert live_settings.RATIONALE_VERBOSITY == "standard"


# ---------------------------------------------------------------------------
# TestStandardMode — backward-compatibility: output unchanged from pre-1.5
# ---------------------------------------------------------------------------

class TestStandardMode:
    """Standard mode must produce a terse single-paragraph rationale with no
    verbose section markers."""

    def test_no_verbose_markers_in_output(self, monkeypatch):
        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", "standard")
        result = _build_rationale(**_default_rationale_kwargs())
        for marker in ("[A]", "[B]", "[C]", "[D]"):
            assert marker not in result

    def test_output_is_single_paragraph(self, monkeypatch):
        """Standard output must not contain the double-newline separator."""
        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", "standard")
        result = _build_rationale(**_default_rationale_kwargs())
        assert "\n\n" not in result

    def test_contains_symbol_and_action(self, monkeypatch):
        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", "standard")
        result = _build_rationale(**_default_rationale_kwargs(symbol="MSFT"))
        assert "MSFT" in result
        assert "Accumulate a new position" in result or "accumulate" in result.lower()

    def test_score_and_regime_in_standard(self, monkeypatch):
        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", "standard")
        result = _build_rationale(**_default_rationale_kwargs(score=70, macro_regime="RISK ON"))
        assert "70/100" in result
        assert "RISK ON" in result

    def test_macro_gate_reason_prepended_in_standard(self, monkeypatch):
        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", "standard")
        result = _build_rationale(**_default_rationale_kwargs(
            action="HOLD",
            macro_gate_reason="Hard gate: RECESSION regime — BUY→HOLD",
        ))
        # Gate reason must appear before the score description
        assert result.index("Hard gate") < result.index("multi-signal")

    def test_dividend_context_in_standard_hold(self, monkeypatch):
        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", "standard")
        result = _build_rationale(**_default_rationale_kwargs(
            action="HOLD",
            is_holding=True,
            dividend_yield=0.06,
            holding_override_reason="dividend hold bias rule: yield 6.0%",
        ))
        assert "dividend" in result.lower()


# ---------------------------------------------------------------------------
# TestVerboseModePresence — all four section headers appear in verbose mode
# ---------------------------------------------------------------------------

class TestVerboseModePresence:
    def _call_verbose(self, monkeypatch, **extra) -> str:
        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", "verbose")
        return _build_rationale(**_default_rationale_kwargs(**extra))

    def test_standard_para_still_present(self, monkeypatch):
        result = self._call_verbose(monkeypatch)
        assert "AAPL" in result
        assert "Raw strategy signal" in result

    def test_section_a_present(self, monkeypatch):
        result = self._call_verbose(monkeypatch)
        assert "[A]" in result

    def test_section_b_present(self, monkeypatch):
        result = self._call_verbose(monkeypatch)
        assert "[B]" in result

    def test_section_c_present(self, monkeypatch):
        result = self._call_verbose(monkeypatch)
        assert "[C]" in result

    def test_separator_between_standard_and_verbose(self, monkeypatch):
        result = self._call_verbose(monkeypatch)
        assert "\n\n" in result


# ---------------------------------------------------------------------------
# TestRegimeContextSection — [A]
# ---------------------------------------------------------------------------

class TestRegimeContextSection:
    def _verbose(self, monkeypatch, hmm=None, vix=18.0, sahm=0.0, spread=0.5) -> str:
        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", "verbose")
        return _build_rationale(**_default_rationale_kwargs(
            hmm_risk_on_probability=hmm,
            vix_value=vix,
            sahm_rule_indicator=sahm,
            yield_curve=spread,
        ))

    def test_high_hmm_produces_confirms_text(self, monkeypatch):
        result = self._verbose(monkeypatch, hmm=0.82)
        assert "strongly confirms" in result

    def test_mid_hmm_produces_uncertain_text(self, monkeypatch):
        result = self._verbose(monkeypatch, hmm=0.50)
        assert "uncertain" in result

    def test_low_hmm_produces_risk_off_warning(self, monkeypatch):
        result = self._verbose(monkeypatch, hmm=0.20)
        assert "risk-off" in result

    def test_none_hmm_produces_unavailable_text(self, monkeypatch):
        result = self._verbose(monkeypatch, hmm=None)
        assert "unavailable" in result

    def test_vix_appears_in_section_a(self, monkeypatch):
        result = self._verbose(monkeypatch, hmm=0.75, vix=28.3)
        assert "VIX=28.3" in result

    def test_sahm_appears_in_section_a(self, monkeypatch):
        result = self._verbose(monkeypatch, hmm=0.75, sahm=0.35)
        assert "Sahm Rule=0.35" in result


# ---------------------------------------------------------------------------
# TestCalibrationSection — [B]
# ---------------------------------------------------------------------------

class TestCalibrationSection:
    def _verbose(self, monkeypatch, win_rate_data=None) -> str:
        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", "verbose")
        return _build_rationale(**_default_rationale_kwargs(win_rate_data=win_rate_data))

    def test_with_win_rate_data_shows_percentage(self, monkeypatch):
        result = self._verbose(monkeypatch, win_rate_data=(0.64, 1.8, 169))
        assert "64%" in result

    def test_with_win_rate_data_shows_trade_count(self, monkeypatch):
        result = self._verbose(monkeypatch, win_rate_data=(0.64, 1.8, 169))
        assert "169" in result

    def test_with_win_rate_data_shows_payoff_ratio(self, monkeypatch):
        result = self._verbose(monkeypatch, win_rate_data=(0.64, 1.8, 169))
        assert "1.8:1" in result

    def test_positive_edge_labelled(self, monkeypatch):
        # p=0.60, b=2.0: edge = 0.60*2.0 - 0.40 = 0.80 > 0
        result = self._verbose(monkeypatch, win_rate_data=(0.60, 2.0, 50))
        assert "positive" in result

    def test_negative_edge_labelled(self, monkeypatch):
        # p=0.30, b=1.5: edge = 0.30*1.5 - 0.70 = -0.25 < 0
        result = self._verbose(monkeypatch, win_rate_data=(0.30, 1.5, 50))
        assert "negative" in result

    def test_none_win_rate_shows_fallback_text(self, monkeypatch):
        result = self._verbose(monkeypatch, win_rate_data=None)
        assert "Insufficient" in result or "< 30" in result


# ---------------------------------------------------------------------------
# TestInvalidationSection — [C]
# ---------------------------------------------------------------------------

class TestInvalidationSection:
    def _verbose(self, monkeypatch, **kw) -> str:
        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", "verbose")
        return _build_rationale(**_default_rationale_kwargs(**kw))

    def test_buy_signal_shows_score_drop_void(self, monkeypatch):
        result = self._verbose(monkeypatch, action="BUY")
        assert "score drop below" in result
        assert "RISK REDUCE" in result

    def test_sell_signal_shows_recovery_void(self, monkeypatch):
        result = self._verbose(monkeypatch, action="SELL")
        assert "score recovery above" in result

    def test_macro_vix_threshold_always_present(self, monkeypatch):
        result = self._verbose(monkeypatch)
        assert f"VIX > {CONFIG['macro_vix_gate_threshold']:.0f}" in result

    def test_macro_sahm_threshold_always_present(self, monkeypatch):
        result = self._verbose(monkeypatch)
        assert f"Sahm Rule" in result
        assert f"{CONFIG['macro_sahm_gate_threshold']:.1f}" in result

    def test_rsi_oversold_void_shown_when_applicable(self, monkeypatch):
        # RSI < 30 on a BUY → show the mean-reversion void condition
        result = self._verbose(monkeypatch, action="BUY", rsi=22.0)
        assert "RSI rising above 35" in result

    def test_rsi_oversold_void_not_shown_for_neutral_rsi(self, monkeypatch):
        # RSI = 55 on a BUY → no oversold void
        result = self._verbose(monkeypatch, action="BUY", rsi=55.0)
        assert "RSI rising above 35" not in result

    def test_rsi2_void_shown_when_applicable(self, monkeypatch):
        result = self._verbose(monkeypatch, action="BUY", rsi_2=5.0)
        assert "RSI(2)" in result

    def test_sector_veto_shown_for_financials(self, monkeypatch):
        result = self._verbose(monkeypatch, sector="Financials")
        # yield curve veto must be mentioned for vetoed sectors
        assert "yield curve inversion" in result or "OAS" in result

    def test_sector_veto_not_shown_for_tech(self, monkeypatch):
        result = self._verbose(monkeypatch, sector="Technology")
        assert "yield curve inversion" not in result

    def test_sma200_void_shown_when_provided(self, monkeypatch):
        result = self._verbose(monkeypatch, sma_200=195.50)
        assert "SMA-200" in result
        assert "$195.50" in result


# ---------------------------------------------------------------------------
# TestIndicatorTheorySection — [D]
# ---------------------------------------------------------------------------

class TestIndicatorTheorySection:
    def _verbose(self, monkeypatch, docs=None) -> str:
        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", "verbose")
        return _build_rationale(**_default_rationale_kwargs(active_module_docs=docs))

    def test_section_d_present_when_docs_populated(self, monkeypatch):
        docs = {"aroon_trend": "Aroon Oscillator chop-filtering for trend detection"}
        result = self._verbose(monkeypatch, docs=docs)
        assert "[D]" in result
        assert "Aroon Trend" in result

    def test_section_d_absent_when_docs_empty(self, monkeypatch):
        result = self._verbose(monkeypatch, docs={})
        assert "[D]" not in result

    def test_section_d_absent_when_docs_none(self, monkeypatch):
        result = self._verbose(monkeypatch, docs=None)
        assert "[D]" not in result

    def test_module_name_title_cased_in_section_d(self, monkeypatch):
        docs = {"timeseries_momentum": "Moskowitz/Ooi/Pedersen time-series momentum"}
        result = self._verbose(monkeypatch, docs=docs)
        assert "Timeseries Momentum" in result

    def test_module_doc_text_appears(self, monkeypatch):
        docs = {"macd_momentum": "MACD Bullish/Bearish crossover scoring"}
        result = self._verbose(monkeypatch, docs=docs)
        assert "MACD Bullish/Bearish crossover scoring" in result

    def test_capped_at_four_modules(self, monkeypatch):
        # Even with 6 modules in the dict, only 4 should appear.
        docs = {
            "mod_a": "Theory A",
            "mod_b": "Theory B",
            "mod_c": "Theory C",
            "mod_d": "Theory D",
            "mod_e": "Theory E",
            "mod_f": "Theory F",
        }
        result = self._verbose(monkeypatch, docs=docs)
        # At most 4 "Theory X" labels appear
        visible = [t for t in ["Theory A", "Theory B", "Theory C", "Theory D", "Theory E", "Theory F"] if t in result]
        assert len(visible) <= 4


# ---------------------------------------------------------------------------
# TestGracefulDegradation — no crash on any combination of missing data
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    """_build_rationale must never raise regardless of what verbose data
    is missing.  This mirrors CONSTRAINT #6 for the rationale builder."""

    def test_all_verbose_fields_none_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", "verbose")
        result = _build_rationale(**_default_rationale_kwargs(
            hmm_risk_on_probability=None,
            win_rate_data=None,
            active_module_docs=None,
            rsi_2=None,
            sma_200=None,
            sector="",
        ))
        assert isinstance(result, str)
        assert len(result) > 0

    def test_extreme_values_do_not_raise(self, monkeypatch):
        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", "verbose")
        result = _build_rationale(**_default_rationale_kwargs(
            vix_value=0.0,
            sahm_rule_indicator=-999.0,
            yield_curve=99.99,
            win_rate_data=(0.0, 0.0, 0),
        ))
        assert isinstance(result, str)

    def test_verbosity_unknown_value_falls_back_to_standard(self, monkeypatch):
        # Any value other than "verbose" should return the standard paragraph.
        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", "ultra")
        result = _build_rationale(**_default_rationale_kwargs())
        assert "[A]" not in result


# ---------------------------------------------------------------------------
# TestEndToEndIntegration — evaluate() with verbose mode via patched engines
# Mirrors the mock pattern in tests/test_advisory.py::TestAcceptanceCriteria
# ---------------------------------------------------------------------------

import numpy as np
import pandas as pd
from datetime import datetime, timezone
from unittest.mock import MagicMock


def _make_bars_for_rationale(n: int = 252, price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(end=datetime.today(), periods=n, freq="B")
    closes = np.full(n, price)
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes,
         "Volume": np.full(n, 100_000)},
        index=idx,
    )


_MOCK_TECH_RATIONALE = {
    "RSI": 55.0, "RSI_2": 8.0, "SMA_200": 190.0, "SMA_50": 185.0,
    "MACD_Line": 0.5, "MACD_Signal": 0.3, "Aroon Oscillator": 60.0,
    "ATR": 2.0, "Chandelier Exit": 88.0, "Sortino Ratio": 0.8,
    "Max Drawdown": -0.12, "RS vs SPY": 0.02, "RS-MACD": 0.1,
    "ROC_12M": 0.08, "ROC_6M": 0.04, "Momentum_Vol_Scaled": 0.01,
    "Realized_Vol_60D": 0.18, "VaR 95": -0.02, "Coppock Curve": 0.0,
    "Aroon Up": 80.0, "Aroon Down": 20.0, "Realized Slippage": 0.0,
    "Options IV Edge": 0.0, "CoVaR Proxy": 0.0,
}


class TestEndToEndIntegration:
    """Verify that evaluate() passes verbose data to _build_rationale without
    breaking any existing TestAcceptanceCriteria assertions."""

    def _run(self, monkeypatch, verbosity="standard"):
        from data.market_data import Quote
        from dto_models import MacroEconomicDTO

        monkeypatch.setattr(live_settings, "RATIONALE_VERBOSITY", verbosity)

        bars = _make_bars_for_rationale(n=252, price=100.0)
        quote = Quote(
            symbol="AAPL", price=100.0, bid=99.99, ask=100.01,
            timestamp=datetime.now(timezone.utc), is_stale=False, source="test",
        )
        market = MagicMock()
        market.get_latest_quote.return_value = quote
        market.get_intraday_bars.return_value = bars
        market.get_fundamentals.return_value = {"sector": "Technology", "Dividend Yield": 0.01}

        macro_dto = MacroEconomicDTO(
            vix_value=18.0,
            yield_curve_10y_2y=0.3,
            high_yield_oas=4.5,
            inflation_rate=2.5,
            sahm_rule_indicator=0.1,
            hmm_risk_on_probability=0.75,
        )

        with (
            mock.patch("engine.advisory.ProcessingEngine") as MockPE,
            mock.patch("engine.advisory.ForecastingEngine") as MockFE,
            mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE,
            mock.patch("engine.advisory.StrategyEngine") as MockSE,
        ):
            pe_inst = MockPE.return_value
            pe_inst.calculate_technical_metrics.return_value = {
                "AAPL": _MOCK_TECH_RATIONALE
            }

            fe_inst = MockFE.return_value
            fe_inst.generate_forecast.return_value = {
                "Forecast_30": 105.0,
                "MC_Target": 105.0,
            }

            toe_inst = MockTOE.return_value
            toe_inst.estimate_gjr_garch_volatility.return_value = 0.20

            se_inst = MockSE.return_value
            se_inst.evaluate_security.return_value = {
                "Action Signal": "BUY",
                "Score": 70,
                "Kelly Target": 0.05,
                "buyRange": "Buy Zone: $98.00 - $102.00",
                "sellRange": "Sell Zone: $105.00 - $110.00 | Stop @ $95.50",
                "Strategy Explainer Notes": "SCORE 70/100: +15pts Aroon Up.",
            }

            ts = TransactionsStore(db_url="sqlite:///:memory:")
            rec = evaluate(
                symbol="AAPL",
                position=None,
                market=market,
                snapshot=None,
                macro_dto=macro_dto,
                transactions_store=ts,
            )
        return rec

    def test_standard_mode_rationale_has_no_verbose_markers(self, monkeypatch):
        rec = self._run(monkeypatch, verbosity="standard")
        for marker in ("[A]", "[B]", "[C]", "[D]"):
            assert marker not in rec.rationale

    def test_verbose_mode_rationale_has_a_b_c(self, monkeypatch):
        rec = self._run(monkeypatch, verbosity="verbose")
        # [D] may be absent in test environments where the signal registry is
        # empty or all modules are inactive — check only A/B/C.
        assert "[A]" in rec.rationale
        assert "[B]" in rec.rationale
        assert "[C]" in rec.rationale

    def test_verbose_hmm_probability_in_section_a(self, monkeypatch):
        rec = self._run(monkeypatch, verbosity="verbose")
        # HMM p=0.75 → "strongly confirms" or the raw probability must appear
        assert "0.75" in rec.rationale or "strongly confirms" in rec.rationale

    def test_action_unchanged_between_verbosity_modes(self, monkeypatch):
        rec_std = self._run(monkeypatch, verbosity="standard")
        rec_vrb = self._run(monkeypatch, verbosity="verbose")
        assert rec_std.action == rec_vrb.action

    def test_conviction_unchanged_between_verbosity_modes(self, monkeypatch):
        rec_std = self._run(monkeypatch, verbosity="standard")
        rec_vrb = self._run(monkeypatch, verbosity="verbose")
        assert rec_std.conviction == rec_vrb.conviction
