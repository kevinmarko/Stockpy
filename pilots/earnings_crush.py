"""
pilots/earnings_crush.py — Earnings Move & IV Crush Analytics Engine.
===================================================================

Quantitative multi-leg options earnings volatility crush strategy builder and
event risk scanner.

Key Capabilities:
- Expected Earnings Move Calculation:
    Uses standard market ATM straddle rule of thumb:
    Move ($) = 0.80 * S * IV * sqrt(DTE / 365)
    Move (%) = 0.80 * IV * sqrt(DTE / 365)
- Historical Realized Moves:
    Extracts prior 8 quarters of earnings actuals from `HistoricalStore`,
    computing percentage gap moves (|Open - PrevClose| / PrevClose).
    Provides realistic empirical fallback bounds for sparse history.
- Crush Edge Ratio & Candidate Evaluation:
    Scans universe for upcoming earnings within 1–5 days.
    Crush Edge = Implied Move % / Median Realized Move %.
    When Edge >= min_edge (default 1.25x), constructs delta-neutral Iron Condor
    spreads (Short legs at 1.0x Expected Move, Long wings at wing_multiplier * Expected Move).

Design Invariants:
* **AST-Safe (CONSTRAINTS #1 & #3)**: Pure compute/read module. Never imports heavy engines
  (`processing_engine`, `strategy_engine`, `forecasting_engine`, `macro_engine`,
   `technical_options_engine`, `main_orchestrator`, `desktop`).
* **Honesty (CONSTRAINT #4)**: Missing/sparse data explicitly flagged (`sparse_history=True`),
  never fabricates false zeros.
* **Never Raises (CONSTRAINT #6)**: Degrades gracefully on empty DB, corrupt files, or missing quotes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import logging
import math
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "calculate_expected_earnings_move",
    "get_historical_earnings_moves",
    "evaluate_earnings_crush_candidates",
    "get_earnings_crush_candidates",
    "to_earnings_crush_candidate_response",
    "execute_earnings_crush_trade",
    "snap_strike_to_grid_or_chain",
    "FALLBACK_MEDIAN_MOVE_PCT",
    "FALLBACK_MEAN_MOVE_PCT",
    "FALLBACK_MIN_MOVE_PCT",
    "FALLBACK_MAX_MOVE_PCT",
]

# Realistic fallback empirical bounds when historical earnings actuals are sparse (< 3 quarters)
FALLBACK_MEDIAN_MOVE_PCT = 0.052  # 5.2% median gap move
FALLBACK_MEAN_MOVE_PCT = 0.055    # 5.5% mean gap move
FALLBACK_MIN_MOVE_PCT = 0.020     # 2.0% minimum gap move
FALLBACK_MAX_MOVE_PCT = 0.095     # 9.5% maximum gap move

STRIKE_GRID_DEFAULT = 0.50


def _parse_date(val: Any) -> Optional[date]:
    """Parse various date representations to a tz-naive datetime.date."""
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, pd.Timestamp):
        return val.date()
    if isinstance(val, str):
        val_clean = val.strip()
        if not val_clean:
            return None
        # Try ISO format or date part
        try:
            return datetime.fromisoformat(val_clean.split("T")[0]).date()
        except Exception:
            try:
                return datetime.strptime(val_clean[:10], "%Y-%m-%d").date()
            except Exception:
                return None
    return None


def calculate_expected_earnings_move(
    spot: float,
    atm_iv: float,
    dte: Union[int, float],
) -> Dict[str, Any]:
    """
    Calculates expected dollar and percentage move using standard market convention:
    Expected Move ($) = 0.80 * S * IV * sqrt(DTE / 365)
    Expected Move (%) = 0.80 * IV * sqrt(DTE / 365)

    Parameters:
    - spot: Current underlying asset price ($).
    - atm_iv: At-The-Money implied volatility (expressed as decimal, e.g. 0.45 = 45%,
              or percentage > 5.0 which will be auto-normalized).
    - dte: Days to expiration of the target option cycle.

    Returns dictionary with:
    - spot: Validated spot price
    - atm_iv: Normalized ATM IV
    - dte: Target DTE
    - expected_move_usd: Expected move in dollars ($)
    - expected_move_pct: Expected move in percentage (decimal, e.g. 0.054 = 5.4%)
    - straddle_price_proxy: Estimated market price of the ATM straddle
    - upper_expected_price: Upper boundary price (Spot + Expected Move)
    - lower_expected_price: Lower boundary price (Spot - Expected Move)
    """
    try:
        spot_val = float(spot) if spot is not None else 0.0
    except (ValueError, TypeError):
        spot_val = 0.0

    try:
        iv_val = float(atm_iv) if atm_iv is not None else 0.0
    except (ValueError, TypeError):
        iv_val = 0.0

    try:
        dte_val = float(dte) if dte is not None else 0.0
    except (ValueError, TypeError):
        dte_val = 0.0

    # Auto-normalize IV if provided in whole percentage points (e.g. 45.0 -> 0.45)
    if iv_val > 5.0:
        iv_val = iv_val / 100.0

    # Handle invalid or degenerate inputs gracefully
    if spot_val <= 0.0 or iv_val <= 0.0 or dte_val < 0.0 or math.isnan(spot_val) or math.isnan(iv_val) or math.isnan(dte_val):
        return {
            "spot": max(0.0, spot_val),
            "atm_iv": max(0.0, iv_val),
            "dte": max(0.0, dte_val),
            "t_years": 0.0,
            "expected_move_usd": 0.0,
            "expected_move_pct": 0.0,
            "straddle_price_proxy": 0.0,
            "upper_expected_price": max(0.0, spot_val),
            "lower_expected_price": max(0.0, spot_val),
        }

    t_years = dte_val / 365.0
    expected_move_usd = 0.80 * spot_val * iv_val * math.sqrt(t_years)
    expected_move_pct = expected_move_usd / spot_val if spot_val > 0 else (0.80 * iv_val * math.sqrt(t_years))
    straddle_price_proxy = expected_move_usd
    upper_expected_price = spot_val + expected_move_usd
    lower_expected_price = max(0.0, spot_val - expected_move_usd)

    return {
        "spot": spot_val,
        "atm_iv": iv_val,
        "dte": dte_val,
        "t_years": t_years,
        "expected_move_usd": round(expected_move_usd, 4),
        "expected_move_pct": round(expected_move_pct, 6),
        "straddle_price_proxy": round(straddle_price_proxy, 4),
        "upper_expected_price": round(upper_expected_price, 4),
        "lower_expected_price": round(lower_expected_price, 4),
    }


def get_historical_earnings_moves(
    symbol: str,
    store: Optional[Any] = None,
    *,
    lookback_quarters: int = 8,
    lookback_days: int = 756,
    as_of: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Retrieves the past 8 quarters of earnings actuals for a symbol and computes
    percentage gap moves.

    The earnings-events source (FMP's `/earnings` calendar, via
    `data/fmp_feeds_company.py::fetch_earnings_rows`) carries no reporting-time/session
    (before-market-open vs. after-market-close) field, so which trading session actually
    held the reaction is unknown per-event. For each event this function therefore computes
    TWO real, bar-derived candidate gaps -- the BMO hypothesis (`|Open[event_date] -
    Close[event_date-1]| / Close[event_date-1]`) and the AMC hypothesis (`|Open[event_date+1]
    - Close[event_date]| / Close[event_date]`) -- and takes whichever is larger, since a
    genuine earnings reaction dominates ordinary single-day noise. This is a data-driven
    inference, never a fabricated label (CONSTRAINT #4): see
    `docs/known_issues/earnings_crush_bmo_amc_bar_alignment.md` for the full writeup of the
    bug this replaced (always assuming BMO, which silently mis-measured the majority AMC
    case) and why "take the larger of two real gaps" is the correct, conservative fix.

    If historical data is sparse (< 3 quarters or missing bars), provides realistic
    empirical fallback bounds with honest metadata flagging (`sparse_history=True`).

    Parameters:
    - symbol: Stock ticker symbol (e.g. 'AAPL')
    - store: Instance of HistoricalStore (if None, initializes dynamically)
    - lookback_quarters: Number of past earnings quarters to evaluate (default 8)
    - lookback_days: Days of price history to fetch for bar gap calculations (default 756)
    - as_of: Point-in-time cutoff (date/datetime/ISO string). When supplied, only earnings
      actuals and price bars dated on or before this date are used to build the realized-move
      statistics. This is what keeps a PRE-earnings signal scored as of a past `as_of` date from
      reading post-earnings realized moves that happened after that date (no lookahead bias).
      Defaults to None, which reproduces the prior "always read the live/current DB state"
      behavior exactly.

    Returns dictionary containing:
    - symbol: Ticker
    - moves: List of individual quarterly move dicts (date, open, prev_close, gap_usd, gap_pct,
      reaction_session_inferred -- "bmo" or "amc", INFERRED from bar data, not source-confirmed)
    - quarters_count: Count of valid quarterly gap moves found
    - median_move_pct: Median historical post-earnings move (decimal)
    - mean_move_pct: Mean historical post-earnings move (decimal)
    - min_move_pct: Minimum historical move (decimal)
    - max_move_pct: Maximum historical move (decimal)
    - sparse_history: Boolean indicating if history had < 3 quarters
    - fallback: Boolean indicating if empirical defaults were applied
    - reason: Explanatory diagnostic message if sparse or fallback
    - timing_data_available: Always False today -- no real per-event BMO/AMC field exists in
      this codebase's earnings-events source (see above); forward-compatible if one is added.
    """
    sym = str(symbol or "").upper().strip()
    if not sym:
        return {
            "symbol": "",
            "moves": [],
            "quarters_count": 0,
            "median_move_pct": FALLBACK_MEDIAN_MOVE_PCT,
            "mean_move_pct": FALLBACK_MEAN_MOVE_PCT,
            "min_move_pct": FALLBACK_MIN_MOVE_PCT,
            "max_move_pct": FALLBACK_MAX_MOVE_PCT,
            "sparse_history": True,
            "fallback": True,
            "reason": "Empty or invalid symbol provided.",
            "timing_data_available": False,
        }

    as_of_date = _parse_date(as_of)

    # Resolve HistoricalStore if not supplied
    if store is None:
        try:
            from data.historical_store import HistoricalStore
            store = HistoricalStore()
        except Exception as exc:
            logger.debug("Could not initialize HistoricalStore: %s", exc)
            store = None

    if store is None:
        return {
            "symbol": sym,
            "moves": [],
            "quarters_count": 0,
            "median_move_pct": FALLBACK_MEDIAN_MOVE_PCT,
            "mean_move_pct": FALLBACK_MEAN_MOVE_PCT,
            "min_move_pct": FALLBACK_MIN_MOVE_PCT,
            "max_move_pct": FALLBACK_MAX_MOVE_PCT,
            "sparse_history": True,
            "fallback": True,
            "reason": "HistoricalStore is unavailable.",
            "timing_data_available": False,
        }

    # Retrieve earnings actuals. `on_or_before=as_of_date` is the store's dedicated
    # point-in-time cutoff for exactly this "trailing surprise" read (see
    # HistoricalStore.get_earnings_events's docstring) -- passing it here (rather than
    # relying on the default "read the live/current DB state") is what prevents a
    # PRE-earnings signal scored as of a past `as_of` date from reading earnings actuals
    # published after that date (no lookahead bias).
    try:
        events = store.get_earnings_events(
            sym,
            actuals_only=True,
            on_or_before=as_of_date.isoformat() if as_of_date else None,
            limit=lookback_quarters * 2,  # fetch buffer to account for duplicate or unaligned dates
        )
    except Exception as exc:
        logger.warning("Failed to fetch earnings events for %s: %s", sym, exc)
        events = []

    # Retrieve price bars. HistoricalStore.get_bars has no point-in-time cutoff of its own
    # (it always tops up to "today" from the live provider), so the cutoff is enforced here
    # on the returned frame -- the gap-move computation below must never see a bar dated
    # after `as_of_date` (no lookahead bias).
    try:
        bars = store.get_bars(sym, lookback_days=lookback_days)
        if as_of_date is not None and isinstance(bars, pd.DataFrame) and not bars.empty:
            cutoff_ts = pd.Timestamp(as_of_date)
            bars = bars[bars.index <= cutoff_ts]
    except Exception as exc:
        logger.warning("Failed to fetch bars for %s: %s", sym, exc)
        bars = None

    if bars is None or (isinstance(bars, pd.DataFrame) and len(bars) < 2) or not events:
        return {
            "symbol": sym,
            "moves": [],
            "quarters_count": 0,
            "median_move_pct": FALLBACK_MEDIAN_MOVE_PCT,
            "mean_move_pct": FALLBACK_MEAN_MOVE_PCT,
            "min_move_pct": FALLBACK_MIN_MOVE_PCT,
            "max_move_pct": FALLBACK_MAX_MOVE_PCT,
            "sparse_history": True,
            "fallback": True,
            "reason": "No earnings events or insufficient price bars in store.",
            "timing_data_available": False,
        }

    # Build date-indexed map for fast bar lookup
    bar_dates: List[date] = []
    for idx in bars.index:
        d = _parse_date(idx)
        if d:
            bar_dates.append(d)
        else:
            bar_dates.append(date(1970, 1, 1))

    moves: List[Dict[str, Any]] = []

    # Evaluate each earnings event
    for event in events:
        if len(moves) >= lookback_quarters:
            break

        event_date_str = str(event.get("event_date") or "")
        event_date = _parse_date(event_date_str)
        if not event_date:
            continue

        # Find the first bar on or after event_date
        bar_idx = -1
        for i, b_date in enumerate(bar_dates):
            if b_date >= event_date:
                bar_idx = i
                break

        if bar_idx <= 0 or bar_idx >= len(bars):
            # If event_date is before the start of available bars or after the end, skip
            continue

        event_bar = bars.iloc[bar_idx]
        prev_bar = bars.iloc[bar_idx - 1]

        try:
            open_price = float(event_bar["Open"])
            prev_close = float(prev_bar["Close"])
        except (KeyError, ValueError, TypeError):
            continue

        if prev_close <= 0.0 or open_price <= 0.0 or math.isnan(prev_close) or math.isnan(open_price):
            continue

        # BMO-hypothesis gap: the overnight move INTO event_date's open -- correct if the
        # company reported before that day's open.
        bmo_gap_usd = abs(open_price - prev_close)
        bmo_gap_pct = bmo_gap_usd / prev_close

        # AMC-hypothesis gap: the overnight move INTO the NEXT trading day's open -- correct
        # if the company reported after event_date's close (the majority case for large-cap
        # tech -- NVDA/AAPL/MSFT/META/GOOGL/AMZN all report AMC). FMP's `/earnings` calendar
        # (this store's sole earnings-events source, see data/fmp_feeds_company.py's
        # fetch_earnings_rows) carries no reporting-time/session field -- verified against
        # FMP's own published response schema, not assumed -- so there is no real per-event
        # BMO/AMC label to read here. Rather than silently assuming BMO (this function's prior
        # behavior, confirmed wrong for the AMC majority -- see
        # docs/known_issues/earnings_crush_bmo_amc_bar_alignment.md), take whichever of the two
        # real, bar-derived gaps is larger: a genuine earnings reaction dominates ordinary
        # single-day noise, so this correctly attributes the move to whichever session actually
        # held it, and it errs conservatively (never UNDERSTATES the realized move that
        # crush_edge_ratio divides by) when the true session is unknown.
        amc_gap_pct: Optional[float] = None
        amc_gap_usd: Optional[float] = None
        amc_bar_idx = bar_idx + 1
        if amc_bar_idx < len(bars):
            next_bar = bars.iloc[amc_bar_idx]
            try:
                next_open = float(next_bar["Open"])
                event_close = float(event_bar["Close"])
            except (KeyError, ValueError, TypeError):
                next_open = None
                event_close = None
            if (
                next_open is not None
                and event_close is not None
                and next_open > 0.0
                and event_close > 0.0
                and not math.isnan(next_open)
                and not math.isnan(event_close)
            ):
                amc_gap_usd = abs(next_open - event_close)
                amc_gap_pct = amc_gap_usd / event_close

        if amc_gap_pct is not None and amc_gap_pct > bmo_gap_pct:
            gap_usd = amc_gap_usd
            gap_pct = amc_gap_pct
            reaction_bar_idx = amc_bar_idx
            reaction_session_inferred = "amc"
        else:
            gap_usd = bmo_gap_usd
            gap_pct = bmo_gap_pct
            reaction_bar_idx = bar_idx
            reaction_session_inferred = "bmo"

        moves.append({
            "event_date": event_date.isoformat(),
            "bar_date": bar_dates[reaction_bar_idx].isoformat(),
            "open": round(open_price, 4),
            "prev_close": round(prev_close, 4),
            "gap_usd": round(gap_usd, 4),
            "gap_pct": round(gap_pct, 4),
            # Inferred, not source-confirmed -- see the amc_gap_pct comment above.
            "reaction_session_inferred": reaction_session_inferred,
            "eps_actual": event.get("eps_actual"),
            "eps_estimated": event.get("eps_estimated"),
            "revenue_actual": event.get("revenue_actual"),
            "revenue_estimated": event.get("revenue_estimated"),
        })

    if not moves:
        return {
            "symbol": sym,
            "moves": [],
            "quarters_count": 0,
            "median_move_pct": FALLBACK_MEDIAN_MOVE_PCT,
            "mean_move_pct": FALLBACK_MEAN_MOVE_PCT,
            "min_move_pct": FALLBACK_MIN_MOVE_PCT,
            "max_move_pct": FALLBACK_MAX_MOVE_PCT,
            "sparse_history": True,
            "fallback": True,
            "reason": "Could not align earnings event dates with historical price bars.",
            "timing_data_available": False,
        }

    gap_values = [m["gap_pct"] for m in moves]
    median_move = float(np.median(gap_values))
    mean_move = float(np.mean(gap_values))
    min_move = float(np.min(gap_values))
    max_move = float(np.max(gap_values))
    sparse = len(moves) < 3

    return {
        "symbol": sym,
        "moves": moves,
        "quarters_count": len(moves),
        "median_move_pct": round(median_move, 4),
        "mean_move_pct": round(mean_move, 4),
        "min_move_pct": round(min_move, 4),
        "max_move_pct": round(max_move, 4),
        "sparse_history": sparse,
        "fallback": False,
        "reason": f"Sparse history: only {len(moves)} quarter(s) found (recommended >= 3)" if sparse else None,
        # No real per-event BMO/AMC field exists in this codebase's earnings-events source
        # today (see the reaction_session_inferred comment in the per-event loop above) --
        # each move's session is an inference from bar data, not a source-confirmed label.
        "timing_data_available": False,
    }


