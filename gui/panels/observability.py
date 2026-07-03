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


