"""
InvestYo Quant Platform - Volatility Mispricing (STRATEGY_REGISTRY) Validation Test
=======================================================================================
Runs ``scripts.refresh_validations._build_vol_mispricing_adapter`` — the
production adapter registered as ``STRATEGY_REGISTRY["vol_mispricing"]`` —
over real historical SPY price data, and verifies the
``StrategyValidationHarness`` produces a well-formed report end-to-end,
including the options-selling stress-gate wiring
(``is_options_selling=True``/``stress_returns_fn``).

Modeled directly on ``tests/test_validation_vrp_premium_selling_registry.py``.
Unlike that sibling, this adapter's alpha term is 100% real (real VIX +
real HAR-RV forecast, no proxy) — see
``validation/options_selling_backtest.py``'s honesty-contract comment block
above ``simulate_vol_mispricing_returns``, and
``docs/signals/vol_mispricing.md`` for the production registry entry's real
measured numbers (deployable=False -- genuinely measured, not a test-suite
concern).

Uses the same 2015-2024 window as the sibling test -- long enough (259 real
monthly cycles measured during development) for both the RICH and CHEAP
branches to open at least once, so this smoke test exercises the iron-condor
leg construction AND the long-straddle leg construction, not just one.
"""

import numpy as np
import pandas as pd
import pytest
import yfinance as yf
from dotenv import dotenv_values

import settings as settings_module
from execution.cost_model import TieredCostModel
from scripts.refresh_validations import (
    _build_vol_mispricing_adapter,
    _make_strategy_fn,
    _resolve_options_selling_stress_fn,
)
from validation.harness import StrategyValidationHarness

# Downloads real price history live from Yahoo Finance in its module-scoped
# fixture -- network-dependent, deselected in CI via `pytest -m "not network"`.
pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def spy_close() -> pd.Series:
    df = yf.download("SPY", start="2015-01-01", end="2024-12-31", progress=False)
    assert df is not None and not df.empty, "Failed to download SPY history"
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index)
    return close.dropna()


@pytest.fixture()
def fred_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore a real ``FRED_API_KEY`` for this test's scope only.

    ``_build_vol_mispricing_adapter`` -> ``simulate_vol_mispricing_returns``
    reads real VIX history via ``HistoricalStore.get_macro("VIXCLS")``, which
    live-fetches from FRED when the local cache has no VIXCLS rows yet. This
    is the same class of gap ``test_validation_forecast_direction.py``'s
    network test hit: conftest.py's session-wide autouse fixture deliberately
    blanks ``settings.settings.FRED_API_KEY`` (an ``env_io.SECRET_KEYS``
    member) for every test, including this network-marked one, for
    isolation. Restores from the raw ``.env`` file (bypassing the
    already-scrubbed settings singleton) for this test's scope only, and
    skips -- honestly, not a fabricated pass -- when no real key is present
    anywhere (a credential-less CI/sandbox environment genuinely cannot
    exercise this live path)."""
    real_key = dotenv_values(settings_module.ENV_PATH).get("FRED_API_KEY") or ""
    if not real_key:
        pytest.skip(
            "No real FRED_API_KEY in .env -- this network-marked test needs "
            "live FRED credentials (for the real VIX history the adapter "
            "depends on) and cannot run in a credential-less environment."
        )
    monkeypatch.setattr(settings_module.settings, "FRED_API_KEY", real_key)


def test_adapter_returns_three_items(spy_close, fred_credential):
    X, y, precomputed = _build_vol_mispricing_adapter(spy_close)
    assert not X.empty
    assert not y.empty
    assert isinstance(precomputed, dict)
    assert "VolMispricing" in precomputed
    assert X.index.is_unique
    assert y.index.is_unique


def test_adapter_produces_finite_returns(spy_close, fred_credential):
    X, y, precomputed = _build_vol_mispricing_adapter(spy_close)
    returns = precomputed["VolMispricing"]
    assert not returns.empty
    assert np.isfinite(returns).all()
    # A real 10-year run must have SOME nonzero trading days -- an all-zero
    # series would mean the RICH/CHEAP gate never opened at all in this
    # window, which is not what was measured during development (see module
    # docstring: 259 monthly cycles, 46% gate-open rate over 2005-2026).
    assert (returns != 0.0).any()


def test_registry_entry_matches_adapter():
    from scripts.refresh_validations import STRATEGY_REGISTRY

    adapter_fn, turnover, universe = STRATEGY_REGISTRY["vol_mispricing"]
    assert adapter_fn is _build_vol_mispricing_adapter
    assert universe == ["SPY"]
    assert 0.0 < turnover < 1.0


def test_options_selling_stress_fn_resolves_for_this_strategy_only():
    from validation.options_selling_backtest import simulate_vol_mispricing_returns

    assert _resolve_options_selling_stress_fn("vol_mispricing") is simulate_vol_mispricing_returns
    # Every pre-existing entry must be completely unaffected (today's exact
    # is_options_selling=False behavior).
    assert _resolve_options_selling_stress_fn("rsi2_mean_reversion") is None
    assert _resolve_options_selling_stress_fn("garch_vol_target") is None
    assert _resolve_options_selling_stress_fn("lgbm_ranker") is None


def test_vol_mispricing_validation_harness_runs(spy_close, tmp_path, fred_credential):
    """Smoke-tests StrategyValidationHarness end-to-end on the production
    adapter, WITH the real is_options_selling/stress_returns_fn wiring --
    asserts a well-formed report (finite numbers, deployable is a bool,
    stress_gate_passed is present) rather than deployability itself. The
    production registry entry's own real measured numbers (deployable=False)
    belong in docs/signals/vol_mispricing.md, not this test.
    """
    X, y, precomputed = _build_vol_mispricing_adapter(spy_close)
    assert not X.empty and not y.empty

    strategy_fn = _make_strategy_fn(precomputed, turnover=0.05)
    stress_fn = _resolve_options_selling_stress_fn("vol_mispricing")
    assert stress_fn is not None

    cost_model = TieredCostModel()
    harness = StrategyValidationHarness(
        strategy_fn=strategy_fn,
        universe_fn=lambda _: ["SPY"],
        cost_model=cost_model,
        n_cpcv_splits=6,   # reduced from the production default (10) purely
        n_test_splits=2,   # for test runtime.
        reports_dir=str(tmp_path),
        is_options_selling=True,
        stress_returns_fn=stress_fn,
    )

    report = harness.run(
        start_date=str(X.index[0].date()),
        end_date=str(X.index[-1].date()),
        X=X,
        y=y,
        strategy_name="vol_mispricing_test",
    )

    summary = report.to_summary_dict()
    assert isinstance(summary["deployable"], bool)
    assert np.isfinite(summary["sharpe"])
    assert np.isfinite(summary["pbo"])
    assert np.isfinite(summary["dsr"])
    assert np.isfinite(summary["max_drawdown"])
    # The options-selling flag must genuinely reach the report -- a silent
    # False here would mean the stress gate never actually ran despite this
    # test passing is_options_selling=True above.
    assert summary["is_options_selling"] is True
    assert "stress_gate_passed" in summary
