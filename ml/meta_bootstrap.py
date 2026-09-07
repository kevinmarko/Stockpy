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
# NOTE: ml/forecast_backfill.py's AgenticForecastBackfiller ALSO trains its
# own multi-horizon diagnostic models (keys like "timeseries_momentum_10d")
# and persists them to ml/models/backfill_diag_<model_key>.pkl -- those raw,
# untyped RandomForestClassifier pickles are NEVER added to
# META_LABELED_SIGNAL_IDS above and never touch the live gate directly.
# Instead, ml/forecast_backfill_registry_bridge.py wraps ONE operator-chosen
# horizon per opted-in signal as a real MetaLabeler, evaluates it via genuine
# CPCV/DSR/PBO, and (if it clears the deployability gate AND the
# LIVE_ROW_FEATURE_WHITELIST feature-compatibility check below) registers it
# under a SEPARATE registry key, meta_labeler_backfill_<signal_id> -- see the
# second loop in bootstrap_meta_registry() below. This lets the Forecast
# Backfill screen (GET/POST /pilots/forecast_backfill, the webapp Forecast
# Backfill screen) extend live meta-label coverage beyond the two AFML-
# trained signals above, gated behind settings.META_LABELING_BACKFILL_
# BRIDGE_ENABLED (default False) and an explicit per-signal opt-in
# (settings.META_LABELING_BACKFILL_ELIGIBLE_SIGNALS, default empty) -- see
# docs/plans/FORECAST_BACKFILL_PLAN.md for the full design. As of the 2026-09
# feature-widening fix, the feature-compatibility gate below passes for all 6
# eligible signals -- the live row now computes every technical feature they
# declare. Live promotion is still gated separately by DSR/PBO deployability
# (_is_deployable() below), which is a genuinely measured, independent check,
# not a rubber stamp -- do not read a passing feature-compatibility check as
# "this signal is live".



def _is_deployable(model_key: str, registry_data: Dict[str, Any]) -> tuple[bool, Optional[float], Optional[float]]:
    """Read ``model_key``'s ``deployable``/``cpcv_dsr``/``pbo``
    fields out of an already-loaded ``ml/registry.yaml`` dict.

    Fails CLOSED: a missing registry, a missing row for this model_key, or a
    missing/non-bool ``deployable`` field all resolve to ``(False, None, None)``
    — a saved pickle with no accompanying deployability record is not evidence
    the model is fine; the absence of proof is treated as proof of absence.
    """
    row = (registry_data.get("models") or {}).get(model_key) or {}
    deployable = row.get("deployable")
    return (deployable is True, row.get("cpcv_dsr"), row.get("pbo"))


