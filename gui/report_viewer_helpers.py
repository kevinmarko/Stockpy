"""Pure data-shaping helpers extracted from ``gui/panels/report_viewer.py``.

Everything in this module is deliberately **pure**: no ``streamlit`` calls, no
disk / network side effects, no ``st.session_state`` reads. Each function takes
plain inputs (DataFrames, dicts, lists, scalars) and returns plain outputs, so
it can be unit-tested without spinning up Streamlit (see
``tests/test_report_viewer_helpers.py``).

The Streamlit render code stays in ``gui/panels/report_viewer.py`` and calls
these helpers where the inline data-shaping logic used to live. This split
follows the repo convention documented in ``docs/test_coverage_analysis.md``:
"extract pure data-shaping helpers and unit-test them, leaving ``st.*`` render
to ``safe_panel`` integration."

Honesty rules preserved from the original inline code (CONSTRAINT #4 / #6):
missing values degrade to explicit placeholders (``"—"`` / ``NaN``), never a
fabricated ``0.0``; nothing here raises on empty / degraded input except the
Brinson-Fachler input builders, which deliberately ``ValueError`` on empty
frames exactly as they did when they lived in the panel module.
"""

from __future__ import annotations

import io
import math
from typing import Any, Dict, List, Tuple

import pandas as pd

# NOTE: ``GICS_SECTORS`` / ``_BF_EDITOR_COLUMNS`` are imported lazily *inside*
# the functions that need them rather than at module top. Importing
# ``gui.panels._shared`` runs the ``gui.panels`` package ``__init__``, which
# imports ``report_viewer``, which imports THIS module — a top-level import
# here would be a circular import whenever this module is loaded first (e.g.
# directly from a test). The lazy import resolves cleanly because by call time
# both modules are fully initialised.


# ===========================================================================
# Brinson-Fachler attribution — pure input shaping + engine bridge
# ===========================================================================


def default_brinson_fachler_frame() -> pd.DataFrame:
    """Return the seed editor DataFrame (GICS 11 sectors, zero weights/returns).

    Kept as a separate factory function so tests can construct the same shape
    the UI starts with without spinning up Streamlit.
    """
    from gui.panels._shared import GICS_SECTORS, _BF_EDITOR_COLUMNS

    rows = [
        {
            "Sector": s,
            "Portfolio Weight (%)": 0.0,
            "Portfolio Return (%)": 0.0,
            "Benchmark Weight (%)": 0.0,
            "Benchmark Return (%)": 0.0,
        }
        for s in GICS_SECTORS
    ]
    return pd.DataFrame(rows, columns=list(_BF_EDITOR_COLUMNS))


