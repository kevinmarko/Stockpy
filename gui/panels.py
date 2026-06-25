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

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from settings import settings
from gui import env_io, orchestrator_runner

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent


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


# ===========================================================================
# Tab 1 — Launcher & Orchestration
# ===========================================================================

def render_launcher() -> None:
    """Launch ``main_orchestrator.py`` and show live per-stage status + log tail."""
    st.subheader("🚀 Program Launcher & Orchestration")
    st.caption(
        "Triggers the async `main_orchestrator.py` pipeline as a subprocess "
        "(non-blocking). Stage indicators are derived from the run log, "
        "heartbeat, and state snapshot."
    )

    col_a, col_b, col_c = st.columns([1, 1, 2])
    with col_a:
        dry_run = st.checkbox(
            "Dry run", value=settings.DRY_RUN,
            help="Log intended orders but never submit them to the broker.",
        )
    with col_b:
        refresh_account = st.checkbox(
            "Refresh Robinhood account", value=False,
            help="Force a fresh account snapshot on this launch.",
        )
    with col_c:
        launch = st.button("▶️  Launch Pipeline", type="primary", width="stretch")

    handle: Optional[orchestrator_runner.RunHandle] = st.session_state.get("run_handle")

    if launch:
        if handle is not None and handle.is_running():
            st.warning("A pipeline run is already in progress — wait for it to finish.")
        else:
            handle = orchestrator_runner.launch_orchestrator(
                dry_run=dry_run, refresh_account=refresh_account
            )
            st.session_state["run_handle"] = handle
            st.success(f"Launched orchestrator (PID {handle.pid}).")

    # Status row.
    running = handle is not None and handle.is_running()
    hb_age = orchestrator_runner.heartbeat_age_seconds()
    cols = st.columns(2)
    with cols[0]:
        if handle is None:
            st.info("No run launched this session.")
        elif running:
            st.success(f"🟢 Running (PID {handle.pid})")
        else:
            rc = handle.returncode()
            st.info(f"⏹️ Finished (exit code {rc})" if rc is not None else "⏹️ Finished")
    with cols[1]:
        if hb_age is None:
            st.metric("Heartbeat", "—")
        else:
            fresh = "🟢" if hb_age < 90 else "🔴"
            st.metric("Heartbeat age", f"{fresh} {hb_age:.0f}s")

    # Stage indicators.
    st.markdown("**Pipeline stages**")
    stage_status = orchestrator_runner.compute_stage_status(handle)
    icon = {"done": "✅", "active": "🟡", "pending": "⚪", "idle": "⚪"}
    stage_cols = st.columns(len(stage_status))
    for col, (label, status) in zip(stage_cols, stage_status.items()):
        with col:
            st.metric(label, f"{icon.get(status, '⚪')} {status}")

    # Log tail.
    with st.expander("📜 Run log (tail)", expanded=running):
        st.code(orchestrator_runner.read_log_tail(max_lines=200), language="text")


# ===========================================================================
# Tab 2 — Interactive Report Viewer
# ===========================================================================

def render_report_viewer() -> None:
    """Surface evaluation_engine / research_engine analytics + report exports."""
    st.subheader("📈 Interactive Report Viewer")

    snap = load_state_snapshot()
    signals = snap.get("signals", [])

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
    else:
        st.caption("MFE/MAE/Edge populate once closed trades and signals exist.")

    # ── Brinson-Fachler attribution (informational; needs weights) ───────────
    with st.expander("📊 Brinson-Fachler attribution (requires weights)"):
        st.caption(
            "Provide portfolio & benchmark sector weights to compute allocation / "
            "selection effects via `EvaluationEngine.calculate_brinson_fachler`."
        )

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

def render_strategy_matrix() -> None:
    """Toggle signal modules, edit weights, and control the macro kill switch."""
    st.subheader("🧩 Strategy Matrix & Risk Gating")

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

