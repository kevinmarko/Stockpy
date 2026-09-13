"""pilots/retrospective_narrative.py — Templated Narrative Generator (v1 Non-LLM)
=============================================================================

Authoritative Requirements:
- .agents/ORIGINAL_REQUEST.md (§ R5, WP-F)
- .agents/PROJECT.md (§ 4 Retrospective Core Engine, Interface Contract 2 & 4)
- .agents/worker_m3/DISPATCH.md
- .agents/explorer_survey_2/retrospective_learning_loop_survey_report.md (§ 5)

Core Architecture:
A deterministic, non-LLM sentence builder that compiles 3 structured clauses
into an analytical, honest narrative for a closed paper trade:
1. Entry Clause: Provenance, execution price, strategy catalyst, conviction, and macro regime.
2. Outcome Clause: Holding duration, exit price, net realized dollar and percentage PnL.
3. Excursion & Calibration Clause: Holding-period risk (MFE/MAE/Edge Ratio) and conviction reliability calibration.

Strict Anti-Fabrication Safeguards (MANDATORY INTEGRITY GATES - WP-F):
- Zero None, NaN, nan, or null tokens may EVER leak into the output text.
- Never infer provenance; if entry snapshot was not captured or is unknown, mark as unrecorded provenance.
- Never assert calibration win rate if sample size < min_sample (default 5) or conviction is uncalibrated.
- If evaluation bridge was not reached or pricing data missing, explicitly state excursion data unavailable.
- Manual discretionary trades explicitly state model calibration is not applicable.
"""

from __future__ import annotations

import math
import re
from typing import Any

# =============================================================================
# Numeric Formatting Safety Primitives (Survey 2 § 5.2 / WP-F Zero Leakage)
# =============================================================================

def _is_valid_num(val: Any) -> bool:
    """Return True if val is a non-None, finite number."""
    if val is None:
        return False
    try:
        num = float(val)
        return math.isfinite(num)
    except (ValueError, TypeError):
        return False


def _fmt_curr(val: Any, fallback: str = "unrecorded") -> str:
    """Safely format currency with dollar sign and commas.
    Guarantees no None/NaN token is rendered.
    """
    if not _is_valid_num(val):
        return fallback
    num = float(val)
    if abs(num) < 1e-9:
        return "$0.00"
    if num < 0:
        return f"-${abs(num):,.2f}"
    return f"${num:,.2f}"


def _fmt_pct(val: Any, signed: bool = False, fallback: str = "unrecorded") -> str:
    """Safely format decimal fractions as percentages (e.g. 0.05 -> 5.0%).
    Guarantees no None/NaN token is rendered.
    """
    if not _is_valid_num(val):
        return fallback
    pct = float(val) * 100.0
    if abs(pct) < 1e-9:
        pct = 0.0
    sign = "+" if signed and pct > 0 else ""
    return f"{sign}{pct:.1f}%"


def _fmt_float(val: Any, decimals: int = 2, fallback: str = "unrecorded") -> str:
    """Safely format floating point numbers with fixed decimals.
    Guarantees no None/NaN token is rendered.
    """
    if not _is_valid_num(val):
        return fallback
    num = float(val)
    if abs(num) < 1e-9:
        num = 0.0
    return f"{num:.{decimals}f}"


# =============================================================================
# Deterministic Narrative Builder
# =============================================================================

