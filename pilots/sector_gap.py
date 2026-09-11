"""pilots/sector_gap.py -- diagnostic "underrepresented sector" composition
=============================================================================

Compares the GICS sector mix of the operator's current paper holdings
against the tracked universe's sector mix, and reports which sectors are
missing or underweighted. Diagnostic-only (per
``docs/FMP_INTEGRATION.md`` §1a's established convention for this class of
feed): no ``SignalModule``, no ``SIGNAL_WEIGHTS`` entry -- this is consumed
only by ``pilots.weekly_digest.compose_digest``'s "Sector Gap" selection
rule, which tags a pick as a SEPARATE, independent claim from a
signal-driven "Personalized" one (plan §6).

Review notes (implementation plan §0 item 5 -- recorded here rather than
only in a PR description, so the reasoning travels with the code):

* **``HistoricalStore.get_fundamentals_raw(sym, max_age_days=99999)`` is
  NOT a guaranteed zero-network-call read**, despite the huge
  ``max_age_days``. Reading ``data/historical_store.py``'s implementation:
  a huge ``max_age_days`` only prevents re-fetching a row that IS already
  cached but has gone stale -- it does nothing for a symbol with NO cached
  fundamentals row at all (never fetched before) or a cached row whose
  ``raw_json`` is missing/malformed, both of which fall through to a live
  ``provider.get_fundamentals(symbol)`` call regardless of
  ``max_age_days``. Mitigated two ways: (1) ``find_underrepresented_sectors``
  now accepts an optional already-loaded ``snapshot`` (the exact
  ``state_snapshot.json`` payload ``pilots.weekly_digest.compose_digest``
  has already loaded one function-call earlier in the same request) and
  resolves ``symbol -> sector`` from its ``signals[]`` list FIRST --
  ``reporting/state_snapshot.py`` writes a ``"sector"`` key into every
  per-ticker entry there, at zero network/DB cost, for essentially the
  whole per-cycle universe (see ``_sector_map_from_snapshot``); the
  ``HistoricalStore`` path below now only runs for symbols the snapshot
  didn't cover. (2) That residual per-symbol loop is still bounded by a
  wall-clock budget (``_MAX_SECONDS_FOR_SECTOR_LOOKUP``) mirroring the
  same "unbounded per-ticker live-fetch loop" fix already applied once in
  this exact codebase for ``processing_engine.py``'s fundamentals-refresh
  loop (``settings.PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE`` /
  CLAUDE.md's "Pipeline hang fix #2") -- generous but finite, not
  operator-tunable, since this stays diagnostic/best-effort.
* **No existing bulk multi-symbol sector-classification helper was found
  to switch to** for the residual (snapshot-uncovered) symbols.
  ``pilots/sector_selection.py``'s ``HistoricalStore.get_sector_snapshots()``
  (gated by ``settings.FMP_SECTOR_SNAPSHOT_ENABLED``) is a DIFFERENT
  concept -- a sector-LEVEL P/E + 1-day-change valuation snapshot keyed by
  sector NAME, not a per-SYMBOL GICS-sector-classification lookup -- so it
  cannot substitute for "what sector is ticker X in." ``HistoricalStore``
  has no batched equivalent of ``get_bars_bulk`` for fundamentals. The
  per-symbol ``get_fundamentals_raw`` loop (deduplicated across the
  holdings/universe union, so a symbol held in BOTH sets is only looked up
  once) is therefore the best available durable-store-only fallback for
  this purpose.
* **``resolve_universe()`` is called with ``allow_live_broker_fetch=False``**
  -- unlike this repo's other headless/background callers, an earlier
  version of this module omitted that argument and inherited the
  default ``True``, meaning a stale cached Robinhood snapshot plus
  ``settings.ROBINHOOD_AUTO_REFRESH_ENABLED=True`` could trigger a real
  Tier-3 device-approval login from what looks like a passive digest
  read -- reachable both from the daemon's periodic weekly-digest check
  AND from an on-demand ``GET /pilots/weekly-digest`` webapp page load,
  turning a rare edge case into a standing, unattended, potentially
  hourly-recurring exposure. This repo's own CLAUDE.md documents fixing
  the identical hazard once already for ``UniverseTransparency.tsx``'s
  ``GET /data/sync-report`` call; every other headless caller in this
  codebase (``scripts/backfill_news_history.py``,
  ``scripts/backfill_sentiment_history.py``,
  ``scripts/repair_price_bars_adjustment.py``, ...) already passes
  ``allow_live_broker_fetch=False``, and this module now matches them.
"""
import logging
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from data.paper_account_store import PaperAccountStore
from data.historical_store import HistoricalStore
from data.portfolio_sync import resolve_universe

logger = logging.getLogger(__name__)

# Generous but finite wall-clock budget for this function's whole
# per-symbol sector-resolution loop. This is a diagnostic, best-effort
# composition (not a pipeline-cycle hot path), so a symbol whose sector
# can't be resolved before the budget trips is simply excluded from this
# cycle's gap computation -- never fabricated, never a hang (CONSTRAINT #6).
_MAX_SECONDS_FOR_SECTOR_LOOKUP = 30.0


