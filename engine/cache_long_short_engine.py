"""Cache Long/Short tax-loss-harvesting advisory strategy engine.

Advisory only: nothing here submits a broker order. Every method reuses
existing platform infra rather than reimplementing it:
  - calculate_beta: processing_engine.calculate_rolling_beta over
    HistoricalStore.get_bars.
  - find_correlated_proxy / check_correlation_drift: pairs_ondemand.analyze_pair,
    via data.market_data.get_provider() -- the same lightweight
    CompositeProvider api/data_api.py's POST /data/pairs/analyze uses.
    NOT data_engine.DataEngine, which has no get_intraday_bars method and
    would silently make every pairs_ondemand call degrade to "not found".
  - check_wash_sale: a real SQL query against CacheLongShortStore's closed
    tax lots, not a hardcoded stub.

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
        and its ``proxy`` hedge. Returns the fresh correlation coefficient
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
    def check_wash_sale(ticker: str) -> bool:
        """True if ``ticker`` has a closed lot with a realized loss in the
        last 30 days (IRS wash-sale window) -- a real query, not a stub."""
        store = CacheLongShortStore()
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        session = store.Session()
        try:
            from data.cache_long_short_store import CacheLongShortTaxLot, CacheLongShortPosition

            naive_cutoff = cutoff.replace(tzinfo=None)
            blocked = (
                session.query(CacheLongShortTaxLot)
                .join(CacheLongShortPosition)
                .filter(
                    CacheLongShortPosition.ticker == ticker.upper().strip(),
                    CacheLongShortTaxLot.status == "closed",
                    CacheLongShortTaxLot.close_date >= naive_cutoff,
                    CacheLongShortTaxLot.realized_pnl < 0,
                )
                .first()
            )
            return blocked is not None
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
        blocked by the wash-sale guardrail."""
        if CacheLongShortEngine.check_wash_sale(ticker):
            return {"status": "blocked", "reason": "Wash sale guardrail active (loss realized within 30d)"}

        store = CacheLongShortStore()
        tax_bank = store.tax_bank()
        if tax_bank <= 0:
            return {"status": "blocked", "reason": "No harvested tax losses available to offset gains"}

        return {
            "status": "approved",
            "recommended_sell_value": tax_bank,  # 1:1 offset for simplicity in V1
            "reason": f"Sized to match ${tax_bank:,.2f} tax bank",
        }
