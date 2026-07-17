"""pilots/strategy_matrix.py — read the signal-module matrix for the PWA.
================================================================================

Pure reader that assembles the signal-module weight/enablement matrix the
Strategy Matrix screen renders, from already-persisted state only. Powers the
mobile ``GET /strategy/matrix`` endpoint.

Design invariants (identical to the rest of the Pilots read layer —
``pilots/options.py``, ``pilots/run_status.py``):

* **Read-only / persisted-state only** — imports only ``settings`` + stdlib.
  It MUST NOT ``import signals``. ``api/pilots_api.py`` imports this module, and
  ``import signals`` executes ``signals/__init__.py``, which eagerly imports all
  17 signal modules (~700 modules into ``sys.modules``) and fires their
  registration side effects. The AST guard on ``api/pilots_api.py``
  (``tests/test_pilots_api.py``) walks that file only, first-segment-only and
  non-transitively — so ``import signals`` here would PASS the guard while
  defeating its intent (the same trap ``desktop`` is denylisted for). And the
  payoff is nil: ``SignalModule`` exposes only ``name`` / ``required_features``
  — no weight, no description — so everything this endpoint needs already lives
  in ``settings.SIGNAL_WEIGHTS`` and the persisted snapshot.

* **Honesty (CONSTRAINT #4)** — a weight the config doesn't carry is ``None``,
  never a fabricated ``0.0``; ``effective_weight`` is ``None`` (never a guess)
  when regime overrides are active but the run's regime is unknown. Each module
  carries a ``source`` provenance tag so a reader can tell where it came from.

* **Never raises (CONSTRAINT #6)** — a missing/corrupt snapshot degrades to a
  weights-only module list + an honest ``reason``.

Two constants are DUPLICATED from ``signals.aggregator`` (which can't be imported
here) and PINNED against the originals by ``tests/test_pilots_strategy_matrix.py``:
``_MAX_WEIGHT`` (≡ ``MAX_SANE_SIGNAL_WEIGHT``) and ``_resolve_effective_weights``
(≡ ``resolve_regime_weights``).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["strategy_matrix"]

_SNAPSHOT_FILENAME = "state_snapshot.json"

# Duplicated from signals.aggregator (see module docstring) — pinned by tests.
_MAX_WEIGHT: float = 100.0
_REGIME_DEFAULT_KEY: str = "_default"
# Modules structurally pinned to weight 0.0 (they carry information through
# `confidence`, not `score`). settings enforces SIGNAL_WEIGHTS["regime_multiplier"]
# == 0.0; the write endpoint refuses to change it.
_PINNED_ZERO_WEIGHT_MODULES: frozenset[str] = frozenset({"regime_multiplier"})

_NO_SNAPSHOT_REASON = (
    "No state snapshot yet — module list derived from configured SIGNAL_WEIGHTS "
    "only. A module registered in code but never weighted and never run would "
    "not appear here."
)


def _default_snapshot_path() -> Path:
    return settings.OUTPUT_DIR / _SNAPSHOT_FILENAME


def _read_json_object(path: Path) -> Optional[dict]:
    """Load a JSON object from ``path``, or ``None`` (never raises)."""
    try:
        if not path.exists():
            return None
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("strategy_matrix snapshot read failed: %s", exc)
        return None


def _resolve_effective_weights(
    market_regime: str,
    regime_weights: Dict[str, Dict[str, float]],
    default_weights: Dict[str, float],
) -> Dict[str, float]:
    """Pure re-implementation of ``signals.aggregator.resolve_regime_weights``
    (minus its WARNING log). Duplicated because importing ``signals`` costs ~700
    modules on the AST-guarded API import path; pinned against the real function
    by ``tests/test_pilots_strategy_matrix.py``.
    """
    if not regime_weights:
        return dict(default_weights)
    override = regime_weights.get(market_regime) or regime_weights.get(_REGIME_DEFAULT_KEY)
    if not override:
        return dict(default_weights)
    return {**default_weights, **override}


def _snapshot_module_symbol_counts(snapshot: Optional[dict]) -> Dict[str, int]:
    """Map ``module_name -> number of symbols whose score_components carried it``
    in the last run. Empty when there's no snapshot."""
    counts: Dict[str, int] = {}
    if not snapshot:
        return counts
    for sig in snapshot.get("signals") or []:
        if not isinstance(sig, dict):
            continue
        components = sig.get("score_components")
        if not isinstance(components, dict):
            continue
        for name in components:
            counts[name] = counts.get(name, 0) + 1
    return counts


