# =============================================================================
# MODULE: DATA TRANSFER OBJECTS (DTO) REGISTRY
# File: dto_models.py
# Description: Defines the structures used to safely transfer data between
#              the data acquisition layer and the mathematical engines.
# =============================================================================

from typing import Optional, Dict, Any
from datetime import datetime
import math
import logging

logger = logging.getLogger("DTO_Validator")


class BaseDTO:
    """Base class providing safe data parsing and conversion utilities for all DTOs."""
    
    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        """Coerces raw input into a safe float, handling formatting issues like '$' or '%'."""
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        
        # String cleansing
        clean_str = str(value).strip().replace("$", "").replace(",", "")
        if "N/A" in clean_str.upper() or not clean_str:
            return default
            
        if "%" in clean_str:
            try:
                return float(clean_str.replace("%", "")) / 100.0
            except ValueError:
                return default
                
        try:
            return float(clean_str)
        except ValueError:
            logger.warning(f"Could not convert '{value}' to float. Falling back to {default}.")
            return default

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        """Coerces raw input into a safe integer."""
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return int(value)
        try:
            clean_str = str(value).strip().replace(",", "")
            return int(float(clean_str))
        except ValueError:
            return default


# =============================================================================
# 1. MARKET DATA DTO
# =============================================================================
class MarketBarDTO(BaseDTO):
    """
    Represents a single pricing interval (bar) for an asset.
    Enforces strict mathematical boundary validation upon initialization.
    """
    def __init__(self, date: datetime, ticker: str, open_price: float, 
                 high_price: float, low_price: float, close_price: float, volume: int):
        self.date: datetime = date
        self.ticker: str = ticker.upper().strip()
        self.open: float = self._to_float(open_price)
        self.high: float = self._to_float(high_price)
        self.low: float = self._to_float(low_price)
        self.close: float = self._to_float(close_price)
        self.volume: int = self._to_int(volume)

        # Enforce physical pricing boundaries
        if self.high < self.low:
            logger.error(f"Malformed pricing for {self.ticker}: High ({self.high}) is lower than Low ({self.low}). Coercing High=Low.")
            self.high = self.low

        if not (self.low <= self.open <= self.high) or not (self.low <= self.close <= self.high):
            logger.warning(f"Open/Close for {self.ticker} on {self.date} fell outside High-Low bounds. Normalizing boundaries.")
            self.open = max(self.low, min(self.high, self.open))
            self.close = max(self.low, min(self.high, self.close))

    def __repr__(self) -> str:
        return f"<MarketBarDTO {self.ticker} @ {self.date.strftime('%Y-%m-%d')} - C: ${self.close:.2f}>"


