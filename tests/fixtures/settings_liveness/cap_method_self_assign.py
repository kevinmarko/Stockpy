"""Rule: method_self_assign -- POST-CONSTRUCTION capture.

The gap this closes: a regular method (not __init__, not reachable from one,
not memoized) that stores a setting into a long-lived instance attribute had
NO applicable rule and was reported live_safe -- the dangerous direction. Real
instance: data/sentiment_sources.py's CompositeSentimentSource.reset_cycle().

`refresh_deadline` also proves the rule sees through an expression the old
three-hop coercion walk could not: the read is wrapped in float(...) AND then
in a BinOp, so a whitelist-based walk loses it even though the value plainly
lands on self.
"""
import time

from settings import settings


class CycleGuard:
    def __init__(self):
        self._deadline = 0.0
        self._budget = 0

    def refresh_deadline(self):
        # method_self_assign, through float() nested inside a BinOp.
        self._deadline = time.monotonic() + float(
            settings.SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE
        )

    def refresh_budget(self):
        # method_self_assign, plain.
        self._budget = settings.SENTIMENT_MAX_DOCUMENTS_PER_CYCLE

    def describe(self):
        # NOT a capture: read into a local that is returned, not stored.
        return f"lookback={settings.SENTIMENT_INGESTION_LOOKBACK_DAYS}"
