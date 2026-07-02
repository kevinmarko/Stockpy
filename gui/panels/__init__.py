"""
gui/panels/__init__.py
======================
Package root — re-exports all public ``render_*`` functions and helpers from
sub-modules.  External callers continue to use ``from gui import panels`` /
``from gui.panels import render_launcher`` unchanged.

Sub-modules extracted so far
-----------------------------
- ``_shared.py``  — shared file-backed loaders, constants, and utility helpers
  (extracted 2026-06-29).  Future extractions add more sub-modules here.
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
from gui import env_io, orchestrator_runner, help_widgets
from gui.symbol_search import filter_by_symbol
from gui.orchestrator_runner import StageStatus

# ---------------------------------------------------------------------------
# Shared loaders + utilities — now live in _shared.py; re-exported here for
# backward compatibility so all existing ``from gui.panels import X`` imports
# continue to resolve correctly without any changes at the call sites.
# ---------------------------------------------------------------------------
from gui.panels._shared import (  # noqa: E402
    GICS_SECTORS,
    _BF_EDITOR_COLUMNS,
    _REPO_ROOT,
    _active_symbols,
    _held_symbols,
    _kill_switch,
    _signal_symbols,
    _watchlist_symbols,
    load_block_log,
    logger,
)

# ===========================================================================
# State-snapshot loaders — KEPT HERE (not in _shared.py) so tests can patch
# ``gui.panels._load_state_snapshot_cached`` on the panels namespace without
# chasing module-reference indirection through _shared.
# ===========================================================================


def load_state_snapshot() -> dict:
    """Load the orchestrator's last ``state_snapshot.json`` (empty dict if absent).

    The cache is keyed on the file's **mtime** (not just a TTL), so a fresh
    orchestrator / advisory run is reflected on the NEXT render instead of after
    up to ``DASHBOARD_REFRESH_SECONDS`` (default 30 min) of staleness. The TTL
    remains as an upper bound for the case where mtime is unavailable.
    """
    snap = settings.OUTPUT_DIR / "state_snapshot.json"
    try:
        mtime = snap.stat().st_mtime if snap.exists() else 0.0
    except OSError:
        mtime = 0.0
    return _load_state_snapshot_cached(str(snap), mtime)


@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _load_state_snapshot_cached(path: str, _mtime: float) -> dict:
    """Read + parse the snapshot JSON. ``_mtime`` participates in the cache key
    only — a changed mtime is a cache miss and forces a fresh read."""
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


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
# Tier 9 — Claude analyst commentary button (Reports tab drill-down)
# ===========================================================================

def _render_llm_commentary_button(row: dict, symbol: str) -> None:
    """Render the on-demand Claude analyst commentary control.

    Three render paths driven by :func:`gui.llm_commentary_panel.commentary_status`:

    * ``disabled`` — master switch off; renders a single info caption with the
      .env knob needed to enable.  No button shown.
    * ``missing_key`` — master switch on but ANTHROPIC_API_KEY unset; renders
      a warning + a disabled button so the operator sees the seam exists.
    * ``ready`` — master switch on AND key configured; renders an enabled
      button.  On click, results are cached in ``st.session_state`` keyed by
      the same UTC-day + score-bucket convention as :mod:`llm.cache`, so
      repeat clicks within the same trading day never re-spend tokens.

    Soft-fail (CONSTRAINT #6): every failure path (enricher raises, provider
    returns None, schema mismatch) ends in
    :func:`gui.llm_commentary_panel.format_rationale_markdown` rendering the
    "unavailable" sentinel.  The deterministic ``row["advisory_rationale"]``
    above this button is the source of truth and is never replaced.
    """
    try:
        from gui.llm_commentary_panel import (
            commentary_state_key,
            commentary_status,
            format_rationale_markdown,
            generate_for_symbol_row,
        )
    except Exception as exc:  # pragma: no cover - import-time degrade
        st.caption(f"(LLM commentary helpers unavailable: {exc})")
        return

    status = commentary_status(settings)
    st.markdown("---")
    st.markdown("**🤖 Claude analyst commentary**")

    if status == "disabled":
        st.caption(
            "LLM commentary is off.  Set `LLM_COMMENTARY_ENABLED=true` and "
            "`ANTHROPIC_API_KEY=…` in `.env`, then relaunch the GUI."
        )
        return

    if status == "missing_key":
        st.warning(
            "`LLM_COMMENTARY_ENABLED=true` but `ANTHROPIC_API_KEY` is unset — "
            "set the key in `.env` and relaunch."
        )
        st.button(
            "🤖 Generate analyst commentary",
            key=f"llm_cmt_btn_{symbol}",
            disabled=True,
            width="stretch",
        )
        return

    # status == "ready"
    score_for_key = 0.0
    try:
        score_for_key = float(row.get("score", row.get("advisory_score", 0.0)) or 0.0)
    except Exception:
        pass
    action_for_key = str(
        row.get("action", row.get("advisory_action", "HOLD")) or "HOLD"
    ).upper()
    cache_key = commentary_state_key(
        symbol=symbol, score=score_for_key, action=action_for_key
    )
    session_slot = f"llm_cmt_payload_{cache_key}"

    if st.button(
        "🤖 Generate analyst commentary",
        key=f"llm_cmt_btn_{cache_key}",
        width="stretch",
    ):
        with st.spinner(f"Asking Claude about {symbol}…"):
            payload = generate_for_symbol_row(row)
        st.session_state[session_slot] = payload
        # Mirror into a symbol-keyed map (separate from the cache-key-keyed
        # session_slot above) so cross-tab aggregate views — the AI Insights
        # tab's Claude-vs-Gemini disagreement table — can look up the latest
        # Claude payload for this symbol without knowing the cache-key hash.
        # Mirrors the analogous gemini_by_symbol map in
        # _render_gemini_chart_section.
        claude_mirror = st.session_state.get("ai_insights_claude_by_symbol", {})
        if payload is not None:
            claude_mirror[symbol] = payload
        else:
            claude_mirror.pop(symbol, None)
        st.session_state["ai_insights_claude_by_symbol"] = claude_mirror

    cached = st.session_state.get(session_slot)
    if cached is not None or session_slot in st.session_state:
        st.markdown(format_rationale_markdown(cached))


# ===========================================================================
# Signal Decision Journal — Streamlit section (Reports tab, Tier 1 / 1.3)
# ===========================================================================

def _render_decision_journal_section(signals: list) -> None:
    """Let the operator log whether they acted on, passed, or modified a signal.

    Renders a compact form with three decision buttons per symbol.  Entries
    are appended to ``output/decision_log.jsonl`` via ``gui.decision_log``.
    The optional trade join (``"acted"`` path) links the entry to the nearest
    ``TransactionsStore`` record within 24 hours so the calibration tracker
    (1.2) can filter to signals the operator actually executed.

    Also renders a collapsible past-decisions log so the operator can verify
    what has been recorded.
    """
    st.markdown("**Signal Decision Journal** — log what you decided to do with each signal")

    if not signals:
        st.caption("No signals yet — run the advisory engine from the Launcher tab.")
        return

    from gui.decision_log import (
        ActionTaken,
        decisions_df,
        log_decision,
    )

    log_path = settings.OUTPUT_DIR / "decision_log.jsonl"

    # ── Symbol selector ──────────────────────────────────────────────────────
    sym_options = sorted({str(s.get("symbol", "")).upper() for s in signals if s.get("symbol")})
    if not sym_options:
        st.caption("Signal list has no symbol column.")
        return

    dj_sym = st.selectbox(
        "Symbol to journal",
        options=sym_options,
        key="dj_selected_symbol",
        help="Pick the ticker whose signal you want to record a decision for.",
    )

    # Pull the matching signal dict so we can show context
    sig_match = next(
        (s for s in signals if str(s.get("symbol", "")).upper() == dj_sym),
        {},
    )
    sig_action = (
        sig_match.get("advisory_action")
        or sig_match.get("action")
        or "—"
    )
    sig_conviction = sig_match.get("advisory_conviction") or sig_match.get("conviction")
    sig_ts = sig_match.get("timestamp", "")

    # ── Signal context strip ─────────────────────────────────────────────────
    sc1, sc2, sc3 = st.columns(3)
    sc1.metric("System recommendation", sig_action)
    sc2.metric(
        "Conviction",
        f"{float(sig_conviction):.0%}" if sig_conviction is not None else "—",
    )
    sc3.metric("Symbol", dj_sym)

    # ── Notes (visible for all actions; mandatory prompt for "modified") ──────
    dj_notes = st.text_area(
        "Notes (optional — required context when modifying a signal)",
        value="",
        key="dj_notes",
        height=68,
        placeholder="e.g. 'Size halved — position already large', 'Used limit instead of market'",
    )

    # ── Three decision buttons ────────────────────────────────────────────────
    st.caption("Log your decision:")
    b1, b2, b3 = st.columns(3)

    _LOG_KWARGS: dict = dict(
        signal_action=sig_action,
        conviction=float(sig_conviction) if sig_conviction is not None else None,
        notes=dj_notes.strip(),
        signal_ts=sig_ts,
        log_path=log_path,
    )

    def _do_log(action: ActionTaken) -> None:
        try:
            from transactions_store import TransactionsStore
            ts_store: object | None = TransactionsStore()
        except Exception:
            ts_store = None
        entry = log_decision(
            symbol=dj_sym,
            action_taken=action,
            transactions_store=ts_store,
            **_LOG_KWARGS,
        )
        st.session_state["dj_last_result"] = (action, entry.symbol, entry.trade_id)

    with b1:
        if st.button("✅ Acted", key="dj_btn_acted", use_container_width=True,
                     help="You placed this trade (or are about to)"):
            _do_log("acted")
    with b2:
        if st.button("⏭ Passed", key="dj_btn_passed", use_container_width=True,
                     help="You reviewed but skipped this signal"):
            _do_log("passed")
    with b3:
        if st.button("🔁 Modified", key="dj_btn_modified", use_container_width=True,
                     help="You acted but changed size, limit price, or timing"):
            if not dj_notes.strip():
                st.warning("Please add a note describing how you modified the signal.")
            else:
                _do_log("modified")

    # ── Success feedback ──────────────────────────────────────────────────────
    if "dj_last_result" in st.session_state:
        action_done, sym_done, trade_id_done = st.session_state["dj_last_result"]
        icon = {"acted": "✅", "passed": "⏭", "modified": "🔁"}.get(action_done, "")
        join_note = (
            f" · linked to trade #{trade_id_done}"
            if trade_id_done is not None
            else " · no trade match found within 24 h"
            if action_done == "acted"
            else ""
        )
        st.success(f"Logged: **{sym_done}** → {icon} {action_done}{join_note}")

    # ── Past decisions (collapsible) ──────────────────────────────────────────
    with st.expander("📋 Past decisions log"):
        try:
            hist_df = decisions_df(log_path)
        except Exception as exc:
            st.caption(f"(log unavailable: {exc})")
            return

        if hist_df.empty:
            st.caption("No decisions logged yet.")
            return

        # Show most recent first; drop the internal-only notes-empty rows
        hist_display = hist_df.sort_values("timestamp", ascending=False).reset_index(drop=True)
        st.dataframe(hist_display, hide_index=True)

        st.download_button(
            "⬇️ Export decision log (CSV)",
            data=hist_display.to_csv(index=False).encode("utf-8"),
            file_name="decision_log.csv",
            mime="text/csv",
        )


# ===========================================================================
# Correlation Cluster Section — Streamlit section (Tier 2.5, Reports tab)
# ===========================================================================

def _render_correlation_cluster_section(signals: list) -> None:
    """Hierarchical clustering of symbol returns — on-demand in the Reports tab.

    Fetches 60-day returns for the current signal universe via yfinance,
    computes pairwise correlation clusters (Lopez de Prado distance + Ward
    linkage), and renders:

    * **Cluster assignments** table: Symbol, Cluster ID, Avg Intra-Cluster
      Correlation.
    * **Cluster Concentration** bar: per-cluster aggregate position weight (%)
      so the operator can see "you'd be 40% in the mega-cap-tech cluster if
      you take all these BUYs".

    No live data is fetched until the operator clicks the "Compute clusters"
    button (on-demand, never automatic).
    """
    with st.expander("📊 Correlation Cluster Awareness (Tier 2.5)", expanded=False):
        if not signals:
            st.info("No signals available. Run the pipeline from the Launcher tab first.")
            return

        syms = sorted({str(s.get("symbol", "")).upper() for s in signals if s.get("symbol")})
        if not syms:
            st.caption("Signal frame has no 'symbol' field — cannot cluster.")
            return

        st.caption(
            f"{len(syms)} symbol(s) in current signal universe. "
            "Clustering uses 60-day yfinance returns (fetched on demand)."
        )

        col1, col2 = st.columns([2, 1])
        with col1:
            lookback = st.slider(
                "Return lookback (days)",
                min_value=20,
                max_value=252,
                value=int(settings.CORRELATION_CLUSTER_LOOKBACK_DAYS),
                step=5,
                key="cluster_lookback",
            )
        with col2:
            threshold = st.slider(
                "Distance threshold",
                min_value=0.1,
                max_value=1.0,
                value=float(settings.CORRELATION_CLUSTER_THRESHOLD),
                step=0.05,
                key="cluster_threshold",
                help="d=sqrt(0.5*(1-ρ)). At 0.4 → stocks with |ρ|>0.68 merge.",
            )

        if st.button("🔗 Compute Clusters", key="compute_clusters_btn"):
            with st.spinner(f"Fetching {lookback}-day returns for {len(syms)} symbols…"):
                try:
                    from research_engine import fetch_returns_for_clustering, compute_correlation_clusters
                    returns_df = fetch_returns_for_clustering(syms, lookback_days=lookback)
                    if returns_df.empty:
                        st.warning("Could not fetch returns data. Check network connectivity.")
                        return
                    labels, summary = compute_correlation_clusters(
                        returns_df, distance_threshold=threshold
                    )
                    st.session_state["cluster_labels"] = labels
                    st.session_state["cluster_summary"] = summary
                    st.session_state["cluster_signals"] = signals
                except Exception as exc:
                    st.error(f"Clustering failed: {exc}")
                    return

        labels = st.session_state.get("cluster_labels")
        summary = st.session_state.get("cluster_summary")
        cached_signals = st.session_state.get("cluster_signals", [])

        if labels is None or summary is None:
            st.caption("Click 'Compute Clusters' to run the analysis.")
            return

        # ── Cluster assignment table ──────────────────────────────────────────
        st.markdown("**Symbol → Cluster Assignments**")
        sig_map = {
            str(s.get("symbol", "")).upper(): s
            for s in cached_signals if s.get("symbol")
        }
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
        assign_df = pd.DataFrame(rows).sort_values(["Cluster", "Symbol"])
        st.dataframe(assign_df, width="stretch", hide_index=True)

        # ── Cluster concentration ─────────────────────────────────────────────
        if not summary.empty:
            st.markdown("**Per-Cluster Concentration (sum of Kelly Targets)**")
            conc_rows = []
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
            conc_df = pd.DataFrame(conc_rows).sort_values("Cluster ID")
            st.dataframe(conc_df, width="stretch", hide_index=True)

            # Highlight clusters with heavy concentration
            heavy = [
                r for r in conc_rows
                if float(r["Total Position %"].strip("%")) / 100 > 0.30
            ]
            if heavy:
                names = ", ".join(f"Cluster {r['Cluster ID']}" for r in heavy)
                st.warning(
                    f"⚠️ High concentration: {names} together exceed 30% of total "
                    "Kelly-weighted position. Consider diversifying across clusters "
                    "before acting on all BUY signals simultaneously."
                )


# ===========================================================================
# Recommendation Tracking — Streamlit section (Tier 4.1)
# ===========================================================================

def _render_recommendation_tracking_section() -> None:
    """Tier 4.1 — Recommendation tracking error: model vs. operator decisions.

    Joins the 1.3 decision journal with historical prices to compare:
    * **Model return** — paper-equivalent return for every logged BUY signal
      held exactly ``horizon_days``, weighted by published conviction.
    * **Operator return** — actual return from closed trades where the operator
      chose to act (``action_taken="acted"``).

    The delta tells the operator whether their judgment additions to the model
    (e.g. "I passed on that BUY because earnings were next week") are helping
    or hurting alpha over the model's mechanical baseline.
    """
    import math as _math

    st.markdown("---")
    st.markdown("### 📊 Recommendation Tracking vs. Actual Decisions")
    st.caption(
        "Model return = conviction-weighted paper return had you taken every BUY signal "
        "and held for the horizon.  Operator return = average actual closed-trade return "
        "from acted signals.  **Delta > 0 → your judgment adds alpha over the model.**"
    )

    try:
        from evaluation_engine import recommendation_tracking_report
        from transactions_store import TransactionsStore
        from gui.decision_log import DEFAULT_LOG_PATH
    except ImportError as exc:
        st.caption(f"(recommendation tracking unavailable: {exc})")
        return

    horizon = st.slider(
        "Return horizon (calendar days)",
        min_value=5, max_value=90, value=30, step=5,
        key="rec_tracking_horizon",
        help="How many calendar days after the signal to measure the model's paper return.",
    )

    @st.cache_data(ttl=300)
    def _load_tracking(h: int) -> Dict[str, Any]:
        try:
            store = TransactionsStore()
            return recommendation_tracking_report(
                log_path=DEFAULT_LOG_PATH,
                transactions_store=store,
                horizon_days=h,
            )
        except Exception as exc:
            logger.warning("_render_recommendation_tracking_section: %s", exc)
            return {
                "rows": [], "model_return_30d": float("nan"),
                "operator_return_30d": float("nan"), "delta": float("nan"),
                "n_signals": 0, "n_acted": 0, "n_completed": 0,
                "n_with_exit": 0, "horizon_days": h,
            }

    rpt = _load_tracking(horizon)

    n_sig = rpt["n_signals"]
    if n_sig == 0:
        st.info(
            "No BUY signals in the decision log yet.  "
            "Use the **Signal Decision Journal** section above to log decisions, "
            "then return here after the horizon elapses to see the tracking report."
        )
        return

    model_ret = rpt["model_return_30d"]
    op_ret = rpt["operator_return_30d"]
    delta = rpt["delta"]
    n_completed = rpt["n_completed"]
    n_with_exit = rpt["n_with_exit"]

    def _pct(v: float) -> str:
        return "—" if _math.isnan(v) else f"{v * 100:+.2f}%"

    def _delta_label(d: float) -> str:
        if _math.isnan(d):
            return "— (insufficient data)"
        if d > 0.005:
            return f"{d * 100:+.2f}% ✅ judgment adds value"
        if d < -0.005:
            return f"{d * 100:+.2f}% ⚠️ judgment costs alpha"
        return f"{d * 100:+.2f}% ≈ neutral"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "BUY Signals Logged", n_sig,
        help=f"{n_completed} completed (horizon elapsed); {n_sig - n_completed} pending.",
    )
    c2.metric(
        f"Model {horizon}d Return", _pct(model_ret),
        help=(
            f"Conviction-weighted {horizon}-day paper return across {n_completed} "
            "completed BUY signals (acted + passed)."
        ),
    )
    c3.metric(
        "Operator Return", _pct(op_ret),
        help=f"Simple-mean actual return from {n_with_exit} acted+closed trades.",
    )
    c4.metric("Delta (Op − Model)", _delta_label(delta))

    # Narrative summary
    if not _math.isnan(model_ret) and not _math.isnan(op_ret):
        st.markdown(
            f"> **If you'd taken every BUY signal at the published conviction-weighted "
            f"size and held {horizon} days:** paper return = **{_pct(model_ret)}**  \n"
            f"> **Your actual closed-trade decisions returned:** **{_pct(op_ret)}**  \n"
            f"> **Judgment edge:** **{_delta_label(delta)}**"
        )
    elif n_completed == 0:
        st.info(
            f"No BUY signals have reached the {horizon}-day horizon yet.  "
            "Check back once the horizon elapses for the first signals you logged."
        )
    elif n_with_exit == 0:
        st.info(
            "Model returns are ready but no acted signals have closed trades yet.  "
            "Once linked trades are closed in the Transactions Store the operator "
            "return will populate automatically."
        )

    # Per-signal breakdown table
    if rpt["rows"]:
        with st.expander(f"Per-signal breakdown ({len(rpt['rows'])} BUY signals logged)"):
            raw_df = pd.DataFrame(rpt["rows"])
            display_cols = [
                "symbol", "signal_action", "conviction", "action_taken",
                "model_return", "actual_return", "days_held", "completed",
            ]
            show_cols = [c for c in display_cols if c in raw_df.columns]
            fmt = raw_df[show_cols].copy()
            for col in ("model_return", "actual_return"):
                if col in fmt.columns:
                    fmt[col] = fmt[col].apply(
                        lambda v: (
                            f"{v * 100:+.2f}%"
                            if isinstance(v, float) and not _math.isnan(v)
                            else "—"
                        )
                    )
            if "conviction" in fmt.columns:
                fmt["conviction"] = fmt["conviction"].apply(
                    lambda v: f"{v:.2f}" if isinstance(v, float) else str(v)
                )
            st.dataframe(fmt, use_container_width=True, hide_index=True)


# ===========================================================================
# Conviction Calibration — Streamlit section (consumed by Reports tab, 1.2)
# ===========================================================================

def _render_calibration_section() -> None:
    """Reliability diagram: conviction score vs actual win rate.

    "When the system says 0.80, does it actually win 80% of the time?"
    Uses matplotlib embedded via st.pyplot for the diagonal reference line
    that a native st.bar_chart cannot render.
    """
    st.markdown("**Conviction Calibration** — does model confidence track real outcomes?")

    try:
        from evaluation_engine import calibration_curve
        from transactions_store import TransactionsStore
        cal_df = calibration_curve(TransactionsStore())
    except Exception as exc:
        st.caption(f"(calibration unavailable: {exc})")
        return

    scored = cal_df.dropna(subset=["win_rate"])
    if cal_df.empty or scored.empty:
        st.info(
            "No conviction data yet. Conviction scores are stored when trades are recorded "
            "via `TransactionsStore.record_trade(conviction=...)`. They will appear here "
            "after the advisory engine has closed trades with conviction annotations."
        )
        return

    total = int(cal_df["count"].sum())
    total_wins = float((cal_df["win_rate"].fillna(0) * cal_df["count"]).sum())
    overall_wr = total_wins / total if total > 0 else float("nan")
    cal_error = float((scored["win_rate"] - scored["bin_center"]).abs().mean())

    kc1, kc2, kc3, kc4 = st.columns(4)
    kc1.metric("Trades w/ Conviction", str(total))
    kc2.metric("Overall Win Rate", f"{overall_wr:.1%}" if overall_wr == overall_wr else "—")
    kc3.metric(
        "Calibration Error (MAE)", f"{cal_error:.3f}",
        help="Mean |actual_win_rate − conviction_bin_center|. 0 = perfect calibration.",
    )
    kc4.metric("Bins w/ Data", str(len(scored)))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(
        scored["bin_center"], scored["win_rate"],
        width=0.09, alpha=0.75, color="#4c8cff", label="Actual win rate",
    )
    ax.plot([0, 1], [0, 1], "k--", linewidth=1.2, label="Perfect calibration")
    ax.set_xlabel("Conviction (model output)")
    ax.set_ylabel("Win rate (actual)")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_title("Reliability Diagram")
    ax.legend(fontsize=8)
    fig.tight_layout()
    st.pyplot(fig, use_container_width=True)
    plt.close(fig)

    with st.expander("📊 Calibration table"):
        st.dataframe(cal_df, hide_index=True)


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
    help_widgets.explain("launcher")
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
        _stage_icons: dict = {
            StageStatus.SUCCESS: "✅",
            StageStatus.ACTIVE:  "🟡",
            StageStatus.ERROR:   "🔴",
            StageStatus.PENDING: "⚪",
            StageStatus.SKIPPED: "⏭️",
            # Legacy string literals for callers that haven't updated yet.
            "done":    "✅",
            "active":  "🟡",
            "pending": "⚪",
            "idle":    "⚪",
            "error":   "🔴",
            "skipped": "⏭️",
        }
        stage_cols = st.columns(len(stage_status))
        for col, (label, status) in zip(stage_cols, stage_status.items()):
            with col:
                ico = _stage_icons.get(status, "⚪")
                st.metric(label, f"{ico} {status.value if isinstance(status, StageStatus) else status}")

    # ── Safety controls (kill switch + safe-mode toggle) ──────────────────
    st.divider()
    _render_launcher_safety_controls()
    st.divider()

    # ── Preflight readiness gate ───────────────────────────────────────────
    _render_preflight_panel()

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

    # ── Dead-Letter Queue ───────────────────────────────────────────────────
    st.divider()
    _render_dead_letter_queue()

    # ── Auto-refresh ticker (opt-in; cheap because Streamlit reruns are fast) ──
    if running and auto_refresh:
        time.sleep(5)
        st.rerun()


# ---------------------------------------------------------------------------
# Launcher — Safety Controls
# ---------------------------------------------------------------------------

def _render_launcher_safety_controls() -> None:
    """Kill-switch toggle + Safe Mode composite indicator for the Launcher tab.

    Safe Mode is DERIVED (not stored):
        ``is_active(kill_switch) AND DRY_RUN=true``.

    The toggle writes BOTH the kill-switch sentinel AND ``DRY_RUN`` together
    so the composite state is always consistent — there is no intermediate
    "half-safe" window (CONSTRAINT #3 — no new env var).

    UI
    --
    *   **🔴 Kill switch** toggle → activates/deactivates the sentinel file.
    *   **🔵 DRY RUN** checkbox → writes ``DRY_RUN`` to ``.env`` via
        :func:`gui.env_io.write_setting` (allowlist-bounded).
    *   **Safe Mode status** chip — green when both are off (normal),
        amber when DRY_RUN alone, red when kill switch active.
    """
    from execution.kill_switch import GlobalKillSwitch

    ks = GlobalKillSwitch()
    ks_active = ks.is_active()
    dry_run_active = settings.DRY_RUN

    safe_mode = ks_active and dry_run_active

    st.markdown("**🛡️ Safety Controls**")
    c1, c2, c3 = st.columns(3)

    with c1:
        if ks_active:
            st.error("🔴 Kill switch: **ACTIVE**")
            if st.button("✅ Deactivate kill switch", key="launcher_ks_deactivate"):
                ks.deactivate()
                st.success("Kill switch deactivated.")
                st.rerun()
        else:
            st.success("🟢 Kill switch: inactive")
            reason = st.text_input(
                "Activation reason (optional)",
                key="launcher_ks_reason",
                placeholder="e.g. manual safety stop",
            )
            if st.button("🔴 Activate kill switch", key="launcher_ks_activate"):
                ks.activate(reason=reason or "Activated from GUI Launcher tab")
                st.warning("Kill switch activated.")
                st.rerun()

    with c2:
        new_dry = st.checkbox(
            "DRY RUN (no orders submitted)",
            value=bool(dry_run_active),
            key="launcher_dry_run_toggle",
            help="Writes DRY_RUN to .env — takes effect on the next launch.",
        )
        if new_dry != bool(dry_run_active):
            try:
                env_io.write_setting("DRY_RUN", "true" if new_dry else "false")
                st.info("DRY_RUN updated in .env — takes effect on the next launch.")
            except Exception as exc:
                st.error(f"Could not write DRY_RUN: {exc}")

    with c3:
        if safe_mode:
            st.error("🔴 Safe Mode: **ON** — kill switch + dry run active")
        elif ks_active:
            st.warning("🟡 Safe Mode: kill switch active, DRY_RUN off")
        elif dry_run_active:
            st.info("🔵 Safe Mode: DRY_RUN active, kill switch off")
        else:
            st.success("🟢 Safe Mode: OFF — normal operation")


# ---------------------------------------------------------------------------
# Launcher — Preflight Panel
# ---------------------------------------------------------------------------

def _render_preflight_panel() -> None:
    """On-demand preflight readiness gate.

    Runs ``scripts/preflight_check.py --json`` in a subprocess and renders
    the per-check pass/fail table.  Timeout and missing-script errors are
    shown as ``all_passed=False`` — CONSTRAINT #4, never fabricate success.
    """
    from gui.preflight_runner import run_preflight

    st.markdown("**🏁 Pre-Launch Readiness Gate**")
    st.caption(
        "Click to run the 12-check preflight gate (FRED key, kill switch, "
        "heartbeat freshness, validation reports, DB existence, etc.)."
    )

    if st.button("🏁 Run preflight checks", key="launcher_preflight_run"):
        with st.spinner("Running preflight checks…"):
            preflight_report = run_preflight()
        st.session_state["preflight_report"] = preflight_report

    pr = st.session_state.get("preflight_report")
    if pr is None:
        st.caption("No preflight run yet this session.")
        return

    if pr.error:
        st.error(f"Preflight failed to run: `{pr.error}`")
        return

    if pr.all_passed:
        st.success(f"✅ All {len(pr.checks)} checks passed — cleared for launch.")
    else:
        failed = [c for c in pr.checks if not c.passed and not c.warning]
        warn_only = [c for c in pr.checks if not c.passed and c.warning]
        st.error(
            f"❌ {len(failed)} blocking check(s) failed, "
            f"{len(warn_only)} warning(s). Review before launching."
        )

    if pr.checks:
        rows = []
        for c in pr.checks:
            icon = "✅" if c.passed else ("⚠️" if c.warning else "❌")
            rows.append({
                "Check": c.name,
                "Status": f"{icon} {'PASS' if c.passed else ('WARN' if c.warning else 'FAIL')}",
                "Reason": c.reason,
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# Launcher — Dead-Letter Queue section
# ---------------------------------------------------------------------------

def _render_dead_letter_queue() -> None:
    """Display failed symbols from the last pipeline run with per-symbol retry buttons.

    Source: ``output/dead_letter.json`` written by :func:`main_orchestrator.run_pipeline`
    at the end of each run (empty entries = all symbols processed cleanly).

    Each failed symbol shows the pipeline stage at which it failed (e.g.
    ``"strategy"`` vs ``"dto_construction"``), the short exception text, and a
    **🔄 Retry** button that spawns ``main.py`` for just that symbol via
    :func:`gui.orchestrator_runner.launch_symbol_retry`.
    """
    from gui.dead_letter import DEAD_LETTER_PATH, read_dead_letter
    from gui import orchestrator_runner

    st.markdown("### 🔴 Dead-Letter Queue — Failed Symbols")
    st.caption(
        "Symbols that failed during the last pipeline run. "
        "Each failure is isolated — the rest of the run was unaffected (Constraint #6). "
        "Use **🔄 Retry** to re-run just that symbol without a full restart."
    )

    report = read_dead_letter()
    if report is None:
        st.caption(
            f"`{DEAD_LETTER_PATH.name}` not found yet — run the pipeline once to populate."
        )
        return

    if report.is_clean:
        st.success(
            f"✅ All symbols processed cleanly in the last run "
            f"(run_id: {report.run_id[:19]})."
        )
        return

    run_ts = report.run_id[:19] if report.run_id else "unknown time"
    st.warning(
        f"⚠️  **{len(report.entries)} symbol(s) failed** in the last run "
        f"({run_ts}). "
        "Use **🔄 Retry** to re-evaluate a single symbol."
    )

    for entry in report.entries:
        retry_key = f"dl_retry_{entry.symbol}"
        retry_handle_key = f"dl_handle_{entry.symbol}"

        c_sym, c_stage, c_err, c_btn = st.columns([1, 1, 4, 1])
        c_sym.code(entry.symbol)
        c_stage.caption(f"stage: **{entry.stage}**")
        c_err.caption(f"🔸 {entry.error[:160]}")

        if c_btn.button("🔄 Retry", key=retry_key, use_container_width=True):
            retry_handle = orchestrator_runner.launch_symbol_retry(entry.symbol)
            st.session_state[retry_handle_key] = retry_handle
            st.success(f"Retry launched for `{entry.symbol}` — PID {retry_handle.pid}.")

        # Show retry log inline if a retry was launched for this symbol.
        retry_handle = st.session_state.get(retry_handle_key)
        if retry_handle is not None:
            is_running = retry_handle.is_running()
            status_label = "🟢 Running" if is_running else "⏹ Done"
            with st.expander(
                f"Retry log — `{entry.symbol}` ({status_label})",
                expanded=is_running,
            ):
                st.code(
                    orchestrator_runner.read_log_tail(max_lines=60, handle=retry_handle),
                    language="text",
                )


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
    help_widgets.explain("reports")
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
        # Symbol search filter
        report_sym_query = st.text_input(
            "🔍 Filter by symbol",
            value="",
            key="report_symbol_search",
            placeholder="e.g. AAPL",
            help="Case-insensitive prefix/contains match — leave blank to show all.",
        )
        sig_df_display = filter_by_symbol(sig_df, report_sym_query, column="symbol")
        chart_cols = [c for c in ["symbol", "score", "kelly_target"] if c in sig_df_display.columns]
        if chart_cols and not sig_df_display.empty:
            st.bar_chart(sig_df_display.set_index("symbol")[[c for c in chart_cols if c != "symbol"]])
        st.dataframe(sig_df_display, width="stretch")

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

                    # Tier 9 — on-demand Claude analyst commentary button.
                    # Renders the same `enrich_with_llm_rationale` seam the
                    # CLI exposes, with a session-state cache keyed by the
                    # same UTC date + score bucket as llm.cache so repeat
                    # clicks within a trading day are free.
                    _render_llm_commentary_button(row, pick)
            else:
                st.caption("Signal frame has no `symbol` column to drill into.")
    else:
        st.caption("MFE/MAE/Edge populate once closed trades and signals exist.")

    # ── Correlation Cluster Awareness (Tier 2.5) ─────────────────────────────
    _render_correlation_cluster_section(signals)

    # ── Signal decision journal (1.3) ────────────────────────────────────────
    _render_decision_journal_section(signals)

    # ── Recommendation tracking: model vs. operator (4.1) ───────────────────
    _render_recommendation_tracking_section()

    # ── Brinson-Fachler attribution (interactive section) ───────────────────
    _render_brinson_fachler_section()

    # ── Conviction calibration (1.2) ─────────────────────────────────────────
    _render_calibration_section()

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
    help_widgets.explain("settings")
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

    Tier 5.1: when ``settings.ADVISORY_ONLY`` is True (the project default),
    the radio + confirm button are NOT rendered.  A disabled placeholder is
    shown instead with a one-line explanation pointing at the ``.env`` flag,
    so the operator cannot accidentally flip the broker stack on through this
    control while ADVISORY_ONLY is the binding gate.
    """
    from gui.strategy_registry import (
        ExecutionMode,
        mode_banner_text,
        read_active_mode,
        set_active_mode,
    )

    st.markdown("### 🎚️ Global Execution Mode")

    if getattr(settings, "ADVISORY_ONLY", True):
        st.warning(
            "📋 **Advisory mode — broker execution disabled.** "
            "Mode-switching is suppressed because `settings.ADVISORY_ONLY=true`. "
            "Set `ADVISORY_ONLY=false` in `.env` to re-enable Simulation / "
            "Paper / Live selection. This is a deliberate Tier 5.1 quarantine.",
            icon="📋",
        )
        # Read-only display so the operator can still see the underlying
        # DRY_RUN / ALPACA_PAPER state — they just cannot edit it from here.
        state = read_active_mode()
        st.caption(
            f"Underlying flags (read-only): {state.mode.label} "
            f"(DRY_RUN={state.dry_run}, ALPACA_PAPER={state.alpaca_paper})"
        )
        return

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
    help_widgets.explain("strategy_matrix")
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
    help_widgets.explain("paper_monitor")
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


def _render_strategy_health() -> None:
    """Strategy Health view from ``output/gravity_verification_report.json``.

    Reads the verification report written by :func:`Gravity AI Review
    Suite._write_gravity_verification_report` and evaluates each strategy
    against :mod:`validation.thresholds` — the canonical single source of
    truth shared with :mod:`validation.harness`.

    Missing file → informational hint (CONSTRAINT #4 — no fabricated rows).
    Corrupt JSON → same hint. Each strategy shows a gate-by-gate table with
    the observed value, threshold, direction, and pass/fail status.
    """
    from gui.strategy_health import DeployabilityGate, evaluate_gate, read_gravity_report
    from validation.thresholds import DSR_MIN, MAX_DRAWDOWN_MAX, NET_SHARPE_MIN, PBO_MAX

    st.markdown("### 📊 Strategy Health — Deployability Gates")
    st.caption(
        "Sourced from `output/gravity_verification_report.json` (written by the "
        "Gravity AI Review Suite). Evaluated against thresholds in "
        "`validation/thresholds.py` — the same constants used by "
        "`validation/harness.py`."
    )

    strategies = read_gravity_report()

    if not strategies:
        st.info(
            "No strategy health data yet. Run the Gravity AI Review Suite below "
            "to populate `output/gravity_verification_report.json`."
        )
        return

    # Summary row
    total = len(strategies)
    deployable_count = sum(1 for s in strategies if s.get("deployable") is True)
    not_deployable = sum(1 for s in strategies if s.get("deployable") is False)
    unknown = total - deployable_count - not_deployable

    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.metric("Strategies", total)
    sc2.metric("✅ Deployable", deployable_count)
    sc3.metric("❌ Not Deployable", not_deployable)
    sc4.metric("❓ Unknown", unknown)

    for strategy_dict in strategies:
        health = evaluate_gate(strategy_dict)
        with st.expander(
            f"{'✅' if health.deployable else ('❌' if health.deployable is False else '❓')} "
            f"`{health.strategy_id}` — "
            f"{'Deployable' if health.deployable else ('NOT deployable' if health.deployable is False else 'Unknown')}",
            expanded=health.deployable is False,
        ):
            if health.last_audited_at:
                st.caption(f"Last audited: {health.last_audited_at}")

            gate_rows = []
            for g in health.gates:
                icon = "✅" if g.passed is True else ("❌" if g.passed is False else "—")
                gate_rows.append({
                    "Metric": g.metric,
                    "Observed": f"{g.value:.4f}" if g.value is not None else "—",
                    "Threshold": f"{g.threshold}",
                    "Direction": f"must be {g.direction} {g.threshold}",
                    "Gate": f"{icon} {'PASS' if g.passed else ('FAIL' if g.passed is False else 'N/A')}",
                })
            st.dataframe(pd.DataFrame(gate_rows), width="stretch", hide_index=True)

            if health.is_options_selling:
                stress_label = (
                    "✅ Stress passed"
                    if health.stress_passed is True
                    else ("❌ Stress FAILED" if health.stress_passed is False else "— Not run")
                )
                st.caption(f"Options-selling strategy — tail-scenario stress gate: {stress_label}")


# ===========================================================================
# Tier 9 Scope 2 — AI Gravity audit runner section (Safety tab)
# ===========================================================================

def _render_gravity_ai_runner_section() -> None:
    """Render the Safety-tab section that surfaces ``engine.gravity_ai_runner``.

    Four render paths driven by :func:`gui.gravity_ai_panel.runner_status`:

    * ``disabled`` — master switch off.  Renders an info caption with the
      ``.env`` knob needed to enable; no button.
    * ``missing_key`` — switch on but neither key set.  Renders a warning
      + a disabled button so the seam is visible.
    * ``partial_key`` — exactly one of the two keys set.  Renders a
      yellow caution + an enabled button (the runner soft-fails the
      missing side and records it as ``skipped``).
    * ``ready`` — both keys + switch on.  Renders the full panel:
      health colour band + 5-metric KPI strip + "▶️ Run AI Gravity audit"
      button + per-step table with Claude vs Gemini badges +
      raw-report expander.

    Soft-fail (CONSTRAINT #6): every code path that touches the runner
    or the on-disk report is wrapped in try/except.  A missing /
    corrupt / wrong-shape report renders as the "no audit yet"
    sentinel — never an exception bubble.
    """
    st.markdown("### 🤖 AI Gravity audit — Claude auditor + Gemini cross-checker")
    try:
        from gui.gravity_ai_panel import (
            health_caption,
            load_audit_report,
            runner_status,
            step_rows,
            summarise_run,
        )
    except Exception as exc:  # pragma: no cover - import-time degrade
        st.caption(f"(AI Gravity helpers unavailable: {exc})")
        return

    status = runner_status(settings)

    if status == "disabled":
        st.caption(
            "AI Gravity runner is off.  Set `GRAVITY_AI_RUNNER_ENABLED=true` plus "
            "`ANTHROPIC_API_KEY` AND `GEMINI_API_KEY` in `.env`, then relaunch the "
            "GUI.  The structural Python-only Gravity audit above is unaffected."
        )
        return

    if status == "missing_key":
        st.warning(
            "`GRAVITY_AI_RUNNER_ENABLED=true` but neither `ANTHROPIC_API_KEY` nor "
            "`GEMINI_API_KEY` is set — provide at least one and relaunch."
        )
        st.button(
            "▶️ Run AI Gravity audit",
            key="gravity_ai_run_btn",
            disabled=True,
            width="stretch",
        )
        return

    if status == "partial_key":
        st.warning(
            "Only one provider key is configured.  The runner will record the "
            "missing side as `skipped` — disagreement detection requires both."
        )

    # status ∈ {"ready", "partial_key"}
    report = load_audit_report()
    summary = summarise_run(report)

    # Health colour band.
    caption = health_caption(summary)
    if summary.health == "fail":
        st.error(caption)
    elif summary.health == "warn":
        st.warning(caption)
    elif summary.health == "clean":
        st.success(caption)
    else:
        st.info(caption)

    # KPI strip.
    cols = st.columns(5)
    cols[0].metric("Steps", summary.total_steps)
    cols[1].metric("Claude ✅", summary.claude_passed,
                   delta=(-summary.claude_failed) if summary.claude_failed else None,
                   delta_color="inverse")
    cols[2].metric("Gemini ✅", summary.gemini_passed,
                   delta=(-summary.gemini_failed) if summary.gemini_failed else None,
                   delta_color="inverse")
    cols[3].metric("⚠ Disagreements", summary.disagreements)
    cols[4].metric("Last run (UTC)", summary.generated_at[:19] if summary.generated_at else "—")

    if st.button("▶️ Run AI Gravity audit (Claude + Gemini)",
                 key="gravity_ai_run_btn", type="primary", width="stretch"):
        with st.spinner("Calling Claude + Gemini for each of the 7 audit steps…"):
            try:
                from engine.gravity_ai_runner import run_all, write_report  # noqa: PLC0415

                fresh = run_all()
                write_report(fresh)
                # Refresh the loaded view from disk so the table updates in-place.
                report = load_audit_report()
                summary = summarise_run(report)
            except Exception as exc:
                st.error(f"AI Gravity runner failed: {exc}")

    rows = step_rows(report)
    if rows:
        df = pd.DataFrame(rows)
        # Friendlier column titles for the operator-facing table.
        df = df.rename(columns={
            "step_number": "Step",
            "step_title": "Title",
            "claude": "Claude",
            "gemini": "Gemini",
            "disagreement": "⚠ Disagree",
            "score_claude": "Score (C)",
            "score_gemini": "Score (G)",
            "notes": "Notes",
        })
        st.dataframe(df, width="stretch", hide_index=True)
        with st.expander("🔬 Full AI audit JSON"):
            st.json(report)
    else:
        st.caption(
            "No AI Gravity audit yet — click ▶️ above to run all 7 steps.  "
            "Results persist to `output/gravity_ai_audit.json`."
        )


def render_gravity_audit() -> None:
    """Render the Safety tab: Circuit Breakers + Dependency Map + Gravity audit.

    Sections (top to bottom):

    1.  **Strategy Health** — deployability gate table from
        ``output/gravity_verification_report.json``.
    2.  **Circuit Breaker Dashboard** — every tripped breaker derived from the
        existing kill-switch sentinel + risk-gate block log. See
        :mod:`gui.circuit_breakers`.
    3.  **Dependency Map** — declarative source → consumer graph from
        :mod:`gui.dependency_map`. The operator picks the degraded sources
        and the panel shows which strategies / tabs / reports lose coverage.
    4.  **Gravity AI Review Suite** — full audit subprocess (the original
        behavior, kept verbatim).
    """
    help_widgets.explain("gravity")
    st.subheader("🛡️ Safety — Circuit Breakers, Dependencies, Gravity Audit")

    _render_strategy_health()
    st.divider()
    _render_circuit_breaker_dashboard()
    st.divider()
    _render_dependency_map()
    st.divider()
    _render_gravity_ai_runner_section()
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
    help_widgets.explain("options")
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
    help_widgets.explain("market_data")
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
    help_widgets.explain("observability")
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
    _render_observability_heartbeat_trend()

    st.divider()
    _render_observability_system_telemetry()

    st.divider()
    _render_observability_latency_heatmap()

    st.divider()
    _render_observability_error_log()


# ---------------------------------------------------------------------------
# Observability — Section 4b: Heartbeat Trend Sparkline
# ---------------------------------------------------------------------------

def _render_observability_heartbeat_trend() -> None:
    """Sparkline of orchestrator heartbeat age over the current GUI session.

    Why this matters
    ----------------
    A single "heartbeat age = 226 s" metric tells the operator the orchestrator
    is slow *right now*, but it gives no signal about *trajectory*. A rising
    trend over several minutes indicates a memory leak or a hanging background
    thread that will eventually crash the system; a flat trend at 90 s means the
    orchestrator is just doing a long single-ticker computation.

    Implementation
    --------------
    :class:`gui.observability_telemetry.HeartbeatTrendStore` is a 60-sample ring
    buffer persisted across Streamlit reruns via ``st.session_state``.  One sample
    is recorded on every render of this panel (up to once per auto-refresh cycle,
    typically 30 s), so 60 samples ≈ 30 minutes of history.
    """
    from gui.observability_telemetry import HeartbeatTrendStore
    from gui import orchestrator_runner

    st.markdown("### 💓 Heartbeat Age Trend")
    st.caption(
        "Sampled on each tab render (60-sample ring buffer ≈ 30 min at 30 s "
        "auto-refresh). A rising trend indicates the orchestrator is slowing — "
        "check for memory pressure or a hanging background thread."
    )

    store_key = "obs_heartbeat_trend"
    if store_key not in st.session_state:
        st.session_state[store_key] = HeartbeatTrendStore(max_samples=60)
    store: HeartbeatTrendStore = st.session_state[store_key]

    age = orchestrator_runner.heartbeat_age_seconds()
    if age is not None:
        store.record(age)
    elif len(store) == 0:
        # No heartbeat at all yet — record NaN so the chart shows a gap.
        import math
        store.record(math.nan)

    df = store.to_dataframe()

    kc1, kc2, kc3, kc4 = st.columns(4)
    if not df.empty and not df["age_seconds"].isna().all():
        valid = df["age_seconds"].dropna()
        latest_age = valid.iloc[-1] if not valid.empty else float("nan")
        peak_age = valid.max() if not valid.empty else float("nan")

        if latest_age != latest_age:  # NaN
            status = "⚪ No heartbeat"
        elif latest_age > 120:
            status = "🔴 Stale"
        elif latest_age > 60:
            status = "🟡 Slow"
        else:
            status = "🟢 Fresh"

        kc1.metric("Current age", f"{latest_age:.0f} s" if latest_age == latest_age else "—")
        kc2.metric("Peak age", f"{peak_age:.0f} s" if peak_age == peak_age else "—")
        kc3.metric("Samples", len(store))
        kc4.metric("Status", status)

        if status == "🔴 Stale":
            st.error(
                "🔴 Heartbeat is stale. The orchestrator may have crashed or be "
                "hanging on a long computation — check the orchestrator log in the "
                "**Launcher** tab."
            )

        st.line_chart(
            df.rename(columns={"age_seconds": "Heartbeat age (s)"}),
            height=130,
        )
    else:
        kc1.metric("Current age", "—")
        kc2.metric("Peak age", "—")
        kc3.metric("Samples", len(store))
        kc4.metric("Status", "⚪ No data")
        st.info(
            "No heartbeat data yet. Launch the orchestrator and return here after "
            "a few refreshes to see the trend.",
            icon="ℹ️",
        )

    if st.button("🧹 Clear heartbeat history", key="obs_clear_heartbeat"):
        store.clear()
        st.rerun()


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
    """Centralised log viewer with level filter, free-text search, and contextual classification.

    Reads ``logs/investyo.log`` (the rotating handler configured by
    :func:`alerting.setup_logging`) via
    :func:`gui.observability_telemetry.read_log_tail`.

    Above the raw log a **Contextual Error Summary** expander groups errors into:

    * **systemic** — pipeline-wide failures (orchestrator crash, FRED unavailable, schema error).
    * **symbol-specific** — per-ticker failures extracted by :func:`gui.observability_telemetry.extract_symbol_from_message`.

    This allows the operator to immediately distinguish a systemic issue (the whole
    run is broken) from a symbol-specific issue (one ticker failed; retrying it via
    the Dead-Letter Queue on the Launcher tab may be enough).
    """
    from gui.observability_telemetry import (
        VALID_LEVELS,
        classify_log_entry,
        extract_symbol_from_message,
        filter_log_entries,
        parse_log_lines,
        read_log_tail,
        tally_levels,
    )
    from gui.orchestrator_runner import TELEMETRY_LOG_PATH

    st.markdown("### 🗂️ Error Aggregation & Contextual Log")
    st.caption(
        f"Tail of `{TELEMETRY_LOG_PATH}`. "
        "Filter by minimum level and substring; multi-line tracebacks are "
        "preserved so context isn't lost. "
        "Errors above WARNING are automatically classified as **systemic** "
        "(whole-pipeline) or **symbol-specific** (one ticker) in the summary below."
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

    # ── Contextual Error Summary ────────────────────────────────────────────
    error_entries = [
        e for e in entries
        if e.parsed and e.level in ("ERROR", "CRITICAL", "WARNING")
    ]
    if error_entries:
        systemic = [e for e in error_entries if classify_log_entry(e) == "systemic"]
        sym_pairs = [
            (e, extract_symbol_from_message(e.message))
            for e in error_entries
            if classify_log_entry(e) == "symbol_specific"
        ]
        unknown_errors = [
            e for e in error_entries
            if classify_log_entry(e) == "unknown"
        ]

        any_error = bool(systemic or sym_pairs)
        with st.expander(
            f"🔬 Contextual Error Summary"
            f" — {len(systemic)} systemic, {len(sym_pairs)} symbol-specific"
            f"{', ' + str(len(unknown_errors)) + ' unclassified' if unknown_errors else ''}",
            expanded=any_error,
        ):
            if systemic:
                st.error(
                    f"**{len(systemic)} systemic error(s)** — "
                    "failures affecting the whole pipeline run:"
                )
                for e in systemic[-10:]:
                    st.markdown(
                        f"- `[{e.level}]` `{e.logger_name}` — {e.message[:220]}"
                    )
                if len(systemic) > 10:
                    st.caption(f"… and {len(systemic) - 10} more. Filter the log below for full detail.")

            if sym_pairs:
                # Deduplicate: group messages by symbol.
                sym_map: dict[str, list[str]] = {}
                for e, sym in sym_pairs:
                    if sym:
                        sym_map.setdefault(sym, []).append(
                            f"[{e.level}] {e.message[:180]}"
                        )
                st.warning(
                    f"**{len(sym_pairs)} symbol-specific error(s)** across "
                    f"{len(sym_map)} ticker(s) — use **🔄 Retry** on the "
                    "Launcher tab to re-run just that symbol:"
                )
                for sym, msgs in sym_map.items():
                    with st.expander(f"🔹 {sym} — {len(msgs)} error(s)"):
                        for msg in msgs:
                            st.caption(f"• {msg}")

            if unknown_errors and not (systemic or sym_pairs):
                st.caption(
                    f"{len(unknown_errors)} unclassified warning/error line(s) "
                    "could not be attributed to a specific symbol or pipeline stage. "
                    "Review the full log below."
                )

    # ── Filters ────────────────────────────────────────────────────────────
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
    help_widgets.explain("live_inventory")
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
    # Quick-add ticker to watchlist.txt
    # Writes to the file only — never touches .env — so the GUI cannot
    # pollute the environment with stale ticker lists.  Picked up by
    # main.py's _load_watchlist() on the next run_once() call.
    # ------------------------------------------------------------------ #
    st.divider()
    st.caption("**➕ Quick-add ticker** — written to `watchlist.txt`, picked up on next run.")
    _wl_col_ticker, _wl_col_btn = st.columns([3, 1])
    with _wl_col_ticker:
        _new_ticker_raw = st.text_input(
            "Ticker symbol",
            key="live_inv_watchlist_add_ticker",
            placeholder="e.g. NVDA",
            label_visibility="collapsed",
        )
    with _wl_col_btn:
        _add_clicked = st.button(
            "➕ Add to watchlist",
            key="live_inv_watchlist_add_btn",
            help="Append the ticker to watchlist.txt (file-backed; never edits .env).",
            use_container_width=True,
        )
    if _add_clicked:
        _ticker = (_new_ticker_raw or "").strip().upper()
        if not _ticker:
            st.warning("Enter a ticker symbol before clicking Add.")
        elif not _ticker.replace(".", "").replace("-", "").isalnum():
            st.warning(f"'{_ticker}' does not look like a valid ticker symbol.")
        else:
            _wl_path = _REPO_ROOT / "watchlist.txt"
            try:
                # Deduplicate: only append if not already present.
                _existing: list[str] = []
                if _wl_path.exists():
                    _existing = [
                        ln.strip().upper()
                        for ln in _wl_path.read_text(encoding="utf-8").splitlines()
                        if ln.strip() and not ln.strip().startswith("#")
                    ]
                if _ticker in _existing:
                    st.info(f"**{_ticker}** is already in watchlist.txt.")
                else:
                    with _wl_path.open("a", encoding="utf-8") as _fh:
                        _fh.write(f"{_ticker}\n")
                    st.success(
                        f"**{_ticker}** added to `watchlist.txt`. "
                        "It will appear in the universe on the next pipeline run."
                    )
                    logger.info("Watchlist quick-add: appended %s to watchlist.txt", _ticker)
            except OSError as _exc:  # noqa: BLE001
                st.error(f"Could not write watchlist.txt: {_exc}")

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

    inventory_sym_query = st.text_input(
        "🔍 Filter by symbol",
        value="",
        key="inventory_symbol_search",
        placeholder="e.g. TSLA",
        help="Case-insensitive prefix/contains match — leave blank to show all.",
    )

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

    # Apply symbol search filter (uses the "Symbol" column after rename).
    df = filter_by_symbol(df, inventory_sym_query, column="Symbol")

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


@st.cache_data(ttl=300)
def _load_guide_section(anchor: str) -> str:
    """Extract the markdown body of the section whose heading slug == ``anchor``.

    Returns "" when the file is missing or no section matches (CONSTRAINT #6
    — never raises). The returned string excludes the heading line itself and
    stops at the next heading of equal or higher level.
    """
    import re as _re
    if not anchor or not anchor.startswith("#"):
        return ""
    target = anchor.lstrip("#")
    guide = _REPO_ROOT / "docs" / "HOW_TO_GUIDE.md"
    try:
        lines = guide.read_text(encoding="utf-8").splitlines()
    except Exception:
        return ""

    def _slug(text: str) -> str:
        t = text.lower()
        t = _re.sub(r"[^\w\s-]", "", t)
        return t.replace(" ", "-")

    in_section = False
    section_level = 0
    body: list[str] = []
    for line in lines:
        heading_match = _re.match(r"^(#+)\s+(.*)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            if in_section and level <= section_level:
                break
            if not in_section and _slug(heading_text) == target:
                in_section = True
                section_level = level
                continue
        if in_section:
            body.append(line)
    return "\n".join(body).strip()


def render_help() -> None:
    """❓ Help tab — searchable glossary, onboarding tour, and tab descriptions."""
    from gui.onboarding import read_onboarding_state, mark_onboarded, DEFAULT_MARKER
    from gui.help_content import GLOSSARY, search_glossary

    _ob_state = read_onboarding_state(st.session_state, DEFAULT_MARKER)

    if _ob_state.should_show:
        st.info(
            "👋 **Welcome to InvestYo Command Center!** This is an advisory-only "
            "platform — it generates signals and recommendations but **never submits "
            "orders to any broker** while `ADVISORY_ONLY=true`.",
            icon="📋",
        )
        with st.expander("✅ Start here — 4-step checklist", expanded=True):
            st.markdown(
                "1. Set `FRED_API_KEY` in `.env` (free key from "
                "[fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html)).\n"
                "2. Click **🔄 Refresh Data (Advisory)** in the Launcher tab.\n"
                "3. Open the HTML report (`output/daily_report_*.html`).\n"
                "4. Review the Conviction Calibration chart (Reports tab) once "
                "closed trades accumulate."
            )
            if st.button("✅ Got it — don't show again"):
                mark_onboarded(DEFAULT_MARKER)
                st.session_state[__import__("gui.onboarding", fromlist=["SESSION_KEY"]).SESSION_KEY] = True
                st.rerun()
        st.divider()

    st.subheader("❓ In-App Help & Glossary")
    st.caption(
        "Plain-English definitions for every concept the platform uses. "
        "All information here is **informational only** — no orders are sent."
    )

    query = st.text_input("🔍 Search glossary", placeholder="e.g. Kelly, PBO, HMM …")
    if query.strip():
        results = search_glossary(query)
    else:
        results = list(GLOSSARY.values())

    if not results:
        st.info("No matching terms found.")
    else:
        for entry in results:
            with st.expander(f"**{entry.term}**"):
                st.markdown(entry.plain_english)
                if entry.guide_anchor:
                    _section = _load_guide_section(entry.guide_anchor)
                    if _section:
                        st.divider()
                        st.markdown("**📖 From the How-To Guide:**")
                        st.markdown(_section)
                    else:
                        st.caption(
                            f"_(Guide section `{entry.guide_anchor}` not found — "
                            f"see `docs/HOW_TO_GUIDE.md` in the repo.)_"
                        )

    st.divider()
    st.subheader("Tab descriptions")
    tab_ids = [
        "launcher", "reports", "settings", "strategy_matrix",
        "paper_monitor", "gravity", "options", "market_data",
        "observability", "live_inventory",
    ]
    for tab_id in tab_ids:
        help_widgets.explain(tab_id, expanded=False)




# ===========================================================================
# Tab 11 — Prompt Registry
# ===========================================================================

def _pr_source_badge(source: str) -> str:
    """Return a one-word emoji badge describing where a resolved prompt came from."""
    return {
        "pin": "📌 pin",
        "remote": "🌐 remote",
        "cache": "💾 cache",
        "baseline": "📦 baseline",
    }.get(source, source)


def _pr_resolve_source(reg, prompt_id: str) -> Tuple[str, str]:
    """Return ``(resolved_version, source_label)`` for *prompt_id* without calling get().

    Used by the status table to display metadata without echoing the full body.
    The logic mirrors PromptRegistry._resolve_chain() but stops at the first hit
    and returns a label rather than the body — so it is safe to call for every row.
    """
    # Pin
    pinned_ver = getattr(reg, "_pins", {}).get(prompt_id)
    if pinned_ver is not None:
        return pinned_ver, "pin"
    # Remote manifest (already fetched into reg._manifest by a prior sync())
    manifest = getattr(reg, "_manifest", None)
    if manifest is not None:
        ver_obj = manifest.prompts.get(prompt_id)
        if ver_obj is not None:
            return ver_obj.latest, "remote"
    # Disk cache — newest version
    cache = getattr(reg, "_cache", None)
    if cache is not None:
        try:
            versions = cache.list_versions(prompt_id)
            if versions:
                return versions[-1], "cache"
        except Exception:
            pass
    # Baseline
    try:
        from prompt_registry.cache import read_baseline
        if read_baseline(prompt_id) is not None:
            return "baseline", "baseline"
    except Exception:
        pass
    return "—", "unknown"


def _pr_cached_versions(reg, prompt_id: str) -> List[str]:
    """Return all version strings cached on disk for *prompt_id*, sorted ascending."""
    cache = getattr(reg, "_cache", None)
    if cache is None:
        return []
    try:
        return list(cache.list_versions(prompt_id))
    except Exception:
        return []


def _pr_body_for_version(reg, prompt_id: str, version: str) -> Optional[str]:
    """Resolve a specific version body (baseline keyword supported)."""
    try:
        from prompt_registry.__main__ import _resolve_body_for_version
        return _resolve_body_for_version(reg, prompt_id, version)
    except Exception:
        return None


@st.cache_data(ttl=60)
def _pr_all_known_ids(enabled: bool) -> List[str]:
    """Return sorted union of baseline IDs + manifest IDs + pinned IDs.

    Cached for 60 s to avoid re-importing the registry on every widget interaction.
    The ``enabled`` arg is a cache-invalidation key so a Sync can bust the cache.
    """
    try:
        from prompt_registry import get_registry, list_baseline_ids
        reg = get_registry()
        ids: set[str] = set(list_baseline_ids())
        manifest = getattr(reg, "_manifest", None)
        if manifest is not None:
            ids.update(manifest.prompts.keys())
        ids.update(getattr(reg, "_pins", {}).keys())
        return sorted(ids)
    except Exception:
        return []


# ===========================================================================
# Tab 13 — AI Insights (Tier 9 Scope 3)
# ===========================================================================


def render_ai_insights() -> None:
    """Render the AI Insights tab — Claude analyst + Gemini chart vision + aggregate view.

    Three sections, all gated by the same ``LLM_COMMENTARY_ENABLED``
    master switch:

    1.  **Per-symbol Claude analyst note** — reuses
        :mod:`gui.llm_commentary_panel` so this tab and the Reports-tab
        drill-down button share one code path AND one session-state cache.
    2.  **Gemini chart pattern interpretation** — renders a 252-bar
        matplotlib chart for the selected symbol and (on click) sends it
        to Gemini Vision via :func:`llm.chart_insight.generate_chart_pattern_read`.
    3.  **Aggregate disagreement view** — walks the cached Claude /
        Gemini outputs in ``st.session_state`` and renders one row per
        watchlist symbol with the deterministic action, the Claude verdict,
        the Gemini verdict, and a disagreement boolean.

    Every section is wrapped in try/except so a section's failure renders
    an inline error message without aborting the tab (CONSTRAINT #6).
    """
    help_widgets.explain("ai_insights")
    st.subheader("🪄 AI Insights — Claude analyst + Gemini Vision")

    try:
        from gui.ai_insights_panel import (
            derive_disagreement_overview,
            disagreement_summary,
            format_chart_pattern_markdown,
            insights_status,
        )
    except Exception as exc:  # pragma: no cover - import-time degrade
        st.error(f"AI Insights helpers unavailable: {exc}")
        return

    status = insights_status(settings)
    if status == "disabled":
        st.info(
            "AI Insights is off.  Set `LLM_COMMENTARY_ENABLED=true` and at "
            "least `GEMINI_API_KEY=…` (plus `ANTHROPIC_API_KEY=…` for the "
            "analyst notes) in `.env`, then relaunch the GUI."
        )
        return
    if status == "missing_key":
        st.warning(
            "`LLM_COMMENTARY_ENABLED=true` but `GEMINI_API_KEY` is unset.  "
            "The chart-pattern section will be a no-op; the analyst-note "
            "section still works if `ANTHROPIC_API_KEY` is set."
        )

    # ── Symbol picker (shared across the three sections) ────────────────
    snap = load_state_snapshot()
    sig_list = snap.get("signals", []) if isinstance(snap, dict) else []
    if not sig_list:
        st.caption(
            "No `state_snapshot.json` yet — run the orchestrator (Launcher tab) "
            "to populate the signal universe AI Insights iterates over."
        )
        return
    sig_df = pd.DataFrame(sig_list)
    symbols = sorted(sig_df["symbol"].astype(str).unique()) if "symbol" in sig_df.columns else []
    if not symbols:
        st.caption("Signals frame has no `symbol` column to iterate over.")
        return

    selected_symbol = st.selectbox(
        "Symbol", options=symbols, key="ai_insights_symbol"
    )
    row = (
        sig_df[sig_df["symbol"].astype(str) == selected_symbol].iloc[0].to_dict()
        if selected_symbol
        else {}
    )

    # ── Section 1 — Claude analyst note (reuses Reports-tab helper) ────
    st.markdown("---")
    st.markdown("#### 🤖 Claude analyst note")
    try:
        _render_llm_commentary_button(row, selected_symbol)
    except Exception as exc:
        st.error(f"Analyst-note section failed: {exc}")

    # ── Section 2 — Gemini chart pattern interpretation ─────────────────
    st.markdown("---")
    st.markdown("#### 📈 Gemini chart pattern interpretation")
    try:
        _render_gemini_chart_section(selected_symbol)
    except Exception as exc:
        st.error(f"Chart-pattern section failed: {exc}")

    # ── Section 3 — Aggregate disagreement view ─────────────────────────
    st.markdown("---")
    st.markdown("#### 🔍 Aggregate Claude vs Gemini disagreement")
    try:
        # Both maps are populated by symbol-keyed mirrors written at click
        # time: the Claude mirror in _render_llm_commentary_button, the
        # Gemini mirror in _render_gemini_chart_section.
        claude_by_symbol = st.session_state.get("ai_insights_claude_by_symbol", {})
        gemini_by_symbol = st.session_state.get("ai_insights_gemini_by_symbol", {})

        rows = derive_disagreement_overview(
            signals=sig_list,
            claude_map=claude_by_symbol,
            gemini_map=gemini_by_symbol,
        )
        summ = disagreement_summary(rows)
        kpi_cols = st.columns(4)
        kpi_cols[0].metric("Symbols", summ["total_symbols"])
        kpi_cols[1].metric("Both verdicts present", summ["both_present"])
        kpi_cols[2].metric("Agreements", summ["agreements"])
        kpi_cols[3].metric("Disagreements", summ["disagreements"])

        if rows:
            disp_df = pd.DataFrame([
                {
                    "Symbol": r.symbol,
                    "Action (deterministic)": r.advisory_action,
                    "Claude": r.claude_verdict or "—",
                    "Gemini": r.gemini_verdict or "—",
                    "Disagreement": "⚠" if r.disagreement else "",
                }
                for r in rows
            ])
            st.dataframe(disp_df, width="stretch", hide_index=True)
        else:
            st.caption("Run section 1 + 2 above on a few symbols to populate the table.")
    except Exception as exc:
        st.error(f"Aggregate view failed: {exc}")


def _render_gemini_chart_section(symbol: str) -> None:
    """Inner helper — chart render + on-demand Gemini Vision call."""
    if not symbol:
        return
    try:
        from llm.chart_insight import generate_chart_pattern_read, render_price_chart_png
    except Exception as exc:
        st.caption(f"(chart_insight helpers unavailable: {exc})")
        return

    # Fetch bars from the live market-data provider — same path the rest
    # of the platform uses.  Soft-fail to caption on any failure.
    try:
        from data.market_data import get_provider  # noqa: PLC0415

        provider = get_provider()
        bars = provider.get_intraday_bars(symbol, lookback_days=252)
    except Exception as exc:
        st.caption(f"Could not fetch bars for {symbol}: {exc}")
        return

    png = render_price_chart_png(symbol, bars)
    if png:
        st.image(png, caption=f"{symbol} — last 252 bars", width='stretch')
    else:
        st.caption("Chart render failed (insufficient bars).")
        return

    session_slot = f"ai_insights_gemini_payload_{symbol}"
    by_symbol_slot = "ai_insights_gemini_by_symbol"
    if st.button(
        "📈 Interpret chart with Gemini",
        key=f"ai_insights_gemini_btn_{symbol}",
        width="stretch",
    ):
        with st.spinner(f"Asking Gemini to read the {symbol} chart…"):
            result = generate_chart_pattern_read(symbol, bars)
        payload = result.model_dump() if result is not None else None
        st.session_state[session_slot] = payload
        # Mirror map for the aggregate view.
        mirror = st.session_state.get(by_symbol_slot, {})
        if payload is not None:
            mirror[symbol] = payload
        else:
            mirror.pop(symbol, None)
        st.session_state[by_symbol_slot] = mirror

    cached = st.session_state.get(session_slot)
    if cached is not None or session_slot in st.session_state:
        try:
            from gui.ai_insights_panel import format_chart_pattern_markdown
        except Exception:
            st.json(cached)
            return
        st.markdown(format_chart_pattern_markdown(cached))


# ===========================================================================
# Tab 14 — AI Control Center (one place for every AI option, operator-only)
# ===========================================================================

def render_ai_control_center() -> None:
    """Single operator-facing surface for every AI option on the platform.

    Four sections, all operator-triggered (nothing autonomous):

    * **A — Capability grid + toggles.** One row per AI option (Claude
      commentary, Gemini alerts, Gemini chart vision, Gravity AI runner, Opal
      research) with a status badge, a masked key-present badge, and an
      enable/disable toggle written via ``gui.env_io`` (takes effect next
      launch). Provider API keys stay secret-only (CONSTRAINT #3).
    * **B — On-demand per-symbol actions.** A symbol picker + buttons that
      REUSE the exact existing helpers (`_render_llm_commentary_button`,
      `_render_gemini_chart_section`) — no logic duplication.
    * **C — Gravity AI audit.** Reuses `_render_gravity_ai_runner_section`.
    * **D — Operator-launched scheduled run.** Start/Stop buttons that spawn /
      terminate ``main.py --interval N`` / ``--agent`` via
      ``gui.orchestrator_runner``. You start it, you stop it — nothing runs on
      its own.
    """
    help_widgets.explain("ai_control_center")
    st.subheader("🎛️ AI Control Center — every AI option, operator-controlled")

    try:
        from gui.ai_control_center import (
            CAPABILITIES,
            control_center_overview,
            status_badge,
            validate_toggle_write,
        )
    except Exception as exc:  # pragma: no cover - import-time degrade
        st.error(f"AI Control Center helpers unavailable: {exc}")
        return

    # ── Section A — capability grid + toggles ───────────────────────────
    st.markdown("#### A · Capabilities & master switches")
    st.caption(
        "Toggles write to `.env` and take effect on the **next launch** (no "
        "hot-reload). Provider API keys are secret-only — set them by hand in "
        "`.env` (CONSTRAINT #3)."
    )
    overview = control_center_overview(settings)
    cap_by_key = {c.key: c for c in CAPABILITIES}
    for rowinfo in overview:
        cap = cap_by_key[rowinfo["key"]]
        c1, c2, c3, c4 = st.columns([3, 2, 2, 3])
        c1.markdown(f"**{rowinfo['label']}**")
        c1.caption(cap.help)
        c2.markdown(status_badge(rowinfo["status"]))
        keys_present = rowinfo["key_present"]
        key_names = ", ".join(rowinfo["provider_keys"])
        c3.markdown(("🔑 set" if keys_present else "🔓 missing") + f"  \n`{key_names}`")
        # Toggle (only for capabilities with a writable master switch that is built)
        tkey = rowinfo["toggle_key"]
        if tkey and rowinfo["built"]:
            # Read the CURRENT value from .env (not the import-frozen `settings`
            # singleton) so that after a write the next rerun sees the updated
            # value and does not spuriously re-write on every unrelated rerun.
            raw = env_io.get_value(tkey, "").strip().lower()
            if raw in ("true", "1", "yes", "on"):
                cur = True
            elif raw in ("false", "0", "no", "off"):
                cur = False
            else:  # key absent from .env → fall back to the runtime default
                cur = bool(getattr(settings, tkey, False))
            new = c4.toggle(
                f"Enable ({tkey})",
                value=cur,
                key=f"acc_toggle_{rowinfo['key']}",
            )
            if new != cur:
                try:
                    validate_toggle_write(tkey)
                    env_io.write_setting(tkey, "true" if new else "false")
                    c4.success("Saved — effective next launch.")
                except Exception as exc:
                    c4.error(f"Write refused: {exc}")
        elif not rowinfo["built"]:
            c4.caption("🚧 requires build — see `docs/OPAL_BUILD_SPEC.md`")
        else:
            c4.caption("—")

    st.divider()

    # ── Section B — on-demand per-symbol actions ────────────────────────
    st.markdown("#### B · On-demand per-symbol actions")
    snap = load_state_snapshot()
    sig_list = snap.get("signals", []) if isinstance(snap, dict) else []
    if not sig_list:
        st.caption(
            "No `state_snapshot.json` yet — run the pipeline (Section D or the "
            "Launcher tab) to populate the symbol universe."
        )
    else:
        sig_df = pd.DataFrame(sig_list)
        symbols = (
            sorted(sig_df["symbol"].astype(str).unique())
            if "symbol" in sig_df.columns
            else []
        )
        if symbols:
            sym = st.selectbox("Symbol", options=symbols, key="acc_symbol")
            row = (
                sig_df[sig_df["symbol"].astype(str) == sym].iloc[0].to_dict()
                if sym
                else {}
            )
            st.markdown("**🤖 Claude analyst note**")
            try:
                _render_llm_commentary_button(row, sym)
            except Exception as exc:
                st.error(f"Claude commentary failed: {exc}")
            st.markdown("**📈 Gemini chart read**")
            try:
                _render_gemini_chart_section(sym)
            except Exception as exc:
                st.error(f"Gemini chart read failed: {exc}")
            st.markdown("**🔬 Opal research brief**")
            try:
                from gui.ai_control_center import opal_built  # noqa: PLC0415

                if not opal_built():
                    st.caption(
                        "🚧 Opal backend not built yet — see `docs/OPAL_BUILD_SPEC.md`. "
                        "This button activates automatically once `llm/research.py` ships."
                    )
                else:
                    from llm.research import generate_research_brief  # noqa: PLC0415

                    slot = f"acc_opal_payload_{sym}"
                    if st.button("🔬 Generate research brief (Opal)", key=f"acc_opal_btn_{sym}",
                                 width="stretch"):
                        with st.spinner(f"Opal researching {sym}…"):
                            res = generate_research_brief(sym, {})
                        st.session_state[slot] = res.model_dump() if res is not None else None
                    cached = st.session_state.get(slot)
                    if cached is not None or slot in st.session_state:
                        st.json(cached if cached is not None else {"status": "unavailable"})
            except Exception as exc:
                st.error(f"Opal research failed: {exc}")
        else:
            st.caption("Signals frame has no `symbol` column.")

    st.divider()

    # ── Section C — Gravity AI audit (reuse) ────────────────────────────
    st.markdown("#### C · Gravity AI audit")
    try:
        _render_gravity_ai_runner_section()
    except Exception as exc:
        st.error(f"Gravity AI runner section failed: {exc}")

    st.divider()

    # ── Section D — operator-launched scheduled run ─────────────────────
    st.markdown("#### D · Operator-launched scheduled run")
    st.caption(
        "Operator-started and stoppable — **nothing runs autonomously**. During "
        "a scheduled run, enabled Gemini alert-commentary fires automatically; "
        "the per-symbol Claude / Gemini-vision / Opal actions above stay "
        "on-demand."
    )
    handle = st.session_state.get("acc_scheduled_handle")
    running = bool(handle is not None and getattr(handle, "is_running", lambda: False)())
    dcol1, dcol2, dcol3 = st.columns([2, 2, 2])
    interval_min = dcol1.number_input(
        "Interval (minutes)", min_value=1, max_value=1440, value=5, step=1,
        key="acc_interval_min", disabled=running,
    )
    if not running:
        if dcol2.button("▶️ Start scheduled run (--interval)", key="acc_start_interval",
                        width="stretch"):
            try:
                h = orchestrator_runner.launch_scheduled_advisory(
                    mode="interval", interval_seconds=int(interval_min) * 60
                )
                st.session_state["acc_scheduled_handle"] = h
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to start scheduled run: {exc}")
        if dcol3.button("🤖 Start agent loop (--agent)", key="acc_start_agent",
                        width="stretch"):
            try:
                h = orchestrator_runner.launch_scheduled_advisory(mode="agent")
                st.session_state["acc_scheduled_handle"] = h
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to start agent loop: {exc}")
    else:
        st.info(f"Scheduled run active — pid {getattr(handle, 'pid', '?')} "
                f"(mode: {getattr(handle, 'mode', '?')}).")
        if dcol2.button("⏹ Stop", key="acc_stop", width="stretch"):
            try:
                ok = orchestrator_runner.stop_run(handle)
                st.session_state.pop("acc_scheduled_handle", None)
                st.success("Stopped." if ok else "Stop signal sent.")
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to stop: {exc}")
        try:
            tail = orchestrator_runner.read_log_tail(handle=handle)
            with st.expander("Scheduled-run log tail"):
                st.code(tail or "(no output yet)", language="log")
        except Exception:
            pass


def render_prompt_registry() -> None:
    """Prompt Registry — version control for every AI-facing instruction.

    Displays the resolved version + source for each registered prompt ID,
    provides a 🔄 Sync button (calls ``PromptRegistry.sync()`` once on-demand),
    a per-ID diff viewer (select two versions to compare), and an ↩ Rollback
    button that writes the rolled-back pin into ``.env`` via the allowlist-bounded
    :mod:`gui.env_io` writer (effective on the **next** orchestrator launch — the
    running process is never hot-swapped).

    **Security banner** (always rendered):
    "Prompts are advisory text; safety gates are enforced in code and are not
    registry-controlled."

    Design constraints honoured
    ---------------------------
    - CONSTRAINT #3: secrets are never displayed; pins are written via
      ``gui.env_io.write_setting`` (``PROMPT_REGISTRY_PINS`` is in ``ALLOWED_KEYS``).
    - CONSTRAINT #4: resolved bodies are never fabricated; a missing body shows
      the baseline (or "unavailable").
    - CONSTRAINT #5: sync is on-demand only; never called on a timer.
    - CONSTRAINT #6: every network/parse failure degrades gracefully via the
      registry's own fail-closed resolution chain.
    """
    st.subheader("📝 Prompt Registry")

    # ── Mandatory security banner ─────────────────────────────────────────
    st.info(
        "**Prompts are advisory text.** "
        "The registry changes what the AI is *told* — it cannot change what the "
        "platform is *permitted to do*. "
        "Order submission, the advisory quarantine, the risk gate, and the kill "
        "switch are enforced in Python and are **not** registry-controlled.",
        icon="🛡️",
    )

    # ── Lazy import: prompt_registry may not be configured ────────────────
    try:
        from prompt_registry import get_registry, reset_registry, list_baseline_ids
        from prompt_registry.registry import PromptRegistry
    except ImportError as exc:
        st.error(f"prompt_registry package not importable: {exc}")
        return

    reg: PromptRegistry = get_registry()
    is_enabled: bool = getattr(settings, "PROMPT_REGISTRY_ENABLED", False)

    # ── Enabled/disabled banner ───────────────────────────────────────────
    if not is_enabled:
        st.warning(
            "Registry is **disabled** (`PROMPT_REGISTRY_ENABLED=false` in `.env`). "
            "All prompts resolve from the committed baseline — zero network calls. "
            "Set `PROMPT_REGISTRY_ENABLED=true` to enable remote fetch and cache.",
            icon="📦",
        )

    # ── Top action strip: Sync + registry version ─────────────────────────
    col_sync, col_status = st.columns([1, 3])
    with col_sync:
        do_sync = st.button(
            "🔄 Sync prompts",
            type="primary",
            disabled=not is_enabled,
            help=(
                "Fetch the remote manifest, verify every version signature, and "
                "pre-warm the disk cache. On-demand only (CONSTRAINT #5)."
            ),
            width="stretch",
        )
    with col_status:
        manifest = getattr(reg, "_manifest", None)
        if manifest is not None:
            st.caption(
                f"Manifest version: `{manifest.registry_version}` · "
                f"signing alg: `{manifest.signing_alg}`"
            )
        else:
            st.caption("No manifest loaded yet — click **🔄 Sync prompts** to fetch.")

    if do_sync:
        if not is_enabled:
            st.warning("Enable the registry first (`PROMPT_REGISTRY_ENABLED=true`).")
        else:
            with st.spinner("Syncing remote manifest…"):
                try:
                    reg.sync()
                    _pr_all_known_ids.clear()  # bust the ID cache
                    st.success(
                        f"Sync complete. "
                        f"Manifest: `{getattr(reg._manifest, 'registry_version', '?')}`"
                    )
                except Exception as exc:
                    st.error(f"Sync failed (registry fell back to cache/baseline): {exc}")

    st.divider()

    # ── Prompt status table ───────────────────────────────────────────────
    st.markdown("#### Registered prompts")
    all_ids = _pr_all_known_ids(is_enabled)
    if not all_ids:
        st.info("No prompt IDs found. Run a Sync or check that `prompt_registry/baseline/` is intact.")
        return

    rows = []
    for pid in all_ids:
        ver, src = _pr_resolve_source(reg, pid)
        pinned = getattr(reg, "_pins", {}).get(pid, "—")
        cached = _pr_cached_versions(reg, pid)
        rows.append({
            "Prompt ID": pid,
            "Resolved version": ver,
            "Source": _pr_source_badge(src),
            "Pinned": pinned if pinned != "—" else "—",
            "Cached versions": len(cached),
        })

    st.dataframe(
        pd.DataFrame(rows).set_index("Prompt ID"),
        width="stretch",
    )

    st.divider()

    # ── Per-ID detail expander ────────────────────────────────────────────
    selected_id = st.selectbox(
        "Inspect a prompt ID",
        options=["(select…)"] + all_ids,
        key="pr_selected_id",
    )
    if selected_id == "(select…)":
        return

    ver, src = _pr_resolve_source(reg, selected_id)
    cached_versions = _pr_cached_versions(reg, selected_id)
    has_baseline = bool(_pr_body_for_version(reg, selected_id, "baseline"))
    version_choices = (["baseline"] if has_baseline else []) + cached_versions

    # ── 1. View current resolved body ─────────────────────────────────────
    with st.expander(f"👁️ View resolved body  ·  {_pr_source_badge(src)}  ·  `{ver}`", expanded=False):
        try:
            body = reg.get(selected_id)
            if body and not body.startswith("[PROMPT UNAVAILABLE"):
                st.code(body, language="markdown")
            else:
                st.warning(f"Prompt unavailable for `{selected_id}`. Sentinel returned.")
        except Exception as exc:
            st.error(f"Could not resolve body: {exc}")

    # ── 2. Unified diff viewer ─────────────────────────────────────────────
    with st.expander("🔍 Diff two versions", expanded=False):
        if len(version_choices) < 2:
            st.info(
                "Need at least 2 versions to diff (baseline + one cached, or two cached). "
                "Sync to populate the cache."
            )
        else:
            diff_col_a, diff_col_b = st.columns(2)
            with diff_col_a:
                ver_a = st.selectbox(
                    "Version A (from)",
                    options=version_choices,
                    key="pr_diff_ver_a",
                )
            with diff_col_b:
                ver_b = st.selectbox(
                    "Version B (to)",
                    options=version_choices,
                    index=min(1, len(version_choices) - 1),
                    key="pr_diff_ver_b",
                )
            if st.button("Compare", key="pr_diff_btn"):
                body_a = _pr_body_for_version(reg, selected_id, ver_a)
                body_b = _pr_body_for_version(reg, selected_id, ver_b)
                if body_a is None:
                    st.error(f"Version `{ver_a}` not found.")
                elif body_b is None:
                    st.error(f"Version `{ver_b}` not found.")
                else:
                    import difflib
                    diff_lines = list(
                        difflib.unified_diff(
                            body_a.splitlines(keepends=True),
                            body_b.splitlines(keepends=True),
                            fromfile=f"{selected_id}@{ver_a}",
                            tofile=f"{selected_id}@{ver_b}",
                        )
                    )
                    if diff_lines:
                        st.code("".join(diff_lines), language="diff")
                    else:
                        st.success("No differences between the two versions.")

    # ── 3. Rollback / pin control ─────────────────────────────────────────
    with st.expander("↩ Rollback / pin", expanded=False):
        st.caption(
            "Pins take effect on the **next** orchestrator launch. "
            "The running process is never hot-swapped. "
            "Written to `.env` via the allowlist-bounded `gui.env_io` writer."
        )

        current_pin = getattr(reg, "_pins", {}).get(selected_id)
        if current_pin:
            st.info(f"Currently pinned to: `{current_pin}`")
        else:
            st.caption("No pin set — resolves to remote latest or cache.")

        pin_col, rb_col = st.columns(2)

        # Manual pin to a specific version
        with pin_col:
            if version_choices:
                pin_target = st.selectbox(
                    "Pin to version",
                    options=version_choices,
                    key="pr_pin_target",
                )
                if st.button("📌 Set pin", key="pr_set_pin", width="stretch"):
                    body_check = _pr_body_for_version(reg, selected_id, pin_target)
                    if body_check is None:
                        st.error(f"Version `{pin_target}` not found; pin not set.")
                    else:
                        reg._pins[selected_id] = pin_target
                        try:
                            import json
                            pins_json = json.dumps(dict(sorted(reg._pins.items())))
                            env_io.write_setting("PROMPT_REGISTRY_PINS", pins_json)
                            st.success(
                                f"Pinned `{selected_id}` → `{pin_target}`. "
                                "Saved to `.env`; effective on next launch."
                            )
                        except env_io.SecretWriteError as exc:
                            st.error(f"Secret write blocked: {exc}")
                        except env_io.DisallowedKeyError as exc:
                            st.warning(
                                f"Pin set in-memory but `.env` write failed "
                                f"(PROMPT_REGISTRY_PINS not in ALLOWED_KEYS yet): {exc}"
                            )
                        except Exception as exc:
                            st.warning(
                                f"Pin set in-memory but `.env` write failed: {exc}"
                            )
            else:
                st.info("No versions available to pin.")

        # Auto-rollback to previous cached version
        with rb_col:
            st.markdown("**Auto-rollback**")
            st.caption("Repoints the pin to the previous cached version.")
            if st.button("↩ Rollback", key="pr_rollback", width="stretch"):
                try:
                    ok = reg.rollback(selected_id)
                    if ok:
                        new_pin = reg._pins.get(selected_id)
                        try:
                            import json
                            pins_json = json.dumps(dict(sorted(reg._pins.items())))
                            env_io.write_setting("PROMPT_REGISTRY_PINS", pins_json)
                            st.success(
                                f"Rolled back `{selected_id}` → `{new_pin}`. "
                                "Saved to `.env`; effective on next launch."
                            )
                        except env_io.SecretWriteError as exc:
                            st.error(f"Secret write blocked: {exc}")
                        except Exception as exc:
                            st.warning(
                                f"Rolled back in-memory but `.env` write failed: {exc}"
                            )
                    else:
                        st.warning(
                            f"No older cached version found for `{selected_id}`. "
                            "Sync to populate the cache with more versions."
                        )
                except Exception as exc:
                    st.error(f"Rollback failed: {exc}")

        # Clear pin
        if current_pin:
            if st.button("🗑️ Clear pin", key="pr_clear_pin"):
                reg._pins.pop(selected_id, None)
                try:
                    import json
                    pins_json = json.dumps(dict(sorted(reg._pins.items())))
                    env_io.write_setting("PROMPT_REGISTRY_PINS", pins_json)
                    st.success(
                        f"Pin for `{selected_id}` cleared. "
                        "Will resolve to remote latest on next launch."
                    )
                except Exception as exc:
                    st.warning(f"Pin cleared in-memory but `.env` write failed: {exc}")


def utcnow_str() -> str:
    """UTC timestamp string for footer display."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def utcnow_str() -> str:
    """UTC timestamp string for footer display."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
