# =============================================================================
# MODULE: ROBINHOOD CLIENT
# File: data/robinhood_client.py
# Description: Connects to Robinhood to fetch user holdings, cost basis, and
#              accumulated dividends. Maps them into RobinhoodPositionDTOs.
# =============================================================================

import logging
import robin_stocks.robinhood as r
from typing import Dict, Optional

from settings import settings
from dto_models import RobinhoodPositionDTO

logger = logging.getLogger("RobinhoodClient")

class RobinhoodClient:
    """Encapsulates Robinhood API interactions and DTO mapping."""
    
    def __init__(self):
        self.username = settings.ROBINHOOD_USERNAME
        self.password = settings.ROBINHOOD_PASSWORD
        self.is_authenticated = False
        
    def login(self) -> bool:
        """Authenticates with Robinhood. Prompts for SMS MFA in the terminal if needed."""
        if not self.username or not self.password:
            logger.info("Robinhood credentials missing. Skipping Robinhood integration.")
            return False
            
        try:
            # Login prompts for SMS if MFA is enabled.
            login_result = r.login(self.username, self.password, by_sms=True)
            if login_result and "access_token" in login_result:
                self.is_authenticated = True
                logger.info("Successfully authenticated with Robinhood.")
                return True
            else:
                logger.warning("Robinhood authentication failed.")
                return False
        except Exception as e:
            logger.error(f"Robinhood login error: {e}")
            return False

    def fetch_positions(self) -> Dict[str, RobinhoodPositionDTO]:
        """Fetches active holdings and associated historical dividends."""
        if not self.is_authenticated:
            return {}
            
        try:
            holdings = r.build_holdings()
            dividends_raw = r.get_dividends()
            
            positions_dtos = {}
            # Map instrument IDs to tickers to correlate dividends
            instrument_urls = {}
            for ticker, data in holdings.items():
                shares = float(data.get("quantity", 0.0))
                avg_cost = float(data.get("average_buy_price", 0.0))
                
                positions_dtos[ticker] = RobinhoodPositionDTO(
                    ticker=ticker,
                    shares=shares,
                    average_cost=avg_cost,
                    total_dividends=0.0
                )
                instrument_urls[data.get('id')] = ticker

            # Calculate dividends
            for d in dividends_raw:
                if d.get("state") in ["paid", "reinvested"]:
                    inst_id = d.get("instrument").split('/')[-2]
                    if inst_id in instrument_urls:
                        ticker = instrument_urls[inst_id]
                        amount = float(d.get("amount", 0.0))
                        positions_dtos[ticker].total_dividends += amount

            logger.info(f"Successfully fetched {len(positions_dtos)} positions from Robinhood.")
            return positions_dtos

        except Exception as e:
            logger.error(f"Error fetching Robinhood positions: {e}")
            return {}
