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
from gui.help_content import MODEL_RETRAIN_WINDOW_DAYS, metric_help
from gui.panels import load_state_snapshot

logger = logging.getLogger(__name__)

# Repo root = two levels up from this file (gui/panels/analytics.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Retrain window (days) beyond which an ML model is flagged 'Needs Retrain'.
# Sourced from gui.help_content.MODEL_RETRAIN_WINDOW_DAYS (which itself mirrors
# ml.meta_labeling.MetaLabeler(retrain_freq_days=30)) so the panel and its help
# text stay driven by one constant rather than two re-typed literals.
_RETRAIN_WINDOW_DAYS = MODEL_RETRAIN_WINDOW_DAYS


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
# ML model freshness helpers (pure — unit-testable outside Streamlit)
# ---------------------------------------------------------------------------


def _days_since(trained_date: Any) -> Optional[int]:
    """Whole days between *trained_date* and today, or ``None`` when unparseable.

    Accepts an ISO date/datetime string (``"2026-07-06"``) or a ``date`` /
    ``datetime`` object (PyYAML may parse an unquoted date as ``datetime.date``).
    Returns ``None`` — never a fabricated ``0`` — for missing / malformed input
    (CONSTRAINT #4).  A future-dated value clamps at ``0`` rather than going
    negative.
    """
    if trained_date is None:
        return None
    try:
        ts = pd.to_datetime(trained_date, errors="coerce")
    except Exception:  # noqa: BLE001
        return None
    if ts is None or pd.isna(ts):
        return None
    try:
        delta_days = (pd.Timestamp.now().normalize() - ts.normalize()).days
    except Exception:  # noqa: BLE001
        return None
    return max(0, int(delta_days))


def _needs_retrain(age_days: Optional[int], window: int = _RETRAIN_WINDOW_DAYS) -> Optional[bool]:
    """Whether a model is stale: last trained ``>= window`` days ago.

    Returns ``None`` (unknown) when *age_days* is ``None`` so the caller renders
    "—" rather than a fabricated verdict.  Mirrors
    ``ml.meta_labeling.MetaLabeler.needs_retrain()`` semantics (``>=`` window).
    """
    if age_days is None:
        return None
    return age_days >= window


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


def _parse_registry_rows(text: str) -> List[Dict[str, Any]]:
    """Parse ``ml/registry.yaml`` text into a flat list of model row dicts (pure).

    Returns ``[]`` on ANY failure (PyYAML missing, malformed YAML, unexpected
    shape) so the caller renders an "unavailable" message instead of a traceback
    (CONSTRAINT #6). ``null`` metrics are preserved as ``None`` (the render layer
    maps them to "—", never 0 — CONSTRAINT #4).
    """
    try:
        import yaml  # PyYAML — already a repo dependency.
    except Exception as exc:  # noqa: BLE001
        logger.debug("PyYAML unavailable for registry load: %s", exc)
        return []
    try:
        raw = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 — dead-letter, never raise into UI
        logger.debug("registry YAML parse failed: %s", exc)
        return []
    if not isinstance(raw, dict):
        return []
    models = raw.get("models")
    if not isinstance(models, dict):
        return []
    rows: List[Dict[str, Any]] = []
    for name, meta in models.items():
        if not isinstance(meta, dict):
            continue  # skip malformed entry rather than fabricating fields
        rows.append({
            "model": str(name),
            "role": meta.get("role"),
            "trained_date": meta.get("trained_date"),
            "cpcv_dsr": meta.get("cpcv_dsr"),
            "pbo": meta.get("pbo"),
            "n_train": meta.get("n_train"),
            "deployable": meta.get("deployable"),
        })
    return rows


@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _load_registry_rows_cached(path_str: str, mtime: float) -> List[Dict[str, Any]]:
    """mtime-keyed cached read of the registry file (``mtime`` in the cache key
    forces a refresh when the file changes, mirroring ``load_state_snapshot``)."""
    try:
        text = Path(path_str).read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — dead-letter, never raise into UI
        logger.debug("registry file read failed: %s", exc)
        return []
    return _parse_registry_rows(text)


