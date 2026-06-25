"""
tests/test_html_report.py
=========================
Offline unit tests for the rebuilt daily HTML report
(``diagnostics_and_visuals.generate_html_report``).

The report was redesigned (2026-06) to lead with **Holdings & P&L** and
**Action & Rationale**.  These tests pin the new contract:

  • ``TestHoldingsAndRationale`` — the advisory schema (holdings, conviction,
    suggested size, rationale) and the optional ``account_summary`` band render
    their values into the HTML.
  • ``TestBackwardCompat`` — the wide pipeline schema (``main_orchestrator.py``)
    still renders with ``account_summary=None`` and no summary band, never
    raising on missing advisory/holdings keys.
  • ``TestRobustness`` — NaN/Inf sanitisation, empty universe, and the
    fallback derivation of market value / unrealized P&L when the snapshot
    fields are absent.
  • ``TestNoSecrets`` — the rendered HTML never contains credential-shaped
    tokens (the account snapshot is the only account-state source and it is
    documented to never carry secrets).

All tests write to a ``tmp_path`` file — no network, no repo pollution.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from diagnostics_and_visuals import generate_html_report


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def advisory_rows():
    """Two rows mirroring ``main.py._write_html_report`` output (advisory schema)."""
    return [
        {
            "Symbol": "AAPL",
            "Action Signal": "BUY",
            "Advisory_Conviction": 0.72,
            "Advisory_Rationale": "Held above effective cost basis with a constructive forecast.",
            "Advisory_Position_Pct": 0.043,
            "Forecast_30": 232.50,
            "data_quality": "OK",
            "strategy": "momentum_trend",
            "Robinhood Shares": 12.0,
            "Robinhood Avg Cost": 180.25,
            "Robinhood Current Price": 214.10,
            "Robinhood Market Value": 2569.20,
            "Robinhood Unrealized PL": 406.20,
            "Robinhood Unrealized PL Pct": 0.1878,
            "Robinhood Dividends": 8.40,
            "Company Name": "Apple Inc.",
            "RSI": 54.2,
            "GARCH_Vol": 0.21,
            "Max Drawdown": -0.14,
        },
        {
            "Symbol": "AGNC",
            "Action Signal": "SELL",
            "Advisory_Conviction": 0.81,
            "Advisory_Rationale": "Below effective cost basis with a bearish forecast.",
            "Advisory_Position_Pct": 0.0,
            "Forecast_30": 8.95,
            "data_quality": "OK",
            "strategy": "mean_reversion",
            "Robinhood Shares": 300.0,
            "Robinhood Avg Cost": 11.40,
            "Robinhood Current Price": 9.62,
            "Robinhood Market Value": 2886.0,
            "Robinhood Unrealized PL": -534.0,
            "Robinhood Unrealized PL Pct": -0.1561,
            "Robinhood Dividends": 142.0,
            "Company Name": "AGNC Investment Corp.",
            "RSI": 31.0,
            "GARCH_Vol": 0.34,
            "Max Drawdown": -0.41,
        },
    ]


@pytest.fixture
def account_summary():
    """Portfolio-level totals band (advisory path). Never contains secrets."""
    return {
        "total_equity": 41250.0,
        "buying_power": 5120.0,
        "total_unrealized_pl": -127.80,
        "total_dividends": 150.40,
        "num_positions": 2,
        "fetched_at": "2026-06-25 13:02 UTC",
        "age_hours": 1.4,
        "is_stale": False,
    }


def _render(tmp_path: Path, rows, **kwargs) -> str:
    out = tmp_path / "report.html"
    generate_html_report(rows, "NEUTRAL", str(out), **kwargs)
    assert out.exists(), "report file was not written"
    return out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Holdings & rationale
# ---------------------------------------------------------------------------

class TestHoldingsAndRationale:
    def test_holdings_values_render(self, tmp_path, advisory_rows):
        html = _render(tmp_path, advisory_rows)
        # Company names, prices and signed P&L all appear.
        assert "Apple Inc." in html
        assert "AGNC Investment Corp." in html
        assert "$214.10" in html          # current price
        assert "+$406" in html            # positive unrealized P&L (signed)
        assert "-$534" in html            # negative unrealized P&L (signed)
        assert "+18.8%" in html           # positive P&L %
        assert "-15.6%" in html           # negative P&L %

    def test_action_and_rationale_render(self, tmp_path, advisory_rows):
        html = _render(tmp_path, advisory_rows)
        assert "Held above effective cost basis" in html
        assert "Below effective cost basis" in html
        # Action signal CSS classes are emitted for colour-coding.
        assert "sig-BUY" in html
        assert "sig-SELL" in html
        # Conviction meters render for non-zero conviction rows.
        assert "conv-fill" in html

    def test_account_summary_band_renders(self, tmp_path, advisory_rows, account_summary):
        html = _render(tmp_path, advisory_rows, account_summary=account_summary)
        assert "Total Equity" in html
        assert "$41,250" in html          # equity formatted with thousands sep
        assert "Buying Power" in html
        assert "$5,120" in html
        assert "Dividends Received" in html
        # Signal tallies are derived and injected into the band.
        assert "1 BUY" in html
        assert "1 SELL" in html


# ---------------------------------------------------------------------------
# Backward compatibility with main_orchestrator.py's wide schema
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_pipeline_schema_no_summary_band(self, tmp_path):
        """The orchestrator's spaced-key schema renders with no account band."""
        pipeline_rows = [
            {
                "Symbol": "SPY",
                "Action Signal": "HOLD",
                "Price": 540.0,
                "Forecast_30": 545.0,
                "Kelly Target": 0.05,
                "Option Strategy": "None",
                "CoVaR Proxy": 0.04,
                "Max Drawdown": -0.10,
            }
        ]
        html = _render(tmp_path, pipeline_rows)  # account_summary defaults to None
        assert "SPY" in html
        assert "sig-HOLD" in html
        # The summary band is hidden when account_summary is absent.
        assert "Total Equity" not in html

    def test_missing_holdings_keys_degrade_to_dash(self, tmp_path):
        """A non-held watchlist symbol (no holdings) renders without raising."""
        rows = [{"Symbol": "TSLA", "Action Signal": "BUY", "Forecast_30": 250.0}]
        html = _render(tmp_path, rows)
        assert "TSLA" in html
        assert "—" in html  # placeholder for absent holdings columns


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_nan_inf_sanitised(self, tmp_path):
        rows = [{
            "Symbol": "BAD",
            "Action Signal": "HOLD",
            "Robinhood Shares": float("nan"),
            "Robinhood Current Price": float("inf"),
            "Forecast_30": float("nan"),
            "RSI": float("nan"),
        }]
        html = _render(tmp_path, rows)  # must not raise on NaN/Inf
        assert "BAD" in html

    def test_empty_universe(self, tmp_path):
        html = _render(tmp_path, [])
        assert "No symbols were analysed" in html

    def test_market_value_derived_when_absent(self, tmp_path):
        """Market value falls back to shares × price when snapshot omits it."""
        rows = [{
            "Symbol": "MSFT",
            "Action Signal": "BUY",
            "Robinhood Shares": 10.0,
            "Robinhood Avg Cost": 300.0,
            "Robinhood Current Price": 400.0,
            # No market value / unrealized P&L supplied → derived.
        }]
        html = _render(tmp_path, rows)
        assert "$4,000" in html          # 10 × 400 market value
        assert "+$1,000" in html         # (400 - 300) × 10 unrealized P&L


# ---------------------------------------------------------------------------
# No-secrets guard
# ---------------------------------------------------------------------------

class TestNoSecrets:
    def test_no_credential_tokens_in_html(self, tmp_path, advisory_rows, account_summary):
        html = _render(tmp_path, advisory_rows, account_summary=account_summary)
        lowered = html.lower()
        for token in ("password", "rh_password", "secret", "mfa", "api_key", "apikey"):
            assert token not in lowered, f"credential-shaped token '{token}' leaked into report"
