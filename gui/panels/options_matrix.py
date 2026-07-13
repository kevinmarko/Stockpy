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
from gui.help_content import metric_help


# ---------------------------------------------------------------------------
# Cached per-symbol directive compute (PR B — GUI panel caching)
#
# ``build_premium_directive`` runs a GJR-GARCH(1,1) MLE fit + full ATM
# Black-Scholes Greeks per symbol — by far the heaviest per-rerun compute in
# this tab, and it fired on EVERY rerun in "Auto-run" mode (and on every button
# click). Extracted into a module-level ``@st.cache_data`` loader keyed on the
# hashable inputs (symbol, DTE, macro VIX/regime, risk-free rate) + a TTL upper
# bound (the codebase convention — see ``analytics._load_realized_performance``).
# Behaviour-preserving: WHAT renders is identical (same directive row per input),
# the per-symbol progress bar and the per-symbol error surfacing are preserved
# in the render loop, and each cached call keeps its own dead-letter try/except
# so a bad symbol never aborts the batch (CONSTRAINT #6).
# ---------------------------------------------------------------------------


class _MacroProxy:
    """MacroEconomicDTO-shaped stub (``.vix`` / ``.market_regime`` only) so the
    VRP regime gate in ``build_premium_directive`` fires without a live FRED
    round-trip. Built from plain scalars so it is trivially constructible inside
    the cached loader from hashable args."""

    def __init__(self, vix: float, market_regime: str):
        self.vix = vix
        self.market_regime = market_regime


def _macro_from_snap(snap: dict) -> Tuple[float, str]:
    """Extract (vix, market_regime) from a state snapshot with neutral defaults.

    Anything missing is left at its neutral default — the gate only flips on
    positive evidence (VIX 15.0 / regime "RISK ON" reproduce the pre-cache
    ``_MacroProxy`` inline defaults exactly)."""
    vix = float(snap.get("vix")) if snap.get("vix") is not None else 15.0
    regime = str(snap.get("market_regime", "RISK ON"))
    return vix, regime


