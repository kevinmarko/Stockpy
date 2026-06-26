"""
tests/test_brinson_fachler_ui.py
================================
Pure-Python tests for the Brinson-Fachler attribution helpers exposed by
``gui/panels.py``.  The helpers are deliberately factored out of the
``render_*`` Streamlit functions so they are testable without spinning up the
GUI: ``default_brinson_fachler_frame``, ``parse_pasted_sector_matrix``,
``build_brinson_fachler_inputs``, ``validate_brinson_fachler_weights``, and
``compute_brinson_fachler``.

Each test pins one user-facing behaviour of the new "Attribution Analysis"
section in the Reports tab (Task 1):

*   default frame matches the GICS 11 sector universe and the canonical
    editor column order;
*   TSV bulk paste with a header row round-trips into the same column shape;
*   TSV bulk paste without a header is interpreted positionally;
*   CSV paste with a header row works (delimiter auto-detection);
*   percentage → fraction conversion is applied in
    ``build_brinson_fachler_inputs`` (the engine multiplies weight × return
    directly, so unit consistency is mandatory);
*   ``compute_brinson_fachler`` end-to-end produces the engine's canonical
    result dict with non-zero allocation/selection effects on a worked
    example, and the per-sector attribution sums to the active return;
*   weight-validation warns on sums far from 100% and on negative weights.

The point is to catch a regression where the editor frame, paste parser, or
unit conversion silently drifts — the engine itself is already covered by
the existing ``EvaluationEngine`` test surface.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from gui.panels import (
    GICS_SECTORS,
    build_brinson_fachler_inputs,
    compute_brinson_fachler,
    default_brinson_fachler_frame,
    parse_pasted_sector_matrix,
    validate_brinson_fachler_weights,
)


# ---------------------------------------------------------------------------
# default_brinson_fachler_frame
# ---------------------------------------------------------------------------

def test_default_frame_shape_and_sectors():
    df = default_brinson_fachler_frame()
    assert list(df.columns) == [
        "Sector",
        "Portfolio Weight (%)",
        "Portfolio Return (%)",
        "Benchmark Weight (%)",
        "Benchmark Return (%)",
    ]
    assert list(df["Sector"]) == list(GICS_SECTORS)
    # All numeric columns are zero so the editor starts blank but valid-shape.
    for col in df.columns[1:]:
        assert (df[col] == 0.0).all()


# ---------------------------------------------------------------------------
# parse_pasted_sector_matrix
# ---------------------------------------------------------------------------

def test_paste_tsv_with_header():
    text = (
        "Sector\tPortfolio Weight (%)\tPortfolio Return (%)\tBenchmark Weight (%)\tBenchmark Return (%)\n"
        "Information Technology\t28\t12.4\t26\t10.1\n"
        "Health Care\t12\t-3.5\t14\t-2.0\n"
    )
    df = parse_pasted_sector_matrix(text)
    assert list(df["Sector"]) == ["Information Technology", "Health Care"]
    assert df.loc[0, "Portfolio Weight (%)"] == 28.0
    assert df.loc[1, "Portfolio Return (%)"] == -3.5


def test_paste_tsv_without_header_is_positional():
    text = "Energy\t5\t2.1\t4\t1.5\nUtilities\t3\t1.0\t3\t0.8\n"
    df = parse_pasted_sector_matrix(text)
    assert list(df["Sector"]) == ["Energy", "Utilities"]
    assert df.loc[0, "Benchmark Weight (%)"] == 4.0
    assert df.loc[1, "Benchmark Return (%)"] == 0.8


def test_paste_csv_with_percent_signs_strips_them():
    text = "Sector,Portfolio Weight (%),Portfolio Return (%),Benchmark Weight (%),Benchmark Return (%)\nFinancials,10%,5.5%,12%,4.0%\n"
    df = parse_pasted_sector_matrix(text)
    assert df.loc[0, "Portfolio Weight (%)"] == 10.0
    assert df.loc[0, "Portfolio Return (%)"] == 5.5


def test_paste_empty_raises():
    with pytest.raises(ValueError):
        parse_pasted_sector_matrix("")


def test_paste_wrong_column_count_raises():
    with pytest.raises(ValueError):
        parse_pasted_sector_matrix("Sector,Weight\nFinancials,10")


# ---------------------------------------------------------------------------
# build_brinson_fachler_inputs — units must be fractions, not percents
# ---------------------------------------------------------------------------

def test_build_inputs_converts_percent_to_fraction():
    editor = pd.DataFrame(
        [
            {"Sector": "Tech",      "Portfolio Weight (%)": 60.0, "Portfolio Return (%)": 10.0,
             "Benchmark Weight (%)": 50.0, "Benchmark Return (%)": 8.0},
            {"Sector": "Financials", "Portfolio Weight (%)": 40.0, "Portfolio Return (%)": 4.0,
             "Benchmark Weight (%)": 50.0, "Benchmark Return (%)": 5.0},
        ]
    )
    p_df, b_df = build_brinson_fachler_inputs(editor)
    # Editor stores percents; engine consumes fractions → divide by 100.
    assert p_df.loc[0, "portfolio_weight"] == pytest.approx(0.60)
    assert p_df.loc[0, "portfolio_return"] == pytest.approx(0.10)
    assert b_df.loc[1, "benchmark_weight"] == pytest.approx(0.50)
    assert b_df.loc[1, "benchmark_return"] == pytest.approx(0.05)


def test_build_inputs_rejects_empty_editor():
    with pytest.raises(ValueError):
        build_brinson_fachler_inputs(pd.DataFrame())


# ---------------------------------------------------------------------------
# validate_brinson_fachler_weights
# ---------------------------------------------------------------------------

def test_validation_clean_when_weights_sum_to_100():
    editor = pd.DataFrame(
        [
            {"Sector": "A", "Portfolio Weight (%)": 60.0, "Portfolio Return (%)": 1.0,
             "Benchmark Weight (%)": 40.0, "Benchmark Return (%)": 1.0},
            {"Sector": "B", "Portfolio Weight (%)": 40.0, "Portfolio Return (%)": 1.0,
             "Benchmark Weight (%)": 60.0, "Benchmark Return (%)": 1.0},
        ]
    )
    assert validate_brinson_fachler_weights(editor) == []


def test_validation_warns_when_weights_dont_sum_to_100():
    editor = pd.DataFrame(
        [
            {"Sector": "A", "Portfolio Weight (%)": 40.0, "Portfolio Return (%)": 0.0,
             "Benchmark Weight (%)": 40.0, "Benchmark Return (%)": 0.0},
        ]
    )
    warnings = validate_brinson_fachler_weights(editor)
    # Both portfolio (40) and benchmark (40) should be flagged.
    assert any("Portfolio weights sum to 40" in w for w in warnings)
    assert any("Benchmark weights sum to 40" in w for w in warnings)


def test_validation_warns_on_negative_weights():
    editor = pd.DataFrame(
        [
            {"Sector": "A", "Portfolio Weight (%)": 110.0, "Portfolio Return (%)": 0.0,
             "Benchmark Weight (%)":  100.0, "Benchmark Return (%)": 0.0},
            {"Sector": "B", "Portfolio Weight (%)": -10.0, "Portfolio Return (%)": 0.0,
             "Benchmark Weight (%)":    0.0, "Benchmark Return (%)": 0.0},
        ]
    )
    warnings = validate_brinson_fachler_weights(editor)
    assert any("Negative values found in 'Portfolio Weight (%)'" in w for w in warnings)


# ---------------------------------------------------------------------------
# compute_brinson_fachler — end-to-end against EvaluationEngine
# ---------------------------------------------------------------------------

def test_compute_attribution_sum_equals_active_return_within_tol():
    """The engine returns a per-sector decomposition. The sum of (allocation +
    selection + interaction) across sectors must equal the active return (the
    engine itself logs a warning if drift > 1e-5)."""
    editor = pd.DataFrame(
        [
            {"Sector": "Tech",       "Portfolio Weight (%)": 60.0, "Portfolio Return (%)": 12.0,
             "Benchmark Weight (%)": 50.0, "Benchmark Return (%)": 10.0},
            {"Sector": "Financials", "Portfolio Weight (%)": 25.0, "Portfolio Return (%)":  3.0,
             "Benchmark Weight (%)": 30.0, "Benchmark Return (%)":  4.0},
            {"Sector": "Energy",     "Portfolio Weight (%)": 15.0, "Portfolio Return (%)":  7.0,
             "Benchmark Weight (%)": 20.0, "Benchmark Return (%)":  5.0},
        ]
    )
    result = compute_brinson_fachler(editor)

    # Top-line fields are present.
    for key in (
        "Portfolio Return", "Benchmark Return", "Active Return",
        "Allocation Effect", "Selection Effect", "Interaction Effect",
        "Attribution Sum", "Sector Details",
    ):
        assert key in result, f"Missing key {key}"

    # Active return = portfolio - benchmark.
    assert math.isclose(
        result["Active Return"],
        result["Portfolio Return"] - result["Benchmark Return"],
        rel_tol=1e-9, abs_tol=1e-9,
    )
    # Attribution sum ≈ active return.
    assert math.isclose(result["Attribution Sum"], result["Active Return"], abs_tol=1e-6)

    # Per-sector entries are dictionaries with the documented schema.
    details = result["Sector Details"]
    assert set(details.keys()) == {"Tech", "Financials", "Energy"}
    expected_keys = {
        "weight_p", "weight_b", "return_p", "return_b",
        "allocation_effect", "selection_effect",
        "interaction_effect", "total_attribution",
    }
    for sector, row in details.items():
        assert expected_keys.issubset(row.keys()), f"Sector {sector} missing keys"


def test_compute_attribution_overweight_tech_drives_positive_allocation():
    """Sanity: overweighting an outperforming sector should produce a
    positive allocation effect for that sector — a standard textbook
    Brinson-Fachler property."""
    editor = pd.DataFrame(
        [
            {"Sector": "Tech",  "Portfolio Weight (%)": 70.0, "Portfolio Return (%)": 10.0,
             "Benchmark Weight (%)": 50.0, "Benchmark Return (%)": 10.0},
            {"Sector": "Bonds", "Portfolio Weight (%)": 30.0, "Portfolio Return (%)":  2.0,
             "Benchmark Weight (%)": 50.0, "Benchmark Return (%)":  2.0},
        ]
    )
    result = compute_brinson_fachler(editor)
    tech = result["Sector Details"]["Tech"]
    # Tech outperforms benchmark total return; we are overweight Tech → positive allocation.
    assert tech["allocation_effect"] > 0