# =============================================================================
# 2. FUNDAMENTAL DATA DTO
# =============================================================================
class FundamentalDataDTO(BaseDTO):
    """
    Standardized, strictly typed representation of an asset's fundamental sheet.
    Self-calculates intrinsic valuations like Graham numbers and payout health metrics.
    """
    def __init__(self, ticker: str, pe_ratio: Optional[float], pb_ratio: Optional[float],
                 dividend_yield: float, book_value: float, eps_trailing: float,
                 dividend_growth_rate: float, payout_ratio: float, sector: str, company_name: str,
                 market_cap: float = 0.0, price: float = 0.0, beta: float = 1.0):
        self.ticker: str = ticker.upper().strip()
        self.company_name: str = company_name.strip() if company_name else "Unknown Asset"
        self.sector: str = sector.strip() if sector else "N/A"
        
        # Valuation Metrics
        self.pe_ratio: Optional[float] = self._to_float(pe_ratio, None) if pe_ratio is not None else None
        self.pb_ratio: Optional[float] = self._to_float(pb_ratio, None) if pb_ratio is not None else None
        self.book_value: float = self._to_float(book_value)
        self.eps_trailing: float = self._to_float(eps_trailing)
        
        # Dividend & Dividend Growth Model Parameters
        self.dividend_yield: float = self._to_float(dividend_yield)
        self.dividend_growth_rate: float = self._to_float(dividend_growth_rate)
        self.payout_ratio: float = self._to_float(payout_ratio)

        # Identity & Price info
        self.market_cap: float = self._to_float(market_cap)
        self.price: float = self._to_float(price)
        self.beta: float = self._to_float(beta, 1.0)
        
        # New default attributes for research engine
        self.held_percent_institutions: float = 0.0
        self.institutional_change: float = 0.0
        self.debt_to_equity: float = 0.0

    @staticmethod
    def _calculate_dividend_growth_rate(info: Dict[str, Any], dividends: Optional[Any] = None) -> float:
        # EXPLANATION: We calculate the actual historical compounded annual dividend growth rate
        # (CAGR) from the dividends Series. If history is unavailable, we fallback to a standard 2%.
        if dividends is not None and hasattr(dividends, "empty") and not dividends.empty:
            try:
                yearly_divs = dividends.groupby(dividends.index.year).sum()
                current_year = datetime.now().year
                yearly_divs = yearly_divs[yearly_divs.index < current_year]
                if len(yearly_divs) >= 2:
                    latest_year = yearly_divs.index.max()
                    # Filter index to years within the last 5 years (excluding current/latest year itself for the start point)
                    possible_starts = [y for y in yearly_divs.index if latest_year - 5 <= y < latest_year]
                    if not possible_starts:
                        possible_starts = [y for y in yearly_divs.index if y < latest_year]
                    
                    if possible_starts:
                        start_year = min(possible_starts)
                        n_years = latest_year - start_year
                        if n_years > 0:
                            div_latest = yearly_divs.loc[latest_year]
                            div_start = yearly_divs.loc[start_year]
                            if div_start > 0 and div_latest > 0:
                                cagr = (div_latest / div_start) ** (1.0 / n_years) - 1.0
                                return max(-0.2, min(0.15, cagr))
            except Exception as e:
                logger.warning(f"Failed to calculate historical dividend growth: {e}")
        return 0.02

    @classmethod
    def from_raw_dict(cls, ticker: str, info: Dict[str, Any], dividends: Optional[Any] = None) -> "FundamentalDataDTO":
        """
        Factory method to parse a raw yfinance/Fidelity dict safely.
        Translates unpredictable API responses into a structured DTO.
        """
        price = info.get("currentPrice") or info.get("previousClose") or 0.0
        calculated_dgr = cls._calculate_dividend_growth_rate(info, dividends)
        dto = cls(
            ticker=ticker,
            company_name=info.get("shortName", info.get("longName", "N/A")),
            sector=info.get("sector", "N/A"),
            pe_ratio=info.get("trailingPE"),
            pb_ratio=info.get("priceToBook"),
            book_value=info.get("bookValue", 0.0),
            eps_trailing=info.get("trailingEps", 0.0),
            dividend_yield=info.get("dividendYield", 0.0),
            dividend_growth_rate=calculated_dgr,
            payout_ratio=info.get("payoutRatio", 0.0),
            market_cap=info.get("marketCap", 0.0),
            price=price,
            beta=info.get("beta", 1.0)
        )
        dto.held_percent_institutions = info.get("heldPercentInstitutions", 0.0)
        dto.institutional_change = info.get("netPercentInstitutionsSharesOut", 0.0)
        dto.debt_to_equity = info.get("debtToEquity", 0.0)
        # EXPLANATION: Store the raw info dictionary for down-stream access to unstructured metrics.
        dto.raw_info = info
        return dto


    @property
    def graham_number(self) -> float:
        r"""
        Calculates Benjamin Graham's Intrinsic Value limit.
        Formula: $V_{Graham} = \sqrt{22.5 \cdot EPS \cdot BookValue}$
        Returns 0.0 if calculations yield imaginary bounds (e.g. negative EPS).
        """

        if self.eps_trailing <= 0 or self.book_value <= 0:
            return 0.0
        return math.sqrt(22.5 * self.eps_trailing * self.book_value)

    @property
    def is_dividend_sustainable(self) -> bool:
        """Evaluates whether the asset is funding dividends via earnings vs. diluting capital."""
        if "REIT" in self.sector or "Real Estate" in self.sector or "Financial" in self.sector:
            # REITs/BDCs operate under structurally high payouts (90% statutory distributions)
            return self.payout_ratio < 0.95
        return self.payout_ratio < 0.75

    def __repr__(self) -> str:
        return f"<FundamentalDataDTO {self.ticker} - EPS: {self.eps_trailing}, Graham Num: ${self.graham_number:.2f}>"