@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _compute_directive_row(
    symbol: str,
    target_dte: int,
    vix: float,
    market_regime: str,
    risk_free_rate: float,
) -> Dict[str, Any]:
    """Cached single-symbol premium-directive compute.

    Returns ``{"row": <directive dict>, "error": <str|None>}`` — the directive
    dict is scalars/lists (picklable by ``st.cache_data``). On failure the row
    is the same error-shaped placeholder the inline loop produced and ``error``
    carries the operator-facing message for the errors expander (CONSTRAINT #6:
    a bad symbol degrades, never raises).
    """
    from technical_options_engine import build_premium_directive
    from data.market_data import get_provider, MarketDataError

    provider = get_provider()
    macro_proxy = _MacroProxy(vix, market_regime)
    try:
        quote = provider.get_latest_quote(symbol)
        bars = provider.get_intraday_bars(symbol, lookback_days=252)
        row = build_premium_directive(
            symbol,
            bars,
            spot_price=float(quote.price),
            is_stale=bool(quote.is_stale),
            target_dte=int(target_dte),
            macro_dto=macro_proxy,
            vrp=None,  # VRP requires an options chain — left None to skip that gate
            risk_free_rate=risk_free_rate,
        )
        return {"row": row, "error": None}
    except MarketDataError as exc:
        logger.warning("market data error for %s: %s", symbol, exc)
        row = {"Symbol": symbol, "Strategy": "—", "Action": "—", "Integrity_OK": False,
               "Integrity_Issues": [str(exc)]}
        return {"row": row, "error": f"{symbol}: market data unavailable ({exc})"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("options matrix failed for %s: %s", symbol, exc)
        row = {"Symbol": symbol, "Strategy": "—", "Action": "—", "Integrity_OK": False,
               "Integrity_Issues": [str(exc)]}
        return {"row": row, "error": f"{symbol}: {exc}"}


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
    help_widgets.section_caption("options.matrix_intro")

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

    # Macro state (VIX + regime) forwarded into the directive so the VRP regime
    # gate fires identically to the live path — extracted as hashable scalars so
    # the per-symbol compute can be served by the cached loader.
    vix, market_regime = _macro_from_snap(snap)
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []

    progress = st.progress(0.0, text="Computing premium directives…")
    for i, sym in enumerate(symbols):
        result = _compute_directive_row(
            sym, int(target_dte), vix, market_regime, settings.RISK_FREE_RATE
        )
        rows.append(result["row"])
        if result["error"]:
            errors.append(result["error"])
        progress.progress((i + 1) / len(symbols),
                          text=f"Computing premium directives… ({i + 1}/{len(symbols)})")
    progress.empty()

    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No rows computed.")
        return

    # Freshness badge (Task C5): quotes/bars used for this matrix are only as
    # fresh as "just now" (the button click that triggered this compute), but
    # each row's own quote can still be stale per-symbol (see Stale column) —
    # this top-level badge is about the ON-DEMAND compute time, not per-quote
    # staleness, which is separately called out via each row's Stale flag.
    try:
        from gui.styling import freshness_badge
        st.caption(freshness_badge(
            datetime.now(timezone.utc), ttl_seconds=settings.MARKET_DATA_QUOTE_TTL_SECONDS,
            label="Matrix computed",
        ))
    except Exception as exc:  # noqa: BLE001 — cosmetic only
        logger.debug("options matrix freshness badge unavailable: %s", exc)

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

    help_widgets.section_caption("options.matrix_methodology")

    _render_portfolio_greeks_rollup(df)


# ===========================================================================
# Task C4 — Portfolio Greeks aggregate + theta-decay carry projection
# ===========================================================================


def _render_portfolio_greeks_rollup(df: pd.DataFrame) -> None:
    """Aggregate ATM Greeks across held positions + a 30-day theta carry projection.

    Weighting caveat (documented rather than fabricated)
    -----------------------------------------------------
    ``build_premium_directive`` returns PER-CONTRACT (or per-100-shares, ATM
    convention) Greeks — it has no visibility into how many contracts/lots an
    operator would actually trade per symbol. Robinhood share quantities (from
    ``cache/account_snapshot.json``) tell us which symbols are HELD, but equity
    share count is not a 1:1 proxy for options contract count (a covered call
    is 1 contract per 100 shares; a spread strategy's sizing is a distinct
    decision). Rather than fabricate a weighted number from an assumption we
    cannot verify, this rollup:

    1. Filters to symbols currently HELD (per the Robinhood account snapshot)
       AND that have a non-"Cash/Wait" directive (an actual actionable
       strategy) among the rows just computed.
    2. Sums the RAW per-symbol ATM Greeks across that filtered set and labels
       the result explicitly as an unweighted sum — never implying a
       position-sized portfolio Greek.
    """
    st.markdown("---")
    st.markdown("### 🧮 Portfolio Greeks Roll-Up (Held Positions)")

    greek_cols = ["ATM_Delta", "ATM_Gamma", "ATM_Vega", "ATM_Theta_Daily"]
    missing = [c for c in greek_cols if c not in df.columns]
    if missing:
        st.caption(f"Greeks columns not present in this run's directive output: {missing}")
        return

    held_syms = set(_held_symbols())
    if not held_syms:
        st.info(
            "No Robinhood holdings found in `cache/account_snapshot.json` — "
            "the roll-up only aggregates HELD positions (not the full "
            "watchlist) since summing Greeks across symbols you don't hold "
            "would misrepresent actual portfolio exposure. Run "
            "`python3 main.py --refresh-account` to populate holdings."
        )
        return

    held_df = df[df["Symbol"].astype(str).str.upper().isin(held_syms)].copy()
    if "Strategy" in held_df.columns:
        held_df = held_df[~held_df["Strategy"].astype(str).str.contains("Cash", case=False, na=False)]

    for c in greek_cols:
        held_df[c] = pd.to_numeric(held_df[c], errors="coerce")

    actionable = held_df.dropna(subset=greek_cols)
    if actionable.empty:
        st.info(
            "No held symbol currently has an actionable (non-Cash/Wait) "
            "options directive with computable Greeks — nothing to aggregate "
            "this cycle."
        )
        return

    total_delta = float(actionable["ATM_Delta"].sum())
    total_gamma = float(actionable["ATM_Gamma"].sum())
    total_vega = float(actionable["ATM_Vega"].sum())
    total_theta = float(actionable["ATM_Theta_Daily"].sum())

    st.caption(
        f"**Unweighted sum** of raw per-contract ATM Greeks across "
        f"{len(actionable)} held symbol(s) with an actionable directive "
        f"(no position-size/contract-count weighting — see caption above for why)."
    )
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Σ Delta", f"{total_delta:.3f}",
              help=metric_help("Portfolio Delta") or "Sum of per-symbol ATM delta.")
    g2.metric("Σ Gamma", f"{total_gamma:.4f}",
              help=metric_help("Portfolio Gamma") or "Sum of per-symbol ATM gamma.")
    g3.metric("Σ Vega", f"{total_vega:.3f}",
              help=metric_help("Portfolio Vega") or "Sum of per-symbol ATM vega.")
    g4.metric("Σ Theta / day", f"{total_theta:.3f}",
              help=metric_help("Portfolio Theta") or "Sum of per-symbol ATM daily theta.")

    # ── 30-day theta-decay carry projection ("if nothing moves") ─────────────
    st.markdown("**30-Day Theta Carry Projection**")
    carry_30d = total_theta * 30.0
    st.metric(
        "Cumulative Theta × 30 days",
        f"{carry_30d:.2f}",
        help=metric_help("Theta Carry Projection")
        or "Cumulative theta decay if held 30 days with no price/vol movement.",
    )
    st.caption(
        "⚠️ **This is NOT a forecast.** It is a mechanical projection of "
        "today's theta held flat for 30 days, assuming zero price movement, "
        "zero IV change, and no gamma/vega repricing — none of which is "
        "realistic over a full month. Treat it only as a rough 'time decay "
        "floor' reference, not an expected P&L."
    )

    st.dataframe(
        actionable[["Symbol", "Strategy"] + greek_cols],
        width="stretch",
        hide_index=True,
    )


# ===========================================================================
# Tab 8 — Market Data
# ===========================================================================


