"""
data/portfolio_sync.py
======================
Task 1.4 — Portfolio & Watchlist Synchronization Engine.

Single-purpose module that takes the **union** of (a) every active Robinhood
holding and (b) every user-defined Robinhood watchlist + plain-text watchlist
files, and reconciles it against the platform's market-data feeds
(``data.market_data`` → Alpaca quotes/bars + Finnhub fundamentals).

Why this exists
---------------
Without an explicit reconciliation pass the pipeline silently drops idiosyncratic
positions held in the brokerage account but absent from market-data coverage,
producing dashboards that mis-state total equity (the holding is invisible) or
recommend cash-out on a symbol the operator deliberately holds.  Task 1.4 makes
that situation **explicit**: every symbol from the operator's portfolio surfaces
in a structured ``SyncReport`` with a typed coverage status; coverage gaps are
logged once (non-blocking) and excluded from price-dependent metrics while
remaining present in the equity view.

Public API
----------
- :class:`CoverageStatus` — enum of per-symbol classifications:
    * ``FULL``           — quote + bars + fundamentals all reachable.
    * ``EQUITY_ONLY``    — held in Robinhood but no quote/bar from the active
                            market-data provider (e.g. delisted, OTC, foreign).
    * ``QUOTES_ONLY``    — quote/bars reachable but fundamentals empty.
    * ``UNCOVERED``      — neither quote nor fundamentals reachable.
    * ``UNKNOWN``        — probe was skipped (offline mode, etc.).

- :class:`SymbolStatus` — frozen dataclass: symbol, coverage status, "held"
    flag, watchlist memberships, cost-basis delta (current_price − avg_cost),
    market value, forecast availability flag, and a short diagnostic string.

- :class:`SyncReport` — frozen dataclass with ``positions``, ``watchlists``
    ``symbols`` (dict[str, SymbolStatus]), ``generated_at`` UTC timestamp, and
    a ``to_dict()`` round-trip for JSON cache persistence.

- :func:`build_sync_report(snapshot, *, client=None, watchlist_files=None,
  forecast_symbols=None, probe_market=True)` — compose a complete report.
  Market probing is delegated to ``data.market_data.get_provider()`` and is
  wrapped per-symbol so one bad symbol never aborts the run.

- :func:`async_sync_now(...)` — async wrapper around ``build_sync_report`` that
  also persists the resulting universe to ``DEFAULT_TICKERS`` via
  :mod:`gui.env_io` so the operator's GUI choice survives the next launch.

CONSTRAINTS honoured
--------------------
* No paid dependencies — Alpaca / Finnhub / yfinance via the existing
  ``data.market_data`` layer; no new vendors.
* No fabricated metrics — when a quote / bar / fundamental fetch fails the
  symbol is marked ``UNCOVERED`` / ``EQUITY_ONLY`` and a NaN is propagated
  rather than a synthetic placeholder.
* No bare ``except Exception: return 0.0`` — every catch logs context and
  records the exception's message into the symbol's diagnostic field.
* Source-of-truth separation (CONSTRAINT #4): Robinhood is the source of truth
  for holdings & cost basis; market data is the source of truth for prices.
  Cost-basis deltas combine the two but never overwrite either.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

logger = logging.getLogger(__name__)

# Cache for the most recent SyncReport — readable by the GUI Live Inventory
# panel after the orchestrator has run, so a panel render does not need to
# block on a fresh probe.  Atomic write-then-rename, same convention as
# data/robinhood_portfolio.py's account cache.
_CACHE_PATH: Path = Path(__file__).parent.parent / "cache" / "sync_report.json"


# ---------------------------------------------------------------------------
# Enums + frozen dataclasses
# ---------------------------------------------------------------------------


class CoverageStatus(str, Enum):
    """Per-symbol cross-source reconciliation outcome.

    Subclassing ``str`` makes the enum JSON-serialisable without a custom
    encoder — ``json.dumps`` will emit the literal string value.
    """

    FULL = "full"               # quote + bars + fundamentals
    QUOTES_ONLY = "quotes_only"  # quote + bars but no fundamentals
    EQUITY_ONLY = "equity_only"  # held in Robinhood but no quote/bar
    UNCOVERED = "uncovered"     # neither quote nor fundamentals
    UNKNOWN = "unknown"         # probe was skipped (e.g. offline mode)


@dataclass(frozen=True)
class SymbolStatus:
    """Immutable per-symbol sync-report row.

    ``cost_basis_delta`` is signed: positive = unrealized gain per share,
    negative = unrealized loss per share.  ``float('nan')`` when either side
    of the subtraction is unknown (no holding OR no live quote) — never a
    synthetic zero, per CONSTRAINT #4.
    """

    symbol: str
    coverage: CoverageStatus
    held: bool
    quantity: float                          # 0.0 when not held
    avg_cost: float                          # NaN when not held
    current_price: float                     # NaN when no quote
    cost_basis_delta_per_share: float        # current_price - avg_cost (signed)
    market_value: float                      # quantity * current_price (NaN if either side NaN)
    is_stale_quote: bool                     # surfaced from MarketDataProvider.Quote.is_stale
    quote_source: str                        # "alpaca"/"yfinance"/"" when no quote
    has_fundamentals: bool
    forecast_available: bool                 # True when a Forecast_30 (or analogous) value exists
    watchlists: tuple[str, ...]              # names of RH lists containing this symbol
    diagnostic: str                          # short non-blocking error / annotation

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "coverage": self.coverage.value,
            "held": self.held,
            "quantity": self.quantity,
            "avg_cost": self.avg_cost,
            "current_price": self.current_price,
            "cost_basis_delta_per_share": self.cost_basis_delta_per_share,
            "market_value": self.market_value,
            "is_stale_quote": self.is_stale_quote,
            "quote_source": self.quote_source,
            "has_fundamentals": self.has_fundamentals,
            "forecast_available": self.forecast_available,
            "watchlists": list(self.watchlists),
            "diagnostic": self.diagnostic,
        }


@dataclass(frozen=True)
class SyncReport:
    """Composite output of one reconciliation pass.

    ``symbols`` is the dict keyed by ticker; ``positions`` and ``watchlists``
    are kept on the report so the GUI can compute additional cuts (held-only,
    watchlist-only, by-list) without re-probing the broker.
    """

    generated_at: datetime
    positions: tuple[str, ...]
    watchlists: Mapping[str, tuple[str, ...]]
    symbols: Mapping[str, SymbolStatus]
    provider_source: str
    fundamentals_source: str

    # ---- summary properties (cheap derived metrics for the GUI) ----

    @property
    def n_full(self) -> int:
        return sum(1 for s in self.symbols.values() if s.coverage is CoverageStatus.FULL)

    @property
    def n_equity_only(self) -> int:
        return sum(1 for s in self.symbols.values() if s.coverage is CoverageStatus.EQUITY_ONLY)

    @property
    def n_uncovered(self) -> int:
        return sum(1 for s in self.symbols.values() if s.coverage is CoverageStatus.UNCOVERED)

    @property
    def n_total(self) -> int:
        return len(self.symbols)

    def held_total_equity(self) -> float:
        """Sum of market_value across all held symbols (NaN-safe).

        Held positions whose live price is unknown contribute their cost basis
        (``quantity * avg_cost``) so the equity view remains accurate at
        cost — never fabricates a current-price proxy.
        """
        total = 0.0
        for s in self.symbols.values():
            if not s.held:
                continue
            if _isfinite(s.market_value):
                total += s.market_value
            elif _isfinite(s.avg_cost):
                total += s.quantity * s.avg_cost
        return total

    # ---- serialization ----

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "positions": list(self.positions),
            "watchlists": {k: list(v) for k, v in self.watchlists.items()},
            "symbols": {k: v.to_dict() for k, v in self.symbols.items()},
            "provider_source": self.provider_source,
            "fundamentals_source": self.fundamentals_source,
        }


def _isfinite(x: float) -> bool:
    """True iff *x* is not NaN and not infinite (stdlib-free check)."""
    return x == x and x not in (float("inf"), float("-inf"))


# ---------------------------------------------------------------------------
# Coverage probe — per-symbol, never raises
# ---------------------------------------------------------------------------


def _probe_symbol_coverage(
    symbol: str,
    provider: Any,
) -> Dict[str, Any]:
    """Probe one symbol against the market-data provider.

    Returns a dict (NOT a SymbolStatus) with the raw fields the caller will
    fold into a finished SymbolStatus, including:
      - ``coverage``     : CoverageStatus enum
      - ``current_price``: float, NaN on failure
      - ``is_stale``     : bool, False on no-quote
      - ``source``       : str, "" on no-quote
      - ``has_funds``    : bool
      - ``diagnostic``   : str, "" on full success
    """
    diag: List[str] = []
    quote_ok = False
    bars_ok = False
    fund_ok = False
    price = float("nan")
    is_stale = False
    source = ""

    # --- quote probe ---
    try:
        q = provider.get_latest_quote(symbol)
        quote_ok = True
        price = float(q.price)
        is_stale = bool(q.is_stale)
        source = str(q.source)
    except Exception as exc:  # noqa: BLE001 - per-symbol dead-letter
        diag.append(f"quote:{type(exc).__name__}")

    # --- bars probe (only if quote succeeded — bar fetch is the heavier call
    # and we don't pay it for symbols we already know aren't covered) ---
    if quote_ok:
        try:
            bars = provider.get_intraday_bars(symbol, lookback_days=5)
            bars_ok = bars is not None and not bars.empty
            if not bars_ok:
                diag.append("bars:empty")
        except Exception as exc:  # noqa: BLE001
            diag.append(f"bars:{type(exc).__name__}")

    # --- fundamentals probe — empty dict is a legitimate "no coverage"
    # outcome (the get_fundamentals contract never raises). ---
    try:
        funds = provider.get_fundamentals(symbol) or {}
        fund_ok = bool(funds)
        if not fund_ok:
            diag.append("fundamentals:empty")
    except Exception as exc:  # noqa: BLE001 - defensive; provider says it doesn't raise
        diag.append(f"fundamentals:{type(exc).__name__}")

    # --- classify ---
    if quote_ok and bars_ok and fund_ok:
        coverage = CoverageStatus.FULL
    elif quote_ok and bars_ok and not fund_ok:
        coverage = CoverageStatus.QUOTES_ONLY
    elif not quote_ok and not fund_ok:
        coverage = CoverageStatus.UNCOVERED
    else:
        # quote failed but fundamentals reachable, OR quote OK but no bars.
        # Treat as uncovered for pricing-dependent metrics; the diagnostic
        # records exactly which leg failed.
        coverage = CoverageStatus.UNCOVERED

    return {
        "coverage": coverage,
        "current_price": price,
        "is_stale": is_stale,
        "source": source,
        "has_funds": fund_ok,
        "diagnostic": ",".join(diag),
    }


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


def _watchlists_to_symbol_map(
    watchlists: Mapping[str, Iterable[str]]
) -> Dict[str, List[str]]:
    """Invert ``{list_name: [symbols]}`` to ``{symbol: [list_names]}``."""
    inverted: Dict[str, List[str]] = {}
    for name, syms in watchlists.items():
        for s in syms:
            inverted.setdefault(s.upper().strip(), []).append(name)
    return inverted


def build_sync_report(
    snapshot: Optional[Any],
    *,
    client: Optional[Any] = None,
    watchlist_files: Optional[Iterable[Path]] = None,
    forecast_symbols: Optional[Iterable[str]] = None,
    probe_market: bool = True,
) -> SyncReport:
    """Build a :class:`SyncReport` reconciling all sources for the current run.

    Parameters
    ----------
    snapshot:
        :class:`data.robinhood_portfolio.AccountSnapshot` (or any object with a
        ``positions`` mapping yielding objects that expose ``symbol``,
        ``quantity``, ``average_cost``, ``current_price``, ``market_value``,
        ``unrealized_pl``).  ``None`` is tolerated — we proceed with an empty
        holdings set.
    client:
        Optional :class:`data.robinhood_client.RobinhoodClient` (authenticated).
        When provided, every Robinhood watchlist is also folded into the
        universe; otherwise only holdings + file-backed lists contribute.
    watchlist_files:
        Iterable of plain-text watchlist file paths (one ticker per line).
        Combined with the ``SYNC_WATCHLIST_FILES`` env var that is read by
        :func:`data.robinhood_client.discover_universe`.
    forecast_symbols:
        Iterable of symbols for which a forecast (``Forecast_30`` or
        equivalent) is known to exist in the latest pipeline run.  The GUI
        uses this to colour-code "forecast available?" without re-running the
        forecasting engine.
    probe_market:
        When ``False``, skip the market-data probe entirely and return all
        symbols as ``CoverageStatus.UNKNOWN``.  Useful for fast offline
        sanity tests.

    Returns
    -------
    SyncReport
        Fully populated report ready to render or cache.  Never raises on
        per-symbol failures — diagnostics are recorded on each SymbolStatus.
    """
    # ----- holdings -----
    positions_map: Dict[str, Any] = {}
    if snapshot is not None and hasattr(snapshot, "positions"):
        positions_map = dict(getattr(snapshot, "positions") or {})

    # ----- watchlists (Robinhood + file) -----
    watchlists: Dict[str, List[str]] = {}
    if client is not None:
        try:
            from data.robinhood_client import discover_watchlists

            watchlists = {k: list(v) for k, v in discover_watchlists(client).items()}
        except Exception as exc:  # noqa: BLE001
            logger.warning("discover_watchlists failed: %s", exc)

    # File-backed watchlists are folded in as a synthetic list named "file:<path>"
    # so the GUI can attribute their entries to a source.
    from data.robinhood_client import _file_tickers, _watchlist_files_from_env

    file_paths: List[Path] = list(_watchlist_files_from_env())
    if watchlist_files:
        file_paths.extend(Path(p) for p in watchlist_files)
    for path in file_paths:
        syms = _file_tickers(path)
        if syms:
            watchlists[f"file:{path.name}"] = syms

    sym_to_lists = _watchlists_to_symbol_map(watchlists)

    # ----- universe = holdings ∪ all watchlists -----
    universe: set[str] = set()
    universe.update(s.upper() for s in positions_map.keys())
    for syms in watchlists.values():
        universe.update(s.upper() for s in syms)

    # ----- provider -----
    provider = None
    provider_source = ""
    fundamentals_source = ""
    if probe_market and universe:
        try:
            from data.market_data import get_provider

            provider = get_provider()
            provider_source = getattr(provider, "quote_source", "unknown")
            fundamentals_source = (
                "finnhub" if (provider_source and __import__("os").environ.get("FINNHUB_API_KEY"))
                else "yfinance"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Market-data provider unavailable for sync probe: %s", exc)
            provider = None
            probe_market = False

    forecast_set = {s.upper() for s in (forecast_symbols or [])}

    # ----- per-symbol assembly -----
    symbols: Dict[str, SymbolStatus] = {}
    for sym in sorted(universe):
        pos = positions_map.get(sym)
        held = pos is not None
        qty = float(getattr(pos, "quantity", 0.0) or 0.0) if held else 0.0
        avg = float(getattr(pos, "average_cost", float("nan"))) if held else float("nan")

        if provider is not None and probe_market:
            probe = _probe_symbol_coverage(sym, provider)
        else:
            probe = {
                "coverage": CoverageStatus.UNKNOWN,
                "current_price": float("nan"),
                "is_stale": False,
                "source": "",
                "has_funds": False,
                "diagnostic": "probe_skipped",
            }

        price = probe["current_price"]
        # Held but uncovered = upgrade to EQUITY_ONLY so the operator knows the
        # position is recognised even though pricing is unavailable.
        coverage: CoverageStatus = probe["coverage"]
        if held and coverage is CoverageStatus.UNCOVERED:
            coverage = CoverageStatus.EQUITY_ONLY

        if _isfinite(price) and _isfinite(avg):
            delta = price - avg
        else:
            delta = float("nan")

        if held and _isfinite(price):
            mkt_val = qty * price
        elif held:
            # No live quote: surface the Robinhood-reported market_value only
            # when it is strictly positive (a 0.0 reading on a held position
            # almost always means RH didn't refresh the field — treat it as
            # unknown so held_total_equity() falls back to cost basis instead
            # of fabricating an at-zero valuation).
            rh_mv = float(getattr(pos, "market_value", float("nan")))
            mkt_val = rh_mv if (_isfinite(rh_mv) and rh_mv > 0) else float("nan")
        else:
            mkt_val = float("nan")

        symbols[sym] = SymbolStatus(
            symbol=sym,
            coverage=coverage,
            held=held,
            quantity=qty,
            avg_cost=avg,
            current_price=price,
            cost_basis_delta_per_share=delta,
            market_value=mkt_val,
            is_stale_quote=bool(probe["is_stale"]),
            quote_source=str(probe["source"]),
            has_fundamentals=bool(probe["has_funds"]),
            forecast_available=sym in forecast_set,
            watchlists=tuple(sym_to_lists.get(sym, [])),
            diagnostic=str(probe["diagnostic"]),
        )

        # One-shot non-blocking diagnostic log so the operator sees coverage
        # gaps in the launch log without scrolling the GUI.
        if coverage in (CoverageStatus.EQUITY_ONLY, CoverageStatus.UNCOVERED):
            logger.info(
                "PortfolioSync: %s classified %s (held=%s, diagnostic=%s)",
                sym, coverage.value, held, probe["diagnostic"] or "n/a",
            )

    return SyncReport(
        generated_at=datetime.now(timezone.utc),
        positions=tuple(sorted(positions_map.keys())),
        watchlists={k: tuple(v) for k, v in watchlists.items()},
        symbols=symbols,
        provider_source=provider_source,
        fundamentals_source=fundamentals_source,
    )


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def write_cache(report: SyncReport, path: Optional[Path] = None) -> None:
    """Atomically persist *report* to a JSON cache file (write-then-rename)."""
    target = path or _CACHE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(target)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write sync-report cache to %s: %s", target, exc)
        tmp.unlink(missing_ok=True)


def read_cache(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Return the most recently cached sync report as a dict, or ``None``."""
    target = path or _CACHE_PATH
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("sync-report cache unreadable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# On-demand async refresh (used by GUI Sync Now button)
# ---------------------------------------------------------------------------


async def async_sync_now(
    snapshot: Optional[Any],
    *,
    client: Optional[Any] = None,
    watchlist_files: Optional[Iterable[Path]] = None,
    forecast_symbols: Optional[Iterable[str]] = None,
    persist_default_tickers: bool = True,
    probe_market: bool = True,
) -> SyncReport:
    """Run :func:`build_sync_report` off-thread and (optionally) persist tickers.

    Parameters
    ----------
    persist_default_tickers:
        When ``True`` the resulting full universe is written to ``.env`` as
        ``DEFAULT_TICKERS`` via :func:`gui.env_io.write_setting` so the
        operator's discovered universe survives the next launch.  Set ``False``
        to dry-run the sync without touching ``.env`` (used in tests and from
        CI).  Secret-write/disallowed-key errors from env_io are caught and
        logged rather than propagated — a failed persist must not crash the
        GUI's refresh handler.
    """
    loop = asyncio.get_event_loop()
    report: SyncReport = await loop.run_in_executor(
        None,
        lambda: build_sync_report(
            snapshot,
            client=client,
            watchlist_files=watchlist_files,
            forecast_symbols=forecast_symbols,
            probe_market=probe_market,
        ),
    )

    # Persist cache regardless — the GUI panel reads it on the next render.
    try:
        write_cache(report)
    except Exception as exc:  # noqa: BLE001 - cache write failure is non-fatal
        logger.warning("sync_now cache persist failed: %s", exc)

    if persist_default_tickers:
        try:
            from gui.env_io import write_setting

            # Only the symbols we actually probed (or pre-classified) — sorted
            # for diff-friendliness.
            tickers = sorted(report.symbols.keys())
            if tickers:
                write_setting("DEFAULT_TICKERS", tickers)
                logger.info(
                    "sync_now: wrote %d tickers to DEFAULT_TICKERS in .env.",
                    len(tickers),
                )
        except Exception as exc:  # noqa: BLE001 - env write errors are non-fatal
            logger.warning("sync_now DEFAULT_TICKERS persist failed: %s", exc)

    return report
