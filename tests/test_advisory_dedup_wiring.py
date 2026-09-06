"""
tests/test_advisory_dedup_wiring.py
====================================
PR D (performance overhaul) — pipeline/production_steps.py's advisory-overlay
``_eval_one`` closure threads ``precomputed_garch``/``precomputed_forecast``
(and, since the Forecast_{h}_Is_Fallback disclosure flag, ``precomputed_forecast_is_fallback``)
into ``engine.advisory.evaluate()`` when ``settings.ADVISORY_REUSE_PIPELINE_COMPUTE``
is enabled, sourced from the SAME cycle's ``dashboard_df['GARCH_Vol']`` /
``dashboard_df['Forecast_30']`` / ``dashboard_df['Forecast_30_Is_Fallback']``
that ``run_pipeline()`` already computed.

``_eval_one`` is an inline closure (not importable), so per this codebase's
established convention (see ``tests/test_forecast_parallel.py`` for the
identical pattern on the forecasting loop), this file reproduces the EXACT
wiring logic byte-for-byte and asserts on it directly — the logic under test
is copy-verified against ``pipeline/production_steps.py`` line-for-line, not
a paraphrase.

Covers:
  * flag OFF (default): precomputed_garch=None, precomputed_forecast=None,
    precomputed_forecast_is_fallback=None is passed regardless of what
    dashboard_df carries for that row -- reproduces pre-PR-D behavior exactly.
  * flag ON: the row's GARCH_Vol / Forecast_30 values are threaded through
    verbatim; Forecast_30_Is_Fallback is threaded through ONLY when it's an
    actual bool (a NaN/missing cell degrades to None, never a fabricated
    True/False -- CONSTRAINT #4).
  * settings default: ADVISORY_REUSE_PIPELINE_COMPUTE is False out of the box.
"""

from __future__ import annotations

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Reproduction of pipeline/production_steps.py's _eval_one precompute-selection
# logic (mirrors the exact lines added around the `_advisory_evaluate(...)` call).
# ---------------------------------------------------------------------------

def _select_precomputed(_row: pd.Series, reuse_pipeline_compute: bool):
    """Byte-for-byte reproduction of the precompute-selection block inside
    pipeline/production_steps.py's `_eval_one` closure (the module this logic
    actually lives in today — see that file's ForecastingStep/advisory-overlay
    step for the real source; this test file's own module docstring predates
    that move and is kept as historical framing, not a live path claim).

    ``_precomputed_forecast_is_fallback`` (added alongside
    forecasting_engine.py's Forecast_30_Is_Fallback disclosure flag) is the
    third value: it only ever trusts an ACTUAL bool from the row -- the same
    cell can also be float('nan') (the row never reached ForecastingStep this
    cycle) or absent, neither of which discloses anything about fallback
    status."""
    _precomputed_garch = None
    _precomputed_forecast = None
    _precomputed_forecast_is_fallback = None
    if reuse_pipeline_compute:
        _precomputed_garch = _row.get('GARCH_Vol')
        _precomputed_forecast = _row.get('Forecast_30')
        _raw_pf_fallback = _row.get('Forecast_30_Is_Fallback')
        _precomputed_forecast_is_fallback = (
            _raw_pf_fallback if isinstance(_raw_pf_fallback, bool) else None
        )
    return _precomputed_garch, _precomputed_forecast, _precomputed_forecast_is_fallback