# =============================================================================
# 3. MACRO ECONOMIC ENVIRONMENT DTO
# =============================================================================
class MacroEconomicDTO(BaseDTO):
    """
    Represents systemic macroeconomic risk indicators captured from raw economic databases.
    Houses the top-down risk assessment models.
    """
    # HMM disagreement thresholds (regime/hmm_regime.py second opinion).
    # See market_regime/killSwitch docstrings below for how these are applied.
    HMM_RISK_ON_DOWNGRADE_THRESHOLD: float = 0.3
    HMM_RISK_OFF_AGREEMENT_THRESHOLD: float = 0.7
    # Lowered (more sensitive) kill-switch thresholds used only when the
    # rules-based regime is RECESSION AND the HMM agrees (see killSwitch).
    KILLSWITCH_VIX_THRESHOLD_AGREED: float = 25.0
    KILLSWITCH_SAHM_THRESHOLD_AGREED: float = 0.3

    def __init__(self, yield_curve_10y_2y: float, high_yield_oas: float,
                 inflation_rate: float, nominal_10y: float = 4.0,
                 date: Optional[datetime] = None, sahm_rule_indicator: float = 0.0,
                 vix_value: float = 15.0, hmm_risk_on_probability: Optional[float] = None):
        self.date = date if date is not None else datetime.now()
        self.sahm_rule_indicator = sahm_rule_indicator
        self.yield_curve: float = self._to_float(yield_curve_10y_2y) # Yield spread (Negative = Inverted)
        self.credit_spread: float = self._to_float(high_yield_oas)   # High-Yield OAS (Risk Indicator)
        self.inflation: float = self._to_float(inflation_rate)
        self.nominal_10y: float = self._to_float(nominal_10y)
        self.vix: float = self._to_float(vix_value)
        # Second-opinion probability from regime/hmm_regime.py's
        # HMMRegimeDetector.predict_proba()['risk_on_probability']. None means
        # the HMM did not run this cycle (e.g. insufficient history) -- in
        # that case market_regime/killSwitch behave EXACTLY as before this
        # feature was added (no fabricated probability, rules-based stays
        # fully authoritative).
        self.hmm_risk_on_probability: Optional[float] = (
            self._to_float(hmm_risk_on_probability, None) if hmm_risk_on_probability is not None else None
        )

    @property
    def _rules_based_regime(self) -> str:
        """The rules-based regime classification, with NO HMM adjustment.
        Internal helper -- market_regime (public) applies the HMM downgrade
        on top of this; killSwitch's agreement check also reads this directly
        so the downgrade (RISK ON -> NEUTRAL) never masks the RECESSION
        agreement check below.
        """
        if (self.yield_curve < -0.25 and self.credit_spread > 6.0) or self.sahm_rule_indicator >= 0.6:
            return "RECESSION"
        elif self.credit_spread > 6.0:
            return "CREDIT EVENT"
        elif self.credit_spread > 4.5:
            return "NEUTRAL"
        else:
            return "RISK ON"

    @property
    def killSwitch(self) -> bool:
        """
        Triggers True (halting all new long equity deployments) if:
        - Sahm rule indicator threshold is breached (>= 0.5) OR
        - VIX spikes above 30

        HMM AGREEMENT -- FASTER KILL SWITCH: if the rules-based regime is
        RECESSION AND the HMM's second opinion agrees (risk_off_probability
        = 1 - hmm_risk_on_probability > HMM_RISK_OFF_AGREEMENT_THRESHOLD),
        the kill switch additionally fires at LOWERED (more sensitive)
        thresholds: VIX > KILLSWITCH_VIX_THRESHOLD_AGREED (25, vs. 30) or
        sahm_rule_indicator >= KILLSWITCH_SAHM_THRESHOLD_AGREED (0.3, vs. 0.5).
        This never makes the kill switch LESS sensitive -- it is a strict
        OR with the base condition.
        """
        base_kill = self.sahm_rule_indicator >= 0.5 or self.vix > 30.0

        if self.hmm_risk_on_probability is None or self._rules_based_regime != "RECESSION":
            return base_kill

        hmm_risk_off_probability = 1.0 - self.hmm_risk_on_probability
        if hmm_risk_off_probability > self.HMM_RISK_OFF_AGREEMENT_THRESHOLD:
            agreed_kill = (
                self.sahm_rule_indicator >= self.KILLSWITCH_SAHM_THRESHOLD_AGREED
                or self.vix > self.KILLSWITCH_VIX_THRESHOLD_AGREED
            )
            return base_kill or agreed_kill

        return base_kill

    @property
    def real_yield(self) -> float:
        return self.nominal_10y - self.inflation

    @property
    def market_regime(self) -> str:
        """
        Implements top-down regime classification.
        - RECESSION: Triggered by yield curve inversions (< -0.25) AND Credit Spread (> 6.0) or Sahm Rule >= 0.6
        - CREDIT EVENT: Spread of corporate bonds over Treasuries spikes (> 6.0%).
        - NEUTRAL: Standard operating environment.
        - RISK ON: Favorable macroeconomic conditions.

        HMM DISAGREEMENT -- DOWNGRADE: if the rules-based regime is RISK ON
        but the HMM's second opinion (hmm_risk_on_probability) is below
        HMM_RISK_ON_DOWNGRADE_THRESHOLD (0.3), this downgrades to NEUTRAL and
        logs the disagreement. The rules-based engine remains primary in
        every other case -- the HMM can only ever pull RISK ON back to
        NEUTRAL, never independently declare RECESSION/CREDIT EVENT, and
        never upgrade a worse rules-based regime.
        """
        rules_regime = self._rules_based_regime

        if (
            rules_regime == "RISK ON"
            and self.hmm_risk_on_probability is not None
            and self.hmm_risk_on_probability < self.HMM_RISK_ON_DOWNGRADE_THRESHOLD
        ):
            logger.warning(
                "MacroEconomicDTO.market_regime: rules-based regime is RISK ON but HMM "
                "risk_on_probability=%.3f < %.2f -- downgrading to NEUTRAL.",
                self.hmm_risk_on_probability, self.HMM_RISK_ON_DOWNGRADE_THRESHOLD,
            )
            return "NEUTRAL"

        return rules_regime

    def __repr__(self) -> str:
        return f"<MacroEconomicDTO - Regime: {self.market_regime} (Spread: {self.credit_spread}%)>"


