"""
InvestYo Quant Platform - Jegadeesh-Titman Cross-Sectional Momentum Signal
===========================================================================
Reference: Jegadeesh & Titman (1993), "Returns to Buying Winners and Selling
Losers: Implications for Stock Market Efficiency," Journal of Finance 48(1):65-91.

STRATEGY LOGIC
--------------
Formation period : 12 months, skipping the most-recent month (avoids 1-month
                   short-term reversal documented by Jegadeesh 1990).
Return formula   : r = price[t-22] / price[t-252] - 1
                   where t-22 skips ~1 month and t-252 is the 12-month lookback.
Holding period   : 1 month (rebalanced by the orchestrator).
Universe         : All tickers in the current pipeline run.

SIGNAL ARCHITECTURE
-------------------
This module uses the two-phase hook pattern:

  pre_compute(universe_df, context)  — ONCE per cycle
      Reads ``XSec_12_1M`` return from universe_df (pre-computed by the
      orchestrator via vectorized shift operations on the full price matrix).
      Computes cross-sectional percentile ranks in one vectorized call.
      Stores {ticker: rank} in context.xsec_percentile_ranks.

  compute(row, context)              — once PER TICKER
      Looks up the ticker's rank from context.xsec_percentile_ranks.
      Returns score = 2 * (rank - 0.5), mapping [0, 1] → [-1, +1].

LOOKAHEAD PREVENTION
--------------------
- The 12-1m return uses shift(22) and shift(252) computed by the orchestrator
  before the per-ticker loop, so t never includes current-month data.
- pre_compute only reads columns already present in universe_df — it never
  fetches new data or peeks at future rows.

LONG-ONLY SCOPE
---------------
Bottom-quintile short overlay is not wired by default (retail simplicity).
Weight = 15.0 in SIGNAL_WEIGHTS; negative scores reduce Kelly Target naturally.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry
from settings import settings

logger = logging.getLogger(__name__)

# Column written by the orchestrator helper into dashboard_df / universe_df
XSEC_RETURN_COL = "XSec_12_1M"
SYMBOL_COL = "Symbol"


class CrossSectionalMomentumSignal(SignalModule):
    """
    Jegadeesh-Titman cross-sectional momentum signal module.

    Uses the pre_compute / compute two-phase pattern to avoid recomputing
    universe-wide ranks once per ticker.
    """

    name = "cross_sectional_momentum"
    required_features: list[str] = []  # Cross-sectional data lives in context, not row

    # ------------------------------------------------------------------ #
    # Phase 1: called once per cycle on the full universe DataFrame        #
    # ------------------------------------------------------------------ #

    def pre_compute(
        self,
        universe_df: pd.DataFrame,
        context: SignalContext,
    ) -> None:
        """Compute universe percentile ranks from 12-1m returns.

        Parameters
        ----------
        universe_df : pd.DataFrame
            Dashboard DataFrame with one row per ticker.
            Must contain columns ``Symbol`` and ``XSec_12_1M``.
        context : SignalContext
            Shared context; ``xsec_percentile_ranks`` is populated in-place.
        """
        if SYMBOL_COL not in universe_df.columns:
            logger.warning(
                "CrossSectionalMomentumSignal.pre_compute: '%s' column missing; "
                "ranks will be empty.",
                SYMBOL_COL,
            )
            return

        if XSEC_RETURN_COL not in universe_df.columns:
            logger.warning(
                "CrossSectionalMomentumSignal.pre_compute: '%s' column missing; "
                "ranks will be empty.  Ensure main_orchestrator calls "
                "compute_xsec_momentum_ranks() before run_pre_compute().",
                XSEC_RETURN_COL,
            )
            return

        # Vectorized percentile rank — ascending=True means low returns get low rank
        raw_returns: pd.Series = universe_df.set_index(SYMBOL_COL)[XSEC_RETURN_COL]
        valid_returns = raw_returns.dropna()

        if len(valid_returns) < 1:
            logger.warning(
                "CrossSectionalMomentumSignal.pre_compute: no valid "
                "returns; cannot compute cross-sectional ranks."
            )
            return

        # pct_rank in [0, 1]; ties broken by average (pandas default)
        pct_ranks: pd.Series = valid_returns.rank(pct=True, ascending=True)

        context.xsec_percentile_ranks = pct_ranks.to_dict()
        logger.info(
            "CrossSectionalMomentumSignal.pre_compute: ranked %d tickers.",
            len(pct_ranks),
        )

    # ------------------------------------------------------------------ #
    # Phase 2: called once per ticker                                       #
    # ------------------------------------------------------------------ #

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        """Map this ticker's cross-sectional rank to a [-1, +1] score.

        Parameters
        ----------
        row : pd.Series
            Per-ticker indicator row (``Symbol`` key must be present).
        context : SignalContext
            Shared context containing pre-computed ``xsec_percentile_ranks``.

        Returns
        -------
        SignalOutput
            score = 2 * (rank - 0.5), confidence = |score|, explanation string.
        """
        ticker: str = str(row.get(SYMBOL_COL, ""))
        ranks = context.xsec_percentile_ranks

        if not ranks or ticker not in ranks:
            return SignalOutput(
                score=0.0,
                confidence=0.0,
                explanation=(
                    f"WARNING: Cross-sectional rank unavailable for {ticker}. "
                    "Score set to 0 (neutral)."
                ),
            )

        rank: float = ranks[ticker]  # [0, 1]
        # Linear mapping: rank=1.0 → score=+1.0 (top), rank=0.0 → score=-1.0 (bottom)
        score: float = 2.0 * (rank - 0.5)

        weight = settings.SIGNAL_WEIGHTS.get(self.name, 15.0)
        contrib = score * weight

        quintile = _quintile_label(rank)
        direction = "Bullish" if score > 0 else ("Bearish" if score < 0 else "Neutral")
        sign = "+" if contrib >= 0 else ""

        explanation = (
            f"{sign}{contrib:.1f}pts: XSec Momentum {direction} "
            f"(rank={rank:.3f}, {quintile}, score={score:+.3f})"
        )
        return SignalOutput(
            score=score,
            confidence=abs(score),
            explanation=explanation,
        )


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _quintile_label(rank: float) -> str:
    """Return a human-readable quintile label for a [0, 1] percentile rank."""
    if rank >= 0.80:
        return "Q5-Winner"
    if rank >= 0.60:
        return "Q4"
    if rank >= 0.40:
        return "Q3"
    if rank >= 0.20:
        return "Q2"
    return "Q1-Loser"


# Auto-register
global_registry.register(CrossSectionalMomentumSignal())
