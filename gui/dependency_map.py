"""
gui/dependency_map.py — declarative data-source → consumer dependency graph.

What this is
------------
A pure-data, pure-Python map of "if data source X goes down, which strategies,
reports, and tabs lose coverage?" The map itself is the contract; the
helpers below project an arbitrary set of degraded sources into the impacted
downstream consumers.

Why declarative
---------------
Inferring this graph from imports would over-couple it to the call sites
(half the consumers only use a source on a code path that's gated by a
config flag). A short, hand-curated table is more honest about what the
operator actually loses when (say) Finnhub is rate-limited mid-run, AND it
puts the map on every reviewer's screen during code review.

Add a new consumer of an existing source: append to ``CONSUMERS`` for that
source. Add a new source: add a new ``DataSource`` entry.

Constraints honoured
--------------------
* CONSTRAINT #5 (never fabricate) — if a source isn't in the map, it
  resolves to ``DataSource.UNKNOWN`` and the impacted list is empty (we
  don't guess what depends on it).
* CONSTRAINT #9 (type hints).
* CONSTRAINT #10 (logging).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Iterable, List, Mapping, Sequence, Set

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source enum
# ---------------------------------------------------------------------------

class DataSource(str, Enum):
    """Coarse identifier for an upstream data source.

    Values are stable strings so they're safe to embed in JSON state
    snapshots or pandas cells.
    """

    # Free / open
    YFINANCE = "yfinance"          # delayed bars + .info fundamentals fallback
    FRED = "fred"                  # macro series (VIX, yield curve, Sahm)

    # Paid-tier-but-free APIs we already use
    ALPACA = "alpaca"              # real-time IEX quotes/bars (free tier)
    FINNHUB = "finnhub"            # fundamentals (free tier)

    # Account state
    ROBINHOOD = "robinhood"        # holdings, cost basis, dividends (read-only)

    # Internal stores
    TRANSACTIONS_DB = "transactions_db"  # SQLite-backed TransactionsStore
    STATE_SNAPSHOT = "state_snapshot"    # output/state_snapshot.json

    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return _LABELS.get(self, self.value)


_LABELS: Dict[DataSource, str] = {
    DataSource.YFINANCE:         "yfinance (delayed quotes/bars + fundamentals fallback)",
    DataSource.FRED:             "FRED (macro series)",
    DataSource.ALPACA:           "Alpaca IEX (real-time quotes/bars)",
    DataSource.FINNHUB:          "Finnhub (fundamentals)",
    DataSource.ROBINHOOD:        "Robinhood (account snapshot, dividends)",
    DataSource.TRANSACTIONS_DB:  "TransactionsStore (closed-trade ledger)",
    DataSource.STATE_SNAPSHOT:   "state_snapshot.json (last orchestrator run)",
    DataSource.UNKNOWN:          "(unknown)",
}


# ---------------------------------------------------------------------------
# Consumer dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Consumer:
    """A downstream module/tab that depends on one or more data sources."""

    name: str
    kind: str   # "strategy" | "report" | "tab" | "engine"
    description: str


@dataclass(frozen=True)
class ImpactRecord:
    """One row of the impact projection rendered in Gravity Audit."""

    source: DataSource
    consumers: tuple[Consumer, ...]

    @property
    def consumer_count(self) -> int:
        return len(self.consumers)


# ---------------------------------------------------------------------------
# Hand-curated map — extend by appending rows, never by editing the algorithm
# ---------------------------------------------------------------------------

# Each Consumer is duplicated across every source it actually depends on so
# the projection is symmetric. Mirroring is intentional: it forces the
# operator to think "what does this strategy need to function" instead of
# only "what does this source feed."

_QUOTE_CONSUMERS: tuple[Consumer, ...] = (
    Consumer("strategy_engine",      "engine",
             "Per-symbol BUY/HOLD/SELL signal scoring (uses live price + bars)."),
    Consumer("forecasting_engine",   "engine",
             "ARIMA / Monte Carlo / Holt-Winters / CNN-LSTM forecasts."),
    Consumer("processing_engine",    "engine",
             "Technical indicators (RSI, MACD, ATR, Aroon, RSI-2, SMA-5)."),
    Consumer("technical_options_engine", "engine",
             "GJR-GARCH σ, IVR proxy, premium-selling matrix."),
    Consumer("Strategy Matrix tab",  "tab",
             "Live weights/preview rely on most-recent quote data."),
    Consumer("Reports tab",          "tab",
             "Portfolio heat + MFE/MAE need the price column."),
)

_FUNDAMENTALS_CONSUMERS: tuple[Consumer, ...] = (
    Consumer("processing_engine.calculate_fundamental_metrics", "engine",
             "Graham number, Gordon fair value, multifactor inputs."),
    Consumer("multifactor signal",   "strategy",
             "Value/Quality factor z-scores."),
    Consumer("Reports tab",          "tab",
             "Brinson-Fachler attribution needs fundamentals to map sectors."),
)

_MACRO_CONSUMERS: tuple[Consumer, ...] = (
    Consumer("macro_engine",         "engine",
             "Sahm Rule, yield curve, HY OAS, killSwitch state."),
    Consumer("regime_multiplier signal", "strategy",
             "HMM second-opinion sizing multiplier."),
    Consumer("Observability tab",    "tab",
             "Recession indicator telemetry strip."),
    Consumer("PreTradeRiskGate.macro_kill_switch_check", "engine",
             "Vetoes new BUY orders in RECESSION / CREDIT EVENT regimes."),
)

_ACCOUNT_CONSUMERS: tuple[Consumer, ...] = (
    Consumer("engine.advisory",      "engine",
             "Cost basis + dividends drive hold/sell overlay decisions."),
    Consumer("Paper-Trading Monitor", "tab",
             "Robinhood account is the source of truth for holdings."),
    Consumer("Live Inventory tab",   "tab",
             "Holdings ∪ RH watchlists ∪ file watchlists sync."),
    Consumer("HTML report — Holdings & P&L", "report",
             "Cost-basis-anchored equity view."),
)

_TXN_CONSUMERS: tuple[Consumer, ...] = (
    Consumer("sizing.kelly",         "engine",
             "Per-strategy bootstrap-conservative Kelly fraction."),
    Consumer("MetaLabeler",          "engine",
             "Stage 4 meta-label training set."),
    Consumer("Observability tab",    "tab",
             "Strategy P&L panel."),
)

_SNAPSHOT_CONSUMERS: tuple[Consumer, ...] = (
    Consumer("Observability tab",    "tab",
             "All KPI tiles read from the last state snapshot."),
    Consumer("Reports tab",          "tab",
             "Portfolio heat snapshot."),
    Consumer("Market Data tab",      "tab",
             "Default symbol set comes from the last signals."),
)


# Final map: source -> consumers. Sources marked with a tuple of
# CONSUMERS share the same list (e.g. Alpaca and yfinance both feed the
# quote consumers — we record both edges so the operator sees that
# yfinance going down still leaves a path via Alpaca, and vice versa).
CONSUMERS: Dict[DataSource, tuple[Consumer, ...]] = {
    DataSource.YFINANCE:        _QUOTE_CONSUMERS + (_FUNDAMENTALS_CONSUMERS[0],),
    DataSource.ALPACA:          _QUOTE_CONSUMERS,
    DataSource.FINNHUB:         _FUNDAMENTALS_CONSUMERS,
    DataSource.FRED:            _MACRO_CONSUMERS,
    DataSource.ROBINHOOD:       _ACCOUNT_CONSUMERS,
    DataSource.TRANSACTIONS_DB: _TXN_CONSUMERS,
    DataSource.STATE_SNAPSHOT:  _SNAPSHOT_CONSUMERS,
}


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------

def impacted_consumers(
    degraded: Iterable[DataSource | str],
) -> List[ImpactRecord]:
    """Return one :class:`ImpactRecord` per degraded source.

    String inputs that do not match a known :class:`DataSource` are mapped to
    :data:`DataSource.UNKNOWN` and produce an empty consumer list — we never
    fabricate impact when we don't know what depends on a mystery source.
    """
    out: List[ImpactRecord] = []
    seen: Set[DataSource] = set()
    for raw in degraded:
        if isinstance(raw, DataSource):
            source = raw
        else:
            try:
                source = DataSource(raw)
            except ValueError:
                logger.debug("dependency_map: unknown source %r → UNKNOWN", raw)
                source = DataSource.UNKNOWN
        if source in seen:
            continue
        seen.add(source)
        out.append(ImpactRecord(source=source,
                                consumers=CONSUMERS.get(source, ())))
    return out


def all_consumers() -> List[Consumer]:
    """Return every consumer appearing anywhere in the map, deduped by name."""
    seen: Dict[str, Consumer] = {}
    for consumers in CONSUMERS.values():
        for c in consumers:
            seen.setdefault(c.name, c)
    return sorted(seen.values(), key=lambda c: (c.kind, c.name))


def render_edges() -> List[tuple[str, str, str]]:
    """Return ``(source_label, consumer_name, consumer_kind)`` edges.

    Used by the Streamlit panel to render a flat dataframe view of the map
    when an interactive graph library isn't available (we deliberately stay
    dependency-free at the graph level — CONSTRAINT #1).
    """
    edges: List[tuple[str, str, str]] = []
    for src, consumers in CONSUMERS.items():
        for c in consumers:
            edges.append((src.label, c.name, c.kind))
    return edges
