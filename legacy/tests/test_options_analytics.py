"""
tests/test_options_analytics.py
================================
Tests for execution/options_analytics.py.

Covers the fix from a `symbol: string` undefined-name NameError (crashed on
import under Python 3.12) and from get_options_analytics_summary() exposing
a deterministic ticker-hash placeholder as Net Dealer Premium / regime / an
intraday theta-gamma curve -- CONSTRAINT #4: no real options-chain /
open-interest data source exists anywhere in this codebase, so the public
summary now honestly reports these as unavailable (None/[]) rather than a
plausible-looking number. The demo/scaffolding helpers
(_demo_net_dealer_premium, _demo_0dte_theta_decay) are kept and directly
tested since they're useful scaffolding for a future real integration, but
are no longer called from the public summary.
"""
import pytest
from execution.options_analytics import (
    _demo_net_dealer_premium,
    _demo_0dte_theta_decay,
    get_options_analytics_summary,
)


def test_demo_net_dealer_premium_is_deterministic_per_symbol():
    assert isinstance(_demo_net_dealer_premium("SPY"), float)
    assert _demo_net_dealer_premium("SPY") == _demo_net_dealer_premium("SPY")


def test_demo_0dte_theta_decay_shape():
    decay_series = _demo_0dte_theta_decay()
    assert len(decay_series) == 13
    assert 'theta' in decay_series[0]
    assert 'gamma' in decay_series[0]

    # Check that theta and gamma are generally increasing
    assert decay_series[-1]['theta'] > decay_series[0]['theta']
    assert decay_series[-1]['gamma'] > decay_series[0]['gamma']


def test_get_options_analytics_summary_reports_unavailable_not_fabricated():
    summary = get_options_analytics_summary("AAPL")
    assert summary["symbol"] == "AAPL"
    assert summary["net_dealer_premium"] is None
    assert summary["regime"] is None
    assert summary["intraday_series"] == []
    assert summary["is_synthetic"] is True
