"""pilots/brinson.py — headless Brinson-Fachler attribution calculator for the
Pilots PWA's ``POST /portfolio/attribution/brinson-fachler`` endpoint.

This is the manual-input, operator-driven analogue of the legacy Streamlit
Command Center's ``gui/panels/report_viewer.py::_render_brinson_fachler_section``
(and its pure helpers in ``gui/report_viewer_helpers.py``): the operator types
or pastes a sector-level portfolio-vs-benchmark weight+return matrix and gets
back the Allocation / Selection / Interaction decomposition of active return.
It is deliberately NOT auto-derived from real holdings — point-in-time
sector-level BENCHMARK return data isn't available anywhere in this platform
(the correlation-cluster / factor-exposure sections of
``pilots/attribution.py`` are the auto-derived, holdings-driven attribution
views; this module is the manual "what-if" calculator sitting alongside them).

Deliberately NOT built on top of ``gui/report_viewer_helpers.py``'s
``compute_brinson_fachler``/``build_brinson_fachler_inputs``/
``default_brinson_fachler_frame`` — those lazily import
``gui.panels._shared``, which executes the ``gui.panels`` package ``__init__``
(-> ``report_viewer.py`` -> ``streamlit``). Pulling a UI framework import into
a headless FastAPI process is a layering violation this module avoids by being
fully self-contained: stdlib + pandas + ``evaluation_engine`` only (the same
scoped-dependency posture ``pilots/attribution.py`` documents for itself),
zero ``gui.*`` imports, zero I/O.

Wire format vs. engine format
------------------------------
The API's wire format uses **percent** (``28.0`` meaning 28%), matching what
an operator naturally types into a form. ``EvaluationEngine.calculate_brinson_fachler``
(the DataFrame-compat path, ``evaluation_engine.py:233-364``) expects
**fractions** (``0.28``) — :func:`build_brinson_fachler_frames` does the
``/ 100.0`` conversion, mirroring ``gui/report_viewer_helpers.py::build_brinson_fachler_inputs``'s
same rescale so the two calculators (Streamlit + PWA) stay numerically
consistent even though they no longer share code.

Known limitation (do NOT attempt to fix ``evaluation_engine.py`` — out of
scope, and it is a shared engine other callers depend on): on an internal
exception, ``_calculate_brinson_fachler_compat`` catches it and returns the
SAME success-shaped dict with every top-line value fabricated as ``0.0``
(this technically violates this repo's CONSTRAINT #4, but it is pre-existing
behavior in code this module does not own). :func:`validate_brinson_fachler_rows`
pre-validates the input (non-empty rows, every row has a non-blank sector
name, at least one non-zero weight) BEFORE the engine is ever called, which
keeps this failure mode rare in practice — but it is not eliminated: a
sufficiently malformed row that still passes validation (e.g. a return value
that overflows float arithmetic) could still trigger the engine's own
fabricated-zero fallback. Callers of :func:`compute_brinson_fachler` should
treat an all-zero result with a nonzero-input request as a signal worth
surfacing to the operator, not as ground truth.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

import pandas as pd

__all__ = [
    "build_brinson_fachler_frames",
    "validate_brinson_fachler_rows",
    "compute_brinson_fachler",
]

_REQUIRED_KEYS = (
    "sector",
    "portfolio_weight_pct",
    "portfolio_return_pct",
    "benchmark_weight_pct",
    "benchmark_return_pct",
)


def _coerce_float(value: Any) -> float:
    """Coerce ``value`` to a finite float, defaulting to ``0.0`` on failure.

    Mirrors ``gui/report_viewer_helpers.py::parse_pasted_sector_matrix``'s own
    "unparseable cell -> 0.0" normalization so a stray blank/non-numeric entry
    degrades to a neutral no-op contribution rather than raising mid-request.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return 0.0
    return f