def _sector_map_from_snapshot(snapshot: Optional[Any]) -> Dict[str, str]:
    """Extract ``symbol -> sector`` directly from an already-loaded
    ``state_snapshot.json`` payload -- zero network/DB cost. Mirrors
    ``pilots.radar_ranking.radar_feed``'s own extraction of the identical
    ``signals[]`` field. Returns ``{}`` on any malformed/missing input;
    never raises (CONSTRAINT #6) -- a symbol absent here simply falls
    through to the bounded ``HistoricalStore`` lookup in
    ``find_underrepresented_sectors`` instead.
    """
    if not isinstance(snapshot, dict):
        return {}
    signals = snapshot.get("signals")
    if not isinstance(signals, list):
        return {}
    out: Dict[str, str] = {}
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        symbol = sig.get("symbol")
        sector = sig.get("sector")
        if symbol and sector:
            out[str(symbol).strip().upper()] = sector
    return out


def _resolve_sector_map(symbols: List[str], store: HistoricalStore) -> Dict[str, str]:
    """Resolve ``symbol -> sector`` for *symbols*, deduplicated, bounded by
    ``_MAX_SECONDS_FOR_SECTOR_LOOKUP`` wall-clock seconds. A symbol whose
    sector can't be resolved (missing fundamentals, budget exhausted) is
    simply absent from the returned dict -- never a fabricated value.
    """
    deadline = time.monotonic() + _MAX_SECONDS_FOR_SECTOR_LOOKUP
    budget_logged = False
    sector_by_symbol: Dict[str, str] = {}

    for sym in symbols:
        if time.monotonic() >= deadline:
            if not budget_logged:
                logger.warning(
                    "find_underrepresented_sectors: _MAX_SECONDS_FOR_SECTOR_LOOKUP "
                    "(%.0fs) exceeded -- skipping sector lookup for remaining "
                    "symbols this cycle (a cold fundamentals cache can still "
                    "trigger a live network fetch per symbol; see this module's "
                    "own docstring).",
                    _MAX_SECONDS_FOR_SECTOR_LOOKUP,
                )
                budget_logged = True
            break
        try:
            raw = store.get_fundamentals_raw(sym, max_age_days=99999) or {}
        except Exception as exc:  # noqa: BLE001 -- one bad symbol must not abort the rest
            logger.debug("find_underrepresented_sectors: sector lookup failed for %s: %s", sym, exc)
            continue
        sector = raw.get("sector")
        if sector:
            sector_by_symbol[sym] = sector

    return sector_by_symbol


def find_underrepresented_sectors(snapshot: Optional[Any] = None) -> List[str]:
    """
    Computes a diagnostic list of sectors that are missing or
    underrepresented in the current paper holdings compared to the
    tracked universe. Never raises (CONSTRAINT #6) -- degrades to ``[]``
    on any failure.

    ``snapshot``: the already-loaded ``state_snapshot.json`` payload, when
    the caller has one in hand (``pilots.weekly_digest.compose_digest``
    always does) -- sector data is read from it FIRST, at zero network/DB
    cost, before falling back to the bounded ``HistoricalStore`` lookup
    for any symbol it doesn't cover. ``None`` (the default, e.g. for a
    standalone caller with no snapshot) preserves the original
    always-fetch behavior.
    """
    try:
        # 1. Read current paper holdings. Uppercased for consistency with
        #    every symbol key this function compares against below
        #    (_sector_map_from_snapshot's keys are uppercased; the codebase
        #    convention throughout is uppercase tickers) -- avoids a
        #    case-mismatch silently excluding a symbol from either lookup.
        paper_store = PaperAccountStore(readonly=True)
        open_positions = paper_store.get_open_positions()
        holdings = [p.symbol.strip().upper() for p in open_positions if p.symbol]

        # 2. Get tracked universe. allow_live_broker_fetch=False -- this
        #    is a headless/background-reachable diagnostic (the daemon's
        #    periodic digest check AND an on-demand webapp page load), and
        #    must never attempt an interactive Robinhood login (see this
        #    module's own docstring).
        universe = [s.strip().upper() for s in (resolve_universe(allow_live_broker_fetch=False) or []) if s]
        if not universe:
            return []

        # 3. Resolve sectors: the already-loaded snapshot first (zero
        #    cost), then HistoricalStore -- bounded, deduplicated -- only
        #    for whatever the snapshot didn't cover (see this module's
        #    docstring for why a per-symbol durable-store read is the
        #    best available fallback here).
        all_symbols = list(dict.fromkeys([*holdings, *universe]))  # de-duped, order-stable
        sector_by_symbol = _sector_map_from_snapshot(snapshot)
        missing = [s for s in all_symbols if s not in sector_by_symbol]
        if missing:
            store = HistoricalStore(readonly=True)
            sector_by_symbol.update(_resolve_sector_map(missing, store))

        holding_sectors = [sector_by_symbol[s] for s in holdings if s in sector_by_symbol]
        universe_sectors = [sector_by_symbol[s] for s in universe if s in sector_by_symbol]

        if not universe_sectors:
            return []

        # 4. Compute underrepresented sectors
        uni_counts = Counter(universe_sectors)
        hold_counts = Counter(holding_sectors)

        uni_total = sum(uni_counts.values())
        hold_total = sum(hold_counts.values())

        underrepresented = []
        for sector, uni_count in uni_counts.items():
            uni_pct = uni_count / uni_total
            hold_pct = hold_counts.get(sector, 0) / hold_total if hold_total > 0 else 0.0

            # Missing completely or underrepresented proportionally
            if hold_counts.get(sector, 0) == 0 or hold_pct < uni_pct:
                underrepresented.append(sector)

        return underrepresented

    except Exception as e:
        logger.warning(f"Sector gap computation degraded gracefully: {e}")
        return []