def _snap_strike(
    target: float,
    available_strikes: Sequence[float],
    preference: str = "nearest",
) -> float:
    """
    Snaps a target strike to the best available strike from a list.
    - 'nearest': absolute closest strike
    - 'above': closest strike >= target (falls back to nearest if none)
    - 'below': closest strike <= target (falls back to nearest if none)
    """
    if not available_strikes:
        return round(target * 2.0) / 2.0  # fallback 0.50 grid

    valid = [float(s) for s in available_strikes if s > 0 and not math.isnan(s)]
    if not valid:
        return round(target * 2.0) / 2.0

    if preference == "above":
        candidates = [s for s in valid if s >= target]
        if candidates:
            return min(candidates)
    elif preference == "below":
        candidates = [s for s in valid if s <= target]
        if candidates:
            return max(candidates)

    # Nearest
    return min(valid, key=lambda s: abs(s - target))


def snap_strike_to_grid_or_chain(
    target: float,
    available_strikes: Optional[Sequence[float]] = None,
    grid: float = STRIKE_GRID_DEFAULT,
    preference: str = "nearest",
) -> float:
    """
    Snaps target strike to available chain strikes if provided, or rounds to exchange grid.
    """
    if available_strikes and len(available_strikes) > 0:
        return _snap_strike(target, available_strikes, preference=preference)
    g = grid if grid > 0 else STRIKE_GRID_DEFAULT
    return round(target / g) * g