def render_gravity_audit() -> None:
    """Run the Gravity AI Review Suite as a subprocess and render its JSON report."""
    st.subheader("🛡️ Gravity AI Audit Logs")
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
    """Black-Scholes Greeks + IVR proxy for active symbols (options-selling aid)."""
    st.subheader("🧮 Technical Options Matrix")
    st.caption(
        "ATM Black-Scholes Greeks and an IV-Rank proxy for active symbols, "
        "to support premium-selling decisions."
    )

    snap = load_state_snapshot()
    symbols = _signal_symbols(snap)
    default_syms = ", ".join(symbols[:10])
    sym_text = st.text_input("Symbols (comma-separated)", value=default_syms)
    target_dte = st.slider("Target DTE (days)", min_value=1, max_value=90, value=30)
    symbols = [s.strip().upper() for s in sym_text.split(",") if s.strip()]

    if not symbols:
        st.info("Enter at least one symbol.")
        return

    if st.button("Compute Greeks matrix", type="primary"):
        from technical_options_engine import OptionsPricingRecommender, TechnicalOptionsEngine
        from data.market_data import get_provider

        provider = get_provider()
        toe = TechnicalOptionsEngine()
        rows: List[Dict[str, Any]] = []
        T = max(target_dte, 1) / 365.0
        for sym in symbols:
            try:
                quote = provider.get_latest_quote(sym)
                price = float(quote.price)
                bars = provider.get_intraday_bars(sym, lookback_days=252)
                try:
                    sigma = float(toe.estimate_gjr_garch_volatility(bars))
                except Exception:
                    sigma = 0.25
                try:
                    ivr = float(toe.calculate_realized_vol_rank(bars, sigma))
                except Exception:
                    ivr = float("nan")
                opr = OptionsPricingRecommender(stock_price=price, risk_free_rate=settings.RISK_FREE_RATE)
                greeks = opr.black_scholes_pricing_and_greeks(K=price, T=T, sigma=sigma, option_type="call")
                rows.append({
                    "Symbol": sym,
                    "Price": round(price, 2),
                    "Stale": quote.is_stale,
                    "IV (σ)": round(sigma, 3),
                    "IVR%": round(ivr, 1) if ivr == ivr else None,
                    "ATM Price": round(float(greeks.get("Price", float("nan"))), 2),
                    "Delta": round(float(greeks.get("Delta", float("nan"))), 3),
                    "Gamma": round(float(greeks.get("Gamma", float("nan"))), 4),
                    "Vega": round(float(greeks.get("Vega", float("nan"))), 3),
                    "Theta/day": round(float(greeks.get("Theta_Daily", float("nan"))), 4),
                })
            except Exception as exc:
                logger.warning("options matrix failed for %s: %s", sym, exc)
                rows.append({"Symbol": sym, "Price": None, "Stale": None,
                             "IV (σ)": None, "IVR%": None, "ATM Price": None,
                             "Delta": None, "Gamma": None, "Vega": None,
                             "Theta/day": None})
        st.dataframe(pd.DataFrame(rows), width="stretch")
        st.caption("σ from GJR-GARCH; IVR% is a realized-vol percentile proxy. "
                   "Stale=True means a delayed quote (yfinance).")


# ===========================================================================
# Tab 8 — Market Data
# ===========================================================================

def render_market_data() -> None:
    """Show the active market-data provider, quote freshness, and cache controls."""
    st.subheader("🛰️ Market Data Provider")

    from data.market_data import get_provider, reset_provider

    provider = get_provider()
    src = getattr(provider, "quote_source", "unknown")
    realtime = getattr(provider, "is_realtime", False)
    c1, c2, c3 = st.columns(3)
    c1.metric("Provider", str(src))
    c2.metric("Mode", "🟢 real-time" if realtime else "🟡 delayed")
    c3.metric("Quote TTL", f"{settings.MARKET_DATA_QUOTE_TTL_SECONDS}s")

    if st.button("♻️ Reset provider singleton"):
        try:
            reset_provider()
            st.success("Provider singleton reset — re-selected on next quote.")
        except Exception as exc:
            st.error(f"Reset failed: {exc}")

    snap = load_state_snapshot()
    symbols = _signal_symbols(snap)
    sym_text = st.text_input("Quote symbols", value=", ".join(symbols[:10]), key="md_syms")
    symbols = [s.strip().upper() for s in sym_text.split(",") if s.strip()]

    if st.button("Fetch quotes"):
        rows = []
        for sym in symbols:
            try:
                q = provider.get_latest_quote(sym)
                rows.append({
                    "Symbol": q.symbol, "Price": round(float(q.price), 2),
                    "Bid": q.bid, "Ask": q.ask,
                    "Stale": q.is_stale, "Source": q.source,
                    "Timestamp (UTC)": q.timestamp.isoformat() if q.timestamp else "—",
                })
            except Exception as exc:
                rows.append({"Symbol": sym, "Price": None, "Bid": None, "Ask": None,
                             "Stale": None, "Source": f"error: {exc}", "Timestamp (UTC)": "—"})
        st.dataframe(pd.DataFrame(rows), width="stretch")


# ===========================================================================
# Tab 9 — Observability (folded-in summary of the existing dashboard)
# ===========================================================================

def render_observability() -> None:
    """Compact macro/regime/P&L view mirroring observability/dashboard.py."""
    st.subheader("📊 Observability")
    st.caption("Summary of the file-backed state. The full standalone dashboard "
               "is still available via `streamlit run observability/dashboard.py`.")

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
        colour = ("🟢" if "RISK ON" in str(regime)
                  else ("🔴" if "RECESSION" in str(regime) else "🟡"))
        st.metric("Macro Regime", f"{colour} {regime}")
    with c_vix:
        vix = snap.get("vix")
        st.metric("VIX", f"{vix:.1f}" if isinstance(vix, (int, float)) else "—")
    with c_hmm:
        hmm_vals = [s.get("hmm_risk_on") for s in snap.get("signals", [])
                    if s.get("hmm_risk_on") is not None]
        st.metric("HMM Risk-On", f"{hmm_vals[0]:.1%}" if hmm_vals else "—")

    last = snap.get("timestamp", "—")
    st.caption(f"Pipeline last run: **{last}**")

    # Trades from the transactions store.
    try:
        from transactions_store import TransactionsStore

        ts = TransactionsStore()
        closed = ts.closed_trades_df()
        if not closed.empty and {"realized_pnl", "strategy_id"} <= set(closed.columns):
            pnl = (closed.groupby("strategy_id")["realized_pnl"].sum()
                   .round(2).reset_index().rename(columns={"realized_pnl": "Realized P&L ($)"}))
            st.markdown("**P&L by strategy**")
            st.dataframe(pnl, width="stretch")
    except Exception as exc:
        st.caption(f"(transactions store unavailable: {exc})")


def utcnow_str() -> str:
    """UTC timestamp string for footer display."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
