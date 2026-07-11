"""
gui/panels/analytics.py
=======================
📊 **Analytics** tab — read-only performance/analytics surface distinct from the
Observability tab's *internal* paper-store view.

Three sections owned by this module:

1.  **Broker Realized Performance** — realized P&L reconstructed from Robinhood
    order history (PURE FIFO round-trips) via
    :func:`data.robinhood_orders.realized_performance`.  Labelled clearly as
    *Broker (Robinhood order history)* so it is never confused with the internal
    ``TransactionsStore`` P&L shown on the Observability tab.
2.  **Account Value Equity Curve** — ``total_equity`` over time from the
    persistent account-snapshot store
    (:meth:`data.historical_store.HistoricalStore.account_snapshot_history`),
    distinct from the closed-trade-derived curve in Observability.
3.  **Recent Alerts Feed** — a read-only tail of the JSONL alert file written by
    ``observability/alerts.py`` (keys: ``timestamp`` / ``level`` / ``message``).

After its own three sections, ``render_analytics`` delegates to the sibling
signals-analytics panels (``gui.panels.analytics_signals``); that import + the
three calls are wrapped in try/except so the tab still renders these three
sections even if the sibling module is not present yet.

Design
------
Read-only / file-backed, dead-letter friendly (CONSTRAINT #6) — every external
call is wrapped in try/except so one failing source renders an inline message
instead of aborting the tab.  No fabricated metrics (CONSTRAINT #4): an empty
realized-performance summary shows an empty-state message, never a fabricated
``0.0`` win rate / profit factor.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from settings import settings
from gui import help_widgets
from gui.panels import load_state_snapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_nan(x: Any) -> bool:
    """True when *x* is a NaN float (or otherwise non-finite numeric)."""
    try:
        return isinstance(x, float) and math.isnan(x)
    except Exception:  # noqa: BLE001
        return False


def _trade_to_row(trade: Any) -> Dict[str, Any]:
    """Coerce a ``ClosedTrade`` (dataclass) — or an already-dict trade — to a
    display row dict.  Tolerant of both shapes so the panel never assumes an
    internal representation."""
    if hasattr(trade, "to_dict"):
        try:
            return trade.to_dict()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(trade, dict):
        return dict(trade)
    # Last-resort attribute scrape.
    return {
        k: getattr(trade, k, None)
        for k in (
            "symbol", "quantity", "entry_ts", "exit_ts", "entry_price",
            "exit_price", "realized_pnl", "return_pct", "holding_days",
        )
    }


# ---------------------------------------------------------------------------
# Cached loaders (PR B — GUI panel caching)
#
# Streamlit reruns the whole script on every interaction, so these two
# unconditional network/DB loads previously fired on every render (a cold
# Robinhood order cache = a full login mid-render; a fresh sqlite connection
# for the equity curve). Extracted into module-level ``@st.cache_data`` loaders
# keyed on a TTL upper bound (the codebase convention — see
# ``gui.panels.load_state_snapshot``). Behaviour-preserving: WHAT is shown is
# unchanged; only WHEN the underlying data is fetched changes. Each loader keeps
# its try/except inside and returns an empty sentinel (``{}`` / empty DataFrame)
# on failure — never raises into the UI (CONSTRAINT #6).
# ---------------------------------------------------------------------------


@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _load_realized_performance() -> Optional[Dict[str, Any]]:
    """Cached wrapper over :func:`data.robinhood_orders.realized_performance`.

    Returns the raw result dict (``{"summary": {...}, "trades": [ClosedTrade,
    ...], "n_fills": int}``) on success, or ``None`` when the fetch itself
    FAILED (so the caller can show the distinct "unavailable" message rather
    than collapsing a broker/auth error into the "no trades yet" empty-state).
    ``ClosedTrade`` is a frozen dataclass of plain scalar/datetime fields, so
    the returned dict is trivially picklable by ``st.cache_data``.
    """
    try:
        from data.robinhood_orders import realized_performance

        return realized_performance() or {}
    except Exception as exc:  # noqa: BLE001 — dead-letter: never raise into UI
        logger.debug("realized_performance() failed: %s", exc)
        return None


@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _load_account_equity_history() -> pd.DataFrame:
    """Cached wrapper over ``HistoricalStore().account_snapshot_history()``.

    Returns the snapshot-history DataFrame, or an empty ``pd.DataFrame`` on any
    failure. DataFrames are natively cacheable by ``st.cache_data``.
    """
    try:
        from data.historical_store import HistoricalStore

        hist = HistoricalStore().account_snapshot_history()
        return hist if hist is not None else pd.DataFrame()
    except Exception as exc:  # noqa: BLE001 — dead-letter: never raise into UI
        logger.debug("account_snapshot_history() failed: %s", exc)
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Section (a) — Broker Realized Performance
# ---------------------------------------------------------------------------


def _render_broker_realized_performance() -> None:
    """Realized P&L reconstructed from Robinhood filled-order history.

    Source of truth: :func:`data.robinhood_orders.realized_performance` — itself
    dead-letter resilient (empty NaN summary on any Robinhood failure). Wrapped
    here in a belt-and-suspenders try/except regardless.
    """
    st.markdown("### 💰 Broker Realized Performance")
    st.caption(
        "Realized P&L reconstructed from your **Robinhood order history** "
        "(PURE FIFO round-trip matching of filled equity orders). This is "
        "distinct from the *internal* paper-store P&L on the **Observability** "
        "tab — this section reflects your real brokerage fills."
    )
    st.caption("Source: **Broker (Robinhood order history)** · read-only, analytics-only.")

    result = _load_realized_performance()
    if result is None:
        st.info(
            "Broker realized performance is unavailable right now "
            "(Robinhood order history could not be read)."
        )
        return

    summary: Dict[str, Any] = (result or {}).get("summary", {}) or {}
    trades: List[Any] = (result or {}).get("trades", []) or []
    n_trades = summary.get("n_trades", 0) or 0

    if not n_trades:
        st.info(
            "No closed round-trip trades reconstructed from Robinhood order "
            "history yet. (Round-trips appear once at least one sell has been "
            "matched against an earlier buy lot; run "
            "`python3 main.py --refresh-account` to refresh the order cache.)"
        )
        return

    total_pnl = summary.get("total_realized_pnl")
    win_rate = summary.get("win_rate")
    profit_factor = summary.get("profit_factor")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric(
        "Total Realized P&L",
        f"${total_pnl:,.2f}" if isinstance(total_pnl, (int, float)) and not _is_nan(total_pnl) else "—",
    )
    k2.metric(
        "Win Rate",
        f"{win_rate:.1%}" if isinstance(win_rate, (int, float)) and not _is_nan(win_rate) else "—",
        help="Fraction of closed round-trips with positive realized P&L.",
    )
    k3.metric(
        "Profit Factor",
        f"{profit_factor:.2f}" if isinstance(profit_factor, (int, float)) and not _is_nan(profit_factor) else "—",
        help="Gross profit ÷ |gross loss|. Undefined (—) when there are no losing trades.",
    )
    k4.metric("Closed Round-Trips", f"{int(n_trades):,}")

    # Secondary stats row.
    avg_win = summary.get("avg_win")
    avg_loss = summary.get("avg_loss")
    avg_ret = summary.get("avg_return_pct")
    avg_hold = summary.get("avg_holding_days")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Avg Win", f"${avg_win:,.2f}" if isinstance(avg_win, (int, float)) and not _is_nan(avg_win) else "—")
    s2.metric("Avg Loss", f"${avg_loss:,.2f}" if isinstance(avg_loss, (int, float)) and not _is_nan(avg_loss) else "—")
    s3.metric("Avg Return %", f"{avg_ret:.2f}%" if isinstance(avg_ret, (int, float)) and not _is_nan(avg_ret) else "—")
    s4.metric("Avg Holding (days)", f"{avg_hold:.1f}" if isinstance(avg_hold, (int, float)) and not _is_nan(avg_hold) else "—")

    # Recent closed trades table (most recent exit first).
    try:
        rows = [_trade_to_row(t) for t in trades]
        df = pd.DataFrame(rows)
        if not df.empty and "exit_ts" in df.columns:
            df = df.sort_values("exit_ts", ascending=False)
        show_cols = [
            c for c in
            ["symbol", "quantity", "entry_ts", "exit_ts", "entry_price",
             "exit_price", "realized_pnl", "return_pct", "holding_days"]
            if c in df.columns
        ]
        st.markdown("**Recent closed round-trips**")
        st.dataframe(
            df[show_cols].head(50) if show_cols else df.head(50),
            width="stretch",
            hide_index=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("closed-trades table render failed: %s", exc)
        st.caption(f"(closed-trades table unavailable: {exc})")


# ---------------------------------------------------------------------------
# Section (b) — Account Value Equity Curve
# ---------------------------------------------------------------------------


def _render_account_equity_curve() -> None:
    """Account-value equity curve from the persistent snapshot store.

    Plots ``total_equity`` (and optionally ``buying_power``) over ``fetched_at``.
    Distinct from the closed-trade-derived cumulative-P&L curve on the
    Observability tab — this reflects total account value snapshots.
    """
    st.markdown("### 📈 Account Value Equity Curve")
    st.caption(
        "Total account **equity** over time, from stored Robinhood account "
        "snapshots (`data/historical_store.py`). Distinct from the closed-trade "
        "cumulative-P&L curve on the Observability tab — this is total account "
        "value, not realized trade P&L."
    )

    hist = _load_account_equity_history()

    if hist is None or hist.empty or "fetched_at" not in hist.columns:
        st.info(
            "No account snapshots stored yet. Run "
            "`python3 main.py --refresh-account` to fetch and persist a "
            "Robinhood account snapshot; the curve populates as snapshots "
            "accumulate over time."
        )
        return

    try:
        df = hist.copy()
        df["fetched_at"] = pd.to_datetime(df["fetched_at"], errors="coerce")
        df = df.dropna(subset=["fetched_at"]).sort_values("fetched_at")
        if df.empty:
            st.info("Account snapshots exist but have no parseable timestamps yet.")
            return

        latest_equity = df["total_equity"].iloc[-1] if "total_equity" in df.columns else None
        latest_bp = df["buying_power"].iloc[-1] if "buying_power" in df.columns else None
        c1, c2, c3 = st.columns(3)
        c1.metric("Snapshots", f"{len(df):,}")
        c2.metric(
            "Latest Equity",
            f"${latest_equity:,.0f}" if isinstance(latest_equity, (int, float)) and not _is_nan(latest_equity) else "—",
        )
        c3.metric(
            "Latest Buying Power",
            f"${latest_bp:,.0f}" if isinstance(latest_bp, (int, float)) and not _is_nan(latest_bp) else "—",
        )

        try:
            from evaluation_engine import calculate_equity_curve_metrics

            metrics = calculate_equity_curve_metrics(df)
        except Exception as exc:  # noqa: BLE001 — dead-letter: never raise into UI
            logger.debug("calculate_equity_curve_metrics failed: %s", exc)
            metrics = {}

        d1, d2, d3, d4 = st.columns(4)
        sharpe = metrics.get("sharpe_ratio", float("nan"))
        calmar = metrics.get("calmar_ratio", float("nan"))
        max_dd = metrics.get("max_drawdown", float("nan"))
        dd_dur = metrics.get("max_drawdown_duration_days", float("nan"))
        d1.metric("Sharpe Ratio", f"{sharpe:.2f}" if not _is_nan(sharpe) else "—")
        d2.metric("Calmar Ratio", f"{calmar:.2f}" if not _is_nan(calmar) else "—")
        d3.metric("Max Drawdown", f"{max_dd:.1%}" if not _is_nan(max_dd) else "—")
        d4.metric(
            "Max DD Duration",
            f"{dd_dur:.0f}d" if not _is_nan(dd_dur) else "—",
        )

        overlay_bp = st.checkbox(
            "Overlay buying power", value=False, key="analytics_equity_overlay_bp",
            help="Add the buying-power series to the equity chart.",
        )
        plot_cols = [c for c in ["total_equity"] if c in df.columns]
        if overlay_bp and "buying_power" in df.columns:
            plot_cols.append("buying_power")

        if plot_cols:
            chart_df = df.set_index("fetched_at")[plot_cols].rename(
                columns={"total_equity": "Total Equity ($)", "buying_power": "Buying Power ($)"}
            )
            st.line_chart(chart_df, width="stretch")
        else:
            st.caption("No `total_equity` column present in the snapshot history.")
    except Exception as exc:  # noqa: BLE001
        logger.debug("equity curve render failed: %s", exc)
        st.caption(f"(equity curve unavailable: {exc})")


# ---------------------------------------------------------------------------
# Section (c) — Recent Alerts Feed
# ---------------------------------------------------------------------------


def _read_alert_tail(path: Path, max_lines: int = 50) -> List[Dict[str, Any]]:
    """Read + JSON-parse the last ``max_lines`` lines of the alert JSONL file.

    Malformed lines are skipped (never raise). Returns newest-first."""
    entries: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception as exc:  # noqa: BLE001
        logger.debug("alert file read failed: %s", exc)
        return entries

    for line in lines[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                entries.append(obj)
        except Exception:  # noqa: BLE001 — skip malformed line
            continue
    entries.reverse()  # newest first
    return entries


def _render_recent_alerts() -> None:
    """Read-only tail of the JSONL alert feed written by ``observability/alerts.py``.

    JSON keys emitted by the file channel: ``timestamp`` / ``level`` /
    ``message`` (plus any ``extra`` keys). Unset / missing path → instructional
    message. Read-only — no send-test button in this PR.
    """
    st.markdown("### 🔔 Recent Alerts Feed")
    st.caption(
        "Tail of the structured alert log (`observability/alerts.py` file "
        "channel). Read-only — configure `ALERT_FILE_PATH` to enable."
    )

    raw_path = getattr(settings, "ALERT_FILE_PATH", None)
    if not raw_path:
        st.info(
            "Alert file not configured (set `ALERT_FILE_PATH` in `.env` to a "
            "JSONL path to enable the alerts feed)."
        )
        return

    path = Path(raw_path)
    if not path.exists():
        st.info(
            f"No alert file yet at `{path}`. It is created the first time an "
            "alert is dispatched to the `file` channel."
        )
        return

    entries = _read_alert_tail(path, max_lines=50)
    if not entries:
        st.info("Alert file is present but contains no parseable alert entries yet.")
        return

    rows: List[Dict[str, Any]] = []
    for e in entries:
        rows.append({
            "Timestamp": e.get("timestamp", "—"),
            "Level": e.get("level", "—"),
            "Message": e.get("message", ""),
        })
    df = pd.DataFrame(rows)

    # Level tally.
    counts = df["Level"].value_counts().to_dict()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("CRITICAL", int(counts.get("CRITICAL", 0)))
    c2.metric("WARNING", int(counts.get("WARNING", 0)))
    c3.metric("INFO", int(counts.get("INFO", 0)))
    c4.metric("Shown", len(df))

    st.dataframe(df, width="stretch", hide_index=True)
    st.caption(f"Last {len(df)} alert(s) from `{path}` (newest first).")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def render_analytics() -> None:
    """Render the 📊 Analytics tab.

    Sections (a) Broker Realized Performance, (b) Account Value Equity Curve,
    (c) Recent Alerts Feed — then delegates to the sibling
    ``gui.panels.analytics_signals`` panels (ML registry / news sentiment /
    slippage & CoVaR). The sibling delegation is guarded so this tab still
    renders its three own sections even if that module is absent.
    """
    help_widgets.explain("analytics")
    st.subheader("📊 Analytics")
    st.caption(
        "Read-only performance & analytics — broker realized P&L, account-value "
        "equity curve, and the alerts feed. Distinct from the Observability tab's "
        "internal paper-store view."
    )

    snap = load_state_snapshot()

    _render_broker_realized_performance()
    st.divider()
    _render_account_equity_curve()
    st.divider()
    _render_recent_alerts()

    # ── Sibling signals-analytics panels (Agent C's module) ──────────────────
    try:
        from gui.panels import analytics_signals

        st.divider()
        analytics_signals.render_ml_registry()
        st.divider()
        analytics_signals.render_news_sentiment(snap)
        st.divider()
        analytics_signals.render_slippage_covar(snap)
    except Exception as exc:  # noqa: BLE001 — sibling module optional / in-flight
        logger.debug("analytics_signals panels unavailable: %s", exc)
        st.caption(
            "_Signals-analytics panels (ML registry / news sentiment / "
            "slippage & CoVaR) are not available yet._"
        )
