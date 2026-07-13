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
from gui.progress_ui import busy


# ---------------------------------------------------------------------------
# Cached loader (PR B — GUI panel caching)
#
# Streamlit reruns the whole script on every interaction, so the state-snapshot
# read + signal-symbol extraction that seeds the "Quote symbols" input ran on
# every render. Route it through an ``@st.cache_data`` loader keyed on the
# snapshot file's **mtime** (the codebase convention — see
# ``gui.panels.load_state_snapshot``). Behaviour-preserving: the returned symbol
# list is identical to ``_signal_symbols(load_state_snapshot())``; a changed
# mtime is a cache miss and forces a fresh read so a new pipeline run's symbols
# appear on the next render. Dead-letter intact: an absent/unreadable snapshot
# yields ``[]`` (the same empty default as before), never a raise.
# ---------------------------------------------------------------------------


@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _load_default_signal_symbols_cached(path_str: str, _mtime: float) -> List[str]:
    """mtime-keyed cached read of the snapshot → its signal symbols."""
    p = Path(path_str)
    if not p.exists():
        return []
    try:
        snap = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — dead-letter, never raise into UI
        logger.debug("market-data snapshot read failed: %s", exc)
        return []
    return _signal_symbols(snap)


def _load_default_signal_symbols() -> List[str]:
    """Signal symbols from the latest state snapshot (``[]`` when absent)."""
    snap_path = settings.OUTPUT_DIR / "state_snapshot.json"
    try:
        mtime = snap_path.stat().st_mtime if snap_path.exists() else 0.0
    except OSError:
        mtime = 0.0
    return _load_default_signal_symbols_cached(str(snap_path), mtime)


