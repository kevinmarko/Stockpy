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

**Version registry (``version_hash`` / ``last_modified`` per module, backlog
item #13a)** — a sha256-prefix fingerprint + file mtime for each module's
``signals/<name>.py`` file, read directly off disk (``hashlib`` + ``pathlib``
only, no import of the ``signals`` package). This deliberately does NOT reuse
``gui.strategy_registry.list_strategy_versions`` — that function's default
code path does ``from signals.registry import global_registry`` / ``import
signals`` to enumerate the live registry, which is exactly the ~700-module
trap this module's docstring (above) warns about. The fingerprint format
(sha256, first 12 hex chars) mirrors that function's for operator-visible
consistency with the desktop Strategy Matrix tab, but the computation here is
independent and reads only the file bytes + mtime — never the module's own
Python code.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["strategy_matrix"]

_SNAPSHOT_FILENAME = "state_snapshot.json"
_HASH_PREFIX_LEN = 12

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


def _default_signals_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "signals"


def _module_fingerprint(signals_dir: Path, name: str) -> "tuple[Optional[str], Optional[str]]":
    """Return ``(version_hash, last_modified)`` for ``<signals_dir>/<name>.py``.

    ``version_hash`` is a sha256 prefix (``_HASH_PREFIX_LEN`` hex chars);
    ``last_modified`` is an ISO-8601 UTC timestamp string. Both are ``None``
    (never fabricated — CONSTRAINT #4) when the module has no corresponding
    file on disk (e.g. an orphan snapshot-only name, a typo'd config key, or
    a module defined without its own file) or the read fails for any reason
    (CONSTRAINT #6 — a hashing failure degrades silently, it never raises)."""
    candidate = signals_dir / f"{name}.py"
    try:
        if not candidate.is_file():
            return None, None
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()[:_HASH_PREFIX_LEN]
        mtime = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc).isoformat()
        return digest, mtime
    except OSError as exc:  # noqa: BLE001 — dead-letter, never fatal
        logger.debug("strategy_matrix: fingerprint read failed for %s: %s", name, exc)
        return None, None


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


def _coerce_float(value: Any) -> Optional[float]:
    """Coerce *value* to a finite float, or ``None`` when not possible.

    Duplicated from ``pilots.scoring._coerce_float`` (see module docstring for
    why this file can't import ``pilots.scoring`` — actually it can, that
    restriction is only for ``signals``/heavy engines; this is duplicated
    purely to keep this module's dependency surface exactly what its docstring
    promises: ``settings`` + stdlib only). NaN/inf collapse to ``None``, never
    a fabricated ``0.0`` — a real ``0.0`` (e.g. a MetaLabeler hard gate) is
    preserved as-is (CONSTRAINT #4)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return f


_META_LABEL_BIN_COUNT = 20
_NO_META_LABEL_REASON = (
    "No meta_label_composite values in this snapshot — either no snapshot "
    "exists yet, or it was written by a pipeline path that doesn't persist "
    "the sizing decomposition. Run the pipeline to populate it."
)


def _meta_label_distribution(snapshot: Optional[dict]) -> Dict[str, Any]:
    """Portfolio-wide distribution of ``meta_label_composite`` across every
    symbol in the snapshot — the read-only data behind the Strategy Matrix
    screen's "Meta-Label Confidence Distribution" section (ports
    ``gui/panels/strategy_matrix.py::_render_meta_label_distribution``).

    A symbol contributes to ``values``/the histogram only when its snapshot
    entry carries a real, finite ``meta_label_composite`` — an absent key or a
    non-numeric value is counted in ``missing``, never coerced into a
    fabricated ``1.0`` (CONSTRAINT #4; the legacy panel's ``or 1.0`` did
    exactly this, which also silently destroyed every genuine ``0.0`` before
    it could be counted — ``n_gated`` here does not repeat that bug).

    Bins are FIXED over ``[0, 1]`` (20 bins, matching the legacy chart's
    ``nbins=20``) rather than auto-ranged over the data — a deliberate
    deviation: auto-ranging a degenerate single-value dataset (every symbol at
    exactly 1.0, the common case with no MetaLabelers registered) produces a
    meaningless single-bar chart with no axis context, whereas fixed [0,1]
    bins let that same case render as an honest spike in the top bin of a
    full-width axis.

    ``all_unity`` mirrors the legacy panel's explicit "this is correct, not a
    bug" branch (tolerance ``1e-9``): with no MetaLabelers registered in
    ``ml.meta_labeling.global_meta_registry``, every module's
    ``meta_label_proba`` defaults to 1.0 (a multiplicative no-op), so a single
    spike at 1.0 is the CORRECT rendering of the current, pre-Stage-4 state.
    """
    values: List[float] = []
    missing = 0
    for sig in (snapshot or {}).get("signals") or []:
        if not isinstance(sig, dict):
            continue
        if "meta_label_composite" not in sig:
            missing += 1
            continue
        v = _coerce_float(sig.get("meta_label_composite"))
        if v is None:
            missing += 1
            continue
        values.append(v)

    bin_width = 1.0 / _META_LABEL_BIN_COUNT
    bins: List[Dict[str, Any]] = [
        {"lo": round(i * bin_width, 4), "hi": round((i + 1) * bin_width, 4), "count": 0}
        for i in range(_META_LABEL_BIN_COUNT)
    ]
    n_gated = 0
    for v in values:
        if v == 0.0:
            n_gated += 1
        # Clamp into [0, 1] for bin placement — a value outside that range is
        # still a real, counted value (min/max below reflect the true range),
        # it just lands in the nearest edge bin rather than being dropped.
        idx = min(_META_LABEL_BIN_COUNT - 1, max(0, int(min(max(v, 0.0), 1.0) / bin_width)))
        bins[idx]["count"] += 1

    return {
        "bins": bins,
        "count": len(values),
        "missing": missing,
        "n_gated": n_gated,
        "all_unity": bool(values) and all(abs(v - 1.0) < 1e-9 for v in values),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "min_confidence": settings.META_LABEL_MIN_CONFIDENCE,
        "reason": None if values else _NO_META_LABEL_REASON,
    }


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


def strategy_matrix(
    snapshot_path: Optional[str] = None,
    *,
    signals_dir: Optional[Path] = None,
) -> Dict[str, Any]:
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
      ``pinned_zero`` / ``version_hash`` (sha256 prefix of ``signals/<name>.py``,
      ``None`` when the module has no file on disk) / ``last_modified`` (that
      file's mtime, ISO-8601 UTC; ``None`` alongside ``version_hash``).

    ``signals_dir`` overrides where module fingerprints are read from (test
    injection only — production callers always get the real ``signals/`` dir).
    """
    path = Path(snapshot_path) if snapshot_path else _default_snapshot_path()
    snapshot = _read_json_object(path)
    resolved_signals_dir = signals_dir if signals_dir is not None else _default_signals_dir()

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
        version_hash, last_modified = _module_fingerprint(resolved_signals_dir, name)

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
                "version_hash": version_hash,
                "last_modified": last_modified,
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
        "meta_label": _meta_label_distribution(snapshot),
    }