def strategy_matrix(snapshot_path: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the signal-module matrix from ``settings`` + the persisted
    ``output/state_snapshot.json``.

    Returns a dict with ``as_of``, ``market_regime``, ``regime_overrides_active``,
    ``weights_source``, ``modules`` (one row per module — see below), ``disabled``,
    ``max_weight``, and ``reason`` (``None`` unless the snapshot is absent).

    Each ``modules`` row:
      ``name`` / ``weight`` (configured, ``None`` if absent) /
      ``effective_weight`` (regime-resolved; ``None`` when unresolvable) /
      ``effective_weight_regime`` (the regime it was resolved for, or ``None`` —
      also ``None`` when overrides are inactive, i.e. it applies to every regime) /
      ``enabled`` / ``source`` (``"weights"`` | ``"snapshot"`` | ``"both"``) /
      ``contributed_last_run`` / ``symbols_scored`` (``None`` with no snapshot) /
      ``pinned_zero``.
    """
    path = Path(snapshot_path) if snapshot_path else _default_snapshot_path()
    snapshot = _read_json_object(path)

    configured: Dict[str, float] = dict(settings.SIGNAL_WEIGHTS or {})
    disabled: List[str] = list(settings.DISABLED_SIGNAL_MODULES or [])
    disabled_set = set(disabled)
    regime_weights: Dict[str, Dict[str, float]] = dict(settings.REGIME_SIGNAL_WEIGHTS or {})
    overrides_active = bool(regime_weights)

    symbol_counts = _snapshot_module_symbol_counts(snapshot)
    snapshot_modules = set(symbol_counts.keys())

    # Regime for effective-weight resolution. "UNKNOWN"/empty is not a regime we
    # can honestly resolve overrides against.
    raw_regime = str((snapshot or {}).get("market_regime") or "").strip()
    regime: Optional[str] = raw_regime if raw_regime and raw_regime != "UNKNOWN" else None

    # Resolve effective weights ONCE. When overrides are inactive, effective ==
    # configured for every regime (regime label is meaningless -> None). When
    # active but the regime is unknown, we cannot resolve honestly -> None.
    resolved: Optional[Dict[str, float]]
    eff_regime: Optional[str]
    if not overrides_active:
        resolved = dict(configured)
        eff_regime = None
    elif regime is not None:
        resolved = _resolve_effective_weights(regime, regime_weights, configured)
        eff_regime = regime
    else:
        resolved = None
        eff_regime = None

    all_names = sorted(set(configured.keys()) | snapshot_modules)
    modules: List[Dict[str, Any]] = []
    for name in all_names:
        in_weights = name in configured
        in_snapshot = name in snapshot_modules
        if in_weights and in_snapshot:
            source = "both"
        elif in_weights:
            source = "weights"
        else:
            source = "snapshot"

        effective_weight = resolved.get(name) if resolved is not None else None

        modules.append(
            {
                "name": name,
                "weight": configured.get(name) if in_weights else None,
                "effective_weight": effective_weight,
                "effective_weight_regime": eff_regime,
                "enabled": name not in disabled_set,
                "source": source,
                "contributed_last_run": in_snapshot,
                # With a snapshot, a module not in any score_components was scored
                # on 0 symbols this run (0, not unknown); None only when there is
                # no snapshot at all.
                "symbols_scored": symbol_counts.get(name, 0) if snapshot is not None else None,
                "pinned_zero": name in _PINNED_ZERO_WEIGHT_MODULES,
            }
        )

    return {
        "as_of": (snapshot or {}).get("timestamp"),
        "market_regime": (snapshot or {}).get("market_regime"),
        "regime_overrides_active": overrides_active,
        "weights_source": "running_process_settings",
        "modules": modules,
        "disabled": disabled,
        "max_weight": _MAX_WEIGHT,
        "reason": None if snapshot is not None else _NO_SNAPSHOT_REASON,
    }
