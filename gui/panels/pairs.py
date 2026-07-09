"""
gui/panels/pairs.py
===================
🔗 **Pairs** tab — a READ-ONLY, ADVISORY-ONLY surface over the built-but-
previously-invisible pairs-trading engine (``signals/pairs_trading.py`` +
``pairs/*``).

The pairs engine is not a per-ticker ``SignalModule`` and therefore never
reached any GUI.  This panel exposes it purely as analytics: it **displays**
cointegration candidates and the current spread-based signal for a chosen
pair — it never places, modifies, or proposes any order.  Like every other
panel it is file-backed / provider-read-only and wrapped by
:func:`gui.app.safe_panel` at the app level.

Two modes
---------
1. **Scan** — take a comma-separated symbol list, fetch each Close series,
   inner-join into an aligned price frame, and run
   :func:`pairs.cointegration.find_cointegrated_pairs` to rank candidate
   cointegrated pairs (p-value ascending, half-life 5–60 days).
2. **Analyze a pair** — pick symbol *Y* and *X*, fetch + align their Closes,
   run :func:`signals.pairs_trading.generate_pairs_signals`, and render the
   current z-score / hedge-ratio / rolling-ADF KPIs, the current human-readable
   signal (entry / hold / exit / stop / not-cointegrated), and a z-score
   time-series chart with the ±2 / ±4 threshold context.

Pure helpers (``_signal_label`` / ``_align_closes``) hold the testable logic so
the coordinator can unit-test them without a Streamlit runtime.
"""

from __future__ import annotations

import logging
from typing import Dict, List

import numpy as np
import pandas as pd
import streamlit as st

from gui import help_widgets
from gui.panels._shared import _active_symbols
from gui.panels import load_state_snapshot

logger = logging.getLogger(__name__)

# Canonical pairs-trading rule thresholds (mirror the defaults in
# ``signals.pairs_trading.generate_pairs_signals``).  Kept as module constants
# so the KPI captions and ``_signal_label`` never drift from the engine.
ENTRY_THRESHOLD = 2.0
EXIT_THRESHOLD = 0.0
STOP_LOSS_THRESHOLD = 4.0
ADF_EXIT_THRESHOLD = 0.10
MIN_HALF_LIFE_DAYS = 5
MAX_HALF_LIFE_DAYS = 60


# ---------------------------------------------------------------------------
# Pure, Streamlit-free helpers (unit-testable)
# ---------------------------------------------------------------------------
def _align_closes(series_by_symbol: Dict[str, pd.Series]) -> pd.DataFrame:
    """Inner-join a mapping of ``{symbol: Close series}`` on common dates.

    Empty / ``None`` series are dropped.  The result is a DataFrame whose
    columns are the symbols and whose index is the set of dates for which
    **every** retained symbol has a value (rows with any NaN removed).  Returns
    an empty DataFrame when nothing usable remains (never raises).
    """
    frame: Dict[str, pd.Series] = {}
    for sym, series in series_by_symbol.items():
        if series is None:
            continue
        try:
            if len(series) == 0:
                continue
        except TypeError:
            continue
        frame[sym] = series

    if not frame:
        return pd.DataFrame()

    df = pd.DataFrame(frame)
    # pd.DataFrame aligns on the union of indices (outer join); dropping any-NaN
    # rows collapses that to the inner join on common dates.
    df = df.dropna(how="any")
    return df


