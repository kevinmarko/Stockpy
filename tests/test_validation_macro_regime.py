"""
tests/test_validation_macro_regime.py

Tests for scripts.refresh_validations's macro_regime_pit adapter -- the
honest backtest for the Regime Navigator Pilot (pilots/catalog.py).

Fast, fully-offline unit tests (default suite) cover:
  * _asof_align's point-in-time alignment semantics
  * _reconstruct_macro_regime_series reusing the REAL dto_models.MacroEconomicDTO
    (not a re-implementation), including the documented v1 caveats (no HMM
    downgrade replay, degrade-to-None on missing series)
  * _build_macro_regime_adapter's score formula fidelity against
    signals/macro_regime.py's exact point scale, the two book-construction
    variants, and no-lookahead (.shift(1)) discipline

One @pytest.mark.network + FRED-key-gated integration test exercises the
adapter against real persisted FRED history end-to-end through
StrategyValidationHarness -- asserting only that the report is well-formed,
NEVER that deployable is True (CONSTRAINT #4: an honest proxy backtest is
not expected to clear the deployability bar on its own).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from dto_models import MacroEconomicDTO
from settings import settings
from scripts.refresh_validations import (
    _asof_align,
    _build_macro_regime_adapter,
    _reconstruct_macro_regime_series,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _synthetic_macro_series(n_days: int = 1000, start: str = "2015-01-01"):
    idx = pd.bdate_range(start=start, periods=n_days)
    rng = np.random.RandomState(11)
    vix = pd.Series(15.0 + rng.normal(0, 3, n_days).cumsum() * 0.02, index=idx).clip(lower=9.0)
    t10y2y = pd.Series(0.5 + rng.normal(0, 0.03, n_days).cumsum() * 0.01, index=idx)
    oas = pd.Series(3.0 + rng.normal(0, 0.2, n_days).cumsum() * 0.02, index=idx).clip(lower=1.0)
    monthly_idx = pd.date_range(
        start=pd.Timestamp(start) - pd.DateOffset(years=1), end=idx[-1], freq="MS"
    )
    unrate = pd.Series(
        5.0 + rng.normal(0, 0.1, len(monthly_idx)).cumsum() * 0.05, index=monthly_idx
    ).clip(lower=3.0)
    return idx, vix, t10y2y, oas, unrate


def _synthetic_closes(tickers, idx, seed: int = 3) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    data = {}
    for t in tickers:
        rets = rng.normal(0.0003, 0.01, len(idx))
        data[t] = 100.0 * np.cumprod(1 + rets)
    return pd.DataFrame(data, index=idx)


# ---------------------------------------------------------------------------
# TestAsofAlign
# ---------------------------------------------------------------------------

class TestAsofAlign:
    def test_empty_series_degrades_to_all_nan(self) -> None:
        common_index = pd.bdate_range("2020-01-01", periods=10)
        result = _asof_align(pd.Series(dtype=float), common_index)
        assert len(result) == 10
        assert result.isna().all()

    def test_backward_fill_carries_last_known_value(self) -> None:
        common_index = pd.bdate_range("2020-01-01", periods=10)
        # Sparse series: one value on day 0, another on day 5.
        sparse = pd.Series(
            [1.0, 2.0], index=[common_index[0], common_index[5]]
        )
        result = _asof_align(sparse, common_index)
        assert result.iloc[0] == 1.0
        assert result.iloc[3] == 1.0  # carried forward, no future leakage
        assert result.iloc[5] == 2.0
        assert result.iloc[9] == 2.0

    def test_never_uses_future_value(self) -> None:
        """A date strictly before the series' first timestamp must be NaN,
        never backward-filled from a future value (CONSTRAINT #4)."""
        common_index = pd.bdate_range("2020-01-01", periods=5)
        late_series = pd.Series([9.0], index=[common_index[3]])
        result = _asof_align(late_series, common_index)
        assert result.iloc[0:3].isna().all()
        assert result.iloc[3] == 9.0
        assert result.iloc[4] == 9.0


# ---------------------------------------------------------------------------
# TestReconstructMacroRegimeSeries
# ---------------------------------------------------------------------------

class TestReconstructMacroRegimeSeries:
    def test_reuses_real_macro_economic_dto(self) -> None:
        """The reconstructed regime at a date must match calling the REAL
        MacroEconomicDTO directly with the same inputs -- proving this is a
        genuine reuse, not a parallel re-implementation."""
        idx, vix, t10y2y, oas, unrate = _synthetic_macro_series(n_days=600)
        regime_df = _reconstruct_macro_regime_series(idx, vix, t10y2y, oas, unrate)

        # Pick a date safely past the Sahm warm-up window.
        probe_date = idx[500]
        row = regime_df.loc[probe_date]
        if row["market_regime"] is None:
            pytest.skip("probe date fell in the warm-up window for this seed")

        aligned_yc = _asof_align(t10y2y, idx).loc[probe_date]
        aligned_oas = _asof_align(oas, idx).loc[probe_date]
        ma3 = unrate.sort_index().rolling(3).mean()
        sahm = ma3 - ma3.rolling(12).min()
        aligned_sahm = _asof_align(sahm, idx).loc[probe_date]
        aligned_vix = _asof_align(vix, idx).loc[probe_date]

        expected = MacroEconomicDTO(
            yield_curve_10y_2y=float(aligned_yc),
            high_yield_oas=float(aligned_oas),
            inflation_rate=2.0,
            sahm_rule_indicator=float(aligned_sahm),
            vix_value=float(aligned_vix),
            hmm_risk_on_probability=None,
        )
        assert row["market_regime"] == expected.market_regime
        assert row["kill_switch"] == expected.killSwitch

    def test_missing_series_degrades_to_none_not_fabricated(self) -> None:
        idx = pd.bdate_range("2020-01-01", periods=20)
        empty = pd.Series(dtype=float)
        regime_df = _reconstruct_macro_regime_series(idx, empty, empty, empty, empty)
        assert regime_df["market_regime"].isna().all()
        assert (regime_df["kill_switch"] == False).all()  # noqa: E712

    def test_recession_classification_from_known_inputs(self) -> None:
        """Deep yield-curve inversion + wide credit spread over the full
        window -> every date classifies RECESSION (mirrors
        dto_models.MacroEconomicDTO._rules_based_regime's own unit tests)."""
        idx = pd.bdate_range("2020-01-01", periods=30)
        vix = pd.Series(20.0, index=idx)
        t10y2y = pd.Series(-1.0, index=idx)
        oas = pd.Series(7.0, index=idx)
        # UNRATE flat -> Sahm ~= 0, so RECESSION here is driven by yc+oas, not Sahm.
        monthly_idx = pd.date_range("2018-01-01", "2020-02-01", freq="MS")
        unrate = pd.Series(4.0, index=monthly_idx)

        regime_df = _reconstruct_macro_regime_series(idx, vix, t10y2y, oas, unrate)
        known = regime_df["market_regime"].dropna()
        assert len(known) > 0
        assert (known == "RECESSION").all()

    def test_hmm_probability_never_passed(self) -> None:
        """v1 scope: hmm_risk_on_probability is always None, so a RISK ON
        classification is never downgraded to NEUTRAL by this adapter."""
        idx = pd.bdate_range("2020-01-01", periods=30)
        vix = pd.Series(12.0, index=idx)
        t10y2y = pd.Series(1.5, index=idx)  # steep, healthy curve
        oas = pd.Series(2.0, index=idx)  # tight spread
        monthly_idx = pd.date_range("2018-01-01", "2020-02-01", freq="MS")
        unrate = pd.Series(4.0, index=monthly_idx)

        regime_df = _reconstruct_macro_regime_series(idx, vix, t10y2y, oas, unrate)
        known = regime_df["market_regime"].dropna()
        assert len(known) > 0
        assert (known == "RISK ON").all()  # never downgraded to NEUTRAL


# ---------------------------------------------------------------------------
# TestBuildMacroRegimeAdapter
# ---------------------------------------------------------------------------

def _patched_adapter_call(closes, vix, t10y2y, oas, unrate, sectors):
    mock_store = MagicMock()
    mock_store.get_macro.side_effect = lambda series_id, **kw: {
        "VIXCLS": vix, "T10Y2Y": t10y2y, "BAMLH0A0HYM2": oas, "UNRATE": unrate,
    }[series_id]
    with patch("data.historical_store.HistoricalStore", return_value=mock_store):
        with patch("scripts.refresh_validations._load_ticker_sectors", return_value=sectors):
            return _build_macro_regime_adapter(closes)


class TestBuildMacroRegimeAdapter:
    _TICKERS = ["AAPL", "JNJ", "XOM", "JPM"]
    _SECTORS = {
        "AAPL": "Technology",
        "JNJ": "Healthcare",
        "XOM": "Energy",
        "JPM": "Financial Services",
    }

    def test_returns_three_items_and_variants(self) -> None:
        idx, vix, t10y2y, oas, unrate = _synthetic_macro_series(n_days=800)
        closes = _synthetic_closes(self._TICKERS, idx)

        X, y, pre = _patched_adapter_call(closes, vix, t10y2y, oas, unrate, self._SECTORS)

        assert not X.empty and not y.empty
        assert "MacroRegime_Composite" in X.columns
        assert set(pre.keys()) == {"MacroRegime_TopHalf", "MacroRegime_SectorRotation"}
        for k, v in pre.items():
            assert v.index.equals(y.index), f"{k} index mismatch"

    def test_sector_rotation_flat_outside_stress_regimes(self) -> None:
        """RISK ON the entire window -> SectorRotation book must be all zero
        (it only takes positions within RECESSION/CREDIT EVENT)."""
        idx = pd.bdate_range("2015-01-01", periods=500)
        vix = pd.Series(12.0, index=idx)
        t10y2y = pd.Series(1.5, index=idx)
        oas = pd.Series(2.0, index=idx)
        monthly_idx = pd.date_range("2014-01-01", idx[-1], freq="MS")
        unrate = pd.Series(4.0, index=monthly_idx)
        closes = _synthetic_closes(self._TICKERS, idx)

        _, _, pre = _patched_adapter_call(closes, vix, t10y2y, oas, unrate, self._SECTORS)
        assert (pre["MacroRegime_SectorRotation"] == 0.0).all()

    def test_sector_rotation_active_during_credit_event(self) -> None:
        idx = pd.bdate_range("2015-01-01", periods=500)
        vix = pd.Series(12.0, index=idx)
        t10y2y = pd.Series(1.5, index=idx)
        oas = pd.Series(7.0, index=idx)  # wide spread -> CREDIT EVENT throughout
        monthly_idx = pd.date_range("2014-01-01", idx[-1], freq="MS")
        unrate = pd.Series(4.0, index=monthly_idx)
        closes = _synthetic_closes(self._TICKERS, idx)

        _, _, pre = _patched_adapter_call(closes, vix, t10y2y, oas, unrate, self._SECTORS)
        # Some (not necessarily all, due to the .shift(1) lag) days must be nonzero.
        assert (pre["MacroRegime_SectorRotation"] != 0.0).sum() > 0

    def test_consumer_defensive_sector_gets_no_bonus_fidelity_note(self) -> None:
        """FIDELITY NOTE: yfinance's real sector taxonomy is 'Consumer
        Defensive', not 'Consumer Staples' -- the live signal's substring
        check for 'Consumer Staples' never actually matches. This backtest
        must reproduce that exact behavior, not silently correct it."""
        idx = pd.bdate_range("2015-01-01", periods=500)
        vix = pd.Series(12.0, index=idx)
        t10y2y = pd.Series(1.5, index=idx)
        oas = pd.Series(7.0, index=idx)  # CREDIT EVENT throughout
        monthly_idx = pd.date_range("2014-01-01", idx[-1], freq="MS")
        unrate = pd.Series(4.0, index=monthly_idx)
        closes = _synthetic_closes(["KO"], idx)

        _, _, pre = _patched_adapter_call(
            closes, vix, t10y2y, oas, unrate, {"KO": "Consumer Defensive"}
        )
        # KO is the only ticker -> if it got the defensive bonus it would be
        # the sole long leg; since "Consumer Defensive" never matches
        # "Consumer Staples", defensive_tickers is empty -> rotation is flat.
        assert (pre["MacroRegime_SectorRotation"] == 0.0).all()

    def test_healthcare_sector_gets_defensive_bonus(self) -> None:
        idx = pd.bdate_range("2015-01-01", periods=500)
        vix = pd.Series(12.0, index=idx)
        t10y2y = pd.Series(1.5, index=idx)
        oas = pd.Series(7.0, index=idx)
        monthly_idx = pd.date_range("2014-01-01", idx[-1], freq="MS")
        unrate = pd.Series(4.0, index=monthly_idx)
        closes = _synthetic_closes(["JNJ"], idx)

        _, _, pre = _patched_adapter_call(
            closes, vix, t10y2y, oas, unrate, {"JNJ": "Healthcare"}
        )
        assert (pre["MacroRegime_SectorRotation"] != 0.0).sum() > 0

    def test_no_lookahead_shift1(self) -> None:
        idx, vix, t10y2y, oas, unrate = _synthetic_macro_series(n_days=700)
        closes = _synthetic_closes(self._TICKERS, idx)
        cutoff = idx[600]

        _, _, pre_orig = _patched_adapter_call(closes, vix, t10y2y, oas, unrate, self._SECTORS)
        val_orig = pre_orig["MacroRegime_TopHalf"].loc[cutoff]

        perturbed = closes.copy()
        perturbed.loc[perturbed.index > cutoff] *= 5.0
        _, _, pre_pert = _patched_adapter_call(
            perturbed, vix, t10y2y, oas, unrate, self._SECTORS
        )
        assert val_orig == pytest.approx(pre_pert["MacroRegime_TopHalf"].loc[cutoff])

    def test_unknown_regime_dates_excluded_never_fabricated(self) -> None:
        """A date range where every macro input is missing (e.g. before the
        earliest persisted history) must contribute zero score/exposure, not
        a fabricated neutral guess."""
        idx = pd.bdate_range("2015-01-01", periods=50)
        empty = pd.Series(dtype=float)
        closes = _synthetic_closes(self._TICKERS, idx)

        X, y, pre = _patched_adapter_call(closes, empty, empty, empty, empty, self._SECTORS)
        assert X["MacroRegime_Composite"].fillna(0.0).eq(0.0).all()
        for v in pre.values():
            assert (v == 0.0).all()


# ---------------------------------------------------------------------------
# TestMacroRegimePitIntegration -- real FRED + real harness (opt-in)
# ---------------------------------------------------------------------------

class TestMacroRegimePitIntegration:
    pytestmark = [
        pytest.mark.network,
        pytest.mark.skipif(not settings.FRED_API_KEY, reason="requires FRED_API_KEY"),
    ]

    def test_macro_regime_pit_runs_end_to_end(self, tmp_path) -> None:
        """Real FRED history + real yfinance prices through the real harness.
        Only asserts the report is well-formed -- NEVER that deployable is
        True (CONSTRAINT #4: an honest proxy backtest earns its own gate)."""
        from execution.cost_model import TieredCostModel
        from validation.harness import StrategyValidationHarness
        from scripts.refresh_validations import _download_closes, _make_strategy_fn

        tickers = ["AAPL", "JNJ", "XOM", "JPM", "KO"]
        closes = _download_closes(tickers, "2015-01-01", "2023-12-31")
        assert len(closes) > 300

        with patch("data.historical_store.HistoricalStore") as mock_cls:
            from data.historical_store import HistoricalStore as _RealStore

            real_store = _RealStore(db_path=str(tmp_path / "macro_pit.db"))
            mock_cls.return_value = real_store
            X, y, precomputed = _build_macro_regime_adapter(closes)

        assert not X.empty and not y.empty
        assert precomputed

        strategy_fn = _make_strategy_fn(precomputed, turnover=0.03)
        harness = StrategyValidationHarness(
            strategy_fn=strategy_fn,
            universe_fn=lambda: tickers,
            cost_model=TieredCostModel(),
        )
        report = harness.run(
            start_date="2015-01-01", end_date="2023-12-31",
            X=X, y=y, strategy_name="macro_regime_pit",
        )
        summary = report.to_summary_dict()
        assert isinstance(summary["deployable"], bool)
        assert np.isfinite(summary["sharpe"])
        assert np.isfinite(summary["max_drawdown"])