def _load_ml_registry_rows() -> List[Dict[str, Any]]:
    """Resolve ``ml/registry.yaml`` (repo-root relative) and load its rows.

    Returns ``[]`` when the file is missing/unreadable/malformed so the panel
    shows an "unavailable" info message rather than an exception or a fabricated
    row (CONSTRAINT #6).
    """
    path = _REPO_ROOT / "ml" / "registry.yaml"
    try:
        if not path.exists():
            return []
        mtime = path.stat().st_mtime
    except Exception as exc:  # noqa: BLE001
        logger.debug("registry stat failed: %s", exc)
        return []
    return _load_registry_rows_cached(str(path), mtime)


@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _load_account_equity_history() -> pd.DataFrame:
    """Cached wrapper over ``HistoricalStore().account_snapshot_history()``.

    Returns the snapshot-history DataFrame, or an empty ``pd.DataFrame`` on any
    failure. DataFrames are natively cacheable by ``st.cache_data``.
    """
    try:
        from data.historical_store import HistoricalStore

        hist = HistoricalStore(readonly=True).account_snapshot_history()
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
# Section (d) — Rolling Beta vs SPY
# ---------------------------------------------------------------------------


def _render_rolling_beta_chart(snap: Dict[str, Any]) -> None:
    """On-demand rolling-beta chart: a symbol's time-varying beta vs SPY,
    computed from HistoricalStore-cached bars (DB-first, matching the
    platform's established bars-fetch convention).

    Distinct from the existing static point-in-time ``Beta`` column
    (config.COLUMN_SCHEMA) -- this shows how beta DRIFTS over time rather
    than a single snapshot. Not persisted as its own DB table; computed
    on-demand from whatever bars are already cached.
    """
    from gui.panels._shared import _active_symbols

    st.markdown("### 📉 Rolling Beta vs SPY")
    st.caption(
        "Time-varying beta (rolling covariance/variance vs SPY), distinct "
        "from the single point-in-time `Beta` column elsewhere in the "
        "platform. Computed on demand from cached price history."
    )

    universe = _active_symbols(snap)
    if not universe:
        st.info("No symbols available (no holdings, watchlist, or recent signals).")
        return

    c1, c2 = st.columns([3, 1])
    symbol = c1.selectbox("Symbol", options=universe, key="analytics_rolling_beta_symbol")
    window = c2.number_input(
        "Window (days)", min_value=10, max_value=252, value=60, step=10,
        key="analytics_rolling_beta_window",
    )

    if not st.button("Compute Rolling Beta", key="analytics_rolling_beta_button"):
        return

    try:
        from data.historical_store import HistoricalStore
        from processing_engine import calculate_rolling_beta

        store = HistoricalStore()
        price_df = store.get_bars(symbol, lookback_days=max(504, int(window) * 3))
        spy_df = store.get_bars("SPY", lookback_days=max(504, int(window) * 3))

        if price_df is None or price_df.empty or spy_df is None or spy_df.empty:
            st.info(f"Insufficient cached bars for {symbol} or SPY yet.")
            return

        beta_series = calculate_rolling_beta(price_df, spy_df, window=int(window))
        beta_series = beta_series.dropna()

        if beta_series.empty:
            st.info(
                f"Not enough overlapping history to compute a {window}-day "
                f"rolling beta for {symbol} yet."
            )
            return

        st.line_chart(beta_series)
        st.caption(
            f"Latest {window}-day rolling beta for {symbol}: "
            f"{beta_series.iloc[-1]:.2f} (as of {beta_series.index[-1].date()})."
        )
    except Exception as exc:  # noqa: BLE001 — dead-letter: never raise into UI
        logger.debug("rolling beta chart failed for %s: %s", symbol, exc)
        st.caption(f"(rolling beta unavailable: {exc})")


# ---------------------------------------------------------------------------
# Section (e) — ML Model Freshness & Deployability Monitoring
# ---------------------------------------------------------------------------


def _deployable_chip(deployable: Any) -> str:
    """Map a registry ``deployable`` flag to a ✅/❌ chip, or "—" when absent."""
    if deployable is True:
        return "✅ Yes"
    if deployable is False:
        return "❌ No"
    return "—"


