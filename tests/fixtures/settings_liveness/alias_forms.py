"""Alias resolution: the singleton is bound under 18 distinct local names
across the real tree, so grepping `settings\\.` is not viable.

Both import shapes must resolve, including function-local imports.
"""
from settings import settings as _s
import settings as _settings_mod


def rate_limit():
    return _s.MAX_ORDER_RATE_PER_MIN


def heat_cap():
    return _settings_mod.settings.MAX_PORTFOLIO_HEAT


def local_import_alias():
    from settings import settings as _late
    return _late.RISK_FREE_RATE
