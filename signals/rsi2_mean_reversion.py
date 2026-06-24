"""
InvestYo Quant Platform - Connors RSI(2) Mean Reversion Signal Module
=======================================================================
Long-only, short-lookback mean-reversion signal (Connors-style RSI(2)) with a
strict regime gate. Unlike the other signal modules (which return a
bidirectional score in [-1.0, 1.0]), this module returns a score in [0.0, 1.0]
because it is long-only by design — there is no short-side analogue here, so
the aggregator only ever sees a non-negative contribution from this module.

Logic
-----
- Trend filter: Close > SMA(200). Mean-reversion longs are only valid with the
  primary trend up; in a downtrend a RSI(2) oversold reading is far more
  likely to be the start of a larger decline than a bounce.
- Entry conviction: RSI(2) < ``oversold_threshold`` (default 10). Score scales
  linearly from 0 at the threshold up to 1.0 as RSI(2) -> 0.
- Already-reverted guard: if Close > SMA(5), the mean-reversion move this
  signal would trade has already happened — score is forced to 0 rather than
  re-firing a stale entry.
- Regime gate: ``is_active_in_regime`` returns False (suppressing this module
  entirely, per signal aggregator wiring) when market_regime is RECESSION or
  CREDIT EVENT, or VIX > 30. Mean reversion is regime-fragile: oversold
  bounces are systematically weaker (or invert into further drawdown) during
  systemic stress, so the module is switched off rather than down-weighted.

Note on position-level exits: this module computes a stateless per-bar ENTRY
score from `row`/`context` alone; it has no visibility into open position
age. The "exit at 5 bars" time-stop from the task spec is a position-
management rule, not a signal-scoring rule, and belongs in the order/position
manager (out of scope for SignalModule.compute). The "close > SMA(5)" exit
condition IS expressible here as the already-reverted guard above, since it
only needs the current row.
"""

import pandas as pd

from dto_models import MacroEconomicDTO
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry

# Regimes during which mean reversion is suppressed entirely (RISK-OFF).
_RISK_OFF_REGIMES = {"RECESSION", "CREDIT EVENT"}
_VIX_RISK_OFF_THRESHOLD = 30.0


class RSI2MeanReversionSignal(SignalModule):
    name = "rsi2_mean_reversion"
    required_features = ["Close", "RSI_2", "SMA_5", "SMA_200"]

    def __init__(self, oversold_threshold: float = 10.0):
        self.oversold_threshold = oversold_threshold

    def is_active_in_regime(self, macro: MacroEconomicDTO) -> bool:
        """RISK-OFF gate: suppressed during RECESSION/CREDIT EVENT or VIX > 30."""
        if macro.market_regime in _RISK_OFF_REGIMES:
            return False
        if macro.vix > _VIX_RISK_OFF_THRESHOLD:
            return False
        return True

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        close = row["Close"]
        rsi_2 = row["RSI_2"]
        sma_5 = row["SMA_5"]
        sma_200 = row["SMA_200"]

        exps = []

        if pd.isna(close) or pd.isna(rsi_2) or pd.isna(sma_5) or pd.isna(sma_200):
            exps.append("DETAIL: Insufficient data for RSI(2) mean reversion (NaN inputs).")
            return SignalOutput(score=0.0, confidence=0.0, explanation="\n".join(exps))

        # 1. Trend filter: long-only mean reversion requires an uptrend.
        if not (close > sma_200):
            exps.append(
                f"0.0: Downtrend (Close {close:.2f} <= SMA200 {sma_200:.2f}) — "
                "RSI(2) mean reversion long disabled."
            )
            return SignalOutput(score=0.0, confidence=1.0, explanation="\n".join(exps))

        # 2. Already-reverted guard: the bounce this signal targets has played out.
        if close > sma_5:
            exps.append(
                f"0.0: Close {close:.2f} already back above SMA5 {sma_5:.2f} — "
                "mean-reversion move has already occurred, no fresh entry."
            )
            return SignalOutput(score=0.0, confidence=1.0, explanation="\n".join(exps))

        # 3. Entry conviction: scales from 0 at the threshold to 1.0 at RSI(2)=0.
        if rsi_2 >= self.oversold_threshold:
            exps.append(f"0.0: RSI(2) {rsi_2:.1f} >= oversold threshold {self.oversold_threshold:.1f}.")
            return SignalOutput(score=0.0, confidence=1.0, explanation="\n".join(exps))

        score = (self.oversold_threshold - rsi_2) / self.oversold_threshold
        score = max(0.0, min(1.0, score))
        exps.append(
            f"{score:.2f}: Oversold-in-uptrend — RSI(2)={rsi_2:.1f} "
            f"(< {self.oversold_threshold:.1f}), Close {close:.2f} > SMA200 {sma_200:.2f}."
        )

        return SignalOutput(score=score, confidence=1.0, explanation="\n".join(exps))


# Auto-register module
global_registry.register(RSI2MeanReversionSignal())
