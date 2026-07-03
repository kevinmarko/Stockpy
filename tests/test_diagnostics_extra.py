"""
tests/test_diagnostics_extra.py
=================================
Closes the remaining coverage gaps in ``diagnostics_and_visuals.py`` beyond
what ``tests/test_html_report.py`` already pins (Holdings & P&L rendering,
backward compat, NaN/Inf sanitization, no-secrets guard — not duplicated
here). Three genuinely uncovered areas, verified against source before
writing:

  * ``generate_plotly_volatility_bands`` — had ZERO unit tests. Covers the
    empty-DataFrame / missing-Close-column early returns (both ``None``, no
    crash, no file written), the documented 'Close'/'close' case-insensitive
    column resolution, and that a short (<20 row) DataFrame — insufficient
    for the 20-period rolling window — still writes a valid file rather than
    raising (the bands are simply NaN for the whole series).
  * ``snapshot_diff`` end-to-end rendering — the kwarg and its Jinja2
    ``.delta-band`` block are wired in, but no test exercises the full
    payload shape produced by ``scripts.snapshot_diff.SnapshotDiff.to_dict()``
    flowing all the way through ``generate_html_report``.
  * The ``cost_basis <= 0`` branch of the derived ``UnrealizedPLPct``
    fallback (``diagnostics_and_visuals.py``'s ``_num`` field-normalization
    block) — a distinct code path from the already-tested "fields entirely
    absent" case; pins that it falls back to ``0.0`` rather than raising or
    fabricating an infinite percentage.

All tests write only to ``tmp_path`` — no network, no repo pollution.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from diagnostics_and_visuals import generate_html_report, generate_plotly_volatility_bands


# ---------------------------------------------------------------------------
# generate_plotly_volatility_bands
# ---------------------------------------------------------------------------

def _price_df(n: int, close_col: str = "Close") -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {close_col: [100.0 + i for i in range(n)]},
        index=dates,
    )


class TestGeneratePlotlyVolatilityBands:
    def test_empty_dataframe_returns_none_and_writes_nothing(self, tmp_path: Path):
        out = tmp_path / "chart.html"
        result = generate_plotly_volatility_bands(pd.DataFrame(), "AAPL", output_path=str(out))
        assert result is None
        assert not out.exists()

    def test_missing_close_column_returns_none(self, tmp_path: Path):
        out = tmp_path / "chart.html"
        df = pd.DataFrame({"Open": [1.0, 2.0, 3.0]}, index=pd.date_range("2026-01-01", periods=3))
        result = generate_plotly_volatility_bands(df, "AAPL", output_path=str(out))
        assert result is None
        assert not out.exists()

    def test_uppercase_close_column_resolves(self, tmp_path: Path):
        out = tmp_path / "chart_upper.html"
        df = _price_df(30, close_col="Close")
        result = generate_plotly_volatility_bands(df, "AAPL", output_path=str(out))
        assert result == str(out)
        assert out.exists()

    def test_lowercase_close_column_resolves(self, tmp_path: Path):
        """Documented backward-compat: main_orchestrator.py lowercases columns."""
        out = tmp_path / "chart_lower.html"
        df = _price_df(30, close_col="close")
        result = generate_plotly_volatility_bands(df, "AAPL", output_path=str(out))
        assert result == str(out)
        assert out.exists()

    def test_short_dataframe_below_rolling_window_still_writes_file(self, tmp_path: Path):
        """Fewer than 20 rows means the 20-period SMA/std bands are entirely
        NaN — verifies this degrades to an empty-but-valid band series
        rather than raising."""
        out = tmp_path / "chart_short.html"
        df = _price_df(5)
        result = generate_plotly_volatility_bands(df, "AAPL", output_path=str(out))
        assert result == str(out)
        assert out.exists()
        html = out.read_text(encoding="utf-8")
        assert len(html) > 0

    def test_output_file_contains_ticker_and_plotly_markers(self, tmp_path: Path):
        out = tmp_path / "chart_full.html"
        df = _price_df(40)
        generate_plotly_volatility_bands(df, "MSFT", output_path=str(out))
        html = out.read_text(encoding="utf-8")
        assert "MSFT" in html
        assert "plotly" in html.lower()

    def test_default_output_path_used_when_not_specified(self, tmp_path: Path, monkeypatch):
        """Default output_path is a bare relative filename — run from tmp_path
        (via monkeypatch.chdir) so a naive call never writes into the repo root."""
        monkeypatch.chdir(tmp_path)
        df = _price_df(25)
        result = generate_plotly_volatility_bands(df, "AAPL")
        assert result == "volatility_bands.html"
        assert (tmp_path / "volatility_bands.html").exists()


# ---------------------------------------------------------------------------
# generate_html_report — snapshot_diff end-to-end integration
# ---------------------------------------------------------------------------

def _render(tmp_path: Path, rows, **kwargs) -> str:
    out = tmp_path / "report.html"
    generate_html_report(rows, "NEUTRAL", str(out), **kwargs)
    assert out.exists()
    return out.read_text(encoding="utf-8")


@pytest.fixture
def minimal_rows():
    return [{"Symbol": "AAPL", "Action Signal": "HOLD"}]


class TestSnapshotDiffIntegration:
    def test_none_hides_delta_band(self, tmp_path, minimal_rows):
        """The CSS rule '.delta-band {' is always present in <style> — only
        the rendered <div class="delta-band"> element is conditional on the
        kwarg, so assert on the element tag, not the bare class-name substring."""
        html = _render(tmp_path, minimal_rows, snapshot_diff=None)
        assert '<div class="delta-band">' not in html

    def test_is_empty_renders_no_material_changes_note(self, tmp_path, minimal_rows):
        diff = {
            "prev_ts": "2026-06-30T10:00:00", "curr_ts": "2026-07-01T10:00:00",
            "regime_change": None, "new_buys": [], "action_flips": [],
            "conviction_deltas": [], "added_holdings": [], "dropped_holdings": [],
            "notes": [], "is_empty": True,
        }
        html = _render(tmp_path, minimal_rows, snapshot_diff=diff)
        assert '<div class="delta-band">' in html
        assert "No material changes since last run." in html

    def test_full_payload_renders_every_section(self, tmp_path, minimal_rows):
        """A fully-populated SnapshotDiff.to_dict()-shaped payload — every
        list field non-empty — must render each corresponding section."""
        diff = {
            "prev_ts": "2026-06-30T10:00:00", "curr_ts": "2026-07-01T10:00:00",
            "regime_change": ["NEUTRAL", "RECESSION"],
            "new_buys": ["NVDA"],
            "action_flips": [{"symbol": "TSLA", "before": "HOLD", "after": "SELL"}],
            "conviction_deltas": [{"symbol": "AAPL", "before": 0.40, "after": 0.75, "delta": 0.35}],
            "added_holdings": ["GOOGL"],
            "dropped_holdings": ["INTC"],
            "notes": [],
            "is_empty": False,
        }
        html = _render(tmp_path, minimal_rows, snapshot_diff=diff)
        assert '<div class="delta-band">' in html
        assert "No material changes since last run." not in html
        # Regime change banner.
        assert "NEUTRAL" in html and "RECESSION" in html
        # New buys.
        assert "NVDA" in html
        # Action flip.
        assert "TSLA" in html
        assert "sig-SELL" in html
        # Conviction delta (formatted to 2 decimals).
        assert "0.40" in html and "0.75" in html
        # Holdings added/dropped.
        assert "GOOGL" in html
        assert "INTC" in html

    def test_first_snapshot_no_prev_ts_shows_first_snapshot_label(self, tmp_path, minimal_rows):
        diff = {
            "prev_ts": None, "curr_ts": "2026-07-01T10:00:00",
            "regime_change": None, "new_buys": ["AAPL"], "action_flips": [],
            "conviction_deltas": [], "added_holdings": ["AAPL"], "dropped_holdings": [],
            "notes": [], "is_empty": False,
        }
        html = _render(tmp_path, minimal_rows, snapshot_diff=diff)
        assert "First snapshot" in html


# ---------------------------------------------------------------------------
# UnrealizedPLPct cost_basis <= 0 fallback
# ---------------------------------------------------------------------------

class TestUnrealizedPLPctCostBasisGuard:
    def test_zero_avg_cost_falls_back_to_zero_pct_not_infinite(self, tmp_path):
        """Shares are held but AvgCost is 0.0 (malformed/missing cost-basis
        feed) — cost_basis = AvgCost * Shares = 0.0, so the pct fallback must
        take the `else 0.0` branch rather than dividing by zero."""
        rows = [{
            "Symbol": "WEIRD",
            "Action Signal": "HOLD",
            "Robinhood Shares": 50.0,
            "Robinhood Avg Cost": 0.0,
            "Robinhood Current Price": 25.0,
            # No UnrealizedPLPct supplied -> derived.
        }]
        html = _render(tmp_path, rows)
        assert "WEIRD" in html
        # Must not render an "inf%" or "nan%" artifact anywhere in the row.
        assert "inf%" not in html.lower()
        assert "nan%" not in html.lower()

    def test_zero_shares_and_zero_cost_no_crash(self, tmp_path):
        rows = [{
            "Symbol": "GHOST",
            "Action Signal": "HOLD",
            "Robinhood Shares": 0.0,
            "Robinhood Avg Cost": 0.0,
            "Robinhood Current Price": 0.0,
        }]
        html = _render(tmp_path, rows)  # must not raise
        assert "GHOST" in html