def build_trade_narrative(
    trade_record: dict[str, Any] | str | None = None,
    side_or_provenance: str | None = None,
    strategy_id: str | None = None,
    entry_price: float | None = None,
    conviction: float | None = None,
    macro_regime: str | None = None,
    operator_notes: str | None = None,
    exit_price: float | None = None,
    holding_days: float | None = None,
    pnl: float | None = None,
    pnl_pct: float | None = None,
    mfe: float | None = None,
    mae: float | None = None,
    edge_ratio: float | None = None,
    bin_win_rate: float | None = None,
    bin_count: int | None = None,
    min_sample: int = 5,
    bridge_reached: bool = True,
    bars_available: bool = True,
    **kwargs: Any,
) -> str:
    """Build a deterministic, non-LLM templated narrative for a trade record.

    Accepts either a composed trade record dictionary as first argument, or explicit
    keyword/positional arguments representing trade attributes across all 15 permutations.
    """
    # 1. Resolve inputs across calling conventions (dict input vs keyword kwargs)
    rec: dict[str, Any] = {}
    prov_arg: str | None = None

    if isinstance(trade_record, dict):
        rec = trade_record
    elif isinstance(trade_record, str):
        prov_arg = trade_record
    elif trade_record is not None:
        prov_arg = str(trade_record)

    snap = rec.get("entry_snapshot") or rec.get("snapshot") or {}
    exc = rec.get("excursion") or rec.get("evaluation") or {}
    cal = rec.get("calibration") or {}

    # Provenance & Captured
    captured: bool = True
    if "captured" in snap:
        captured = bool(snap["captured"])
    elif "captured" in rec:
        captured = bool(rec["captured"])
    elif "decision_context_status" in snap:
        captured = str(snap["decision_context_status"]).lower() == "captured"

    raw_prov = (
        kwargs.get("provenance")
        or prov_arg
        or rec.get("provenance")
        or snap.get("provenance")
        or "unknown"
    )
    prov = str(raw_prov).lower().strip()
    if not captured or prov not in ("signal_driven", "manual"):
        prov = "unknown"

    # Side
    raw_side = (
        kwargs.get("side")
        or (side_or_provenance if prov_arg is None else None)
        or rec.get("side")
        or snap.get("side")
        or "buy"
    )
    side_str = str(raw_side).lower().strip()
    if side_str not in ("buy", "sell", "long", "short"):
        side_str = "buy"

    # Strategy ID
    strat = (
        kwargs.get("strategy_id")
        or strategy_id
        or rec.get("strategy_id")
        or snap.get("strategy_id")
    )
    if strat and str(strat).strip().lower() in ("none", "null", "nan"):
        strat = None

    # Entry Price
    ep = (
        kwargs.get("entry_price")
        if "entry_price" in kwargs
        else (entry_price if entry_price is not None else rec.get("entry_price", snap.get("entry_price")))
    )
    if not _is_valid_num(ep):
        ep = None
    else:
        ep = float(ep)

    # Conviction
    conv = (
        kwargs.get("conviction")
        if "conviction" in kwargs
        else (
            conviction
            if conviction is not None
            else snap.get("conviction", cal.get("conviction", rec.get("conviction")))
        )
    )
    if not _is_valid_num(conv):
        conv = None
    else:
        conv = float(conv)

    # Macro Regime
    regime = (
        kwargs.get("macro_regime")
        or macro_regime
        or snap.get("macro_regime")
        or rec.get("macro_regime")
    )
    if regime and str(regime).strip().lower() in ("none", "null", "nan", "unrecorded"):
        regime = None

    # Operator Notes
    notes = (
        kwargs.get("operator_notes")
        or operator_notes
        or snap.get("decision_rationale")
        or snap.get("operator_notes")
        or rec.get("operator_notes")
        or rec.get("decision_rationale")
    )
    if notes and str(notes).strip().lower() in ("none", "null", "nan"):
        notes = None

    # Exit Price
    xp = (
        kwargs.get("exit_price")
        if "exit_price" in kwargs
        else (exit_price if exit_price is not None else rec.get("exit_price"))
    )
    if not _is_valid_num(xp):
        xp = None
    else:
        xp = float(xp)

    # Holding Days
    h_days = (
        kwargs.get("holding_days")
        if "holding_days" in kwargs
        else (
            holding_days
            if holding_days is not None
            else rec.get("holding_period_days", rec.get("holding_days"))
        )
    )
    if not _is_valid_num(h_days):
        h_days = None
    else:
        h_days = float(h_days)

    # Realized PnL & PnL %
    realized_p = (
        kwargs.get("pnl")
        if "pnl" in kwargs
        else (pnl if pnl is not None else rec.get("realized_pnl", rec.get("pnl")))
    )
    if not _is_valid_num(realized_p):
        realized_p = None
    else:
        realized_p = float(realized_p)

    realized_pct = (
        kwargs.get("pnl_pct")
        if "pnl_pct" in kwargs
        else (pnl_pct if pnl_pct is not None else rec.get("realized_pnl_pct", rec.get("pnl_pct")))
    )
    if not _is_valid_num(realized_pct):
        realized_pct = None
    else:
        realized_pct = float(realized_pct)

    # Degenerate entry price guard (force realized_pct=None if entry_price <= 0)
    if ep is not None and ep <= 0.0:
        realized_pct = None

    # Excursion (MFE, MAE, Edge Ratio)
    mfe_val = (
        kwargs.get("mfe")
        if "mfe" in kwargs
        else (mfe if mfe is not None else exc.get("mfe"))
    )
    if not _is_valid_num(mfe_val):
        mfe_val = None
    else:
        mfe_val = float(mfe_val)

    mae_val = (
        kwargs.get("mae")
        if "mae" in kwargs
        else (mae if mae is not None else exc.get("mae"))
    )
    if not _is_valid_num(mae_val):
        mae_val = None
    else:
        mae_val = float(mae_val)

    edge_val = (
        kwargs.get("edge_ratio")
        if "edge_ratio" in kwargs
        else (edge_ratio if edge_ratio is not None else exc.get("edge_ratio"))
    )
    if not _is_valid_num(edge_val):
        edge_val = None
    else:
        edge_val = float(edge_val)

    # Calibration (Bin Win Rate, Bin Trade Count)
    b_wr = (
        kwargs.get("bin_win_rate")
        if "bin_win_rate" in kwargs
        else (
            bin_win_rate
            if bin_win_rate is not None
            else cal.get("bin_win_rate", cal.get("historical_bin_win_rate"))
        )
    )
    if not _is_valid_num(b_wr):
        b_wr = None
    else:
        b_wr = float(b_wr)

    b_cnt = (
        kwargs.get("bin_count")
        if "bin_count" in kwargs
        else (
            bin_count
            if bin_count is not None
            else cal.get("bin_trade_count", cal.get("bin_count"))
        )
    )
    if b_cnt is not None:
        try:
            b_cnt = int(b_cnt)
        except (ValueError, TypeError):
            b_cnt = None

    # Bridge Reached & Bars Available
    if "bridge_reached" in kwargs:
        b_reached = bool(kwargs["bridge_reached"])
    elif "bridge_status" in rec:
        b_reached = (rec.get("bridge_status") == "bridged")
    elif "bridge_reached" in exc:
        b_reached = bool(exc.get("bridge_reached"))
    else:
        b_reached = bool(bridge_reached)

    if "bars_available" in kwargs:
        b_bars = bool(kwargs["bars_available"])
    elif b_reached and str(exc.get("evaluation_status", "")).lower() == "evaluation data unavailable":
        b_bars = False
    else:
        b_bars = bool(bars_available)

    # -------------------------------------------------------------------------
    # CLAUSE 1: Entry & Decision Catalyst
    # -------------------------------------------------------------------------
    if prov == "signal_driven":
        strat_display = strat or "automated strategy"
        if conv is not None and regime is not None:
            # S1.1: Complete context
            entry_clause = (
                f"Signal-driven {side_str} trade entered on {strat_display} recommendation "
                f"at {_fmt_curr(ep)} (conviction: {_fmt_float(conv)}, regime: {regime})."
            )
        elif conv is not None and regime is None:
            # S1.2: Missing regime
            entry_clause = (
                f"Signal-driven {side_str} trade entered on {strat_display} recommendation "
                f"at {_fmt_curr(ep)} (conviction: {_fmt_float(conv)}, regime: unrecorded)."
            )
        elif conv is None and regime is not None:
            # S1.3: Missing conviction
            entry_clause = (
                f"Signal-driven {side_str} trade entered on {strat_display} recommendation "
                f"at {_fmt_curr(ep)} (regime: {regime})."
            )
        elif strat is not None:
            # S1.4: Missing both conviction & regime, known strategy ID
            entry_clause = (
                f"Signal-driven {side_str} trade entered on {strat} recommendation at {_fmt_curr(ep)}."
            )
        else:
            # S1.5: Missing strategy ID
            entry_clause = f"Signal-driven {side_str} trade entered via automated strategy at {_fmt_curr(ep)}."

    elif prov == "manual":
        if notes and str(notes).strip():
            # S2.2: Manual with operator note
            entry_clause = (
                f'Manual discretionary {side_str} trade executed by operator at {_fmt_curr(ep)} '
                f'(note: "{notes}").'
            )
        else:
            # S2.1: Standard manual
            entry_clause = f"Manual discretionary {side_str} trade executed by operator at {_fmt_curr(ep)}."

    else:  # unknown / unrecorded
        if ep is not None:
            # S3.1: Standard unrecorded
            entry_clause = (
                f"Trade executed at {_fmt_curr(ep)} with unrecorded provenance "
                f"(entry-time context not captured)."
            )
        else:
            # S3.2: Missing entry price
            entry_clause = "Trade executed with unrecorded provenance and unverified entry price."

    # -------------------------------------------------------------------------
    # CLAUSE 2: Outcome & Hold Period
    # -------------------------------------------------------------------------
    if realized_p is not None and realized_pct is None:
        # O1.5: Degenerate entry price
        outcome_clause = (
            f"Position closed at {_fmt_curr(xp)} realizing {_fmt_curr(realized_p)} "
            f"(percentage return unavailable due to degenerate entry price)."
        )
    elif realized_p == 0.0:
        # O1.3: Breakeven
        if h_days is not None:
            outcome_clause = (
                f"Position closed at {_fmt_curr(xp)} after {_fmt_float(h_days, 1)} days "
                f"at breakeven ($0.00 realized PnL)."
            )
        else:
            outcome_clause = f"Position closed at {_fmt_curr(xp)} at breakeven ($0.00 realized PnL)."
    elif realized_p is not None and realized_p > 0:
        # O1.1: Gain
        if h_days is not None:
            outcome_clause = (
                f"Position closed at {_fmt_curr(xp)} after {_fmt_float(h_days, 1)} days, "
                f"realizing a gain of +{_fmt_curr(realized_p)} ({_fmt_pct(realized_pct, signed=True)})."
            )
        else:
            outcome_clause = (
                f"Position closed at {_fmt_curr(xp)} (holding duration unrecorded), "
                f"realizing a gain of +{_fmt_curr(realized_p)} ({_fmt_pct(realized_pct, signed=True)})."
            )
    elif realized_p is not None and realized_p < 0:
        # O1.2: Loss
        if h_days is not None:
            outcome_clause = (
                f"Position closed at {_fmt_curr(xp)} after {_fmt_float(h_days, 1)} days, "
                f"realizing a loss of -{_fmt_curr(abs(realized_p))} ({_fmt_pct(realized_pct, signed=True)})."
            )
        else:
            outcome_clause = (
                f"Position closed at {_fmt_curr(xp)} (holding duration unrecorded), "
                f"realizing a loss of -{_fmt_curr(abs(realized_p))} ({_fmt_pct(realized_pct, signed=True)})."
            )
    else:
        outcome_clause = f"Position closed at {_fmt_curr(xp)}."

    # -------------------------------------------------------------------------
    # CLAUSE 3: Excursion & Conviction Calibration
    # -------------------------------------------------------------------------
    if not b_reached:
        if prov == "manual":
            excursion_clause = (
                "Hold-period excursion metrics unavailable (trade did not reach evaluation bridge); "
                "model calibration not applicable for manual trades."
            )
        elif prov == "signal_driven":
            excursion_clause = (
                "Hold-period excursion metrics unavailable (trade did not reach evaluation bridge)."
            )
        else:
            excursion_clause = (
                "Hold-period excursion metrics unavailable (trade did not reach evaluation bridge); "
                "conviction calibration unavailable."
            )
    elif not b_bars or mae_val is None or mfe_val is None:
        if prov == "manual":
            excursion_clause = (
                "Hold-period excursion metrics unavailable (pricing data missing for hold period); "
                "model calibration not applicable for manual trades."
            )
        elif prov == "unknown":
            excursion_clause = (
                "Hold-period excursion metrics unavailable (pricing data missing for hold period); "
                "conviction calibration unavailable."
            )
        else:
            excursion_clause = (
                "Hold-period excursion metrics unavailable (pricing data missing for hold period)."
            )
    else:
        exc_prefix = (
            f"Hold-period excursion reached MFE +{_fmt_pct(mfe_val)} vs MAE -{_fmt_pct(mae_val)} "
            f"(Edge Ratio: {_fmt_float(edge_val)})"
        )
        if prov == "manual":
            excursion_clause = f"{exc_prefix}; model calibration not applicable for manual trades."
        elif prov == "unknown":
            excursion_clause = f"{exc_prefix}; conviction calibration unavailable (provenance unrecorded)."
        else:  # signal_driven
            if b_wr is not None and b_cnt is not None and b_cnt >= min_sample:
                excursion_clause = (
                    f"{exc_prefix}; entry conviction binned at historical {_fmt_pct(b_wr)} win rate (N={b_cnt})."
                )
            elif b_cnt is not None and b_cnt < min_sample:
                excursion_clause = (
                    f"{exc_prefix}; historical calibration unavailable for this conviction level "
                    f"(insufficient sample, N={b_cnt} < {min_sample})."
                )
            else:
                excursion_clause = f"{exc_prefix}."

    # Final assembly
    narrative = f"{entry_clause} {outcome_clause} {excursion_clause}".strip()

    # Defense-in-depth: guarantee ZERO None, NaN, nan, null leakage (WP-F)
    narrative = re.sub(r"\bNone\b", "unrecorded", narrative)
    narrative = re.sub(r"\b(NaN|nan)\b", "unrecorded", narrative)
    narrative = re.sub(r"\bnull\b", "unrecorded", narrative)

    return narrative
