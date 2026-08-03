"""Helper module for cap_cross_module.py's cross_module_init_helper case.

Mirrors db_config.py's engine builders: small, single-purpose, module-level
functions whose reads land in whatever long-lived object the caller builds.
"""
from settings import settings


def build_engine(url=None):
    return {
        "url": url or settings.DATABASE_URL,
        "pool_size": settings.DB_POOL_SIZE,
    }