def _render_ml_model_monitoring() -> None:
    """ML model freshness + deployability monitor over ``ml/registry.yaml``.

    Extends the sibling registry table (``analytics_signals.render_ml_registry``)
    with per-model **last-trained age**, a **Needs Retrain** flag (age vs the
    ``_RETRAIN_WINDOW_DAYS`` window), **DSR / PBO** (formatted, "—" on null), and
    a **deployable ✅/❌ chip**. Dead-letter safe: a missing/malformed registry
    renders an info message, never a traceback or fabricated row (CONSTRAINT #6).
    """
    st.markdown("### 🩺 ML Model Freshness & Deployability")
    st.caption(
        f"Per-model training freshness and the deployability gate from "
        f"`ml/registry.yaml`. A model is flagged **Needs Retrain** once its last "
        f"training run is older than the {_RETRAIN_WINDOW_DAYS}-day window "
        f"(mirrors `ml.meta_labeling.MetaLabeler.needs_retrain()`). Deployability "
        f"is separate from freshness — a deployable model can still be stale. "
        f"`null` metrics render `—`, never a fabricated 0."
    )

    rows = _load_ml_registry_rows()
    if not rows:
        st.info(
            "ML model registry unavailable (`ml/registry.yaml` missing, "
            "unreadable, or malformed)."
        )
        return

    table_rows: List[Dict[str, Any]] = []
    stale_count = 0
    for r in rows:
        age = _days_since(r.get("trained_date"))
        stale = _needs_retrain(age)
        if stale is True:
            stale_count += 1
        if stale is True:
            retrain_str = "⚠️ Yes"
        elif stale is False:
            retrain_str = "✅ No"
        else:
            retrain_str = "—"

        cpcv_dsr = r.get("cpcv_dsr")
        pbo = r.get("pbo")
        table_rows.append({
            "Model": r.get("model") or "—",
            "Role": r.get("role") or "—",
            "Trained": r.get("trained_date") or "—",
            "Age (days)": f"{age:,}" if age is not None else "—",
            "Needs Retrain": retrain_str,
            "CPCV DSR": _fmt_ml_metric(cpcv_dsr, "{:.4f}"),
            "PBO": _fmt_ml_metric(pbo, "{:.4f}"),
            "Deployable": _deployable_chip(r.get("deployable")),
        })

    # Summary tiles with help tooltips sourced from gui/help_content.py.
    deployable_count = sum(1 for r in rows if r.get("deployable") is True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Models Tracked", f"{len(rows):,}")
    c2.metric(
        "Deployable",
        f"{deployable_count:,}",
        help=metric_help("analytics.deployable"),
    )
    c3.metric(
        "Needs Retrain",
        f"{stale_count:,}",
        help=metric_help("analytics.needs_retrain"),
    )

    df = pd.DataFrame(
        table_rows,
        columns=[
            "Model", "Role", "Trained", "Age (days)", "Needs Retrain",
            "CPCV DSR", "PBO", "Deployable",
        ],
    )
    st.dataframe(df, width="stretch", hide_index=True)

    # Column-level tooltips (help text lives ONLY in gui/help_content.py).
    with st.expander("ℹ️ What these columns mean"):
        st.markdown(f"- **Age (days)** — {metric_help('analytics.last_trained_age')}")
        st.markdown(f"- **Needs Retrain** — {metric_help('analytics.needs_retrain')}")
        st.markdown(f"- **CPCV DSR** — {metric_help('analytics.cpcv_dsr')}")
        st.markdown(f"- **PBO** — {metric_help('analytics.pbo')}")
        st.markdown(f"- **Deployable** — {metric_help('analytics.deployable')}")


def _fmt_ml_metric(value: Any, fmt: str = "{:.4f}") -> str:
    """Format a registry metric, degrading ``None``/``NaN``/non-numeric to "—".

    CONSTRAINT #4: a missing metric is "—", never a fabricated 0.
    """
    if value is None:
        return "—"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "—"
    if f != f:  # NaN
        return "—"
    return fmt.format(f)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def render_analytics() -> None:
    """Render the 📊 Analytics tab.

    Sections (a) Broker Realized Performance, (b) Account Value Equity Curve,
    (c) Recent Alerts Feed, (d) Rolling Beta vs SPY — then delegates to the
    sibling ``gui.panels.analytics_signals`` panels (ML registry / news
    sentiment / slippage & CoVaR). The sibling delegation is guarded so this
    tab still renders its own sections even if that module is absent.
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
    st.divider()
    _render_rolling_beta_chart(snap)
    st.divider()
    _render_ml_model_monitoring()

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
