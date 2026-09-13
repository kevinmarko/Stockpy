"""pilots/retrospective_narrative.py — per-trade templated narrative (v1, no LLM).

Renders one plain-text narrative sentence-block for a single Retrospective
Learning Loop (Trade Journal) record — the exact dict shape produced by
``pilots.retrospective_composer.compose_trade_retrospective`` (see that
module's own docstring / CLAUDE.md's "Retrospective Learning Loop" framing
for the locked contract):

    {
        "trade_id": int, "symbol": str, "strategy_id": str, "pilot_id": Optional[str],
        "side": str, "qty": float, "entry_ts": Optional[str], "entry_price": float,
        "exit_ts": str, "exit_price": float, "realized_pnl": float,
        "realized_pnl_pct": Optional[float], "holding_period_days": Optional[float],
        "close_reason": str,
        "evaluation": {
            "available": bool, "mfe": Optional[float], "mae": Optional[float],
            "edge_ratio": Optional[float], "reason": Optional[str],
        },
        "decision": {
            "state": "signal_driven" | "manual" | "unknown",
            "provenance": Optional[str], "conviction": Optional[float],
            "regime": Optional[str], "factors": Optional[Dict[str, Any]], "notes": Optional[str],
        },
    }

**Strictly template-based — there is NO LLM call anywhere in this module.**
Every branch below is an explicit, hand-written template string; every value
that could be ``None``/NaN has an explicit missing-data variant, so the
rendered narrative can NEVER present a guess as a fact (CONSTRAINT #4) and
NEVER raises on a malformed/incomplete input (CONSTRAINT #6).

The single most fabrication-sensitive branch in this whole module is the
"why" sentence (:func:`_why_sentence`): it hard-codes exactly three literal
outcomes for ``decision.state`` — ``"signal_driven"``, ``"manual"``, and
``"unknown"`` — and the "manual"/"unknown" wordings are REQUIRED VERBATIM
text (per the Retrospective Learning Loop plan's own checklist), never
embellished or inferred. ``decision.state`` is read as-is from the composed
record; this module never re-derives it from ``strategy_id`` or anything
else — that inference belongs solely to the composer, per its own
docstring's anti-shortcut rule.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["build_trade_narrative"]


# ---------------------------------------------------------------------------
# Shared, defensive formatting helpers — every one degrades to an honest
# "unknown"/omitted variant on a missing or non-finite value; none ever
# raises and none ever renders the literal substring "None"/"nan"/"NaN".
# ---------------------------------------------------------------------------


def _finite_float(value: Any) -> Optional[float]:
    """Coerce to a finite float, else ``None`` (NaN/inf/garbage -> None)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _fmt_qty(value: Any) -> str:
    """Render a share/contract quantity. Direction is conveyed by the side
    verb, not the sign, so this always renders the magnitude."""
    f = _finite_float(value)
    if f is None:
        return "an unknown quantity of"
    f = abs(f)
    if f == int(f):
        return str(int(f))
    return f"{f:g}"


def _fmt_price(value: Any) -> str:
    f = _finite_float(value)
    if f is None:
        return "an unknown price"
    return f"${f:,.2f}"


def _fmt_signed_dollar(value: Any) -> str:
    f = _finite_float(value)
    if f is None:
        return "an unknown amount"
    sign = "+" if f >= 0 else "-"
    return f"{sign}${abs(f):,.2f}"


def _fmt_signed_pct(fraction: float) -> str:
    """``fraction`` is a raw ratio (0.08 == 8%), matching
    ``realized_pnl_pct``'s/``mfe``/``mae``'s storage convention throughout
    this codebase (see ``data/paper_account_store.py``'s
    ``realized_pnl_pct`` computation and ``evaluation_engine.
    calculate_edge_ratio``'s MFE/MAE)."""
    sign = "+" if fraction >= 0 else "-"
    return f"{sign}{abs(fraction) * 100:.2f}%"


def _fmt_ts(value: Any) -> Optional[str]:
    """``"2026-01-05 14:30 UTC"`` from an ISO string or a real ``datetime``;
    ``None`` on anything unparsable — caller supplies the missing-data
    variant text."""
    if value is None:
        return None
    dt: Optional[datetime] = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    if dt is None:
        return None
    try:
        return dt.strftime("%Y-%m-%d %H:%M") + " UTC"
    except Exception:  # noqa: BLE001 — defensive: never raise on a bad datetime
        return None


