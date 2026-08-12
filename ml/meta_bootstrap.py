"""
InvestYo Quant Platform - Meta-Labeler Runtime Bootstrap
========================================================
THE runtime wiring that activates trained meta-labelers.

Background
----------
``ml.meta_labeling.global_meta_registry`` is the singleton the
``signals.aggregator.SignalAggregator`` queries per cycle to decide whether a
primary signal's ``meta_hard_gate`` should fire (zeroing that signal's Kelly
Target when ``P(primary_signal_correct) < settings.META_LABEL_MIN_CONFIDENCE``).

However, prior to this module, ``global_meta_registry.register()`` was NEVER
called anywhere at runtime — only in tests and the Gravity suite. That meant
even a fully-trained meta-labeler pickle sitting in ``ml/models/`` would never
activate the gate: the registry stayed empty for the life of every real
process, so ``get_proba()`` always returned the neutral ``1.0``.

``bootstrap_meta_registry()`` closes that gap. It is called ONCE at startup by
both entry points (``main.py`` and ``main_orchestrator.py``). For each
configured ``signal_id`` it attempts ``MetaLabeler.load_latest(signal_id)`` and,
if a saved model exists AND ``ml/registry.yaml`` marks it ``deployable: true``,
registers it. When no saved model exists, or a saved model exists but isn't
deployable, it is a logged no-op — preserving the exact pre-model behavior
until ``scripts/train_meta_labelers.py`` has actually produced a pickle that
clears the gate.

Safety
------
- Gated behind ``settings.META_LABELING_ENABLED`` (default ``True``). Set to
  ``False`` to disable all meta-label registration regardless of saved models.
- **Deployability-gated (added after a real gap was found in practice):** a
  saved pickle existing on disk is NOT sufficient to activate it. This
  function reads ``ml/registry.yaml``'s ``meta_labeler_<signal_id>.deployable``
  field (the same ``DSR > 0.95 AND PBO < 0.5`` gate used everywhere else in
  this codebase — ``ml.registry_io.compute_deployable``) and refuses to
  register a model that isn't ``true``. Before this, ``MetaLabeler.load_latest()``
  finding a file was the ONLY condition checked — a freshly-trained model
  that had been honestly evaluated as statistically indistinguishable from
  noise (or, for one real signal_id, a consistently NEGATIVE out-of-sample
  Sharpe) would have silently started dampening live position sizing via the
  meta_hard_gate the moment its pickle existed, regardless of how badly it
  failed CPCV. Fails CLOSED: if the registry file, or this signal_id's row
  within it, can't be read at all, the model is treated as non-deployable
  rather than assumed fine (a missing deployability record is not evidence
  of quality).
- Dead-letter resilient (CONSTRAINT #6): a load/register failure for one
  signal_id is logged and skipped — it NEVER crashes the advisory pipeline.
- No fabricated behavior: a missing model registers nothing (registry stays
  empty for that signal), which the aggregator already treats as ``P=1.0``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ML.MetaBootstrap")

# The primary signals for which a meta-labeler may be trained/registered.
# Mirrors the meta_labeler_* rows in ml/registry.yaml and the SignalModule.name
# values in signals/timeseries_momentum.py & signals/cross_sectional_momentum.py.
META_LABELED_SIGNAL_IDS: tuple[str, ...] = (
    "timeseries_momentum",
    "cross_sectional_momentum",
)
# NOTE: ml/forecast_backfill.py's AgenticForecastBackfiller trains its own
# multi-horizon models (keys like "TSMOM_10d"/"CSMOM_90d") and persists them
# to ml/models/meta_<model_key>.pkl. Those are NOT MetaLabeler instances (they
# are raw sklearn/lightgbm classifiers) and their model_key never matches a
# live SignalModule.name — MetaLabeler.load_latest() globs
# meta_<signal_id>_<stamp>.pkl and requires the pickle to be a MetaLabeler, so
# they cannot be (and must not be) added here: doing so would silently no-op
# (file never found) or crash the load (wrong pickle type), and even if both
# were fixed, SignalAggregator.aggregate() only ever queries the registry
# with the two real signal_ids above, so a "TSMOM_10d" entry would just sit
# unused. The multi-horizon backfill is a standalone research/diagnostic
# engine (see docs/plans/FORECAST_BACKFILL_PLAN.md) — surfaced via
# GET /pilots/forecast_backfill and the webapp Forecast Backfill screen, not
# wired into live position sizing.



def _is_deployable(signal_id: str, registry_data: Dict[str, Any]) -> tuple[bool, Optional[float], Optional[float]]:
    """Read ``meta_labeler_<signal_id>``'s ``deployable``/``cpcv_dsr``/``pbo``
    fields out of an already-loaded ``ml/registry.yaml`` dict.

    Fails CLOSED: a missing registry, a missing row for this signal_id, or a
    missing/non-bool ``deployable`` field all resolve to ``(False, None, None)``
    — a saved pickle with no accompanying deployability record is not evidence
    the model is fine; the absence of proof is treated as proof of absence.
    """
    row = (registry_data.get("models") or {}).get(f"meta_labeler_{signal_id}") or {}
    deployable = row.get("deployable")
    return (deployable is True, row.get("cpcv_dsr"), row.get("pbo"))


def bootstrap_meta_registry(
    signal_ids: Optional[tuple[str, ...]] = None,
    registry_path: Optional[Path] = None,
) -> List[str]:
    """Load and register any trained, DEPLOYABLE meta-labelers into
    ``global_meta_registry``.

    Called ONCE at process startup by both orchestrators. For each ``signal_id``
    it attempts ``MetaLabeler.load_latest()`` and, if a pickle exists, checks
    ``ml/registry.yaml``'s ``meta_labeler_<signal_id>.deployable`` field before
    registering it — a saved model that failed the DSR>0.95/PBO<0.5 gate is
    left unregistered exactly like a model that was never trained at all (see
    module docstring's Safety section for why this check exists).

    Strict no-op semantics: when no saved model exists for a ``signal_id``, or
    a saved model exists but isn't deployable, nothing is registered for it
    and the aggregator continues to treat that signal as ``P(correct)=1.0`` —
    behavior is byte-identical to the pre-bootstrap platform.

    Gated behind ``settings.META_LABELING_ENABLED`` (default ``True``). When
    disabled, returns an empty list without touching the registry.

    Dead-letter resilient: a failure to load/register any single model is logged
    and skipped; it never propagates (CONSTRAINT #6).

    Parameters
    ----------
    signal_ids:
        Optional override of the signal ids to attempt. Defaults to
        ``META_LABELED_SIGNAL_IDS``.
    registry_path:
        Optional override of the ``ml/registry.yaml`` path read for the
        deployability check. Defaults to ``ml.registry_io``'s own module
        default (the real registry file). Tests pass an isolated temp copy
        here rather than monkeypatching a private module constant.

    Returns
    -------
    list[str]
        The signal ids that were actually registered this call (empty if none
        had a saved AND deployable model, or the feature is disabled).
        Returned so callers / tests can assert on what was activated without
        parsing logs.
    """
    # Lazy imports (mirror how the repo lazy-imports HistoricalStore) to keep
    # module import cheap and avoid any circular-import risk between the ml,
    # signals, and settings layers at load time.
    try:
        from settings import settings  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover — settings import is fundamental
        logger.warning(
            "bootstrap_meta_registry: could not import settings (%s) — "
            "skipping meta-label registration.", exc,
        )
        return []

    if not getattr(settings, "META_LABELING_ENABLED", True):
        logger.info(
            "bootstrap_meta_registry: META_LABELING_ENABLED is False — "
            "no meta-labelers registered (aggregator behaves as P=1.0)."
        )
        return []

    ids = signal_ids if signal_ids is not None else META_LABELED_SIGNAL_IDS

    try:
        from ml.meta_labeling import MetaLabeler, global_meta_registry  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "bootstrap_meta_registry: could not import ml.meta_labeling (%s) — "
            "skipping meta-label registration.", exc,
        )
        return []

    try:
        from ml.registry_io import load_registry  # noqa: PLC0415
        registry_data = load_registry(registry_path)
    except Exception as exc:
        # Fail closed (see _is_deployable's docstring): an unreadable registry
        # means every signal_id below resolves to non-deployable, not "assume
        # it's fine".
        logger.warning(
            "bootstrap_meta_registry: could not load ml/registry.yaml (%s) — "
            "treating every meta-labeler as non-deployable.", exc,
        )
        registry_data = {}

    registered: List[str] = []
    for signal_id in ids:
        try:
            labeler = MetaLabeler.load_latest(signal_id)
        except Exception as exc:
            # A corrupt/incompatible pickle must not crash startup.
            logger.warning(
                "bootstrap_meta_registry: failed to load meta-labeler for %r "
                "(%s) — skipping.", signal_id, exc,
            )
            continue

        if labeler is None:
            # Strict no-op path: no saved model yet. This is the CURRENT state
            # of the platform and is intentionally silent-at-INFO (not a warning)
            # so a fresh checkout produces no alarming log noise.
            logger.info(
                "bootstrap_meta_registry: no saved meta-labeler for %r — "
                "leaving unregistered (signal treated as P=1.0).", signal_id,
            )
            continue

        deployable, dsr, pbo = _is_deployable(signal_id, registry_data)
        if not deployable:
            logger.warning(
                "bootstrap_meta_registry: meta-labeler for %r exists on disk "
                "(ml/models/) but is NOT deployable per ml/registry.yaml "
                "(cpcv_dsr=%s, pbo=%s) — leaving unregistered (signal treated "
                "as P=1.0). Re-run scripts/train_meta_labelers.py and confirm "
                "deployable:true before this model can activate.",
                signal_id, dsr, pbo,
            )
            continue

        try:
            global_meta_registry.register(labeler)
            registered.append(signal_id)
            logger.info(
                "bootstrap_meta_registry: registered meta-labeler for %r "
                "(trained on %d samples).",
                signal_id, getattr(labeler, "_n_train_samples", 0),
            )
        except Exception as exc:
            logger.warning(
                "bootstrap_meta_registry: failed to register meta-labeler for "
                "%r (%s) — skipping.", signal_id, exc,
            )
            continue

    if registered:
        logger.info(
            "bootstrap_meta_registry: %d meta-labeler(s) active: %s",
            len(registered), ", ".join(registered),
        )
    return registered
