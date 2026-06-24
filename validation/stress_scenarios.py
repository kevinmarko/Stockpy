"""
InvestYo Quant Platform - Tail-Scenario Stress Testing
========================================================
Options-selling strategies harvest the Volatility Risk Premium (VRP), which is
real and persistent. But the payoff is *negatively skewed*: many small wins
punctuated by rare, violent losses. A Sharpe ratio, a CPCV path distribution,
and even a 23-year backtest can all look excellent while quietly hiding the
fact that the strategy would have been wiped out in a single tail event. The
standard validation gates (PBO/DSR/Sharpe/MaxDD over the *full* sample) do not
protect against this, because a multi-year average drawdown washes out a
two-week catastrophe.

This module therefore replays each candidate options-selling strategy across a
fixed set of historically dated shock windows and asks one blunt question per
window: *would this strategy have survived, and how deep was the hole?*

RATIONALE / FAILURE MODES BEING TESTED
---------------------------------------
- OCT_2008 (Lehman aftermath): VIX peaked above 80. Short-gamma books that
  rolled untouched through September were destroyed in October as realized vol
  dwarfed every strike sold.
- FEB_2018 ("Volmageddon"): the XIV / short-VIX-ETP complex blew up in a single
  session when VIX nearly doubled intraday. Tests survival against a sudden
  vol-of-vol spike rather than a slow grind.
- MAR_2020 (COVID crash + rebound): the fastest peak-to-trough in market
  history, immediately followed by a violent recovery — punishes naked short
  puts on the way down AND short calls on the way back up.
- AUG_2024 (yen carry unwind): a sharp, liquidity-driven deleveraging spike.
  A recent reminder that tail events are not confined to the distant past.

DESIGN
------
Strategy returns for a window are produced by a caller-supplied
``returns_fn(start, end) -> pd.Series`` of *daily strategy returns* (fraction,
e.g. 0.01 == +1%). This keeps the module decoupled from how any particular
options strategy is simulated — the harness, a Backtrader run, or a test mock
can all supply it. No data is fabricated here: if ``returns_fn`` yields an
empty series for a window, that scenario is recorded with ``error`` set and is
treated as a *gate failure* (fail-closed), never silently skipped or passed.

DEPLOYABILITY GATE
------------------
An options-selling strategy passes the stress gate iff, in EVERY scenario
window: (a) max drawdown < ``MAX_STRESS_DRAWDOWN`` (50%), AND (b) the account
survived (cumulative equity never hit zero — i.e. no single daily return
<= -100% and the compounded equity curve stayed strictly positive throughout).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Deployability threshold: an options-selling strategy must keep max drawdown
# strictly below this in EVERY stress window. 50% is a deliberately lenient
# survival bar — a short-vol book down 50% in a two-week shock is already in
# serious trouble; anything worse is disqualifying regardless of full-sample
# metrics.
MAX_STRESS_DRAWDOWN: float = 0.50


@dataclass(frozen=True)
class StressScenario:
    """A single dated historical shock window.

    Attributes
    ----------
    name : str
        Stable identifier (e.g. "MAR_2020").
    start, end : str
        Inclusive window bounds, "YYYY-MM-DD".
    expected_max_dd_for_short_vol : float
        Approximate historical drawdown magnitude (positive fraction) an
        *unhedged* short-vol book would have suffered in this window. This is a
        documented reference point for report readers — NOT a pass/fail
        threshold (the gate uses MAX_STRESS_DRAWDOWN). Sourced from published
        post-mortems of each event, rounded conservatively.
    description : str
        One-line human summary of the event.
    """
    name: str
    start: str
    end: str
    expected_max_dd_for_short_vol: float
    description: str

    def as_tuple(self) -> tuple:
        """(start, end, expected_max_dd_for_short_vol_strategies) — the tuple
        form requested by the task spec."""
        return (self.start, self.end, self.expected_max_dd_for_short_vol)


# Ordered registry of the canonical tail scenarios.
STRESS_SCENARIOS: Dict[str, StressScenario] = {
    "OCT_2008": StressScenario(
        name="OCT_2008",
        start="2008-10-01",
        end="2008-11-30",
        expected_max_dd_for_short_vol=0.70,
        description="Lehman aftermath; VIX peaked above 80.",
    ),
    "FEB_2018": StressScenario(
        name="FEB_2018",
        start="2018-02-01",
        end="2018-02-15",
        expected_max_dd_for_short_vol=0.90,
        description="XIV / short-vol ETP blowup ('Volmageddon').",
    ),
    "MAR_2020": StressScenario(
        name="MAR_2020",
        start="2020-02-15",
        end="2020-04-15",
        expected_max_dd_for_short_vol=0.60,
        description="COVID crash + rebound; fastest peak-to-trough on record.",
    ),
    "AUG_2024": StressScenario(
        name="AUG_2024",
        start="2024-08-01",
        end="2024-08-15",
        expected_max_dd_for_short_vol=0.30,
        description="Yen carry-trade unwind; sharp liquidity-driven vol spike.",
    ),
}


@dataclass
class StressResult:
    """Outcome of replaying a strategy across one stress window."""
    scenario: str
    start: str
    end: str
    max_drawdown: float          # positive magnitude; NaN if no data
    final_return: float          # compounded return over the window; NaN if no data
    survived: bool               # cumulative equity stayed strictly > 0 throughout
    n_days: int
    expected_max_dd_for_short_vol: float
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        """This single window passes iff the account survived AND max drawdown
        is strictly below MAX_STRESS_DRAWDOWN. A window with an error (no data)
        fails closed."""
        if self.error is not None:
            return False
        if not self.survived:
            return False
        if np.isnan(self.max_drawdown):
            return False
        return self.max_drawdown < MAX_STRESS_DRAWDOWN


# Type alias for the caller-supplied per-window returns provider.
ReturnsFn = Callable[[str, str], pd.Series]


def compute_max_drawdown(returns: pd.Series) -> float:
    """Max drawdown magnitude (positive fraction) of a daily-returns series.

    Returns NaN for an empty series (never fabricates 0.0, which would falsely
    read as 'no drawdown'). Uses compounded equity, so a single -100% day
    produces a 1.0 (total) drawdown.
    """
    if returns is None or len(returns) == 0:
        return float("nan")
    equity = (1.0 + returns.astype(float)).cumprod()
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    return float(abs(drawdown.min()))


def account_survived(returns: pd.Series) -> bool:
    """True iff the compounded equity curve stays strictly positive throughout.

    An options-selling account "blows up" if any daily return is <= -100%
    (equity hits or crosses zero) — at that point the equity curve is wiped out
    and all subsequent compounding is meaningless. An empty series is treated
    as non-survival (fail-closed: we cannot assert survival without data).
    """
    if returns is None or len(returns) == 0:
        return False
    r = returns.astype(float)
    if (r <= -1.0).any():
        return False
    equity = (1.0 + r).cumprod()
    return bool((equity > 0.0).all())


def run_stress_scenario(returns_fn: ReturnsFn, scenario: StressScenario) -> StressResult:
    """Replays one scenario window and computes its StressResult.

    Any exception raised by ``returns_fn`` (e.g. a failed data download) is
    caught and recorded as an ``error`` — a stress test that cannot run must
    fail the gate, never crash the surrounding validation pipeline.
    """
    try:
        returns = returns_fn(scenario.start, scenario.end)
    except Exception as e:  # noqa: BLE001 - logged with context, recorded as gate failure
        logger.error("Stress scenario %s: returns_fn raised: %s", scenario.name, e)
        return StressResult(
            scenario=scenario.name, start=scenario.start, end=scenario.end,
            max_drawdown=float("nan"), final_return=float("nan"), survived=False,
            n_days=0, expected_max_dd_for_short_vol=scenario.expected_max_dd_for_short_vol,
            error=str(e),
        )

    if returns is None or len(returns) == 0:
        logger.warning("Stress scenario %s: returns_fn produced no data for %s..%s.",
                       scenario.name, scenario.start, scenario.end)
        return StressResult(
            scenario=scenario.name, start=scenario.start, end=scenario.end,
            max_drawdown=float("nan"), final_return=float("nan"), survived=False,
            n_days=0, expected_max_dd_for_short_vol=scenario.expected_max_dd_for_short_vol,
            error="no data in window",
        )

    r = returns.astype(float)
    max_dd = compute_max_drawdown(r)
    survived = account_survived(r)
    final_return = float((1.0 + r).prod() - 1.0)

    return StressResult(
        scenario=scenario.name, start=scenario.start, end=scenario.end,
        max_drawdown=max_dd, final_return=final_return, survived=survived,
        n_days=int(len(r)), expected_max_dd_for_short_vol=scenario.expected_max_dd_for_short_vol,
    )


def run_stress_tests(
    returns_fn: ReturnsFn,
    scenarios: Optional[Dict[str, StressScenario]] = None,
) -> Dict[str, StressResult]:
    """Runs every scenario and returns {scenario_name: StressResult}."""
    scenarios = scenarios if scenarios is not None else STRESS_SCENARIOS
    return {name: run_stress_scenario(returns_fn, sc) for name, sc in scenarios.items()}


def passes_stress_gate(results: Optional[Dict[str, StressResult]]) -> bool:
    """Deployability gate for options-selling strategies.

    Returns True iff results are present for at least the canonical scenarios
    AND every scenario individually passes (survived + max_dd < MAX_STRESS_DRAWDOWN).
    Empty/None results fail closed — an options-selling strategy that was never
    stress-tested is NOT deployable.
    """
    if not results:
        return False
    # Require coverage of every canonical scenario (fail-closed on missing windows).
    if not set(STRESS_SCENARIOS.keys()).issubset(set(results.keys())):
        return False
    return all(res.passed for res in results.values())


def format_stress_summary(results: Optional[Dict[str, StressResult]]) -> str:
    """Human-readable stress summary block, intended to be printed at the TOP of
    every validation report so the tail risk is the first thing a reviewer sees.
    """
    lines: List[str] = []
    lines.append("=" * 64)
    lines.append(" TAIL-SCENARIO STRESS TEST (options-selling survival check)")
    lines.append("=" * 64)

    if not results:
        lines.append(" NO STRESS RESULTS — options-selling strategy NOT deployable.")
        lines.append("=" * 64)
        return "\n".join(lines)

    gate_pass = passes_stress_gate(results)
    lines.append(f" GATE: {'PASS' if gate_pass else 'FAIL'}  "
                 f"(max DD < {MAX_STRESS_DRAWDOWN*100:.0f}% AND survives, every window)")
    lines.append("-" * 64)
    header = f" {'Scenario':<10} {'MaxDD':>8} {'Final':>9} {'Survived':>9} {'Result':>8}"
    lines.append(header)
    for name in (results.keys()):
        res = results[name]
        if res.error is not None:
            lines.append(f" {name:<10} {'n/a':>8} {'n/a':>9} {'NO':>9} {'FAIL':>8}  ({res.error})")
            continue
        lines.append(
            f" {name:<10} {res.max_drawdown*100:>7.1f}% {res.final_return*100:>8.1f}% "
            f"{('YES' if res.survived else 'NO'):>9} {('PASS' if res.passed else 'FAIL'):>8}"
        )
    lines.append("=" * 64)
    return "\n".join(lines)