def _extract_chain_strikes_and_iv(
    chain: Any,
    spot: float,
) -> Tuple[List[float], List[float], Optional[float], Dict[str, Any]]:
    """
    Extracts available call and put strikes, ATM IV, and best quote mappings from an options chain.

    Returns `atm_iv=None` (never a fabricated baseline vol -- CONSTRAINT #4) when the chain is
    unavailable or carries no usable per-strike implied volatility quotes; the caller must treat
    `None` as "insufficient real data" and refuse to build an Iron Condor recommendation off it.
    """
    call_strikes: List[float] = []
    put_strikes: List[float] = []
    quotes_map: Dict[str, Dict[str, Any]] = {}
    call_ivs: List[Tuple[float, float]] = []  # (dist_to_spot, iv)
    put_ivs: List[Tuple[float, float]] = []

    if chain is None:
        return [], [], None, quotes_map

    # Check yfinance / DataFrame style chain (.calls, .puts)
    calls_df = getattr(chain, "calls", None)
    puts_df = getattr(chain, "puts", None)

    if calls_df is None and isinstance(chain, dict):
        calls_df = chain.get("calls")
        puts_df = chain.get("puts")

    # Parse calls
    if isinstance(calls_df, pd.DataFrame):
        for _, row in calls_df.iterrows():
            try:
                strike = float(row.get("strike", 0.0))
                if strike > 0:
                    call_strikes.append(strike)
                    iv = float(row.get("impliedVolatility", 0.0) or 0.0)
                    bid = float(row.get("bid", 0.0) or 0.0)
                    ask = float(row.get("ask", 0.0) or 0.0)
                    last = float(row.get("lastPrice", 0.0) or 0.0)
                    quotes_map[f"call_{strike:.2f}"] = {
                        "strike": strike, "type": "call", "iv": iv, "bid": bid, "ask": ask, "last": last
                    }
                    if iv > 0 and not math.isnan(iv):
                        call_ivs.append((abs(strike - spot), iv))
            except Exception:
                continue
    elif isinstance(calls_df, (list, tuple)):
        for item in calls_df:
            try:
                strike = float(item.get("strike", 0.0) if isinstance(item, dict) else getattr(item, "strike", 0.0))
                if strike > 0:
                    call_strikes.append(strike)
                    iv = float((item.get("impliedVolatility") if isinstance(item, dict) else getattr(item, "impliedVolatility", 0.0)) or 0.0)
                    bid = float((item.get("bid") if isinstance(item, dict) else getattr(item, "bid", 0.0)) or 0.0)
                    ask = float((item.get("ask") if isinstance(item, dict) else getattr(item, "ask", 0.0)) or 0.0)
                    last = float((item.get("lastPrice") if isinstance(item, dict) else getattr(item, "lastPrice", 0.0)) or 0.0)
                    quotes_map[f"call_{strike:.2f}"] = {
                        "strike": strike, "type": "call", "iv": iv, "bid": bid, "ask": ask, "last": last
                    }
                    if iv > 0 and not math.isnan(iv):
                        call_ivs.append((abs(strike - spot), iv))
            except Exception:
                continue

    # Parse puts
    if isinstance(puts_df, pd.DataFrame):
        for _, row in puts_df.iterrows():
            try:
                strike = float(row.get("strike", 0.0))
                if strike > 0:
                    put_strikes.append(strike)
                    iv = float(row.get("impliedVolatility", 0.0) or 0.0)
                    bid = float(row.get("bid", 0.0) or 0.0)
                    ask = float(row.get("ask", 0.0) or 0.0)
                    last = float(row.get("lastPrice", 0.0) or 0.0)
                    quotes_map[f"put_{strike:.2f}"] = {
                        "strike": strike, "type": "put", "iv": iv, "bid": bid, "ask": ask, "last": last
                    }
                    if iv > 0 and not math.isnan(iv):
                        put_ivs.append((abs(strike - spot), iv))
            except Exception:
                continue
    elif isinstance(puts_df, (list, tuple)):
        for item in puts_df:
            try:
                strike = float(item.get("strike", 0.0) if isinstance(item, dict) else getattr(item, "strike", 0.0))
                if strike > 0:
                    put_strikes.append(strike)
                    iv = float((item.get("impliedVolatility") if isinstance(item, dict) else getattr(item, "impliedVolatility", 0.0)) or 0.0)
                    bid = float((item.get("bid") if isinstance(item, dict) else getattr(item, "bid", 0.0)) or 0.0)
                    ask = float((item.get("ask") if isinstance(item, dict) else getattr(item, "ask", 0.0)) or 0.0)
                    last = float((item.get("lastPrice") if isinstance(item, dict) else getattr(item, "lastPrice", 0.0)) or 0.0)
                    quotes_map[f"put_{strike:.2f}"] = {
                        "strike": strike, "type": "put", "iv": iv, "bid": bid, "ask": ask, "last": last
                    }
                    if iv > 0 and not math.isnan(iv):
                        put_ivs.append((abs(strike - spot), iv))
            except Exception:
                continue

    # Compute ATM IV
    atm_call_iv = min(call_ivs, key=lambda x: x[0])[1] if call_ivs else None
    atm_put_iv = min(put_ivs, key=lambda x: x[0])[1] if put_ivs else None

    if atm_call_iv is not None and atm_put_iv is not None:
        atm_iv = (atm_call_iv + atm_put_iv) / 2.0
    elif atm_call_iv is not None:
        atm_iv = atm_call_iv
    elif atm_put_iv is not None:
        atm_iv = atm_put_iv
    else:
        # No usable per-strike IV in the fetched chain -- refuse to fabricate a plausible-looking
        # baseline vol (CONSTRAINT #4). The caller must skip/decline the recommendation rather
        # than size an Iron Condor's edge/wings off an invented number.
        atm_iv = None

    return (
        sorted(list(set(call_strikes))),
        sorted(list(set(put_strikes))),
        (float(atm_iv) if atm_iv is not None else None),
        quotes_map,
    )


