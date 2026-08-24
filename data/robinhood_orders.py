"""ADVISORY ONLY — NO ORDER CODE. Read-only realized-P&L engine (Tier 7): fetches FILLED equity orders, reconstructs closed round-trip trades via pure FIFO lot-matching, and summarizes realized P&L, win rate, profit factor, and holding statistics with a daily on-disk cache and dead-letter resilience."""

# =============================================================================
# MODULE: ROBINHOOD ORDER HISTORY → REALIZED P&L  (READ-ONLY, ADVISORY ONLY)
# File: data/robinhood_orders.py
#
# ADVISORY ONLY — this module READS the account's *filled* order history and
# reconstructs closed round-trip trades for performance analysis.  It contains
# NO order-submission, order-modification, or order-cancellation code of any
# kind.  Do NOT add any execution function here under any circumstances.
#
# What it provides
# ----------------
#   * `fetch_filled_orders()`        — pulls filled equity orders from Robinhood
#                                       and normalises them to `OrderFill`s
#                                       (with a daily JSON cache).
#   * `reconstruct_closed_trades()`  — PURE FIFO lot-matching: pairs sells
#                                       against the oldest open buy lots to
#                                       produce `ClosedTrade` round-trips with
#                                       realized P&L and holding period.
#   * `realized_pnl_summary()`       — PURE aggregation: realized P&L, win rate,
#                                       avg win / loss, profit factor, holding
#                                       period stats.
#   * `realized_performance()`       — convenience: fetch → reconstruct →
#                                       summarise in one call.
#
# Why this matters
# ----------------
# The platform's calibration tracker (Tier 1.2), fractional-Kelly sizing
# (`sizing/kelly.py`), and the GUI rely on a population of *closed* trades.
# This module is the live, repeatable source for that population — the same
# FIFO reconstruction that originally seeded the `trades` table, but as a
# first-class, tested, dead-letter-resilient module instead of a one-off.
#
# No fabricated metrics (CONSTRAINT #4): a sell with no matching open lot is
# logged and skipped (never invented as a zero-cost trade); an empty trade set
# yields NaN summary stats, never 0.0.
# =============================================================================

from __future__ import annotations

import json
import logging
import math
import os
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from settings import settings

logger = logging.getLogger(__name__)

# Daily cache of normalised fills — under settings.LOCAL_DATA_ROOT, shared
# across every git worktree on this machine (see docs/architecture/data-layer.md).
_CACHE_PATH: Path = settings.LOCAL_DATA_ROOT / "robinhood_cache" / "robinhood_orders.json"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrderFill:
    """One filled equity order, normalised from the Robinhood order record.

    A single Robinhood order may fill across several executions; we collapse it
    to the cumulative filled quantity at the order's average execution price,
    which is the standard, lossless input for FIFO round-trip reconstruction.
    """
    symbol: str
    side: str          # "buy" | "sell"
    quantity: float
    price: float       # average execution price (USD/share)
    timestamp: datetime  # UTC-aware fill time
    order_id: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OrderFill":
        ts = d["timestamp"]
        dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return cls(
            symbol=str(d["symbol"]).upper(),
            side=str(d["side"]).lower(),
            quantity=float(d["quantity"]),
            price=float(d["price"]),
            timestamp=dt,
            order_id=str(d.get("order_id", "")),
        )


@dataclass(frozen=True)
class ClosedTrade:
    """One reconstructed round-trip (a sell matched against an earlier buy lot)."""
    symbol: str
    quantity: float
    entry_ts: datetime
    exit_ts: datetime
    entry_price: float
    exit_price: float
    realized_pnl: float   # (exit_price - entry_price) * quantity, USD
    return_pct: float     # (exit_price - entry_price) / entry_price * 100
    holding_days: float

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["entry_ts"] = self.entry_ts.isoformat()
        d["exit_ts"] = self.exit_ts.isoformat()
        return d


# ---------------------------------------------------------------------------
# PURE: FIFO round-trip reconstruction
# ---------------------------------------------------------------------------

