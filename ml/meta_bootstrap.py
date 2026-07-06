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
if a saved model exists, registers it. When no saved model exists it is a
strict, logged no-op — preserving the exact pre-model behavior until
``scripts/train_meta_labelers.py`` has actually produced a pickle.

Safety
------
- Gated behind ``settings.META_LABELING_ENABLED`` (default ``True``). Set to
  ``False`` to disable all meta-label registration regardless of saved models.
- Dead-letter resilient (CONSTRAINT #6): a load/register failure for one
  signal_id is logged and skipped — it NEVER crashes the advisory pipeline.
- No fabricated behavior: a missing model registers nothing (registry stays
  empty for that signal), which the aggregator already treats as ``P=1.0``.
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger("ML.MetaBootstrap")

# The primary signals for which a meta-labeler may be trained/registered.
# Mirrors the meta_labeler_* rows in ml/registry.yaml and the SignalModule.name
# values in signals/timeseries_momentum.py & signals/cross_sectional_momentum.py.
META_LABELED_SIGNAL_IDS: tuple[str, ...] = (
    "timeseries_momentum",
    "cross_sectional_momentum",
)


def bootstrap_meta_registry(
    signal_ids: Optional[tuple[str, ...]] = None,
) -> List[str]:
    """Load and register any trained meta-labelers into ``global_meta_registry``.

    Called ONCE at process startup by both orchestrators. For each ``signal_id``
    it attempts ``MetaLabeler.load_latest()`` and registers the model if present.

    Strict no-op semantics: when no saved model exists for a ``signal_id`` (the
    current state of a fresh checkout), nothing is registered for it and the
    aggregator continues to treat that signal as ``P(correct)=1.0`` — behavior
    is byte-identical to the pre-bootstrap platform.

    Gated behind ``settings.META_LABELING_ENABLED`` (default ``True``). When
    disabled, returns an empty list without touching the registry.

    Dead-letter resilient: a failure to load/register any single model is logged
    and skipped; it never propagates (CONSTRAINT #6).

    Parameters
    ----------
    signal_ids:
        Optional override of the signal ids to attempt. Defaults to
        ``META_LABELED_SIGNAL_IDS``.

    Returns
    -------
    list[str]
        The signal ids that were actually registered this call (empty if none
        had a saved model or the feature is disabled). Returned so callers /
        tests can assert on what was activated without parsing logs.
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
