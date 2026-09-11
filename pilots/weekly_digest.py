"""pilots/weekly_digest.py -- "This Week's Digest" composer
=============================================================

Combines Today's Radar's (``pilots/radar_ranking.py``) top-ranked symbols
with a minimal view-tracking log (``data/symbol_view_store.py``) and a
sector-gap diagnostic (``pilots/sector_gap.py``) into an honestly-tagged,
5-slot-max weekly digest. No new scoring logic lives here -- see the
introducing implementation plan (``.claude/weekly-digest_implementation_plan.md``)
§1/§2's explicit scope boundary.

Honest 3-rung fallback ladder (plan §4), enforced by ``compose_digest``:

1. **No snapshot yet, or Radar computed zero candidates this cycle**
   (sparse ``DailySignals`` is the realistic default state, not a rare
   edge case) -> an empty payload whose ``reason`` is reused VERBATIM from
   ``radar_feed``'s own honest message, never re-typed here -- so the two
   honesty strings can never drift apart.
2. **Radar has candidates, but the view-tracking log has no recorded
   views yet** (fresh install / cold start -- the realistic default state
   for any new deployment) -> every item is honestly capped at
   ``selection_type="Today's Radar"``/``confidence_tier="low"`` unless a
   genuinely independent claim (sector-gap) applies; ``"Personalized"`` is
   *never* minted vacuously just because the empty view log makes every
   candidate trivially "not in an empty set" (CONSTRAINT #4 -- the
   critical bug this module was fixed for: see
   ``docs/known_issues/`` / this PR's own walkthrough).
3. **Personalization active, items populated, nothing else degraded**
   (the normal happy path) -> ``reason=None``.

Sector-gap tagging (``find_underrepresented_sectors``) is a SEPARATE,
portfolio-composition-driven claim, not gated on view-tracking history at
all -- it can honestly apply whether or not personalization is active,
since "you're underweight this sector" and "you haven't looked at this"
are independent facts (plan §6: never blend the two into one opaque
score/confidence).
"""
import logging
from typing import Optional

from pilots.scoring import load_snapshot
from pilots.radar_ranking import radar_feed
from data.symbol_view_store import SymbolViewStore
from pilots.sector_gap import find_underrepresented_sectors
from pilots.digest_models import DigestPayload, DigestItem

logger = logging.getLogger(__name__)

_MAX_ITEMS = 5

_PERSONALIZATION_INACTIVE_REASON = (
    "Personalization isn't active yet — view a few symbols to get "
    "personalized picks. Showing today's top Radar picks instead."
)

_NO_RADAR_CANDIDATES_FALLBACK_REASON = "No candidates available this cycle."


def compose_digest(snapshot_path: Optional[str] = None) -> DigestPayload:
    """Compose the up-to-5-item weekly digest, following the honest 3-rung
    fallback ladder described in this module's docstring.

    Never raises (CONSTRAINT #6): ``radar_feed``/``find_underrepresented_sectors``
    already degrade honestly on failure, and a ``SymbolViewStore`` failure
    here degrades to an empty (not fabricated) view-history set.
    """
    snapshot = load_snapshot(snapshot_path)

    # radar_feed itself honestly handles snapshot=None/malformed/empty --
    # calling it unconditionally (rather than short-circuiting here) means
    # this module never has to re-type its own copy of that honesty
    # message; it just reuses whatever radar_feed says. limit=_MAX_ITEMS,
    # not some larger number: radar_feed sorts candidates by composite
    # score BEFORE truncating to the limit, and the loop below never looks
    # past the first _MAX_ITEMS anyway (it breaks the instant it accepts
    # that many) -- so a larger limit would only make radar_feed build and
    # immediately discard extra, unused per-item reason strings.
    feed = radar_feed(snapshot, limit=_MAX_ITEMS)
    items = feed.get("items", [])
    if not items:
        return DigestPayload(
            items=[],
            personalization_active=False,
            reason=feed.get("reason") or _NO_RADAR_CANDIDATES_FALLBACK_REASON,
        )

    try:
        store = SymbolViewStore(readonly=True)
        recently_viewed = set(store.get_recently_viewed_symbols(days=14))
    except Exception as exc:
        logger.warning(f"Failed to fetch recently viewed symbols: {exc}")
        recently_viewed = set()

    # CONSTRAINT #4 -- the critical fix: a symbol can only be honestly
    # called "not recently viewed" when the view-tracking log carries at
    # least SOME history to check it against. An empty log (fresh install,
    # or every prior view aged out of the lookback window) means "we have
    # zero information," not "confirmed not viewed" -- so it must never
    # vacuously mint every candidate as "Personalized" just because an
    # empty set contains nothing.
    personalization_active = bool(recently_viewed)

    # snapshot is passed through so find_underrepresented_sectors can read
    # sector data straight off it (zero network/DB cost) instead of
    # re-fetching -- see that module's own docstring.
    underrepresented_sectors = set(find_underrepresented_sectors(snapshot))

    digest_items = []
    for item in items:
        if len(digest_items) >= _MAX_ITEMS:
            break

        symbol = item.get("symbol")
        if not symbol:
            continue

        # `or ""`, not `.get("sector", "")` -- radar_feed always sets the
        # "sector" KEY but its VALUE can be None (not omitted) when a
        # symbol has no resolved sector, so the dict.get default never
        # actually fires for that case (a recurring bug-class this exact
        # repo has hit before). Currently harmless either way since this
        # is only ever used in a truthy check below, but `or ""` is
        # correct regardless of how it's used later.
        sector = item.get("sector") or ""
        reason = item.get("reason", "")

        if personalization_active and symbol not in recently_viewed:
            selection_type = "Personalized"
        elif sector and sector in underrepresented_sectors:
            selection_type = "Sector Gap"
        else:
            selection_type = "Today's Radar"

        # confidence_tier is no longer passed here -- DigestItem.__post_init__
        # (pilots/digest_models.py) now derives and enforces it structurally
        # from selection_type, so there is exactly one place this mapping
        # can ever be defined or drift.
        digest_items.append(DigestItem(
            symbol=symbol,
            reason=reason,
            selection_type=selection_type,
        ))

    return DigestPayload(
        items=digest_items,
        personalization_active=personalization_active,
        reason=None if personalization_active else _PERSONALIZATION_INACTIVE_REASON,
    )