# Hand-copied from strategy_engine.py::evaluate_security()'s real per-ticker
# `row = pd.Series({...})` construction (the exact feature set a MetaLabeler
# is queried with at live inference, via SignalAggregator -> MetaLabelerRegistry
# .get_proba()) plus the aggregator's own appended "primary_score" key. There
# is no dynamic introspection path without executing the strategy engine
# itself, so this MUST be kept in sync by hand if that row construction ever
# changes -- a stale whitelist here would either wrongly refuse a genuinely
# compatible model or (worse) wrongly admit one whose features aren't really
# all present, silently zero-filling at inference (see
# MetaLabeler._prepare_X()). tests/test_train_meta_labelers.py cross-checks
# a live SignalAggregator row shape against this set.
LIVE_ROW_FEATURE_WHITELIST: frozenset[str] = frozenset([
    "forecast_price",
    "trend_strength",
    "atr",
    "macd_line",
    "macd_signal",
    "aroon_osc",
    "rsi",
    "sortino_ratio",
    "max_drawdown",
    "relative_strength",
    "garch_vol",
    "GARCH_Vol",
    "edge_ratio",
    "chandelier_long",
    "chandelier_short",
    "current_price",
    "Close",
    "ticker",
    "sector",
    "roc_12m",
    "ROC_12M",
    "SMA_200",
    "RSI_2",
    "SMA_5",
    # Added so check_feature_compatibility() can genuinely resolve the 6
    # Forecast-Backfill-eligible signals' declared meta_label_features
    # (timeseries_momentum, cross_sectional_momentum, rsi2_mean_reversion,
    # sector_quality_rank, vrp_premium_selling, options_flow_sentiment) --
    # previously always refused, by construction, regardless of DSR/PBO,
    # since these 9 keys were genuinely absent from strategy_engine.py's
    # `row` construction (the source of truth this whitelist mirrors) until
    # this change added them there too.
    "Vol_20",
    "Vol_50",
    "Vol_Ratio",
    "RSI_14",
    "MACD",
    "MACD_Signal",
    # roc_6m/ROC_6M: strategy_engine.py's row carries both the lowercase and
    # uppercase key for this value (mirroring the pre-existing roc_12m/
    # ROC_12M duplicate-casing pair a few lines above), so both must be
    # whitelisted for tests/test_train_meta_labelers.py::
    # test_live_row_feature_whitelist_matches_the_real_live_row to hold.
    "roc_6m",
    "ROC_6M",
    "ROC_5",
    "ROC_20",
    # Appended by signals/aggregator.py's aggregate() (feat_row["primary_score"]
    # = output.score) AFTER strategy_engine.py's own `row` is built, immediately
    # before the meta-labeler is queried -- genuinely part of the live feature
    # row, not part of `row` itself. Both existing AFML meta-labelers declare
    # this as a training feature (see ml/registry.yaml's
    # meta_labeler_timeseries_momentum/meta_labeler_cross_sectional_momentum
    # `features` lists) -- omitting it here was a real gap, caught by
    # tests/test_train_meta_labelers.py::test_live_row_feature_whitelist_matches_the_real_live_row.
    "primary_score",
])

def check_feature_compatibility(feature_names: List[str]) -> tuple[bool, List[str]]:
    missing = [f for f in feature_names if f not in LIVE_ROW_FEATURE_WHITELIST]
    return len(missing) == 0, missing


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

        deployable, dsr, pbo = _is_deployable(f"meta_labeler_{signal_id}", registry_data)
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

    if getattr(settings, "META_LABELING_BACKFILL_BRIDGE_ENABLED", False):
        eligible_signals = getattr(settings, "META_LABELING_BACKFILL_ELIGIBLE_SIGNALS", [])
        for signal_id in eligible_signals:
            if signal_id in registered:
                continue
            
            try:
                labeler = MetaLabeler.load_latest(signal_id, prefix="backfill_meta")
            except Exception as exc:
                logger.warning(
                    "bootstrap_meta_registry: failed to load backfill meta-labeler for %r "
                    "(%s) — skipping.", signal_id, exc,
                )
                continue

            if labeler is None:
                continue

            deployable, dsr, pbo = _is_deployable(f"meta_labeler_backfill_{signal_id}", registry_data)
            if not deployable:
                logger.warning(
                    "bootstrap_meta_registry: backfill meta-labeler for %r exists on disk "
                    "but is NOT deployable per ml/registry.yaml "
                    "(cpcv_dsr=%s, pbo=%s) — leaving unregistered.",
                    signal_id, dsr, pbo,
                )
                continue
            
            is_compat, missing_features = check_feature_compatibility(getattr(labeler, "_feature_names", []))
            if not is_compat:
                logger.warning(
                    "bootstrap_meta_registry: backfill meta-labeler for %r is deployable "
                    "but its declared features are incompatible with the live row schema "
                    "(missing: %s) — leaving unregistered.",
                    signal_id, missing_features
                )
                continue

            try:
                global_meta_registry.register(labeler)
                registered.append(signal_id)
                logger.info(
                    "bootstrap_meta_registry: registered BACKFILL meta-labeler for %r "
                    "(trained on %d samples).",
                    signal_id, getattr(labeler, "_n_train_samples", 0),
                )
            except Exception as exc:
                logger.warning(
                    "bootstrap_meta_registry: failed to register backfill meta-labeler for "
                    "%r (%s) — skipping.", signal_id, exc,
                )
                continue

    if registered:
        logger.info(
            "bootstrap_meta_registry: %d meta-labeler(s) active: %s",
            len(registered), ", ".join(registered),
        )
    return registered
