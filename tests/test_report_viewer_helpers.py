"""Unit tests for :mod:`gui.report_viewer_helpers`.

These cover the pure data-shaping helpers extracted out of
``gui/panels/report_viewer.py`` (Agent G1 hardening refactor). Every helper is
exercised for a known-input → known-output case plus its empty / NaN / degraded
path — asserting the honesty rules the panel relied on inline: missing values
degrade to explicit ``"—"`` / ``NaN`` placeholders, never a fabricated ``0.0``,
and nothing crashes on empty input (except the Brinson-Fachler input builders,
which deliberately raise ``ValueError`` exactly as they did in the panel).

Fully offline — no Streamlit, no network, no disk.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from gui.report_viewer_helpers import (
    build_brinson_fachler_inputs,
    build_cluster_assignment_frame,
    build_cluster_concentration_rows,
    build_hidden_fields_frame,
    build_mfe_mae_scatter_frame,
    build_tactical_ranges_frame,
    calibration_summary_stats,
    compute_brinson_fachler,
    default_brinson_fachler_frame,
    format_tracking_pct,
    heavy_concentration_clusters,
    parse_pasted_sector_matrix,
    shape_sector_details_frame,
    tracking_delta_label,
    validate_brinson_fachler_weights,
)
from gui.panels._shared import GICS_SECTORS, _BF_EDITOR_COLUMNS


# ===========================================================================
# default_brinson_fachler_frame
# ===========================================================================


def test_default_frame_shape_and_sectors():
    df = default_brinson_fachler_frame()
    assert list(df.columns) == list(_BF_EDITOR_COLUMNS)
    assert list(df["Sector"]) == list(GICS_SECTORS)
    assert len(df) == len(GICS_SECTORS)
    # all numeric seed values are zero
    for c in _BF_EDITOR_COLUMNS[1:]:
        assert (df[c] == 0.0).all()


# ===========================================================================
# parse_pasted_sector_matrix
# ===========================================================================


def test_parse_with_header_tsv():
    text = (
        "Sector\tPortfolio Weight (%)\tPortfolio Return (%)\t"
        "Benchmark Weight (%)\tBenchmark Return (%)\n"
        "Information Technology\t28\t12.4\t26\t10.1\n"
        "Financials\t15\t3.2\t14\t2.9\n"
    )
    df = parse_pasted_sector_matrix(text)
    assert list(df.columns) == list(_BF_EDITOR_COLUMNS)
    assert list(df["Sector"]) == ["Information Technology", "Financials"]
    assert df.loc[0, "Portfolio Weight (%)"] == 28.0
    assert df.loc[1, "Portfolio Return (%)"] == 3.2


def test_parse_headerless_positional_csv():
    # No header, comma-delimited: first row is data, must NOT be promoted.
    text = "Tech,28,12.4,26,10.1\nHealth,20,5.0,18,4.5\n"
    df = parse_pasted_sector_matrix(text)
    assert list(df.columns) == list(_BF_EDITOR_COLUMNS)
    assert list(df["Sector"]) == ["Tech", "Health"]
    assert df.loc[0, "Benchmark Return (%)"] == 10.1


def test_parse_strips_percent_and_coerces_bad_cells_to_zero():
    # A header row is required here: a lone data row whose 2nd-5th cells aren't
    # all numeric is (by design) detected as a header, so we supply an explicit
    # header plus one data row carrying a "%" suffix and an unparseable cell.
    text = (
        "Sector,Portfolio Weight (%),Portfolio Return (%),"
        "Benchmark Weight (%),Benchmark Return (%)\n"
        "Tech,28%,foo,26,10.1\n"
    )
    df = parse_pasted_sector_matrix(text)
    assert df.loc[0, "Portfolio Weight (%)"] == 28.0
    # unparseable 'foo' -> 0.0 (normalized up front)
    assert df.loc[0, "Portfolio Return (%)"] == 0.0


def test_parse_empty_raises():
    with pytest.raises(ValueError):
        parse_pasted_sector_matrix("")
    with pytest.raises(ValueError):
        parse_pasted_sector_matrix("   \n  ")


def test_parse_wrong_column_count_raises():
    with pytest.raises(ValueError):
        parse_pasted_sector_matrix("Sector,Weight\nFinancials,10")


# ===========================================================================
# build_brinson_fachler_inputs — fractions, not percents
# ===========================================================================


def test_build_inputs_divides_by_100():
    editor = pd.DataFrame(
        [
            {
                "Sector": "Tech",
                "Portfolio Weight (%)": 60.0,
                "Portfolio Return (%)": 10.0,
                "Benchmark Weight (%)": 50.0,
                "Benchmark Return (%)": 8.0,
            },
            {
                "Sector": "Fin",
                "Portfolio Weight (%)": 40.0,
                "Portfolio Return (%)": 4.0,
                "Benchmark Weight (%)": 50.0,
                "Benchmark Return (%)": 5.0,
            },
        ]
    )
    p_df, b_df = build_brinson_fachler_inputs(editor)
    assert list(p_df.columns) == ["sector", "portfolio_weight", "portfolio_return"]
    assert list(b_df.columns) == ["sector", "benchmark_weight", "benchmark_return"]
    assert p_df.loc[0, "portfolio_weight"] == pytest.approx(0.60)
    assert p_df.loc[0, "portfolio_return"] == pytest.approx(0.10)
    assert b_df.loc[1, "benchmark_return"] == pytest.approx(0.05)


def test_build_inputs_empty_raises():
    with pytest.raises(ValueError):
        build_brinson_fachler_inputs(pd.DataFrame())


def test_build_inputs_missing_column_raises():
    bad = pd.DataFrame([{"Sector": "Tech", "Portfolio Weight (%)": 100.0}])
    with pytest.raises(ValueError):
        build_brinson_fachler_inputs(bad)


def test_build_inputs_drops_blank_sector_rows():
    editor = default_brinson_fachler_frame()
    editor.loc[0, "Sector"] = "   "  # blank after strip
    p_df, _ = build_brinson_fachler_inputs(editor)
    assert len(p_df) == len(GICS_SECTORS) - 1


# ===========================================================================
# validate_brinson_fachler_weights
# ===========================================================================


def _balanced_editor() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Sector": "Tech",
                "Portfolio Weight (%)": 60.0,
                "Portfolio Return (%)": 10.0,
                "Benchmark Weight (%)": 50.0,
                "Benchmark Return (%)": 8.0,
            },
            {
                "Sector": "Fin",
                "Portfolio Weight (%)": 40.0,
                "Portfolio Return (%)": 4.0,
                "Benchmark Weight (%)": 50.0,
                "Benchmark Return (%)": 5.0,
            },
        ]
    )


def test_validate_clean_returns_empty():
    assert validate_brinson_fachler_weights(_balanced_editor()) == []


def test_validate_flags_off_100_sums():
    editor = _balanced_editor()
    editor.loc[0, "Portfolio Weight (%)"] = 10.0  # portfolio now sums to 50%
    warnings = validate_brinson_fachler_weights(editor)
    assert any("Portfolio weights sum" in w for w in warnings)


def test_validate_flags_negative_weight():
    editor = _balanced_editor()
    editor.loc[0, "Benchmark Weight (%)"] = -10.0
    editor.loc[1, "Benchmark Weight (%)"] = 110.0  # keep sum ~100
    warnings = validate_brinson_fachler_weights(editor)
    assert any("Negative values" in w for w in warnings)


def test_validate_empty_frame():
    assert validate_brinson_fachler_weights(pd.DataFrame()) == ["Sector editor is empty."]


# ===========================================================================
# compute_brinson_fachler — end-to-end against EvaluationEngine
# ===========================================================================


def test_compute_returns_canonical_keys():
    result = compute_brinson_fachler(_balanced_editor())
    for key in (
        "Portfolio Return",
        "Benchmark Return",
        "Active Return",
        "Allocation Effect",
        "Selection Effect",
        "Interaction Effect",
        "Attribution Sum",
        "Sector Details",
    ):
        assert key in result
    # Active return = portfolio - benchmark, within float tolerance.
    assert result["Active Return"] == pytest.approx(
        result["Portfolio Return"] - result["Benchmark Return"], abs=1e-9
    )


# ===========================================================================
# shape_sector_details_frame
# ===========================================================================


def test_shape_sector_details_orders_columns():
    details = {
        "Tech": {
            "weight_p": 0.6,
            "weight_b": 0.5,
            "return_p": 0.10,
            "return_b": 0.08,
            "allocation_effect": 0.001,
            "selection_effect": 0.012,
            "interaction_effect": 0.002,
            "total_attribution": 0.015,
            "extra_ignored": 99,  # not in preferred order → dropped
        }
    }
    df = shape_sector_details_frame(details)
    assert list(df.columns) == [
        "sector", "weight_p", "weight_b", "return_p", "return_b",
        "allocation_effect", "selection_effect",
        "interaction_effect", "total_attribution",
    ]
    assert df.loc[0, "sector"] == "Tech"
    assert "extra_ignored" not in df.columns


def test_shape_sector_details_empty_returns_empty_frame():
    assert shape_sector_details_frame({}).empty
    assert shape_sector_details_frame(None).empty


# ===========================================================================
# build_tactical_ranges_frame
# ===========================================================================


def test_tactical_ranges_missing_values_show_dash():
    signals = [{"symbol": "AAPL"}]  # no action / ranges
    df = build_tactical_ranges_frame(signals)
    row = df.iloc[0]
    assert row["Symbol"] == "AAPL"
    assert row["Action"] == "—"
    assert row["Buy Range"] == "—"
    assert row["Sell Range"] == "—"
    assert row["Suggested Exit %"] == "—"


def test_tactical_ranges_sell_exit_pct_only_for_sell():
    signals = [
        {"symbol": "AAPL", "action": "SELL", "suggested_exit_pct": 0.25,
         "buy_range": "b", "sell_range": "s"},
        {"symbol": "MSFT", "action": "BUY", "suggested_exit_pct": 0.25},
    ]
    df = build_tactical_ranges_frame(signals)
    assert df.loc[0, "Suggested Exit %"] == "25%"
    assert df.loc[0, "Buy Range"] == "b"
    # BUY row never shows an exit pct even when the field is present
    assert df.loc[1, "Suggested Exit %"] == "—"


def test_tactical_ranges_empty_signals():
    df = build_tactical_ranges_frame([])
    assert df.empty


# ===========================================================================
# build_hidden_fields_frame
# ===========================================================================


def test_hidden_fields_any_populated_true():
    signals = [{"symbol": "AAPL", "value_z": 1.2, "quality_z": None}]
    df, any_populated = build_hidden_fields_frame(signals)
    assert any_populated is True
    assert df.loc[0, "Symbol"] == "AAPL"
    assert df.loc[0, "Value Z"] == 1.2
    # Missing factor renders "—", never fabricated 0.0
    assert df.loc[0, "Quality Z"] == "—"


def test_hidden_fields_all_missing_any_populated_false():
    signals = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
    df, any_populated = build_hidden_fields_frame(signals)
    assert any_populated is False
    assert list(df["Symbol"]) == ["AAPL", "MSFT"]
    assert (df["Value Z"] == "—").all()


def test_hidden_fields_symbol_fallback_dash():
    df, any_populated = build_hidden_fields_frame([{}])
    assert df.loc[0, "Symbol"] == "—"
    assert any_populated is False


# ===========================================================================
# build_mfe_mae_scatter_frame
# ===========================================================================


def test_scatter_drops_rows_missing_mfe_or_mae():
    signals = [
        {"symbol": "AAPL", "mfe": 0.05, "mae": 0.02, "advisory_conviction": 0.8},
        {"symbol": "MSFT", "mfe": None, "mae": 0.03},  # dropped
        {"symbol": "NVDA", "mfe": 0.10},  # missing mae → dropped
    ]
    df = build_mfe_mae_scatter_frame(signals)
    assert list(df["symbol"]) == ["AAPL"]
    assert df.loc[df.index[0], "conviction"] == 0.8


def test_scatter_action_fallback_chain():
    signals = [{"symbol": "X", "mfe": 0.01, "mae": 0.01, "advisory_action": "BUY"}]
    df = build_mfe_mae_scatter_frame(signals)
    assert df.iloc[0]["action"] == "BUY"


def test_scatter_empty_has_correct_columns():
    df = build_mfe_mae_scatter_frame([])
    assert df.empty
    assert list(df.columns) == ["symbol", "mfe", "mae", "edge_ratio", "conviction", "action"]


# ===========================================================================
# Recommendation tracking formatters
# ===========================================================================


def test_format_tracking_pct():
    assert format_tracking_pct(0.1234) == "+12.34%"
    assert format_tracking_pct(-0.05) == "-5.00%"
    assert format_tracking_pct(float("nan")) == "—"


def test_tracking_delta_label_thresholds():
    assert "insufficient data" in tracking_delta_label(float("nan"))
    assert "adds value" in tracking_delta_label(0.02)
    assert "costs alpha" in tracking_delta_label(-0.02)
    assert "neutral" in tracking_delta_label(0.001)
    # boundary: exactly 0.005 is NOT > 0.005 → neutral
    assert "neutral" in tracking_delta_label(0.005)


# ===========================================================================
# calibration_summary_stats
# ===========================================================================


def test_calibration_summary_known_values():
    cal_df = pd.DataFrame(
        {
            "bin_center": [0.15, 0.55, 0.85],
            "win_rate": [0.10, 0.60, 0.90],
            "count": [10, 20, 20],
        }
    )
    stats = calibration_summary_stats(cal_df)
    assert stats["total"] == 50
    # count-weighted win rate = (0.10*10 + 0.60*20 + 0.90*20) / 50 = 0.62
    assert stats["overall_win_rate"] == pytest.approx(0.62)
    # MAE of |win_rate - bin_center| over the 3 scored bins
    expected_mae = np.mean([0.05, 0.05, 0.05])
    assert stats["calibration_error"] == pytest.approx(expected_mae)
    assert stats["n_scored_bins"] == 3


def test_calibration_summary_nan_bins_excluded_from_error():
    cal_df = pd.DataFrame(
        {
            "bin_center": [0.15, 0.55, 0.85],
            "win_rate": [0.10, float("nan"), 0.90],
            "count": [10, 0, 20],
        }
    )
    stats = calibration_summary_stats(cal_df)
    assert stats["n_scored_bins"] == 2
    assert not math.isnan(stats["calibration_error"])


def test_calibration_summary_all_nan_win_rates():
    cal_df = pd.DataFrame(
        {
            "bin_center": [0.15, 0.55],
            "win_rate": [float("nan"), float("nan")],
            "count": [0, 0],
        }
    )
    stats = calibration_summary_stats(cal_df)
    # No trades → overall win rate undefined (NaN), never fabricated 0.0
    assert math.isnan(stats["overall_win_rate"])
    assert math.isnan(stats["calibration_error"])
    assert stats["n_scored_bins"] == 0
    assert stats["total"] == 0


# ===========================================================================
# Correlation cluster shaping
# ===========================================================================


def test_cluster_assignment_frame():
    labels = {"AAPL": 1, "MSFT": 1, "XOM": 2}
    sig_map = {
        "AAPL": {"kelly_target": 0.12, "action": "BUY"},
        "MSFT": {"kelly_target": 0.0, "action": "HOLD"},  # zero kelly → "—"
        # XOM missing from sig_map entirely
    }
    df = build_cluster_assignment_frame(labels, sig_map)
    assert set(df.columns) == {"Symbol", "Cluster", "Action", "Kelly Target"}
    aapl = df[df["Symbol"] == "AAPL"].iloc[0]
    assert aapl["Cluster"] == 1
    assert aapl["Kelly Target"] == "12.0%"
    msft = df[df["Symbol"] == "MSFT"].iloc[0]
    assert msft["Kelly Target"] == "—"  # zero kelly not fabricated as 0.0%
    xom = df[df["Symbol"] == "XOM"].iloc[0]
    assert xom["Action"] == "—"  # missing signal → dash


def test_cluster_assignment_noise_cluster_dash():
    labels = {"AAPL": 0}  # cluster 0 = noise/unclustered
    df = build_cluster_assignment_frame(labels, {})
    assert df.iloc[0]["Cluster"] == "—"


def test_cluster_concentration_rows():
    summary = pd.DataFrame(
        [
            {"cluster_id": 1, "symbols": ["AAPL", "MSFT"], "n_symbols": 2, "avg_intra_corr": 0.72},
            {"cluster_id": 2, "symbols": ["XOM"], "n_symbols": 1, "avg_intra_corr": float("nan")},
        ]
    )
    sig_map = {
        "AAPL": {"kelly_target": 0.20},
        "MSFT": {"kelly_target": 0.15},
        "XOM": {"kelly_target": 0.05},
    }
    rows = build_cluster_concentration_rows(summary, sig_map)
    assert len(rows) == 2
    r1 = rows[0]
    assert r1["Cluster ID"] == 1
    assert r1["Symbols"] == "AAPL, MSFT"
    assert r1["Count"] == 2
    assert r1["Total Position %"] == "35.0%"
    assert r1["Avg |Corr|"] == "0.72"
    # NaN correlation → "—", never fabricated
    assert rows[1]["Avg |Corr|"] == "—"


def test_cluster_concentration_empty_summary():
    assert build_cluster_concentration_rows(pd.DataFrame(), {}) == []
    assert build_cluster_concentration_rows(None, {}) == []


def test_heavy_concentration_clusters():
    conc_rows = [
        {"Cluster ID": 1, "Total Position %": "41.0%"},
        {"Cluster ID": 2, "Total Position %": "10.0%"},
        {"Cluster ID": 3, "Total Position %": "30.0%"},  # exactly 30% → NOT heavy
    ]
    heavy = heavy_concentration_clusters(conc_rows)
    assert [r["Cluster ID"] for r in heavy] == [1]


def test_heavy_concentration_custom_threshold():
    conc_rows = [{"Cluster ID": 1, "Total Position %": "25.0%"}]
    assert heavy_concentration_clusters(conc_rows, threshold=0.20) == conc_rows
    assert heavy_concentration_clusters(conc_rows, threshold=0.30) == []
