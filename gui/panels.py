"""
gui/panels.py
=============
Render functions for the InvestYo Command Center, one per tab.  Each public
``render_*`` function is wrapped by :func:`safe_panel` in ``gui/app.py`` so a
failure in any single panel surfaces as an inline error box rather than
crashing the whole app (dead-letter UI pattern, CONSTRAINT #6).

The panels deliberately avoid live async broker calls.  They read the
orchestrator's file-backed state (``output/state_snapshot.json`` etc.) and call
the platform's existing synchronous engines directly:

*   evaluation/research analytics  → ``evaluation_engine`` / ``research_engine``
*   signal registry + weights      → ``signals.registry`` / ``settings.SIGNAL_WEIGHTS``
*   kill switch                     → ``execution.kill_switch.GlobalKillSwitch``
*   options greeks / IVR            → ``technical_options_engine``
*   account state (RH)              → ``data.robinhood_portfolio`` (account only)
*   prices / fundamentals           → ``data.market_data.get_provider`` (markets only)

Source-of-truth separation (CONSTRAINT #4) is enforced visually: the
Paper-Trading Monitor labels every column with its origin so Robinhood account
state and market-data prices are never conflated.
"""

from __future__ import annotations

import io
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

from settings import settings
from gui import env_io, orchestrator_runner

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# GICS 11 sector seed for the Brinson-Fachler attribution editor. Choosing a
# fixed canonical list (rather than an empty grid) gives the operator a
# starting point that matches the way most public benchmarks publish their
# sector exposures, so the typical workflow is "tweak weights" rather than
# "type 11 sector names from scratch".
# ---------------------------------------------------------------------------
GICS_SECTORS: Tuple[str, ...] = (
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financials",
    "Health Care",
    "Industrials",
    "Information Technology",
    "Materials",
    "Real Estate",
    "Utilities",
)

# Canonical column names used by the editor table AND
# ``EvaluationEngine._calculate_brinson_fachler_compat`` (it accepts both
# ``portfolio_weight``/``portfolio_return`` and ``weight``/``return`` shapes;
# we hand it the explicit form so the column-rename layer is exercised
# deterministically).
_BF_EDITOR_COLUMNS: Tuple[str, ...] = (
    "Sector",
    "Portfolio Weight (%)",
    "Portfolio Return (%)",
    "Benchmark Weight (%)",
    "Benchmark Return (%)",
)


# ===========================================================================
# Shared file-backed loaders (cached) — mirror observability/dashboard.py
# ===========================================================================

