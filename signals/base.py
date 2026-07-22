"""
InvestYo Quant Platform - Pluggable Signal Abstractions
======================================================
Defines the abstract base class and data transfer objects for signal modules.

CHANGE LOG
----------
- Added `xsec_percentile_ranks` to SignalContext: an optional dict mapping
  ticker -> float [0, 1] universe-relative percentile rank.  All existing
  modules ignore it (default empty dict = no-op).
- Added `pre_compute(universe_df, context)` no-op hook to SignalModule.
  Cross-sectional modules override it to compute universe-wide statistics
  once per orchestrator cycle instead of redundantly per ticker.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import pandas as pd

from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO


@dataclass
class SignalContext:
    """Holds metadata and configuration objects for signal evaluation.

    Attributes
    ----------
    bar : MarketBarDTO
    fundamentals : FundamentalDataDTO
    macro : MacroEconomicDTO
    xsec_percentile_ranks : dict[str, float]
        Universe-wide cross-sectional percentile ranks keyed by ticker.
        Populated once per cycle by SignalRegistry.run_pre_compute().
        Empty dict when no cross-sectional module is active.
    multifactor_scores : dict[str, dict[str, float]]
        Per-ticker Fama-French-style factor scores keyed by ticker, each a
        dict with keys "Value_Z", "Quality_Z", "LowVol_Z", "Size_Z",
        "Multifactor_Composite", "excluded_microcap" (bool). Populated once
        per cycle by signals/multifactor.py's pre_compute(). Empty dict when
        the multifactor module hasn't run pre_compute yet this cycle.
    """
    bar: MarketBarDTO
    fundamentals: FundamentalDataDTO
    macro: MacroEconomicDTO
    xsec_percentile_ranks: Dict[str, float] = field(default_factory=dict)
    multifactor_scores: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # Per-ticker LGBM cross-sectional rank percentiles (populated by
    # LGBMRankerSignal.pre_compute; empty dict = module did not run this cycle).
    lgbm_scores: Dict[str, float] = field(default_factory=dict)
    # News sentiment scores (Tier 2.4) keyed by symbol (float in [-1, +1]).
    # Populated by NewsCatalystSignal.pre_compute(); empty when Finnhub is
    # not configured or the module hasn't run this cycle.
    news_sentiment_scores: Dict[str, float] = field(default_factory=dict)
    # Next earnings dates as ISO-date strings keyed by symbol ("" = unknown).
    # Populated by NewsCatalystSignal.pre_compute() alongside news_sentiment_scores.
    earnings_dates: Dict[str, str] = field(default_factory=dict)
    # Multi-source credibility-weighted sentiment aggregate (Sentiment Pipeline
    # Phase 4), keyed by symbol, each a dict with keys
    # "credibility_weighted_sentiment", "bot_activity_ratio",
    # "aggregated_source_credibility". Populated once per cycle by
    # NewsCatalystSignal.pre_compute() from
    # HistoricalStore.get_sentiment_aggregate_by_symbol(). Empty dict when no
    # multi-source social documents exist for this trading day (distinct from
    # news_sentiment_scores, which is Finnhub-headline-only).
    sentiment_credibility_scores: Dict[str, Dict[str, float]] = field(default_factory=dict)


@dataclass
class SignalOutput:
    """Standardized output returned by a single signal module.

    Attributes
    ----------
    score : float
        Scaled directional value in [-1.0, 1.0]. Multiplied by the module's
        configured weight in ``settings.SIGNAL_WEIGHTS`` before being added
        to the 50-point neutral base in ``SignalAggregator``.
    confidence : float
        Sizing probability / reliability metric in [0.0, 1.0]. Used by
        ``regime_multiplier`` to carry the HMM second-opinion multiplier;
        default value of 1.0 is a no-op for all other modules.
    explanation : str
        Rationale log for verbose explainer notes. Lines prefixed
        ``WARNING:`` are collected as warnings; ``DETAIL:`` as details;
        all others as score_log entries.
    meta_label_proba : float
        **Stage 4 placeholder — Lopez de Prado meta-label probability.**
        Represents the meta-model's estimated probability that this signal
        is *currently* generating a true positive (not a false positive).
        Default 1.0 is a multiplicative no-op so the field has zero
        behavioral effect until a real meta-label model is wired in.
        ``SignalAggregator.aggregate()`` collects this across all active
        modules and returns ``meta_label_composite`` (geometric mean of
        active modules' ``meta_label_proba`` values); ``StrategyEngine``
        then multiplies the final Kelly Target by it. When all modules
        return 1.0, ``meta_label_composite = 1.0`` and sizing is unchanged.
    """
    score: float           # Scaled value in [-1.0, 1.0]
    confidence: float      # Sizing probability/reliability metric in [0.0, 1.0]
    explanation: str       # Rationale log for verbose explainer notes
    meta_label_proba: float = 1.0  # Stage 4 meta-label placeholder (default=1.0, no-op)


class SignalModule(ABC):
    """Abstract base class for all signal modules in the strategy engine.

    Per-ticker hook
    ---------------
    ``compute(row, context)`` — called once per ticker per cycle.

    Cross-sectional hook (optional override)
    ----------------------------------------
    ``pre_compute(universe_df, context)`` — called **once per cycle** before
    the per-ticker loop.  Default is a no-op.  Override in cross-sectional
    modules (e.g. CrossSectionalMomentumSignal) to compute universe-wide
    statistics and store them in ``context.xsec_percentile_ranks``.
    """

    name: str = ""
    required_features: List[str] = []

    def is_active_in_regime(self, macro: MacroEconomicDTO) -> bool:
        """Whether this module should contribute to the aggregate score this cycle.

        Default implementation is always-active (True) — most signals (macro
        regime, valuation, momentum, etc.) are regime-agnostic by design; the
        macro regime itself is already a separate signal input. Override this
        in regime-fragile modules (e.g. RSI(2) mean reversion) to return False
        during RISK-OFF conditions, where the strategy's edge is known to
        degrade or invert (see signals/rsi2_mean_reversion.py).

        Parameters
        ----------
        macro : MacroEconomicDTO
            Current-cycle macro context (``market_regime``, ``vix``, ``killSwitch``).

        Returns
        -------
        bool
            False suppresses this module's contribution entirely for this cycle
            (SignalAggregator skips both its score and its explanation lines).
        """
        return True

    def pre_compute(
        self,
        universe_df: pd.DataFrame,
        context: SignalContext,
    ) -> None:
        """Pre-compute universe-wide statistics before the per-ticker loop.

        Default implementation is a no-op.  Cross-sectional modules override
        this to populate ``context.xsec_percentile_ranks`` in one vectorized
        pass over the full universe DataFrame.

        Parameters
        ----------
        universe_df : pd.DataFrame
            One row per ticker with at least a ``Symbol`` column and any
            features needed for cross-sectional ranking (e.g. ``XSec_12_1M``).
        context : SignalContext
            Shared context object whose ``xsec_percentile_ranks`` dict will be
            mutated in-place.
        """

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        """Executes signal calculation logic in bulk across a universe DataFrame.

        Default implementation falls back to calling the scalar `compute()` method
        row-by-row. Modules that can be vectorized should override this method
        to perform pandas/numpy operations directly.

        Parameters
        ----------
        df : pd.DataFrame
            One row per ticker containing all indicator features.
        context : SignalContext
            Global macro, market, and fundamental data (and xsec ranks).

        Returns
        -------
        pd.DataFrame
            DataFrame with the same index as `df`, and columns:
            ['score', 'confidence', 'explanation', 'meta_label_proba']
        """
        results = df.apply(lambda row: self.compute(row, context), axis=1)
        # Convert list of SignalOutput objects into a DataFrame
        return pd.DataFrame([
            {
                "score": r.score,
                "confidence": r.confidence,
                "explanation": r.explanation,
                "meta_label_proba": r.meta_label_proba
            } for r in results
        ], index=df.index)

    @abstractmethod
    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        """Executes signal calculation logic on a single security observation.

        Parameters
        ----------
        row : pd.Series
            Indicator features for this ticker.
        context : SignalContext
            Global macro, market, and fundamental data (and xsec ranks).

        Returns
        -------
        SignalOutput
            Score, confidence, and explanation.
        """