def _signal_label(
    last_position: float,
    last_z: float,
    last_rolling_p: float,
    *,
    entry: float = ENTRY_THRESHOLD,
    stop: float = STOP_LOSS_THRESHOLD,
    adf_exit: float = ADF_EXIT_THRESHOLD,
) -> str:
    """Translate the last position / z-score / rolling-ADF into a human label.

    ADVISORY DISPLAY ONLY — this returns a *string* describing the current
    pairs state, it never places a trade.  ``position`` follows the engine
    convention: ``+1`` = long spread (long Y / short X), ``-1`` = short spread
    (short Y / long X), ``0`` = flat.
    """
    if pd.isna(last_z) or pd.isna(last_rolling_p):
        return "No signal — insufficient history"

    # Cointegration broken → the pair is no longer tradeable this window.
    if last_rolling_p > adf_exit:
        return "No signal — not cointegrated (ADF p>0.10)"

    abs_z = abs(last_z)

    if last_position > 0:  # currently long the spread
        if abs_z > stop:
            return "STOP — |z|>4 (exit long spread)"
        if last_z >= EXIT_THRESHOLD:
            return "Exit — z-score crossed 0"
        return "Hold LONG spread (long Y / short X)"

    if last_position < 0:  # currently short the spread
        if abs_z > stop:
            return "STOP — |z|>4 (exit short spread)"
        if last_z <= EXIT_THRESHOLD:
            return "Exit — z-score crossed 0"
        return "Hold SHORT spread (short Y / long X)"

    # Flat — look for a fresh entry.
    if last_z <= -entry:
        return "Entry LONG spread (long Y / short X)"
    if last_z >= entry:
        return "Entry SHORT spread (short Y / long X)"
    return "Hold — flat, |z| below entry threshold"


# ---------------------------------------------------------------------------
# Provider / fetch helpers (Streamlit-adjacent but no widgets)
# ---------------------------------------------------------------------------
def _fetch_close(symbol: str, lookback_days: int = 252) -> pd.Series:
    """Fetch a single symbol's Close series via the market-data provider.

    Returns an empty Series (never raises) so one dead symbol can't abort a
    scan — the caller drops empties in :func:`_align_closes`.
    """
    from data.market_data import get_provider

    try:
        bars = get_provider().get_intraday_bars(symbol, lookback_days=lookback_days)
        if bars is None or bars.empty or "Close" not in bars.columns:
            return pd.Series(dtype=float, name=symbol)
        close = bars["Close"].copy()
        close.name = symbol
        return close
    except Exception as exc:  # noqa: BLE001 - dead-letter per symbol
        logger.debug("Pairs: Close fetch failed for %s: %s", symbol, exc)
        return pd.Series(dtype=float, name=symbol)


def _parse_symbol_list(text: str) -> List[str]:
    """Split a comma/space-separated free-text symbol list into uppercased tickers."""
    return [s.strip().upper() for s in text.replace(",", " ").split() if s.strip()]


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def render_pairs() -> None:
    """🔗 Pairs — advisory-only cointegration scan + pair-spread signal viewer."""
    help_widgets.explain("pairs")
    st.subheader("🔗 Pairs Trading (advisory)")
    st.info(
        "📋 **Advisory only — no orders are placed.** This tab *displays* "
        "statistical-arbitrage candidates and the current spread signal for a "
        "chosen pair. It never submits, modifies, or proposes a trade.",
        icon="📋",
    )

    snap = load_state_snapshot()
    try:
        default_universe = _active_symbols(snap)
    except Exception as exc:  # noqa: BLE001 - default list is best-effort
        logger.debug("Pairs: could not derive default universe: %s", exc)
        default_universe = ["SPY", "IVV", "VOO", "QQQ"]

    mode = st.radio(
        "Mode",
        options=["🔍 Scan for pairs", "🔬 Analyze a pair"],
        horizontal=True,
        key="pairs_mode",
    )

    if mode == "🔍 Scan for pairs":
        _render_scan_mode(default_universe)
    else:
        _render_analyze_mode(default_universe)


