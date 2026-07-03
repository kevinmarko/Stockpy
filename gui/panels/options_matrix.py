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


def render_options_matrix() -> None:
    """Hydrated premium-selling matrix across held + watchlist + signal symbols.

    Pipeline per symbol (dead-letter resilient, CONSTRAINT #6):
      1. Provider quote + 252-day OHLCV.
      2. ``build_premium_directive`` — GJR-GARCH σ, realized-vol IVR proxy,
         Aroon+Coppock trend bias, full ATM Black-Scholes Greeks, deterministic
         strategy directive (Put Credit / Iron Condor / Debit / Covered Call),
         realizable daily theta after DTE-scaled execution friction, and the
         per-leg matrix-integrity verdict ($0.50 strike grid + delta-target
         tolerance).
      3. The macro state snapshot is forwarded into the directive so the VRP
         regime gate (VIX ≥ 30 ∨ CREDIT EVENT) fires identically to the live
         orchestrator path — no premium-selling advice in a stress regime.

    The universe auto-iterates **all** active symbols from
    :func:`_active_symbols` (held Robinhood positions ∪ watchlist ∪ last
    pipeline signals) so no premium-selling opportunity is silently dropped.
    """
    help_widgets.explain("options")
    st.subheader("🧮 Technical Options Matrix")
    st.caption(
        "Hydrated premium-selling matrix: GJR-GARCH σ, realized-vol IVR proxy, "
        "Aroon+Coppock trend bias, ATM Black-Scholes Greeks, and the "
        "deterministic strategy directive with $0.50 strike-grid integrity checks."
    )

    snap = load_state_snapshot()
    default_universe = _active_symbols(snap)

    col_syms, col_dte, col_auto = st.columns([4, 1, 1])
    with col_syms:
        sym_text = st.text_input(
            "Symbols",
            value=", ".join(default_universe),
            help="Auto-populated from held positions ∪ watchlist ∪ last signals. Edit to override.",
        )
    with col_dte:
        target_dte = st.number_input(
            "Target DTE", min_value=1, max_value=120, value=30, step=1,
            help="Days to expiration used by Black-Scholes and the theta haircut.",
        )
    with col_auto:
        auto_run = st.checkbox(
            "Auto-run", value=False,
            help="Recompute on every rerun (otherwise click the button).",
        )

    symbols = [s.strip().upper() for s in sym_text.split(",") if s.strip()]
    if not symbols:
        st.info("Enter at least one symbol.")
        return

    run = auto_run or st.button("▶️ Compute matrix", type="primary")
    if not run:
        st.caption(f"{len(symbols)} symbol(s) queued: {', '.join(symbols[:25])}"
                   + (" …" if len(symbols) > 25 else ""))
        return

    from technical_options_engine import build_premium_directive
    from data.market_data import get_provider, MarketDataError

    # Lightweight MacroEconomicDTO-shaped object built from the snapshot so the
    # regime gate can fire without a live FRED round-trip. Anything missing is
    # left at its neutral default — the gate only flips on positive evidence.
    class _MacroProxy:
        def __init__(self, snap_: dict):
            self.vix = float(snap_.get("vix")) if snap_.get("vix") is not None else 15.0
            self.market_regime = str(snap_.get("market_regime", "RISK ON"))

    macro_proxy = _MacroProxy(snap)
    provider = get_provider()
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []

    progress = st.progress(0.0, text="Computing premium directives…")
    for i, sym in enumerate(symbols):
        try:
            quote = provider.get_latest_quote(sym)
            bars = provider.get_intraday_bars(sym, lookback_days=252)
            row = build_premium_directive(
                sym,
                bars,
                spot_price=float(quote.price),
                is_stale=bool(quote.is_stale),
                target_dte=int(target_dte),
                macro_dto=macro_proxy,
                vrp=None,  # VRP requires an options chain — left None to skip that gate
                risk_free_rate=settings.RISK_FREE_RATE,
            )
        except MarketDataError as exc:
            logger.warning("market data error for %s: %s", sym, exc)
            errors.append(f"{sym}: market data unavailable ({exc})")
            row = {"Symbol": sym, "Strategy": "—", "Action": "—", "Integrity_OK": False,
                   "Integrity_Issues": [str(exc)]}
        except Exception as exc:  # noqa: BLE001
            logger.warning("options matrix failed for %s: %s", sym, exc)
            errors.append(f"{sym}: {exc}")
            row = {"Symbol": sym, "Strategy": "—", "Action": "—", "Integrity_OK": False,
                   "Integrity_Issues": [str(exc)]}
        rows.append(row)
        progress.progress((i + 1) / len(symbols),
                          text=f"Computing premium directives… ({i + 1}/{len(symbols)})")
    progress.empty()

    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No rows computed.")
        return

    # Stable column order matching config.COLUMN_SCHEMA naming conventions where
    # they overlap. NaN columns are tolerated by Streamlit's dataframe widget.
    column_order = [
        "Symbol", "Price", "Stale",
        "Sigma_GARCH", "IVR_Proxy",
        "Aroon_Oscillator", "Coppock_Curve", "Trend_Bias",
        "Strategy", "Action",
        "Short_Strike", "Short_Delta", "Long_Strike", "Long_Delta",
        "Net_Premium", "Realizable_Daily_Theta",
        "ATM_Delta", "ATM_Gamma", "ATM_Vega", "ATM_Theta_Daily",
        "Integrity_OK",
    ]
    display_cols = [c for c in column_order if c in df.columns]
    st.dataframe(df[display_cols], width="stretch")

    # Integrity verdict summary (top-line readout — drill-down available below).
    if "Integrity_OK" in df.columns:
        ok_count = int(df["Integrity_OK"].sum())
        total = len(df)
        if ok_count == total:
            st.success(f"✅ Matrix integrity: {ok_count}/{total} legs on $0.50 grid + within delta tolerance.")
        else:
            st.warning(f"⚠️ Matrix integrity: {ok_count}/{total} clean; "
                       f"{total - ok_count} symbol(s) flagged below.")

    # Per-symbol breakdown for any flagged or actionable row.
    flagged = df[~df.get("Integrity_OK", True).fillna(False).astype(bool)]
    if not flagged.empty:
        with st.expander(f"🔬 Integrity issues ({len(flagged)})", expanded=False):
            for _, r in flagged.iterrows():
                issues = r.get("Integrity_Issues") or []
                st.markdown(f"**{r.get('Symbol', '?')}** — {r.get('Strategy', '?')}")
                for issue in issues:
                    st.markdown(f"  - {issue}")

    if errors:
        with st.expander(f"⚠️ Errors ({len(errors)})", expanded=False):
            for e in errors:
                st.markdown(f"- {e}")

    st.caption(
        "σ from GJR-GARCH(1,1) with 20-day realized fallback; **IVR proxy** is a "
        "realized-vol percentile (true IVR requires an options chain). Trend bias is "
        "Aroon+Coppock sign agreement. **Stale=True** marks delayed (~15 min) yfinance "
        "quotes. Realizable Theta applies a DTE-scaled execution-friction haircut "
        "(40% @ 1DTE, 22% @ 7DTE, 12% @ 30DTE, 5% baseline)."
    )


# ===========================================================================
# Tab 8 — Market Data
# ===========================================================================


