"""
cache/__init__.py
=================
Disk-persisted, cadence-aware cache for the InvestYo Quant Platform.

Re-exports all public symbols from cache_store so callers can do:
    from cache import Cache, Cadence, cached, get_default_cache
"""

from cache.cache_store import (
    Cache,
    CacheEntry,
    Cadence,
    CADENCE_TTL,
    CADENCE_REGISTRY,
    cached,
    get_default_cache,
)

__all__ = [
    "Cache",
    "CacheEntry",
    "Cadence",
    "CADENCE_TTL",
    "CADENCE_REGISTRY",
    "cached",
    "get_default_cache",
]
