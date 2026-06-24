"""
InvestYo Quant Platform - Fama-French-Style Multifactor Cross-Sectional Signal
================================================================================
Reference: Fama & French (1992, 1993) value/size factors; Hou, Xue & Zhang
(2020), "Replicating Anomalies," Review of Financial Studies 33(5):2019-2133
-- ~65% of published cross-sectional "anomalies" fail to replicate out of
sample, so this module is deliberately restricted to the four factors with
the strongest, most-replicated economic priors:

  Value     : book-to-market (1/P/B) and earnings yield (1/P/E)
  Quality   : ROE + operating margin (debt/equity proxy if unavailable)
  Low Vol   : negative of trailing 60-day realized volatility
  Size      : negative of log market cap (smaller = positive), microcap-excluded

Momentum (the fifth Fama-French-style factor with a strong prior) is already
implemented separately in signals/cross_sectional_momentum.py and is NOT
duplicated here.

SIGNAL ARCHITECTURE
--------------------
Two-phase hook pattern, same convention as CrossSectionalMomentumSignal:

  pre_compute(universe_df, context)  -- ONCE per cycle
      Reads the raw factor inputs (book_to_market, earnings_yield,
      quality_factor_score, low_vol_score, log_market_cap, Market Cap)
      written into universe_df by processing_engine.calculate_fundamental_metrics().
      Excludes microcaps (Market Cap < settings.MULTIFACTOR_MICROCAP_THRESHOLD)
      from the cross-sectional z-scoring population, z-scores each input,
      winsorizes at +/-3, averages into per-factor Z's and a composite, and
      stores everything in context.multifactor_scores keyed by ticker.

  compute(row, context)              -- once PER TICKER
      Looks up the ticker's composite Z from context.multifactor_scores and
      maps it to [-1, +1] via tanh(z / 2). Microcap-excluded or
      data-unavailable tickers get a neutral 0.0 score (never a fabricated
      factor exposure).

LOOKAHEAD PREVENTION
---------------------
- All raw inputs are themselves lookahead-free: book_to_market/earnings_yield/
  quality_factor_score/log_market_cap come from current point-in-time
  yfinance fundamentals (not historical data subject to shift errors), and
  low_vol_score is sourced from calculate_momentum_metrics()'s
  Realized_Vol_60D, which uses .shift(1) before its rolling window.
- pre_compute only reads columns already present in universe_df -- it never
  fetches new data or peeks at future rows.
- Cross-sectional z-scoring uses only the current cycle's universe; no
  forward-looking statistics are used.

LONG/SHORT SCOPE
------------------
Score can be negative (cheap-but-low-quality / expensive-high-vol names
score negatively), unlike the long-only RSI(2) module. Weight = 15.0 in
SIGNAL_WEIGHTS (see settings.py comment for why this deviates from the
"0.15" figure used in the original task spec -- it is rescaled to match this
codebase's existing points-scale weight convention, where contribution =
score[-1,1] * weight and weights for other modules range 10-45).
"""

from __future__ import annotations

import logging
import math
from typing import Dict, Optional

import numpy as np
import pandas as pd

from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry
from settings import settings

logger = logging.getLogger(__name__)

SYMBOL_COL = "Symbol"
MARKET_CAP_COL = "Market Cap"

# Raw factor-input columns written into dashboard_df by
# processing_engine.calculate_fundamental_metrics() / calculate_technical_metrics().
RAW_INPUT_COLS = [
    "book_to_market",
    "earnings_yield",
    "quality_factor_score",
    "low_vol_score",
    "log_market_cap",
]

WINSOR_LIMIT = 3.0


def _zscore_winsorize(series: pd.Series, limit: float = WINSOR_LIMIT) -> pd.Series:
    """Cross-sectional z-score, then clip to [-limit, +limit].

    NaN inputs propagate as NaN (never fabricated as 0); a zero-variance
    cross-section (all values identical, or <2 valid observations) returns
    all-NaN rather than dividing by zero.
    """
    valid = series.dropna()
    if len(valid) < 2:
        return pd.Series(float("nan"), index=series.index)

    mean = valid.mean()
    std = valid.std(ddof=1)
    if not std or std == 0.0 or math.isnan(std):
        return pd.Series(float("nan"), index=series.index)

    z = (series - mean) / std
    return z.clip(lower=-limit, upper=limit)


