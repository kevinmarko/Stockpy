"""
InvestYo Quant Platform - Sector-Neutral Earnings-Quality Rank (SNEQR)
=======================================================================
References
----------
- Sloan, R. G. (1996). "Do Stock Prices Fully Reflect Information in Accruals
  and Cash Flows about Future Earnings?" The Accounting Review, 71(3), 289-315.
  -- the accrual anomaly: firms with high accruals (earnings propped up by
  non-cash items) subsequently underperform firms with low accruals /
  cash-backed earnings.
- Novy-Marx, R. (2013). "The Other Side of Value: The Gross Profitability
  Premium." Journal of Financial Economics, 108(1), 1-28. -- gross profit
  scaled by total assets is a cleaner, less-manipulable profitability
  measure than net-income-based ratios and independently predicts returns.
- Asness, C., Frazzini, A., & Pedersen, L. H. (2019). "Quality Minus Junk."
  Review of Accounting Studies, 24(1), 34-112. -- the sector/industry-neutral
  ranking mechanism this module borrows: quality is ranked *within* an
  economically comparable peer group (their construction uses country and
  industry neutralization) rather than across the whole cross-market
  universe, because "high quality for a bank" and "high quality for a
  software company" are not directly comparable on the same raw scale.

STRATEGY LOGIC
--------------
Two raw per-ticker inputs, combined into a single composite:

  accrual_ratio        : Sloan (1996)-style accrual quality, sign-flipped so
                          higher = better (low accruals = more cash-backed
                          earnings = higher score).
  gross_profitability  : Novy-Marx (2013) gross-profit-to-assets proxy,
                          higher = better.

Both inputs are z-scored *within sector* (not across the whole market) via
``groupby(sector).transform(...)`` reusing ``signals.multifactor._zscore_winsorize``,
then averaged into a per-ticker composite and converted to a percentile rank.
Sector-neutrality is the entire mechanism here: a raw quality score for
"Financials" is not comparable to a raw quality score for "Technology"
(different accrual/margin norms by industry), so ranking happens *within*
each sector's own same-date cross-section before the results are pooled.
This module is therefore genuinely cross-sectional -- it cannot be computed
from a single ticker's row in isolation, only from the full universe grouped
by sector on the same date.

DISTINCTION FROM signals/multifactor.py's Quality_Z
-----------------------------------------------------
``multifactor.py``'s Quality_Z is the mean of {returnOnEquity, operatingMargins,
grossMargins} (or a leverage fallback), z-scored **across the whole market**
(no sector grouping) -- see multifactor.py's ``pre_compute()``. It contains no
accrual measure at all. SNEQR is deliberately different on both axes: (1) it
adds the Sloan accrual-quality dimension multifactor.py's Quality_Z omits
entirely, and (2) it standardizes **within sector** rather than market-wide,
which is the Asness-Frazzini-Pedersen (2019) "Quality Minus Junk" construction
choice. The two modules are complementary, not redundant, and both may be
active in the aggregate score simultaneously.

DATA AVAILABILITY GAP (read before enabling in production)
------------------------------------------------------------
As of this module's introduction, **neither raw input is populated anywhere
in this codebase's live per-cycle data path**. This was verified by tracing
every fundamentals source reachable from ``universe_df``:

  - ``data/yahoo_fundamentals.py::compute_fundamentals()`` (the default
    ``FUNDAMENTALS_SOURCE="yahoo"`` primary) emits exactly 17 *ratio* keys
    (``FUNDAMENTAL_KEYS``) -- no NetIncome/OperatingCashFlow/TotalAssets/
    GrossProfit dollar figures.
  - ``data/market_data.py::YFinanceProvider.get_fundamentals()`` (the
    ``FUNDAMENTALS_SOURCE="yfinance_info"`` fallback) returns the raw
    yfinance ``.info`` dict, which likewise does not reliably carry
    ``totalAssets`` (that lives only in the separate balance-sheet
    statement, which no live per-cycle fetch path pulls).
  - ``data/fmp_fundamentals.py`` (opt-in) is the same story: ratio-only.
  - ``processing_engine.py::calculate_fundamental_metrics()`` -- the
    function that actually populates ``universe_df`` for every existing
    multifactor input (``book_to_market``, ``earnings_yield``,
    ``quality_factor_score``, ``low_vol_score``, ``log_market_cap``) --
    never computes or writes an accrual ratio or a gross-profitability
    ratio today.

Per CONSTRAINT #4 ("never fabricate a metric"), this module does NOT invent
a substitute formula out of unrelated ratios to make the composite "work"
today. Instead, ``pre_compute()`` declares the real contract it needs
(``accrual_ratio``, ``gross_profitability`` -- see ``RAW_INPUT_COLS`` below)
and degrades honestly -- log a WARNING, leave ``context.sector_quality_ranks``
empty -- exactly like ``multifactor.py``'s own ``missing_inputs`` guard, when
those columns are absent from ``universe_df`` (true in production today).
Wiring real accrual/gross-profitability data into
``processing_engine.calculate_fundamental_metrics()`` (e.g. from SEC EDGAR
XBRL facts -- ``NetIncomeLoss``, ``NetCashProvidedByUsedInOperatingActivities``,
``Assets``, ``GrossProfit`` are all standard us-gaap tags already used
point-in-time by ``data/edgar_fundamentals.py`` for the historical backfill
path, just not wired into the live per-cycle universe yet) is left to a
follow-up data-plumbing task. See docs/signals/sector_quality_rank.md's
"Data Availability Gap" section.

SIGNAL ARCHITECTURE
--------------------
Two-phase hook pattern, same convention as CrossSectionalMomentumSignal /
MultifactorSignal:

  pre_compute(universe_df, context)  -- ONCE per cycle
      Reads ``Symbol``, ``sector`` (verified real column name -- see below),
      ``accrual_ratio``, ``gross_profitability`` from universe_df. Excludes
      sectors with fewer than MIN_SECTOR_SIZE names this cycle (never
      force-ranks a thin sector). Z-scores each raw input *within sector*,
      averages into a composite, converts to a percentile rank, and stores
      {ticker: percentile} in context.sector_quality_ranks.

  compute(row, context)              -- once PER TICKER
      Looks up the ticker's percentile from context.sector_quality_ranks.
      Returns score = 2 * (percentile - 0.5), mapping [0, 1] -> [-1, +1].

VERIFIED COLUMN NAME NOTE
--------------------------
The real universe_df sector column is the **lowercase** ``"sector"`` --
written by ``processing_engine.calculate_fundamental_metrics()`` as
``results[ticker]['sector'] = dto.sector`` and displayed with header
"Sector" via ``config.COLUMN_SCHEMA`` (``{"header": "Sector", "key":
"sector", ...}``). This module reads ``sector`` first and falls back to a
capitalized ``"Sector"`` only defensively (mirroring
``main_orchestrator.py``'s own ``row.get("sector", row.get("Sector", ""))``
dual-check), never assumes the capitalized form is primary.

LOOKAHEAD PREVENTION
---------------------
- pre_compute only reads columns already present in universe_df -- it never
  fetches new data or peeks at future rows.
- pre_compute is a pure, stateless function of (universe_df, context): it
  keeps no cross-call cache, so a later cycle's data can never leak into an
  earlier cycle's computed ranks (see
  tests/test_sector_quality_rank.py::test_no_cross_cycle_state_leakage).
- Once real accrual_ratio/gross_profitability inputs are wired upstream,
  their own temporal lookahead-safety is that upstream computation's
  responsibility -- exactly the same delegation multifactor.py's module
  docstring already documents for its own raw inputs.

LONG/SHORT SCOPE
------------------
Score can be negative (poor accrual quality / low gross profitability,
relative to sector peers, scores negatively). Weight = 15.0 in
SIGNAL_WEIGHTS, matching the magnitude convention used by the other
multi-input cross-sectional modules (cross_sectional_momentum, multifactor).
"""

