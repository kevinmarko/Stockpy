"""Cache Long/Short tax-loss-harvesting advisory strategy engine.

Advisory only: nothing here submits a broker order. Every method reuses
existing platform infra rather than reimplementing it:
  - calculate_beta: processing_engine.calculate_rolling_beta over
    HistoricalStore.get_bars.
  - find_correlated_proxy: ranks proxy candidates by
    pairs_ondemand.analyze_pair's rolling cointegration p-value, via
    data.market_data.get_provider() -- the same lightweight
    CompositeProvider api/data_api.py's POST /data/pairs/analyze uses.
    NOT data_engine.DataEngine, which has no get_intraday_bars method and
    would silently make every pairs_ondemand call degrade to "not found".
    The winning candidate's PERSISTED correlation number, however, is a
    separately computed Pearson correlation (_pearson_correlation below),
    not anything read off analyze_pair's own return value -- analyze_pair
    returns cointegration/beta/z-score/half-life diagnostics, not a plain
    correlation coefficient, so there is no "the" analyze_pair correlation
    to reuse for this. This is a deliberate second, intentional
    implementation for a different question ("how correlated are these two
    price series", not "are these two series cointegrated"), not an
    accidental duplicate of analyze_pair's math.
  - check_correlation_drift: recomputes that same _pearson_correlation --
    NOT analyze_pair, for the same reason as above. (A prior revision of
    this docstring described both functions as delegating to analyze_pair
    for their correlation number, which was never accurate for either.)
  - check_wash_sale: a real SQL query against CacheLongShortStore's tax
    lots' ACQUISITION dates (the actual IRS wash-sale trigger -- see the
    method's own docstring), not a stub and not merely a closed-lot P&L
    lookup.

Only called from main_orchestrator.py's background worker (settings-gated)
and api/data_api.py's interactive POST /data/cache-long-short/simulate --
NEVER from api/pilots_api.py, which is AST-guarded against importing
processing_engine even transitively (see the pilots-endpoint skill).
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from data.historical_store import HistoricalStore
from data.cache_long_short_store import CacheLongShortStore, TaxLotDTO
from data.market_data import get_provider
from processing_engine import calculate_rolling_beta
from pairs_ondemand import analyze_pair
from settings import settings

logger = logging.getLogger(__name__)


def _pearson_correlation(ticker: str, other: str, *, lookback_days: int = 90) -> Optional[float]:
    """Real Close-to-Close Pearson correlation over the trailing window.
    None (never a fabricated placeholder) on any failure or insufficient
    overlapping history."""
    try:
        store = HistoricalStore()
        df_a = store.get_bars(ticker, lookback_days=lookback_days)
        df_b = store.get_bars(other, lookback_days=lookback_days)
        if df_a.empty or df_b.empty:
            return None
        joined = df_a[["Close"]].join(df_b[["Close"]], lsuffix="_a", rsuffix="_b", how="inner")
        if len(joined) < 2:
            return None
        corr = joined["Close_a"].corr(joined["Close_b"])
        return float(corr) if corr is not None and math.isfinite(corr) else None
    except Exception as exc:
        logger.debug("_pearson_correlation(%s, %s) failed: %s", ticker, other, exc)
        return None


class CacheLongShortEngine:
    @staticmethod
    def calculate_beta(ticker: str, window: int = 60) -> Optional[float]:
        """Rolling beta of ``ticker`` against SPY. None (never fabricated)
        on insufficient history."""
        try:
            store = HistoricalStore()
            # A bit more than window so the rolling calc has room to warm up.
            price_df = store.get_bars(ticker, lookback_days=window + 30)
            if price_df.empty or len(price_df) < window:
                return None
            spy_df = store.get_bars("SPY", lookback_days=window + 30)
            if spy_df.empty or len(spy_df) < window:
                return None

            beta_series = calculate_rolling_beta(price_df, spy_df, window=window)
            if beta_series.empty or beta_series.isna().all():
                return None
            val = float(beta_series.dropna().iloc[-1])
            return val if math.isfinite(val) else None
        except Exception as exc:
            logger.debug("calculate_beta failed for %s: %s", ticker, exc)
            return None

    @staticmethod
    def find_correlated_proxy(
        ticker: str, candidates: Optional[List[str]] = None
    ) -> Tuple[Optional[str], Optional[float]]:
        """Screens ``candidates`` (default settings.CACHE_LONG_SHORT_PROXY_CANDIDATES)
        for the best cointegrated proxy hedge, ranked by pairs_ondemand's
        rolling ADF p-value (lowest = strongest relationship). Persists the
        winner to CacheLongShortStore. Returns (None, None) -- never a
        fabricated proxy/correlation -- when no candidate has a usable
        relationship."""
        if not candidates:
            candidates = settings.CACHE_LONG_SHORT_PROXY_CANDIDATES

        provider = get_provider()
        best_proxy: Optional[str] = None
        best_corr: Optional[float] = None
        lowest_p = float("inf")

        for candidate in candidates:
            if candidate.upper() == ticker.upper():
                continue
            res = analyze_pair(ticker, candidate, provider)
            p_val = res.get("rolling_p")
            if p_val is None or p_val >= lowest_p:
                continue
            corr = _pearson_correlation(ticker, candidate)
            if corr is None:
                # No usable price overlap for this candidate -- skip rather
                # than fabricate a correlation value (CONSTRAINT #4).
                continue
            lowest_p = p_val
            best_proxy = candidate
            best_corr = corr

        if best_proxy is not None and best_corr is not None:
            store = CacheLongShortStore()
            store.upsert_security_proxy(ticker, best_proxy, best_corr)
        return best_proxy, best_corr

    @staticmethod
    def check_correlation_drift(ticker: str, proxy: str) -> Optional[float]:
        """Recomputes and persists the real correlation between ``ticker``
        and its ``proxy`` hedge via _pearson_correlation -- NOT
        pairs_ondemand.analyze_pair, which returns cointegration/beta/
        z-score diagnostics rather than a plain correlation coefficient (see
        the module docstring). Returns the fresh correlation coefficient
        (None if it couldn't be computed) -- callers compare against
        settings.CACHE_LONG_SHORT_MIN_CORRELATION to decide whether the
        hedge is "out of balance"."""
        corr = _pearson_correlation(ticker, proxy)
        if corr is None:
            return None
        store = CacheLongShortStore()
        store.upsert_security_proxy(ticker, proxy, corr)
        return corr

    @staticmethod
    def check_wash_sale(ticker: str, as_of: Optional[datetime] = None) -> bool:
        """True if selling ``ticker`` today (``as_of``, default now) would
        trigger the IRS wash-sale disallowance (26 U.S.C. Sec. 1091): a
        "substantially identical" security was ACQUIRED within 30 calendar
        days before -- or, if already on record, after -- the sale date.
        This checks acquisition dates (ANY tax lot for the ticker, open or
        closed), which is the actual wash-sale trigger -- a prior revision
        of this function instead looked at whether a *closed* lot had
        realized a loss in the last 30 days, which is a different question
        that neither implies nor is implied by an actual wash sale: it
        missed the textbook case (a recent purchase with no closed lot at
        all) and could also false-block on an old, already-fully-resolved
        loss with no repurchase since.

        Only the backward-looking half of the 61-day window (30 days
        *before* the sale) can ever be a real check at call time -- a
        future repurchase has no row to find before it happens. Checking
        30 days *after* ``as_of`` as well is harmless for a live call (no
        such row can exist yet) and makes this correct for a historical/
        backtest ``as_of`` too, but it does NOT mean this function can warn
        about a real future repurchase before the operator makes it --
        that's why generate_sell_down_orders below also returns a
        forward-looking advisory note.

        Scoped to exact-ticker match only, not the correlated-proxy
        relationships this module also tracks (find_correlated_proxy) --
        the IRS's "substantially identical" test is narrower than mere
        price correlation, and widening this check to a proxy ticker is a
        deliberate policy call left for a future change, not assumed here.
        """
        store = CacheLongShortStore()
        sale_date = as_of if as_of is not None else datetime.now(timezone.utc)
        if sale_date.tzinfo is None:
            sale_date = sale_date.replace(tzinfo=timezone.utc)
        window_start = (sale_date - timedelta(days=30)).replace(tzinfo=None)
        window_end = (sale_date + timedelta(days=30)).replace(tzinfo=None)
        session = store.Session()
        try:
            from data.cache_long_short_store import CacheLongShortTaxLot, CacheLongShortPosition

            acquired_in_window = (
                session.query(CacheLongShortTaxLot)
                .join(CacheLongShortPosition)
                .filter(
                    CacheLongShortPosition.ticker == ticker.upper().strip(),
                    CacheLongShortTaxLot.acquisition_date >= window_start,
                    CacheLongShortTaxLot.acquisition_date <= window_end,
                )
                .first()
            )
            return acquired_in_window is not None
        except Exception as exc:
            logger.debug("check_wash_sale failed for %s: %s", ticker, exc)
            return False
        finally:
            session.close()

    @staticmethod
    def scan_tlh_opportunities() -> List[TaxLotDTO]:
        """Flags every open lot whose unrealized loss exceeds
        settings.CACHE_LONG_SHORT_TLH_THRESHOLD_PCT, persisting the flag via
        CacheLongShortStore.flag_lot_for_tlh so GET
        /pilots/cache-long-short/pending-approvals can read real
        opportunities without importing this (heavy-engine-importing)
        module. Returns the flagged lots for the caller's own logging."""
        from data.cache_long_short_store import CacheLongShortPosition

        store = CacheLongShortStore()
        h_store = HistoricalStore()
        opportunities: List[TaxLotDTO] = []
        lots = store.get_open_tax_lots()
        if not lots:
            return opportunities

        session = store.Session()
        try:
            positions_by_id = {
                p.id: p
                for p in session.query(CacheLongShortPosition)
                .filter(CacheLongShortPosition.id.in_({lot.position_id for lot in lots}))
                .all()
            }
        finally:
            session.close()

        for lot in lots:
            pos = positions_by_id.get(lot.position_id)
            if not pos or lot.cost_basis_per_share <= 0:
                continue
            df = h_store.get_bars(pos.ticker, lookback_days=5)
            if df.empty:
                continue
            curr_price = float(df.iloc[-1]["Close"])

            if pos.position_type == "long":
                unrealized_pct = (curr_price - lot.cost_basis_per_share) / lot.cost_basis_per_share
            else:
                unrealized_pct = (lot.cost_basis_per_share - curr_price) / lot.cost_basis_per_share

            if unrealized_pct < 0 and abs(unrealized_pct) > settings.CACHE_LONG_SHORT_TLH_THRESHOLD_PCT:
                store.flag_lot_for_tlh(lot.lot_id, unrealized_pct)
                opportunities.append(lot)

        return opportunities

    @staticmethod
    def generate_sell_down_orders(ticker: str) -> Dict[str, Any]:
        """Sizes an advisory sell-down recommendation against the tax bank,
        blocked by the wash-sale guardrail (see check_wash_sale's docstring
        for what it actually checks -- acquisition timing, not closed-lot
        P&L)."""
        if CacheLongShortEngine.check_wash_sale(ticker):
            return {
                "status": "blocked",
                "reason": (
                    f"Wash sale guardrail active: {ticker} was acquired within the last 30 days, "
                    "which would disallow this loss under the IRS wash-sale rule (Sec. 1091)."
                ),
            }

        store = CacheLongShortStore()
        tax_bank = store.tax_bank()
        if tax_bank <= 0:
            return {"status": "blocked", "reason": "No harvested tax losses available to offset gains"}

        return {
            "status": "approved",
            "recommended_sell_value": tax_bank,  # 1:1 offset for simplicity in V1
            "reason": f"Sized to match ${tax_bank:,.2f} tax bank",
            # The forward-looking half of the wash-sale window (a repurchase
            # in the 30 days AFTER this sale) has no row to check against --
            # it hasn't happened yet. check_wash_sale can only ever enforce
            # the backward-looking half; this note is the honest,
            # operator-facing substitute for the half the code cannot
            # enforce.
            "wash_sale_note": (
                f"To preserve this harvested loss, avoid repurchasing {ticker} "
                "(or a substantially identical security) for 30 days after this sale."
            ),
        }
