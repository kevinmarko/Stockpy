"""InvestYo Command Center — Observability / Mission Control tab. Renders the system-health bar (kill switch / regime / VIX / HMM), the Macro Regime Gate toggle, recession-indicator telemetry, strategy P&L, and a read-only Forecast Skill sub-section, all sourced from the file-backed state snapshot."""

from __future__ import annotations

from __future__ import annotations
import io
import json
import logging
import os
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
from gui.observability_panel_helpers import (
    compute_portfolio_heat,
    format_rmse,
    format_skill_weight,
    heartbeat_status,
    hy_oas_badge,
    portfolio_heat_badge,
    regime_emoji,
    sahm_badge,
    vix_badge,
    yield_curve_badge,
)


def _system_health_fragment() -> None:
    """Cheap kill-switch / regime / VIX / HMM health bar, isolated into its own
    fragment so it can refresh independently of the heavy Observability tab
    (equity curve, latency heatmap, telemetry). ``load_state_snapshot()`` is
    mtime-cached, so the re-read here is cheap and reflects fresh orchestrator
    writes. Registered with ``run_every`` via the call-form gate in
    ``render_observability()`` (see the "Live health bar" checkbox)."""
    snap = load_state_snapshot()
    ks = _kill_switch()

    c_ks, c_reg, c_vix, c_hmm = st.columns(4)
    with c_ks:
        if ks.is_active():
            st.error("🚨 Kill switch ACTIVE")
        else:
            st.success("✅ Kill switch inactive")
    with c_reg:
        regime = snap.get("market_regime", "—")
        colour = regime_emoji(regime)
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


def render_observability() -> None:
    """Macro / regime / P&L / account / risk view — Mission Control for the
    platform.

    This tab is the single observability surface for InvestYo — the former
    standalone ``streamlit run observability/dashboard.py`` app has been
    retired; every panel it rendered now lives here (or, for validation-report
    trend analysis, in the Gravity Audit Logs tab — cross-referenced below).

    Sections
    --------
    1.  System-health bar         — kill switch, macro regime, VIX, HMM risk-on.
    2.  Macro Regime Gate         — operator toggle (MACRO_REGIME_GATE_ENABLED)
                                    with live Sahm-Rule and HY-OAS telemetry.
                                    Writes the setting to .env via gui.env_io
                                    (CONSTRAINT #3).
    3.  Recession indicators      — Sahm Rule / HY OAS / yield curve with
                                    threshold colour-coding so the operator can
                                    judge whether a "Risk Off" trigger is
                                    genuine or idiosyncratic.
    4.  Strategy P&L               — realized P&L by strategy from TransactionsStore.
    4a-1. Account Holdings & P&L  — Robinhood snapshot (cache/account_snapshot.json).
    4a-2. Open Positions          — internal book vs. latest pipeline signals.
    4a-3. Portfolio Risk Metrics  — heat / gross / net exposure.
    4a-4. Validation Report Status — per-strategy deployability snapshot.
    4a-5. Recent Closed Trades    — last 20 exits from TransactionsStore.
    4a-6. Equity Curve            — cumulative realized P&L, drawdown, regime overlay.
    4a-7. Risk Gate Block Log     — last 100 blocked orders.
    4b+.  Heartbeat trend / system telemetry / latency heatmap / error log.
    """
    help_widgets.explain("observability")
    st.subheader("📊 Observability — Mission Control")
    help_widgets.section_caption("observability.snapshot_summary")

    snap = load_state_snapshot()

    # ── 1. System-health bar ─────────────────────────────────────────────────
    # Isolated into a fragment: it is a cheap health bar (kill switch / regime /
    # VIX / HMM) that can auto-refresh on its own without redrawing the rest of
    # this expensive tab (equity curve, latency heatmap, telemetry). The tab is
    # costly, so the live tick defaults OFF (manual refresh).
    live_health = st.checkbox(
        "🔴 Live health bar (30s)", value=False, key="obs_health_live",
        help="Auto-refresh just the health bar every 30s (rest of tab stays static)."
    )
    st.fragment(run_every="30s" if live_health else None)(_system_health_fragment)()

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

    help_widgets.section_caption("observability.macro_gate_write")

    st.divider()

    # ── 3. Recession-indicator telemetry ─────────────────────────────────────
    st.markdown("### 📉 Recession Indicator Telemetry")
    help_widgets.section_caption("observability.recession_telemetry")

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
            _b = sahm_badge(sahm)
            getattr(st, _b.level)(_b.message)
        else:
            st.metric("Sahm Rule", "—", help="Not available in last snapshot")

    # HY OAS — threshold 6.0 (RECESSION) / 4.5 (NEUTRAL→CREDIT EVENT)
    with c2:
        if hy_oas is not None:
            st.metric(
                "HY OAS (%)", f"{hy_oas:.2f}",
                help="High-Yield Option-Adjusted Spread. >6.0% → RECESSION; >4.5% → CREDIT EVENT; >6% + yield inversion → RECESSION",
            )
            _b = hy_oas_badge(hy_oas)
            getattr(st, _b.level)(_b.message)
        else:
            st.metric("HY OAS (%)", "—")

    # Yield curve — inversion below -0.25 is part of RECESSION gate
    with c3:
        if yc is not None:
            st.metric(
                "10Y-2Y Spread (%)", f"{yc:.3f}",
                help="Yield curve 10Y-2Y. < -0.25% + HY OAS > 6% → RECESSION",
            )
            _b = yield_curve_badge(yc)
            getattr(st, _b.level)(_b.message)
        else:
            st.metric("10Y-2Y Spread (%)", "—")

    # VIX — kill-switch threshold 30
    with c4:
        if vix_val is not None:
            st.metric(
                "VIX", f"{vix_val:.1f}",
                help="CBOE Volatility Index. > 30 → killSwitch fires",
            )
            _b = vix_badge(vix_val)
            getattr(st, _b.level)(_b.message)
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

        ts = TransactionsStore(readonly=True)
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
    _render_observability_account_holdings()

    st.divider()
    _render_observability_open_positions_vs_signals(snap)

    st.divider()
    _render_observability_portfolio_risk_metrics()

    st.divider()
    _render_observability_validation_status()

    st.divider()
    _render_observability_recent_closed_trades()

    st.divider()
    _render_observability_equity_curve()

    st.divider()
    _render_observability_forecast_skill(snap)

    st.divider()
    _render_observability_risk_gate_block_log()

    st.divider()
    _render_observability_sizing_cap_audit()

    st.divider()
    _render_observability_heartbeat_trend()

    st.divider()
    _render_observability_system_telemetry()

    st.divider()
    _render_observability_latency_heatmap()

    st.divider()
    _render_observability_error_log()