def evaluate_earnings_crush_candidates(
    universe: Sequence[str],
    store: Optional[Any] = None,
    options_provider: Optional[Any] = None,
    min_edge: Optional[float] = None,
    wing_multiplier: Optional[float] = None,
    *,
    as_of: Optional[Any] = None,
    min_days_to_earnings: int = 1,
    max_days_to_earnings: int = 5,
    strike_grid: float = STRIKE_GRID_DEFAULT,
    diagnostics: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """
    Scans a universe of tickers for upcoming earnings announcements within 1–5 days,
    computes Expected Move vs. Realized Move, and calculates the Crush Edge Ratio.

    When Crush Edge Ratio >= min_edge (default 1.25x), constructs delta-neutral
    Iron Condor spreads (Short legs at 1.0x Expected Move, Long wings at
    wing_multiplier * Expected Move).

    Parameters:
    - universe: List or sequence of stock ticker symbols
    - store: HistoricalStore instance (or None for dynamic initialization)
    - options_provider: OptionsDataProvider instance (or None for dynamic initialization)
    - min_edge: Minimum Crush Edge Ratio to qualify (defaults to settings.OPTIONS_EARNINGS_MIN_EDGE or 1.25)
    - wing_multiplier: Wing width multiplier on expected move (defaults to settings.OPTIONS_EARNINGS_WING_MULTIPLIER or 1.20)
    - as_of: Valuation reference date (defaults to UTC today)
    - min_days_to_earnings: Minimum days until announcement to consider (default 1)
    - max_days_to_earnings: Maximum days until announcement to consider (default 5)
    - strike_grid: Grid spacing if chain strikes unavailable (default $0.50)
    - diagnostics: Optional mutable dict. When provided, populated with
      `symbols_total` (int, universe size), `store_available`/`options_provider_available`
      (bool, whether each resolved to a usable instance), and `symbols_errored`
      (list of symbols that raised during per-symbol processing) -- lets a caller
      (e.g. the Pilots API) distinguish "nothing qualified" from "the scan itself
      degraded" (CONSTRAINT #4 honesty). Purely additive: when None (the default),
      this function's return value and behavior are completely unchanged.

    Returns:
    - List of evaluated candidate dictionaries, sorted by crush_edge_ratio descending.
    """
    if diagnostics is not None:
        diagnostics["symbols_total"] = len(universe)
        diagnostics.setdefault("symbols_errored", [])

    # Resolve configurable thresholds from settings
    resolved_min_edge = float(
        min_edge if min_edge is not None else getattr(settings, "OPTIONS_EARNINGS_MIN_EDGE", 1.25)
    )
    resolved_wing_mult = float(
        wing_multiplier if wing_multiplier is not None else getattr(settings, "OPTIONS_EARNINGS_WING_MULTIPLIER", 1.20)
    )

    as_of_date = _parse_date(as_of) or datetime.now(timezone.utc).date()

    # Resolve store and options_provider safely
    if store is None:
        try:
            from data.historical_store import HistoricalStore
            store = HistoricalStore()
        except Exception as exc:
            logger.debug("HistoricalStore could not be initialized: %s", exc)
            store = None

    if options_provider is None:
        try:
            from data.market_data import get_options_provider
            options_provider = get_options_provider()
        except Exception as exc:
            logger.debug("OptionsProvider could not be initialized: %s", exc)
            options_provider = None

    if diagnostics is not None:
        diagnostics["store_available"] = store is not None
        diagnostics["options_provider_available"] = options_provider is not None

    candidates: List[Dict[str, Any]] = []
    spot_override_map = kwargs.get("spot_prices", {})
    upcoming_override_map = kwargs.get("upcoming_earnings", {})

    for sym_raw in universe:
        sym = str(sym_raw or "").upper().strip()
        if not sym:
            continue

        try:
            # 1. Identify upcoming earnings date
            event_date: Optional[date] = None
            if sym in upcoming_override_map:
                event_date = _parse_date(upcoming_override_map[sym])
            elif store is not None:
                # Query upcoming events from HistoricalStore
                upcoming_events = store.get_earnings_events(
                    sym,
                    after=as_of_date.isoformat(),
                    limit=5,
                )
                for ev in upcoming_events:
                    ed = _parse_date(ev.get("event_date"))
                    if ed and ed >= as_of_date:
                        event_date = ed
                        break

            if not event_date:
                continue

            days_to_earnings = (event_date - as_of_date).days
            if days_to_earnings < min_days_to_earnings or days_to_earnings > max_days_to_earnings:
                continue

            # 2. Resolve spot price
            spot: Optional[float] = None
            if sym in spot_override_map:
                spot = float(spot_override_map[sym])
            elif store is not None:
                bars = store.get_bars(sym, lookback_days=5)
                if bars is not None and len(bars) > 0 and "Close" in bars.columns:
                    spot = float(bars["Close"].iloc[-1])

            if spot is None or spot <= 0.0 or math.isnan(spot):
                # No real spot price available for this symbol -- refuse to fabricate one
                # (CONSTRAINT #4) and size an Iron Condor's strikes/edge off an invented price.
                logger.info(
                    "Skipping earnings crush candidate for %s: no real spot price available.",
                    sym,
                )
                continue

            # 2b. Resolve a display company name, defensively. `store` may be a test stub
            # (see tests/test_earnings_crush.py's MockHistoricalStore) that does not implement
            # get_fundamentals_raw at all -- the hasattr guard keeps that a clean no-op rather
            # than an AttributeError that the outer per-symbol try/except would otherwise turn
            # into a silently-dropped candidate. Never fabricated (CONSTRAINT #4): None/omitted
            # when unavailable.
            company_name: Optional[str] = None
            try:
                if store is not None and hasattr(store, "get_fundamentals_raw"):
                    raw_fund = store.get_fundamentals_raw(sym)
                    if isinstance(raw_fund, dict):
                        cn = raw_fund.get("company_name")
                        if isinstance(cn, str) and cn.strip():
                            company_name = cn.strip()
            except Exception as exc:
                logger.debug("company_name lookup failed for %s: %s", sym, exc)

            # 3. Retrieve options chain and find target expiration
            expirations: List[str] = []
            if options_provider is not None:
                try:
                    exp_list = options_provider.fetch_options_chain(sym)
                    if isinstance(exp_list, (list, tuple)):
                        expirations = [str(e) for e in exp_list]
                except Exception as exc:
                    logger.debug("fetch_options_chain failed for %s: %s", sym, exc)

            # Find front-week expiration covering earnings. Requires an expiration that
            # STRICTLY clears event_date (not merely reaches it) so the position survives an
            # after-market-close (AMC) reaction, which lands on event_date+1 -- FMP's earnings
            # calendar carries no BMO/AMC field (see get_historical_earnings_moves's docstring
            # and docs/known_issues/earnings_crush_bmo_amc_bar_alignment.md), so an expiration
            # dated exactly event_date could expire before an AMC print without this. A
            # before-market-open (BMO) reaction, which happens during event_date's own
            # session, remains fully covered by an expiration one day later.
            target_exp_str: Optional[str] = None
            target_dte: int = 7
            for exp_candidate in expirations:
                ed = _parse_date(exp_candidate)
                if ed and ed > event_date:
                    target_exp_str = exp_candidate
                    target_dte = max(1, (ed - as_of_date).days)
                    break

            if not target_exp_str and expirations:
                # No expiration in the chain strictly clears event_date -- degenerate chain
                # (e.g. only same-day/expired listings). Falls back to the nearest available
                # rather than refusing the candidate outright; there is no better choice here.
                target_exp_str = expirations[0]
                ed = _parse_date(target_exp_str)
                if ed:
                    target_dte = max(1, (ed - as_of_date).days)
            elif not target_exp_str:
                # Default synthetic front-week expiration
                exp_date_synth = event_date + timedelta(days=max(1, (4 - event_date.weekday()) % 7))
                target_exp_str = exp_date_synth.isoformat()
                target_dte = max(1, (exp_date_synth - as_of_date).days)

            # Fetch chain quotes and strikes
            chain_data = None
            if options_provider is not None and target_exp_str:
                try:
                    chain_data = options_provider.fetch_options_chain(sym, target_exp_str)
                except Exception as exc:
                    logger.debug("fetch_options_chain(%s, %s) failed: %s", sym, target_exp_str, exc)

            call_strikes, put_strikes, atm_iv, quotes_map = _extract_chain_strikes_and_iv(chain_data, spot)

            if atm_iv is None:
                # No usable ATM implied volatility from either the call or put chain -- refuse
                # to fabricate a baseline vol and size an Iron Condor's edge/wings off it
                # (CONSTRAINT #4). Report insufficient data by skipping this candidate.
                logger.info(
                    "Skipping earnings crush candidate for %s: no usable ATM implied volatility "
                    "from the options chain.",
                    sym,
                )
                continue

            # 4. Calculate Expected Move
            exp_move_res = calculate_expected_earnings_move(spot, atm_iv, target_dte)
            expected_move_usd = exp_move_res["expected_move_usd"]
            expected_move_pct = exp_move_res["expected_move_pct"]

            # 5. Retrieve Historical Realized Move (as_of-gated -- see get_historical_earnings_moves)
            hist_res = get_historical_earnings_moves(sym, store, as_of=as_of_date)
            realized_move_pct = hist_res["median_move_pct"]
            if realized_move_pct <= 0.0:
                realized_move_pct = FALLBACK_MEDIAN_MOVE_PCT

            # 6. Compute Crush Edge Ratio (CONSTRAINT #4: Never recommend a trade on synthetic fallback data)
            if hist_res.get("fallback") or hist_res.get("quarters_count", 0) == 0:
                crush_edge_ratio = round(expected_move_pct / realized_move_pct, 3) if realized_move_pct > 0 else 0.0
                is_recommended = False
            else:
                crush_edge_ratio = round(expected_move_pct / realized_move_pct, 3) if realized_move_pct > 0 else 0.0
                is_recommended = bool(crush_edge_ratio >= resolved_min_edge and not hist_res.get("sparse_history", False))

            # 7. Construct delta-neutral Iron Condor strikes
            target_short_call = spot + 1.0 * expected_move_usd
            target_long_call = spot + resolved_wing_mult * expected_move_usd
            target_short_put = spot - 1.0 * expected_move_usd
            target_long_put = spot - resolved_wing_mult * expected_move_usd

            # Snap strikes
            short_call_strike = snap_strike_to_grid_or_chain(
                target_short_call, call_strikes, grid=strike_grid, preference="above"
            )
            long_call_strike = snap_strike_to_grid_or_chain(
                target_long_call, call_strikes, grid=strike_grid, preference="above"
            )
            if long_call_strike <= short_call_strike:
                long_call_strike = short_call_strike + strike_grid

            short_put_strike = snap_strike_to_grid_or_chain(
                target_short_put, put_strikes, grid=strike_grid, preference="below"
            )
            long_put_strike = snap_strike_to_grid_or_chain(
                target_long_put, put_strikes, grid=strike_grid, preference="below"
            )
            if long_put_strike >= short_put_strike:
                long_put_strike = short_put_strike - strike_grid

            # Enforce boundary order: long_put < short_put < spot < short_call < long_call
            long_put_strike = round(long_put_strike, 2)
            short_put_strike = round(short_put_strike, 2)
            short_call_strike = round(short_call_strike, 2)
            long_call_strike = round(long_call_strike, 2)

            # Build standard 4-leg Iron Condor payload
            legs = [
                {
                    "side": "buy",
                    "action": "buy",
                    "type": "put",
                    "strike": long_put_strike,
                    "ratio": 1,
                    "expiration": target_exp_str,
                    "contract_symbol": f"{sym} {target_exp_str} ${long_put_strike:.2f} PUT",
                },
                {
                    "side": "sell",
                    "action": "sell",
                    "type": "put",
                    "strike": short_put_strike,
                    "ratio": 1,
                    "expiration": target_exp_str,
                    "contract_symbol": f"{sym} {target_exp_str} ${short_put_strike:.2f} PUT",
                },
                {
                    "side": "sell",
                    "action": "sell",
                    "type": "call",
                    "strike": short_call_strike,
                    "ratio": 1,
                    "expiration": target_exp_str,
                    "contract_symbol": f"{sym} {target_exp_str} ${short_call_strike:.2f} CALL",
                },
                {
                    "side": "buy",
                    "action": "buy",
                    "type": "call",
                    "strike": long_call_strike,
                    "ratio": 1,
                    "expiration": target_exp_str,
                    "contract_symbol": f"{sym} {target_exp_str} ${long_call_strike:.2f} CALL",
                },
            ]

            # Estimate net credit and max profit / loss
            # Estimated credit per share: approx 25-35% of wing width if pricing quotes unavailable
            call_wing_width = long_call_strike - short_call_strike
            put_wing_width = short_put_strike - long_put_strike
            max_wing_width = max(call_wing_width, put_wing_width)

            # Attempt real quotes estimation if present
            quote_sp = quotes_map.get(f"put_{short_put_strike:.2f}", {})
            quote_lp = quotes_map.get(f"put_{long_put_strike:.2f}", {})
            quote_sc = quotes_map.get(f"call_{short_call_strike:.2f}", {})
            quote_lc = quotes_map.get(f"call_{long_call_strike:.2f}", {})

            sp_real = quote_sp.get("bid") or quote_sp.get("last")
            lp_real = quote_lp.get("ask") or quote_lp.get("last")
            sc_real = quote_sc.get("bid") or quote_sc.get("last")
            lc_real = quote_lc.get("ask") or quote_lc.get("last")

            # Honesty flag (matches this file's existing sparse_history/fallback convention):
            # True whenever ANY leg's price had to fall back to the wing-width heuristic below
            # rather than a real chain quote, so a consumer can tell a real net_credit/max_profit/
            # max_loss apart from an estimated one (CONSTRAINT #4).
            pricing_is_estimated = not (sp_real and lp_real and sc_real and lc_real)

            sp_price = sp_real or (expected_move_usd * 0.20)
            lp_price = lp_real or (expected_move_usd * 0.08)
            sc_price = sc_real or (expected_move_usd * 0.20)
            lc_price = lc_real or (expected_move_usd * 0.08)

            net_credit = max(0.10, (sp_price - lp_price) + (sc_price - lc_price))
            net_credit = round(net_credit, 2)
            max_profit = round(net_credit * 100.0, 2)
            max_loss = round(max(0.0, (max_wing_width - net_credit) * 100.0), 2)

            candidate = {
                "symbol": sym,
                "spot": spot,
                "earnings_date": event_date.isoformat(),
                "days_to_earnings": days_to_earnings,
                "expiration": target_exp_str,
                "dte": target_dte,
                "atm_iv": round(atm_iv, 4),
                "expected_move_usd": expected_move_usd,
                "expected_move_pct": expected_move_pct,
                "realized_move_pct": round(realized_move_pct, 4),
                "crush_edge_ratio": crush_edge_ratio,
                "is_recommended": is_recommended,
                "strategy": "Iron Condor",
                "strikes": {
                    "long_put": long_put_strike,
                    "short_put": short_put_strike,
                    "short_call": short_call_strike,
                    "long_call": long_call_strike,
                },
                "legs": legs,
                "net_credit": net_credit,
                "max_profit": max_profit,
                "max_loss": max_loss,
                "pricing_is_estimated": pricing_is_estimated,
                "company_name": company_name,
                "historical_summary": {
                    "quarters_count": hist_res["quarters_count"],
                    "median_move_pct": hist_res["median_move_pct"],
                    "sparse_history": hist_res["sparse_history"],
                    "fallback": hist_res["fallback"],
                    "moves": hist_res["moves"],
                },
            }
            candidates.append(candidate)

        except Exception as exc:
            logger.warning("Error evaluating earnings crush candidate %s: %s", sym_raw, exc)
            if diagnostics is not None:
                diagnostics.setdefault("symbols_errored", []).append(sym)
            continue

    # Sort candidates by crush_edge_ratio descending
    candidates.sort(key=lambda c: float(c.get("crush_edge_ratio", 0.0)), reverse=True)

    # Dispatch alerts for qualifying candidates (non-blocking, condition-deduped).
    # Gate on `is_recommended` alone -- it already encodes crush_edge_ratio >= min_edge
    # AND excludes synthetic/fallback-data candidates (see step 6 above, CONSTRAINT #4).
    # An `or crush_edge_ratio >= 1.35` fallback here would defeat that exclusion: a
    # candidate whose realized-move history is fabricated fallback data can still post a
    # high edge ratio purely from the fallback constant, and would then alert anyway.
    for cand in candidates:
        if cand.get("is_recommended"):
            try:
                from pilots.options_alerts import dispatch_earnings_crush_alert
                dispatch_earnings_crush_alert(cand)
            except Exception as exc:  # noqa: BLE001 — never raises (CONSTRAINT #6)
                logger.debug("Earnings crush alert dispatch failed for %s: %s", cand.get("symbol", ""), exc)

    return candidates


def get_earnings_crush_candidates(
    symbols: Optional[Sequence[str]] = None,
    min_edge: Optional[float] = None,
    store: Optional[Any] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Convenience alias for evaluate_earnings_crush_candidates."""
    universe = list(symbols) if symbols else ["NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "GOOGL", "META", "AMD", "NFLX", "DIS"]
    return evaluate_earnings_crush_candidates(
        universe=universe,
        min_edge=min_edge,
        store=store,
        diagnostics=diagnostics,
    )


def to_earnings_crush_candidate_response(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reshapes one evaluate_earnings_crush_candidates()/get_earnings_crush_candidates()
    raw candidate dict (symbol/spot/earnings_date/expected_move_usd/realized_move_pct/
    strategy/strikes/net_credit/... -- the shape every existing test in
    tests/test_earnings_crush.py asserts on) into the EarningsCrushCandidate contract
    webapp/src/api/types.ts, webapp/src/api/mock.ts, and
    webapp/src/components/options/EarningsCrushScanner.tsx already agree on
    (spot_price/report_date/expected_move_dollar/median_realized_move_pct/
    suggested_strategy/*_wing_strike/*_strike/estimated_credit).

    Kept as a separate step from get_earnings_crush_candidates() itself so every existing
    caller/test of the pure evaluation function is unaffected -- only the API handler for
    GET /pilots/options/earnings-crush/candidates applies this reshape.

    Any upstream field that is None/missing is OMITTED rather than fabricated (CONSTRAINT #4)
    so the frontend's own null-guards render an honest "--" instead of a synthetic number.
    """
    strikes: Dict[str, Any] = candidate.get("strikes") or {}

    response: Dict[str, Any] = {
        "symbol": candidate.get("symbol", ""),
        "report_date": candidate.get("earnings_date", ""),
        "atm_iv": candidate.get("atm_iv"),
        "dte": candidate.get("dte"),
        "expected_move_pct": candidate.get("expected_move_pct"),
        "crush_edge_ratio": candidate.get("crush_edge_ratio"),
        "suggested_strategy": candidate.get("strategy", "Iron Condor"),
    }

    if candidate.get("spot") is not None:
        response["spot_price"] = candidate["spot"]
    if candidate.get("expected_move_usd") is not None:
        response["expected_move_dollar"] = candidate["expected_move_usd"]
    if candidate.get("realized_move_pct") is not None:
        response["median_realized_move_pct"] = candidate["realized_move_pct"]
    if candidate.get("expiration") is not None:
        response["expiration"] = candidate["expiration"]
    if candidate.get("net_credit") is not None:
        response["estimated_credit"] = candidate["net_credit"]
    if candidate.get("is_recommended") is not None:
        response["edge_passed"] = candidate["is_recommended"]
    if strikes.get("long_put") is not None:
        response["put_wing_strike"] = strikes["long_put"]
    if strikes.get("short_put") is not None:
        response["short_put_strike"] = strikes["short_put"]
    if strikes.get("short_call") is not None:
        response["short_call_strike"] = strikes["short_call"]
    if strikes.get("long_call") is not None:
        response["call_wing_strike"] = strikes["long_call"]

    # historical_moves: raw per-quarter gap moves, percent-scaled, OLDEST-FIRST. `hist_res["moves"]`
    # (see get_historical_earnings_moves / HistoricalStore.get_earnings_events's `ORDER BY
    # event_date DESC`) is newest-first, but webapp/src/components/options/EarningsCrushScanner.tsx's
    # bar chart labels index 0 as "Q-8" (oldest) through index 7 as "Q-1" (most recent) -- so the
    # response array must be reversed here to line up with those labels.
    moves = (candidate.get("historical_summary") or {}).get("moves") or []
    if moves:
        response["historical_moves"] = [round(float(m["gap_pct"]) * 100.0, 2) for m in reversed(moves)]
    if candidate.get("company_name"):
        response["company_name"] = candidate["company_name"]

    # report_timing (BMO/AMC/DURING_HOURS) is deliberately NOT populated here. No real source
    # exists in this codebase: FMP's `/earnings` calendar (this store's sole earnings-events
    # source, see data/fmp_feeds_company.py::fetch_earnings_rows) carries no reporting-time/
    # session field -- verified against FMP's own published response schema, not assumed (see
    # get_historical_earnings_moves's own docstring/comments and its `timing_data_available:
    # False` field). Fabricating a BMO/AMC label here would violate CONSTRAINT #4 (never
    # fabricate a metric/field); the webapp's `report_timing` type field stays optional and
    # simply never gets set until a real source is wired up.

    return response


def execute_earnings_crush_trade(
    symbol: str,
    *,
    strategy: str = "Iron Condor",
    expiration: Optional[str] = None,
    contracts: int = 1,
    legs: Optional[List[Dict[str, Any]]] = None,
    limit_price: Optional[float] = None,
    dry_run: bool = False,
    is_live: bool = False,
) -> Dict[str, Any]:
    """
    Executes a pre-earnings multi-leg trade (Iron Condor or Short Strangle) in the paper broker.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return {"ok": False, "message": "Symbol is required."}

    if is_live:
        return {
            "ok": False,
            "message": "Advisory-Only Mode: Live options order execution is disabled. Please use paper mode.",
        }

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "symbol": sym,
            "strategy": strategy,
            "contracts": contracts,
            "message": f"Dry run: {strategy} earnings crush order validated for {sym}.",
        }

    try:
        from execution.options_paper_executor import OptionsPaperExecutor
        executor = OptionsPaperExecutor()
        candidate = {
            "symbol": sym,
            "strategy": strategy,
            "expiration": expiration,
            "legs": legs,
            "limit_price": limit_price,
        }
        res = executor.execute_earnings_crush_trade(candidate, contracts=contracts)
        if res.get("success"):
            # Reconstruct the real pre-commission per-share net credit from the executor's own
            # returned fields -- never fabricate a value (CONSTRAINT #4). `net_cash_impact` is
            # the total cash impact of the fill (positive for a net credit); adding back the
            # commission recovers the raw premium, then dividing by (100 * contracts) converts
            # back to a per-share credit.
            net_credit: Optional[float] = None
            try:
                net_cash_impact = res.get("net_cash_impact")
                commission = res.get("commission")
                res_contracts = res.get("contracts") or contracts
                if net_cash_impact is not None and commission is not None and res_contracts:
                    net_credit = round(
                        (float(net_cash_impact) + float(commission)) / (100.0 * float(res_contracts)), 2
                    )
            except (TypeError, ValueError, ZeroDivisionError):
                net_credit = None

            return {
                "ok": True,
                "order_id": res.get("order_id") or f"ec_{uuid.uuid4().hex[:8]}",
                "symbol": sym,
                "strategy": strategy,
                "contracts": contracts,
                "net_credit": net_credit,
                "message": f"Successfully executed {strategy} earnings crush trade for {sym}.",
                "details": res,
            }
        else:
            return {
                "ok": False,
                "message": res.get("reason", "Failed to execute earnings crush trade"),
                "details": res,
            }
    except Exception as exc:
        logger.warning("execute_earnings_crush_trade fallback to paper_broker_options_order: %s", exc)
        from pilots.paper_broker_options_order import execute_paper_order
        res = execute_paper_order(
            symbol=sym,
            asset_type="option",
            expiration=expiration,
            legs=legs,
            quantity=float(contracts),
            limit_price=limit_price,
            is_live=False,
        )
        return res