_SIDE_VERBS = {"BUY": "Bought", "SELL": "Sold"}


def _side_verb(side: Any) -> str:
    if not isinstance(side, str) or not side:
        return "Traded"
    return _SIDE_VERBS.get(side.upper(), f"Traded ({side})")


def _indefinite_article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


# ---------------------------------------------------------------------------
# Sentence 1 — what happened
# ---------------------------------------------------------------------------


def _what_happened_sentence(retro: Dict[str, Any]) -> str:
    """Symbol, side, qty, entry (price + time, or "at an unknown time" when
    ``entry_ts is None``), exit (price + time), close_reason."""
    if not isinstance(retro, dict):
        retro = {}
    symbol = retro.get("symbol") or "the position"
    side_verb = _side_verb(retro.get("side"))
    qty_str = _fmt_qty(retro.get("qty"))

    entry_ts_str = _fmt_ts(retro.get("entry_ts"))
    entry_price_str = _fmt_price(retro.get("entry_price"))
    if entry_ts_str:
        opened_clause = f"opened {entry_ts_str} at {entry_price_str}"
    else:
        opened_clause = f"opened at an unknown time at {entry_price_str}"

    exit_ts_str = _fmt_ts(retro.get("exit_ts"))
    exit_price_str = _fmt_price(retro.get("exit_price"))
    if exit_ts_str:
        closed_clause = f"closed {exit_ts_str} at {exit_price_str}"
    else:
        closed_clause = f"closed at an unknown time at {exit_price_str}"

    close_reason = retro.get("close_reason") or "unknown reason"

    return f"{side_verb} {qty_str} {symbol} — {opened_clause}, {closed_clause} ({close_reason})."


# ---------------------------------------------------------------------------
# Sentence 2 — outcome
# ---------------------------------------------------------------------------


def _outcome_sentence(retro: Dict[str, Any]) -> str:
    """``realized_pnl`` + ``realized_pnl_pct`` when it is a real, finite
    number; the percentage clause is OMITTED (never rendered as
    "None%"/"nan%") when it is unavailable."""
    if not isinstance(retro, dict):
        retro = {}
    pnl_str = _fmt_signed_dollar(retro.get("realized_pnl"))

    pct = _finite_float(retro.get("realized_pnl_pct"))
    if pct is not None:
        return f"Realized P&L: {pnl_str} ({_fmt_signed_pct(pct)})."
    return f"Realized P&L: {pnl_str} (percentage unavailable — degenerate entry price)."


# ---------------------------------------------------------------------------
# Sentence 3 — the full move (MFE/MAE/Edge Ratio, or an honest reason)
# ---------------------------------------------------------------------------


def _move_sentence(retro: Dict[str, Any]) -> str:
    if not isinstance(retro, dict):
        retro = {}
    evaluation = retro.get("evaluation")
    if not isinstance(evaluation, dict):
        evaluation = {}

    if evaluation.get("available"):
        mfe = _finite_float(evaluation.get("mfe"))
        mae = _finite_float(evaluation.get("mae"))
        if mfe is not None and mae is not None:
            edge_ratio = _finite_float(evaluation.get("edge_ratio"))
            edge_clause = (
                f" (Edge Ratio {edge_ratio:.2f})" if edge_ratio is not None else " (Edge Ratio unavailable)"
            )
            return (
                f"Over the hold, price moved as much as {mfe:.1%} in your favor "
                f"and {mae:.1%} against you{edge_clause}."
            )
        # Marked available but MFE/MAE are themselves missing/non-finite —
        # defensive branch; should not happen given the composer's contract,
        # but never fabricate a number here (CONSTRAINT #4).
        return "Evaluation data unavailable for this trade — MFE/MAE missing despite being marked available."

    reason = evaluation.get("reason") or "no reason given"
    return f"Evaluation data unavailable for this trade — {reason}."


# ---------------------------------------------------------------------------
# Sentence 4 — why (the fabrication-sensitive branch)
# ---------------------------------------------------------------------------

# Required-verbatim wording (per the Retrospective Learning Loop plan's own
# checklist) for the two non-signal-driven states. NEVER embellish or infer
# a reason for either — a manual trade has no model reasoning behind it by
# definition, and "unknown" means decision context was never captured at all.
_MANUAL_TEXT = "You placed this trade manually — no model signal was behind it."
_UNKNOWN_TEXT = "Entry context wasn't captured for this trade."


