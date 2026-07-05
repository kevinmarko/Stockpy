"""
InvestYo Quant Platform - Multiple-Testing Correction Across Signal Modules
============================================================================
The platform runs ~17 pluggable signal modules under ``signals/`` (see
``signals/registry.py`` for the registered set), each independently
backtested/validated, often across multiple hyperparameter configurations
within its own harness run.

``validation/metrics.py::deflated_sharpe_ratio`` already corrects for
``n_trials`` SELECTION BIAS *within a single strategy's own hyperparameter
search* — it answers "given that I tried N configurations of THIS strategy
and kept the best one, how much of its apparent Sharpe is luck?"

It does NOT answer the family-wise question: "given that I ALSO tried ~17
other strategies (each with their own trial counts), what is the chance that
AT LEAST ONE of them looks this good purely by chance?" With many strategies
each independently tested, the uncorrected family-wise false-positive rate is
much higher than any single strategy's DSR threshold implies — this is the
classic multiple-comparisons problem (Bonferroni / Benjamini-Hochberg).

This module adds two independent corrections that operate ACROSS the full
signal family, without touching the existing per-strategy DSR/PBO/CPCV
pipeline in ``validation/harness.py``:

1. ``benjamini_hochberg`` — the standard BH step-up FDR-control procedure,
   applied to a vector of p-values (one per strategy/signal-module trial
   family). Controls the expected proportion of false discoveries among all
   "significant" results, which is far less conservative (more power) than a
   flat Bonferroni correction while still controlling the error rate.

2. ``deflated_sharpe_family`` — extends the existing single-strategy
   ``deflated_sharpe_ratio`` (imported and reused verbatim, never
   reimplemented) by substituting the TOTAL trial count across the entire
   signal family for the single-strategy ``n_trials`` argument. Because DSR's
   expected-max-Sharpe-under-the-null term grows with ``n_trials``, feeding
   in the family-wide total produces a strictly more conservative (lower)
   DSR for the same nominal observed Sharpe — exactly the correction needed
   when "the best of 17 independently-tested strategies" is being evaluated,
   not "the best of one strategy's own hyperparameter sweep".

Neither function requires re-running any backtest: both operate purely on
already-computed summary statistics (p-values, Sharpe ratios, trial counts),
matching the "read validation/metrics.py first and reuse it, don't
reimplement" instruction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

from validation.metrics import deflated_sharpe_ratio

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Benjamini-Hochberg (1995) step-up FDR-control procedure
# ---------------------------------------------------------------------------

def benjamini_hochberg(pvalues: Sequence[float], alpha: float = 0.05) -> List[bool]:
    """Standard Benjamini-Hochberg step-up procedure for False Discovery Rate
    (FDR) control across a family of hypothesis tests.

    Procedure
    ---------
    1. Sort the m p-values ascending: p_(1) <= p_(2) <= ... <= p_(m).
    2. Find the largest k such that p_(k) <= (k / m) * alpha.
    3. Reject (declare significant) all hypotheses with p-value <= p_(k).
    4. If no such k exists, reject nothing.

    Parameters
    ----------
    pvalues:
        Raw (uncorrected) p-values, one per hypothesis/strategy trial family.
        NaN entries are treated as non-significant (never rejected) rather
        than raising, since a NaN p-value typically means "could not be
        computed" (e.g. insufficient trade history) — CONSTRAINT #4/#6 spirit:
        never fabricate significance for an unmeasurable quantity.
    alpha:
        Target false discovery rate (default 0.05).

    Returns
    -------
    List[bool]
        Same length and ORDER as *pvalues* — True where the corresponding
        hypothesis is rejected (declared significant after correction),
        False otherwise.

    Edge cases
    ----------
    - Empty input -> empty output (no hypotheses, nothing to reject).
    - All p-values above the BH-adjusted threshold -> nothing rejected.
    - All p-values far below alpha -> everything rejected.
    """
    m = len(pvalues)
    if m == 0:
        return []

    pvals = np.asarray(pvalues, dtype=float)
    # Treat NaN p-values as effectively 1.0 (never significant) so they never
    # participate in — or are accidentally boosted by — the sort/threshold
    # step, and are never rejected.
    safe_pvals = np.where(np.isnan(pvals), 1.0, pvals)

    order = np.argsort(safe_pvals, kind="stable")
    sorted_pvals = safe_pvals[order]

    ranks = np.arange(1, m + 1)
    thresholds = (ranks / m) * alpha

    below_threshold = sorted_pvals <= thresholds
    if not np.any(below_threshold):
        return [False] * m

    # Largest k (1-indexed) satisfying p_(k) <= (k/m)*alpha.
    k = int(np.max(np.where(below_threshold)[0])) + 1
    critical_pvalue = sorted_pvals[k - 1]

    # Reject every original hypothesis whose (safe) p-value is <= the
    # critical p-value — this correctly rejects ties at the boundary too.
    rejected_mask = safe_pvals <= critical_pvalue
    return rejected_mask.tolist()


# ---------------------------------------------------------------------------
# Family-wise Deflated Sharpe Ratio
# ---------------------------------------------------------------------------

@dataclass
class FamilyDSRResult:
    """Per-strategy result of the family-wise DSR correction.

    Attributes
    ----------
    strategy_id : str
        Identifier of the signal module / strategy (e.g. a
        ``signals/registry.py`` module name).
    sharpe_observed : float
        The strategy's own observed (annualized) Sharpe ratio.
    n_trials_own : int
        The strategy's own hyperparameter trial count (what
        ``deflated_sharpe_ratio`` would use in isolation).
    n_trials_family : int
        Total trial count across the ENTIRE signal family (sum of every
        strategy's own trial count) — the deflator actually used here.
    dsr_single_strategy : float
        DSR computed the ORIGINAL way, using only this strategy's own trial
        count. Provided for side-by-side comparison in reports.
    dsr_family_corrected : float
        DSR computed using the family-wide total trial count. Always
        <= ``dsr_single_strategy`` for the same inputs, since a larger
        ``n_trials`` raises the expected-max-Sharpe-under-the-null term,
        which strictly deflates the resulting DSR.
    """
    strategy_id: str
    sharpe_observed: float
    n_trials_own: int
    n_trials_family: int
    dsr_single_strategy: float
    dsr_family_corrected: float


def deflated_sharpe_family(
    sharpe_ratios: Sequence[float],
    n_trials_per_strategy: Sequence[int],
    *,
    strategy_ids: Optional[Sequence[str]] = None,
    sr_variance: float = 1.0,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    n_observations: int = 252,
    freq: int = 252,
) -> List[FamilyDSRResult]:
    """Compute family-wise-corrected Deflated Sharpe Ratios for a family of
    independently-tested strategies (e.g. the ~17 ``signals/`` modules).

    This does NOT reimplement the DSR math — it calls
    ``validation.metrics.deflated_sharpe_ratio`` twice per strategy: once with
    the strategy's own ``n_trials`` (reproducing the existing single-strategy
    behavior) and once with the TOTAL trial count summed across every
    strategy in the family (the correction this module adds).

    Parameters
    ----------
    sharpe_ratios:
        Each strategy's observed (annualized) Sharpe ratio. Same length and
        order as *n_trials_per_strategy*.
    n_trials_per_strategy:
        Each strategy's own hyperparameter-search trial count (what would be
        passed to ``deflated_sharpe_ratio`` in isolation for that strategy).
    strategy_ids:
        Optional human-readable identifiers, same order as the other two
        sequences. Defaults to ``"strategy_0", "strategy_1", ...`` when
        omitted.
    sr_variance, skew, kurtosis, n_observations, freq:
        Passed straight through to ``deflated_sharpe_ratio`` for every
        strategy. In a real deployment these would typically differ per
        strategy (each has its own return-distribution moments); accepting
        them as scalars here keeps the signature simple for the common case
        of an opportunistic aggregate report computed from summary
        statistics already on hand — callers needing per-strategy moments
        can call ``deflated_sharpe_ratio`` directly per row instead.

    Returns
    -------
    List[FamilyDSRResult]
        One result per input strategy, in the same order as the inputs.
        Empty list if the inputs are empty.

    Raises
    ------
    ValueError
        If *sharpe_ratios* and *n_trials_per_strategy* have mismatched
        lengths — a caller bug that should surface loudly rather than
        silently truncate.
    """
    n = len(sharpe_ratios)
    if n != len(n_trials_per_strategy):
        raise ValueError(
            f"sharpe_ratios (len={n}) and n_trials_per_strategy "
            f"(len={len(n_trials_per_strategy)}) must be the same length."
        )
    if n == 0:
        return []

    if strategy_ids is None:
        strategy_ids = [f"strategy_{i}" for i in range(n)]
    elif len(strategy_ids) != n:
        raise ValueError(
            f"strategy_ids (len={len(strategy_ids)}) must match "
            f"sharpe_ratios (len={n})."
        )

    n_trials_family_total = int(sum(int(t) for t in n_trials_per_strategy))

    results: List[FamilyDSRResult] = []
    for sid, sr, n_own in zip(strategy_ids, sharpe_ratios, n_trials_per_strategy):
        try:
            dsr_own = deflated_sharpe_ratio(
                sr_observed=sr,
                n_trials=int(n_own),
                sr_variance=sr_variance,
                skew=skew,
                kurtosis=kurtosis,
                n_observations=n_observations,
                freq=freq,
            )
        except Exception as exc:  # noqa: BLE001 - dead-letter resilience (CONSTRAINT #6)
            logger.warning(
                "deflated_sharpe_family(%s): single-strategy DSR failed: %s", sid, exc,
            )
            dsr_own = float("nan")

        try:
            dsr_family = deflated_sharpe_ratio(
                sr_observed=sr,
                n_trials=n_trials_family_total,
                sr_variance=sr_variance,
                skew=skew,
                kurtosis=kurtosis,
                n_observations=n_observations,
                freq=freq,
            )
        except Exception as exc:  # noqa: BLE001 - dead-letter resilience (CONSTRAINT #6)
            logger.warning(
                "deflated_sharpe_family(%s): family-corrected DSR failed: %s", sid, exc,
            )
            dsr_family = float("nan")

        results.append(
            FamilyDSRResult(
                strategy_id=sid,
                sharpe_observed=float(sr),
                n_trials_own=int(n_own),
                n_trials_family=n_trials_family_total,
                dsr_single_strategy=dsr_own,
                dsr_family_corrected=dsr_family,
            )
        )

    return results


def format_multiple_testing_summary(
    bh_rejected: List[bool],
    strategy_ids: Optional[Sequence[str]] = None,
    family_dsr_results: Optional[List[FamilyDSRResult]] = None,
) -> str:
    """Human-readable summary block for the multiple-testing correction,
    mirroring the style of ``validation/stress_scenarios.py::format_stress_summary``.
    """
    lines: List[str] = []
    lines.append("=" * 64)
    lines.append(" MULTIPLE-TESTING CORRECTION (family-wise, across signal modules)")
    lines.append("=" * 64)

    if not bh_rejected and not family_dsr_results:
        lines.append(" NO RESULTS — nothing to report.")
        lines.append("=" * 64)
        return "\n".join(lines)

    if bh_rejected:
        ids = strategy_ids if strategy_ids is not None else [f"strategy_{i}" for i in range(len(bh_rejected))]
        n_rejected = sum(bh_rejected)
        lines.append(f" Benjamini-Hochberg: {n_rejected}/{len(bh_rejected)} significant after FDR correction")
        for sid, rej in zip(ids, bh_rejected):
            lines.append(f"   {sid:<24} {'SIGNIFICANT' if rej else 'not significant'}")
        lines.append("-" * 64)

    if family_dsr_results:
        lines.append(f" {'Strategy':<24} {'SR':>7} {'DSR(own)':>10} {'DSR(family)':>12}")
        for r in family_dsr_results:
            lines.append(
                f" {r.strategy_id:<24} {r.sharpe_observed:>7.2f} "
                f"{r.dsr_single_strategy:>10.3f} {r.dsr_family_corrected:>12.3f}"
            )

    lines.append("=" * 64)
    return "\n".join(lines)