# =============================================================================
# 4. ROBINHOOD POSITION DTO
# ==========================================================
class RobinhoodPositionDTO(BaseDTO):
    """
    Represents an open position and its accumulated dividend history from Robinhood.
    Used to adjust true break-even costs and provide holding-aware advice.
    """
    def __init__(self, ticker: str, shares: float, average_cost: float, total_dividends: float = 0.0):
        self.ticker: str = ticker.upper().strip()
        self.shares: float = self._to_float(shares)
        self.average_cost: float = self._to_float(average_cost)
        self.total_dividends: float = self._to_float(total_dividends)
        
    @property
    def true_break_even(self) -> float:
        """Returns the effective average cost after factoring in dividends received per share."""
        if self.shares <= 0:
            return self.average_cost
        divs_per_share = self.total_dividends / self.shares
        return max(0.0, self.average_cost - divs_per_share)

    def __repr__(self) -> str:
        return f"<RobinhoodPositionDTO {self.ticker} - {self.shares} shares @ ${self.average_cost:.2f} (Divs: ${self.total_dividends:.2f})>"


def test_dto_pipeline():
    """Validates type coercions, extreme bounds, and local logic parsing."""
    print("--- Running DTO Registration Test Routine ---")

    # 1. Test pricing bounds normalization
    print("\n[Testing Market Data Normalization]")
    bar = MarketBarDTO(
        date=datetime.now(),
        ticker="AAPL",
        open_price="$180.50",
        high_price=175.00,
        low_price=178.00,
        close_price=179.00,
        volume="10,250,300"
    )
    print(f"Parsed Market Bar DTO: {bar}")
    print(f"High Price Normalized: {bar.high} (Original invalid: 175.0)")
    print(f"Volume Coerced: {bar.volume}")

    # 2. Test Fundamental DTO logic
    print("\n[Testing Fundamental Calculations & Cleansing]")
    raw_info = {
        "shortName": "Carlyle Secured Lending",
        "sector": "Financial Services (BDC)",
        "trailingPE": "11.2",
        "priceToBook": "1.05",
        "bookValue": "17.20",
        "trailingEps": "1.54",
        "dividendYield": "8.5%",
        "payoutRatio": "0.85"
    }

    fund = FundamentalDataDTO.from_raw_dict("CGBD", raw_info)
    print(f"Company: {fund.company_name}")
    print(f"Dividend Yield Float: {fund.dividend_yield:.4f}")
    print(f"Graham Number: ${fund.graham_number:.2f}")
    print(f"Dividend Sustainable: {fund.is_dividend_sustainable}")

    # 3. Test Macro Risk Regimes
    print("\n[Testing Top-Down Regime Multiplier]")
    macro_hostile = MacroEconomicDTO(
        yield_curve_10y_2y=-0.25,
        high_yield_oas=6.10,
        inflation_rate=2.5,
        nominal_10y=4.0
    )
    print(macro_hostile)
    print(f"Real Yield: {macro_hostile.real_yield}%")


if __name__ == "__main__":
    test_dto_pipeline()
