"""Pilots package — copyable strategy "Pilots" backed by Stockpy's own signal blends.

A **Pilot** is a retail-friendly, copyable strategy defined purely as a weight
vector over Stockpy's existing ``signals/`` modules (``settings.SIGNAL_WEIGHTS``
keys), optionally joined to an honest, PBO/DSR-gated backtest in
``scripts.refresh_validations.STRATEGY_REGISTRY``.

This top-level module re-exports the catalog API so callers can simply do::

    from pilots import Pilot, list_pilots, get_pilot

The catalog is deliberately dependency-light: it imports ONLY from ``settings``,
the stdlib, ``dataclasses`` and ``typing`` — never the heavy calculation engines
(``strategy_engine``, ``processing_engine``, ``forecasting_engine`` …) — so it is
cheap to import on the API read path.
"""
from __future__ import annotations

from pilots.catalog import PILOTS, Pilot, get_pilot, list_pilots

__all__ = ["Pilot", "PILOTS", "list_pilots", "get_pilot"]
