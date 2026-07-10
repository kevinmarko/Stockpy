"""
tests/test_advisory_dedup_wiring.py
====================================
PR D (performance overhaul) — main_orchestrator.py's advisory-overlay
``_eval_one`` closure threads ``precomputed_garch``/``precomputed_forecast``
into ``engine.advisory.evaluate()`` when ``settings.ADVISORY_REUSE_PIPELINE_COMPUTE``
is enabled, sourced from the SAME cycle's ``dashboard_df['GARCH_Vol']`` /
``dashboard_df['Forecast_30']`` that ``run_pipeline()`` already computed.

``_eval_one`` is an inline closure inside ``_main_body`` (not importable), so
per this codebase's established convention (see ``tests/test_forecast_parallel.py``
for the identical pattern on the forecasting loop), this file reproduces the
EXACT wiring logic byte-for-byte and asserts on it directly — the logic under
test is copy-verified against ``main_orchestrator.py`` line-for-line, not a
paraphrase.

Covers:
  * flag OFF (default): precomputed_garch=None, precomputed_forecast=None is
    passed regardless of what dashboard_df carries for that row -- reproduces
    pre-PR-D behavior exactly.
  * flag ON: the row's GARCH_Vol / Forecast_30 values are threaded through
    verbatim.
  * settings default: ADVISORY_REUSE_PIPELINE_COMPUTE is False out of the box.
"""

from __future__ import annotations

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Reproduction of main_orchestrator.py's _eval_one precompute-selection logic
# (mirrors the exact lines added around the `_advisory_evaluate(...)` call).
# ---------------------------------------------------------------------------

def _select_precomputed(_row: pd.Series, reuse_pipeline_compute: bool):
    """Byte-for-byte reproduction of the precompute-selection block inside
    main_orchestrator.py's `_eval_one` closure."""
    _precomputed_garch = None
    _precomputed_forecast = None
    if reuse_pipeline_compute:
        _precomputed_garch = _row.get('GARCH_Vol')
        _precomputed_forecast = _row.get('Forecast_30')
    return _precomputed_garch, _precomputed_forecast


class TestPrecomputeSelectionWiring:
    def test_flag_off_always_passes_none_regardless_of_row_contents(self):
        row = pd.Series({'Symbol': 'AAPL', 'GARCH_Vol': 0.35, 'Forecast_30': 150.0})
        garch, forecast = _select_precomputed(row, reuse_pipeline_compute=False)
        assert garch is None
        assert forecast is None

    def test_flag_on_threads_row_values_verbatim(self):
        row = pd.Series({'Symbol': 'AAPL', 'GARCH_Vol': 0.35, 'Forecast_30': 150.0})
        garch, forecast = _select_precomputed(row, reuse_pipeline_compute=True)
        assert garch == pytest.approx(0.35)
        assert forecast == pytest.approx(150.0)

    def test_flag_on_missing_columns_degrades_to_none(self):
        """A row from a degraded cycle (e.g. GARCH fit failed upstream and
        the column was never populated for this ticker) must not raise --
        advisory.evaluate()'s own >0 guard is the actual safety net, but the
        wiring itself must tolerate a missing key too."""
        row = pd.Series({'Symbol': 'AAPL'})  # no GARCH_Vol / Forecast_30 keys
        garch, forecast = _select_precomputed(row, reuse_pipeline_compute=True)
        assert garch is None
        assert forecast is None

    def test_flag_on_zero_placeholder_passes_through_zero(self):
        """dashboard_df initializes GARCH_Vol=0.0 as a placeholder before the
        options loop fills it in; if a ticker's fit failed upstream the
        placeholder 0.0 survives to this row. The wiring passes it through
        as-is -- advisory.evaluate()'s `> 0` guard (not this selection logic)
        is what correctly rejects it and falls through to a fresh fit."""
        row = pd.Series({'Symbol': 'AAPL', 'GARCH_Vol': 0.0, 'Forecast_30': 0.0})
        garch, forecast = _select_precomputed(row, reuse_pipeline_compute=True)
        assert garch == 0.0
        assert forecast == 0.0


class TestEvaluateRejectsNonPositivePrecomputedValues:
    """End-to-end proof that a passed-through zero/placeholder is safely
    rejected by advisory.evaluate() itself (the actual dead-letter gate),
    closing the loop the wiring-only tests above leave open."""

    def test_zero_garch_and_forecast_trigger_fresh_fit_not_fabrication(self):
        import unittest.mock as mock
        from unittest.mock import MagicMock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore
        from tests.test_advisory import (
            _make_market_provider, _make_bars, _make_account_snapshot, _MOCK_TECH,
        )

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))
        snapshot = _make_account_snapshot()

        row = pd.Series({'Symbol': 'TEST', 'GARCH_Vol': 0.0, 'Forecast_30': 0.0})
        garch, forecast = _select_precomputed(row, reuse_pipeline_compute=True)

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            pe_instance = MagicMock()
            pe_instance.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockPE.return_value = pe_instance

            fe_instance = MagicMock()
            fe_instance.generate_forecast.return_value = {"Forecast_30": 108.0}
            MockFE.return_value = fe_instance

            toe_instance = MagicMock()
            toe_instance.estimate_gjr_garch_volatility.return_value = 0.19
            MockTOE.return_value = toe_instance

            se_instance = MagicMock()
            se_instance.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.0,
            }
            MockSE.return_value = se_instance

            rec = evaluate(
                symbol="TEST", position=None, market=market, snapshot=snapshot,
                transactions_store=ts,
                precomputed_garch=garch, precomputed_forecast=forecast,
            )

            # The zero placeholders were rejected -- both engines still fit fresh.
            assert toe_instance.estimate_gjr_garch_volatility.called
            assert fe_instance.generate_forecast.called
        assert rec.key_indicators["garch_vol"] == pytest.approx(0.19)
        assert rec.forecast == pytest.approx(108.0)


class TestSettingsDefault:
    def test_advisory_reuse_pipeline_compute_defaults_false(self):
        from settings import Settings
        assert Settings().ADVISORY_REUSE_PIPELINE_COMPUTE is False
