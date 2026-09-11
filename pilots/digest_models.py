"""Data structures for the Weekly Digest feature.
==============================================

Provides the data transfer objects for the Weekly Digest, ensuring that
selections have required explanations and are bounded in count.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List


@dataclass(frozen=True)
class DigestItem:
    """A single stock selection for the Weekly Digest."""
    symbol: str
    reason: str
    selection_type: str


@dataclass(frozen=True)
class DigestPayload:
    """The full payload for the Weekly Digest, containing up to 5 items."""
    items: List[DigestItem] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        """Validate the maximum item constraint."""
        if len(self.items) > 5:
            raise ValueError(f"DigestPayload cannot contain more than 5 items. Got {len(self.items)}.")
