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


