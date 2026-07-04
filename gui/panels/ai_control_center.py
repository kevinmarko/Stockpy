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
from gui.panels import load_state_snapshot
from gui.panels.ai_insights import _render_gemini_chart_section
from gui.panels.gravity_audit import _render_gravity_ai_runner_section
from gui.panels.report_viewer import _render_llm_commentary_button


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
        active_provider = rowinfo.get("active_provider")
        if active_provider:
            c3.caption(f"via: **{active_provider}**")
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



