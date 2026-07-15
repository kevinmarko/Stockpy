"""InvestYo Command Center — Strategy Matrix & Risk Gating tab. Enumerates the registered signal modules, edits their weights, toggles modules on/off (DISABLED_SIGNAL_MODULES), and controls the global kill switch, all via the allowlist-bounded gui.env_io writer."""

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
# Imported lazily-safe (gui.panels.__init__ imports this module after defining
# load_state_snapshot) — same pattern as gui/panels/paper_monitor.py and
# gui/panels/observability.py.
from gui.panels import load_state_snapshot
from gui.progress_ui import busy


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
                    with busy(f"Applying {chosen_mode.label}…"):
                        new_state = set_active_mode(chosen_mode)
                    st.success(
                        f"Mode written to `.env` → {new_state.mode.label}. "
                        "Takes effect on the next orchestrator / advisory launch."
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to persist mode: {exc}")
        with col_cancel:
            help_widgets.section_caption("strategy_matrix.mode_consistency")



def _render_strategy_version_registry() -> None:
    """Show each signal module's deployment fingerprint (sha256 prefix + mtime).

    Backed by :func:`gui.strategy_registry.list_strategy_versions`. Useful for
    answering "did I really redeploy the meta-labeler since last week's run?"
    without having to scroll git log.
    """
    from gui.strategy_registry import list_strategy_versions

    st.markdown("### 📜 Strategy Version Registry")
    help_widgets.section_caption("strategy_matrix.version_registry")
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



# ===========================================================================
# Score-component decomposition, meta-label distribution, regime impact,
# and symbol comparison — sourced from output/state_snapshot.json's
# per-signal "score_components" / "meta_label_composite" / "regime_multiplier"
# / "kelly_target_pre_regime" / "kelly_target_post_regime" keys, additively
# written by main.py's _write_state_snapshot() (via engine/advisory.py /
# strategy_engine.py). These keys are ABSENT from snapshots written by
# main_orchestrator.py (a file outside this task's scope) — every helper
# below degrades to a friendly message rather than crashing when a key is
# missing (CONSTRAINT #6 dead-letter UI).
# ===========================================================================


def _signal_lookup(snap: dict) -> Dict[str, dict]:
    """Return ``{symbol: signal_dict}`` from the snapshot's ``signals`` list."""
    out: Dict[str, dict] = {}
    for s in snap.get("signals", []) or []:
        sym = s.get("symbol")
        if sym:
            out[str(sym).upper()] = s
    return out


def _render_score_decomposition() -> None:
    """Section 1 — per-symbol score-component breakdown (bar chart).

    Shows each active signal module's weighted contribution
    (``score * weight``) to the selected symbol's final aggregated score,
    sourced from ``state_snapshot.json``'s ``signals[].score_components``
    dict (written by ``strategy_engine.evaluate_security``'s
    ``Score_Components`` field via ``engine/advisory.py``).
    """
    st.markdown("### 🧮 Score Component Decomposition")
    st.caption(
        help_widgets.metric_help("strategy_matrix.score_components")
        or "How each active signal module contributed to a symbol's final score."
    )

    snap = load_state_snapshot()
    lookup = _signal_lookup(snap)
    if not lookup:
        st.info("No snapshot data yet — run the pipeline to populate the Strategy Matrix.")
        return

    symbols = sorted(lookup.keys())
    selected = st.selectbox(
        "Symbol", symbols, key="score_decomp_symbol",
        help="Pick a symbol to see which signal modules drove its final score.",
    )
    sig = lookup.get(selected, {})
    components = sig.get("score_components") or {}

    if not components:
        st.info(
            f"No score-component breakdown available for {selected} this cycle "
            "(the strategy engine may have failed, or this snapshot was written "
            "by main_orchestrator.py, which does not yet persist this field)."
        )
        return

    comp_df = pd.DataFrame(
        sorted(components.items(), key=lambda kv: abs(kv[1]), reverse=True),
        columns=["Module", "Weighted Contribution"],
    )
    try:
        import plotly.express as px

        fig = px.bar(
            comp_df, x="Weighted Contribution", y="Module", orientation="h",
            color="Weighted Contribution", color_continuous_scale="RdYlGn",
            title=f"{selected} — weighted score contribution by module",
        )
        fig.update_layout(height=max(250, 40 * len(comp_df)), showlegend=False)
        st.plotly_chart(fig, width="stretch")
    except Exception as exc:  # noqa: BLE001 — plotly optional at runtime
        logger.debug("plotly bar chart unavailable, falling back to st.bar_chart: %s", exc)
        st.bar_chart(comp_df.set_index("Module"))

    st.dataframe(comp_df, width="stretch", hide_index=True)
    st.caption(
        f"Base neutral score is 50; final score ≈ 50 + Σ(weighted contributions) "
        f"= {sig.get('score', float('nan'))}. Only modules active this cycle "
        "(not disabled, not regime-gated) are shown."
    )


def _render_meta_label_distribution() -> None:
    """Section 2 — histogram of meta_label_composite across the portfolio.

    Pre-Stage-4-deployment (the current default, no MetaLabelers registered),
    every symbol's composite is exactly 1.0 by design
    (``ml/meta_labeling.py``'s documented no-op default) — a single spike at
    1.0 is the CORRECT rendering, not a bug.
    """
    st.markdown("### 🏷️ Meta-Label Confidence Distribution")
    st.caption(
        help_widgets.metric_help("strategy_matrix.meta_label_composite")
        or "Distribution of meta-label confidence (geometric mean of active "
        "modules' P(signal correct)) across all symbols in the last snapshot."
    )

    snap = load_state_snapshot()
    signals = snap.get("signals", []) or []
    if not signals:
        st.info("No snapshot data yet — run the pipeline to populate the Strategy Matrix.")
        return

    values = [
        float(s.get("meta_label_composite", 1.0) or 1.0)
        for s in signals
        if "meta_label_composite" in s
    ]
    if not values:
        st.info(
            "meta_label_composite is not present in this snapshot (written by "
            "main_orchestrator.py, which does not yet persist this field)."
        )
        return

    dist_df = pd.DataFrame({"meta_label_composite": values})
    try:
        import plotly.express as px

        fig = px.histogram(
            dist_df, x="meta_label_composite", nbins=20,
            title="Meta-label composite confidence across the portfolio",
        )
        fig.update_layout(height=320, bargap=0.05)
        st.plotly_chart(fig, width="stretch")
    except Exception as exc:  # noqa: BLE001
        logger.debug("plotly histogram unavailable, falling back to st.bar_chart: %s", exc)
        st.bar_chart(dist_df["meta_label_composite"].value_counts().sort_index())

    if all(abs(v - 1.0) < 1e-9 for v in values):
        st.info(
            "Every symbol shows exactly 1.0 — this is expected pre-Stage-4-deployment. "
            "No MetaLabelers are currently registered in `ml.meta_labeling."
            "global_meta_registry`, so `meta_label_proba` defaults to 1.0 (a "
            "multiplicative no-op) for every signal module. This is NOT fabricated "
            "variation; the histogram will spread out once real MetaLabelers are "
            "trained and registered."
        )
    else:
        n_gated = sum(1 for v in values if v == 0.0)
        st.caption(
            f"{len(values)} symbols. {n_gated} currently hard-gated to 0.0 "
            "(a registered MetaLabeler's P(correct) fell below "
            f"`settings.META_LABEL_MIN_CONFIDENCE`={settings.META_LABEL_MIN_CONFIDENCE})."
        )


def _render_regime_multiplier_impact() -> None:
    """Section 3 — Kelly Target before vs. after the HMM regime multiplier.

    Shows what ``StrategyEngine._calculate_kelly_sizing`` produced (pre-regime,
    already MAX_POSITION_WEIGHT-clamped) vs. the final value after
    ``regime_multiplier``'s HMM-derived ``confidence`` and
    ``meta_label_composite`` were multiplied in and re-clamped — so the
    operator can see exactly how much current macro conditions are
    discounting (or boosting) sizing for a symbol right now.
    """
    st.markdown("### 🌡️ Regime-Multiplier Sizing Impact")
    st.caption(
        help_widgets.metric_help("strategy_matrix.regime_multiplier")
        or "Kelly Target before vs. after the HMM regime multiplier + meta-label "
        "composite were applied."
    )

    snap = load_state_snapshot()
    lookup = _signal_lookup(snap)
    if not lookup:
        st.info("No snapshot data yet — run the pipeline to populate the Strategy Matrix.")
        return

    symbols = sorted(lookup.keys())
    selected = st.selectbox(
        "Symbol", symbols, key="regime_impact_symbol",
        help="Pick a symbol to see the macro-driven sizing discount/boost.",
    )
    sig = lookup.get(selected, {})

    pre = sig.get("kelly_target_pre_regime")
    post = sig.get("kelly_target_post_regime")
    if pre is None or post is None or (isinstance(pre, float) and pd.isna(pre)):
        st.info(
            f"Pre/post-regime Kelly Target breakdown is not available for {selected} "
            "(missing from this snapshot — written by main_orchestrator.py, which "
            "does not yet persist this field, or the strategy engine failed this cycle)."
        )
        return

    pre = float(pre)
    post = float(post)
    regime_mult = float(sig.get("regime_multiplier", 1.0) or 1.0)
    meta_comp = float(sig.get("meta_label_composite", 1.0) or 1.0)

    c1, c2, c3 = st.columns(3)
    c1.metric("Kelly Target (pre-regime)", f"{pre * 100:.2f}%")
    c2.metric(
        "Kelly Target (post-regime)", f"{post * 100:.2f}%",
        delta=f"{(post - pre) * 100:+.2f}pp",
    )
    c3.metric("HMM Regime Multiplier", f"{regime_mult:.3f}", help=help_widgets.metric_help("HMM_Risk_On_Probability"))

    try:
        import plotly.graph_objects as go

        fig = go.Figure(data=[
            go.Bar(name="Pre-regime", x=[selected], y=[pre * 100]),
            go.Bar(name="Post-regime", x=[selected], y=[post * 100]),
        ])
        fig.update_layout(
            title=f"{selected} — Kelly Target before/after macro adjustment",
            yaxis_title="Kelly Target (%)", height=320, barmode="group",
        )
        st.plotly_chart(fig, width="stretch")
    except Exception as exc:  # noqa: BLE001
        logger.debug("plotly bar chart unavailable, falling back to st.bar_chart: %s", exc)
        st.bar_chart(pd.DataFrame({"Kelly Target %": [pre * 100, post * 100]}, index=["Pre-regime", "Post-regime"]))

    st.caption(
        f"Meta-label composite currently {meta_comp:.3f} (multiplied in alongside "
        "the regime multiplier, then re-clamped to `settings.MAX_POSITION_WEIGHT`)."
    )


def _render_symbol_comparison() -> None:
    """Section 4 — side-by-side comparison of 2-3 operator-selected symbols.

    Final score, Kelly Target, conviction, GARCH vol, and the score-component
    breakdown for each selected symbol, so "why did A score higher than B"
    has a direct answer in the UI.
    """
    st.markdown("### ⚖️ Symbol Comparison")
    help_widgets.section_caption("strategy_matrix.symbol_comparison")

    snap = load_state_snapshot()
    lookup = _signal_lookup(snap)
    if not lookup:
        st.info("No snapshot data yet — run the pipeline to populate the Strategy Matrix.")
        return

    symbols = sorted(lookup.keys())
    chosen = st.multiselect(
        "Symbols to compare (2-3 recommended)", symbols,
        default=symbols[: min(2, len(symbols))],
        key="symbol_comparison_multiselect",
        max_selections=3,
    )
    if len(chosen) < 2:
        st.info("Select at least 2 symbols to compare.")
        return

    rows = []
    all_modules: set = set()
    for sym in chosen:
        sig = lookup.get(sym, {})
        components = sig.get("score_components") or {}
        all_modules.update(components.keys())
        rows.append({
            "Symbol": sym,
            "Final Score": sig.get("score", float("nan")),
            "Action": sig.get("action", "—"),
            "Kelly Target": sig.get("kelly_target", float("nan")),
            "Conviction": sig.get("advisory_conviction", float("nan")),
            "GARCH Vol": sig.get("garch_vol", float("nan")),
            "Meta-Label Composite": sig.get("meta_label_composite", float("nan")),
            "Regime Multiplier": sig.get("regime_multiplier", float("nan")),
        })

    compare_df = pd.DataFrame(rows).set_index("Symbol")
    st.dataframe(compare_df, width="stretch")

    if all_modules:
        comp_rows = []
        for sym in chosen:
            sig = lookup.get(sym, {})
            components = sig.get("score_components") or {}
            for module in sorted(all_modules):
                comp_rows.append({
                    "Symbol": sym, "Module": module,
                    "Weighted Contribution": components.get(module, 0.0),
                })
        comp_df = pd.DataFrame(comp_rows)
        try:
            import plotly.express as px

            fig = px.bar(
                comp_df, x="Module", y="Weighted Contribution", color="Symbol",
                barmode="group", title="Score-component breakdown by symbol",
            )
            fig.update_layout(height=380, xaxis_tickangle=-30)
            st.plotly_chart(fig, width="stretch")
        except Exception as exc:  # noqa: BLE001
            logger.debug("plotly grouped bar unavailable, falling back to pivot table: %s", exc)
            pivot = comp_df.pivot(index="Module", columns="Symbol", values="Weighted Contribution")
            st.bar_chart(pivot)
    else:
        st.info(
            "No score-component breakdown available for the selected symbols "
            "this cycle — comparison limited to the summary table above."
        )


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
    3.  **Score Component Decomposition** — per-symbol bar chart of each
        active signal module's weighted contribution to the final score.
    4.  **Meta-Label Confidence Distribution** — histogram of
        ``meta_label_composite`` across all symbols in the last snapshot.
    5.  **Regime-Multiplier Sizing Impact** — Kelly Target before vs. after
        the HMM regime multiplier + meta-label composite were applied.
    6.  **Symbol Comparison** — side-by-side score/sizing/component breakdown
        for 2-3 operator-selected symbols.
    7.  **Signal modules** — existing per-module enable/weight form.
    8.  **Manual macro kill switch** — existing GlobalKillSwitch wrapper.
    9.  **Recent risk-gate blocks** — existing block log table.
    """
    help_widgets.explain("strategy_matrix")
    st.subheader("🧩 Strategy Matrix & Risk Gating")

    _render_strategy_mode_toggle()
    st.divider()

    _render_strategy_version_registry()
    st.divider()

    _render_score_decomposition()
    st.divider()

    _render_meta_label_distribution()
    st.divider()

    _render_regime_multiplier_impact()
    st.divider()

    _render_symbol_comparison()
    st.divider()

    # ── Module enable/disable + weights ──────────────────────────────────────
    st.markdown(help_widgets.section_help("strategy_matrix.signal_modules"))

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

    with st.container():
        st.caption("Module state updates in real-time, but click Save below to persist to `.env` for the orchestrator.")
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

        saved = st.button("💾 Save module config", type="primary")

    if saved:
        try:
            with busy("Saving module config…"):
                env_io.write_setting("DISABLED_SIGNAL_MODULES", new_disabled)
                env_io.write_setting("SIGNAL_WEIGHTS", new_weights)
            st.success(
                f"Saved. Disabled: {new_disabled or 'none'}. Re-launch to apply."
            )
        except Exception as exc:
            st.error(f"Failed to save module config: {exc}")

    help_widgets.section_caption("strategy_matrix.regime_multiplier_note")

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


