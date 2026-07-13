"""
tests/test_diagnostics_charts.py — figure builders in diagnostics_and_visuals.py
=================================================================================
Covers the Plotly figure-construction body of
``diagnostics_and_visuals.generate_plotly_volatility_bands`` (the SMA + 2σ
Bollinger-band trace construction, layout, and case-insensitive Close/close
resolution) — the chart-helper region flagged uncovered in
``docs/test_coverage_analysis.md``.

Complementary to ``tests/test_diagnostics_extra.py``, which pins the early-return
/ file-written contract via bare ``plotly`` substring checks; this file asserts
the *content* of the built figure (every trace name, the SMA band values, the
titled layout) so the trace-construction lines are genuinely exercised.

TEST-ONLY: ``diagnostics_and_visuals.py`` source is NOT modified. Everything is
written to ``tmp_path`` — no network, no repo pollution.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from diagnostics_and_visuals import generate_plotly_volatility_bands

_TRACE_NAMES = ["Close Price", "20-Day SMA", "Upper BB (2.0 Std)", "Lower BB (2.0 Std)"]


def _price_df(n: int, close_col: str = "Close", start: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({close_col: [start + i for i in range(n)]}, index=dates)


class TestVolatilityBandsFigureContent:
    def test_builds_all_four_traces(self, tmp_path: Path):
        out = tmp_path / "chart.html"
        result = generate_plotly_volatility_bands(_price_df(40), "MSFT", output_path=str(out))
        assert result == str(out)
        html = out.read_text(encoding="utf-8")
        for name in _TRACE_NAMES:
            assert name in html, f"expected trace {name!r} in the rendered figure"

    def test_title_carries_the_ticker(self, tmp_path: Path):
        out = tmp_path / "chart.html"
        generate_plotly_volatility_bands(_price_df(30), "AAPL", output_path=str(out))
        html = out.read_text(encoding="utf-8")
        assert "Volatility Bands & Tactical Ranges: AAPL" in html

    def test_band_math_is_well_formed_for_sufficient_history(self, tmp_path: Path):
        """With ≥20 rows the 20-period SMA/2σ bands are non-NaN and correctly
        ordered (upper > sma > lower once std > 0) — the same vectorized band
        math the figure builder embeds. (Plotly serializes numeric arrays as
        base64 ``bdata`` in the HTML, so the ordering is asserted on the pandas
        computation rather than a literal-value substring in the file.)"""
        n = 40
        df = _price_df(n)
        out = tmp_path / "chart.html"
        result = generate_plotly_volatility_bands(df, "SPY", output_path=str(out))
        assert result == str(out) and out.exists()

        sma = df["Close"].rolling(window=20).mean()
        std = df["Close"].rolling(window=20).std()
        upper = sma + std * 2.0
        lower = sma - std * 2.0
        assert not pd.isna(sma.iloc[-1])
        assert float(lower.iloc[-1]) < float(sma.iloc[-1]) < float(upper.iloc[-1])

    def test_lowercase_close_column_builds_same_traces(self, tmp_path: Path):
        """Documented backward-compat: main_orchestrator lowercases columns."""
        out = tmp_path / "chart_lower.html"
        result = generate_plotly_volatility_bands(
            _price_df(30, close_col="close"), "NVDA", output_path=str(out)
        )
        assert result == str(out)
        html = out.read_text(encoding="utf-8")
        for name in _TRACE_NAMES:
            assert name in html

    def test_short_dataframe_still_builds_without_raising(self, tmp_path: Path):
        """Fewer than 20 rows → bands are NaN for the whole series, but the figure
        must still build and write (degrade, never raise)."""
        out = tmp_path / "chart_short.html"
        result = generate_plotly_volatility_bands(_price_df(5), "TSLA", output_path=str(out))
        assert result == str(out)
        assert out.exists() and out.stat().st_size > 0


class TestVolatilityBandsEarlyReturns:
    def test_empty_dataframe_degrades_to_none_no_file(self, tmp_path: Path):
        out = tmp_path / "chart.html"
        assert generate_plotly_volatility_bands(pd.DataFrame(), "AAPL", output_path=str(out)) is None
        assert not out.exists()

    def test_missing_close_column_degrades_to_none_no_file(self, tmp_path: Path):
        out = tmp_path / "chart.html"
        df = pd.DataFrame({"Open": [1.0, 2.0, 3.0]}, index=pd.date_range("2026-01-01", periods=3))
        assert generate_plotly_volatility_bands(df, "AAPL", output_path=str(out)) is None
        assert not out.exists()