# ---------------------------------------------------------------------------
# Observability — Section 4a-1: Account Holdings & P&L
#
# Ported from the retired ``observability/dashboard.py`` standalone Streamlit
# app (its "💼 Account Holdings & P&L" row). Source of truth for account
# state (CONSTRAINT #4) is ``cache/account_snapshot.json``, written by
# ``data.robinhood_portfolio.fetch_account_snapshot()``. Uses the shared
# ``gui.styling`` Styler helpers (``_color_pnl`` / ``style_severity``) instead
# of re-implementing the green/red P&L coloring that used to live directly in
# the standalone dashboard module.
# ---------------------------------------------------------------------------


def _load_account_snapshot_cache() -> Dict[str, Any]:
    """Load the cached Robinhood account snapshot (holdings + P&L).

    Reads ``cache/account_snapshot.json`` directly (no live Robinhood call) —
    the same file :func:`gui.panels._shared._held_symbols` reads, but this
    loader returns the FULL parsed payload (positions + equity + buying power
    + dividends), not just the symbol list. Returns ``{}`` on any read/parse
    failure so the panel degrades to an instructional message rather than a
    traceback (dead-letter pattern used throughout this codebase).
    """
    cache_path = _REPO_ROOT / "cache" / "account_snapshot.json"
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("account_snapshot.json read failed: %s", exc)
        return {}