from __future__ import annotations

import logging
import math
from typing import Dict, Optional

import numpy as np
import pandas as pd

from signals.base import SignalModule, SignalContext, SignalOutput
from signals.multifactor import _zscore_winsorize
from signals.registry import global_registry
from settings import settings

logger = logging.getLogger(__name__)

SYMBOL_COL = "Symbol"
# Verified real column (see module docstring's "VERIFIED COLUMN NAME NOTE").
SECTOR_COL = "sector"
SECTOR_COL_FALLBACK = "Sector"

# Raw factor-input columns this module needs from universe_df. NOT populated
# anywhere in this codebase's live per-cycle path as of this module's
# introduction -- see the module docstring's "DATA AVAILABILITY GAP" section.
# Names are chosen to match this codebase's existing convention (semantic,
# already-transformed ratio columns written by processing_engine.py --
# e.g. book_to_market/quality_factor_score -- rather than raw dollar
# components), so a follow-up data-plumbing task has an unambiguous contract:
#   results[ticker]['accrual_ratio'] = -((net_income - operating_cash_flow) / total_assets)
#   results[ticker]['gross_profitability'] = gross_profit / total_assets
ACCRUAL_RATIO_COL = "accrual_ratio"
GROSS_PROFITABILITY_COL = "gross_profitability"
RAW_INPUT_COLS = [ACCRUAL_RATIO_COL, GROSS_PROFITABILITY_COL]

