"""pilots/retrospective_insights.py — Batch Pattern Insights Engine (Strict Cohort Separation)
=============================================================================================

Authoritative Requirements:
- .agents/ORIGINAL_REQUEST.md (§ R5, WP-G)
- .agents/PROJECT.md (§ 3 Batch Analytics, Interface Contract 3)
- .agents/worker_m3/DISPATCH.md
- .agents/explorer_survey_2/retrospective_learning_loop_survey_report.md (§ 6)

Core Architecture:
Computes batch-level performance, excursion, and calibration insights partitioned
strictly into disjoint cohorts:
1. automated_cohort (signal_driven): Automated algorithmic trades with valid entry snapshots.
2. manual_cohort: Discretionary manual orders executed by operator.
3. unrecorded_cohort: Legacy or unrecorded trades without entry snapshots.
4. contrastive_insights: Qualitative analytical comparison highlighting behavioral differences.
5. bridge_health: Completeness telemetry of the execution-to-evaluation bridge.

Strict Anti-Fabrication Safeguards (MANDATORY INTEGRITY GATES - WP-G):
- ZERO blended/aggregate performance metrics across cohorts (no top-level win_rate, total_pnl, or profit_factor).
- Disjoint calculation: adding manual trades must never alter automated cohort metrics.
- Division-by-zero guards on empty or single-trade cohorts.
- Zero None, NaN, nan, or null tokens rendered in contrastive analytical strings.
"""

from __future__ import annotations

import logging
from typing import Any