def reconstruct_closed_trades(fills: List[OrderFill]) -> List[ClosedTrade]:
    """Match sells against the oldest open buy lots (FIFO) per symbol.

    Pure function — deterministic, no I/O.  For each symbol:
      * Buys push an open lot ``(remaining_qty, price, timestamp)``.
      * Sells consume lots from the front; each consumed slice becomes one
        ``ClosedTrade`` whose entry is the lot and exit is the sell.
      * A partially-consumed lot is left on the queue with reduced quantity.
      * A sell quantity exceeding the available open lots (short sale, or a buy
        that predates the fetch window) consumes what exists and the EXCESS is
        logged and dropped — never fabricated as a zero-cost entry (CONSTRAINT #4).

    Output is sorted by ``exit_ts`` ascending so callers get chronological
    closed-trade order (matching ``TransactionsStore.closed_trades_df()``).
    """
    by_symbol: Dict[str, List[OrderFill]] = {}
    for f in fills:
        if f.quantity <= 0 or f.price <= 0:
            continue
        by_symbol.setdefault(f.symbol.upper(), []).append(f)

    trades: List[ClosedTrade] = []
    for symbol, sym_fills in by_symbol.items():
        # Stable chronological order; ties keep buys before sells so a same-
        # timestamp buy+sell still pairs (defensive — rare in practice).
        sym_fills.sort(key=lambda x: (x.timestamp, 0 if x.side == "buy" else 1))
        open_lots: Deque[List[float]] = deque()  # each: [remaining_qty, price, ts_epoch]
        for f in sym_fills:
            if f.side == "buy":
                open_lots.append([f.quantity, f.price, f.timestamp])
                continue
            if f.side != "sell":
                continue
            remaining = f.quantity
            while remaining > 1e-9 and open_lots:
                lot = open_lots[0]
                lot_qty, lot_price, lot_ts = lot[0], lot[1], lot[2]
                matched = min(remaining, lot_qty)
                pnl = (f.price - lot_price) * matched
                ret_pct = ((f.price - lot_price) / lot_price * 100.0) if lot_price > 0 else 0.0
                holding_days = max(0.0, (f.timestamp - lot_ts).total_seconds() / 86400.0)
                trades.append(ClosedTrade(
                    symbol=symbol,
                    quantity=round(matched, 8),
                    entry_ts=lot_ts,
                    exit_ts=f.timestamp,
                    entry_price=lot_price,
                    exit_price=f.price,
                    realized_pnl=pnl,
                    return_pct=ret_pct,
                    holding_days=holding_days,
                ))
                remaining -= matched
                if matched >= lot_qty - 1e-9:
                    open_lots.popleft()
                else:
                    lot[0] = lot_qty - matched
            if remaining > 1e-6:
                logger.info(
                    "FIFO: %s sell of %.4f sh has no matching open lot "
                    "(short or pre-window buy); dropping the unmatched excess.",
                    symbol, remaining,
                )

    trades.sort(key=lambda t: t.exit_ts)
    return trades


# ---------------------------------------------------------------------------
# PURE: realized-performance summary
# ---------------------------------------------------------------------------