def _render_scan_mode(default_universe: List[str]) -> None:
    st.markdown("#### 🔍 Cointegration scan")
    st.caption(
        "Fetches each symbol's Close series, inner-joins on common dates, and "
        "runs the Engle-Granger cointegration test across every pair. Only pairs "
        f"with p < the threshold **and** a mean-reversion half-life between "
        f"{MIN_HALF_LIFE_DAYS} and {MAX_HALF_LIFE_DAYS} days are kept "
        "(a tradeable-speed filter)."
    )

    default_text = ", ".join(default_universe[:12])
    sym_text = st.text_input(
        "Symbols to scan (comma-separated)",
        value=default_text,
        key="pairs_scan_syms",
        help="At least two symbols. Fewer than ~60 overlapping bars per pair is "
             "skipped as insufficient history.",
    )
    symbols = sorted(set(_parse_symbol_list(sym_text)))

    c1, c2 = st.columns(2)
    p_threshold = c1.slider(
        "Max cointegration p-value", min_value=0.01, max_value=0.10,
        value=0.05, step=0.01,
        help="Lower = stricter. 0.05 is the conventional statistical threshold.",
    )
    max_pairs = int(c2.number_input(
        "Max pairs to return", min_value=1, max_value=50, value=20, step=1,
    ))

    if not st.button("Run scan", type="primary", key="pairs_scan_run"):
        return

    if len(symbols) < 2:
        st.warning("Enter at least two distinct symbols to scan for pairs.")
        return

    with st.spinner(f"Fetching {len(symbols)} Close series…"):
        series_by_symbol = {s: _fetch_close(s) for s in symbols}
    price_df = _align_closes(series_by_symbol)

    fetched = [s for s, ser in series_by_symbol.items() if not ser.empty]
    missing = [s for s in symbols if s not in fetched]
    if missing:
        st.caption(f"⚠️ No data for: {', '.join(missing)} (skipped).")

    if price_df.shape[1] < 2 or price_df.shape[0] < 60:
        st.info(
            "Insufficient aligned history to scan — need at least two symbols "
            "with ~60+ overlapping daily bars after the inner-join. Try a "
            "different / larger symbol list.",
            icon="ℹ️",
        )
        return

    st.caption(
        f"Aligned frame: **{price_df.shape[0]}** common dates × "
        f"**{price_df.shape[1]}** symbols."
    )

    try:
        from pairs.cointegration import find_cointegrated_pairs

        pairs = find_cointegrated_pairs(
            price_df, p_threshold=p_threshold, max_pairs=max_pairs
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"Cointegration scan failed: {exc}")
        return

    if not pairs:
        st.info(
            "No cointegrated pairs found for this universe at the selected "
            "p-value with a 5–60 day half-life. Statistical arbitrage "
            "candidates are genuinely rare — this is an honest empty result, "
            "not an error.",
            icon="ℹ️",
        )
        return

    rows = [
        {
            "Symbol Y": p.ticker1,
            "Symbol X": p.ticker2,
            "Coint. p-value": round(float(p.p_value), 4),
            "Half-life (days)": round(float(p.half_life), 1),
        }
        for p in pairs
    ]
    df = pd.DataFrame(rows)
    st.dataframe(df, width="stretch", hide_index=True)
    st.caption(
        "Sorted by cointegration p-value ascending (strongest evidence first). "
        "Copy a Symbol Y / Symbol X pair into the **Analyze a pair** mode to see "
        "its live spread signal. **Advisory only — no orders are placed.**"
    )


