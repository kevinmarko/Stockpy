"""
Decision context for pipeline paper orders (telemetry only).

``main_orchestrator._execute_broker_orders`` attaches the dict built here to
``OrderIntent.decision_context`` AFTER the order's trading fields are final.
``FMPPaperBroker`` turns it into extra ``PaperAccountStore.apply_fill`` keyword
arguments so that:

* an opening fill writes a ``paper_entry_snapshots`` row (score, regime,
  forecast, sizing inputs, rationale), and
* a closing fill records the real exit trigger as ``close_reason``
  (``signal_risk_reduce`` etc.) plus ``paper_closed_trades.exit_context_json``.

Rules:

* Nothing here influences sizing, the risk gate, the kill switch or
  ``make_client_order_id``. ``OrderManager`` never reads the context.
* CONSTRAINT #4: every value comes from the row the decision used. Missing,
  NaN, inf or placeholder values become ``None``, never ``0.0``.
* This is observability, so it fails open for the order: every public
  function here catches its own errors, and callers wrap them again.
* The snapshot's ``conviction`` column stays ``None`` for pipeline trades:
  the strategy engine has no 0-1 conviction. The advisory engine's
  same-cycle conviction is kept only inside ``key_indicators_json`` under
  ``advisory_conviction_same_cycle`` so the retrospective calibration never
  silently measures a different engine.

Stdlib only (no pandas/settings import) so it stays cheap to import and test.
``row`` is any mapping with ``.get`` (a pandas row works).
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

CONTEXT_SCHEMA_VERSION = 1

# Must equal main_orchestrator.EXIT_SIGNALS (pinned by a test).
EXIT_SIGNAL_CLOSE_REASONS: Dict[str, str] = {
    "SELL": "signal_sell",
    "TRIM": "signal_trim",
    "RISK REDUCE": "signal_risk_reduce",
    "AVOID": "signal_avoid",
}

_ENTRY_JSON_BUDGET = 8000
_EXIT_JSON_BUDGET = 4000
_MAX_DEPTH = 4


def _get(row: Any, key: str) -> Any:
    if row is None:
        return None
    try:
        return row.get(key)
    except Exception:  # noqa: BLE001 -- exotic row object: treat as missing
        return None


def _finite_or_none(value: Any) -> Optional[float]:
    """Finite float or None. Bools and non-numeric values are None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _positive_or_none(value: Any) -> Optional[float]:
    out = _finite_or_none(value)
    return out if out is not None and out > 0 else None


def _str_or_none(value: Any, max_len: int) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        text = str(value).strip()
    except Exception:  # noqa: BLE001
        return None
    if not text or text.lower() in ("nan", "none", "n/a"):
        return None
    return text[:max_len]


def _bool_or_none(value: Any) -> Optional[bool]:
    # Only a real bool says anything (numpy bool included); NaN/absent is unknown.
    if isinstance(value, bool):
        return value
    if type(value).__name__ == "bool_":
        return bool(value)
    return None


def _json_safe(obj: Any, depth: int = 0) -> Any:
    """Convert to JSON-safe python. NaN/inf -> None; unknown objects -> str."""
    if depth > _MAX_DEPTH:
        return None
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if type(obj).__name__ == "bool_":
        return bool(obj)
    if hasattr(obj, "item") and not isinstance(obj, (dict, list, tuple)):
        try:
            return _json_safe(obj.item(), depth + 1)
        except Exception:  # noqa: BLE001
            pass
    if isinstance(obj, Mapping):
        return {str(k): _json_safe(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v, depth + 1) for v in obj]
    try:
        return str(obj)[:500]
    except Exception:  # noqa: BLE001
        return None


def _dumps_within_budget(payload: Dict[str, Any], budget: int, drop_order: tuple) -> str:
    """Serialise ``payload``; drop the keys in ``drop_order`` until it fits."""
    data = dict(payload)
    text = json.dumps(data, sort_keys=True, allow_nan=False)
    for key in drop_order:
        if len(text) <= budget:
            break
        if key in data and data[key] is not None:
            data[key] = None
            data["context_status"] = "truncated"
            text = json.dumps(data, sort_keys=True, allow_nan=False)
    if len(text) > budget:
        # Last resort: keep only the scalar header so the JSON stays valid.
        minimal = {
            "context_schema_version": CONTEXT_SCHEMA_VERSION,
            "context_status": "truncated",
        }
        text = json.dumps(minimal, sort_keys=True, allow_nan=False)
    return text