def parse_pasted_sector_matrix(text: str) -> pd.DataFrame:
    """Parse a TSV / CSV block pasted from a spreadsheet into the editor shape.

    The function accepts either:

    *   A 5-column matrix with a header row whose names match (case-insensitive,
        whitespace-tolerant) :data:`_BF_EDITOR_COLUMNS`.
    *   A 5-column matrix with no header (positional: sector, p_w, p_r, b_w,
        b_r).

    Values are coerced to float; missing / unparseable cells become ``0.0`` so
    the engine never sees ``NaN`` (the engine fills NaN to 0 internally too,
    but normalizing up front gives a clean editor view).

    Raises
    ------
    ValueError
        On unrecognised column counts or completely empty input.
    """
    from gui.panels._shared import _BF_EDITOR_COLUMNS

    if not text or not text.strip():
        raise ValueError("Pasted text is empty.")

    # Detect delimiter — spreadsheet copies are usually TSV; fall back to CSV.
    sample = text.strip().splitlines()[0]
    delim = "\t" if "\t" in sample else ","

    # Header detection: pandas would happily promote the first data row to the
    # header, dropping a real data row in the header-less case. Sniff the first
    # line directly: if columns 2..5 parse as floats, it's data, not a header.
    first_cells = [c.strip().replace("%", "") for c in sample.split(delim)]
    has_header = True
    if len(first_cells) >= 5:
        try:
            for cell in first_cells[1:5]:
                float(cell)
            has_header = False  # all numeric → first row is data
        except ValueError:
            has_header = True

    header_arg = 0 if has_header else None
    df = pd.read_csv(io.StringIO(text), sep=delim, dtype=str,
                     engine="python", header=header_arg)

    if df.shape[1] != 5:
        raise ValueError(
            f"Expected 5 columns (Sector, P-Weight, P-Return, B-Weight, B-Return); "
            f"got {df.shape[1]}."
        )

    if not has_header:
        df.columns = list(_BF_EDITOR_COLUMNS)
    else:
        # Header present — normalise column names by lowercase comparison.
        canonical = {c.lower().strip(): c for c in _BF_EDITOR_COLUMNS}
        renamed: Dict[str, str] = {}
        for c in df.columns:
            key = str(c).lower().strip()
            if key in canonical:
                renamed[c] = canonical[key]
        df = df.rename(columns=renamed)
        # If after renaming we still don't have all canonical columns, treat
        # the input as positional anyway.
        if not set(_BF_EDITOR_COLUMNS).issubset(df.columns):
            df.columns = list(_BF_EDITOR_COLUMNS)

    # Coerce numerics; non-parsable strings -> 0.0
    for c in _BF_EDITOR_COLUMNS[1:]:
        df[c] = pd.to_numeric(df[c].astype(str).str.replace("%", "", regex=False),
                              errors="coerce").fillna(0.0)
    df["Sector"] = df["Sector"].astype(str).str.strip()
    df = df[df["Sector"] != ""]  # drop blank rows
    if df.empty:
        raise ValueError("No data rows found after parsing.")
    return df[list(_BF_EDITOR_COLUMNS)].reset_index(drop=True)