def _render_analyze_mode(default_universe: List[str]) -> None:
    st.markdown("#### 🔬 Analyze a pair")

    default_y = default_universe[0] if default_universe else "SPY"
    default_x = default_universe[1] if len(default_universe) > 1 else "IVV"

    c1, c2 = st.columns(2)
    sym_y = c1.text_input("Symbol Y (dependent leg)", value=default_y,
                          key="pairs_y").strip().upper()
    sym_x = c2.text_input("Symbol X (hedge leg)", value=default_x,
                          key="pairs_x").strip().upper()

    if not st.button("Analyze pair", type="primary", key="pairs_analyze_run"):
        return

    if not sym_y or not sym_x:
        st.warning("Enter both a Symbol Y and a Symbol X.")
        return
    if sym_y == sym_x:
        st.warning("Symbol Y and Symbol X must be different tickers.")
        return

    with st.spinner(f"Fetching {sym_y} and {sym_x} Close series…"):
        y_close = _fetch_close(sym_y)
        x_close = _fetch_close(sym_x)

    aligned = _align_closes({sym_y: y_close, sym_x: x_close})
    if aligned.shape[0] < 60 or aligned.shape[1] < 2:
        st.info(
            f"Insufficient aligned history for {sym_y}/{sym_x} — need ~60+ "
            "overlapping daily bars. One or both symbols may be unavailable "
            "from the provider.",
            icon="ℹ️",
        )
        return

    y_prices = aligned[sym_y]
    x_prices = aligned[sym_x]

    try:
        from signals.pairs_trading import generate_pairs_signals

        signals_df = generate_pairs_signals(y_prices, x_prices)
    except Exception as exc:  # noqa: BLE001
        st.error(
            f"Could not generate pairs signals for {sym_y}/{sym_x}: {exc}. "
            "This can happen for a degenerate or too-short pair."
        )
        return

    if signals_df is None or signals_df.empty:
        st.info("No signal rows produced for this pair.", icon="ℹ️")
        return

    last = signals_df.iloc[-1]

    # --- Half-life (guarded independently) ---
    half_life = float("nan")
    try:
        from pairs.cointegration import compute_half_life

        half_life = float(compute_half_life(signals_df["spread"]))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Pairs: half-life failed for %s/%s: %s", sym_y, sym_x, exc)

    # --- Current signal label (guarded) ---
    try:
        signal_label = _signal_label(
            float(last.get("position", float("nan"))),
            float(last.get("z_score", float("nan"))),
            float(last.get("rolling_p", float("nan"))),
        )
    except Exception as exc:  # noqa: BLE001
        signal_label = f"(signal unavailable: {exc})"

    def _fmt(val: float, digits: int = 2) -> str:
        try:
            f = float(val)
            return "—" if np.isnan(f) else f"{f:.{digits}f}"
        except (TypeError, ValueError):
            return "—"

    hl_ok = (not np.isnan(half_life)) and (MIN_HALF_LIFE_DAYS <= half_life <= MAX_HALF_LIFE_DAYS)

    st.markdown(f"##### {sym_y} vs {sym_x}")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Spread z-score", _fmt(last.get("z_score")),
              help="Current standardized spread. |z|>2 = entry, 0-cross = exit, |z|>4 = stop.")
    k2.metric("Hedge ratio β", _fmt(last.get("beta"), 4),
              help="Kalman-filtered dynamic hedge ratio: units of X shorted/longed per 1 unit of Y.")
    k3.metric("Rolling ADF p", _fmt(last.get("rolling_p"), 4),
              help="Rolling-window unit-root p-value. >0.10 means cointegration has broken.")
    k4.metric(
        "Half-life (days)",
        _fmt(half_life, 1),
        help="AR(1) mean-reversion speed. Tradeable pairs need 5–60 days.",
    )

    if hl_ok:
        st.success(f"**Current signal:** {signal_label}", icon="🔗")
    else:
        st.warning(
            f"**Current signal:** {signal_label}  \n"
            f"⚠️ Half-life ({_fmt(half_life, 1)} days) is outside the tradeable "
            f"{MIN_HALF_LIFE_DAYS}–{MAX_HALF_LIFE_DAYS}-day band — treat the pair "
            "as not currently actionable.",
            icon="⚠️",
        )

    st.caption(
        "**This is a displayed signal, not an order.** The platform never trades "
        "pairs automatically."
    )

    # --- z-score time series chart ---
    try:
        z = signals_df["z_score"].dropna()
        if not z.empty:
            st.line_chart(z, height=280)
            st.caption(
                "Spread z-score over time. Reference lines (not drawn): **±2** = "
                "entry bands, **0** = exit (mean), **±4** = stop-loss. "
                "A tradeable pair oscillates through 0 and rarely breaches ±4."
            )
        else:
            st.caption("z-score series is all-NaN (warm-up window not yet filled).")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not render z-score chart: {exc}")

    with st.expander("Pairs-trading rules (advisory)"):
        st.markdown(
            f"- **Entry:** open the spread when **|z| > {ENTRY_THRESHOLD:.0f}** "
            f"(z < −{ENTRY_THRESHOLD:.0f} → long Y / short X; "
            f"z > +{ENTRY_THRESHOLD:.0f} → short Y / long X).\n"
            f"- **Exit:** close when the z-score crosses **{EXIT_THRESHOLD:.0f}** "
            f"(mean reversion complete) **or** cointegration breaks "
            f"(rolling ADF p > {ADF_EXIT_THRESHOLD:.2f}).\n"
            f"- **Stop:** hard exit when **|z| > {STOP_LOSS_THRESHOLD:.0f}** "
            "(the spread is diverging, not reverting).\n"
            f"- **Tradeability:** the mean-reversion half-life must sit between "
            f"**{MIN_HALF_LIFE_DAYS} and {MAX_HALF_LIFE_DAYS} days** — too fast is "
            "noise, too slow ties up capital.\n\n"
            "_Every value above is informational. **No orders are placed from "
            "this tab.**_"
        )
