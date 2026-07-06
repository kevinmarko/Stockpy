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


def render_live_inventory() -> None:
    """Render the synchronized portfolio + watchlist inventory + "Sync Now".

    Sources combined (read-only):
      - Robinhood account snapshot (positions, cost basis) — source of truth
        for held shares.
      - Every Robinhood "Lists" entry — discovered via
        :func:`data.robinhood_client.discover_watchlists`.
      - Plain-text watchlist files referenced by the ``SYNC_WATCHLIST_FILES``
        env var.
      - Market-data coverage probe — via
        :func:`data.portfolio_sync.build_sync_report`.
      - Pipeline forecast availability — derived from the last
        ``state_snapshot.json`` (a non-NaN ``Forecast_30`` column means the
        forecasting engine produced a number for that symbol).

    The **🔄 Sync Now** button schedules
    :func:`data.portfolio_sync.async_sync_now` on a background event loop,
    writes the discovered universe to ``DEFAULT_TICKERS`` in ``.env`` via the
    allowlist-bounded :mod:`gui.env_io` writer, and refreshes the panel — all
    without restarting the orchestrator.
    """
    help_widgets.explain("live_inventory")
    st.subheader("📡 Live Inventory & Synchronization")
    st.caption(
        "Holdings ∪ Robinhood watchlists ∪ file watchlists, reconciled against "
        "the active market-data feeds. Idiosyncratic positions without "
        "market-data coverage stay visible (EQUITY_ONLY) but are flagged so "
        "pricing-dependent metrics can exclude them."
    )

    # ------------------------------------------------------------------ #
    # 1. Resolve a Robinhood snapshot (use the session value if the operator
    #    already fetched one on the Paper Monitor tab — never block here).
    # ------------------------------------------------------------------ #
    snapshot_obj = st.session_state.get("rh_snapshot")
    col_fetch, col_sync = st.columns([1, 1])
    with col_fetch:
        fetch_rh = st.button(
            "📥 Refresh Robinhood snapshot",
            help="Fetch fresh holdings/dividends. Uses the daily cache when fresh.",
            width="stretch",
        )
    with col_sync:
        do_sync = st.button(
            "🔄 Sync Now",
            type="primary",
            help=(
                "Discover holdings + every Robinhood watchlist, reconcile against "
                "the market-data feeds, and write the union to DEFAULT_TICKERS "
                "in .env. Takes effect on the next orchestrator launch."
            ),
            width="stretch",
        )

    if fetch_rh:
        try:
            from data.robinhood_portfolio import fetch_account_snapshot

            snapshot_obj = fetch_account_snapshot()
            st.session_state["rh_snapshot"] = snapshot_obj
        except Exception as exc:  # noqa: BLE001 - never crash the panel
            st.error(f"Robinhood snapshot failed: {exc}")

    # ------------------------------------------------------------------ #
    # Quick-add ticker to watchlist.txt
    # Writes to the file only — never touches .env — so the GUI cannot
    # pollute the environment with stale ticker lists.  Picked up by
    # main.py's _load_watchlist() on the next run_once() call.
    # ------------------------------------------------------------------ #
    st.divider()
    st.caption("**➕ Quick-add ticker** — written to `watchlist.txt`, picked up on next run.")
    _wl_col_ticker, _wl_col_btn = st.columns([3, 1])
    with _wl_col_ticker:
        _new_ticker_raw = st.text_input(
            "Ticker symbol",
            key="live_inv_watchlist_add_ticker",
            placeholder="e.g. NVDA",
            label_visibility="collapsed",
        )
    with _wl_col_btn:
        _add_clicked = st.button(
            "➕ Add to watchlist",
            key="live_inv_watchlist_add_btn",
            help="Append the ticker to watchlist.txt (file-backed; never edits .env).",
            use_container_width=True,
        )
    if _add_clicked:
        _ticker = (_new_ticker_raw or "").strip().upper()
        if not _ticker:
            st.warning("Enter a ticker symbol before clicking Add.")
        elif not _ticker.replace(".", "").replace("-", "").isalnum():
            st.warning(f"'{_ticker}' does not look like a valid ticker symbol.")
        else:
            _wl_path = _REPO_ROOT / "watchlist.txt"
            try:
                # Deduplicate: only append if not already present.
                _existing: list[str] = []
                if _wl_path.exists():
                    _existing = [
                        ln.strip().upper()
                        for ln in _wl_path.read_text(encoding="utf-8").splitlines()
                        if ln.strip() and not ln.strip().startswith("#")
                    ]
                if _ticker in _existing:
                    st.info(f"**{_ticker}** is already in watchlist.txt.")
                else:
                    with _wl_path.open("a", encoding="utf-8") as _fh:
                        _fh.write(f"{_ticker}\n")
                    st.success(
                        f"**{_ticker}** added to `watchlist.txt`. "
                        "It will appear in the universe on the next pipeline run."
                    )
                    logger.info("Watchlist quick-add: appended %s to watchlist.txt", _ticker)
            except OSError as _exc:  # noqa: BLE001
                st.error(f"Could not write watchlist.txt: {_exc}")

    # ------------------------------------------------------------------ #
    # 2. Optionally trigger an async sync. Run the coroutine to completion on
    #    a freshly created event loop — Streamlit runs each interaction on a
    #    new thread so we MUST create the loop explicitly.
    # ------------------------------------------------------------------ #
    if do_sync:
        import asyncio

        from data.portfolio_sync import async_sync_now

        # Try to attach an authenticated RobinhoodClient — best-effort.
        client = None
        try:
            from data.robinhood_client import RobinhoodClient

            client = RobinhoodClient()
            client.login()
        except Exception as exc:  # noqa: BLE001
            logger.info("Sync Now without an authenticated client: %s", exc)

        # Forecast set from the latest pipeline snapshot.
        snap = load_state_snapshot()
        forecast_syms = [
            s.get("symbol") for s in snap.get("signals", [])
            if s.get("symbol")
        ]

        with st.status("Syncing portfolio…", expanded=True) as status:
            try:
                status.update(
                    label="Discovering universe (holdings ∪ watchlists ∪ files)…",
                    state="running",
                )
                loop = asyncio.new_event_loop()
                try:
                    status.update(
                        label="Probing market-data coverage…", state="running"
                    )
                    report = loop.run_until_complete(
                        async_sync_now(
                            snapshot_obj,
                            client=client,
                            forecast_symbols=forecast_syms,
                            persist_default_tickers=True,
                        )
                    )
                finally:
                    loop.close()
                status.update(
                    label="Persisting DEFAULT_TICKERS…", state="running"
                )
                st.session_state["last_sync_report"] = report
                st.success(
                    f"Synced {report.n_total} symbols "
                    f"({report.n_full} full, {report.n_equity_only} equity-only, "
                    f"{report.n_uncovered} uncovered). DEFAULT_TICKERS updated."
                )
                status.update(label="✅ Sync complete", state="complete")
            except Exception as exc:  # noqa: BLE001
                status.update(label="❌ Sync failed", state="error")
                st.error(f"Sync failed: {exc}")

    # ------------------------------------------------------------------ #
    # 3. Resolve the report to display: prefer the in-session result, then
    #    the on-disk cache from the most recent run, then nothing.
    # ------------------------------------------------------------------ #
    report = st.session_state.get("last_sync_report")
    cached_dict: Optional[dict] = None
    if report is None:
        from data.portfolio_sync import read_cache

        cached_dict = read_cache()
        if cached_dict is None:
            st.info(
                "No sync report yet. Click **Sync Now** to discover and "
                "reconcile your universe."
            )
            return

    # ------------------------------------------------------------------ #
    # 4. Header strip: counts + provider + timestamp.
    # ------------------------------------------------------------------ #
    if report is not None:
        rows = [s.to_dict() for s in report.symbols.values()]
        n_total = report.n_total
        n_full = report.n_full
        n_equity = report.n_equity_only
        n_unc = report.n_uncovered
        provider_src = report.provider_source or "—"
        funds_src = report.fundamentals_source or "—"
        ts = report.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        # Cached path
        rows = list((cached_dict or {}).get("symbols", {}).values())
        n_total = len(rows)
        n_full = sum(1 for r in rows if r.get("coverage") == "full")
        n_equity = sum(1 for r in rows if r.get("coverage") == "equity_only")
        n_unc = sum(1 for r in rows if r.get("coverage") == "uncovered")
        provider_src = (cached_dict or {}).get("provider_source") or "—"
        funds_src = (cached_dict or {}).get("fundamentals_source") or "—"
        ts = (cached_dict or {}).get("generated_at", "—")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Symbols", n_total)
    c2.metric("FULL coverage", n_full)
    c3.metric("EQUITY_ONLY", n_equity, help="Held but no market-data coverage")
    c4.metric("UNCOVERED", n_unc)
    c5.metric("Provider", str(provider_src),
              help=f"Fundamentals: {funds_src}")
    st.caption(f"Last sync: **{ts}**")

    # ------------------------------------------------------------------ #
    # 5. Inventory table.
    # ------------------------------------------------------------------ #
    if not rows:
        st.info("Sync report is empty.")
        return

    inventory_sym_query = st.text_input(
        "🔍 Filter by symbol",
        value="",
        key="inventory_symbol_search",
        placeholder="e.g. TSLA",
        help="Case-insensitive prefix/contains match — leave blank to show all.",
    )

    df = pd.DataFrame(rows)
    # Pretty column names + ordering for the visible inventory.
    display_cols = [
        ("symbol", "Symbol"),
        ("held", "Held?"),
        ("coverage", "Coverage"),
        ("quantity", "Qty"),
        ("avg_cost", "Avg Cost"),
        ("current_price", "Current Px"),
        ("cost_basis_delta_per_share", "Δ / share"),
        ("market_value", "Mkt Value"),
        ("is_stale_quote", "Stale?"),
        ("quote_source", "Source"),
        ("forecast_available", "Forecast?"),
        ("has_fundamentals", "Fundamentals?"),
        ("watchlists", "Lists"),
        ("diagnostic", "Diagnostic"),
    ]
    keep = [src for src, _ in display_cols if src in df.columns]
    rename = {src: lbl for src, lbl in display_cols if src in df.columns}
    df = df[keep].rename(columns=rename).copy()

    # Apply symbol search filter (uses the "Symbol" column after rename).
    df = filter_by_symbol(df, inventory_sym_query, column="Symbol")

    # Convert the watchlists list-of-strings to a comma-joined string so the
    # built-in dataframe renderer doesn't truncate to "[...]" text.
    if "Lists" in df.columns:
        df["Lists"] = df["Lists"].apply(
            lambda v: ", ".join(v) if isinstance(v, (list, tuple)) else (v or "")
        )

    # Filter widgets — pure pandas slicing, no extra dependency.
    f1, f2, f3 = st.columns(3)
    held_only = f1.checkbox("Held only", value=False)
    only_gaps = f2.checkbox(
        "Coverage gaps only", value=False,
        help="Show EQUITY_ONLY + UNCOVERED + QUOTES_ONLY.",
    )
    only_no_forecast = f3.checkbox("No forecast", value=False)

    filtered = df.copy()
    if held_only and "Held?" in filtered.columns:
        filtered = filtered[filtered["Held?"] == True]  # noqa: E712
    if only_gaps and "Coverage" in filtered.columns:
        filtered = filtered[filtered["Coverage"] != "full"]
    if only_no_forecast and "Forecast?" in filtered.columns:
        filtered = filtered[filtered["Forecast?"] == False]  # noqa: E712

    st.dataframe(filtered, width="stretch", hide_index=True)

    # ------------------------------------------------------------------ #
    # 6. Watchlist breakdown — quick reference of where symbols originated.
    # ------------------------------------------------------------------ #
    with st.expander("📂 Watchlists discovered", expanded=False):
        if report is not None:
            wl_map = report.watchlists
        else:
            wl_map = (cached_dict or {}).get("watchlists", {})
        if not wl_map:
            st.caption(
                "No Robinhood watchlists discovered. (Authenticate the "
                "RobinhoodClient or set SYNC_WATCHLIST_FILES.)"
            )
        else:
            for name, syms in wl_map.items():
                syms_list = list(syms) if isinstance(syms, (list, tuple)) else []
                st.markdown(f"**{name}** — {len(syms_list)} symbol(s)")
                st.code(", ".join(syms_list) or "(empty)", language="text")