def exit_close_reason(signal: Any) -> Optional[str]:
    """``signal_<name>`` for an EXIT_SIGNALS member, else None (store keeps 'flatten')."""
    if signal is None:
        return None
    try:
        key = str(signal).strip().upper()
    except Exception:  # noqa: BLE001
        return None
    return EXIT_SIGNAL_CLOSE_REASONS.get(key)


def _advisory_conviction(row: Any) -> Optional[float]:
    # AdvisoryOverlayStep blank-fills Advisory_Conviction with 0.0 for symbols
    # it never evaluated (Advisory_Action == ""). That 0.0 is a placeholder,
    # not a measurement, so only trust the value when the action is real.
    action = _str_or_none(_get(row, "Advisory_Action"), 50)
    if action is None:
        return None
    value = _finite_or_none(_get(row, "Advisory_Conviction"))
    if value is None or value < 0.0 or value > 1.0:
        return None
    return value


def _status(values: Dict[str, Any]) -> str:
    return "ok" if all(v is not None for v in values.values()) else "partial"


def build_entry_context(
    row: Any,
    *,
    sizing_source: str,
    effective_weight: Any,
    equity: Any,
    price: Any,
    macro_dto: Any = None,
) -> Dict[str, Any]:
    """Entry context for a pipeline BUY. Never raises."""
    try:
        score = _finite_or_none(_get(row, "Score"))
        macro_regime = _str_or_none(_get(row, "Macro Status"), 50)
        if macro_regime is None and macro_dto is not None:
            macro_regime = _str_or_none(getattr(macro_dto, "market_regime", None), 50)
        raw_forecast = _positive_or_none(_get(row, "Forecast_30"))
        rationale = _str_or_none(_get(row, "Actionable Advice Signal"), 2000)
        source = _str_or_none(sizing_source, 20) or "unknown"

        core = {
            "action_signal": _str_or_none(_get(row, "Action Signal"), 30),
            "score": score,
            "kelly_target_row": _finite_or_none(_get(row, "Kelly Target")),
            "effective_weight": _finite_or_none(effective_weight),
            "price_at_decision": _positive_or_none(price),
            "equity_at_decision": _positive_or_none(equity),
            "macro_status": macro_regime,
            "forecast_30": raw_forecast,
        }
        indicators: Dict[str, Any] = dict(core)
        indicators.update({
            "context_schema_version": CONTEXT_SCHEMA_VERSION,
            "context_status": _status(core),
            "sizing_source": source,
            "kelly_target_pre_regime": _finite_or_none(_get(row, "Kelly_Target_Pre_Regime")),
            "kelly_target_post_regime": _finite_or_none(_get(row, "Kelly_Target_Post_Regime")),
            "regime_multiplier": _finite_or_none(_get(row, "Regime_Multiplier")),
            "meta_label_composite": _finite_or_none(_get(row, "Meta_Label_Composite")),
            "sizing_binding_constraint": _str_or_none(_get(row, "Sizing_Binding_Constraint"), 50),
            "hmm_regime_state": _json_safe(_get(row, "HMM_Regime_State")),
            "hmm_risk_on_probability": _finite_or_none(_get(row, "HMM_Risk_On_Probability")),
            "dual_momentum_signal": _str_or_none(_get(row, "DualMomentum_Signal"), 30),
            "garch_vol": _finite_or_none(_get(row, "GARCH_Vol")),
            "forecast_30_pct": _finite_or_none(_get(row, "Forecast_30_Pct")),
            "forecast_30_is_fallback": _bool_or_none(_get(row, "Forecast_30_Is_Fallback")),
            "advisory_action": _str_or_none(_get(row, "Advisory_Action"), 50),
            "advisory_conviction_same_cycle": _advisory_conviction(row),
            "advisory_rationale": _str_or_none(_get(row, "Advisory_Rationale"), 500),
            "score_components": _json_safe(_get(row, "Score_Components")),
        })
        key_indicators_json = _dumps_within_budget(
            indicators, _ENTRY_JSON_BUDGET,
            drop_order=("score_components", "advisory_rationale", "hmm_regime_state"),
        )
        return {
            "kind": "entry",
            "context_schema_version": CONTEXT_SCHEMA_VERSION,
            "provenance": "signal_driven",
            "provenance_tag": f"main_pipeline:{source}"[:100],
            # Deliberately None: the strategy engine has no 0-1 conviction.
            "conviction": None,
            "macro_regime": macro_regime,
            "signal_score": score,
            "raw_forecast": raw_forecast,
            # No column records which forecast model won; never invent one.
            "forecast_model": None,
            "key_indicators_json": key_indicators_json,
            "decision_rationale": rationale,
        }
    except Exception as exc:  # noqa: BLE001 -- telemetry never blocks an order
        logger.warning("build_entry_context failed: %s", exc)
        return {
            "kind": "entry",
            "context_schema_version": CONTEXT_SCHEMA_VERSION,
            "provenance": "signal_driven",
            "provenance_tag": "main_pipeline:context_unavailable",
            "key_indicators_json": json.dumps({
                "context_schema_version": CONTEXT_SCHEMA_VERSION,
                "context_status": "unavailable",
                "error": str(exc)[:200],
            }, sort_keys=True),
        }


