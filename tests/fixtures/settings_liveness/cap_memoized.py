"""Rule: memoized_singleton -- the result outlives the first call."""
import functools

from settings import settings


@functools.lru_cache(maxsize=None)
def cached_batch_size():
    return settings.FINBERT_BATCH_SIZE


class Loader:
    @functools.cached_property
    def lookback(self):
        return settings.NEWS_LOOKBACK_DAYS
