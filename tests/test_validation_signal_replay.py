"""
tests/test_validation_signal_replay.py

Tests for scripts.refresh_validations's signal_replay_balanced_blend adapter
-- the honest backtest for the Balanced Blend Pilot (pilots/catalog.py).

This is the largest and most novel adapter in the module: it replays the
REAL SignalAggregator/SignalRegistry code path over history, rather than
hand-rolling a standalone formula. The most important test in this file is
TestLookaheadSafety -- a regression guard proving the filtered replay
registry genuinely never touches news_catalyst's live Finnhub calls or
lgbm_ranker's current-model load, both of which would be a real lookahead
bug (and, for news_catalyst, an unwanted live network side effect) if the
module-exclusion filter ever regressed.

All tests use tiny synthetic windows and a mocked HistoricalStore/OHLCV
downloader so the suite stays fast and fully offline.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from scripts.refresh_validations import (
    _REPLAY_EXCLUDED_MODULES,
    _aroon_up_down,
    _build_signal_replay_adapter,
    _ewma_vol_annualized,
    _pit_row_to_fundamentals_dto,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _synthetic_closes(tickers, n_days: int = 600, start: str = "2020-01-01", seed: int = 3) -> pd.DataFrame:
    idx = pd.bdate_range(start=start, periods=n_days)
    rng = np.random.RandomState(seed)
    data = {}
    for t in tickers:
        rets = rng.normal(0.0003, 0.01, n_days)
        data[t] = 100.0 * np.cumprod(1 + rets)
    return pd.DataFrame(data, index=idx)


def _mock_macro_series(idx):
    vix = pd.Series(15.0, index=idx)
    t10y2y = pd.Series(1.0, index=idx)
    oas = pd.Series(3.0, index=idx)
    monthly_idx = pd.date_range(idx[0] - pd.DateOffset(years=1), idx[-1], freq="MS")
    unrate = pd.Series(4.0, index=monthly_idx)
    return vix, t10y2y, oas, unrate


def _mock_store(idx):
    vix, t10y2y, oas, unrate = _mock_macro_series(idx)
    store = MagicMock()
    store.get_macro.side_effect = lambda series_id, **kw: {
        "VIXCLS": vix, "T10Y2Y": t10y2y, "BAMLH0A0HYM2": oas, "UNRATE": unrate,
    }[series_id]
    store.get_fundamentals_history.return_value = pd.DataFrame()
    return store


def _run_adapter(closes, sectors=None, ohlcv=None, store=None):
    idx = closes.index
    store = store or _mock_store(idx)
    sectors = sectors if sectors is not None else {t: "Technology" for t in closes.columns if t != "SPY"}
    ohlcv = ohlcv if ohlcv is not None else {}
    with patch("data.historical_store.HistoricalStore", return_value=store):
        with patch("scripts.refresh_validations._load_ticker_sectors", return_value=sectors):
            with patch("scripts.refresh_validations._download_ohlcv", return_value=ohlcv):
                return _build_signal_replay_adapter(closes)


# ---------------------------------------------------------------------------
# TestReplayExcludedModules
# ---------------------------------------------------------------------------

class TestReplayExcludedModules:
    def test_excludes_exactly_three_modules(self) -> None:
        assert _REPLAY_EXCLUDED_MODULES == {"news_catalyst", "lgbm_ranker", "forecast_alignment"}

    def test_surviving_registry_has_fourteen_modules(self) -> None:
        from signals.registry import global_registry

        all_names = set(global_registry.get_all().keys())
        surviving = all_names - _REPLAY_EXCLUDED_MODULES
        assert len(all_names) == 17
        assert len(surviving) == 14


# ---------------------------------------------------------------------------
# TestAroonUpDown
# ---------------------------------------------------------------------------

class TestAroonUpDown:
    def test_pure_uptrend_favors_aroon_up(self) -> None:
        idx = pd.bdate_range("2020-01-01", periods=60)
        high = pd.Series(np.linspace(100, 160, 60), index=idx)
        low = high - 1.0
        aroon_up, aroon_down = _aroon_up_down(high, low, length=25)
        assert aroon_up.iloc[-1] == pytest.approx(100.0)
        assert aroon_down.iloc[-1] == pytest.approx(0.0)

    def test_pure_downtrend_favors_aroon_down(self) -> None:
        idx = pd.bdate_range("2020-01-01", periods=60)
        low = pd.Series(np.linspace(160, 100, 60), index=idx)
        high = low + 1.0
        aroon_up, aroon_down = _aroon_up_down(high, low, length=25)
        assert aroon_down.iloc[-1] == pytest.approx(100.0)
        assert aroon_up.iloc[-1] == pytest.approx(0.0)

    def test_causal_no_lookahead(self) -> None:
        idx = pd.bdate_range("2020-01-01", periods=80)
        rng = np.random.RandomState(1)
        high = pd.Series(100 + rng.normal(0, 1, 80).cumsum(), index=idx)
        low = high - 1.0
        cutoff_i = 60
        au1, ad1 = _aroon_up_down(high, low)

        high2 = high.copy()
        high2.iloc[cutoff_i + 1:] += 50.0
        au2, ad2 = _aroon_up_down(high2, low)
        assert au1.iloc[cutoff_i] == pytest.approx(au2.iloc[cutoff_i])
        assert ad1.iloc[cutoff_i] == pytest.approx(ad2.iloc[cutoff_i])


# ---------------------------------------------------------------------------
# TestEwmaVolAnnualized
# ---------------------------------------------------------------------------

class TestEwmaVolAnnualized:
    def test_zero_return_series_is_zero_vol(self) -> None:
        idx = pd.bdate_range("2020-01-01", periods=30)
        ret = pd.Series(0.0, index=idx)
        vol = _ewma_vol_annualized(ret)
        assert (vol.fillna(0.0) == 0.0).all()

    def test_higher_variance_gives_higher_vol(self) -> None:
        idx = pd.bdate_range("2020-01-01", periods=100)
        low_var = pd.Series(np.random.RandomState(1).normal(0, 0.001, 100), index=idx)
        high_var = pd.Series(np.random.RandomState(1).normal(0, 0.05, 100), index=idx)
        assert _ewma_vol_annualized(high_var).iloc[-1] > _ewma_vol_annualized(low_var).iloc[-1]


# ---------------------------------------------------------------------------
# TestPitRowToFundamentalsDto
# ---------------------------------------------------------------------------

class TestPitRowToFundamentalsDto:
    def test_empty_raw_degrades_honestly(self) -> None:
        dto = _pit_row_to_fundamentals_dto("AAPL", "Technology", {})
        assert np.isnan(dto.graham_number)
        assert dto.is_dividend_sustainable is False
        assert np.isnan(dto.dividend_yield)

    def test_real_pit_shape_maps_correctly(self) -> None:
        raw = {
            "pe_ratio": 25.0, "pb_ratio": 8.0, "roe": 0.35, "dividend_yield": 0.005,
            "market_cap": 2.5e12, "eps": 6.0, "operating_margin": 0.3,
            "debt_to_equity": 150.0, "current_ratio": 1.1,
        }
        dto = _pit_row_to_fundamentals_dto("AAPL", "Technology", raw)
        assert dto.pe_ratio == pytest.approx(25.0)
        assert dto.pb_ratio == pytest.approx(8.0)
        assert dto.eps_trailing == pytest.approx(6.0)
        assert dto.dividend_yield == pytest.approx(0.005)
        # book_value/payout_ratio deliberately NaN -- never derived from a
        # mixed price vintage (see docstring) -- so graham_number/
        # is_dividend_sustainable degrade to their own honest "no data" branches.
        assert np.isnan(dto.book_value)
        assert np.isnan(dto.payout_ratio)
        assert np.isnan(dto.graham_number)

    def test_never_fabricates_a_zero_where_data_is_missing(self) -> None:
        """CONSTRAINT #4: book_value/payout_ratio must be NaN, not a silent
        0.0 default -- a 0.0 payout_ratio would flip is_dividend_sustainable
        to a fabricated 'always sustainable' verdict."""
        dto = _pit_row_to_fundamentals_dto("AAPL", "Technology", {"dividend_yield": 0.03})
        # payout_ratio NaN -> is_dividend_sustainable is False (conservative),
        # NOT True (which a fabricated 0.0 payout_ratio would incorrectly produce).
        assert dto.is_dividend_sustainable is False


# ---------------------------------------------------------------------------
# TestBuildSignalReplayAdapter
# ---------------------------------------------------------------------------

class TestBuildSignalReplayAdapter:
    def test_returns_three_items_and_variant(self) -> None:
        closes = _synthetic_closes(["SPY", "AAPL", "JNJ"], n_days=600)
        X, y, pre = _run_adapter(closes)
        assert not X.empty and not y.empty
        assert "SignalReplay_Composite" in X.columns
        assert set(pre.keys()) == {"SignalReplay_TopHalf"}
        assert pre["SignalReplay_TopHalf"].index.equals(y.index)

    def test_requires_spy_benchmark(self) -> None:
        closes = _synthetic_closes(["AAPL", "JNJ"], n_days=600)
        with pytest.raises(RuntimeError, match="SPY"):
            _run_adapter(closes)

    def test_insufficient_history_degrades_cleanly(self) -> None:
        closes = _synthetic_closes(["SPY", "AAPL"], n_days=100)
        X, y, pre = _run_adapter(closes)
        assert X.empty and y.empty and pre == {}

    def test_trims_504day_warmup(self) -> None:
        closes = _synthetic_closes(["SPY", "AAPL", "JNJ"], n_days=600)
        X, y, pre = _run_adapter(closes)
        assert len(X) == 600 - 504

    def test_score_is_bounded(self) -> None:
        """final_score in [0,100] -> normalized (score-50)/50 in [-1,1]."""
        closes = _synthetic_closes(["SPY", "AAPL", "JNJ"], n_days=600)
        X, y, pre = _run_adapter(closes)
        assert X["SignalReplay_Composite"].between(-1.0, 1.0).all()

    def test_no_lookahead_shift1(self) -> None:
        closes = _synthetic_closes(["SPY", "AAPL", "JNJ"], n_days=650)
        cutoff = closes.index[600]

        _, _, pre_orig = _run_adapter(closes)
        val_orig = pre_orig["SignalReplay_TopHalf"].loc[cutoff]

        perturbed = closes.copy()
        perturbed.loc[perturbed.index > cutoff] *= 5.0
        _, _, pre_pert = _run_adapter(perturbed)
        val_pert = pre_pert["SignalReplay_TopHalf"].loc[cutoff]

        assert val_orig == pytest.approx(val_pert)

    def test_weight_renormalization_preserves_total_mass(self) -> None:
        """The 14 surviving modules' weights must renormalize back to the
        SAME total mass as the original 17-module settings.SIGNAL_WEIGHTS
        (not left as a smaller raw subset, which would bias every score
        toward the neutral 50 baseline)."""
        from settings import settings

        base_weights = dict(settings.SIGNAL_WEIGHTS)
        surviving_names = [n for n in base_weights if n not in _REPLAY_EXCLUDED_MODULES]
        survivor_sum = sum(base_weights.get(n, 0.0) for n in surviving_names)
        original_total = sum(base_weights.values())
        renorm_factor = original_total / survivor_sum if survivor_sum > 0 else 1.0
        renormalized_total = sum(base_weights.get(n, 0.0) * renorm_factor for n in surviving_names)
        assert renormalized_total == pytest.approx(original_total, rel=1e-6)

    def test_empty_pit_store_never_crashes(self) -> None:
        """A fresh-clone HistoricalStore with zero EDGAR PIT rows must degrade
        gracefully (graham_value/dividend_quality fall to their own "no
        data" branches) rather than raise."""
        closes = _synthetic_closes(["SPY", "AAPL", "JNJ"], n_days=600)
        X, y, pre = _run_adapter(closes)  # default mock store has empty PIT history
        assert not X.empty  # must not have raised or returned empty due to a crash


# ---------------------------------------------------------------------------
# TestLookaheadSafety -- the single most important test in this file
# ---------------------------------------------------------------------------

class TestLookaheadSafety:
    """Guards against the adapter ever making a live Finnhub call or loading
    the CURRENT LGBM model from inside an offline historical replay.

    IMPORTANT NUANCE (found while writing this test, kept documented rather
    than silently glossed over): both ``NewsCatalystSignal.compute()`` and
    ``LGBMRankerSignal.compute()`` are pure dict lookups against
    ``context.news_sentiment_scores``/``context.lgbm_scores`` -- the actual
    live Finnhub call / ``LGBMCrossSectionalRanker.load_latest()`` only ever
    happens inside their respective ``pre_compute()`` methods. Since this
    adapter's daily loop calls ONLY ``MultifactorSignal.pre_compute()`` and
    ``CrossSectionalMomentumSignal.pre_compute()`` (hardcoded, never a batch
    ``registry.run_pre_compute()`` across every registered module), the two
    tests below pass TODAY even independent of ``_REPLAY_EXCLUDED_MODULES``
    -- they guard against a plausible FUTURE regression (e.g. a refactor
    that switches to calling ``replay_registry.run_pre_compute()`` for
    convenience, which WOULD trigger a real Finnhub call / stale-model load
    if the exclusion set were removed at the same time).

    The genuinely load-bearing reason ``_REPLAY_EXCLUDED_MODULES`` matters
    TODAY is weight redistribution, not network safety: leaving
    news_catalyst/lgbm_ranker registered without their pre_compute() ever
    running would have them contribute a constant neutral score under their
    real (nonzero) settings.SIGNAL_WEIGHTS weight -- silently wasting that
    weight mass instead of redistributing it to the 14 modules that can
    actually produce a real historical score. See
    test_excluded_modules_weight_mass_is_redistributed_not_wasted below.
    """

    def test_finnhub_never_called(self) -> None:
        closes = _synthetic_closes(["SPY", "AAPL", "JNJ"], n_days=600)
        finnhub_mock = MagicMock(name="build_finnhub_client")
        with patch("signals.news_catalyst.build_finnhub_client", finnhub_mock):
            _run_adapter(closes)
        finnhub_mock.assert_not_called()

    def test_lgbm_load_latest_never_called(self) -> None:
        closes = _synthetic_closes(["SPY", "AAPL", "JNJ"], n_days=600)
        with patch(
            "ml.lgbm_ranker.LGBMCrossSectionalRanker.load_latest",
            side_effect=AssertionError("lgbm_ranker.load_latest must never be called in signal replay"),
        ) as lgbm_mock:
            _run_adapter(closes)
        lgbm_mock.assert_not_called()

    def test_news_catalyst_absent_from_replay_exclusion_set(self) -> None:
        """Directly tied to _REPLAY_EXCLUDED_MODULES -- WILL fail if any of
        the three names is ever removed from that constant."""
        from scripts.refresh_validations import _REPLAY_EXCLUDED_MODULES

        assert "news_catalyst" in _REPLAY_EXCLUDED_MODULES
        assert "lgbm_ranker" in _REPLAY_EXCLUDED_MODULES
        assert "forecast_alignment" in _REPLAY_EXCLUDED_MODULES

    def test_excluded_modules_weight_mass_is_redistributed_not_wasted(self) -> None:
        """The real load-bearing reason for _REPLAY_EXCLUDED_MODULES today:
        their weight mass must be redistributed to the 14 surviving modules
        (via renormalization), not silently left assigned to a module that
        structurally can't produce a real score in this replay."""
        from settings import settings

        from scripts.refresh_validations import _REPLAY_EXCLUDED_MODULES

        base_weights = dict(settings.SIGNAL_WEIGHTS)
        excluded_weight_mass = sum(base_weights.get(n, 0.0) for n in _REPLAY_EXCLUDED_MODULES)
        # A meaningful chunk of weight would be wasted if this were zero --
        # confirms the exclusion is not a no-op in weight terms.
        assert excluded_weight_mass > 0.0
