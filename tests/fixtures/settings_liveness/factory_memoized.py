"""Read form 4, captured variant: the factory's inner function is memoized.

Same shape as factory_fresh.py, but @lru_cache means the first call's value is
frozen for the process lifetime. The classifier must carry the INNER
function's own capture rules onto the factory_param read -- checking only the
outer call site would report this as live-safe.
"""
import functools

from settings import settings


def make_cached_lookup(setting_name):
    @functools.lru_cache(maxsize=1)
    def _lookup():
        return getattr(settings, setting_name, None)

    return _lookup


get_log_level = make_cached_lookup("LOG_LEVEL")