# Sloan/Novy-Marx-Asness-style sector-neutral ranking is not meaningful for a
# thin sector -- with too few peers, "within-sector z-score" is either
# undefined (<2 valid observations, per _zscore_winsorize's own guard) or a
# statistically noisy comparison against a handful of names. Documented
# failure mode: these sectors are excluded from ranking entirely this cycle
# rather than force-ranked against too small a peer group.
MIN_SECTOR_SIZE = 5


def _resolve_sector_column(universe_df: pd.DataFrame) -> Optional[str]:
    """Return whichever real sector column name is present, preferring the
    verified-lowercase ``sector`` over the defensive-fallback ``Sector``."""
    if SECTOR_COL in universe_df.columns:
        return SECTOR_COL
    if SECTOR_COL_FALLBACK in universe_df.columns:
        return SECTOR_COL_FALLBACK
    return None


class SectorNeutralQualitySignal(SignalModule):
    """
    Sector-Neutral Earnings-Quality Rank (SNEQR): accrual quality +
    gross profitability, ranked within sector.

    Uses the pre_compute / compute two-phase pattern, mirroring
    CrossSectionalMomentumSignal and MultifactorSignal, to standardize the
    full universe once per cycle -- grouped by sector -- rather than
    redundantly per ticker.
    """

    name = "sector_quality_rank"
    required_features: list[str] = []  # Cross-sectional data lives in context, not row
    meta_label_features = ["ROC_12M", "ROC_6M", "Vol_20", "Vol_50", "GARCH_Vol", "SMA_200"]
    meta_label_horizons = [10, 30, 60, 90]

    # ------------------------------------------------------------------ #
    # Phase 1: called once per cycle on the full universe DataFrame        #
    # ------------------------------------------------------------------ #

    def pre_compute(
        self,
        universe_df: pd.DataFrame,
        context: SignalContext,
    ) -> None:
        """Compute per-ticker sector-relative earnings-quality percentiles.

        Parameters
        ----------
        universe_df : pd.DataFrame
            Dashboard DataFrame with one row per ticker. Must contain
            ``Symbol``, a sector column (``sector`` or ``Sector``), and the
            RAW_INPUT_COLS.
        context : SignalContext
            Shared context; ``sector_quality_ranks`` is populated in-place.
        """
        if SYMBOL_COL not in universe_df.columns:
            logger.warning(
                "SectorNeutralQualitySignal.pre_compute: '%s' column missing; "
                "ranks will be empty.",
                SYMBOL_COL,
            )
            return

        sector_col = _resolve_sector_column(universe_df)
        if sector_col is None:
            logger.warning(
                "SectorNeutralQualitySignal.pre_compute: no sector column "
                "('%s' or '%s') present; ranks will be empty.",
                SECTOR_COL, SECTOR_COL_FALLBACK,
            )
            return

        missing_inputs = [c for c in RAW_INPUT_COLS if c not in universe_df.columns]
        if missing_inputs:
            logger.warning(
                "SectorNeutralQualitySignal.pre_compute: missing raw input "
                "columns %s. This pipeline does not yet compute point-in-time "
                "accrual/gross-profitability ratios upstream of universe_df "
                "(see docs/signals/sector_quality_rank.md's Data Availability "
                "Gap section) -- module is inert until a follow-up task wires "
                "them into processing_engine.calculate_fundamental_metrics(). "
                "Scores will be empty.",
                missing_inputs,
            )
            return

        df = universe_df.set_index(SYMBOL_COL)
        sector = df[sector_col].astype(str)

        # Thin-sector exclusion: count every ticker assigned to a sector this
        # cycle (regardless of whether its raw inputs are individually
        # present), never force-rank a sector with too few peers.
        sector_sizes = sector.groupby(sector).transform("size")
        is_thin_sector = sector_sizes < MIN_SECTOR_SIZE
        n_thin = int(is_thin_sector.sum())

        eligible_df = df.loc[~is_thin_sector].copy()
        eligible_sector = sector.loc[~is_thin_sector]

        if eligible_df.empty:
            logger.warning(
                "SectorNeutralQualitySignal.pre_compute: no sector has >= %d "
                "names this cycle; ranks will be empty.",
                MIN_SECTOR_SIZE,
            )
            return

        accrual_z = eligible_df.groupby(eligible_sector)[ACCRUAL_RATIO_COL].transform(
            _zscore_winsorize
        )
        gp_z = eligible_df.groupby(eligible_sector)[GROSS_PROFITABILITY_COL].transform(
            _zscore_winsorize
        )

        composite_z = pd.concat([accrual_z, gp_z], axis=1).mean(axis=1, skipna=True)
        # A ticker with BOTH inputs NaN yields a NaN mean (skipna=True over an
        # all-NaN row is NaN, not a fabricated 0) -- excluded from ranking
        # below by rank(pct=True)'s own NaN handling.
        #
        # The percentile rank is taken WITHIN EACH SECTOR GROUP (not
        # globally across the whole eligible universe) -- this is the
        # literal sector-neutral mechanism: a ticker's score depends only on
        # its standing relative to same-sector peers, so the best name in a
        # structurally weaker sector still scores as well as the best name
        # in a structurally stronger one (Asness-Frazzini-Pedersen 2019's
        # "rank within peer group" construction). The prior within-sector
        # z-scoring step is what makes averaging accrual_z and gp_z fair
        # (both already on a comparable, sector-relative scale) before this
        # final within-sector rank is taken.
        sector_percentile = composite_z.groupby(eligible_sector).rank(pct=True)

        ranks: Dict[str, float] = {
            str(ticker): float(pct)
            for ticker, pct in sector_percentile.items()
            if pct is not None and not (isinstance(pct, float) and math.isnan(pct))
        }

        context.sector_quality_ranks = ranks
        logger.info(
            "SectorNeutralQualitySignal.pre_compute: ranked %d tickers "
            "(%d excluded for thin sector < %d names, %d excluded for "
            "all-NaN inputs).",
            len(ranks), n_thin, MIN_SECTOR_SIZE,
            len(eligible_df) - len(ranks),
        )

    # ------------------------------------------------------------------ #
    # Phase 2: called once per ticker                                       #
    # ------------------------------------------------------------------ #

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        """Map this ticker's sector-relative quality percentile to [-1, +1].

        Parameters
        ----------
        row : pd.Series
            Per-ticker indicator row (``Symbol`` key must be present).
        context : SignalContext
            Shared context containing pre-computed ``sector_quality_ranks``.

        Returns
        -------
        SignalOutput
            score = 2 * (percentile - 0.5), confidence = |score|, explanation.
        """
        ticker: str = str(row.get(SYMBOL_COL, ""))
        ranks = context.sector_quality_ranks

        if not ranks or ticker not in ranks:
            return SignalOutput(
                score=0.0,
                confidence=0.0,
                explanation=(
                    f"WARNING: Sector-neutral quality rank unavailable for "
                    f"{ticker}. Score set to 0 (neutral)."
                ),
            )

        percentile: float = ranks[ticker]  # [0, 1]
        # Linear mapping: percentile=1.0 -> score=+1.0 (best), percentile=0.0 -> score=-1.0 (worst)
        score: float = 2.0 * (percentile - 0.5)

        weight = settings.SIGNAL_WEIGHTS.get(self.name, 15.0)
        contrib = score * weight

        sector = str(row.get(SECTOR_COL, row.get(SECTOR_COL_FALLBACK, "Unknown")))
        direction = "Bullish" if score > 0 else ("Bearish" if score < 0 else "Neutral")
        sign = "+" if contrib >= 0 else ""

        explanation = (
            f"{sign}{contrib:.1f}pts: SNEQR {direction} "
            f"(sector={sector}, percentile={percentile:.3f}, score={score:+.3f})"
        )
        return SignalOutput(
            score=score,
            confidence=abs(score),
            explanation=explanation,
        )


# Auto-register
global_registry.register(SectorNeutralQualitySignal())
