"""
InvestYo Quant Platform - Execution Cost Model
==============================================
Provides a tiered, realistic execution cost model for US equities,
accounting for SEC fees, FINRA TAF, bid-ask spreads by liquidity tier,
and slippage. Integrates with Backtrader and VectorBT.
"""

import logging
from typing import Dict, Any, Optional
import numpy as np

# Set up module logger
logger = logging.getLogger("Cost_Model")

class TieredCostModel:
    """
    Tiered transaction cost model for US Equities.
    Models commissions, bid-ask spread, regulatory fees, and market impact.
    """
    def __init__(
        self,
        commission_per_share: float = 0.0,
        sec_fee_rate: float = 0.0000278,  # SEC §31 fee rate as of 2024-2025
        taf_per_share: float = 0.000166,  # FINRA TAF rate per share
        taf_cap: float = 8.30,            # FINRA TAF cap per transaction
        spread_bps_by_liquidity: Optional[Dict[str, float]] = None,
        slippage_bps_market_order: float = 5.0,
        options_per_contract: float = 0.65
    ):
        self.commission_per_share = commission_per_share
        self.sec_fee_rate = sec_fee_rate
        self.taf_per_share = taf_per_share
        self.taf_cap = taf_cap
        
        if spread_bps_by_liquidity is None:
            self.spread_bps_by_liquidity = {
                "large_cap": 1.0,   # ~0.01%
                "mid_cap": 3.0,     # ~0.03%
                "small_cap": 8.0,    # ~0.08%
                "illiquid": 20.0     # ~0.20%
            }
        else:
            self.spread_bps_by_liquidity = spread_bps_by_liquidity
            
        self.slippage_bps_market_order = slippage_bps_market_order
        self.options_per_contract = options_per_contract

    def get_liquidity_tier(self, market_cap: Optional[float]) -> str:
        """Categorize stock into a liquidity tier based on market cap."""
        if market_cap is None:
            return "large_cap"
        if market_cap >= 10e9:  # > $10B
            return "large_cap"
        elif market_cap >= 2e9:  # $2B - $10B
            return "mid_cap"
        elif market_cap >= 300e6:  # $300M - $2B
            return "small_cap"
        else:
            return "illiquid"

    def calculate_cost(
        self,
        side: str,
        shares: float,
        price: float,
        order_type: str = "market",
        market_cap: Optional[float] = None
    ) -> Dict[str, float]:
        """
        Calculates execution costs for a single transaction (buy or sell).
        """
        side = side.lower().strip()
        order_type = order_type.lower().strip()
        
        # 1. Base Commission
        commission = self.commission_per_share * shares
        
        # 2. Spread cost (incurred on both buy and sell)
        tier = self.get_liquidity_tier(market_cap)
        spread_bps = self.spread_bps_by_liquidity.get(tier, 1.0)
        # Bid-ask spread cost is half-spread per transaction side
        spread = shares * price * (spread_bps / 2.0 / 10000.0)
        
        # 3. Market impact / Slippage (only for market orders)
        slippage = 0.0
        if order_type == "market":
            slippage = shares * price * (self.slippage_bps_market_order / 10000.0)
            
        # 4. Regulatory fees (SEC & TAF apply ONLY to sells)
        sec_fee = 0.0
        taf = 0.0
        if side == "sell":
            sec_fee = shares * price * self.sec_fee_rate
            taf = min(self.taf_cap, shares * self.taf_per_share)

        total_dollars = commission + spread + slippage + sec_fee + taf
        trade_value = shares * price
        total_bps = (total_dollars / trade_value * 10000.0) if trade_value > 0 else 0.0
        
        return {
            "commission": commission,
            "spread": spread,
            "slippage": slippage,
            "sec_fee": sec_fee,
            "taf": taf,
            "total_bps": total_bps,
            "total_dollars": total_dollars
        }

    def estimate_round_trip_cost(
        self,
        symbol: str,
        shares: float,
        price: float,
        order_type: str = "market",
        market_cap: Optional[float] = None
    ) -> Dict[str, float]:
        """
        Estimates total round-trip cost (buy side + sell side) for a position.
        """
        buy_costs = self.calculate_cost("buy", shares, price, order_type, market_cap)
        sell_costs = self.calculate_cost("sell", shares, price, order_type, market_cap)
        
        total_dollars = buy_costs["total_dollars"] + sell_costs["total_dollars"]
        trade_value = shares * price
        total_bps = (total_dollars / trade_value * 10000.0) if trade_value > 0 else 0.0
        
        return {
            "commission": buy_costs["commission"] + sell_costs["commission"],
            "spread": buy_costs["spread"] + sell_costs["spread"],
            "slippage": buy_costs["slippage"] + sell_costs["slippage"],
            "sec_fee": sell_costs["sec_fee"],
            "taf": sell_costs["taf"],
            "total_bps": total_bps,
            "total_dollars": total_dollars
        }


# =============================================================================
# BACKTRADER COMMISSION INFO SUBCLASS
# =============================================================================
try:
    import backtrader as bt
    
    class TieredCostCommissionInfo(bt.CommInfoBase):
        """
        Custom Backtrader CommissionInfo that delegates to TieredCostModel.
        """
        params = (
            ('stocklike', True),
            ('commtype', bt.CommInfoBase.COMM_FIXED),
            ('tiered_model', None),    # Must pass an instance of TieredCostModel
            ('market_cap', None),
            ('order_type', 'market'),   # 'market' or 'limit'
        )

        def _getcommission(self, size, price, pseudoexec):
            """
            size: positive for buy, negative for sell
            price: price of execution
            """
            if self.p.tiered_model is None:
                # Fallback to no commission
                return 0.0
                
            shares = abs(size)
            side = "buy" if size > 0 else "sell"
            
            costs = self.p.tiered_model.calculate_cost(
                side=side,
                shares=shares,
                price=price,
                order_type=self.p.order_type,
                market_cap=self.p.market_cap
            )
            return costs["total_dollars"]
            
except ImportError:
    # Backtrader is optional, do not crash if not installed
    pass