from pilots.retrospective_narrative import (
    _fmt_float,
    _fmt_pct,
    _is_valid_num,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Helper: Extract Record Attributes
# =============================================================================

def _extract_provenance(record: dict[str, Any]) -> str:
    """Extract provenance tag from record or its snapshot, enforcing anti-fabrication.

    Anti-fabrication rule (mirrors ``retrospective_composer.py``'s own gate):
    a record EXPLICITLY marked ``captured=False`` (the composer's own
    signal for "snapshot missing, never infer") is always "unknown",
    regardless of whatever `provenance` string happens to also be present.
    `captured` genuinely absent (`None` -- e.g. a caller-constructed record
    that never carries a snapshot dict at all, the convention this
    function's own test suite uses for direct cohort-math testing) trusts
    the top-level `provenance` field as-is; a real composer-produced record
    is never actually ambiguous here since `compose_trade_retrospective`
    always sets `captured` to an explicit `True`/`False`.
    """
    snap = record.get("entry_snapshot") or record.get("snapshot") or {}
    captured = snap.get("captured")
    if captured is None:
        captured = record.get("captured")
    if captured is None and "decision_context_status" in snap:
        captured = str(snap["decision_context_status"]).lower() == "captured"

    # If captured is explicitly False, provenance must be unknown
    if captured is False:
        return "unknown"

    raw_prov = (
        record.get("provenance")
        or snap.get("provenance")
        or "unknown"
    )
    prov = str(raw_prov).lower().strip()
    if prov in ("signal_driven", "manual"):
        return prov
    return "unknown"


def _extract_pnl(record: dict[str, Any]) -> float | None:
    """Safely extract realized PnL in dollars.

    Returns ``None`` -- never a fabricated ``0.0`` (CONSTRAINT #4) -- for a
    trade whose realized PnL was never actually recorded. Coercing a missing
    value to `0.0` would silently count an unmeasurable trade as a measured
    breakeven, corrupting win rate, total PnL, and the Brier score for every
    cohort that includes it (see `_compute_cohort_metrics`, which explicitly
    excludes `None` PnLs from every aggregate rather than folding them in).
    """
    val = record.get("realized_pnl")
    if val is None:
        val = record.get("pnl")
    if _is_valid_num(val):
        return float(val)
    return None


def _extract_holding_days(record: dict[str, Any]) -> float | None:
    """Safely extract holding duration in days."""
    val = record.get("holding_period_days")
    if val is None:
        val = record.get("holding_days")
    if _is_valid_num(val):
        return float(val)
    return None


def _extract_excursion_metric(record: dict[str, Any], key: str) -> float | None:
    """Safely extract excursion metric (mae, mfe, edge_ratio)."""
    exc = record.get("excursion") or record.get("evaluation") or {}
    val = exc.get(key)
    if val is None:
        val = record.get(key)
    if _is_valid_num(val):
        return float(val)
    return None


def _extract_conviction(record: dict[str, Any]) -> float | None:
    """Safely extract conviction value."""
    snap = record.get("entry_snapshot") or record.get("snapshot") or {}
    cal = record.get("calibration") or {}
    val = snap.get("conviction")
    if val is None:
        val = cal.get("conviction")
    if val is None:
        val = record.get("conviction")
    if _is_valid_num(val):
        return float(val)
    return None


# =============================================================================
# Cohort Metrics Calculator (Disjoint & Independent)
# =============================================================================

def _compute_cohort_metrics(
    trades: list[dict[str, Any]],
    is_automated: bool = False,
    is_unrecorded: bool = False,
) -> dict[str, Any]:
    """Compute aggregate performance and risk metrics strictly for a single cohort."""
    total_trades = len(trades)
    trade_count = total_trades

    if total_trades == 0:
        base_metrics: dict[str, Any] = {
            "total_trades": 0,
            "trade_count": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "breakeven_trades": 0,
            "win_rate": None,
            "total_realized_pnl": 0.0,
            "profit_factor": None,
            "mean_holding_period_days": None,
            "mean_edge_ratio": None,
            "mean_mae": None,
            "mean_mfe": None,
            "symbols": [],
            "excluded_unmeasurable_pnl_count": 0,
        }
        if is_automated:
            base_metrics["cohort_name"] = "Automated (Signal-Driven)"
            base_metrics["calibration_brier_score"] = None
            base_metrics["strategies"] = {}
        elif is_unrecorded:
            base_metrics["cohort_name"] = "Unrecorded (Pre-Feature / Missing Snapshot)"
            base_metrics["note"] = "Historical trades without entry snapshot; excluded from systematic model evaluation."
        else:
            base_metrics["cohort_name"] = "Manual (Discretionary)"
            base_metrics["calibration_status"] = "not_applicable"
        return base_metrics

    # Compute PnLs and outcomes. `_extract_pnl` returns None (never a
    # fabricated 0.0 -- CONSTRAINT #4) for a trade whose realized PnL was
    # never actually recorded; such a trade is excluded from every PnL-based
    # aggregate below rather than silently counted as a $0.00 breakeven, and
    # the exclusion count is reported honestly instead of hidden.
    raw_pnls = [_extract_pnl(t) for t in trades]
    pnls = [p for p in raw_pnls if p is not None]
    excluded_unmeasurable_pnl_count = len(raw_pnls) - len(pnls)

    winning_trades = sum(1 for p in pnls if p > 0)
    losing_trades = sum(1 for p in pnls if p < 0)
    breakeven_trades = sum(1 for p in pnls if p == 0)

    # Win rate is computed over trades with a MEASURED outcome only -- an
    # unmeasurable trade must never silently dilute the denominator.
    win_rate = round(winning_trades / len(pnls), 4) if pnls else None
    total_realized_pnl = round(sum(pnls), 2) if pnls else None

    # Profit Factor: Gross Gains / Gross Losses
    gross_gains = sum(p for p in pnls if p > 0)
    gross_losses = sum(abs(p) for p in pnls if p < 0)
    if gross_losses > 0:
        profit_factor = round(gross_gains / gross_losses, 4)
    else:
        profit_factor = None

    # Holding Period Days
    holding_periods = [h for t in trades if (h := _extract_holding_days(t)) is not None]
    if holding_periods:
        mean_holding_period_days = round(sum(holding_periods) / len(holding_periods), 2)
    else:
        mean_holding_period_days = None

    # Excursion Metrics
    maes = [m for t in trades if (m := _extract_excursion_metric(t, "mae")) is not None]
    mfes = [m for t in trades if (m := _extract_excursion_metric(t, "mfe")) is not None]
    edges = [e for t in trades if (e := _extract_excursion_metric(t, "edge_ratio")) is not None]

    mean_mae = round(sum(maes) / len(maes), 4) if maes else None
    mean_mfe = round(sum(mfes) / len(mfes), 4) if mfes else None
    mean_edge_ratio = round(sum(edges) / len(edges), 4) if edges else None

    # Unique symbols
    symbols = sorted({str(t.get("symbol")) for t in trades if t.get("symbol")})

    metrics: dict[str, Any] = {
        "total_trades": total_trades,
        "trade_count": trade_count,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "breakeven_trades": breakeven_trades,
        "win_rate": win_rate,
        "total_realized_pnl": total_realized_pnl,
        "profit_factor": profit_factor,
        "mean_holding_period_days": mean_holding_period_days,
        "mean_edge_ratio": mean_edge_ratio,
        "mean_mae": mean_mae,
        "mean_mfe": mean_mfe,
        "symbols": symbols,
        "excluded_unmeasurable_pnl_count": excluded_unmeasurable_pnl_count,
    }

    if is_automated:
        metrics["cohort_name"] = "Automated (Signal-Driven)"

        # Calibration Brier Score: mean of (conviction - outcome)^2. A trade
        # with a real conviction but no MEASURED PnL contributes no outcome
        # to score against, so it is excluded here too (never scored as a
        # fabricated loss via a coerced 0.0 PnL).
        brier_sq_errors: list[float] = []
        for t in trades:
            conv = _extract_conviction(t)
            p = _extract_pnl(t)
            if conv is not None and p is not None:
                outcome = 1.0 if p > 0 else 0.0
                brier_sq_errors.append((conv - outcome) ** 2)

        if brier_sq_errors:
            metrics["calibration_brier_score"] = round(
                sum(brier_sq_errors) / len(brier_sq_errors), 4
            )
        else:
            metrics["calibration_brier_score"] = None

        # Per-strategy breakdown
        strategies_dict: dict[str, Any] = {}
        grouped_by_strategy: dict[str, list[dict[str, Any]]] = {}
        for t in trades:
            strat_name = (
                t.get("strategy_id")
                or (t.get("entry_snapshot") or {}).get("strategy_id")
                or (t.get("snapshot") or {}).get("strategy_id")
                or "unspecified"
            )
            grouped_by_strategy.setdefault(str(strat_name), []).append(t)

        for strat_id, s_trades in grouped_by_strategy.items():
            s_raw_pnls = [_extract_pnl(st) for st in s_trades]
            s_pnls = [p for p in s_raw_pnls if p is not None]
            s_wins = sum(1 for p in s_pnls if p > 0)
            s_losses = sum(1 for p in s_pnls if p < 0)
            s_count = len(s_trades)
            s_wr = round(s_wins / len(s_pnls), 4) if s_pnls else None
            s_total_pnl = round(sum(s_pnls), 2) if s_pnls else None
            s_edges = [
                e
                for st in s_trades
                if (e := _extract_excursion_metric(st, "edge_ratio")) is not None
            ]
            s_mean_edge = round(sum(s_edges) / len(s_edges), 4) if s_edges else None

            strategies_dict[strat_id] = {
                "trades": s_count,
                "total_trades": s_count,
                "winning_trades": s_wins,
                "losing_trades": s_losses,
                "win_rate": s_wr,
                "total_pnl": s_total_pnl,
                "total_realized_pnl": s_total_pnl,
                "mean_edge_ratio": s_mean_edge,
                "excluded_unmeasurable_pnl_count": len(s_raw_pnls) - len(s_pnls),
            }
        metrics["strategies"] = strategies_dict

    elif is_unrecorded:
        metrics["cohort_name"] = "Unrecorded (Pre-Feature / Missing Snapshot)"
        metrics["note"] = (
            "Historical trades without entry snapshot; excluded from systematic model evaluation."
        )

    else:  # manual
        metrics["cohort_name"] = "Manual (Discretionary)"
        metrics["calibration_status"] = "not_applicable"

    return metrics


# =============================================================================
# Contrastive Insights Synthesizer
# =============================================================================

#: Minimum trades REQUIRED in EACH cohort before a per-cohort excursion
#: comparison is stated at all. A comparison between, say, one manual trade
#: and one automated trade is not a pattern -- it's noise, and dressing it up
#: as a systemic "wider loss tolerance" finding is an unearned causal claim
#: (a true statistical-significance test is a larger follow-up; this floor is
#: the minimum honesty bar for v1).
_MIN_TRADES_FOR_EXCURSION_COMPARISON = 5


def _build_contrastive_insights(
    auto_stats: dict[str, Any],
    manual_stats: dict[str, Any],
    unrecorded_stats: dict[str, Any] | None = None,
) -> list[str]:
    """Generate human-readable analytical contrastive insights comparing cohorts.

    Every statement here names the sample size (N) it is based on, and never
    asserts a causal/behavioral explanation ("indicating wider loss
    tolerance...") for a difference between two means -- that is an
    inference this module has no basis for, not a measurement.
    """
    insights: list[str] = []

    auto_trades = auto_stats.get("total_trades", 0)
    manual_trades = manual_stats.get("total_trades", 0)

    if auto_trades > 0 and manual_trades > 0:
        # 1. Performance & Holding Period Contrast (N always disclosed)
        auto_wr_str = _fmt_pct(auto_stats.get("win_rate"))
        man_wr_str = _fmt_pct(manual_stats.get("win_rate"))

        auto_edge = auto_stats.get("mean_edge_ratio")
        auto_edge_str = f" (Edge Ratio: {_fmt_float(auto_edge)})" if auto_edge is not None else ""

        man_edge = manual_stats.get("mean_edge_ratio")
        man_edge_str = f" (Edge Ratio: {_fmt_float(man_edge)})" if man_edge is not None else ""

        auto_hold = _fmt_float(auto_stats.get("mean_holding_period_days"), 1)
        man_hold = _fmt_float(manual_stats.get("mean_holding_period_days"), 1)

        insights.append(
            f"Automated strategies achieved a {auto_wr_str} win rate{auto_edge_str} "
            f"over a {auto_hold}-day average holding period (N={auto_trades}), compared to manual "
            f"discretionary trading's {man_wr_str} win rate{man_edge_str} over a {man_hold}-day "
            f"average holding period (N={manual_trades})."
        )

        # 2. Excursion Comparison -- measurement only, no causal attribution,
        # and gated on a minimum sample in BOTH cohorts.
        auto_mae = auto_stats.get("mean_mae")
        man_mae = manual_stats.get("mean_mae")
        if (
            auto_mae is not None
            and man_mae is not None
            and auto_trades >= _MIN_TRADES_FOR_EXCURSION_COMPARISON
            and manual_trades >= _MIN_TRADES_FOR_EXCURSION_COMPARISON
        ):
            if man_mae > auto_mae:
                insights.append(
                    f"Manual trades measured a higher average adverse excursion than automated "
                    f"trades (MAE {_fmt_pct(man_mae)} vs {_fmt_pct(auto_mae)}; N={manual_trades} "
                    f"manual, N={auto_trades} automated)."
                )
            elif auto_mae > man_mae:
                insights.append(
                    f"Automated trades measured a higher average adverse excursion than manual "
                    f"trades (MAE {_fmt_pct(auto_mae)} vs {_fmt_pct(man_mae)}; N={auto_trades} "
                    f"automated, N={manual_trades} manual)."
                )
            else:
                insights.append(
                    f"Automated and manual trades measured an identical average adverse excursion "
                    f"(MAE {_fmt_pct(auto_mae)}; N={auto_trades} automated, N={manual_trades} manual)."
                )
        elif (
            auto_mae is not None
            and man_mae is not None
            and (auto_trades < _MIN_TRADES_FOR_EXCURSION_COMPARISON
                 or manual_trades < _MIN_TRADES_FOR_EXCURSION_COMPARISON)
        ):
            insights.append(
                f"Excursion comparison not stated (insufficient sample: N={auto_trades} automated, "
                f"N={manual_trades} manual; both cohorts need >= "
                f"{_MIN_TRADES_FOR_EXCURSION_COMPARISON} trades)."
            )

        # 3. Model Calibration Brier Score
        brier = auto_stats.get("calibration_brier_score")
        if brier is not None:
            insights.append(
                f"Model conviction calibration is operating with a Brier score of "
                f"{_fmt_float(brier, 3)} across {auto_trades} automated trades."
            )

    elif auto_trades > 0 and manual_trades == 0:
        auto_wr_str = _fmt_pct(auto_stats.get("win_rate"))
        insights.append(
            f"Automated strategies executed {auto_trades} trades with a {auto_wr_str} win rate; "
            f"no manual discretionary trades were recorded for comparison."
        )
        brier = auto_stats.get("calibration_brier_score")
        if brier is not None:
            insights.append(
                f"Model conviction calibration is operating with a Brier score of "
                f"{_fmt_float(brier, 3)} across {auto_trades} automated trades."
            )

    elif auto_trades == 0 and manual_trades > 0:
        man_wr_str = _fmt_pct(manual_stats.get("win_rate"))
        insights.append(
            f"Manual discretionary trading executed {manual_trades} trades with a {man_wr_str} win rate; "
            f"no automated strategy trades were recorded for comparison."
        )

    else:
        insights.append(
            "No closed trades available across automated or manual cohorts to derive contrastive insights."
        )

    # 4. Unrecorded cohort mention
    if unrecorded_stats and unrecorded_stats.get("total_trades", 0) > 0:
        unrec_n = unrecorded_stats["total_trades"]
        insights.append(
            f"{unrec_n} historical trades were executed with unrecorded provenance and excluded from systematic model evaluation."
        )

    return insights


# =============================================================================
# Primary Batch Analytics Generator
# =============================================================================

def generate_batch_retrospective_insights(
    composed_records: list[dict[str, Any]] | None = None,
    limit: int = 100,
    paper_store: Any | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Generate batch retrospective insights strictly partitioned by cohort.

    Guarantees zero blended/aggregate performance metrics across automated and manual trades.
    """
    records: list[dict[str, Any]] = []

    # 1. Resolve records input. No caller-frame inspection here (a prior
    # version walked the call stack looking for a local variable literally
    # named `simulated_trades`/`composed_records` -- a test-fitting hack that
    # let a caller's unrelated local silently become this function's dataset;
    # see docs/known_issues for the incident). The caller either passes
    # `composed_records` explicitly, or this genuinely composes from the
    # store -- nothing in between.
    if composed_records is not None:
        records = composed_records
    else:
        try:
            from pilots.retrospective_composer import compose_retrospectives_batch
            records = compose_retrospectives_batch(limit=limit, paper_store=paper_store, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("compose_retrospectives_batch error: %s", exc)
            records = []

    # 2. Partition into disjoint cohorts
    automated_trades: list[dict[str, Any]] = []
    manual_trades: list[dict[str, Any]] = []
    unrecorded_trades: list[dict[str, Any]] = []

    for r in records:
        prov = _extract_provenance(r)
        if prov == "signal_driven":
            automated_trades.append(r)
        elif prov == "manual":
            manual_trades.append(r)
        else:
            unrecorded_trades.append(r)

    # 3. Compute metrics completely independently (ZERO crosstalk / interference)
    auto_stats = _compute_cohort_metrics(automated_trades, is_automated=True)
    manual_stats = _compute_cohort_metrics(manual_trades, is_automated=False)
    unrecorded_stats = _compute_cohort_metrics(unrecorded_trades, is_automated=False, is_unrecorded=True)

    # 4. Generate contrastive analytical narrative
    contrastive_insights = _build_contrastive_insights(auto_stats, manual_stats, unrecorded_stats)

    # 5. Bridge Health Telemetry
    total_records = len(records)
    bridged_count = sum(1 for r in records if r.get("bridge_status") == "bridged")
    failed_count = sum(1 for r in records if r.get("bridge_status") == "failed")
    disabled_count = sum(1 for r in records if r.get("bridge_status") == "disabled")

    if paper_store and hasattr(paper_store, "get_bridge_completeness_metrics"):
        try:
            bridge_health = paper_store.get_bridge_completeness_metrics()
        except Exception:  # noqa: BLE001
            bridge_health = None
    else:
        bridge_health = None

    if bridge_health is None:
        # Local fallback when no store (or a store lacking the method) was
        # supplied -- mirrors PaperAccountStore.get_bridge_completeness_
        # metrics()'s own anti-fabrication contract: `completeness_pct` is
        # only ever a real percentage when something was actually attempted
        # through the bridge; a cold-start/empty/all-disabled set of records
        # reports `None`/"unknown" rather than a fabricated all-clear
        # (CONSTRAINT #4).
        attempted_count = bridged_count + failed_count
        if attempted_count > 0:
            comp_pct = round((bridged_count / attempted_count) * 100.0, 2)
            status = "healthy" if failed_count == 0 else "degraded"
        elif total_records == 0:
            comp_pct = None
            status = "unknown"
        else:
            # Records exist but none were ever attempted (e.g. every one is
            # "disabled" -- the bridge was off for all of them).
            comp_pct = None
            status = "disabled" if disabled_count == total_records else "unknown"
        bridge_health = {
            "total_closed_trades": total_records,
            "attempted_count": attempted_count,
            "bridged_count": bridged_count,
            "failed_count": failed_count,
            "disabled_count": disabled_count,
            "completeness_pct": comp_pct,
            "status": status,
        }

    # 6. Return strictly partitioned structure with ZERO blended aggregate
    # metrics. `signal_driven_cohort` is a genuine copy of `auto_stats`, not
    # the same dict object aliased under a second key -- both keys exist for
    # interface-contract compatibility (some callers/tests read one name,
    # some the other), but a caller mutating one must never silently mutate
    # the other.
    return {
        "automated_cohort": auto_stats,
        "signal_driven_cohort": dict(auto_stats),
        "manual_cohort": manual_stats,
        "unrecorded_cohort": unrecorded_stats,
        "contrastive_insights": contrastive_insights,
        "bridge_health": bridge_health,
    }