def build_brinson_fachler_inputs(
    editor_df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Split the editor frame into the (portfolio_df, benchmark_df) shape the
    engine's DataFrame-compat path consumes.

    Percentages in the editor are converted to fractions (``/ 100.0``) so the
    engine's allocation/selection arithmetic is unit-consistent (the engine
    multiplies weights × returns without rescaling).

    The DataFrames carry the explicit ``portfolio_weight`` / ``portfolio_return``
    and ``benchmark_weight`` / ``benchmark_return`` column names that
    ``EvaluationEngine._calculate_brinson_fachler_compat`` looks up — passing
    the explicit shape exercises the engine's name-mapping branch deterministically.
    """
    from gui.panels._shared import _BF_EDITOR_COLUMNS

    if editor_df is None or editor_df.empty:
        raise ValueError("Sector editor is empty.")

    df = editor_df.copy()
    for c in _BF_EDITOR_COLUMNS:
        if c not in df.columns:
            raise ValueError(f"Editor frame missing required column: {c}")

    df["Sector"] = df["Sector"].astype(str).str.strip()
    df = df[df["Sector"] != ""].copy()
    if df.empty:
        raise ValueError("Sector editor has no non-empty rows.")

    portfolio_df = pd.DataFrame({
        "sector": df["Sector"],
        "portfolio_weight": pd.to_numeric(df["Portfolio Weight (%)"], errors="coerce").fillna(0.0) / 100.0,
        "portfolio_return": pd.to_numeric(df["Portfolio Return (%)"], errors="coerce").fillna(0.0) / 100.0,
    })
    benchmark_df = pd.DataFrame({
        "sector": df["Sector"],
        "benchmark_weight": pd.to_numeric(df["Benchmark Weight (%)"], errors="coerce").fillna(0.0) / 100.0,
        "benchmark_return": pd.to_numeric(df["Benchmark Return (%)"], errors="coerce").fillna(0.0) / 100.0,
    })
    return portfolio_df, benchmark_df


def compute_brinson_fachler(editor_df: pd.DataFrame) -> Dict[str, Any]:
    """Run :class:`EvaluationEngine.calculate_brinson_fachler` on editor input.

    Returns the engine's structured dict unchanged so the UI and tests share
    one canonical result shape — ``Portfolio Return``, ``Benchmark Return``,
    ``Active Return``, ``Allocation Effect``, ``Selection Effect``,
    ``Interaction Effect``, ``Attribution Sum``, and ``Sector Details``.
    """
    from evaluation_engine import EvaluationEngine

    portfolio_df, benchmark_df = build_brinson_fachler_inputs(editor_df)
    engine = EvaluationEngine()
    return engine.calculate_brinson_fachler(portfolio_df, benchmark_df)


def validate_brinson_fachler_weights(
    editor_df: pd.DataFrame,
    *,
    tolerance_pct: float = 1.0,
) -> List[str]:
    """Return a list of human-readable validation warnings (empty when clean).

    Checks:
      * portfolio weights sum to ~100% (within ``tolerance_pct``);
      * benchmark weights sum to ~100%;
      * no negative weights (the engine itself does not forbid them, but
        negative sector weights almost always indicate a data-entry error in
        long-only attribution).
    """
    warnings: List[str] = []
    if editor_df is None or editor_df.empty:
        return ["Sector editor is empty."]

    p_sum = float(pd.to_numeric(editor_df.get("Portfolio Weight (%)", 0), errors="coerce").fillna(0.0).sum())
    b_sum = float(pd.to_numeric(editor_df.get("Benchmark Weight (%)", 0), errors="coerce").fillna(0.0).sum())
    if abs(p_sum - 100.0) > tolerance_pct:
        warnings.append(f"Portfolio weights sum to {p_sum:.2f}% (expected ~100%).")
    if abs(b_sum - 100.0) > tolerance_pct:
        warnings.append(f"Benchmark weights sum to {b_sum:.2f}% (expected ~100%).")

    for col in ("Portfolio Weight (%)", "Benchmark Weight (%)"):
        if col in editor_df.columns:
            neg = pd.to_numeric(editor_df[col], errors="coerce").fillna(0.0) < 0
            if neg.any():
                warnings.append(f"Negative values found in '{col}' — long-only attribution typically requires non-negative weights.")
    return warnings


def shape_sector_details_frame(sector_details: Dict[str, Any]) -> pd.DataFrame:
    """Convert the engine's ``Sector Details`` dict into an ordered display frame.

    ``sector_details`` maps each sector name to a per-sector effects dict. The
    returned frame has a leading ``sector`` column followed by whichever of the
    preferred effect columns are actually present (extra columns are dropped
    from the ordering but never fabricated). An empty / falsy input returns an
    empty DataFrame rather than raising (CONSTRAINT #6).
    """
    if not sector_details:
        return pd.DataFrame()

    sector_df = pd.DataFrame.from_dict(sector_details, orient="index").reset_index()
    sector_df = sector_df.rename(columns={"index": "sector"})
    # Pretty column order for display.
    preferred = [
        "sector", "weight_p", "weight_b", "return_p", "return_b",
        "allocation_effect", "selection_effect",
        "interaction_effect", "total_attribution",
    ]
    ordered = [c for c in preferred if c in sector_df.columns]
    return sector_df[ordered]


# ===========================================================================
# Tactical ranges (Buy Zone / Sell Zone) — Reports tab
# ===========================================================================


def build_tactical_ranges_frame(signals: List[dict]) -> pd.DataFrame:
    """Build the Buy Range / Sell Range side-by-side table from raw signals.

    ``buy_range`` / ``sell_range`` degrade to ``"—"`` when absent (never
    fabricated). The ``Suggested Exit %`` column is only shown for ``SELL``
    signals with a positive suggested exit fraction; every other row shows
    ``"—"``. Returns an empty DataFrame for an empty signal list.
    """
    range_rows = [
        {
            "Symbol": s.get("symbol", "—"),
            "Action": s.get("action", "—"),
            "Buy Range": s.get("buy_range") or "—",
            "Sell Range": s.get("sell_range") or "—",
            "Suggested Exit %": (
                f"{float(s.get('suggested_exit_pct') or 0.0):.0%}"
                if s.get("action") == "SELL" and float(s.get("suggested_exit_pct") or 0.0) > 0
                else "—"
            ),
        }
        for s in signals
    ]
    return pd.DataFrame(range_rows)


# ===========================================================================
# Multifactor / cross-sectional momentum hidden fields — Reports tab
# ===========================================================================


def build_hidden_fields_frame(signals: List[dict]) -> Tuple[pd.DataFrame, bool]:
    """Shape the multifactor z-score / xsec-momentum surfacing table.

    Returns ``(factor_df, any_populated)``. ``any_populated`` is ``True`` when at
    least one non-symbol factor cell carried a real (non-``None``) value in the
    snapshot; the caller uses it to decide between rendering the table and
    showing the "no cross-sectional data" caption. Missing cells render as
    ``"—"`` — never a fabricated ``0.0`` (CONSTRAINT #4).
    """
    factor_cols = [
        ("symbol", "Symbol"),
        ("value_z", "Value Z"),
        ("quality_z", "Quality Z"),
        ("lowvol_z", "LowVol Z"),
        ("size_z", "Size Z"),
        ("multifactor_composite", "Multifactor Composite"),
        ("xsec_12_1m", "XSec 12-1M Return"),
        ("xsec_momentum_rank", "XSec Momentum Rank"),
    ]
    rows = []
    any_populated = False
    for s in signals:
        row = {}
        for key, label in factor_cols:
            v = s.get(key)
            if key != "symbol" and v is not None:
                any_populated = True
            row[label] = v if v is not None else ("—" if key != "symbol" else s.get("symbol", "—"))
        rows.append(row)

    return pd.DataFrame(rows), any_populated


# ===========================================================================
# Trade quality — MFE vs MAE scatter frame — Reports tab
# ===========================================================================


def build_mfe_mae_scatter_frame(signals: List[dict]) -> pd.DataFrame:
    """Build the per-symbol MFE/MAE scatter frame from raw signals.

    Rows without both ``mfe`` and ``mae`` are dropped (``dropna``) so the
    scatter never plots a fabricated origin point for a symbol that has no
    excursion data yet. Column order is fixed so an all-empty snapshot still
    yields a correctly-shaped (empty) frame.
    """
    scatter_rows = [
        {
            "symbol": s.get("symbol", "?"),
            "mfe": s.get("mfe"),
            "mae": s.get("mae"),
            "edge_ratio": s.get("edge_ratio"),
            "conviction": s.get("advisory_conviction"),
            "action": s.get("action") or s.get("advisory_action") or "—",
        }
        for s in signals
    ]
    return pd.DataFrame(
        scatter_rows,
        columns=["symbol", "mfe", "mae", "edge_ratio", "conviction", "action"],
    ).dropna(subset=["mfe", "mae"])


# ===========================================================================
# Recommendation tracking — pure formatters — Reports tab
# ===========================================================================


def format_tracking_pct(v: float) -> str:
    """Format a return fraction as a signed percentage, ``"—"`` when NaN."""
    return "—" if math.isnan(v) else f"{v * 100:+.2f}%"


def tracking_delta_label(d: float) -> str:
    """Label the operator-vs-model return delta with a verdict badge.

    Mirrors the original inline thresholds exactly: ``> +0.5%`` adds value,
    ``< -0.5%`` costs alpha, in-between is neutral, NaN is insufficient data.
    """
    if math.isnan(d):
        return "— (insufficient data)"
    if d > 0.005:
        return f"{d * 100:+.2f}% ✅ judgment adds value"
    if d < -0.005:
        return f"{d * 100:+.2f}% ⚠️ judgment costs alpha"
    return f"{d * 100:+.2f}% ≈ neutral"


# ===========================================================================
# Conviction calibration — pure summary stats — Reports tab
# ===========================================================================


def calibration_summary_stats(cal_df: pd.DataFrame) -> Dict[str, Any]:
    """Compute the reliability-diagram summary tiles from a calibration frame.

    ``cal_df`` carries ``count`` / ``win_rate`` / ``bin_center`` columns (the
    shape ``evaluation_engine.calibration_curve`` returns). Returns a dict with:

    * ``total`` — total trades carrying a conviction annotation.
    * ``overall_win_rate`` — count-weighted win rate (``NaN`` when no trades).
    * ``calibration_error`` — MAE of ``|win_rate − bin_center|`` over scored
      bins (``NaN`` when no bins have data).
    * ``n_scored_bins`` — number of bins with a non-null ``win_rate``.

    ``NaN`` — never a fabricated ``0.0`` — is used for undefined statistics.
    """
    scored = cal_df.dropna(subset=["win_rate"])
    total = int(cal_df["count"].sum()) if "count" in cal_df.columns else 0
    if total > 0 and "win_rate" in cal_df.columns and "count" in cal_df.columns:
        total_wins = float((cal_df["win_rate"].fillna(0) * cal_df["count"]).sum())
        overall_wr = total_wins / total
    else:
        overall_wr = float("nan")
    if not scored.empty:
        cal_error = float((scored["win_rate"] - scored["bin_center"]).abs().mean())
    else:
        cal_error = float("nan")
    return {
        "total": total,
        "overall_win_rate": overall_wr,
        "calibration_error": cal_error,
        "n_scored_bins": len(scored),
    }


# ===========================================================================
# Correlation clusters — pure assignment + concentration shaping — Reports tab
# ===========================================================================


def build_cluster_assignment_frame(
    labels: Dict[str, int], sig_map: Dict[str, dict]
) -> pd.DataFrame:
    """Build the Symbol → Cluster assignment table.

    ``labels`` maps each symbol to a cluster id (``0`` = unclustered / noise,
    rendered as ``"—"``). ``sig_map`` maps upper-cased symbol → its signal dict
    (used for the Action + Kelly Target columns). A missing Kelly Target
    renders ``"—"`` rather than a fabricated ``0%``.
    """
    rows = []
    for sym in sorted(labels):
        cid = labels[sym]
        sig = sig_map.get(sym, {})
        kelly = float(sig.get("kelly_target", 0.0) or 0.0)
        action = str(sig.get("action", sig.get("action_signal", "—")))
        rows.append({
            "Symbol": sym,
            "Cluster": cid if cid != 0 else "—",
            "Action": action,
            "Kelly Target": f"{kelly:.1%}" if kelly else "—",
        })
    return pd.DataFrame(rows).sort_values(["Cluster", "Symbol"])


def build_cluster_concentration_rows(
    summary: pd.DataFrame, sig_map: Dict[str, dict]
) -> List[dict]:
    """Build per-cluster concentration rows (sum of Kelly Targets per cluster).

    ``summary`` is the cluster-summary frame (``cluster_id`` / ``symbols`` /
    ``n_symbols`` / ``avg_intra_corr`` columns). Each returned dict carries the
    cluster's total Kelly-weighted position and average intra-cluster
    correlation (``"—"`` when the correlation is ``NaN``, never fabricated).
    Returns ``[]`` for an empty summary.
    """
    conc_rows: List[dict] = []
    if summary is None or summary.empty:
        return conc_rows
    for _, row in summary.iterrows():
        cid = int(row["cluster_id"])
        cluster_syms = row["symbols"] if isinstance(row["symbols"], list) else []
        total_kelly = sum(
            float(sig_map.get(s, {}).get("kelly_target", 0.0) or 0.0)
            for s in cluster_syms
        )
        avg_corr = row.get("avg_intra_corr", float("nan"))
        conc_rows.append({
            "Cluster ID": cid,
            "Symbols": ", ".join(cluster_syms),
            "Count": int(row["n_symbols"]),
            "Total Position %": f"{total_kelly:.1%}",
            "Avg |Corr|": f"{avg_corr:.2f}" if avg_corr == avg_corr else "—",
        })
    return conc_rows


def heavy_concentration_clusters(
    conc_rows: List[dict], *, threshold: float = 0.30
) -> List[dict]:
    """Return the subset of concentration rows exceeding ``threshold`` weight.

    ``conc_rows`` is the output of :func:`build_cluster_concentration_rows`; the
    ``"Total Position %"`` string (e.g. ``"41.0%"``) is parsed back to a
    fraction and compared to ``threshold`` (default 30%). Used to drive the
    "high concentration" warning.
    """
    return [
        r for r in conc_rows
        if float(r["Total Position %"].strip("%")) / 100 > threshold
    ]