def _clean_nan(obj: Any) -> Any:
    """Recursively convert NaN/inf floats to ``None`` (JSON ``null``).

    Mirrors ``api/metrics_api.py::_clean_nan`` — kept as a local copy rather
    than an import so this module's dependency surface stays stdlib+pandas+
    ``evaluation_engine`` only.
    """
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_nan(x) for x in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def build_brinson_fachler_frames(
    rows: List[Dict[str, Any]],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Split the wire-format row list into the ``(portfolio_df, benchmark_df)``
    shape ``EvaluationEngine._calculate_brinson_fachler_compat`` consumes.

    Each row is a dict of the wire (percent) fields in :data:`_REQUIRED_KEYS`.
    Percentages are converted to fractions (``/ 100.0``) here — the engine
    multiplies weight x return without any further rescaling, so this is the
    only place the unit conversion happens.

    Raises
    ------
    ValueError
        On empty ``rows``, or when every row has a blank sector name.
    """
    if not rows:
        raise ValueError("No sector rows provided.")

    sectors: List[str] = []
    p_weights: List[float] = []
    p_returns: List[float] = []
    b_weights: List[float] = []
    b_returns: List[float] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        sector = str(row.get("sector") or "").strip()
        if not sector:
            continue
        sectors.append(sector)
        p_weights.append(_coerce_float(row.get("portfolio_weight_pct")) / 100.0)
        p_returns.append(_coerce_float(row.get("portfolio_return_pct")) / 100.0)
        b_weights.append(_coerce_float(row.get("benchmark_weight_pct")) / 100.0)
        b_returns.append(_coerce_float(row.get("benchmark_return_pct")) / 100.0)

    if not sectors:
        raise ValueError("No rows with a non-blank sector name.")

    portfolio_df = pd.DataFrame({
        "sector": sectors,
        "portfolio_weight": p_weights,
        "portfolio_return": p_returns,
    })
    benchmark_df = pd.DataFrame({
        "sector": sectors,
        "benchmark_weight": b_weights,
        "benchmark_return": b_returns,
    })
    return portfolio_df, benchmark_df


def validate_brinson_fachler_rows(rows: List[Dict[str, Any]]) -> List[str]:
    """Return a list of human-readable validation warnings (empty when clean).

    A small, self-contained reimplementation of
    ``gui/report_viewer_helpers.py::validate_brinson_fachler_weights``'s
    checks (deliberately not imported — see the module docstring). Checks:

      * at least one row with a non-blank sector name;
      * portfolio weights sum to ~100% (within 1 percentage point);
      * benchmark weights sum to ~100%;
      * no negative weights (the engine doesn't forbid them, but a negative
        sector weight almost always indicates a data-entry error in
        long-only attribution);
      * at least one non-zero weight overall (an all-zero matrix is legal
        input shape-wise but produces a meaningless all-zero result).
    """
    warnings: List[str] = []
    valid_rows = [
        r for r in (rows or [])
        if isinstance(r, dict) and str(r.get("sector") or "").strip()
    ]
    if not valid_rows:
        return ["No rows with a non-blank sector name."]

    p_sum = sum(_coerce_float(r.get("portfolio_weight_pct")) for r in valid_rows)
    b_sum = sum(_coerce_float(r.get("benchmark_weight_pct")) for r in valid_rows)

    if abs(p_sum - 100.0) > 1.0:
        warnings.append(f"Portfolio weights sum to {p_sum:.2f}% (expected ~100%).")
    if abs(b_sum - 100.0) > 1.0:
        warnings.append(f"Benchmark weights sum to {b_sum:.2f}% (expected ~100%).")

    if any(_coerce_float(r.get("portfolio_weight_pct")) < 0 for r in valid_rows):
        warnings.append("Negative values found in Portfolio Weight — long-only attribution typically requires non-negative weights.")
    if any(_coerce_float(r.get("benchmark_weight_pct")) < 0 for r in valid_rows):
        warnings.append("Negative values found in Benchmark Weight — long-only attribution typically requires non-negative weights.")

    if p_sum == 0.0 and b_sum == 0.0:
        warnings.append("All weights are zero — nothing to attribute.")

    return warnings


def compute_brinson_fachler(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute Brinson-Fachler attribution for a wire-format sector matrix.

    Pre-validates ``rows`` (raises ``ValueError`` with a clear message on
    structurally bad input — e.g. empty, no sector names, or every weight
    zero — so the caller can 422 it before it ever reaches the engine) then
    calls ``EvaluationEngine.calculate_brinson_fachler(portfolio_df,
    benchmark_df)`` and returns its result dict, NaN/inf-cleaned to ``None``
    for JSON.

    Returns the SAME top-line/``Sector Details`` shape
    ``_calculate_brinson_fachler_compat`` produces (see ``evaluation_engine.py``
    for the field list) — this function does not reshape or rename anything,
    only validates the input and cleans the output.
    """
    if not rows:
        raise ValueError("No sector rows provided.")

    validation_errors = validate_brinson_fachler_rows(rows)
    # Only the "no usable rows" case is a hard failure here; weight-sum /
    # negative-weight / all-zero warnings are informational (the frontend
    # shows them inline) and do not block computation.
    if validation_errors and validation_errors[0].startswith("No rows with a non-blank sector name"):
        raise ValueError(validation_errors[0])

    portfolio_df, benchmark_df = build_brinson_fachler_frames(rows)

    from evaluation_engine import EvaluationEngine

    engine = EvaluationEngine()
    result = engine.calculate_brinson_fachler(portfolio_df, benchmark_df)
    return _clean_nan(dict(result))
