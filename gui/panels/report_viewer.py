from __future__ import annotations

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



