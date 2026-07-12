"""No-lookahead perturbation tests for signals.pairs_trading.generate_pairs_signals.

The pairs signal was previously subtly non-causal: `compute_half_life` ran over
the ENTIRE spread series and the resulting half-life set the rolling z-score
window length applied across all of history, so the window at an early bar `t`
depended on data dated after `t`. The fix estimates the half-life from a causal
in-sample warmup prefix (`half_life_lookback` bars) instead.

These tests prove the whole pipeline (Kalman forward-filter hedge → spread →
half-life-derived window → rolling z-score → position state machine) is causal:
perturbing prices strictly AFTER a cutoff must not change any z-score or position
at or before that cutoff.

Per the repo convention (CONSTRAINT #7 — validation uses the same data library as
the other validation tests), the primary test pulls a real cointegrated-ish ETF
pair from Yahoo (yfinance); it skips cleanly when the network/library is
unavailable. A deterministic synthetic variant provides an always-on offline
guarantee.
"""
import numpy as np
import pandas as pd
import pytest

from signals.pairs_trading import generate_pairs_signals


def _assert_causal_up_to_cutoff(y: pd.Series, x: pd.Series, cutoff: int) -> None:
    """Perturb both price series strictly after `cutoff`; assert z_score and
    position at indices <= cutoff are unchanged (bit-for-bit, modulo NaN)."""
    base = generate_pairs_signals(y, x)

    y_pert = y.copy()
    x_pert = x.copy()
    # Inflate everything after the cutoff to an extreme value — a future that a
    # non-causal window-size estimate would "see".
    y_pert.iloc[cutoff + 1:] = y_pert.iloc[cutoff + 1:] * 5.0 + 1000.0
    x_pert.iloc[cutoff + 1:] = x_pert.iloc[cutoff + 1:] * 0.2 - 500.0
    pert = generate_pairs_signals(y_pert, x_pert)

    for col in ("z_score", "position"):
        a = base[col].iloc[: cutoff + 1].to_numpy()
        b = pert[col].iloc[: cutoff + 1].to_numpy()
        # NaNs must line up; finite values must match exactly.
        assert np.array_equal(np.isnan(a), np.isnan(b)), f"{col}: NaN mask diverged before cutoff"
        mask = ~np.isnan(a)
        np.testing.assert_allclose(
            a[mask], b[mask], rtol=0, atol=0,
            err_msg=f"{col} changed at/before cutoff after perturbing future data (lookahead!)",
        )


def _download_yahoo_pair():
    """Fetch a real, historically-correlated ETF pair from Yahoo (EWA/EWC —
    the canonical Australia/Canada resource-economy cointegration example).
    Returns (y, x) aligned Close series, or skips if unavailable."""
    yf = pytest.importorskip("yfinance")
    try:
        data = yf.download(
            ["EWA", "EWC"], start="2016-01-01", end="2021-01-01",
            progress=False, auto_adjust=True,
        )
    except Exception as exc:  # pragma: no cover - network dependent
        pytest.skip(f"yfinance download failed: {exc}")

    if data is None or data.empty or "Close" not in data:
        pytest.skip("yfinance returned no data for EWA/EWC")

    close = data["Close"].dropna()
    if not {"EWA", "EWC"}.issubset(close.columns) or len(close) < 200:
        pytest.skip("insufficient EWA/EWC history from Yahoo")

    # tz-naive index, aligned
    close.index = pd.DatetimeIndex(close.index).tz_localize(None)
    return close["EWA"], close["EWC"]


def test_pairs_no_lookahead_yahoo_data():
    """PRIMARY: real Yahoo (yfinance) EWA/EWC pair — perturbing the last third
    of history must not change any signal in the first two-thirds."""
    y, x = _download_yahoo_pair()
    cutoff = int(len(y) * 0.66)
    _assert_causal_up_to_cutoff(y, x, cutoff)


def test_pairs_no_lookahead_synthetic_offline():
    """Always-on offline guarantee on a synthetic cointegrated pair."""
    rng = np.random.default_rng(7)
    n = 400
    dates = pd.date_range("2019-01-01", periods=n, freq="B")
    x = np.cumsum(rng.normal(0, 0.5, n)) + 100.0
    spread = np.zeros(n)
    for i in range(1, n):
        spread[i] = 0.9 * spread[i - 1] + rng.normal(0, 0.1)
    y = 0.8 * x + 5.0 + spread
    y_s = pd.Series(y, index=dates)
    x_s = pd.Series(x, index=dates)

    # Cutoff comfortably past the default half_life_lookback (63) warmup prefix.
    _assert_causal_up_to_cutoff(y_s, x_s, cutoff=250)


def test_half_life_lookback_prefix_only_uses_warmup():
    """The half-life window is derived from the first `half_life_lookback` bars,
    so changing data only AFTER that prefix leaves zscore_window (hence the whole
    pre-cutoff signal) unchanged — a direct check of the fix's mechanism."""
    rng = np.random.default_rng(11)
    n = 300
    dates = pd.date_range("2019-01-01", periods=n, freq="B")
    x = np.cumsum(rng.normal(0, 0.5, n)) + 100.0
    spread = np.zeros(n)
    for i in range(1, n):
        spread[i] = 0.85 * spread[i - 1] + rng.normal(0, 0.1)
    y = 0.7 * x + 3.0 + spread
    y_s = pd.Series(y, index=dates)
    x_s = pd.Series(x, index=dates)

    # Perturb everything after the warmup prefix; the pre-cutoff signal is causal.
    _assert_causal_up_to_cutoff(y_s, x_s, cutoff=63)