def build_exit_context(row: Any, *, signal: Any, held_qty: Any) -> Dict[str, Any]:
    """Exit context for a pipeline SELL on an EXIT_SIGNALS row. Never raises."""
    try:
        exit_signal = _str_or_none(signal, 30)
        core = {
            "exit_signal": exit_signal,
            "score": _finite_or_none(_get(row, "Score")),
            "price_at_decision": _positive_or_none(_get(row, "Price")),
            "macro_status": _str_or_none(_get(row, "Macro Status"), 50),
        }
        payload: Dict[str, Any] = dict(core)
        payload.update({
            "context_schema_version": CONTEXT_SCHEMA_VERSION,
            "context_status": _status(core),
            "held_qty": _finite_or_none(held_qty),
            "kelly_target_row": _finite_or_none(_get(row, "Kelly Target")),
            "dual_momentum_signal": _str_or_none(_get(row, "DualMomentum_Signal"), 30),
            "hmm_regime_state": _json_safe(_get(row, "HMM_Regime_State")),
            "hmm_risk_on_probability": _finite_or_none(_get(row, "HMM_Risk_On_Probability")),
            "regime_multiplier": _finite_or_none(_get(row, "Regime_Multiplier")),
            "meta_label_composite": _finite_or_none(_get(row, "Meta_Label_Composite")),
            "actionable_advice": _str_or_none(_get(row, "Actionable Advice Signal"), 1000),
            "advisory_action": _str_or_none(_get(row, "Advisory_Action"), 50),
            "advisory_rationale": _str_or_none(_get(row, "Advisory_Rationale"), 500),
        })
        return {
            "kind": "exit",
            "context_schema_version": CONTEXT_SCHEMA_VERSION,
            "close_reason": exit_close_reason(signal),
            "exit_context_json": _dumps_within_budget(
                payload, _EXIT_JSON_BUDGET,
                drop_order=("advisory_rationale", "actionable_advice", "hmm_regime_state"),
            ),
        }
    except Exception as exc:  # noqa: BLE001 -- telemetry never blocks an order
        logger.warning("build_exit_context failed: %s", exc)
        return {
            "kind": "exit",
            "context_schema_version": CONTEXT_SCHEMA_VERSION,
            "close_reason": exit_close_reason(signal),
            "exit_context_json": None,
        }


_ENTRY_KWARGS = (
    "provenance", "provenance_tag", "conviction", "macro_regime", "signal_score",
    "raw_forecast", "forecast_model", "key_indicators_json", "decision_rationale",
)


def apply_fill_kwargs(ctx: Any) -> Dict[str, Any]:
    """Map a decision context to extra ``PaperAccountStore.apply_fill`` kwargs.

    ``None``/non-dict/unknown kind -> ``{}`` (today's exact call). Never raises.
    """
    try:
        if not isinstance(ctx, dict):
            return {}
        kind = ctx.get("kind")
        if kind == "entry":
            return {k: ctx.get(k) for k in _ENTRY_KWARGS if k in ctx}
        if kind == "exit":
            out: Dict[str, Any] = {}
            if ctx.get("close_reason") is not None:
                out["close_reason"] = ctx.get("close_reason")
            if ctx.get("exit_context_json") is not None:
                out["exit_context_json"] = ctx.get("exit_context_json")
            return out
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("apply_fill_kwargs failed: %s", exc)
        return {}
