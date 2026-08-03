"""Rule: global_assign -- a read stored into a module global from a function."""
from settings import settings

_CACHED_HISTORY_DAYS = None


def warm_cache():
    global _CACHED_HISTORY_DAYS
    _CACHED_HISTORY_DAYS = settings.SNAPSHOT_HISTORY_DAYS