def _render_observability_account_holdings() -> None:
    """Account Holdings & P&L — headline metrics + per-position table.

    Mirrors ``observability/dashboard.py``'s account-snapshot row: Total
    Equity / Buying Power / Unrealized P&L / Dividends Received metrics, plus
    a per-position table colour-coded green/red on Unrealized P&L and P&L %
    via :func:`gui.styling.style_severity`.
    """
    from gui.styling import style_severity

    st.markdown("### 💼 Account Holdings & P&L")
    account = _load_account_snapshot_cache()

    if not account:
        st.info(
            "No account snapshot found at `cache/account_snapshot.json`. "
            "Run `python3 main.py --refresh-account` to fetch holdings from Robinhood."
        )
        return

    positions = account.get("positions", {}) or {}
    total_equity = float(account.get("total_equity", 0.0) or 0.0)
    buying_power = float(account.get("buying_power", 0.0) or 0.0)
    total_dividends = float(account.get("total_dividends", 0.0) or 0.0)
    total_unrealized = sum(
        float(p.get("unrealized_pl", 0.0) or 0.0) for p in positions.values()
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Equity", f"${total_equity:,.0f}")
    m2.metric("Buying Power", f"${buying_power:,.0f}")
    m3.metric("Unrealized P&L", f"${total_unrealized:,.0f}", delta=f"{total_unrealized:,.0f}")
    m4.metric("Dividends Received", f"${total_dividends:,.0f}")

    fetched = account.get("fetched_at", "—")
    st.caption(f"Snapshot fetched: {fetched}  ·  {len(positions)} position(s) held")

    if not positions:
        st.info("Account snapshot has no open positions.")
        return

    holdings_df = pd.DataFrame([
        {
            "Symbol": p.get("symbol", sym),
            "Name": p.get("name", "") or "",
            "Qty": float(p.get("quantity", 0.0) or 0.0),
            "Avg Cost": float(p.get("average_cost", 0.0) or 0.0),
            "Price": float(p.get("current_price", 0.0) or 0.0),
            "Market Value": float(p.get("market_value", 0.0) or 0.0),
            "Unrealized P&L": float(p.get("unrealized_pl", 0.0) or 0.0),
            "P&L %": float(p.get("unrealized_pl_pct", 0.0) or 0.0),
            "Dividends": float(p.get("dividends_received", 0.0) or 0.0),
        }
        for sym, p in positions.items()
    ]).sort_values("Market Value", ascending=False)

    fmt = {
        "Qty": "{:.2f}",
        "Avg Cost": "${:.2f}",
        "Price": "${:.2f}",
        "Market Value": "${:,.0f}",
        "Unrealized P&L": "${:,.0f}",
        "P&L %": "{:.1%}",
        "Dividends": "${:,.2f}",
    }
    fmt = {k: v for k, v in fmt.items() if k in holdings_df.columns}
    styler = style_severity(holdings_df, pnl_cols=("Unrealized P&L", "P&L %")).format(fmt)
    st.dataframe(styler, width="stretch")


# ---------------------------------------------------------------------------
# Observability — Section 4a-2: Open Positions (internal book) vs. Pipeline
# Signals
#
# Ported from ``observability/dashboard.py``'s "📂 Open Positions" row —
# side-by-side comparison so the operator can spot reconciliation gaps
# between the internal TransactionsStore book and the last pipeline snapshot.
# ---------------------------------------------------------------------------


def _render_observability_open_positions_vs_signals(snap: Dict[str, Any]) -> None:
    st.markdown("### 📂 Open Positions")

    try:
        from transactions_store import TransactionsStore

        open_df = TransactionsStore(readonly=True).open_trades_df()
    except Exception as exc:  # noqa: BLE001
        st.caption(f"(transactions store unavailable: {exc})")
        open_df = pd.DataFrame()

    col_open, col_signals = st.columns(2)

    with col_open:
        st.caption("From transactions_store (internal book)")
        if not open_df.empty:
            display_cols = [
                c for c in ["symbol", "strategy_id", "entry_price", "entry_ts", "qty"]
                if c in open_df.columns
            ]
            st.dataframe(open_df[display_cols] if display_cols else open_df, width="stretch")
        else:
            st.info("No open positions in the internal book.")

    with col_signals:
        st.caption("Latest pipeline signals")
        signals = snap.get("signals", [])
        if signals:
            sigs_df = pd.DataFrame(signals)
            if "kelly_target" in sigs_df.columns:
                sigs_df["kelly_target"] = (
                    sigs_df["kelly_target"] * 100
                ).round(1).astype(str) + "%"
            st.dataframe(sigs_df, width="stretch")
        else:
            st.info("No signals in last snapshot.")


# ---------------------------------------------------------------------------
# Observability — Section 4a-3: Portfolio Risk Metrics (heat / gross / net)
#
# Ported from ``observability/dashboard.py``'s "🌡️ Portfolio Risk Metrics"
# row. Heat/gross/net all derive from the internal TransactionsStore open
# book. The heat *denominator* is now the real total account equity from the
# cached Robinhood snapshot (``cache/account_snapshot.json`` via
# :func:`_load_account_snapshot_cache`), replacing the former hard-coded
# 100_000 placeholder. When equity is unavailable (no snapshot / non-positive),
# :func:`gui.observability_panel_helpers.compute_portfolio_heat` returns NaN and
# the tile degrades to "—" — never a fabricated denominator (CONSTRAINT #4).
# Gross/net likewise degrade to "—" when the required columns are absent.
# ---------------------------------------------------------------------------


def _render_observability_portfolio_risk_metrics() -> None:
    st.markdown("### 🌡️ Portfolio Risk Metrics")

    try:
        from transactions_store import TransactionsStore

        open_df = TransactionsStore(readonly=True).open_trades_df()
    except Exception:
        open_df = pd.DataFrame()

    col_heat, col_gross, col_net = st.columns(3)

    with col_heat:
        if not open_df.empty and "unrealized_pnl" in open_df.columns:
            adverse = open_df[open_df["unrealized_pnl"] < 0]["unrealized_pnl"].abs().sum()
            # Real total account equity as the heat denominator (not a hard-coded
            # placeholder). Absent/non-positive equity → NaN → "—" tile.
            total_equity = _load_account_snapshot_cache().get("total_equity")
            heat_pct = compute_portfolio_heat(float(adverse), total_equity)
            if heat_pct == heat_pct:  # not NaN
                heat_colour = portfolio_heat_badge(heat_pct)
                st.metric("Portfolio Heat", f"{heat_colour} {heat_pct:.1%}")
            else:
                st.metric(
                    "Portfolio Heat", "—",
                    help="Total account equity unavailable "
                         "(cache/account_snapshot.json) — cannot compute the heat "
                         "denominator honestly. Run `python3 main.py "
                         "--refresh-account` to populate.",
                )
        else:
            st.metric("Portfolio Heat", "—")

    with col_gross:
        if not open_df.empty and "market_value" in open_df.columns:
            gross = open_df["market_value"].abs().sum()
            st.metric("Gross Exposure", f"${gross:,.0f}")
        else:
            st.metric("Gross Exposure", "—")

    with col_net:
        if not open_df.empty and "market_value" in open_df.columns:
            net = open_df["market_value"].sum()
            st.metric("Net Exposure", f"${net:,.0f}")
        else:
            st.metric("Net Exposure", "—")


# ---------------------------------------------------------------------------
# Observability — Section 4a-4: Validation Report Status
#
# Ported from ``observability/dashboard.py``'s "🏷️ Validation Report Status"
# row. NOTE: a richer, cross-strategy version of this table — plus
# run-over-run PBO/DSR/Sharpe/MaxDD trend lines — already lives in the
# Gravity Audit tab's ``_render_validation_stress_regime_section()``. Rather
# than duplicate that logic, this section renders the same compact summary
# the standalone dashboard used to show (so the Observability tab alone is
# still a full superset) and points to the richer view for trend analysis.
# ---------------------------------------------------------------------------


def _load_validation_reports() -> List[Dict[str, Any]]:
    """Load all ``reports/*_validation_summary.json`` files.

    Same glob :func:`gui.panels.gravity_audit._render_validation_stress_regime_section`
    already uses — duplicated here as a small local read (not re-exported)
    rather than importing across panel modules, since ``gui/panels/_shared.py``
    is the sanctioned place for cross-panel-module reuse and this one-line
    glob does not warrant promoting there.
    """
    reports_dir = _REPO_ROOT / "reports"
    if not reports_dir.exists():
        return []
    summaries: List[Dict[str, Any]] = []
    for f in reports_dir.glob("*_validation_summary.json"):
        try:
            summaries.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not parse %s: %s", f, exc)
    return summaries


def _render_observability_validation_status() -> None:
    st.markdown("### 🏷️ Validation Report Status")
    st.caption(
        "For run-over-run PBO/DSR/Sharpe/MaxDD trend lines and the "
        "tail-scenario stress gate breakdown, see **Gravity Audit Logs → "
        "📐 Validation & Stress Trend**."
    )

    val_reports = _load_validation_reports()
    if not val_reports:
        st.warning(
            "No validation summaries found in reports/.  "
            "Run: `python -m validation.harness --strategy <name> "
            "--start YYYY-MM-DD --end YYYY-MM-DD`"
        )
        return

    vr_df = pd.DataFrame(val_reports)
    status_col = vr_df["deployable"].map({True: "✅ DEPLOYABLE", False: "❌ REJECTED"})
    vr_df.insert(1, "Status", status_col)
    show_cols = [
        c for c in
        ["strategy_id", "Status", "pbo", "dsr", "sharpe", "max_drawdown", "report_date"]
        if c in vr_df.columns
    ]
    st.dataframe(vr_df[show_cols] if show_cols else vr_df, width="stretch")


# ---------------------------------------------------------------------------
# Observability — Section 4a-5: Recent Closed Trades
#
# Ported from ``observability/dashboard.py``'s "🗒️ Recent Closed Trades" row.
# ---------------------------------------------------------------------------


def _render_observability_recent_closed_trades() -> None:
    st.markdown("### 🗒️ Recent Closed Trades")

    try:
        from transactions_store import TransactionsStore

        closed_df = TransactionsStore(readonly=True).closed_trades_df()
    except Exception as exc:  # noqa: BLE001
        st.caption(f"(transactions store unavailable: {exc})")
        return

    if closed_df.empty:
        st.info("No closed trades yet.")
        return

    display = closed_df.tail(20).copy()
    if "exit_ts" in display.columns:
        display = display.sort_values("exit_ts", ascending=False)
    show_cols = [
        c for c in
        ["symbol", "strategy_id", "entry_price", "exit_price", "realized_pnl", "exit_ts"]
        if c in display.columns
    ]
    st.dataframe(display[show_cols] if show_cols else display, width="stretch")


# ---------------------------------------------------------------------------
# Observability — Section 4a-6: Equity Curve, Drawdown & Regime Overlay
#
# Ported from ``observability/dashboard.py``'s "📈 Equity Curve, Drawdown &
# Regime Overlay" row (added there after the initial dashboard-vs-GUI split,
# so it never had a GUI-side counterpart). Computed from
# ``TransactionsStore.closed_trades_df()``.  ``realized_pnl`` is NOT a stored
# column on the ``Trade`` model — it is derived here exactly as the original
# dashboard did: ``(exit_price - entry_price) * shares`` for longs, inverted
# for shorts, matching ``TransactionsStore.record_trade()``/``close_trade()``'s
# sign convention.
#
# Regime overlay: sourced from ``output/history/state_snapshot_*.json`` via
# ``scripts.snapshot_diff`` — the same rotated-snapshot source the Gravity
# Audit tab's Macro Regime Timeline uses. If fewer than 2 rotated snapshots
# exist, no fabricated regime history is shown (CONSTRAINT #4).
# ---------------------------------------------------------------------------


def _render_observability_equity_curve() -> None:
    from gui.styling import freshness_badge

    st.markdown("### 📈 Equity Curve, Drawdown & Regime Overlay")

    try:
        from transactions_store import TransactionsStore

        closed_df = TransactionsStore(readonly=True).closed_trades_df()
    except Exception as exc:  # noqa: BLE001
        st.caption(f"(transactions store unavailable: {exc})")
        return

    # Freshness badge: "as of" the most recent closed-trade exit.
    try:
        latest_exit = None
        if not closed_df.empty and "exit_ts" in closed_df.columns:
            exit_series = pd.to_datetime(closed_df["exit_ts"], errors="coerce").dropna()
            if not exit_series.empty:
                latest_exit = exit_series.max().to_pydatetime().replace(tzinfo=timezone.utc)
        st.caption(freshness_badge(
            latest_exit, ttl_seconds=settings.DASHBOARD_REFRESH_SECONDS,
            label="Most recent closed trade",
        ))
    except Exception as exc:  # noqa: BLE001
        st.caption(f"(freshness badge unavailable: {exc})")

    if closed_df.empty or "exit_ts" not in closed_df.columns:
        st.info(
            "No closed trades yet — the equity curve populates once "
            "`TransactionsStore` has at least one closed round-trip trade "
            "(entry + exit recorded via `record_trade()` / `close_trade()`)."
        )
        return

    eq_df = closed_df.dropna(subset=["exit_ts", "entry_price", "exit_price", "shares"]).copy()
    if eq_df.empty:
        st.info("Closed trades exist but are missing price/quantity fields needed for P&L.")
        return

    eq_df["exit_ts"] = pd.to_datetime(eq_df["exit_ts"])
    eq_df["side"] = eq_df.get("side", "long").fillna("long").str.lower()
    sign = eq_df["side"].map(lambda s: -1.0 if s == "short" else 1.0)
    eq_df["realized_pnl"] = (eq_df["exit_price"] - eq_df["entry_price"]) * eq_df["shares"] * sign
    eq_df = eq_df.sort_values("exit_ts")
    eq_df["cumulative_pnl"] = eq_df["realized_pnl"].cumsum()
    eq_df["running_peak"] = eq_df["cumulative_pnl"].cummax()
    # Drawdown as % of peak equity above zero; peak floor of $1 avoids a
    # divide-by-zero / meaningless percentage while cumulative P&L is still
    # <= 0 (i.e. before the strategy has ever been net profitable).
    peak_floor = eq_df["running_peak"].clip(lower=1.0)
    eq_df["drawdown_pct"] = (eq_df["cumulative_pnl"] - eq_df["running_peak"]) / peak_floor

    ec1, ec2, ec3 = st.columns(3)
    ec1.metric("Closed Trades", len(eq_df))
    ec2.metric("Cumulative Realized P&L", f"${eq_df['cumulative_pnl'].iloc[-1]:,.2f}")
    ec3.metric("Max Drawdown", f"{eq_df['drawdown_pct'].min():.1%}")

    equity_chart_df = eq_df.set_index("exit_ts")[["cumulative_pnl"]].rename(
        columns={"cumulative_pnl": "Cumulative P&L ($)"}
    )
    st.line_chart(equity_chart_df, width="stretch")

    drawdown_chart_df = eq_df.set_index("exit_ts")[["drawdown_pct"]].rename(
        columns={"drawdown_pct": "Drawdown (%)"}
    )
    st.area_chart(drawdown_chart_df, width="stretch")

    # ── Regime overlay (best-effort, real history only) ──────────────────────
    try:
        from scripts.snapshot_diff import list_rotated_snapshots, load_snapshot

        rotated_paths = list_rotated_snapshots(settings.OUTPUT_DIR)
        regime_points = []
        for p in rotated_paths:
            snap_hist = load_snapshot(p)
            if not snap_hist:
                continue
            ts_raw = snap_hist.get("timestamp")
            regime_raw = snap_hist.get("market_regime")
            if ts_raw and regime_raw:
                regime_points.append({
                    "timestamp": pd.to_datetime(ts_raw),
                    "market_regime": str(regime_raw),
                })
        if len(regime_points) >= 2:
            regime_hist_df = pd.DataFrame(regime_points).sort_values("timestamp")
            st.markdown("**Macro Regime Over Time** (from `output/history/`)")
            st.dataframe(
                regime_hist_df.rename(
                    columns={"timestamp": "Timestamp (UTC)", "market_regime": "Market Regime"}
                ),
                width="stretch",
            )
            st.caption(
                f"{len(regime_points)} rotated snapshot(s) found "
                f"(retained {settings.SNAPSHOT_HISTORY_DAYS} days). "
                "Shown as a table rather than a shaded chart overlay because "
                "regime changes are sparse/irregular events, not a dense "
                "time series aligned to trade exits. See also Gravity Audit "
                "Logs → Macro Regime Timeline."
            )
        else:
            st.caption(
                "⚠️ Regime overlay not available: only "
                f"{len(regime_points)} rotated snapshot(s) exist in "
                "`output/history/` so far (need ≥ 2 to show a regime-over-time "
                "table). This is a genuine data limitation, not a bug — "
                "regime history accumulates one entry per orchestrator/"
                "advisory run via `scripts.snapshot_diff.rotate_snapshot()`. "
                "Run the pipeline more than once to populate this."
            )
    except Exception as exc:  # noqa: BLE001
        st.caption(f"(regime history unavailable: {exc})")

    with st.expander("🔬 Underlying closed-trade P&L table"):
        st.dataframe(
            eq_df[["symbol", "strategy", "side", "entry_ts", "entry_price",
                   "exit_ts", "exit_price", "shares", "realized_pnl",
                   "cumulative_pnl", "drawdown_pct"]],
            width="stretch",
        )


# ---------------------------------------------------------------------------
# Observability — Section 4a-6b: Forecast Skill (per-model accuracy)
#
# Surfaces ``forecasting.forecast_tracker.ForecastTracker`` — the rolling-window
# inverse-RMSE tracker that (when ``FORECAST_SKILL_WEIGHTING_ENABLED``) weights
# ARIMA / Monte Carlo / Holt-Winters / CNN-LSTM by recent realized accuracy.
# Nothing else in the GUI exposes it, so an operator who flips the flag on is
# otherwise tuning blind. Read-only: all DB access is wrapped in try/except and
# a missing table / empty DB renders an info message, never a traceback
# (CONSTRAINT #6). RMSE / weights degrade to "—" when no data (CONSTRAINT #4 —
# never a fabricated 0.0).
# ---------------------------------------------------------------------------


def _forecast_rmse_by_model(
    db_path: str, symbol: str, horizon_days: int, window_days: int
) -> Dict[str, float]:
    """Per-model RMSE over completed ``forecast_errors`` rows in the window.

    Opens a short read-only ``sqlite3`` connection (mirroring how
    ``forecast_tracker.py`` opens connections) and SELECTs the mean
    ``squared_error`` per model for actualized rows within ``window_days``,
    returning ``sqrt(mean_sq_err)``. Read-only SELECT only. Returns ``{}`` on
    any failure (missing table / DB error) so the caller degrades gracefully —
    ``forecast_tracker`` intentionally exposes no public per-symbol-RMSE method,
    hence this local helper rather than an API addition.
    """
    import math
    import sqlite3
    from datetime import datetime, timedelta, timezone

    from db_config import sqlite_readonly_uri

    out: Dict[str, float] = {}
    try:
        since_iso = (
            datetime.now(timezone.utc) - timedelta(days=window_days)
        ).isoformat()
        # DATABASE-LEVEL read-only (mode=ro): this helper only SELECTs, and a
        # read-only connection also avoids create'ing a stray empty DB file when
        # db_path is missing (which raises here → caught → empty render). Scope
        # note: _forecast_skill_rows below ALSO builds a ForecastTracker, whose
        # own connection stays read-write (it has real writers elsewhere); this
        # change hardens the two direct SELECT connections, not the whole panel.
        conn = sqlite3.connect(sqlite_readonly_uri(db_path), uri=True)
        try:
            cursor = conn.execute(
                """SELECT model_name, AVG(squared_error) AS mse
                   FROM forecast_errors
                   WHERE symbol        = ?
                     AND horizon_days  = ?
                     AND actual_price  IS NOT NULL
                     AND forecast_ts   >= ?
                   GROUP BY model_name""",
                (symbol.upper(), horizon_days, since_iso),
            )
            for model_name, mse in cursor.fetchall():
                if mse is not None and mse >= 0:
                    out[model_name] = math.sqrt(mse)
                else:
                    out[model_name] = float("nan")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — dead-letter: never raise into UI
        logger.debug(
            "forecast RMSE query failed for %s h=%d: %s", symbol, horizon_days, exc
        )
    return out


_FORECAST_SKILL_HORIZONS = (10, 30, 60, 90)


@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _forecast_skill_rows(
    db_path: str,
    db_mtime: float,
    symbols: Tuple[str, ...],
    window_days: int,
    min_obs: int,
) -> Dict[str, Any]:
    """Batched + cached loader behind the Forecast-Skill table.

    Replaces the previous ``symbols × 4 horizons`` double loop that opened a
    FRESH ``sqlite3`` connection per cell (≈120 connections/rerun) with:

    * ONE aggregated ``forecast_errors`` SELECT (over ONE connection) yielding
      the per-``(symbol, horizon, model)`` RMSE map — same NaN/None handling as
      :func:`_forecast_rmse_by_model` (``mse`` None or ``< 0`` → NaN);
    * ONE :class:`ForecastTracker` (its methods reuse a single connection after
      PR A) for ``pending_count`` / ``completed_count`` / ``get_skill_weights``.

    ``db_mtime`` participates in the cache key ONLY — a fresh pipeline cycle
    (which writes ``forecast_errors``) changes the DB mtime and busts the cache,
    mirroring ``gui.panels.load_state_snapshot``'s mtime-keying. ``symbols`` is a
    tuple so the arguments are hashable.

    Returns ``{"rows": [...], "any_history": bool}`` — the SAME ``rows`` list of
    dicts (identical keys) the inline loop used to build. Dead-letter resilient
    (CONSTRAINT #6): any catastrophic failure degrades to
    ``{"rows": [], "any_history": False}``; RMSE / weights degrade to ``—`` /
    equal weights, never a fabricated ``0.0`` (CONSTRAINT #4).
    """
    import math
    import sqlite3
    from datetime import datetime, timedelta, timezone

    from db_config import sqlite_readonly_uri

    try:
        from forecasting.forecast_tracker import ALL_MODEL_NAMES, ForecastTracker
    except Exception as exc:  # noqa: BLE001
        logger.debug("ForecastTracker import failed in loader: %s", exc)
        return {"rows": [], "any_history": False}

    try:
        since_iso = (
            datetime.now(timezone.utc) - timedelta(days=window_days)
        ).isoformat()

        # ── 1. ONE aggregated RMSE query over ONE connection ─────────────────
        # rmse_by_cell[(symbol_upper, horizon)] -> {model_name: rmse|nan}
        # DATABASE-LEVEL read-only (mode=ro) — SELECT-only, and no stray DB file
        # is created if db_path is missing. The ForecastTracker built below keeps
        # its own read-write connection (it has real writers elsewhere).
        rmse_by_cell: Dict[Tuple[str, int], Dict[str, float]] = {}
        upper_syms = [s.upper() for s in symbols]
        if upper_syms:
            placeholders = ",".join("?" for _ in upper_syms)
            try:
                conn = sqlite3.connect(sqlite_readonly_uri(db_path), uri=True)
                try:
                    cursor = conn.execute(
                        f"""SELECT symbol, horizon_days, model_name,
                                   AVG(squared_error) AS mse
                            FROM forecast_errors
                            WHERE actual_price IS NOT NULL
                              AND forecast_ts   >= ?
                              AND symbol IN ({placeholders})
                            GROUP BY symbol, horizon_days, model_name""",
                        (since_iso, *upper_syms),
                    )
                    for sym_u, horizon, model_name, mse in cursor.fetchall():
                        cell = rmse_by_cell.setdefault((sym_u, int(horizon)), {})
                        if mse is not None and mse >= 0:
                            cell[model_name] = math.sqrt(mse)
                        else:
                            cell[model_name] = float("nan")
                finally:
                    conn.close()
            except Exception as exc:  # noqa: BLE001 — RMSE step degrades to empty
                logger.debug("batched forecast RMSE query failed: %s", exc)
                rmse_by_cell = {}

        # ── 2. ONE ForecastTracker for pending/completed/weights ─────────────
        # Safe to open read-only: this loader is only ever reached (via
        # _render_observability_forecast_skill, below) AFTER that function's own
        # write-mode ForecastTracker() has already self-provisioned the table.
        tracker = ForecastTracker(db_path=db_path, readonly=True)

        rows: List[Dict[str, Any]] = []
        any_history = False
        for sym in symbols:
            for h in _FORECAST_SKILL_HORIZONS:
                try:
                    pending = tracker.pending_count(sym, h)
                    completed = tracker.completed_count(sym, h, window_days=window_days)
                    weights = tracker.get_skill_weights(
                        sym, h, window_days=window_days, min_obs=min_obs
                    )
                except Exception as exc:  # noqa: BLE001 — never break the tab
                    logger.debug("forecast skill row failed for %s h=%d: %s", sym, h, exc)
                    pending, completed, weights = 0, 0, {}

                rmse_map = rmse_by_cell.get((sym.upper(), h), {})

                if pending or completed:
                    any_history = True

                models = sorted(set(rmse_map) | set(weights)) or list(ALL_MODEL_NAMES)
                for model in models:
                    r = rmse_map.get(model)
                    w = weights.get(model)
                    rows.append({
                        "Symbol": sym,
                        "Horizon (d)": h,
                        "Model": model,
                        "Pending": pending,
                        "Completed": completed,
                        "RMSE ($)": format_rmse(r),
                        "Skill weight": format_skill_weight(w),
                    })

        return {"rows": rows, "any_history": any_history}
    except Exception as exc:  # noqa: BLE001 — dead-letter: never raise into UI
        logger.debug("forecast skill loader failed: %s", exc)
        return {"rows": [], "any_history": False}


def _render_observability_forecast_skill(snap: Dict[str, Any]) -> None:
    """Per-model forecast-accuracy view over the live ``forecast_errors`` table.

    Shows, per symbol × horizon [10, 30, 60, 90]: ``pending_count`` /
    ``completed_count`` and, per model, the rolling-window RMSE plus the live
    inverse-RMSE ``get_skill_weights`` blend weight. The current flag state and
    window / min-obs are surfaced so the operator knows whether weighting is
    even engaging.
    """
    st.markdown("### 🎯 Forecast Skill (per-model accuracy)")
    st.caption(
        "Rolling-window RMSE per forecasting model (ARIMA / Monte Carlo / "
        "Holt-Winters / CNN-LSTM) and the live inverse-RMSE blend weights from "
        "`forecasting.forecast_tracker.ForecastTracker`."
    )

    enabled = bool(settings.FORECAST_SKILL_WEIGHTING_ENABLED)
    window_days = int(settings.FORECAST_SKILL_WINDOW_DAYS)
    min_obs = int(settings.FORECAST_SKILL_MIN_OBS)

    if enabled:
        st.success(
            f"🟢 `FORECAST_SKILL_WEIGHTING_ENABLED = True` — inverse-RMSE "
            f"skill-weighted blend active."
        )
    else:
        st.info(
            "⚪ `FORECAST_SKILL_WEIGHTING_ENABLED = False` — static blend in use. "
            "Enable it (Settings tab / `.env`) to weight models by recent "
            "accuracy. The table below still populates so you can inspect skill "
            "before flipping the flag."
        )
    st.caption(
        f"Window: **{window_days}d**  ·  Min obs: **{min_obs}** per model.  "
        "Weighting only engages once `completed_count ≥ min_obs` for every model "
        "in the window — below that, equal (cold-start) weights are used."
    )

    # Construct the tracker (self-provisions its table) once, only to resolve
    # the DB path + guard the "unavailable" case. The heavy per-cell reads are
    # done inside the cached ``_forecast_skill_rows`` loader below.
    try:
        from forecasting.forecast_tracker import ForecastTracker

        tracker = ForecastTracker()
        db_path = tracker._db_path  # noqa: SLF001 — read-only path reuse
    except Exception as exc:  # noqa: BLE001
        logger.debug("ForecastTracker unavailable: %s", exc)
        st.info(
            "No forecast-accuracy history yet — enable "
            "`FORECAST_SKILL_WEIGHTING_ENABLED` and run cycles to accumulate."
        )
        return

    # Symbols: prefer the loaded state snapshot; else let the operator type one.
    symbols: List[str] = []
    for s in snap.get("signals", []) or []:
        sym = s.get("symbol")
        if sym and sym not in symbols:
            symbols.append(str(sym))
    symbols = symbols[:30]  # bound the number of DB round-trips

    if not symbols:
        typed = st.text_input(
            "No pipeline signals in the last snapshot — enter a symbol to inspect",
            value="", key="obs_forecast_skill_symbol", placeholder="e.g. AAPL",
        )
        if typed.strip():
            symbols = [typed.strip().upper()]
        else:
            st.info(
                "No forecast-accuracy history yet — enable "
                "`FORECAST_SKILL_WEIGHTING_ENABLED` and run cycles to accumulate."
            )
            return

    # DB mtime is the freshness key: a fresh pipeline cycle writes
    # forecast_errors → mtime changes → cache miss (mirrors load_state_snapshot).
    try:
        db_mtime = os.path.getmtime(db_path)
    except Exception:  # noqa: BLE001
        db_mtime = 0.0

    skill = _forecast_skill_rows(
        db_path, db_mtime, tuple(symbols), window_days, min_obs
    )
    rows: List[Dict[str, Any]] = skill.get("rows", [])
    any_history = bool(skill.get("any_history", False))

    if not any_history:
        st.info(
            "No forecast-accuracy history yet — enable "
            "`FORECAST_SKILL_WEIGHTING_ENABLED` and run cycles to accumulate. "
            "(Forecasts are recorded on each run and actualized once their "
            "horizon elapses.)"
        )
        return

    df = pd.DataFrame(rows)
    st.dataframe(df, width="stretch", hide_index=True)
    st.caption(
        "RMSE is computed over completed (actualized) rows in the window; "
        "`—` means no data / cold start. An empty **Skill weight** across the "
        "board means the tracker returned `{}` (no completed history) → equal "
        "weights are used. Weights sum to 1.0 within a symbol/horizon once warm."
    )

    # ── Forecast Reliability (calibration) sub-section ──────────────────────
    # Distinct from the RMSE table above (per-symbol/per-horizon accuracy) --
    # this bins realized percent error per (model, horizon) group across ALL
    # symbols to show systematic over-/under-prediction bias, using the same
    # ForecastTracker instance the RMSE table already reads from.
    st.markdown("#### 📐 Forecast Reliability (calibration)")
    st.caption(
        "Bins realized percent error `(actual - forecast) / actual` by model "
        "and horizon across all symbols. A model whose bins cluster near 0% "
        "is well-calibrated; a persistent positive/negative bias means it "
        "systematically under-/over-predicts."
    )
    try:
        from forecasting.forecast_tracker import ForecastTracker

        # Safe to open read-only: this section only renders after the RMSE
        # section above's write-mode ForecastTracker() has already run (any
        # earlier failure/early-return in the same function exits before here).
        tracker = ForecastTracker(readonly=True)
        horizons_available = sorted({int(h) for h in {10, 30, 60, 90}})
        rel_horizon = st.selectbox(
            "Horizon (days)", options=["All"] + horizons_available,
            key="obs_reliability_horizon",
        )
        rel_model = st.text_input(
            "Model filter (optional, exact match)", value="",
            key="obs_reliability_model",
        )

        curve = tracker.get_forecast_reliability_curve(
            horizon_days=None if rel_horizon == "All" else int(rel_horizon),
        )
        if rel_model.strip():
            curve = curve[curve["model_name"] == rel_model.strip()]

        if curve is None or curve.empty:
            st.info(
                "No completed forecast history yet for the selected filter — "
                "the reliability curve populates as forecasts are recorded "
                "and actualized over time."
            )
        else:
            chart_df = curve.set_index("bin_center")[["mean_pct_error"]].dropna()
            if not chart_df.empty:
                st.bar_chart(chart_df)
            st.dataframe(
                curve[["model_name", "horizon_days", "bin_center", "mean_pct_error", "count"]],
                width="stretch", hide_index=True,
            )
    except Exception as exc:  # noqa: BLE001 — dead-letter: never raise into UI
        logger.debug("forecast reliability curve failed: %s", exc)
        st.caption(f"(forecast reliability unavailable: {exc})")


# ---------------------------------------------------------------------------
# Observability — Section 4a-7: Risk Gate Block Log
#
# Ported from ``observability/dashboard.py``'s "🚧 Risk Gate Block Log" row.
# Reuses the already-shared ``gui.panels._shared.load_block_log`` loader
# (identical to the one the standalone dashboard defined locally) rather than
# duplicating the JSONL-tail-read logic.
# ---------------------------------------------------------------------------


def _render_observability_risk_gate_block_log() -> None:
    st.markdown("### 🚧 Risk Gate Block Log (last 100)")
    block_log = load_block_log()
    if block_log:
        st.dataframe(pd.DataFrame(block_log), width="stretch")
    else:
        st.success("No blocked orders in the log.")


# ---------------------------------------------------------------------------
# Observability — Section: Sizing Cap-Event Audit Trail (sizing/position_sizer.py,
# sizing/cap_audit_store.py)
# ---------------------------------------------------------------------------


def _render_observability_sizing_cap_audit() -> None:
    """Durable history of position-sizing guardrail events (last 100).

    Distinct from the per-cycle ``Sizing_Was_Capped``/``Sizing_Binding_Constraint``
    columns already surfaced elsewhere (dashboard_df, state_snapshot, Sheet) --
    this section reads the DURABLE ``sizing_cap_events`` table
    (``sizing/cap_audit_store.py``), so an operator can see "which names have
    been hitting a ceiling, and how often" across cycles, not just the latest
    one. Read-only (``CapAuditStore(readonly=True)``) -- this panel never
    writes. Degrades to an info message on any DB error (dead-letter pattern
    used throughout this codebase), never a traceback.
    """
    st.markdown("### 🧢 Sizing Cap-Event Audit Trail (last 100)")
    st.caption(
        help_widgets.metric_help("observability.sizing_cap_audit")
        or "Durable log of every position-sizing capping event (KELLY_CAP, "
        "MAX_POSITION_WEIGHT, the portfolio-wide gross-exposure cap, or "
        "cap-aware escalation) -- not just this cycle's snapshot."
    )

    if not settings.SIZING_CAP_AUDIT_ENABLED:
        st.info(
            "⚪ `SIZING_CAP_AUDIT_ENABLED = False` — the durable cap-event log "
            "is not being written this run. Enable it (Settings tab / `.env`) "
            "to start accumulating history."
        )
        return

    try:
        from sizing.cap_audit_store import CapAuditStore

        events = CapAuditStore(readonly=True).get_recent(limit=100)
    except Exception as exc:  # noqa: BLE001
        logger.debug("CapAuditStore read failed: %s", exc)
        events = []

    if not events:
        st.info("No cap events recorded yet — they accumulate as cycles run.")
        return

    df = pd.DataFrame(events)
    capped_only = df[df["was_capped"]] if "was_capped" in df.columns else df
    if capped_only.empty:
        st.success("No names have hit a sizing ceiling in the recorded history.")
        return

    st.dataframe(
        capped_only[["timestamp", "symbol", "strategy_id", "final_weight", "binding_constraint", "cycle_id"]],
        width="stretch", hide_index=True,
    )

    if settings.SIZING_CAP_ESCALATION_ENABLED:
        st.caption(
            f"🟢 Cap-aware escalation active: a name capped for "
            f"**{settings.SIZING_CAP_ESCALATION_THRESHOLD_CYCLES}** consecutive "
            f"cycles is down-weighted by **{settings.SIZING_CAP_ESCALATION_FACTOR:.2f}x**."
        )
    else:
        st.caption("⚪ Cap-aware escalation disabled (`SIZING_CAP_ESCALATION_ENABLED = False`).")


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

        status = heartbeat_status(latest_age)

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


