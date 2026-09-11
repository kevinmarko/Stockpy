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
  ``max_age_days``. This module's per-symbol loop is therefore capable of
  triggering real network calls on a cold cache -- the same "unbounded
  per-ticker live-fetch loop" bug class already fixed once in this exact
  codebase for ``processing_engine.py``'s fundamentals-refresh loop (see
  ``settings.PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE`` / CLAUDE.md's
  "Pipeline hang fix #2"). This function is NOT on any pipeline-cycle hot
  path (it is read on-demand by the weekly digest composer, dispatched at
  most a few times a day by the daemon's weekly-digest check), but a
  hundreds-of-symbols tracked universe with a fully-cold cache could still
  make this call meaningfully slow the first time it runs. Mitigated below
  with a bounded wall-clock budget (``_MAX_SECONDS_FOR_SECTOR_LOOKUP``)
  mirroring that same established convention, rather than a new
  ``settings.py`` field -- this module is diagnostic/best-effort and the
  bound only needs to be "generous but finite," not operator-tunable.
* **No existing bulk multi-symbol sector-classification helper was found
  to switch to.** ``pilots/sector_selection.py``'s
  ``HistoricalStore.get_sector_snapshots()`` (gated by
  ``settings.FMP_SECTOR_SNAPSHOT_ENABLED``) is a DIFFERENT concept -- a
  sector-LEVEL P/E + 1-day-change valuation snapshot keyed by sector NAME,
  not a per-SYMBOL GICS-sector-classification lookup -- so it cannot
  substitute for "what sector is ticker X in." ``HistoricalStore`` has no
  batched equivalent of ``get_bars_bulk`` for fundamentals. The per-symbol
  ``get_fundamentals_raw`` loop (deduplicated across the holdings/universe
  union below, so a symbol held in BOTH sets is only looked up once) is
  therefore the best available durable-store-only read for this purpose
  today.
"""
import logging
import time
from collections import Counter
from typing import Dict, List

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


def find_underrepresented_sectors() -> List[str]:
    """
    Computes a diagnostic list of sectors that are missing or
    underrepresented in the current paper holdings compared to the
    tracked universe. Never raises (CONSTRAINT #6) -- degrades to ``[]``
    on any failure.
    """
    try:
        # 1. Read current paper holdings
        paper_store = PaperAccountStore(readonly=True)
        open_positions = paper_store.get_open_positions()
        holdings = [p.symbol for p in open_positions]

        # 2. Get tracked universe
        universe = resolve_universe()
        if not universe:
            return []

        # 3. Resolve sectors using HistoricalStore -- deduplicated across
        #    the holdings/universe union so a symbol present in both is
        #    only looked up once (see this module's docstring for why a
        #    per-symbol durable-store read is the best available option
        #    here, and why it is still bounded by a wall-clock budget).
        store = HistoricalStore(readonly=True)
        all_symbols = list(dict.fromkeys([*holdings, *universe]))  # de-duped, order-stable
        sector_by_symbol = _resolve_sector_map(all_symbols, store)

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