@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def load_state_snapshot() -> dict:
    """Load the orchestrator's last ``state_snapshot.json`` (empty dict if absent)."""
    snap = settings.OUTPUT_DIR / "state_snapshot.json"
    if snap.exists():
        try:
            return json.loads(snap.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def load_block_log(n: int = 100) -> List[dict]:
    """Load the most recent ``n`` risk-gate block entries (newest first)."""
    log_path = settings.OUTPUT_DIR / "risk_gate_blocks.jsonl"
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        rows: List[dict] = []
        for line in lines[-n:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return list(reversed(rows))
    except Exception:
        return []


def _kill_switch():
    """Construct a GlobalKillSwitch pointed at the configured output dir."""
    from execution.kill_switch import GlobalKillSwitch

    return GlobalKillSwitch(sentinel_file=settings.OUTPUT_DIR / "KILL_SWITCH")


def _signal_symbols(snap: dict) -> List[str]:
    """Active symbols from the last snapshot, falling back to DEFAULT_TICKERS."""
    syms = [s.get("symbol") for s in snap.get("signals", []) if s.get("symbol")]
    if syms:
        return syms
    return list(settings.DEFAULT_TICKERS)


def _watchlist_symbols() -> List[str]:
    """Return tickers from ``WATCHLIST`` env var or repo-root ``watchlist.txt``.

    Mirrors :func:`main._load_watchlist` so the GUI's symbol discovery is
    consistent with the orchestrator's evaluation universe. Silently returns
    ``[]`` when neither source exists — never raises (CONSTRAINT #6).
    """
    import os

    env_val = os.environ.get("WATCHLIST", "").strip()
    if env_val:
        return [t.strip().upper() for t in env_val.split(",") if t.strip()]

    wl = _REPO_ROOT / "watchlist.txt"
    if wl.exists():
        try:
            return [
                line.strip().upper()
                for line in wl.read_text().splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("watchlist.txt read failed: %s", exc)
    return []


def _held_symbols() -> List[str]:
    """Robinhood-held tickers from a cached snapshot (if any).

    Reads ``cache/account_snapshot.json`` directly so the matrix tab doesn't
    trigger a live broker login. Returns ``[]`` if the cache is absent or
    unparseable.
    """
    cache = _REPO_ROOT / "cache" / "account_snapshot.json"
    if not cache.exists():
        return []
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        positions = data.get("positions", {})
        return sorted(positions.keys())
    except Exception as exc:  # noqa: BLE001
        logger.warning("account_snapshot.json read failed: %s", exc)
        return []


def _active_symbols(snap: dict) -> List[str]:
    """Union of held positions, watchlist, and last pipeline signals.

    Falls back to :data:`settings.DEFAULT_TICKERS` only when all three
    sources are empty — matches the Portfolio & Watchlist Synchronization
    contract documented in :mod:`main`.
    """
    universe: List[str] = []
    seen: set = set()
    for src in (_held_symbols(), _watchlist_symbols(), _signal_symbols(snap)):
        for s in src:
            if s not in seen:
                seen.add(s)
                universe.append(s)
    if not universe:
        return list(settings.DEFAULT_TICKERS)
    return universe


# ===========================================================================
# Brinson-Fachler attribution — pure helpers (testable without Streamlit)
# ===========================================================================

def default_brinson_fachler_frame() -> pd.DataFrame:
    """Return the seed editor DataFrame (GICS 11 sectors, zero weights/returns).

    Kept as a separate factory function so tests can construct the same shape
    the UI starts with without spinning up Streamlit.
    """
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


# ===========================================================================
# Brinson-Fachler attribution — Streamlit section (consumed by Reports tab)
# ===========================================================================

def _render_brinson_fachler_section() -> None:
    """Render the interactive Brinson-Fachler attribution UI.

    Layout (top → bottom):
      1. **Editable sector matrix** (``st.data_editor``) seeded with GICS 11 —
         operator types or pastes weights & returns directly.
      2. **Bulk paste** — a textarea accepting CSV or TSV from a spreadsheet,
         with a "Parse pasted data" button that replaces the editor contents.
      3. **Validation chips** — warn on weights that don't sum to ~100 % or any
         negative weight (long-only attribution convention).
      4. **Compute attribution** — runs
         :func:`compute_brinson_fachler` which delegates to
         ``EvaluationEngine.calculate_brinson_fachler``.
      5. **Result panel** — top-line metrics (portfolio/benchmark/active
         returns, allocation/selection/interaction effects), per-sector
         breakdown table, and an effects bar chart.  CSV download buttons let
         the operator persist the editor input and the per-sector breakdown.

    All editor + result state lives in ``st.session_state`` keys prefixed with
    ``bf_`` so swapping tabs doesn't lose work.
    """
    st.markdown("---")
    st.markdown("### 📊 Brinson-Fachler Attribution Analysis")
    st.caption(
        "Decompose active return into **allocation effect** (sector weighting) "
        "and **selection effect** (stock picking) via "
        "`EvaluationEngine.calculate_brinson_fachler`. Edit the matrix below, "
        "or bulk-paste TSV/CSV from a spreadsheet."
    )

    # ── 1. Editor frame in session state ─────────────────────────────────────
    if "bf_editor_df" not in st.session_state:
        st.session_state["bf_editor_df"] = default_brinson_fachler_frame()

    edited = st.data_editor(
        st.session_state["bf_editor_df"],
        key="bf_editor_widget",
        width="stretch",
        num_rows="dynamic",
        hide_index=True,
        column_config={
            "Sector": st.column_config.TextColumn(
                "Sector", required=True, help="Sector label (free-form)."
            ),
            "Portfolio Weight (%)": st.column_config.NumberColumn(
                "Portfolio Weight (%)", format="%.4f",
                help="Portfolio weight in this sector, in percent (0–100).",
            ),
            "Portfolio Return (%)": st.column_config.NumberColumn(
                "Portfolio Return (%)", format="%.4f",
                help="Portfolio return contributed by this sector, in percent.",
            ),
            "Benchmark Weight (%)": st.column_config.NumberColumn(
                "Benchmark Weight (%)", format="%.4f",
                help="Benchmark weight in this sector, in percent (0–100).",
            ),
            "Benchmark Return (%)": st.column_config.NumberColumn(
                "Benchmark Return (%)", format="%.4f",
                help="Benchmark return for this sector, in percent.",
            ),
        },
    )
    # Persist the latest edit so reruns survive.
    st.session_state["bf_editor_df"] = edited

    # ── 2. Bulk paste fallback ────────────────────────────────────────────────
    with st.expander("📋 Bulk paste from spreadsheet (TSV / CSV)"):
        st.caption(
            "Copy a 5-column block (Sector, P-Weight%, P-Return%, B-Weight%, "
            "B-Return%) from Excel / Google Sheets and paste here. The header "
            "row is optional."
        )
        pasted = st.text_area(
            "Paste data here", value="", height=140, key="bf_paste_area",
            placeholder="Sector\tPortfolio Weight (%)\tPortfolio Return (%)\tBenchmark Weight (%)\tBenchmark Return (%)\nInformation Technology\t28\t12.4\t26\t10.1",
        )
        c_paste, c_reset = st.columns(2)
        with c_paste:
            if st.button("📥 Parse pasted data", key="bf_paste_btn"):
                try:
                    parsed = parse_pasted_sector_matrix(pasted)
                    st.session_state["bf_editor_df"] = parsed
                    st.success(f"Parsed {len(parsed)} sector row(s) — editor refreshed.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001 - user-facing parse error
                    st.error(f"Could not parse pasted data: {exc}")
        with c_reset:
            if st.button("♻️ Reset to GICS 11 default", key="bf_reset_btn"):
                st.session_state["bf_editor_df"] = default_brinson_fachler_frame()
                st.rerun()

    # ── 3. Validation chips ───────────────────────────────────────────────────
    warnings = validate_brinson_fachler_weights(edited)
    if warnings:
        for w in warnings:
            st.warning(f"⚠️ {w}")
    else:
        st.success("✅ Weights validated (portfolio + benchmark each sum to ~100%).")

    # ── 4. Compute attribution ───────────────────────────────────────────────
    if st.button("▶️ Compute Brinson-Fachler attribution",
                 type="primary", key="bf_compute_btn"):
        try:
            result = compute_brinson_fachler(edited)
            st.session_state["bf_result"] = result
        except Exception as exc:  # noqa: BLE001 - surface engine error inline
            logger.exception("Brinson-Fachler attribution failed")
            st.error(f"Attribution failed: {exc}")
            st.session_state["bf_result"] = None

    # ── 5. Result panel ──────────────────────────────────────────────────────
    result = st.session_state.get("bf_result")
    if result:
        st.markdown("#### 📈 Attribution result")
        m1, m2, m3 = st.columns(3)
        m1.metric("Portfolio Return",  f"{float(result.get('Portfolio Return', 0.0))*100:.3f}%")
        m2.metric("Benchmark Return",  f"{float(result.get('Benchmark Return', 0.0))*100:.3f}%")
        m3.metric("Active Return",     f"{float(result.get('Active Return', 0.0))*100:.3f}%")

        e1, e2, e3, e4 = st.columns(4)
        e1.metric("Allocation Effect",
                  f"{float(result.get('Allocation Effect', 0.0))*100:.3f}%",
                  help="Active return from sector weighting decisions.")
        e2.metric("Selection Effect",
                  f"{float(result.get('Selection Effect', 0.0))*100:.3f}%",
                  help="Active return from stock-picking within sectors.")
        e3.metric("Interaction Effect",
                  f"{float(result.get('Interaction Effect', 0.0))*100:.3f}%",
                  help="Cross-term: (Δweight) × (Δreturn).")
        e4.metric("Attribution Sum",
                  f"{float(result.get('Attribution Sum', 0.0))*100:.3f}%",
                  help="Allocation + Selection + Interaction (should ≈ Active Return).")

        sector_details = result.get("Sector Details") or {}
        if sector_details:
            sector_df = pd.DataFrame.from_dict(sector_details, orient="index").reset_index()
            sector_df = sector_df.rename(columns={"index": "sector"})
            # Pretty column order for display.
            preferred = [
                "sector", "weight_p", "weight_b", "return_p", "return_b",
                "allocation_effect", "selection_effect",
                "interaction_effect", "total_attribution",
            ]
            ordered = [c for c in preferred if c in sector_df.columns]
            sector_df = sector_df[ordered]

            st.markdown("**Per-sector breakdown**")
            st.dataframe(sector_df, width="stretch", hide_index=True)

            # Bar chart of allocation vs. selection by sector — vectorized, no
            # extra dependencies (st.bar_chart consumes a DataFrame directly).
            chart_df = sector_df.set_index("sector")[
                [c for c in ("allocation_effect", "selection_effect") if c in sector_df.columns]
            ]
            st.markdown("**Allocation vs. Selection effect by sector**")
            st.bar_chart(chart_df)

            st.download_button(
                "⬇️ Download per-sector breakdown (CSV)",
                data=sector_df.to_csv(index=False).encode("utf-8"),
                file_name="brinson_fachler_breakdown.csv",
                mime="text/csv",
                key="bf_download_sector",
            )

        st.download_button(
            "⬇️ Download editor input (CSV)",
            data=edited.to_csv(index=False).encode("utf-8"),
            file_name="brinson_fachler_input.csv",
            mime="text/csv",
            key="bf_download_input",
        )


# ===========================================================================
# Tab 1 — Launcher & Orchestration
# ===========================================================================

def render_launcher() -> None:
    """Launch the pipeline (orchestrator OR advisory) and stream live feedback.

    Two launch paths are surfaced, each as a distinct button so the operator
    can pick the entry point that matches their intent:

    *   **▶️ Launch Pipeline** — spawns ``main_orchestrator.py`` (async, full
        pipeline including broker execution + HTML report).
    *   **🔄 Refresh Data (Advisory)** — spawns ``main.py`` (synchronous
        advisory loop).  This is the canonical ``.env``-loading entry point per
        the project convention documented in :mod:`main`, so the operator can
        use it as a fast, broker-free refresh that still hydrates the state
        snapshot every observability panel reads from.

    Pre-launch readiness:
        :func:`orchestrator_runner.validate_required_env` is run on every
        render and a missing variable is surfaced as an inline warning BEFORE
        the buttons are clicked — eliminating the failure mode where the
        subprocess silently degrades to neutral defaults and the operator
        sees no observable result.

    Telemetry feedback:
        Three log streams are tailed side-by-side — the active run log
        (``output/gui_run.log`` or ``output/gui_advisory.log`` depending on
        which entry point was launched), and the platform-wide structured
        telemetry written by ``alerting.setup_logging()`` to
        ``logs/investyo.log``.  The expander auto-expands while a run is in
        flight and an opt-in **auto-refresh** ticker (5 s) keeps the tail
        scrolling without manual clicks.
    """
    st.subheader("🚀 Program Launcher & Orchestration")
    st.caption(
        "Two entry points: the async `main_orchestrator.py` (full pipeline + "
        "broker) or the synchronous `main.py` advisory loop. Stage indicators, "
        "log tail, and the `logs/investyo.log` telemetry stream below give "
        "real-time observability."
    )

    # ── Pre-launch environment readiness check ─────────────────────────────
    env_status = orchestrator_runner.validate_required_env()
    missing = [k for k, ok in env_status.items() if not ok]
    if missing:
        st.error(
            "⚠️  Missing required env var(s): "
            + ", ".join(f"`{k}`" for k in missing)
            + ". Pipeline will run but produce neutral / degraded output. "
            "Set them in `.env` before launching."
        )
    else:
        st.caption("✅  Required env vars present (`" + "`, `".join(env_status.keys()) + "`).")

    # ── Launch controls ────────────────────────────────────────────────────
    col_a, col_b, col_c, col_d = st.columns([1, 1, 1.4, 1.4])
    with col_a:
        dry_run = st.checkbox(
            "Dry run", value=settings.DRY_RUN,
            help="Orchestrator-only: log intended orders but never submit them.",
        )
    with col_b:
        refresh_account = st.checkbox(
            "Refresh RH account", value=False,
            help="Force a fresh Robinhood account snapshot on this launch.",
        )
    with col_c:
        launch_orch = st.button(
            "▶️  Launch Pipeline", type="primary", width="stretch",
            help="Run `main_orchestrator.py` (async, includes broker execution).",
        )
    with col_d:
        launch_adv = st.button(
            "🔄  Refresh Data (Advisory)", width="stretch",
            help="Run `main.py` (advisory-only — no broker; canonical `.env` entry point).",
        )

    handle: Optional[orchestrator_runner.RunHandle] = st.session_state.get("run_handle")

    if launch_orch:
        if handle is not None and handle.is_running():
            st.warning("A pipeline run is already in progress — wait for it to finish.")
        else:
            handle = orchestrator_runner.launch_orchestrator(
                dry_run=dry_run, refresh_account=refresh_account
            )
            st.session_state["run_handle"] = handle
            st.session_state["last_launch_kind"] = "orchestrator"
            st.success(f"🚀  Launched orchestrator (PID {handle.pid}).")
    elif launch_adv:
        if handle is not None and handle.is_running():
            st.warning("A pipeline run is already in progress — wait for it to finish.")
        else:
            handle = orchestrator_runner.launch_advisory_main(refresh_account=refresh_account)
            st.session_state["run_handle"] = handle
            st.session_state["last_launch_kind"] = "advisory"
            st.success(f"🔄  Launched advisory main.py (PID {handle.pid}).")

    # ── Status row ─────────────────────────────────────────────────────────
    running = handle is not None and handle.is_running()
    hb_age = orchestrator_runner.heartbeat_age_seconds()
    cols = st.columns(3)
    with cols[0]:
        if handle is None:
            st.info("No run launched this session.")
        elif running:
            mode_label = (handle.mode or "?").title()
            st.success(f"🟢 Running ({mode_label}, PID {handle.pid})")
        else:
            rc = handle.returncode()
            mode_label = (handle.mode or "?").title()
            if rc is None:
                st.info(f"⏹️ Finished ({mode_label})")
            elif rc == 0:
                st.success(f"✅ Finished cleanly ({mode_label}, exit 0)")
            else:
                st.error(f"❌ Finished with errors ({mode_label}, exit {rc})")
    with cols[1]:
        if hb_age is None:
            st.metric("Heartbeat", "—")
        else:
            fresh = "🟢" if hb_age < 90 else "🔴"
            st.metric("Heartbeat age", f"{fresh} {hb_age:.0f}s")
    with cols[2]:
        auto_refresh = st.checkbox(
            "Auto-refresh while running", value=False, key="launcher_auto_refresh",
            help="Re-render this tab every 5 s while a run is active so the log tail keeps scrolling.",
        )

    # ── Stage indicators (orchestrator only — advisory has its own log shape) ──
    if handle is None or handle.mode == "orchestrator":
        st.markdown("**Pipeline stages**")
        stage_status = orchestrator_runner.compute_stage_status(handle)
        icon = {"done": "✅", "active": "🟡", "pending": "⚪", "idle": "⚪"}
        stage_cols = st.columns(len(stage_status))
        for col, (label, status) in zip(stage_cols, stage_status.items()):
            with col:
                st.metric(label, f"{icon.get(status, '⚪')} {status}")

    # ── Active run log (kind picked from the active handle) ────────────────
    log_label = "📜 Advisory log tail" if (handle and handle.mode == "advisory") else "📜 Orchestrator log tail"
    with st.expander(log_label, expanded=running):
        st.code(orchestrator_runner.read_log_tail(max_lines=200, handle=handle), language="text")

    # ── Platform-wide structured telemetry (alerting.py / logs/investyo.log) ──
    with st.expander("🛰️ Telemetry log (logs/investyo.log)", expanded=False):
        st.caption(
            "Structured logs written by `alerting.setup_logging()` — shared by "
            "both entry points. Rotates at 10 MB × 5 backups."
        )
        st.code(orchestrator_runner.read_telemetry_tail(max_lines=120), language="text")

    # ── Auto-refresh ticker (opt-in; cheap because Streamlit reruns are fast) ──
    if running and auto_refresh:
        time.sleep(5)
        st.rerun()


# ===========================================================================
# Tab 2 — Interactive Report Viewer
# ===========================================================================

# Distinct colour cues for Live vs Backtested data, applied via inline Markdown.
# Streamlit doesn't expose a primary-colour API per-element, so we use
# ``st.info`` (blue) for Live and a Markdown blockquote with a grey diamond
# for Backtested — both are clearly visually distinct at a glance.

_LIVE_TAG = "🔵 Live data"
_BACKTEST_TAG = "⚪ Backtested / simulated"


def _render_report_provenance_banner(snap: dict) -> None:
    """One-line banner classifying the data feeding this tab as Live vs Backtested.

    Rules:
      * Snapshot present + execution mode is PAPER or LIVE → Live (blue).
      * Snapshot present but mode is SIMULATION (``DRY_RUN=true``) → Backtested (grey).
      * Snapshot absent → Backtested (grey) with a hint about the Launcher.
    """
    from gui.strategy_registry import ExecutionMode, read_active_mode

    mode_state = read_active_mode()
    has_snap = bool(snap.get("signals") or snap.get("timestamp"))

    is_live = has_snap and mode_state.mode in (ExecutionMode.PAPER, ExecutionMode.LIVE)
    last_ts = snap.get("timestamp", "—")

    if is_live:
        st.info(
            f"{_LIVE_TAG} — sourced from `output/state_snapshot.json` "
            f"(mode: {mode_state.mode.label}; last run: {last_ts}).",
            icon="🔵",
        )
    else:
        reason = (
            "No state snapshot yet — run the orchestrator or `main.py`."
            if not has_snap
            else f"DRY_RUN active — every value here is simulated (mode: {mode_state.mode.label})."
        )
        st.markdown(
            f"> {_BACKTEST_TAG} — {reason}"
        )


def render_report_viewer() -> None:
    """Surface evaluation_engine / research_engine analytics + report exports.

    Visual cues
    -----------
    Every section of this tab is tagged Blue (Live) or Grey (Backtested /
    Simulated) so the operator cannot mistake one for the other. The
    classification rules:

    * **Blue / Live** — data sourced from ``output/state_snapshot.json``
      written by the most recent orchestrator / advisory run AND the active
      execution mode is :data:`ExecutionMode.PAPER` or
      :data:`ExecutionMode.LIVE`.
    * **Grey / Backtested** — data sourced from CSV uploads, validation
      reports, or anything authored under ``DRY_RUN=true`` (simulation mode).

    Drill-down: every metric tile has a "🔬 Inspect" expander revealing the
    underlying trade log, per-symbol contribution table, or raw signal row so
    the operator can see *why* a number is what it is rather than only *what*
    it is.
    """
    st.subheader("📈 Interactive Report Viewer")

    snap = load_state_snapshot()
    signals = snap.get("signals", [])

    _render_report_provenance_banner(snap)

    # ── Portfolio heat + edge from the engine ────────────────────────────────
    from evaluation_engine import EvaluationEngine

    ee = EvaluationEngine(max_portfolio_heat=settings.MAX_PORTFOLIO_HEAT)

    st.markdown("**Portfolio risk snapshot**")
    if signals:
        sig_df = pd.DataFrame(signals)
        # Build a minimal positions frame the heat calc understands; degrade
        # gracefully when the expected columns are absent.
        pos_df = pd.DataFrame(
            {
                "Symbol": sig_df.get("symbol", pd.Series(dtype=str)),
                "Kelly Target": sig_df.get("kelly_target", pd.Series(dtype=float)),
            }
        )
        try:
            heat = ee.calculate_portfolio_heat(pos_df)
        except Exception as exc:
            logger.warning("portfolio heat failed: %s", exc)
            heat = float("nan")
        c1, c2, c3 = st.columns(3)
        with c1:
            heat_icon = "🔴" if (heat == heat and heat > settings.MAX_PORTFOLIO_HEAT) else "🟢"
            st.metric("Portfolio Heat", f"{heat_icon} {heat:.2%}" if heat == heat else "—")
        with c2:
            st.metric("Heat Limit", f"{settings.MAX_PORTFOLIO_HEAT:.0%}")
        with c3:
            st.metric("Active Signals", str(len(signals)))
    else:
        st.info("No pipeline signals yet — run the orchestrator from the Launcher tab.")

    # ── MFE/MAE & Edge Ratio chart ───────────────────────────────────────────
    st.markdown("**MFE / MAE / Edge Ratio (latest signals)**")
    if signals:
        sig_df = pd.DataFrame(signals)
        chart_cols = [c for c in ["symbol", "score", "kelly_target"] if c in sig_df.columns]
        if chart_cols:
            st.bar_chart(sig_df.set_index("symbol")[[c for c in chart_cols if c != "symbol"]])
        st.dataframe(sig_df, width="stretch")

        # ── Drill-down: pick a symbol → see its full signal row + recent
        #    closed trades from the TransactionsStore. Click-to-explain "why
        #    is this score what it is" rather than scrolling the wide table.
        with st.expander("🔬 Drill down by symbol"):
            if "symbol" in sig_df.columns:
                pick = st.selectbox(
                    "Symbol",
                    options=sorted(sig_df["symbol"].astype(str).unique()),
                    key="report_drilldown_symbol",
                )
                if pick:
                    row = sig_df[sig_df["symbol"].astype(str) == pick].iloc[0].to_dict()
                    st.markdown(f"**Signal row for `{pick}`**")
                    st.json(row)

                    # Trade-log drill-down via TransactionsStore (CONSTRAINT
                    # #7: integrate, don't reinvent — read the existing
                    # ledger directly).
                    try:
                        from transactions_store import TransactionsStore
                        ts = TransactionsStore()
                        closed = ts.closed_trades_df()
                        if (not closed.empty
                                and "symbol" in closed.columns):
                            sym_trades = closed[
                                closed["symbol"].astype(str) == pick
                            ].sort_values("exit_ts", ascending=False).head(20)
                            if not sym_trades.empty:
                                st.markdown(f"**Closed trades for `{pick}` "
                                            f"(latest {len(sym_trades)})**")
                                st.dataframe(sym_trades, width="stretch",
                                             hide_index=True)
                            else:
                                st.caption(
                                    f"No closed trades for `{pick}` yet — "
                                    "score-only drill-down."
                                )
                    except Exception as exc:  # noqa: BLE001 — degrade
                        st.caption(f"(transactions store unavailable: {exc})")
            else:
                st.caption("Signal frame has no `symbol` column to drill into.")
    else:
        st.caption("MFE/MAE/Edge populate once closed trades and signals exist.")

    # ── Brinson-Fachler attribution (interactive section) ───────────────────
    _render_brinson_fachler_section()

    # ── Existing HTML report export ──────────────────────────────────────────
    st.markdown("**Generated reports**")
    html_report = settings.OUTPUT_DIR / "daily_report_dashboard.html"
    if html_report.exists():
        st.download_button(
            "⬇️ Download daily HTML report",
            data=html_report.read_bytes(),
            file_name="daily_report_dashboard.html",
            mime="text/html",
            width="stretch",
        )
    else:
        st.caption("No HTML report yet — generated at the end of an orchestrator run.")

    if signals:
        st.download_button(
            "⬇️ Export latest signals (CSV)",
            data=pd.DataFrame(signals).to_csv(index=False).encode("utf-8"),
            file_name="latest_signals.csv",
            mime="text/csv",
            width="stretch",
        )


# ===========================================================================
# Tab 3 — Dynamic Settings Manager
# ===========================================================================

# Render hints: (key, widget_kind). Unlisted allowlist keys default to text.
_SETTINGS_LAYOUT: List[tuple[str, str]] = [
    ("RISK_FREE_RATE", "number"),
    ("MARKET_RISK_PREMIUM", "number"),
    ("REQUIRED_RETURN_RATE", "number"),
    ("MAX_PORTFOLIO_HEAT", "number"),
    ("KELLY_FRACTION", "number"),
    ("KELLY_CAP", "number"),
    ("VOL_TARGET", "number"),
    ("MAX_LEVERAGE", "number"),
    ("MAX_POSITION_WEIGHT", "number"),
    ("MAX_CORRELATION", "number"),
    ("DAILY_LOSS_LIMIT_PCT", "number"),
    ("HMM_RISK_OFF_BLOCK_THRESHOLD", "number"),
    ("META_LABEL_MIN_CONFIDENCE", "number"),
    ("DASHBOARD_REFRESH_SECONDS", "int"),
    ("MAX_ORDER_RATE_PER_MIN", "int"),
    ("MARKET_DATA_QUOTE_TTL_SECONDS", "int"),
    ("DRY_RUN", "bool"),
    ("RISK_GATE_ENFORCE_MARKET_HOURS", "bool"),
    ("MARKET_DATA_PROVIDER", "text"),
    ("LOG_LEVEL", "text"),
    ("DEFAULT_TICKERS", "tickers"),
]


def _current_scalar(key: str, fallback: Any) -> Any:
    """Best-effort current value of ``key`` (from .env, else live settings)."""
    try:
        raw = env_io.get_value(key, "")
    except Exception:
        raw = ""
    if raw != "":
        return raw
    return getattr(settings, key, fallback)


def render_settings_manager() -> None:
    """Edit NON-secret tunables and persist them to ``.env`` (secrets masked)."""
    st.subheader("⚙️ Dynamic Settings Manager")
    st.caption(
        "Edit non-secret runtime tunables. Changes are written to `.env` and take "
        "effect on the **next** launch. Secrets are masked and read-only here "
        "(edit them directly in `.env`)."
    )

    updates: Dict[str, Any] = {}
    with st.form("settings_form"):
        for key, kind in _SETTINGS_LAYOUT:
            cur = _current_scalar(key, getattr(settings, key, ""))
            if kind == "number":
                try:
                    val = st.number_input(key, value=float(cur), step=0.01, format="%.4f")
                except Exception:
                    val = st.number_input(key, value=0.0, step=0.01, format="%.4f")
                updates[key] = val
            elif kind == "int":
                try:
                    val = st.number_input(key, value=int(float(cur)), step=1)
                except Exception:
                    val = st.number_input(key, value=0, step=1)
                updates[key] = int(val)
            elif kind == "bool":
                truthy = str(cur).strip().lower() in {"1", "true", "yes", "on"}
                updates[key] = st.checkbox(key, value=truthy)
            elif kind == "tickers":
                default_list = (
                    cur if isinstance(cur, list) else list(settings.DEFAULT_TICKERS)
                )
                text = st.text_input(
                    key, value=", ".join(default_list),
                    help="Comma-separated tickers; stored as a JSON array.",
                )
                updates[key] = [t.strip().upper() for t in text.split(",") if t.strip()]
            else:  # text
                updates[key] = st.text_input(key, value="" if cur is None else str(cur))

        submitted = st.form_submit_button("💾 Save to .env", type="primary")

    if submitted:
        try:
            written = env_io.write_many(updates)
            st.success(f"Saved {len(written)} setting(s) to .env. Re-launch to apply.")
        except env_io.SecretWriteError as exc:
            st.error(f"Refused to write a secret: {exc}")
        except Exception as exc:
            st.error(f"Failed to write settings: {exc}")

    # Masked view of secrets so the operator can confirm what's configured.
    with st.expander("🔒 Secrets (masked, read-only)"):
        secret_rows = []
        for key in env_io.SECRET_KEYS:
            try:
                raw = dict(env_io._raw_env()).get(key)  # noqa: SLF001 - internal read for display
            except Exception:
                raw = None
            secret_rows.append({"Key": key, "Status": env_io.mask_secret(raw)})
        st.dataframe(pd.DataFrame(secret_rows), width="stretch")


# ===========================================================================
# Tab 4 — Strategy Matrix & Risk Gating
# ===========================================================================

def _render_strategy_mode_toggle() -> None:
    """Global Simulation / Paper / Live selector.

    Backed by :func:`gui.strategy_registry.set_active_mode`, which writes
    ``DRY_RUN`` and ``ALPACA_PAPER`` to ``.env`` via the allowlist-bounded
    :mod:`gui.env_io` writer. Effect on the **next** orchestrator launch — we
    never patch a running ``settings`` instance.
    """
    from gui.strategy_registry import (
        ExecutionMode,
        mode_banner_text,
        read_active_mode,
        set_active_mode,
    )

    st.markdown("### 🎚️ Global Execution Mode")
    state = read_active_mode()

    if state.is_live:
        st.error(f"🔴 **{state.mode.label}** — orders WILL hit the live broker.",
                 icon="⚠️")
    elif state.mode is ExecutionMode.PAPER:
        st.info(f"📝 **{state.mode.label}** — orders route to the Alpaca paper sandbox.",
                icon="ℹ️")
    else:
        st.success(f"🧪 **{state.mode.label}** — OrderManager intercepts before broker contact.",
                   icon="🧪")
    st.caption(mode_banner_text(state))

    options: list[ExecutionMode] = list(ExecutionMode)
    labels = [m.label for m in options]
    current_idx = options.index(state.mode)
    chosen_label = st.radio(
        "Switch mode",
        options=labels,
        index=current_idx,
        horizontal=True,
        key="strategy_mode_radio",
    )
    chosen_mode = options[labels.index(chosen_label)]

    if chosen_mode is not state.mode:
        col_confirm, col_cancel = st.columns([1, 1])
        with col_confirm:
            confirm_label = (
                "🔴 CONFIRM LIVE PRODUCTION"
                if chosen_mode is ExecutionMode.LIVE
                else f"Apply {chosen_mode.label}"
            )
            if st.button(confirm_label, type="primary", key="apply_mode"):
                try:
                    new_state = set_active_mode(chosen_mode)
                    st.success(
                        f"Mode written to `.env` → {new_state.mode.label}. "
                        "Takes effect on the next orchestrator / advisory launch."
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to persist mode: {exc}")
        with col_cancel:
            st.caption(
                "ALPACA_PAPER and DRY_RUN are written together so the mode is "
                "fully consistent — no half-flips."
            )


def _render_strategy_version_registry() -> None:
    """Show each signal module's deployment fingerprint (sha256 prefix + mtime).

    Backed by :func:`gui.strategy_registry.list_strategy_versions`. Useful for
    answering "did I really redeploy the meta-labeler since last week's run?"
    without having to scroll git log.
    """
    from gui.strategy_registry import list_strategy_versions

    st.markdown("### 📜 Strategy Version Registry")
    st.caption(
        "Each module's deployment fingerprint — sha256 prefix + file mtime — "
        "joined with live enable/weight state from `settings`."
    )
    records = list_strategy_versions()
    if not records:
        st.info("No registered signal modules detected.")
        return

    rows = []
    for r in records:
        rows.append({
            "Module": r.name,
            "Enabled": "✅" if r.enabled else "⏸",
            "Weight": round(r.weight, 4),
            "Version": r.version_hash or "—",
            "Last modified (UTC)": (r.last_modified.isoformat(timespec="seconds")
                                    if r.last_modified else "—"),
            "Source file": (str(r.file_path.relative_to(_REPO_ROOT))
                            if r.file_path else "—"),
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_strategy_matrix() -> None:
    """Strategy Matrix & Risk Gating — module toggles, weights, kill switch, version registry.

    Sections
    --------
    1.  **Global Execution Mode** — Simulation / Paper / Live selector backed
        by :mod:`gui.strategy_registry`. Writes ``DRY_RUN`` + ``ALPACA_PAPER``
        to ``.env`` via the allowlist-bounded :mod:`gui.env_io` writer.
        Effect on next orchestrator launch.
    2.  **Strategy Version Registry** — sha256-prefix fingerprint + file mtime
        of every signal module so the operator can see at a glance whether a
        strategy file has been redeployed since the last run.
    3.  **Signal modules** — existing per-module enable/weight form.
    4.  **Manual macro kill switch** — existing GlobalKillSwitch wrapper.
    5.  **Recent risk-gate blocks** — existing block log table.
    """
    st.subheader("🧩 Strategy Matrix & Risk Gating")

    _render_strategy_mode_toggle()
    st.divider()

    _render_strategy_version_registry()
    st.divider()

    # ── Module enable/disable + weights ──────────────────────────────────────
    st.markdown("**Signal modules** — disable a module or adjust its weight; "
                "saved to `.env` and honored by `SignalAggregator` on next run.")

    try:
        from signals.registry import global_registry
        # Importing the package registers the built-in modules as a side effect.
        import signals  # noqa: F401
        module_names = sorted(global_registry.get_all().keys())
    except Exception as exc:
        logger.warning("signal registry unavailable: %s", exc)
        module_names = sorted(settings.SIGNAL_WEIGHTS.keys())

    if not module_names:
        module_names = sorted(settings.SIGNAL_WEIGHTS.keys())

    disabled_now = set(settings.DISABLED_SIGNAL_MODULES)
    weights_now = dict(settings.SIGNAL_WEIGHTS)

    with st.form("strategy_matrix_form"):
        new_disabled: List[str] = []
        new_weights: Dict[str, float] = {}
        header = st.columns([3, 1, 2])
        header[0].caption("Module")
        header[1].caption("Enabled")
        header[2].caption("Weight")
        for name in module_names:
            c0, c1, c2 = st.columns([3, 1, 2])
            c0.write(f"`{name}`")
            enabled = c1.checkbox(
                "on", value=(name not in disabled_now), key=f"en_{name}",
                label_visibility="collapsed",
            )
            wt = c2.number_input(
                "wt", value=float(weights_now.get(name, 0.0)), step=1.0,
                key=f"wt_{name}", label_visibility="collapsed",
            )
            if not enabled:
                new_disabled.append(name)
            new_weights[name] = float(wt)

        saved = st.form_submit_button("💾 Save module config", type="primary")

    if saved:
        try:
            env_io.write_setting("DISABLED_SIGNAL_MODULES", new_disabled)
            env_io.write_setting("SIGNAL_WEIGHTS", new_weights)
            st.success(
                f"Saved. Disabled: {new_disabled or 'none'}. Re-launch to apply."
            )
        except Exception as exc:
            st.error(f"Failed to save module config: {exc}")

    st.caption(
        "Note: `regime_multiplier` must keep weight 0.0 — it carries the HMM "
        "second opinion as a sizing multiplier, not a score."
    )

    st.divider()

    # ── Manual macro kill switch ─────────────────────────────────────────────
    st.markdown("**Macro Kill Switch** — global halt on new order submission.")
    ks = _kill_switch()
    active = ks.is_active()
    col_status, col_action = st.columns([2, 2])
    with col_status:
        if active:
            st.error(f"🚨 ACTIVE — {ks.reason() or '(no reason stored)'}")
        else:
            st.success("✅ INACTIVE")
    with col_action:
        if active:
            if st.button("Deactivate kill switch", width="stretch"):
                ks.deactivate()
                st.rerun()
        else:
            reason = st.text_input("Reason", value="Manual halt via Command Center")
            if st.button("🛑 Activate kill switch", type="primary", width="stretch"):
                ks.activate(reason)
                st.rerun()

    st.divider()

    # ── Risk gate block log ──────────────────────────────────────────────────
    st.markdown("**Recent risk-gate blocks**")
    blocks = load_block_log(100)
    if blocks:
        st.dataframe(pd.DataFrame(blocks), width="stretch")
    else:
        st.success("No blocked orders in the log.")


# ===========================================================================
# Tab 5 — Paper-Trading Monitor (RH account vs. internal projection)
# ===========================================================================

def render_paper_monitor() -> None:
    """Reconcile Robinhood account truth against internal pipeline projections.

    CONSTRAINT #4: Robinhood supplies ACCOUNT STATE ONLY (qty, cost basis,
    buying power, equity, dividends). Pipeline projections come from the
    market-data-driven snapshot. Columns are explicitly source-labeled so the
    two are never conflated.
    """
    st.subheader("📒 Paper-Trading Monitor")
    st.caption(
        "Left: **Robinhood account truth** (account state only). "
        "Right: **internal pipeline projection** (market-data driven)."
    )

    fetch = st.button("🔄 Fetch Robinhood snapshot (read-only)")
    snapshot_obj = st.session_state.get("rh_snapshot")
    if fetch:
        try:
            from data.robinhood_portfolio import fetch_account_snapshot

            snapshot_obj = fetch_account_snapshot()
            st.session_state["rh_snapshot"] = snapshot_obj
        except Exception as exc:
            st.error(f"Robinhood fetch failed: {exc}")
            snapshot_obj = None

    col_rh, col_proj = st.columns(2)

    with col_rh:
        st.markdown("**🟢 Robinhood (broker truth)**")
        if snapshot_obj is not None:
            try:
                stale = snapshot_obj.is_stale()
                badge = "🔴 STALE" if stale else "🟢 fresh"
                st.caption(
                    f"Equity ${snapshot_obj.total_equity:,.2f} · "
                    f"Buying power ${snapshot_obj.buying_power:,.2f} · "
                    f"Dividends ${snapshot_obj.total_dividends:,.2f} · {badge}"
                )
                rows = [
                    {
                        "Symbol": p.symbol,
                        "Qty": p.quantity,
                        "Avg Cost": round(p.average_cost, 2),
                        "Mkt Value": round(p.market_value, 2),
                        "Unrl P/L": round(p.unrealized_pl, 2),
                        "P/L %": round(p.unrealized_pl_pct, 2),
                        "Div Recv": round(p.dividends_received, 2),
                    }
                    for p in snapshot_obj.positions.values()
                ]
                st.dataframe(pd.DataFrame(rows), width="stretch")
            except Exception as exc:
                st.error(f"Failed to render snapshot: {exc}")
        else:
            st.info("Click 'Fetch Robinhood snapshot' (requires RH_* env vars).")

    with col_proj:
        st.markdown("**🔵 Pipeline projection (market data)**")
        snap = load_state_snapshot()
        signals = snap.get("signals", [])
        if signals:
            proj_df = pd.DataFrame(signals)
            show = [c for c in ["symbol", "price", "action", "kelly_target", "score"]
                    if c in proj_df.columns]
            st.dataframe(proj_df[show] if show else proj_df, width="stretch")
        else:
            st.info("No pipeline projection yet — run the orchestrator.")

    # ── Reconciliation on symbol ─────────────────────────────────────────────
    if snapshot_obj is not None:
        st.markdown("**Reconciliation (held vs. projected)**")
        try:
            held = set(snapshot_obj.positions.keys())
            projected = {s.get("symbol") for s in load_state_snapshot().get("signals", [])}
            held_only = sorted(held - projected)
            proj_only = sorted(projected - held - {None})
            rc1, rc2 = st.columns(2)
            rc1.metric("Held, no signal", ", ".join(held_only) or "—")
            rc2.metric("Signalled, not held", ", ".join(proj_only) or "—")
        except Exception as exc:
            st.warning(f"Reconciliation failed: {exc}")


# ===========================================================================
# Tab 6 — Gravity AI Audit Logs
# ===========================================================================

def _render_circuit_breaker_dashboard() -> None:
    """Render every tripped breaker — kill switch + recent risk-gate blocks.

    Read-only derivation via :mod:`gui.circuit_breakers`. Adding a new
    breaker means adding a check inside ``execution/risk_gate.py``; this
    panel auto-picks-up the new tag via the ``_KNOWN_CHECKS`` table over there.
    """
    from gui.circuit_breakers import (
        collect_circuit_breaker_trips,
        summarise_trips,
    )

    st.markdown("### 🚧 Circuit Breaker Dashboard")
    st.caption(
        "Trips derived from `output/KILL_SWITCH` and `output/risk_gate_blocks.jsonl` "
        "(last 24 h). Most recent per (breaker, strategy) shown."
    )

    trips = collect_circuit_breaker_trips(
        kill_switch_sentinel=settings.OUTPUT_DIR / "KILL_SWITCH",
        block_log_path=settings.OUTPUT_DIR / "risk_gate_blocks.jsonl",
    )
    summary = summarise_trips(trips)

    k1, k2, k3 = st.columns(3)
    k1.metric("CRITICAL trips", summary["CRITICAL"])
    k2.metric("WARNING trips", summary["WARNING"])
    k3.metric("Total", summary["TOTAL"])

    if not trips:
        st.success("✅ No active circuit-breaker trips in the last 24 h.")
        return

    rows = []
    for t in trips:
        rows.append({
            "Severity": ("🔴 CRITICAL" if t.severity == "CRITICAL"
                         else "🟡 WARNING"),
            "Breaker": t.name,
            "Summary": t.summary,
            "Triggered (UTC)": (t.triggered_at.isoformat(timespec="seconds")
                                if t.triggered_at else "—"),
            "Threshold": (f"{t.threshold:.4g}" if t.threshold is not None else "—"),
            "Observed": (f"{t.observed:.4g}" if t.observed is not None else "—"),
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    with st.expander("🔬 Inspect raw trip payloads"):
        for t in trips:
            st.markdown(f"**{t.name}** — {t.severity}")
            st.json(dict(t.detail))


def _render_dependency_map() -> None:
    """Pick degraded sources → list every impacted strategy/tab/report."""
    from gui.dependency_map import (
        CONSUMERS,
        DataSource,
        impacted_consumers,
        render_edges,
    )

    st.markdown("### 🕸️ Dependency Map")
    st.caption(
        "Declarative source → consumer graph. Pick the sources that are "
        "degraded right now and the panel projects which strategies, tabs, "
        "and reports lose coverage. The map itself lives in "
        "`gui/dependency_map.py`; extend it there as new consumers come online."
    )

    options = [s for s in DataSource if s is not DataSource.UNKNOWN]
    labels = {s.label: s for s in options}
    chosen_labels = st.multiselect(
        "Degraded data sources",
        options=list(labels.keys()),
        default=[],
        help="Pick zero or more sources to simulate / acknowledge an outage.",
    )
    chosen = [labels[name] for name in chosen_labels]

    if chosen:
        impact = impacted_consumers(chosen)
        rows = []
        for record in impact:
            for c in record.consumers:
                rows.append({
                    "Degraded source": record.source.label,
                    "Impacted": c.name,
                    "Kind": c.kind,
                    "Why": c.description,
                })
        if rows:
            st.warning(
                f"⚠️ {len(rows)} downstream consumer(s) impacted across "
                f"{len(impact)} source(s).",
                icon="⚠️",
            )
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.info("Selected source(s) have no registered consumers — "
                    "verify `gui/dependency_map.py` is current.")
    else:
        st.caption("No degraded sources selected. Showing the full graph below.")

    with st.expander("🔬 Full dependency graph"):
        edges = render_edges()
        edge_df = pd.DataFrame(edges, columns=["Source", "Consumer", "Kind"])
        st.dataframe(edge_df, width="stretch", hide_index=True)


def render_gravity_audit() -> None:
    """Render the Safety tab: Circuit Breakers + Dependency Map + Gravity audit.

    Sections (top to bottom):

    1.  **Circuit Breaker Dashboard** — every tripped breaker derived from the
        existing kill-switch sentinel + risk-gate block log. See
        :mod:`gui.circuit_breakers`.
    2.  **Dependency Map** — declarative source → consumer graph from
        :mod:`gui.dependency_map`. The operator picks the degraded sources
        and the panel shows which strategies / tabs / reports lose coverage.
    3.  **Gravity AI Review Suite** — full audit subprocess (the original
        behavior, kept verbatim).
    """
    st.subheader("🛡️ Safety — Circuit Breakers, Dependencies, Gravity Audit")

    _render_circuit_breaker_dashboard()
    st.divider()
    _render_dependency_map()
    st.divider()

    st.markdown("### 🧪 Gravity AI Review Suite")
    st.caption(
        "Runs `Gravity AI Review Suite.py` — Pandera schema conformance, "
        "lookahead-bias perturbation, signal-registry health, sizing/risk gates. "
        "Review before authorizing a live run."
    )

    if st.button("▶️ Run Gravity audit", type="primary"):
        with st.spinner("Running Gravity AI Review Suite (this can take a minute)…"):
            try:
                import subprocess
                import sys

                proc = subprocess.run(
                    [sys.executable, "Gravity AI Review Suite.py"],
                    cwd=str(_REPO_ROOT),
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                st.session_state["gravity_stdout"] = proc.stdout
                st.session_state["gravity_returncode"] = proc.returncode
            except Exception as exc:
                st.session_state["gravity_stdout"] = ""
                st.session_state["gravity_error"] = str(exc)

    stdout = st.session_state.get("gravity_stdout", "")
    if st.session_state.get("gravity_error"):
        st.error(f"Audit failed to launch: {st.session_state['gravity_error']}")

    if stdout:
        report = _parse_trailing_json(stdout)
        if report is None:
            st.warning("Could not parse a JSON report from the audit output.")
            st.code(stdout[-4000:], language="text")
            return

        rows = []
        for key, val in report.items():
            if not isinstance(val, dict):
                continue
            status = str(val.get("status", "—"))
            ok = status.upper().startswith("PASS")
            rows.append({"Step": key, "Status": ("✅ " if ok else "❌ ") + status})
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch")
            failed = [r for r in rows if "✅" not in r["Status"]]
            if failed:
                st.error(f"{len(failed)} audit step(s) failed — NOT cleared for live.")
            else:
                st.success("All audit steps passed — cleared for live readiness review.")
        with st.expander("🔬 Full audit JSON"):
            st.json(report)


def _parse_trailing_json(text: str) -> Optional[dict]:
    """Extract the last top-level JSON object from arbitrary stdout."""
    end = text.rfind("}")
    if end == -1:
        return None
    depth = 0
    start = -1
    for i in range(end, -1, -1):
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            depth -= 1
            if depth == 0:
                start = i
                break
    if start == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


# ===========================================================================
# Tab 7 — Technical Options Matrix
# ===========================================================================

def render_options_matrix() -> None:
    """Hydrated premium-selling matrix across held + watchlist + signal symbols.

    Pipeline per symbol (dead-letter resilient, CONSTRAINT #6):
      1. Provider quote + 252-day OHLCV.
      2. ``build_premium_directive`` — GJR-GARCH σ, realized-vol IVR proxy,
         Aroon+Coppock trend bias, full ATM Black-Scholes Greeks, deterministic
         strategy directive (Put Credit / Iron Condor / Debit / Covered Call),
         realizable daily theta after DTE-scaled execution friction, and the
         per-leg matrix-integrity verdict ($0.50 strike grid + delta-target
         tolerance).
      3. The macro state snapshot is forwarded into the directive so the VRP
         regime gate (VIX ≥ 30 ∨ CREDIT EVENT) fires identically to the live
         orchestrator path — no premium-selling advice in a stress regime.

    The universe auto-iterates **all** active symbols from
    :func:`_active_symbols` (held Robinhood positions ∪ watchlist ∪ last
    pipeline signals) so no premium-selling opportunity is silently dropped.
    """
    st.subheader("🧮 Technical Options Matrix")
    st.caption(
        "Hydrated premium-selling matrix: GJR-GARCH σ, realized-vol IVR proxy, "
        "Aroon+Coppock trend bias, ATM Black-Scholes Greeks, and the "
        "deterministic strategy directive with $0.50 strike-grid integrity checks."
    )

    snap = load_state_snapshot()
    default_universe = _active_symbols(snap)

    col_syms, col_dte, col_auto = st.columns([4, 1, 1])
    with col_syms:
        sym_text = st.text_input(
            "Symbols",
            value=", ".join(default_universe),
            help="Auto-populated from held positions ∪ watchlist ∪ last signals. Edit to override.",
        )
    with col_dte:
        target_dte = st.number_input(
            "Target DTE", min_value=1, max_value=120, value=30, step=1,
            help="Days to expiration used by Black-Scholes and the theta haircut.",
        )
    with col_auto:
        auto_run = st.checkbox(
            "Auto-run", value=False,
            help="Recompute on every rerun (otherwise click the button).",
        )

    symbols = [s.strip().upper() for s in sym_text.split(",") if s.strip()]
    if not symbols:
        st.info("Enter at least one symbol.")
        return

    run = auto_run or st.button("▶️ Compute matrix", type="primary")
    if not run:
        st.caption(f"{len(symbols)} symbol(s) queued: {', '.join(symbols[:25])}"
                   + (" …" if len(symbols) > 25 else ""))
        return

    from technical_options_engine import build_premium_directive
    from data.market_data import get_provider, MarketDataError

    # Lightweight MacroEconomicDTO-shaped object built from the snapshot so the
    # regime gate can fire without a live FRED round-trip. Anything missing is
    # left at its neutral default — the gate only flips on positive evidence.
    class _MacroProxy:
        def __init__(self, snap_: dict):
            self.vix = float(snap_.get("vix")) if snap_.get("vix") is not None else 15.0
            self.market_regime = str(snap_.get("market_regime", "RISK ON"))

    macro_proxy = _MacroProxy(snap)
    provider = get_provider()
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []

    progress = st.progress(0.0, text="Computing premium directives…")
    for i, sym in enumerate(symbols):
        try:
            quote = provider.get_latest_quote(sym)
            bars = provider.get_intraday_bars(sym, lookback_days=252)
            row = build_premium_directive(
                sym,
                bars,
                spot_price=float(quote.price),
                is_stale=bool(quote.is_stale),
                target_dte=int(target_dte),
                macro_dto=macro_proxy,
                vrp=None,  # VRP requires an options chain — left None to skip that gate
                risk_free_rate=settings.RISK_FREE_RATE,
            )
        except MarketDataError as exc:
            logger.warning("market data error for %s: %s", sym, exc)
            errors.append(f"{sym}: market data unavailable ({exc})")
            row = {"Symbol": sym, "Strategy": "—", "Action": "—", "Integrity_OK": False,
                   "Integrity_Issues": [str(exc)]}
        except Exception as exc:  # noqa: BLE001
            logger.warning("options matrix failed for %s: %s", sym, exc)
            errors.append(f"{sym}: {exc}")
            row = {"Symbol": sym, "Strategy": "—", "Action": "—", "Integrity_OK": False,
                   "Integrity_Issues": [str(exc)]}
        rows.append(row)
        progress.progress((i + 1) / len(symbols),
                          text=f"Computing premium directives… ({i + 1}/{len(symbols)})")
    progress.empty()

    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No rows computed.")
        return

    # Stable column order matching config.COLUMN_SCHEMA naming conventions where
    # they overlap. NaN columns are tolerated by Streamlit's dataframe widget.
    column_order = [
        "Symbol", "Price", "Stale",
        "Sigma_GARCH", "IVR_Proxy",
        "Aroon_Oscillator", "Coppock_Curve", "Trend_Bias",
        "Strategy", "Action",
        "Short_Strike", "Short_Delta", "Long_Strike", "Long_Delta",
        "Net_Premium", "Realizable_Daily_Theta",
        "ATM_Delta", "ATM_Gamma", "ATM_Vega", "ATM_Theta_Daily",
        "Integrity_OK",
    ]
    display_cols = [c for c in column_order if c in df.columns]
    st.dataframe(df[display_cols], width="stretch")

    # Integrity verdict summary (top-line readout — drill-down available below).
    if "Integrity_OK" in df.columns:
        ok_count = int(df["Integrity_OK"].sum())
        total = len(df)
        if ok_count == total:
            st.success(f"✅ Matrix integrity: {ok_count}/{total} legs on $0.50 grid + within delta tolerance.")
        else:
            st.warning(f"⚠️ Matrix integrity: {ok_count}/{total} clean; "
                       f"{total - ok_count} symbol(s) flagged below.")

    # Per-symbol breakdown for any flagged or actionable row.
    flagged = df[~df.get("Integrity_OK", True).fillna(False).astype(bool)]
    if not flagged.empty:
        with st.expander(f"🔬 Integrity issues ({len(flagged)})", expanded=False):
            for _, r in flagged.iterrows():
                issues = r.get("Integrity_Issues") or []
                st.markdown(f"**{r.get('Symbol', '?')}** — {r.get('Strategy', '?')}")
                for issue in issues:
                    st.markdown(f"  - {issue}")

    if errors:
        with st.expander(f"⚠️ Errors ({len(errors)})", expanded=False):
            for e in errors:
                st.markdown(f"- {e}")

    st.caption(
        "σ from GJR-GARCH(1,1) with 20-day realized fallback; **IVR proxy** is a "
        "realized-vol percentile (true IVR requires an options chain). Trend bias is "
        "Aroon+Coppock sign agreement. **Stale=True** marks delayed (~15 min) yfinance "
        "quotes. Realizable Theta applies a DTE-scaled execution-friction haircut "
        "(40% @ 1DTE, 22% @ 7DTE, 12% @ 30DTE, 5% baseline)."
    )


# ===========================================================================
# Tab 8 — Market Data
# ===========================================================================

def render_market_data() -> None:
    """Market Data Provider tab — diagnostic-rich quote fetcher.

    Improvements over the legacy panel
    ----------------------------------
    *   **Connectivity badge** — sliding-window success rate from
        :class:`gui.market_data_diagnostics.FetchHealthTracker` (Healthy /
        Degraded / Down), persisted across reruns in ``st.session_state``.
    *   **Throttled batch fetch** — uses
        :class:`gui.market_data_diagnostics.BatchQuoteFetcher` with default
        100 ms spacing so a 50-symbol watchlist sync stops triggering
        yfinance / Finnhub rate-limit storms.
    *   **Progress bar + per-symbol streaming** — operator sees ``i/N``
        feedback rather than a frozen "Running" spinner.
    *   **Typed error feedback** — failed fetches surface a specific category
        ("API Rate Limited", "Symbol Not Found", "Network Timeout",
        "Malformed Response", "Unknown Error") via
        :func:`classify_market_error`, never an opaque ``None``.
    *   **Quote validation** — :func:`validate_quote` flags NaN price, missing
        timestamp, or inverted bid/ask with a ⚠ icon BEFORE the row is
        considered usable by the rest of the pipeline (CONSTRAINT #4).
    """
    st.subheader("🛰️ Market Data Provider")

    from data.market_data import get_provider, reset_provider
    from gui.market_data_diagnostics import (
        BatchQuoteFetcher,
        FetchHealthTracker,
        category_label,
        summarise_categories,
    )
    from gui.observability_telemetry import LatencySampleStore

    provider = get_provider()
    src = getattr(provider, "quote_source", "unknown")
    realtime = getattr(provider, "is_realtime", False)

    # Persist the health tracker across Streamlit reruns so the badge survives
    # tab switches and the "Fetch quotes" button click cycle.
    tracker_key = "md_health_tracker"
    if tracker_key not in st.session_state:
        st.session_state[tracker_key] = FetchHealthTracker()
    health: FetchHealthTracker = st.session_state[tracker_key]
    report = health.status()

    # Shared latency store — also consumed by render_observability's heatmap so
    # one fetch in this tab updates the Observability view too.
    latency_key = "obs_latency_store"
    if latency_key not in st.session_state:
        st.session_state[latency_key] = LatencySampleStore()
    latency_store: LatencySampleStore = st.session_state[latency_key]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Provider", str(src))
    c2.metric("Mode", "🟢 real-time" if realtime else "🟡 delayed (~15 min)")
    c3.metric("Quote TTL", f"{settings.MARKET_DATA_QUOTE_TTL_SECONDS}s")
    c4.metric("Connection", report.badge(),
              help="Sliding window of the last 20 fetches. Healthy ≥ 90% success, "
                   "Degraded ≥ 50%, otherwise Down.")

    if not realtime:
        st.info(
            "🟡 yfinance is delayed by ~15 minutes and marked `is_stale=True` "
            "on every quote. Set `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` in `.env` "
            "to upgrade to the free IEX real-time feed.",
            icon="ℹ️",
        )

    bcol1, bcol2 = st.columns([1, 1])
    with bcol1:
        if st.button("♻️ Reset provider singleton",
                     help="Drops the cached provider so the next fetch re-evaluates env vars."):
            try:
                reset_provider()
                st.success("Provider singleton reset — re-selected on next quote.")
            except Exception as exc:
                st.error(f"Reset failed: {exc}")
    with bcol2:
        if st.button("🩺 Reset connection health",
                     help="Clear the success/failure ledger (badge returns to Healthy)."):
            st.session_state[tracker_key] = FetchHealthTracker()
            st.rerun()

    snap = load_state_snapshot()
    symbols_default = _signal_symbols(snap)
    sym_text = st.text_input(
        "Quote symbols",
        value=", ".join(symbols_default[:10]),
        key="md_syms",
        help="Comma- or space-separated tickers. Each fetch is throttled to "
             "≥100 ms apart to avoid free-tier rate limits.",
    )
    symbols = [s.strip().upper() for s in sym_text.replace(",", " ").split() if s.strip()]

    spacing_ms = st.slider(
        "Throttle (ms between fetches)", min_value=0, max_value=1000,
        value=100, step=25,
        help="Sliding gap between consecutive provider calls. 100 ms is safe "
             "for both yfinance and Alpaca free tiers.",
    )

    if st.button("Fetch quotes", type="primary"):
        if not symbols:
            st.warning("Enter at least one symbol.")
            return

        fetcher = BatchQuoteFetcher(
            fetch_fn=provider.get_latest_quote,
            spacing_seconds=spacing_ms / 1000.0,
            health_tracker=health,
        )

        progress = st.progress(0.0, text=f"Fetching 0/{len(symbols)}…")
        rows: List[Dict[str, Any]] = []
        results = []
        n = len(symbols)
        for result in fetcher.iter_fetch(symbols):
            results.append(result)
            if result.quote is not None:
                q = result.quote
                v = result.validation
                if q.timestamp is not None:
                    latency_store.record(
                        symbol=q.symbol, source=q.source,
                        quote_timestamp=q.timestamp, is_stale=q.is_stale,
                    )
                rows.append({
                    "Status": (v.label if v is not None else "OK"),
                    "Symbol": q.symbol,
                    "Price": round(float(q.price), 2) if v and v.ok else q.price,
                    "Bid": q.bid,
                    "Ask": q.ask,
                    "Stale": q.is_stale,
                    "Source": q.source,
                    "Error": "",
                    "Timestamp (UTC)": q.timestamp.isoformat() if q.timestamp else "—",
                })
            else:
                rows.append({
                    "Status": "❌ ERROR",
                    "Symbol": result.symbol,
                    "Price": None,
                    "Bid": None,
                    "Ask": None,
                    "Stale": None,
                    "Source": str(src),
                    "Error": (
                        f"{category_label(result.category)}: {result.error}"
                        if result.category is not None
                        else f"Unknown Error: {result.error}"
                    ),
                    "Timestamp (UTC)": "—",
                })
            progress.progress(
                (result.index + 1) / n,
                text=f"Fetching {result.index + 1}/{n} — {result.symbol}",
            )

        progress.empty()
        st.session_state["md_last_results"] = rows
        tally = summarise_categories(results)
        ok_count = tally.get("ok", 0)
        bad_count = sum(v for k, v in tally.items() if k != "ok")

        if bad_count == 0:
            st.success(f"✅ Fetched {ok_count}/{n} symbols cleanly.", icon="✅")
        else:
            breakdown = ", ".join(f"{k}: {v}" for k, v in tally.items() if k != "ok")
            st.warning(
                f"⚠️ {ok_count}/{n} ok • {bad_count} failed → {breakdown}",
                icon="⚠️",
            )

    rows = st.session_state.get("md_last_results")
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, width="stretch", hide_index=True)
        st.caption(
            "⚠ icons in **Status** mark malformed quotes (NaN price, missing "
            "timestamp, inverted bid/ask). These rows are never silently "
            "promoted into the quant pipeline."
        )


# ===========================================================================
# Tab 9 — Observability (folded-in summary of the existing dashboard)
# ===========================================================================

def render_observability() -> None:
    """Compact macro / regime / P&L view — Mission Control for the platform.

    Sections
    --------
    1.  System-health bar      — kill switch, macro regime, VIX, HMM risk-on.
    2.  Macro Regime Gate      — operator toggle (MACRO_REGIME_GATE_ENABLED) with
                                 live Sahm-Rule and HY-OAS telemetry.  Writes the
                                 setting to .env via gui.env_io (CONSTRAINT #3).
    3.  Recession indicators   — Sahm Rule / HY OAS / yield curve with threshold
                                 colour-coding so the operator can judge whether
                                 a "Risk Off" trigger is genuine or idiosyncratic.
    4.  Strategy P&L           — realized P&L by strategy from TransactionsStore.
    """
    st.subheader("📊 Observability — Mission Control")
    st.caption(
        "Summary of the file-backed state last written by the orchestrator. "
        "Full standalone dashboard: `streamlit run observability/dashboard.py`"
    )

    snap = load_state_snapshot()
    ks = _kill_switch()

    # ── 1. System-health bar ─────────────────────────────────────────────────
    c_ks, c_reg, c_vix, c_hmm = st.columns(4)
    with c_ks:
        if ks.is_active():
            st.error("🚨 Kill switch ACTIVE")
        else:
            st.success("✅ Kill switch inactive")
    with c_reg:
        regime = snap.get("market_regime", "—")
        colour = ("🟢" if "RISK ON" in str(regime)
                  else ("🔴" if "RECESSION" in str(regime) else "🟡"))
        st.metric("Macro Regime", f"{colour} {regime}")
    with c_vix:
        vix = snap.get("vix")
        st.metric("VIX", f"{vix:.1f}" if isinstance(vix, (int, float)) else "—",
                  delta=None, help="Kill-switch threshold: 30")
    with c_hmm:
        hmm_vals = [s.get("hmm_risk_on") for s in snap.get("signals", [])
                    if s.get("hmm_risk_on") is not None]
        st.metric("HMM Risk-On", f"{hmm_vals[0]:.1%}" if hmm_vals else "—",
                  help="Gaussian-HMM second opinion; below 20% → hmm_regime gate fires")

    last = snap.get("timestamp", "—")
    st.caption(f"Pipeline last run: **{last}**")

    st.divider()

    # ── 2. Macro Regime Gate toggle ──────────────────────────────────────────
    st.markdown("### 🔒 Macro Regime Gate")
    st.markdown(
        "Controls whether **MacroEconomicDTO.killSwitch** vetoes new BUY orders "
        "during recessionary/credit-stress environments.  "
        "\n\n"
        "- **ON (default):** autonomous mode — the engine halts fresh equity "
        "allocations and overrides technical BUY signals when Sahm Rule ≥ 0.5, "
        "VIX > 30, or HY OAS > 6 %.  \n"
        "- **OFF:** hybrid mode — technical signals run freely; the operator "
        "accepts responsibility for idiosyncratic false-positive suppression.  \n"
        "\n"
        "> ⚠️  **Always re-enable before going live.**  "
        "`scripts/preflight_check.py` will fail if the gate is off and "
        "`ALPACA_PAPER=false`."
    )

    # Read the *current* value from .env (not the in-process settings object so
    # changes made earlier this session are visible without a restart).
    try:
        current_raw = env_io.get_value("MACRO_REGIME_GATE_ENABLED")
        gate_on = current_raw.lower() not in ("false", "0", "no", "off")
    except Exception:
        # Key absent from .env — fall back to the settings default (True).
        gate_on = settings.MACRO_REGIME_GATE_ENABLED

    col_status, col_btn = st.columns([3, 1])
    with col_status:
        if gate_on:
            st.success("🟢 **Gate ON** — macro regime vetoes active")
        else:
            st.error("🔴 **Gate OFF** — technical signals run without macro veto")

    with col_btn:
        if gate_on:
            if st.button("⏸ Disable gate", key="disable_macro_gate",
                         help="Switch to hybrid mode (technical signals only)"):
                try:
                    env_io.write_setting("MACRO_REGIME_GATE_ENABLED", False)
                    st.cache_data.clear()
                    st.toast("Macro gate disabled — takes effect on next orchestrator launch.",
                             icon="⏸")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to write setting: {exc}")
        else:
            if st.button("▶ Enable gate", key="enable_macro_gate",
                         help="Restore autonomous macro-veto mode"):
                try:
                    env_io.write_setting("MACRO_REGIME_GATE_ENABLED", True)
                    st.cache_data.clear()
                    st.toast("Macro gate enabled — takes effect on next orchestrator launch.",
                             icon="✅")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to write setting: {exc}")

    if not gate_on:
        st.warning(
            "⚠️ **Macro Regime Gate is OFF.**  BUY orders will NOT be vetoed by "
            "RECESSION or CREDIT EVENT regime signals.  Re-enable before going live.",
            icon="⚠️",
        )

    st.caption(
        "Writes `MACRO_REGIME_GATE_ENABLED` to `.env` via `gui/env_io.py`.  "
        "Change takes effect when the orchestrator next starts."
    )

    st.divider()

    # ── 3. Recession-indicator telemetry ─────────────────────────────────────
    st.markdown("### 📉 Recession Indicator Telemetry")
    st.caption(
        "Values are sourced from the last orchestrator run's state snapshot "
        "(FRED data).  They reflect conditions at pipeline execution time, "
        "not real-time — run the orchestrator to refresh."
    )

    sahm = snap.get("sahm_rule")
    hy_oas = snap.get("high_yield_oas")
    yc = snap.get("yield_curve")
    vix_val = snap.get("vix")

    c1, c2, c3, c4 = st.columns(4)

    # Sahm Rule — threshold 0.5 (kill switch) / 0.3 (hmm-agreement fast-trigger)
    with c1:
        if sahm is not None:
            sahm_delta = None
            sahm_str = f"{sahm:.3f}"
            st.metric(
                "Sahm Rule", sahm_str,
                delta=None,
                help="≥ 0.50 → killSwitch fires; ≥ 0.30 + HMM agreement → lowered-threshold fast-trigger",
            )
            if sahm >= 0.5:
                st.error("🔴 ≥ 0.50 — kill-switch threshold breached")
            elif sahm >= 0.3:
                st.warning("🟡 ≥ 0.30 — fast-trigger zone (HMM agreement needed)")
            else:
                st.success("🟢 < 0.30 — below fast-trigger zone")
        else:
            st.metric("Sahm Rule", "—", help="Not available in last snapshot")

    # HY OAS — threshold 6.0 (RECESSION) / 4.5 (NEUTRAL→CREDIT EVENT)
    with c2:
        if hy_oas is not None:
            st.metric(
                "HY OAS (%)", f"{hy_oas:.2f}",
                help="High-Yield Option-Adjusted Spread. >6.0% → RECESSION; >4.5% → CREDIT EVENT; >6% + yield inversion → RECESSION",
            )
            if hy_oas >= 6.0:
                st.error("🔴 ≥ 6.0% — RECESSION regime trigger")
            elif hy_oas >= 4.5:
                st.warning("🟡 ≥ 4.5% — CREDIT EVENT zone")
            else:
                st.success("🟢 < 4.5% — below credit-stress threshold")
        else:
            st.metric("HY OAS (%)", "—")

    # Yield curve — inversion below -0.25 is part of RECESSION gate
    with c3:
        if yc is not None:
            st.metric(
                "10Y-2Y Spread (%)", f"{yc:.3f}",
                help="Yield curve 10Y-2Y. < -0.25% + HY OAS > 6% → RECESSION",
            )
            if yc < -0.25:
                st.warning("🟡 Inverted (< -0.25%)")
            else:
                st.success("🟢 Not inverted")
        else:
            st.metric("10Y-2Y Spread (%)", "—")

    # VIX — kill-switch threshold 30
    with c4:
        if vix_val is not None:
            st.metric(
                "VIX", f"{vix_val:.1f}",
                help="CBOE Volatility Index. > 30 → killSwitch fires",
            )
            if vix_val > 30:
                st.error("🔴 > 30 — kill-switch VIX threshold breached")
            elif vix_val > 25:
                st.warning("🟡 > 25 — lowered-threshold zone (HMM-agreement)")
            else:
                st.success("🟢 ≤ 25")
        else:
            st.metric("VIX", "—")

    # Composite kill-switch status derived from snapshot
    gate_from_snap = snap.get("macro_regime_gate_enabled", True)
    ks_active = snap.get("kill_switch_active", False)
    if ks_active:
        st.error("🚨 **MacroEconomicDTO.killSwitch was ACTIVE** at last pipeline run — "
                 "BUY orders were vetoed.")
    elif not gate_from_snap:
        st.info("ℹ️ Macro regime gate was **disabled** at last pipeline run — "
                "kill-switch veto was bypassed.")
    else:
        st.success("✅ Macro regime gate was active and kill switch was inactive at last run.")

    st.divider()

    # ── 4. Strategy P&L ──────────────────────────────────────────────────────
    st.markdown("### 💹 Strategy P&L")
    try:
        from transactions_store import TransactionsStore

        ts = TransactionsStore()
        closed = ts.closed_trades_df()
        if not closed.empty and {"realized_pnl", "strategy_id"} <= set(closed.columns):
            pnl = (closed.groupby("strategy_id")["realized_pnl"].sum()
                   .round(2).reset_index()
                   .rename(columns={"realized_pnl": "Realized P&L ($)"}))
            st.dataframe(pnl, width="stretch")
        else:
            st.caption("No closed trades in transactions store yet.")
    except Exception as exc:
        st.caption(f"(transactions store unavailable: {exc})")

    st.divider()
    _render_observability_system_telemetry()

    st.divider()
    _render_observability_latency_heatmap()

    st.divider()
    _render_observability_error_log()


# ---------------------------------------------------------------------------
# Observability — Section 5: System Telemetry
# ---------------------------------------------------------------------------

def _render_observability_system_telemetry() -> None:
    """CPU / memory / disk metrics for the host AND the current Python process.

    Backed by :func:`gui.observability_telemetry.collect_system_telemetry`,
    which falls back to NaN-shaped output when ``psutil`` is unavailable
    (CONSTRAINT #4 — no fabricated zeros).
    """
    from gui.observability_telemetry import collect_system_telemetry, format_bytes

    st.markdown("### 🖥️ System Telemetry")
    st.caption(
        "Resource usage for the host machine and the current Python process. "
        "Refresh the page to re-sample (CPU% is averaged since last call)."
    )
    telemetry = collect_system_telemetry()

    if not telemetry.psutil_available:
        st.warning(
            "`psutil` is not available — telemetry shown as `—`. "
            "Add `psutil` to requirements.txt to re-enable.",
            icon="ℹ️",
        )

    host_col, proc_col = st.columns(2)
    with host_col:
        st.markdown("**Host**")
        h1, h2, h3 = st.columns(3)
        h1.metric("CPU %",
                  f"{telemetry.cpu_percent:.1f}%" if telemetry.psutil_available else "—",
                  help=f"{telemetry.cpu_count_logical} logical cores"
                       if telemetry.cpu_count_logical > 0 else "—")
        h2.metric("Memory %",
                  f"{telemetry.memory_percent:.1f}%" if telemetry.psutil_available else "—",
                  delta=f"{format_bytes(telemetry.memory_used_bytes)} / "
                        f"{format_bytes(telemetry.memory_total_bytes)}",
                  delta_color="off")
        h3.metric("Disk %",
                  f"{telemetry.disk_percent:.1f}%" if telemetry.psutil_available else "—",
                  delta=f"{format_bytes(telemetry.disk_used_bytes)} / "
                        f"{format_bytes(telemetry.disk_total_bytes)}",
                  delta_color="off")
        if not (telemetry.load_avg_1m != telemetry.load_avg_1m):  # not NaN
            st.caption(f"Load avg (1 min): {telemetry.load_avg_1m:.2f}")

    with proc_col:
        st.markdown("**Process (this Python)**")
        p1, p2, p3 = st.columns(3)
        p1.metric("RSS",
                  format_bytes(telemetry.process_rss_bytes)
                  if telemetry.process_rss_bytes >= 0 else "—")
        p2.metric("Process CPU %",
                  f"{telemetry.process_cpu_percent:.1f}%"
                  if telemetry.psutil_available else "—")
        p3.metric("Threads",
                  str(telemetry.process_threads)
                  if telemetry.process_threads >= 0 else "—")

    # Visual saturation cues — only when the host metric is available.
    if telemetry.psutil_available:
        if telemetry.cpu_percent >= 90:
            st.error(f"🔴 CPU saturated at {telemetry.cpu_percent:.0f}% — "
                     "strategy backtests may be queuing.", icon="🔥")
        elif telemetry.cpu_percent >= 75:
            st.warning(f"🟡 CPU at {telemetry.cpu_percent:.0f}% — watch for slowdowns.")

        if telemetry.memory_percent >= 90:
            st.error(f"🔴 Memory at {telemetry.memory_percent:.0f}% — "
                     "consider releasing caches (Reset provider / Reset health).")


# ---------------------------------------------------------------------------
# Observability — Section 6: Data Latency Heatmap
# ---------------------------------------------------------------------------

def _render_observability_latency_heatmap() -> None:
    """Per-symbol fetch-to-ingest latency heatmap fed by Market Data tab.

    Source: ``st.session_state['obs_latency_store']`` — a shared
    :class:`gui.observability_telemetry.LatencySampleStore` populated each time
    the operator clicks **Fetch quotes** on the Market Data tab.
    """
    from gui.observability_telemetry import LatencySampleStore, summarise_latency

    st.markdown("### ⏱️ Data Latency Heatmap")
    st.caption(
        "End-to-end latency from provider quote timestamp to local ingestion. "
        "Fed by the Market Data tab's `Fetch quotes` action — high latency or "
        "stale flags here indicate the strategies are being fed delayed data."
    )

    latency_key = "obs_latency_store"
    if latency_key not in st.session_state:
        st.session_state[latency_key] = LatencySampleStore()
    store: LatencySampleStore = st.session_state[latency_key]
    samples = store.samples()

    if not samples:
        st.info(
            "No latency samples yet. Open the **Market Data** tab and click "
            "**Fetch quotes** to populate.",
            icon="ℹ️",
        )
        return

    summary = summarise_latency(samples)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Samples", summary["count"])
    c2.metric("Median (p50)", f"{summary['p50']:.2f} s")
    c3.metric("p95", f"{summary['p95']:.2f} s")
    if summary["worst_symbol"]:
        c4.metric("Worst symbol",
                  summary["worst_symbol"],
                  delta=f"p95 {summary['worst_p95']:.2f} s",
                  delta_color="inverse")
    else:
        c4.metric("Worst symbol", "—")

    rows = []
    for s in samples:
        rows.append({
            "Symbol": s.symbol,
            "Source": s.source,
            "Quote (UTC)": s.quote_timestamp.isoformat(timespec="seconds"),
            "Ingested (UTC)": s.ingested_at.isoformat(timespec="seconds"),
            "Latency (s)": round(max(0.0, s.latency_seconds), 3),
            "Stale": s.is_stale,
        })
    df = pd.DataFrame(rows)

    try:
        styled = df.style.background_gradient(
            subset=["Latency (s)"], cmap="RdYlGn_r",
            vmin=0, vmax=max(df["Latency (s)"].max(), 1.0),
        )
        st.dataframe(styled, width="stretch", hide_index=True)
    except Exception as exc:  # noqa: BLE001 — fall back to plain table
        logger.debug("Latency heatmap gradient failed (%s); rendering plain table", exc)
        st.dataframe(df, width="stretch", hide_index=True)

    if st.button("🧹 Clear latency samples", key="obs_clear_latency"):
        store.clear()
        st.rerun()


# ---------------------------------------------------------------------------
# Observability — Section 7: Error / Log Aggregation
# ---------------------------------------------------------------------------

def _render_observability_error_log() -> None:
    """Centralised log viewer with level filter + free-text search.

    Reads ``logs/investyo.log`` (the rotating handler configured by
    :func:`alerting.setup_logging`) via
    :func:`gui.observability_telemetry.read_log_tail`.
    """
    from gui.observability_telemetry import (
        VALID_LEVELS,
        filter_log_entries,
        parse_log_lines,
        read_log_tail,
        tally_levels,
    )
    from gui.orchestrator_runner import TELEMETRY_LOG_PATH

    st.markdown("### 🗂️ Error Aggregation")
    st.caption(
        f"Tail of `{TELEMETRY_LOG_PATH}`. "
        "Filter by minimum level and substring; multi-line tracebacks are "
        "preserved so context isn't lost."
    )

    raw_lines = read_log_tail(TELEMETRY_LOG_PATH, max_lines=1000)
    if not raw_lines:
        st.info(
            f"No log file yet at `{TELEMETRY_LOG_PATH}`. "
            "Launch the orchestrator or `main.py` once to populate "
            "(`alerting.setup_logging()` writes the file).",
            icon="ℹ️",
        )
        return

    entries = parse_log_lines(raw_lines)
    tally = tally_levels(entries)

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("CRITICAL", tally.get("CRITICAL", 0))
    k2.metric("ERROR", tally.get("ERROR", 0))
    k3.metric("WARNING", tally.get("WARNING", 0))
    k4.metric("INFO", tally.get("INFO", 0))
    k5.metric("Total lines", len(entries))

    f1, f2 = st.columns([1, 2])
    with f1:
        min_level = st.selectbox(
            "Minimum level", options=list(VALID_LEVELS), index=1,  # default INFO
            key="obs_log_min_level",
        )
    with f2:
        needle = st.text_input(
            "Filter (substring, case-insensitive)",
            value="", key="obs_log_filter",
            placeholder="e.g. ALPACA, KILL_SWITCH, AAPL",
        )

    filtered = filter_log_entries(entries, min_level=min_level,
                                  contains=needle or None)
    if not filtered:
        st.caption("No log lines match the current filter.")
        return

    st.caption(f"Showing {len(filtered)} of {len(entries)} lines (most recent last).")
    # ``st.code`` keeps the monospace + alignment, which matters for
    # log-grep-style scanning. Cap the rendered block so a runaway run does
    # not freeze the browser.
    body = "\n".join(e.raw for e in filtered[-300:])
    st.code(body, language="log")


# ===========================================================================
# Tab 10 — Live Inventory (Task 1.4: Portfolio & Watchlist Sync)
# ===========================================================================

def render_live_inventory() -> None:
    """Render the synchronized portfolio + watchlist inventory + "Sync Now".

    Sources combined (read-only):
      - Robinhood account snapshot (positions, cost basis) — source of truth
        for held shares.
      - Every Robinhood "Lists" entry — discovered via
        :func:`data.robinhood_client.discover_watchlists`.
      - Plain-text watchlist files referenced by the ``SYNC_WATCHLIST_FILES``
        env var.
      - Market-data coverage probe — via
        :func:`data.portfolio_sync.build_sync_report`.
      - Pipeline forecast availability — derived from the last
        ``state_snapshot.json`` (a non-NaN ``Forecast_30`` column means the
        forecasting engine produced a number for that symbol).

    The **🔄 Sync Now** button schedules
    :func:`data.portfolio_sync.async_sync_now` on a background event loop,
    writes the discovered universe to ``DEFAULT_TICKERS`` in ``.env`` via the
    allowlist-bounded :mod:`gui.env_io` writer, and refreshes the panel — all
    without restarting the orchestrator.
    """
    st.subheader("📡 Live Inventory & Synchronization")
    st.caption(
        "Holdings ∪ Robinhood watchlists ∪ file watchlists, reconciled against "
        "the active market-data feeds. Idiosyncratic positions without "
        "market-data coverage stay visible (EQUITY_ONLY) but are flagged so "
        "pricing-dependent metrics can exclude them."
    )

    # ------------------------------------------------------------------ #
    # 1. Resolve a Robinhood snapshot (use the session value if the operator
    #    already fetched one on the Paper Monitor tab — never block here).
    # ------------------------------------------------------------------ #
    snapshot_obj = st.session_state.get("rh_snapshot")
    col_fetch, col_sync = st.columns([1, 1])
    with col_fetch:
        fetch_rh = st.button(
            "📥 Refresh Robinhood snapshot",
            help="Fetch fresh holdings/dividends. Uses the daily cache when fresh.",
            width="stretch",
        )
    with col_sync:
        do_sync = st.button(
            "🔄 Sync Now",
            type="primary",
            help=(
                "Discover holdings + every Robinhood watchlist, reconcile against "
                "the market-data feeds, and write the union to DEFAULT_TICKERS "
                "in .env. Takes effect on the next orchestrator launch."
            ),
            width="stretch",
        )

    if fetch_rh:
        try:
            from data.robinhood_portfolio import fetch_account_snapshot

            snapshot_obj = fetch_account_snapshot()
            st.session_state["rh_snapshot"] = snapshot_obj
        except Exception as exc:  # noqa: BLE001 - never crash the panel
            st.error(f"Robinhood snapshot failed: {exc}")

    # ------------------------------------------------------------------ #
    # 2. Optionally trigger an async sync. Run the coroutine to completion on
    #    a freshly created event loop — Streamlit runs each interaction on a
    #    new thread so we MUST create the loop explicitly.
    # ------------------------------------------------------------------ #
    if do_sync:
        import asyncio

        from data.portfolio_sync import async_sync_now

        # Try to attach an authenticated RobinhoodClient — best-effort.
        client = None
        try:
            from data.robinhood_client import RobinhoodClient

            client = RobinhoodClient()
            client.login()
        except Exception as exc:  # noqa: BLE001
            logger.info("Sync Now without an authenticated client: %s", exc)

        # Forecast set from the latest pipeline snapshot.
        snap = load_state_snapshot()
        forecast_syms = [
            s.get("symbol") for s in snap.get("signals", [])
            if s.get("symbol")
        ]

        with st.spinner("Reconciling portfolio against market-data feeds…"):
            try:
                loop = asyncio.new_event_loop()
                try:
                    report = loop.run_until_complete(
                        async_sync_now(
                            snapshot_obj,
                            client=client,
                            forecast_symbols=forecast_syms,
                            persist_default_tickers=True,
                        )
                    )
                finally:
                    loop.close()
                st.session_state["last_sync_report"] = report
                st.success(
                    f"Synced {report.n_total} symbols "
                    f"({report.n_full} full, {report.n_equity_only} equity-only, "
                    f"{report.n_uncovered} uncovered). DEFAULT_TICKERS updated."
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"Sync failed: {exc}")

    # ------------------------------------------------------------------ #
    # 3. Resolve the report to display: prefer the in-session result, then
    #    the on-disk cache from the most recent run, then nothing.
    # ------------------------------------------------------------------ #
    report = st.session_state.get("last_sync_report")
    cached_dict: Optional[dict] = None
    if report is None:
        from data.portfolio_sync import read_cache

        cached_dict = read_cache()
        if cached_dict is None:
            st.info(
                "No sync report yet. Click **Sync Now** to discover and "
                "reconcile your universe."
            )
            return

    # ------------------------------------------------------------------ #
    # 4. Header strip: counts + provider + timestamp.
    # ------------------------------------------------------------------ #
    if report is not None:
        rows = [s.to_dict() for s in report.symbols.values()]
        n_total = report.n_total
        n_full = report.n_full
        n_equity = report.n_equity_only
        n_unc = report.n_uncovered
        provider_src = report.provider_source or "—"
        funds_src = report.fundamentals_source or "—"
        ts = report.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        # Cached path
        rows = list((cached_dict or {}).get("symbols", {}).values())
        n_total = len(rows)
        n_full = sum(1 for r in rows if r.get("coverage") == "full")
        n_equity = sum(1 for r in rows if r.get("coverage") == "equity_only")
        n_unc = sum(1 for r in rows if r.get("coverage") == "uncovered")
        provider_src = (cached_dict or {}).get("provider_source") or "—"
        funds_src = (cached_dict or {}).get("fundamentals_source") or "—"
        ts = (cached_dict or {}).get("generated_at", "—")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Symbols", n_total)
    c2.metric("FULL coverage", n_full)
    c3.metric("EQUITY_ONLY", n_equity, help="Held but no market-data coverage")
    c4.metric("UNCOVERED", n_unc)
    c5.metric("Provider", str(provider_src),
              help=f"Fundamentals: {funds_src}")
    st.caption(f"Last sync: **{ts}**")

    # ------------------------------------------------------------------ #
    # 5. Inventory table.
    # ------------------------------------------------------------------ #
    if not rows:
        st.info("Sync report is empty.")
        return

    df = pd.DataFrame(rows)
    # Pretty column names + ordering for the visible inventory.
    display_cols = [
        ("symbol", "Symbol"),
        ("held", "Held?"),
        ("coverage", "Coverage"),
        ("quantity", "Qty"),
        ("avg_cost", "Avg Cost"),
        ("current_price", "Current Px"),
        ("cost_basis_delta_per_share", "Δ / share"),
        ("market_value", "Mkt Value"),
        ("is_stale_quote", "Stale?"),
        ("quote_source", "Source"),
        ("forecast_available", "Forecast?"),
        ("has_fundamentals", "Fundamentals?"),
        ("watchlists", "Lists"),
        ("diagnostic", "Diagnostic"),
    ]
    keep = [src for src, _ in display_cols if src in df.columns]
    rename = {src: lbl for src, lbl in display_cols if src in df.columns}
    df = df[keep].rename(columns=rename).copy()

    # Convert the watchlists list-of-strings to a comma-joined string so the
    # built-in dataframe renderer doesn't truncate to "[...]" text.
    if "Lists" in df.columns:
        df["Lists"] = df["Lists"].apply(
            lambda v: ", ".join(v) if isinstance(v, (list, tuple)) else (v or "")
        )

    # Filter widgets — pure pandas slicing, no extra dependency.
    f1, f2, f3 = st.columns(3)
    held_only = f1.checkbox("Held only", value=False)
    only_gaps = f2.checkbox(
        "Coverage gaps only", value=False,
        help="Show EQUITY_ONLY + UNCOVERED + QUOTES_ONLY.",
    )
    only_no_forecast = f3.checkbox("No forecast", value=False)

    filtered = df.copy()
    if held_only and "Held?" in filtered.columns:
        filtered = filtered[filtered["Held?"] == True]  # noqa: E712
    if only_gaps and "Coverage" in filtered.columns:
        filtered = filtered[filtered["Coverage"] != "full"]
    if only_no_forecast and "Forecast?" in filtered.columns:
        filtered = filtered[filtered["Forecast?"] == False]  # noqa: E712

    st.dataframe(filtered, width="stretch", hide_index=True)

    # ------------------------------------------------------------------ #
    # 6. Watchlist breakdown — quick reference of where symbols originated.
    # ------------------------------------------------------------------ #
    with st.expander("📂 Watchlists discovered", expanded=False):
        if report is not None:
            wl_map = report.watchlists
        else:
            wl_map = (cached_dict or {}).get("watchlists", {})
        if not wl_map:
            st.caption(
                "No Robinhood watchlists discovered. (Authenticate the "
                "RobinhoodClient or set SYNC_WATCHLIST_FILES.)"
            )
        else:
            for name, syms in wl_map.items():
                syms_list = list(syms) if isinstance(syms, (list, tuple)) else []
                st.markdown(f"**{name}** — {len(syms_list)} symbol(s)")
                st.code(", ".join(syms_list) or "(empty)", language="text")


def utcnow_str() -> str:
    """UTC timestamp string for footer display."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