def realized_pnl_summary(trades: List[ClosedTrade]) -> Dict[str, Any]:
    """Aggregate closed trades into realized-performance statistics.

    Pure function.  Empty input yields a NaN-shaped summary (CONSTRAINT #4 —
    never fabricated zeros for win rate / averages).  ``profit_factor`` is NaN
    when there are no losing trades (the ratio is undefined, not infinite).
    """
    nan = float("nan")
    n = len(trades)
    if n == 0:
        return {
            "n_trades": 0,
            "total_realized_pnl": 0.0,   # a *sum* over zero trades is genuinely 0
            "win_rate": nan,
            "avg_win": nan,
            "avg_loss": nan,
            "profit_factor": nan,
            "avg_return_pct": nan,
            "avg_holding_days": nan,
            "best_trade_pnl": nan,
            "worst_trade_pnl": nan,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
        }

    pnls = [t.realized_pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = float(sum(wins))
    gross_loss = float(sum(losses))  # negative or zero

    return {
        "n_trades": n,
        "total_realized_pnl": float(sum(pnls)),
        "win_rate": len(wins) / n,
        "avg_win": (gross_profit / len(wins)) if wins else nan,
        "avg_loss": (gross_loss / len(losses)) if losses else nan,
        # profit factor = gross profit / |gross loss|; undefined with no losses.
        "profit_factor": (gross_profit / abs(gross_loss)) if losses else nan,
        "avg_return_pct": float(sum(t.return_pct for t in trades) / n),
        "avg_holding_days": float(sum(t.holding_days for t in trades) / n),
        "best_trade_pnl": float(max(pnls)),
        "worst_trade_pnl": float(min(pnls)),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
    }


# ---------------------------------------------------------------------------
# PURE-ish: Robinhood order record → OrderFill normalisation
# ---------------------------------------------------------------------------

def _parse_timestamp(raw: Any) -> Optional[datetime]:
    """Parse a Robinhood ISO timestamp to a UTC-aware datetime; None on failure."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_orders(
    raw_orders: List[Dict[str, Any]],
    symbol_resolver: Callable[[str], Optional[str]],
) -> List[OrderFill]:
    """Normalise raw Robinhood stock-order dicts to a list of ``OrderFill``.

    Only orders with ``state == "filled"`` and a positive cumulative quantity
    are kept.  ``symbol_resolver`` maps an instrument URL → ticker (cached by
    the caller to avoid repeat network calls).  Records that fail to parse are
    logged at DEBUG and skipped — one bad order never aborts the batch.

    Pure with respect to wall-clock; the only side effect is calling the
    injected ``symbol_resolver`` (which the caller controls / mocks).
    """
    fills: List[OrderFill] = []
    for od in raw_orders or []:
        try:
            if str(od.get("state", "")).lower() != "filled":
                continue
            side = str(od.get("side", "")).lower()
            if side not in ("buy", "sell"):
                continue
            qty = float(od.get("cumulative_quantity") or 0.0)
            if qty <= 0:
                continue
            price_raw = od.get("average_price")
            if price_raw in (None, ""):
                # Fall back to the price field; skip if neither is usable.
                price_raw = od.get("price")
            price = float(price_raw or 0.0)
            if price <= 0:
                continue
            ts = (
                _parse_timestamp(od.get("last_transaction_at"))
                or _parse_timestamp(od.get("updated_at"))
                or _parse_timestamp(od.get("created_at"))
            )
            if ts is None:
                continue
            inst = str(od.get("instrument") or "")
            symbol = symbol_resolver(inst) if inst else None
            if not symbol:
                continue
            fills.append(OrderFill(
                symbol=str(symbol).upper(),
                side=side,
                quantity=qty,
                price=price,
                timestamp=ts,
                order_id=str(od.get("id") or ""),
            ))
        except Exception as exc:
            logger.debug("Skipping unparseable order record: %s", exc)
    return fills


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _write_cache(fills: List[OrderFill]) -> None:
    """Atomically serialise fills to the daily cache (write-then-rename)."""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".tmp")
        payload = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "fills": [f.to_dict() for f in fills],
        }
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(_CACHE_PATH)
        logger.debug("Cached %d Robinhood fills → %s", len(fills), _CACHE_PATH)
    except Exception as exc:
        logger.warning("Failed to write Robinhood orders cache: %s", exc)


def _read_cache(max_age_hours: float) -> Optional[List[OrderFill]]:
    """Return cached fills when present and fresher than ``max_age_hours``."""
    if not _CACHE_PATH.exists():
        return None
    try:
        payload = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        fetched_at = _parse_timestamp(payload.get("fetched_at"))
        if fetched_at is None:
            return None
        age_h = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600.0
        if age_h > max_age_hours:
            return None
        return [OrderFill.from_dict(d) for d in payload.get("fills", [])]
    except Exception as exc:
        logger.warning("Robinhood orders cache unreadable (%s) — ignoring.", exc)
        return None


# ---------------------------------------------------------------------------
# Network fetch (READ ONLY)
# ---------------------------------------------------------------------------

def _default_symbol_resolver(
    *,
    seed: Optional[Dict[str, Optional[str]]] = None,
    max_network_resolutions: Optional[int] = None,
) -> Callable[[str], Optional[str]]:
    """Build an instrument-URL → ticker resolver backed by robin_stocks.

    ``seed`` pre-populates the in-process cache (e.g. from
    ``data.broker_fills_store.BrokerFillsStore.instrument_symbol_map()``), so
    only instruments not already resolved on a prior ingest pay the network
    cost. ``max_network_resolutions`` bounds how many NEW (non-seeded)
    lookups this resolver will perform before it starts returning ``None``
    for any further unseeded URL without even trying the network — the
    resolutions it did perform are still returned via
    ``resolve.newly_resolved`` (set on the function object) so a caller can
    persist them for next time regardless of whether the budget was hit.

    Memoised so each unique instrument URL hits the network at most once per
    process. Returns ``None`` for any URL that cannot be resolved (never
    raises) so ``parse_orders`` simply skips that order.
    """
    import robin_stocks.robinhood as r  # local import — keep module import light

    cache: Dict[str, Optional[str]] = dict(seed or {})
    newly_resolved: Dict[str, Optional[str]] = {}
    budget = math.inf if max_network_resolutions is None else max(0, max_network_resolutions)

    def resolve(url: str) -> Optional[str]:
        if not url:
            return None
        if url in cache:
            return cache[url]
        if budget <= len(newly_resolved):
            return None
        sym: Optional[str] = None
        try:
            sym = r.get_symbol_by_url(url)
        except Exception as exc:
            logger.debug("symbol resolve failed for %s: %s", url, exc)
        resolved = str(sym).upper() if sym else None
        cache[url] = resolved
        newly_resolved[url] = resolved
        return resolved

    resolve.newly_resolved = newly_resolved  # type: ignore[attr-defined]
    return resolve


def fetch_filled_orders(
    *,
    force: bool = False,
    cache_max_age_hours: float = 20.0,
    orders_fetcher: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    symbol_resolver: Optional[Callable[[str], Optional[str]]] = None,
) -> List[OrderFill]:
    """Fetch and normalise the account's filled equity orders.  READ ONLY.

    Uses a daily JSON cache (``cache/robinhood_orders.json``); pass
    ``force=True`` to bypass it.  ``orders_fetcher`` and ``symbol_resolver`` are
    injectable for testing — by default they call ``robin_stocks`` (logging in
    via the shared TOTP path in :mod:`data.robinhood_portfolio`).

    On any network/auth failure the function degrades to the cached fills when
    available, else returns ``[]`` — it never raises (CONSTRAINT #6).
    """
    if not force:
        cached = _read_cache(cache_max_age_hours)
        if cached is not None:
            logger.info("Using cached Robinhood fills (%d).", len(cached))
            return cached

    try:
        if orders_fetcher is None:
            if os.environ.get("RH_LOGIN_WORKER") == "1":
                # Inside the isolated login worker there is already a real,
                # authenticated robin_stocks session in this process (see
                # data/robinhood_portfolio.py's RH_LOGIN_WORKER branch) —
                # use it directly, same pattern as _fetch_live_snapshot.
                import robin_stocks.robinhood as r
                orders_fetcher = lambda: r.get_all_stock_orders() or []  # noqa: E731
            else:
                # No session exists in this process, and logging in here is
                # structurally forbidden (see
                # data.robinhood_portfolio._login_with's RH_LOGIN_WORKER
                # guard) — a second, unsupervised login attempt from a web
                # request or GUI callback would either hang on a blocking
                # approval prompt or silently fail. Real order history is
                # only ever fetched inside the login worker during a
                # `refresh` login (data/robinhood_login_worker.py, gated by
                # settings.BROKER_TRADE_INGEST_ENABLED). This is caught by
                # the except below and degrades to the cache, exactly as
                # every existing caller (pilots/realized.py,
                # execution/receipts_store.py, both of which inject their
                # own fetcher) already expects.
                from data.robinhood_portfolio import RobinhoodApprovalRequired

                raise RobinhoodApprovalRequired(
                    "Robinhood order history can only be fetched inside the "
                    "isolated login worker. Run `python3 main.py "
                    "--refresh-account` to ingest it."
                )
        if symbol_resolver is None:
            symbol_resolver = _default_symbol_resolver()

        raw_orders = orders_fetcher() or []
        fills = parse_orders(raw_orders, symbol_resolver)
        _write_cache(fills)
        logger.info("Fetched %d filled Robinhood orders.", len(fills))
        return fills
    except Exception as exc:
        logger.error("Robinhood order-history fetch failed: %s", exc)
        cached = _read_cache(max_age_hours=float("inf"))
        if cached is not None:
            logger.warning("Returning stale cached fills (%d) after fetch failure.", len(cached))
            return cached
        return []


def realized_performance(
    *,
    force: bool = False,
    cache_max_age_hours: float = 20.0,
    orders_fetcher: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    symbol_resolver: Optional[Callable[[str], Optional[str]]] = None,
) -> Dict[str, Any]:
    """Convenience: fetch fills → reconstruct closed trades → summarise.

    Returns ``{"summary": {...}, "trades": [ClosedTrade, ...], "n_fills": int}``.
    Dead-letter resilient end-to-end — a fetch failure yields an empty (NaN)
    summary, never an exception.
    """
    fills = fetch_filled_orders(
        force=force,
        cache_max_age_hours=cache_max_age_hours,
        orders_fetcher=orders_fetcher,
        symbol_resolver=symbol_resolver,
    )
    trades = reconstruct_closed_trades(fills)
    return {
        "summary": realized_pnl_summary(trades),
        "trades": trades,
        "n_fills": len(fills),
    }