def render_market_data() -> None:
    """Market Data Provider tab — diagnostic-rich quote fetcher.

    Improvements over the legacy panel
    ----------------------------------
    *   **Connectivity badge** — sliding-window success rate from
        :class:`gui.market_data_diagnostics.FetchHealthTracker` (Healthy /
        Degraded / Down), persisted across reruns in ``st.session_state``.
    *   **Throttled batch fetch** — uses
        :class:`gui.market_data_diagnostics.BatchQuoteFetcher` with default
        100 ms spacing so a 50-symbol watchlist sync stops triggering
        yfinance / Finnhub rate-limit storms.
    *   **Progress bar + per-symbol streaming** — operator sees ``i/N``
        feedback rather than a frozen "Running" spinner.
    *   **Typed error feedback** — failed fetches surface a specific category
        ("API Rate Limited", "Symbol Not Found", "Network Timeout",
        "Malformed Response", "Unknown Error") via
        :func:`classify_market_error`, never an opaque ``None``.
    *   **Quote validation** — :func:`validate_quote` flags NaN price, missing
        timestamp, or inverted bid/ask with a ⚠ icon BEFORE the row is
        considered usable by the rest of the pipeline (CONSTRAINT #4).
    """
    help_widgets.explain("market_data")
    st.subheader("🛰️ Market Data Provider")

    from data.market_data import get_provider, reset_provider
    from gui.market_data_diagnostics import (
        BatchQuoteFetcher,
        FetchHealthTracker,
        category_label,
        summarise_categories,
    )
    from gui.observability_telemetry import LatencySampleStore

    provider = get_provider()
    src = getattr(provider, "quote_source", "unknown")
    realtime = getattr(provider, "is_realtime", False)

    # Persist the health tracker across Streamlit reruns so the badge survives
    # tab switches and the "Fetch quotes" button click cycle.
    tracker_key = "md_health_tracker"
    if tracker_key not in st.session_state:
        st.session_state[tracker_key] = FetchHealthTracker()
    health: FetchHealthTracker = st.session_state[tracker_key]
    report = health.status()

    # Shared latency store — also consumed by render_observability's heatmap so
    # one fetch in this tab updates the Observability view too.
    latency_key = "obs_latency_store"
    if latency_key not in st.session_state:
        st.session_state[latency_key] = LatencySampleStore()
    latency_store: LatencySampleStore = st.session_state[latency_key]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Provider", str(src))
    c2.metric("Mode", "🟢 real-time" if realtime else "🟡 delayed (~15 min)")
    c3.metric("Quote TTL", f"{settings.MARKET_DATA_QUOTE_TTL_SECONDS}s")
    c4.metric("Connection", report.badge(),
              help="Sliding window of the last 20 fetches. Healthy ≥ 90% success, "
                   "Degraded ≥ 50%, otherwise Down.")

    if not realtime:
        st.info(
            "🟡 yfinance is delayed by ~15 minutes and marked `is_stale=True` "
            "on every quote. Set `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` in `.env` "
            "to upgrade to the free IEX real-time feed.",
            icon="ℹ️",
        )

    bcol1, bcol2 = st.columns([1, 1])
    with bcol1:
        if st.button("♻️ Reset provider singleton",
                     help="Drops the cached provider so the next fetch re-evaluates env vars."):
            try:
                with busy("Resetting provider singleton…"):
                    reset_provider()
                st.success("Provider singleton reset — re-selected on next quote.")
            except Exception as exc:
                st.error(f"Reset failed: {exc}")
    with bcol2:
        if st.button("🩺 Reset connection health",
                     help="Clear the success/failure ledger (badge returns to Healthy)."):
            with busy("Resetting connection health…"):
                st.session_state[tracker_key] = FetchHealthTracker()
            st.rerun()

    symbols_default = _load_default_signal_symbols()
    sym_text = st.text_input(
        "Quote symbols",
        value=", ".join(symbols_default[:10]),
        key="md_syms",
        help="Comma- or space-separated tickers. Each fetch is throttled to "
             "≥100 ms apart to avoid free-tier rate limits.",
    )
    symbols = [s.strip().upper() for s in sym_text.replace(",", " ").split() if s.strip()]

    spacing_ms = st.slider(
        "Throttle (ms between fetches)", min_value=0, max_value=1000,
        value=100, step=25,
        help="Sliding gap between consecutive provider calls. 100 ms is safe "
             "for both yfinance and Alpaca free tiers.",
    )

    if st.button("Fetch quotes", type="primary"):
        if not symbols:
            st.warning("Enter at least one symbol.")
            return

        fetcher = BatchQuoteFetcher(
            fetch_fn=provider.get_latest_quote,
            spacing_seconds=spacing_ms / 1000.0,
            health_tracker=health,
        )

        progress = st.progress(0.0, text=f"Fetching 0/{len(symbols)}…")
        rows: List[Dict[str, Any]] = []
        results = []
        n = len(symbols)
        for result in fetcher.iter_fetch(symbols):
            results.append(result)
            if result.quote is not None:
                q = result.quote
                v = result.validation
                if q.timestamp is not None:
                    latency_store.record(
                        symbol=q.symbol, source=q.source,
                        quote_timestamp=q.timestamp, is_stale=q.is_stale,
                    )
                rows.append({
                    "Status": (v.label if v is not None else "OK"),
                    "Symbol": q.symbol,
                    "Price": round(float(q.price), 2) if v and v.ok else q.price,
                    "Bid": q.bid,
                    "Ask": q.ask,
                    "Stale": q.is_stale,
                    "Source": q.source,
                    "Error": "",
                    "Timestamp (UTC)": q.timestamp.isoformat() if q.timestamp else "—",
                })
            else:
                rows.append({
                    "Status": "❌ ERROR",
                    "Symbol": result.symbol,
                    "Price": None,
                    "Bid": None,
                    "Ask": None,
                    "Stale": None,
                    "Source": str(src),
                    "Error": (
                        f"{category_label(result.category)}: {result.error}"
                        if result.category is not None
                        else f"Unknown Error: {result.error}"
                    ),
                    "Timestamp (UTC)": "—",
                })
            progress.progress(
                (result.index + 1) / n,
                text=f"Fetching {result.index + 1}/{n} — {result.symbol}",
            )

        progress.empty()
        st.session_state["md_last_results"] = rows
        tally = summarise_categories(results)
        ok_count = tally.get("ok", 0)
        bad_count = sum(v for k, v in tally.items() if k != "ok")

        if bad_count == 0:
            st.success(f"✅ Fetched {ok_count}/{n} symbols cleanly.", icon="✅")
        else:
            breakdown = ", ".join(f"{k}: {v}" for k, v in tally.items() if k != "ok")
            st.warning(
                f"⚠️ {ok_count}/{n} ok • {bad_count} failed → {breakdown}",
                icon="⚠️",
            )

    rows = st.session_state.get("md_last_results")
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, width="stretch", hide_index=True)
        st.caption(
            "⚠ icons in **Status** mark malformed quotes (NaN price, missing "
            "timestamp, inverted bid/ask). These rows are never silently "
            "promoted into the quant pipeline."
        )


# ===========================================================================
# Tab 9 — Observability (folded-in summary of the existing dashboard)
# ===========================================================================


