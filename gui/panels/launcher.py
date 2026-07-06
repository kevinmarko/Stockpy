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

    # ── Robinhood Execution Bridge status ───────────────────────────────────
    st.divider()
    _render_robinhood_execution_status()

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


def _render_robinhood_execution_status() -> None:
    """Show the Tier 8 Robinhood execution bridge — mode, queue, receipts.

    Read-only mirror of ``output/execution_queue.json`` (Python-authored
    intents) and ``output/execution_receipts.jsonl`` (agent-authored outcomes
    from the ``/rh-execute`` command). This panel never contacts the
    Robinhood MCP itself — only a Claude Code agent running the
    ``robinhood-execution`` skill does that. See
    ``.claude/skills/robinhood-execution/SKILL.md``.
    """
    from gui.robinhood_execution_panel import (
        EXECUTION_QUEUE_PATH,
        build_reconciliation_summary,
        derive_intent_status,
        is_queue_stale,
        mfa_secret_configured,
        notification_age_seconds,
        ntfy_topic_configured,
        queue_age_seconds,
        read_execution_queue,
        read_execution_receipts,
        read_notification_state,
        read_placed_ledger,
    )
    from gui.robinhood_mode import read_robinhood_execution_mode
    from gui.help_content import SECTION_HELP
    from gui import help_widgets

    st.markdown("### 🤖 Robinhood Execution Bridge")
    help_widgets.glossary_chip("robinhood execution bridge")
    help_widgets.help_expander(
        "ℹ️ About this section", SECTION_HELP.get("robinhood_execution_bridge")
    )

    mode_state = read_robinhood_execution_mode(settings)
    if mode_state.mode == "off":
        st.info("Mode: **off** — the bridge is disabled; run `main.py` after setting "
                 "`ROBINHOOD_EXECUTION_MODE=review` in `.env` to start emitting a queue.")
    elif mode_state.mode == "review":
        st.warning("Mode: **review** (paper/dry-run) — every intent previews only; "
                   "nothing can be placed.")
    else:
        cap_note = (
            f"cap ${mode_state.notional_cap:,.2f}/order" if mode_state.notional_cap_set
            else "⚠ no notional cap set — nothing is placeable until one is configured"
        )
        st.error(f"Mode: **live** — {cap_note}. Placement still requires per-order "
                 "human confirmation in the agent session.")

    if not mfa_secret_configured():
        st.caption(
            "ℹ️ `RH_MFA_SECRET` is not set — the next Robinhood account refresh "
            "(`main.py --refresh-account`, or a stale-cache refresh) will fall back "
            "to an interactive MFA prompt in the terminal. Set `RH_MFA_SECRET` in "
            "`.env` (Base32 TOTP secret) to avoid this."
        )

    if not ntfy_topic_configured():
        st.caption(
            "🔕 Push notifications are off — set `NTFY_TOPIC` in `.env` to get an "
            "ntfy.sh push when the queue gains a new or newly-placeable intent."
        )
    else:
        notif_state = read_notification_state()
        if notif_state is None:
            st.caption("🔔 Push notifications are on — none sent yet.")
        else:
            notif_age_s = notification_age_seconds(notif_state)
            notif_age_label = f"{notif_age_s / 60:.0f} min ago" if notif_age_s == notif_age_s else "unknown age"
            priority_tag = "🔴 high priority" if notif_state.last_notified_priority == "high" else "🔵 default priority"
            st.caption(
                f"🔔 Last notification: **{notif_age_label}** — "
                f"{notif_state.last_notified_count} new intent(s), {priority_tag}."
            )

    snapshot = read_execution_queue()
    if snapshot is None:
        st.caption(
            f"`{EXECUTION_QUEUE_PATH.name}` not found yet — run `python3 main.py` "
            "(with `ROBINHOOD_EXECUTION_MODE` set to `review` or `live`) to generate one."
        )
        return

    stale = is_queue_stale(snapshot)
    age_s = queue_age_seconds(snapshot)
    age_label = f"{age_s / 60:.0f} min ago" if age_s == age_s else "unknown age"

    kpi_cols = st.columns(5)
    with kpi_cols[0]:
        help_widgets.metric_with_help("Intents Queued", snapshot.n_intents, "Intents Queued")
    with kpi_cols[1]:
        help_widgets.metric_with_help("Placeable", snapshot.n_placeable, "Placeable")
    with kpi_cols[2]:
        help_widgets.metric_with_help("Queue Age", age_label, "Queue Age")
    with kpi_cols[3]:
        help_widgets.metric_with_help(
            "Kill Switch",
            "🔴 active" if snapshot.kill_switch_active else "🟢 clear",
            "Kill Switch",
        )
    with kpi_cols[4]:
        help_widgets.metric_with_help("Execution Mode", snapshot.mode, "Execution Mode")

    if stale:
        st.warning(
            "⚠️ Queue is **stale** (> 30 minutes old) — the `/rh-execute` skill will "
            "refuse to place from it. Re-run `python3 main.py` for a fresh queue."
        )

    # Fetch a wider receipts window for status/reconciliation matching than we
    # display in the "recent receipts" expander below — an older receipt still
    # determines an intent's status even if it has scrolled out of the last 20.
    all_receipts = read_execution_receipts(max_lines=200)

    _STATUS_BADGE = {
        "success": "🟢",
        "warning": "🟡",
        "neutral": "⚪",
    }

    if snapshot.intents:
        rows = []
        for intent in snapshot.intents:
            status = derive_intent_status(intent, all_receipts)
            rows.append({
                "Symbol": intent.symbol,
                "Action": intent.action,
                "Qty": intent.qty,
                "Target $": intent.target_notional,
                "Conviction": intent.conviction,
                "Gate OK": "✅" if intent.gate_allowed else "🚫",
                "Placeable": "✅" if intent.allow_place else "—",
                "Status": f"{_STATUS_BADGE.get(status.color, '⚪')} {status.status}",
                "Status detail": status.detail,
                "Gate reasons": "; ".join(intent.gate_reasons) if intent.gate_reasons else "",
            })
        queue_df = pd.DataFrame(rows)
        st.dataframe(queue_df, width="stretch", hide_index=True)
    else:
        st.caption("Queue is empty — no eligible BUY/SELL signals this cycle.")

    display_receipts = all_receipts[-20:]
    with st.expander(f"📜 Recent execution receipts ({len(display_receipts)})", expanded=False):
        if display_receipts:
            st.dataframe(pd.DataFrame(display_receipts), width="stretch", hide_index=True)
        else:
            st.caption(
                "No receipts yet — outcomes are appended to "
                "`output/execution_receipts.jsonl` by the `/rh-execute` agent session, "
                "not by the pipeline."
            )

    placed_ledger = read_placed_ledger()
    recon = build_reconciliation_summary(placed_ledger, all_receipts)
    with st.expander(
        f"🧾 Placement reconciliation ({recon.placed_count} placed)", expanded=False
    ):
        if recon.placed_count == 0:
            st.caption(
                "No placed orders yet — `output/execution_placed.jsonl` is populated by "
                "the `/rh-execute` agent session after a live placement."
            )
        else:
            st.caption(
                f"{len(recon.matched)} of {recon.placed_count} ledger entries have a "
                "matching 'placed' receipt."
            )
            if recon.unmatched:
                st.warning(
                    f"⚠️ {len(recon.unmatched)} ledger entr"
                    f"{'y has' if len(recon.unmatched) == 1 else 'ies have'} no matching "
                    "receipt — investigate a possible receipt/ledger divergence."
                )
                st.dataframe(pd.DataFrame(recon.unmatched), width="stretch", hide_index=True)


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