def _provenance_label(provenance: Any) -> str:
    """Derive a short human label from a raw provenance string, e.g.
    ``"automated:options_auto_scan"`` -> ``"automated options"``. Falls back
    to a generic ``"automated"`` label for an unrecognized/malformed
    provenance — never fabricates a specific-sounding source that wasn't
    literally present in the string."""
    if not isinstance(provenance, str) or not provenance:
        return "automated"
    prefix, sep, source = provenance.partition(":")
    if not sep:
        cleaned = provenance.replace("_", " ").replace("-", " ").strip()
        return cleaned or "automated"
    source_label = source.replace("_", " ").replace("-", " ").strip()
    if not source_label:
        return prefix or "automated"
    first_word = source_label.split()[0]
    return f"{prefix} {first_word}".strip() or "automated"


# Known factor keys get a friendlier, still-verbatim-value phrasing; any
# other key falls back to a generic "a <key> of <value>" rendering — the
# KEY and VALUE are always read straight from ``factors``, never invented.
_FACTOR_TEMPLATES = {
    "ivr": "an IVR of {value}",
    "vrp": "a VRP of {value}",
    "vix": "a VIX of {value}",
    "credit_to_width_ratio": "a credit-to-width ratio of {value}",
    "short_delta": "a short delta of {value}",
}


def _fmt_factor_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _format_one_factor(key: str, value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if key == "trend_bias" and isinstance(value, str) and value:
        return f"a {value.lower()} trend bias"
    template = _FACTOR_TEMPLATES.get(key)
    if template:
        return template.format(value=_fmt_factor_value(value))
    label = key.replace("_", " ").strip()
    if not label:
        return None
    return f"a {label} of {_fmt_factor_value(value)}"


def _factor_clause(factors: Any) -> Optional[str]:
    """Up to two of the present, non-None ``factors`` entries, rendered
    verbatim (real key, real value). ``None``/empty -> no clause at all."""
    if not isinstance(factors, dict) or not factors:
        return None
    parts: List[str] = []
    for key, value in factors.items():
        formatted = _format_one_factor(key, value)
        if formatted:
            parts.append(formatted)
        if len(parts) >= 2:
            break
    if not parts:
        return None
    return f"driven partly by {' and '.join(parts)}"


def _signal_driven_sentence(decision: Dict[str, Any]) -> str:
    label = _provenance_label(decision.get("provenance"))
    conviction = _finite_float(decision.get("conviction"))
    if conviction is not None:
        base = f"The model rated this a {conviction:.2f} conviction {label} trade"
    else:
        # Degrade to omit the conviction number entirely -- never render
        # "None conviction" (CONSTRAINT #4).
        article = _indefinite_article(label)
        base = f"The model rated this {article} {label} trade"

    clause = _factor_clause(decision.get("factors"))
    if clause:
        return f"{base}, {clause}."
    return f"{base}."


def _why_sentence(retro: Dict[str, Any]) -> str:
    if not isinstance(retro, dict):
        retro = {}
    decision = retro.get("decision")
    if not isinstance(decision, dict):
        decision = {}
    state = decision.get("state")

    if state == "signal_driven":
        return _signal_driven_sentence(decision)
    if state == "manual":
        return _MANUAL_TEXT
    # "unknown" and any other/unexpected value both defensively fall back
    # here (CONSTRAINT #6) -- never guess at what a model "probably" thought.
    return _UNKNOWN_TEXT


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_trade_narrative(retro: Dict[str, Any]) -> str:
    """Plain-text, 4-sentence narrative for one retrospective record (the
    exact shape documented in this module's docstring). Strictly
    template-based — NO LLM call anywhere in this module. Never raises
    (CONSTRAINT #6): a malformed/incomplete ``retro`` degrades to a
    best-effort narrative rather than crashing the caller."""
    try:
        if not isinstance(retro, dict):
            retro = {}
        sentences = [
            _what_happened_sentence(retro),
            _outcome_sentence(retro),
            _move_sentence(retro),
            _why_sentence(retro),
        ]
        return " ".join(s for s in sentences if s)
    except Exception as exc:  # noqa: BLE001 — CONSTRAINT #6: never raise
        logger.warning("build_trade_narrative failed on a malformed record: %s", exc)
        return "Narrative unavailable for this trade — the underlying record was malformed."
