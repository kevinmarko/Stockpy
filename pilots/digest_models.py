"""Data structures for the Weekly Digest feature.
==============================================

Provides the data transfer objects for the Weekly Digest, ensuring that
selections have required explanations and are bounded in count.

Honesty contract (CONSTRAINT #4, implementation plan §4/§6):
``DigestPayload.personalization_active``/``reason`` and
``DigestItem.confidence_tier`` exist so a consumer (the webapp panel, the
push-alert message) can never render a lower-confidence fallback pick with
the same visual/textual weight as a genuinely personalized one -- see
``pilots.weekly_digest.compose_digest`` for how these are populated.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


@dataclass(frozen=True)
class DigestItem:
    """A single stock selection for the Weekly Digest.

    ``confidence_tier`` is derived mechanically from ``selection_type`` by
    the composer (never set independently, so the two can never drift
    apart): ``"Personalized"`` -> ``"high"`` (the model's own top-ranked
    pick, genuinely not recently viewed), ``"Sector Gap"`` -> ``"medium"``
    (a diagnostic, portfolio-composition-driven claim -- a DIFFERENT kind
    of evidence than a signal-driven pick, per CONSTRAINT #4/plan §6, so it
    is deliberately never blended into the same confidence as
    ``"Personalized"``), ``"Today's Radar"`` -> ``"low"`` (the honest
    fallback when neither of the above honestly applies -- e.g. the
    view-tracking log has no history yet, so "not recently viewed" cannot
    be claimed).
    """
    symbol: str
    reason: str
    selection_type: str       # "Personalized" | "Sector Gap" | "Today's Radar"
    confidence_tier: str      # "high" | "medium" | "low"


@dataclass(frozen=True)
class DigestPayload:
    """The full payload for the Weekly Digest, containing up to 5 items.

    ``personalization_active`` is ``True`` iff the view-tracking log had at
    least one real recorded view this cycle -- ``False`` (never a
    fabricated ``True``) whenever there is no snapshot, no radar
    candidates, or a genuinely empty view log (CONSTRAINT #4: an empty log
    means "we don't know what's been viewed," never "confirmed not
    viewed").

    ``reason`` carries a plain-English explanation whenever ``items`` is
    empty OR ``personalization_active`` is ``False`` -- ``None`` only on
    the fully-normal happy path (personalization active, items populated,
    nothing else degraded).
    """
    items: List[DigestItem] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    personalization_active: bool = False
    reason: Optional[str] = None

    def __post_init__(self):
        """Validate the maximum item constraint."""
        if len(self.items) > 5:
            raise ValueError(f"DigestPayload cannot contain more than 5 items. Got {len(self.items)}.")