class TestPrecomputeSelectionWiring:
    def test_flag_off_always_passes_none_regardless_of_row_contents(self):
        row = pd.Series({'Symbol': 'AAPL', 'GARCH_Vol': 0.35, 'Forecast_30': 150.0})
        garch, forecast, is_fallback = _select_precomputed(row, reuse_pipeline_compute=False)
        assert garch is None
        assert forecast is None

    def test_flag_on_threads_row_values_verbatim(self):
        row = pd.Series({'Symbol': 'AAPL', 'GARCH_Vol': 0.35, 'Forecast_30': 150.0})
        garch, forecast, is_fallback = _select_precomputed(row, reuse_pipeline_compute=True)
        assert garch == pytest.approx(0.35)
        assert forecast == pytest.approx(150.0)

    def test_flag_on_missing_columns_degrades_to_none(self):
        """A row from a degraded cycle (e.g. GARCH fit failed upstream and
        the column was never populated for this ticker) must not raise --
        advisory.evaluate()'s own >0 guard is the actual safety net, but the
        wiring itself must tolerate a missing key too."""
        row = pd.Series({'Symbol': 'AAPL'})  # no GARCH_Vol / Forecast_30 keys
        garch, forecast, is_fallback = _select_precomputed(row, reuse_pipeline_compute=True)
        assert garch is None
        assert forecast is None

    def test_flag_on_zero_placeholder_passes_through_zero(self):
        """dashboard_df initializes GARCH_Vol=0.0 as a placeholder before the
        options loop fills it in; if a ticker's fit failed upstream the
        placeholder 0.0 survives to this row. The wiring passes it through
        as-is -- advisory.evaluate()'s `> 0` guard (not this selection logic)
        is what correctly rejects it and falls through to a fresh fit."""
        row = pd.Series({'Symbol': 'AAPL', 'GARCH_Vol': 0.0, 'Forecast_30': 0.0})
        garch, forecast, is_fallback = _select_precomputed(row, reuse_pipeline_compute=True)
        assert garch == 0.0
        assert forecast == 0.0

    def test_flag_on_threads_a_real_bool_fallback_flag(self):
        row = pd.Series({'Symbol': 'AAPL', 'GARCH_Vol': 0.35, 'Forecast_30': 150.0,
                          'Forecast_30_Is_Fallback': True})
        garch, forecast, is_fallback = _select_precomputed(row, reuse_pipeline_compute=True)
        assert is_fallback is True

    def test_flag_off_fallback_flag_stays_none_regardless_of_row_contents(self):
        row = pd.Series({'Symbol': 'AAPL', 'Forecast_30_Is_Fallback': True})
        garch, forecast, is_fallback = _select_precomputed(row, reuse_pipeline_compute=False)
        assert is_fallback is None

    def test_flag_on_nan_fallback_cell_degrades_to_none_not_true(self):
        """A NaN cell (row skipped ForecastingStep entirely this cycle) is
        truthy in plain Python -- `isinstance(..., bool)` is what keeps this
        from silently being read as `is_fallback=True`."""
        row = pd.Series({'Symbol': 'AAPL', 'GARCH_Vol': 0.35, 'Forecast_30': 150.0,
                          'Forecast_30_Is_Fallback': float('nan')})
        garch, forecast, is_fallback = _select_precomputed(row, reuse_pipeline_compute=True)
        assert is_fallback is None

    def test_flag_on_missing_fallback_column_degrades_to_none(self):
        row = pd.Series({'Symbol': 'AAPL', 'GARCH_Vol': 0.35, 'Forecast_30': 150.0})
        garch, forecast, is_fallback = _select_precomputed(row, reuse_pipeline_compute=True)
        assert is_fallback is None


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
        garch, forecast, is_fallback = _select_precomputed(row, reuse_pipeline_compute=True)

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
            toe_instance.estimate_gjr_garch_volatility_term_structure.return_value = {h: 0.19 for h in (1, 10, 30, 60, 90)}
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
                precomputed_forecast_is_fallback=is_fallback,
            )

            # The zero placeholders were rejected -- both engines still fit fresh.
            assert toe_instance.estimate_gjr_garch_volatility_term_structure.called
            assert fe_instance.generate_forecast.called
        assert rec.key_indicators["garch_vol"] == pytest.approx(0.19)
        assert rec.forecast == pytest.approx(108.0)


class TestSettingsDefault:
    def test_advisory_reuse_pipeline_compute_defaults_false(self):
        """Checked against the field's own coded default, not a freshly
        constructed ``Settings()`` -- the latter still reads a real,
        operator-populated ``.env`` (and any real os.environ this process's
        import chain may have picked up via another module's
        ``load_dotenv()``), so it is not a reliable stand-in for "the
        project default" on a machine with its own ``.env``."""
        from settings import Settings
        assert Settings.model_fields["ADVISORY_REUSE_PIPELINE_COMPUTE"].default is False