class MultifactorSignal(SignalModule):
    """
    Fama-French-style multifactor (Value, Quality, Low-Vol, Size) cross-
    sectional signal module.

    Uses the pre_compute / compute two-phase pattern, mirroring
    CrossSectionalMomentumSignal, to z-score the full universe once per cycle
    rather than per ticker.
    """

    name = "multifactor"
    required_features: list[str] = []  # Cross-sectional data lives in context, not row

    # ------------------------------------------------------------------ #
    # Phase 1: called once per cycle on the full universe DataFrame        #
    # ------------------------------------------------------------------ #

    def pre_compute(
        self,
        universe_df: pd.DataFrame,
        context: SignalContext,
    ) -> None:
        """Compute per-ticker Value_Z / Quality_Z / LowVol_Z / Size_Z / composite.

        Parameters
        ----------
        universe_df : pd.DataFrame
            Dashboard DataFrame with one row per ticker. Must contain
            ``Symbol``, ``Market Cap``, and the RAW_INPUT_COLS.
        context : SignalContext
            Shared context; ``multifactor_scores`` is populated in-place.
        """
        if SYMBOL_COL not in universe_df.columns:
            logger.warning("MultifactorSignal.pre_compute: '%s' column missing; scores will be empty.", SYMBOL_COL)
            return

        missing_inputs = [c for c in RAW_INPUT_COLS if c not in universe_df.columns]
        if missing_inputs:
            logger.warning(
                "MultifactorSignal.pre_compute: missing raw input columns %s; "
                "ensure processing_engine.calculate_fundamental_metrics() ran first. "
                "Scores will be empty.",
                missing_inputs,
            )
            return

        df = universe_df.set_index(SYMBOL_COL)
        market_cap = df.get(MARKET_CAP_COL, pd.Series(float("nan"), index=df.index))

        microcap_threshold = settings.MULTIFACTOR_MICROCAP_THRESHOLD
        is_microcap = market_cap.fillna(0.0) < microcap_threshold

        scores: Dict[str, Dict[str, float]] = {}

        # Microcaps are excluded from the cross-sectional population entirely
        # (they neither contribute to the mean/std nor receive an exposure).
        eligible_df = df.loc[~is_microcap]

        b2m_z = _zscore_winsorize(eligible_df["book_to_market"])
        ey_z = _zscore_winsorize(eligible_df["earnings_yield"])
        quality_z = _zscore_winsorize(eligible_df["quality_factor_score"])
        lowvol_z = _zscore_winsorize(eligible_df["low_vol_score"])
        size_z_raw = _zscore_winsorize(eligible_df["log_market_cap"])

        value_z = pd.concat([b2m_z, ey_z], axis=1).mean(axis=1, skipna=True)
        size_z = -size_z_raw  # smaller market cap -> positive size exposure

        composite = pd.concat(
            [value_z, quality_z, lowvol_z, size_z], axis=1
        ).mean(axis=1, skipna=True).clip(lower=-WINSOR_LIMIT, upper=WINSOR_LIMIT)

        for ticker in df.index:
            if ticker in is_microcap.index and bool(is_microcap.loc[ticker]):
                scores[str(ticker)] = {
                    "Value_Z": float("nan"),
                    "Quality_Z": float("nan"),
                    "LowVol_Z": float("nan"),
                    "Size_Z": float("nan"),
                    "Multifactor_Composite": float("nan"),
                    "excluded_microcap": True,
                }
                continue

            scores[str(ticker)] = {
                "Value_Z": float(value_z.get(ticker, float("nan"))),
                "Quality_Z": float(quality_z.get(ticker, float("nan"))),
                "LowVol_Z": float(lowvol_z.get(ticker, float("nan"))),
                "Size_Z": float(size_z.get(ticker, float("nan"))),
                "Multifactor_Composite": float(composite.get(ticker, float("nan"))),
                "excluded_microcap": False,
            }

        context.multifactor_scores = scores
        logger.info(
            "MultifactorSignal.pre_compute: scored %d tickers (%d microcap-excluded).",
            len(scores), int(is_microcap.sum()),
        )

    # ------------------------------------------------------------------ #
    # Phase 2: called once per ticker                                       #
    # ------------------------------------------------------------------ #

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        """Map this ticker's composite Z-score to a [-1, +1] score via tanh(z/2).

        Returns a neutral 0.0 score (never a fabricated factor exposure) if
        the ticker is microcap-excluded, missing from context.multifactor_scores,
        or its composite is NaN (e.g. insufficient cross-sectional population).
        """
        ticker: str = str(row.get(SYMBOL_COL, ""))
        entry: Optional[Dict[str, float]] = context.multifactor_scores.get(ticker)

        if entry is None:
            return SignalOutput(
                score=0.0,
                confidence=0.0,
                explanation=(
                    f"WARNING: Multifactor scores unavailable for {ticker}. "
                    "Score set to 0 (neutral)."
                ),
            )

        if entry.get("excluded_microcap"):
            return SignalOutput(
                score=0.0,
                confidence=0.0,
                explanation=f"DETAIL: {ticker} excluded from multifactor scoring (microcap).",
            )

        composite = entry.get("Multifactor_Composite", float("nan"))
        if composite is None or (isinstance(composite, float) and math.isnan(composite)):
            return SignalOutput(
                score=0.0,
                confidence=0.0,
                explanation=(
                    f"WARNING: Multifactor composite is NaN for {ticker} "
                    "(insufficient cross-sectional data). Score set to 0 (neutral)."
                ),
            )

        score = float(np.tanh(composite / 2.0))
        weight = settings.SIGNAL_WEIGHTS.get(self.name, 15.0)
        contrib = score * weight
        sign = "+" if contrib >= 0 else ""

        explanation = (
            f"{sign}{contrib:.1f}pts: Multifactor composite={composite:+.2f} "
            f"(Value={entry.get('Value_Z', float('nan')):+.2f}, "
            f"Quality={entry.get('Quality_Z', float('nan')):+.2f}, "
            f"LowVol={entry.get('LowVol_Z', float('nan')):+.2f}, "
            f"Size={entry.get('Size_Z', float('nan')):+.2f}) -> score={score:+.3f}"
        )
        return SignalOutput(
            score=score,
            confidence=abs(score),
            explanation=explanation,
        )


# Auto-register
global_registry.register(MultifactorSignal())
