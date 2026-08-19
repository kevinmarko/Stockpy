"""
pilots/unusual_options_flow.py — Unusual Options Activity (UOA) & Flow Anomaly Engine.
=====================================================================================

Detects institutional unusual options activity, sweeps vs blocks, IV burst anomalies,
and calculates net order flow sentiment for the Pilots desk and alpha scoring overlay.

Key Capabilities:
1. **UOA Anomaly Detection**:
   - Flags contracts exceeding Volume/OI ratio (default >= 3.0x).
   - Filters by minimum institutional contract volume (default >= 500) and premium notional (default >= $100,000).
   - Ingests multiple chain data formats (DataFrames, dicts, expiration mappings, objects).

2. **Trade Aggressiveness Categorization**:
   - `"ask_sweep"`: Trade price >= Ask (Aggressive Bullish Call / Aggressive Bearish Put).
   - `"bid_sweep"`: Trade price <= Bid (Aggressive Bearish Call / Aggressive Bullish Put).
   - `"mid_block"` / `"block"`: Bid < Trade price < Ask (Neutral / Mid-market block).

3. **IV Burst Expansion Anomaly**:
   - Compares contract Implied Volatility (IV) against 30-day Historical Realized Volatility (HV30).
   - Flags IV expansion when IV >= 1.25 * HV30.

4. **Net Flow Sentiment Aggregation**:
   - Quantifies net directional bias: (Bullish Notional - Bearish Notional) / Total Directional Notional in [-1.0, +1.0].
   - Computes Call/Put volume ratios and top active strike breakdowns.

5. **Persistence & Read Helpers**:
   - `save_uoa_records(records, path)` and `load_uoa_records(path)` for pipeline and API integration.
   - `get_symbol_flow_sentiment(symbol, path)` for mobile and webapp querying.

Design Invariants:
* **AST-Safe (CONSTRAINT #1 & #3)** — Pure compute/read module. Never imports heavy engines
  (`processing_engine`, `technical_options_engine`, `strategy_engine`, `macro_engine`, etc.).
  The one exception is a lazy, function-scoped `data.market_data` import inside
  `get_unusual_options_activity`'s live-fetch helpers (`_fetch_live_options_chain_map`,
  `_resolve_live_spot_price`) — the same lightweight `CompositeOptionsProvider`/
  `CompositeProvider` live-fetch pattern already used by `pilots/options_gex.py`,
  `pilots/vol_mispricing.py`, and `pilots/har_volatility.py`; enforced by
  `tests/test_unusual_options_flow.py::TestASTSafety`.
* **Honesty (CONSTRAINT #4)** — Preserves None / 0.0 for uncomputable metrics, never fabricates fake
  prices, and never falls back to a synthetic/simulated chain when the live provider has no data.
* **Never Raises (CONSTRAINT #6)** — Degrades gracefully on empty/malformed option chains.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
import json
import logging
import math
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from settings import settings

logger = logging.getLogger(__name__)

DEFAULT_MIN_VOL_OI_RATIO = 3.0
DEFAULT_MIN_NOTIONAL = 100000.0
DEFAULT_MIN_VOLUME = 500
IV_BURST_THRESHOLD = 1.25
TRADING_DAYS_PER_YEAR = 252.0
_FILENAME = "unusual_options_flow.json"
MAX_PERSISTED_UOA_RECORDS = 2000  # bounds output/unusual_options_flow.json growth (read-through cache)
LIVE_SCAN_MAX_EXPIRATIONS = 6  # nearest expirations scanned per symbol on a live-fetch cache miss

__all__ = [
    "UOARecord",
    "scan_unusual_options_activity",
    "get_unusual_options_activity",
    "calculate_net_flow_sentiment",
    "get_flow_sentiment",
    "to_flow_sentiment_response",
    "calculate_historical_volatility",
    "categorize_trade_aggressiveness",
    "calculate_iv_burst_score",
    "save_uoa_records",
    "load_uoa_records",
    "get_symbol_flow_sentiment",
    "DEFAULT_MIN_VOL_OI_RATIO",
    "DEFAULT_MIN_NOTIONAL",
    "DEFAULT_MIN_VOLUME",
    "IV_BURST_THRESHOLD",
]


# Regex for OCC option symbol format (e.g., AAPL260918C00150000) or human format (AAPL 2026-09-18 $150.00 CALL)
_OCC_SYM_RE = re.compile(
    r"^(?P<ticker>[A-Z0-9]+)(?P<exp_short>\d{6})(?P<type>[CP])(?P<strike_int>\d{8})$",
    re.IGNORECASE,
)
_HUMAN_SYM_RE = re.compile(
    r"^(?P<ticker>[A-Z0-9]+)\s+(?P<exp>\d{4}-\d{2}-\d{2})\s+\$?(?P<strike>\d+(?:\.\d+)?)\s+(?P<type>CALL|PUT)$",
    re.IGNORECASE,
)


@dataclass
class UOARecord:
    """Standardized Unusual Options Activity container supporting both attribute and dict-like access."""
    id: Optional[str] = None
    symbol: str = ""
    contract_symbol: str = ""
    expiration: str = ""
    strike: float = 0.0
    option_type: str = "call"  # "call" or "put"
    trade_price: float = 0.0
    price: float = 0.0
    spot_price: Optional[float] = None
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0
    open_interest: int = 0
    vol_oi_ratio: float = 0.0
    notional: float = 0.0  # Premium notional (price * volume * 100)
    underlying_notional: float = 0.0  # Underlying notional (spot * volume * 100)
    aggressiveness: str = "mid_block"  # "ask_sweep", "bid_sweep", "mid_block"
    trade_type: str = "block"  # "ask_sweep", "bid_sweep", "mid_block", "block"
    aggressor_side: str = ""  # "ASK", "BID", "MID"
    sentiment: str = "NEUTRAL"  # "BULLISH", "BEARISH", "NEUTRAL"
    iv: Optional[float] = None
    implied_volatility: Optional[float] = None
    hv_30: Optional[float] = None
    historical_volatility: Optional[float] = None
    historical_vol_30d: Optional[float] = None
    iv_burst_score: Optional[float] = None  # IV / HV_30
    iv_burst_detected: bool = False
    iv_expansion_flag: bool = False
    dte: int = 0
    timestamp: str = ""
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Sync price aliases
        if self.trade_price == 0.0 and self.price > 0.0:
            self.trade_price = self.price
        elif self.price == 0.0 and self.trade_price > 0.0:
            self.price = self.trade_price

        # Sync id
        if not self.id:
            opt_type_upper = self.option_type.upper()
            self.id = self.contract_symbol or f"{self.symbol}-{self.expiration}-{self.strike}-{opt_type_upper}-{self.timestamp}"

        # Sync trade_type and aggressiveness
        if self.aggressiveness == "mid_block" and self.trade_type == "block":
            pass
        elif self.trade_type and not self.aggressiveness:
            self.aggressiveness = "mid_block" if self.trade_type in ("block", "mid_block") else self.trade_type
        elif self.aggressiveness and not self.trade_type:
            self.trade_type = "block" if self.aggressiveness == "mid_block" else self.aggressiveness

        # Sync IV aliases
        if self.iv is None and self.implied_volatility is not None:
            self.iv = self.implied_volatility
        elif self.implied_volatility is None and self.iv is not None:
            self.implied_volatility = self.iv

        # Sync HV aliases
        hvs = [x for x in (self.hv_30, self.historical_volatility, self.historical_vol_30d) if x is not None]
        if hvs:
            hv_val = hvs[0]
            self.hv_30 = hv_val
            self.historical_volatility = hv_val
            self.historical_vol_30d = hv_val

        # Sync aggressor_side
        if not self.aggressor_side:
            if self.aggressiveness == "ask_sweep":
                self.aggressor_side = "ASK"
            elif self.aggressiveness == "bid_sweep":
                self.aggressor_side = "BID"
            else:
                self.aggressor_side = "MID"

        # Sync IV expansion flags
        if self.iv_burst_detected and not self.iv_expansion_flag:
            self.iv_expansion_flag = True
        elif self.iv_expansion_flag and not self.iv_burst_detected:
            self.iv_burst_detected = True

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def keys(self):
        return asdict(self).keys()

    def values(self):
        return asdict(self).values()

    def items(self):
        return asdict(self).items()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Default Storage Path
# ---------------------------------------------------------------------------


def _default_path() -> Path:
    return settings.OUTPUT_DIR / _FILENAME


# ---------------------------------------------------------------------------
# Historical Realized Volatility Helper
# ---------------------------------------------------------------------------


def calculate_historical_volatility(
    prices: Union[Sequence[float], np.ndarray, pd.Series],
    window: int = 30,
    annualization_factor: float = TRADING_DAYS_PER_YEAR,
) -> Optional[float]:
    """
    Calculates annualized realized volatility from historical close prices over rolling window.
    Formula: std(ln(P_t / P_{t-1}), ddof=1) * sqrt(annualization_factor)
    """
    if prices is None:
        return None

    if isinstance(prices, pd.Series):
        arr = prices.dropna().to_numpy(dtype=float)
    elif isinstance(prices, np.ndarray):
        arr = prices[~np.isnan(prices)].astype(float)
    elif isinstance(prices, (list, tuple)):
        arr = np.array([p for p in prices if p is not None and not (isinstance(p, float) and math.isnan(p))], dtype=float)
    else:
        return None

    if len(arr) < max(2, window + 1):
        return None

    sub_prices = arr[-(window + 1):]
    if np.any(sub_prices <= 0):
        return None

    log_returns = np.diff(np.log(sub_prices))
    if len(log_returns) < 2:
        return None

    std_dev = np.std(log_returns, ddof=1)
    if math.isnan(std_dev) or std_dev < 0:
        return None

    rv = float(std_dev * math.sqrt(annualization_factor))
    return round(rv, 6)


# ---------------------------------------------------------------------------
# Trade Aggressiveness & IV Burst Categorization
# ---------------------------------------------------------------------------


def categorize_trade_aggressiveness(
    trade_price: float,
    bid: float,
    ask: float,
    option_type: str,
) -> Tuple[str, str]:
    """
    Categorizes trade aggressiveness and inferred sentiment.

    Returns:
        (aggressiveness, sentiment)
        aggressiveness: "ask_sweep" | "bid_sweep" | "mid_block"
        sentiment: "BULLISH" | "BEARISH" | "NEUTRAL"
    """
    opt_type = str(option_type or "").lower().strip()

    # If ask > 0 and trade price >= ask -> Ask Sweep (Aggressive Buyer)
    if ask > 0 and trade_price >= ask:
        aggressiveness = "ask_sweep"
        sentiment = "BULLISH" if opt_type == "call" else "BEARISH"
    # If bid > 0 and trade price <= bid -> Bid Sweep (Aggressive Seller / Floor Hit)
    elif bid > 0 and trade_price <= bid:
        aggressiveness = "bid_sweep"
        sentiment = "BEARISH" if opt_type == "call" else "BULLISH"
    # If bid and ask exist and trade price is between bid and ask -> Mid Block
    elif bid > 0 and ask > 0 and bid < trade_price < ask:
        aggressiveness = "mid_block"
        # Mid-market block with slight lean if near edges
        midpoint = (bid + ask) / 2.0
        if trade_price > midpoint:
            sentiment = "BULLISH" if opt_type == "call" else "BEARISH"
        elif trade_price < midpoint:
            sentiment = "BEARISH" if opt_type == "call" else "BULLISH"
        else:
            sentiment = "NEUTRAL"
    else:
        aggressiveness = "mid_block"
        sentiment = "NEUTRAL"

    return aggressiveness, sentiment


def calculate_iv_burst_score(
    iv: Optional[float],
    hv_30: Optional[float],
    burst_threshold: float = IV_BURST_THRESHOLD,
) -> Tuple[Optional[float], bool]:
    """
    Calculates IV burst score (IV vs 30d Realized Volatility).

    Returns:
        (iv_burst_score, iv_burst_detected)
    """
    if iv is None or hv_30 is None:
        return None, False
    if iv <= 0 or hv_30 <= 0:
        return None, False

    burst_score = round(float(iv / hv_30), 4)
    burst_detected = bool(burst_score >= burst_threshold)
    return burst_score, burst_detected


# ---------------------------------------------------------------------------
# Contract Normalization Helper
# ---------------------------------------------------------------------------


def _parse_contract_symbol_metadata(contract_symbol: str) -> Dict[str, Any]:
    """Extracts ticker, expiration, strike, and type from OCC or human symbol string."""
    clean = str(contract_symbol or "").strip()
    # 1. OCC format (e.g., AAPL260918C00150000)
    m_occ = _OCC_SYM_RE.match(clean)
    if m_occ:
        ticker = m_occ.group("ticker").upper()
        exp_raw = m_occ.group("exp_short")
        exp_str = f"20{exp_raw[0:2]}-{exp_raw[2:4]}-{exp_raw[4:6]}"
        opt_type = "call" if m_occ.group("type").upper() == "C" else "put"
        strike = float(m_occ.group("strike_int")) / 1000.0
        return {"ticker": ticker, "expiration": exp_str, "strike": strike, "option_type": opt_type}

    # 2. Human format (e.g., AAPL 2026-09-18 $150.00 CALL)
    m_hum = _HUMAN_SYM_RE.match(clean)
    if m_hum:
        ticker = m_hum.group("ticker").upper()
        exp_str = m_hum.group("exp")
        strike = float(m_hum.group("strike"))
        opt_type = m_hum.group("type").lower()
        return {"ticker": ticker, "expiration": exp_str, "strike": strike, "option_type": opt_type}

    return {}


def _extract_contract_fields(
    contract: Any,
    default_type: Optional[str] = None,
    default_exp: Optional[str] = None,
    default_symbol: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Normalizes arbitrary contract inputs into standardized fields."""
    if contract is None:
        return None

    # Determine dictionary-like access vs attribute access
    if isinstance(contract, dict):
        get_val = lambda k, default=None: contract.get(k, default)
    elif isinstance(contract, pd.Series):
        get_val = lambda k, default=None: contract[k] if k in contract and pd.notna(contract[k]) else default
    elif hasattr(contract, "__dict__"):
        get_val = lambda k, default=None: getattr(contract, k, default)
    else:
        return None

    contract_sym = str(
        get_val("contractSymbol")
        or get_val("contract_symbol")
        or get_val("symbol")
        or ""
    ).strip()

    parsed_meta = _parse_contract_symbol_metadata(contract_sym)

    ticker = str(
        get_val("ticker")
        or get_val("rootSymbol")
        or get_val("underlying")
        or get_val("symbol")
        or parsed_meta.get("ticker")
        or default_symbol
        or ""
    ).upper().strip()

    expiration = str(
        get_val("expiration")
        or get_val("expirationDate")
        or get_val("expiration_date")
        or get_val("expiry")
        or get_val("exp")
        or parsed_meta.get("expiration")
        or default_exp
        or ""
    ).strip()

    # Strike price
    strike_val = (
        get_val("strike")
        or get_val("strikePrice")
        or get_val("strike_price")
        or parsed_meta.get("strike")
        or 0.0
    )
    try:
        strike = float(strike_val)
    except (ValueError, TypeError):
        strike = 0.0

    # Option type
    raw_type = (
        get_val("option_type")
        or get_val("optionType")
        or get_val("type")
        or get_val("side")
        or parsed_meta.get("option_type")
        or default_type
        or ""
    )
    raw_type_str = str(raw_type).lower().strip()
    if raw_type_str in ("c", "call", "calls"):
        opt_type = "call"
    elif raw_type_str in ("p", "put", "puts"):
        opt_type = "put"
    else:
        opt_type = "call"

    # Trade / Last price
    price_val = (
        get_val("lastPrice")
        or get_val("last_price")
        or get_val("price")
        or get_val("trade_price")
        or get_val("last")
        or 0.0
    )
    try:
        trade_price = max(0.0, float(price_val))
    except (ValueError, TypeError):
        trade_price = 0.0

    # Bid
    bid_val = get_val("bid") or get_val("bidPrice") or get_val("bid_price") or 0.0
    try:
        bid = max(0.0, float(bid_val))
    except (ValueError, TypeError):
        bid = 0.0

    # Ask
    ask_val = get_val("ask") or get_val("askPrice") or get_val("ask_price") or 0.0
    try:
        ask = max(0.0, float(ask_val))
    except (ValueError, TypeError):
        ask = 0.0

    # If trade_price is 0 but bid/ask exists, use midpoint
    if trade_price <= 0.0 and bid > 0 and ask > 0:
        trade_price = round((bid + ask) / 2.0, 4)

    # Volume
    vol_val = get_val("volume") or get_val("vol") or get_val("trade_volume") or 0
    try:
        volume = max(0, int(float(vol_val)))
    except (ValueError, TypeError):
        volume = 0

    # Open Interest
    oi_val = get_val("openInterest") or get_val("open_interest") or get_val("oi") or 0
    try:
        open_interest = max(0, int(float(oi_val)))
    except (ValueError, TypeError):
        open_interest = 0

    # Implied Volatility
    iv_val = (
        get_val("impliedVolatility")
        or get_val("implied_volatility")
        or get_val("iv")
    )
    iv = None
    if iv_val is not None:
        try:
            iv_float = float(iv_val)
            if iv_float > 0 and not math.isnan(iv_float):
                iv = round(iv_float, 4)
        except (ValueError, TypeError):
            iv = None

    # Historical Volatility
    hv_val = (
        get_val("historicalVolatility")
        or get_val("historical_volatility")
        or get_val("hv_30")
        or get_val("hv30")
    )
    hv = None
    if hv_val is not None:
        try:
            hv_float = float(hv_val)
            if hv_float > 0 and not math.isnan(hv_float):
                hv = round(hv_float, 4)
        except (ValueError, TypeError):
            hv = None

    # Construct standard contract symbol if missing
    if not contract_sym:
        exp_clean = expiration.replace("-", "")[2:8] if len(expiration) == 10 else "000000"
        strike_occ = int(round(strike * 1000))
        t_char = "C" if opt_type == "call" else "P"
        contract_sym = f"{ticker}{exp_clean}{t_char}{strike_occ:08d}"

    return {
        "symbol": ticker,
        "contract_symbol": contract_sym,
        "expiration": expiration,
        "strike": strike,
        "option_type": opt_type,
        "trade_price": trade_price,
        "bid": bid,
        "ask": ask,
        "volume": volume,
        "open_interest": open_interest,
        "iv": iv,
        "hv": hv,
    }


def _extract_all_contracts_from_chain(chain_data: Any) -> List[Dict[str, Any]]:
    """Recursively walks arbitrary chain_data container to extract contract items."""
    contracts: List[Dict[str, Any]] = []
    if chain_data is None:
        return contracts

    # 1. Pandas DataFrame
    if isinstance(chain_data, pd.DataFrame):
        for _, row in chain_data.iterrows():
            item = _extract_contract_fields(row)
            if item:
                contracts.append(item)
        return contracts

    # 2. Object with .calls and .puts attributes (e.g., yfinance OptionsChain or mock)
    if hasattr(chain_data, "calls") or hasattr(chain_data, "puts"):
        calls_data = getattr(chain_data, "calls", None)
        puts_data = getattr(chain_data, "puts", None)
        exp = getattr(chain_data, "expiration", None)
        sym = getattr(chain_data, "symbol", None)

        if isinstance(calls_data, pd.DataFrame):
            for _, r in calls_data.iterrows():
                item = _extract_contract_fields(r, default_type="call", default_exp=exp, default_symbol=sym)
                if item:
                    contracts.append(item)
        elif isinstance(calls_data, (list, tuple)):
            for c in calls_data:
                item = _extract_contract_fields(c, default_type="call", default_exp=exp, default_symbol=sym)
                if item:
                    contracts.append(item)

        if isinstance(puts_data, pd.DataFrame):
            for _, r in puts_data.iterrows():
                item = _extract_contract_fields(r, default_type="put", default_exp=exp, default_symbol=sym)
                if item:
                    contracts.append(item)
        elif isinstance(puts_data, (list, tuple)):
            for p in puts_data:
                item = _extract_contract_fields(p, default_type="put", default_exp=exp, default_symbol=sym)
                if item:
                    contracts.append(item)
        return contracts

    # 3. Dictionary
    if isinstance(chain_data, dict):
        # Case A: {"options": [...]} or {"calls": [...], "puts": [...]}
        if "options" in chain_data and isinstance(chain_data["options"], list):
            for c in chain_data["options"]:
                item = _extract_contract_fields(c)
                if item:
                    contracts.append(item)
            return contracts

        if "calls" in chain_data or "puts" in chain_data:
            calls_list = chain_data.get("calls") or []
            puts_list = chain_data.get("puts") or []
            exp = chain_data.get("expiration")
            sym = chain_data.get("symbol") or chain_data.get("ticker")

            if isinstance(calls_list, pd.DataFrame):
                for _, r in calls_list.iterrows():
                    item = _extract_contract_fields(r, default_type="call", default_exp=exp, default_symbol=sym)
                    if item:
                        contracts.append(item)
            elif isinstance(calls_list, (list, tuple)):
                for c in calls_list:
                    item = _extract_contract_fields(c, default_type="call", default_exp=exp, default_symbol=sym)
                    if item:
                        contracts.append(item)

            if isinstance(puts_list, pd.DataFrame):
                for _, r in puts_list.iterrows():
                    item = _extract_contract_fields(r, default_type="put", default_exp=exp, default_symbol=sym)
                    if item:
                        contracts.append(item)
            elif isinstance(puts_list, (list, tuple)):
                for p in puts_list:
                    item = _extract_contract_fields(p, default_type="put", default_exp=exp, default_symbol=sym)
                    if item:
                        contracts.append(item)
            return contracts

        # Case B: Expiration-mapped dict {"2026-09-18": {"calls": ..., "puts": ...}, ...}
        for exp_key, sub_chain in chain_data.items():
            if isinstance(sub_chain, dict):
                sub_calls = sub_chain.get("calls") or []
                sub_puts = sub_chain.get("puts") or []
                sym = sub_chain.get("symbol") or sub_chain.get("ticker")

                if isinstance(sub_calls, pd.DataFrame):
                    for _, r in sub_calls.iterrows():
                        item = _extract_contract_fields(r, default_type="call", default_exp=exp_key, default_symbol=sym)
                        if item:
                            contracts.append(item)
                elif isinstance(sub_calls, (list, tuple)):
                    for c in sub_calls:
                        item = _extract_contract_fields(c, default_type="call", default_exp=exp_key, default_symbol=sym)
                        if item:
                            contracts.append(item)

                if isinstance(sub_puts, pd.DataFrame):
                    for _, r in sub_puts.iterrows():
                        item = _extract_contract_fields(r, default_type="put", default_exp=exp_key, default_symbol=sym)
                        if item:
                            contracts.append(item)
                elif isinstance(sub_puts, (list, tuple)):
                    for p in sub_puts:
                        item = _extract_contract_fields(p, default_type="put", default_exp=exp_key, default_symbol=sym)
                        if item:
                            contracts.append(item)
            elif hasattr(sub_chain, "calls") or hasattr(sub_chain, "puts"):
                sub_res = _extract_all_contracts_from_chain(sub_chain)
                contracts.extend(sub_res)
            elif isinstance(sub_chain, (list, tuple)):
                for c in sub_chain:
                    item = _extract_contract_fields(c, default_exp=exp_key)
                    if item:
                        contracts.append(item)
        return contracts

    # 4. List of contracts or list of chains
    if isinstance(chain_data, (list, tuple)):
        for entry in chain_data:
            if hasattr(entry, "calls") or (isinstance(entry, dict) and ("calls" in entry or "puts" in entry or "options" in entry)):
                contracts.extend(_extract_all_contracts_from_chain(entry))
            else:
                item = _extract_contract_fields(entry)
                if item:
                    contracts.append(item)
        return contracts

    return contracts


# ---------------------------------------------------------------------------
# Master UOA Anomaly Detection Scanner
# ---------------------------------------------------------------------------


def scan_unusual_options_activity(
    chain_data: Any,
    spot_price: Optional[float] = None,
    min_vol_oi_ratio: float = DEFAULT_MIN_VOL_OI_RATIO,
    min_notional: float = DEFAULT_MIN_NOTIONAL,
    min_volume: int = DEFAULT_MIN_VOLUME,
    historical_vol_30d: Optional[float] = None,
    historical_prices: Optional[Any] = None,
    historical_volatility: Optional[float] = None,
    filter_anomalies: bool = True,
    **kwargs: Any,
) -> List[UOARecord]:
    """
    Scans option chain contracts for Unusual Options Activity (UOA) anomalies.

    Filtering Criteria:
    - Volume >= min_volume (default 500)
    - Volume / OI >= min_vol_oi_ratio (default 3.0x)
    - Premium Notional (Trade Price * Volume * 100) >= min_notional (default $100,000)

    Trade Aggressiveness:
    - "ask_sweep": last/trade price >= ask (Aggressive Bullish Call / Aggressive Bearish Put)
    - "bid_sweep": last/trade price <= bid (Aggressive Bearish Call / Aggressive Bullish Put)
    - "mid_block" / "block": bid < trade price < ask

    IV Burst Score:
    - Compares IV vs 30d Historical Volatility (IV / HV30)
    - Flags IV expansion when IV >= 1.25 * HV30

    Returns:
        List of UOARecord anomaly objects sorted descending by premium notional.
    """
    try:
        raw_contracts = _extract_all_contracts_from_chain(chain_data)
        if not raw_contracts:
            return []

        # Resolve 30d historical realized volatility if historical prices provided
        hv_30 = historical_vol_30d if historical_vol_30d is not None else historical_volatility
        if hv_30 is None and historical_prices is not None:
            hv_30 = calculate_historical_volatility(historical_prices, window=30)

        # Infer spot price if not provided
        resolved_spot = spot_price
        if (resolved_spot is None or resolved_spot <= 0) and raw_contracts:
            strikes = [c["strike"] for c in raw_contracts if c["strike"] > 0]
            if strikes:
                resolved_spot = float(np.median(strikes))

        anomalies: List[UOARecord] = []

        for c in raw_contracts:
            volume = c["volume"]
            oi = c["open_interest"]
            trade_price = c["trade_price"]
            bid = c["bid"]
            ask = c["ask"]
            strike = c["strike"]
            opt_type = c["option_type"]
            iv = c["iv"]
            hv_contract = c.get("hv") or hv_30
            symbol = c["symbol"]
            contract_sym = c["contract_symbol"]
            expiration = c["expiration"]

            # Calculate Volume / OI Ratio
            if oi > 0:
                vol_oi_ratio = round(float(volume) / float(oi), 2)
            elif volume > 0:
                vol_oi_ratio = float("inf")
            else:
                vol_oi_ratio = 0.0

            # Calculate Premium Notional ($)
            effective_price = trade_price if trade_price > 0 else ((bid + ask) / 2.0 if (bid + ask) > 0 else 0.0)
            notional = round(effective_price * volume * 100.0, 2)

            # Calculate Underlying Notional ($)
            ref_underlying = resolved_spot if (resolved_spot and resolved_spot > 0) else strike
            underlying_notional = round(ref_underlying * volume * 100.0, 2)

            # Check Anomaly Criteria
            is_vol_anomaly = volume >= min_volume
            is_ratio_anomaly = vol_oi_ratio >= min_vol_oi_ratio
            is_notional_anomaly = notional >= min_notional

            is_anomaly = is_vol_anomaly and is_ratio_anomaly and is_notional_anomaly

            if filter_anomalies and not is_anomaly:
                continue

            # Classify aggressiveness and sentiment
            aggressiveness, sentiment = categorize_trade_aggressiveness(trade_price, bid, ask, opt_type)
            trade_type = "block" if aggressiveness == "mid_block" else aggressiveness

            # Compute IV burst score
            iv_burst_score, iv_burst_detected = calculate_iv_burst_score(iv, hv_contract)

            # Format human-readable description
            ratio_str = f"{vol_oi_ratio:.1f}x" if vol_oi_ratio != float("inf") else "NEW/0 OI"
            opt_type_upper = opt_type.upper()
            action_desc = "Ask Sweep" if aggressiveness == "ask_sweep" else ("Bid Sweep" if aggressiveness == "bid_sweep" else "Mid Block")
            desc = (
                f"{sentiment} {action_desc}: {volume:,} {symbol} {expiration} ${strike:.2f} {opt_type_upper} "
                f"@ ${effective_price:.2f} (V/OI {ratio_str}, Notional ${notional:,.0f})"
            )

            record = UOARecord(
                symbol=symbol,
                contract_symbol=contract_sym,
                expiration=expiration,
                strike=strike,
                option_type=opt_type,
                trade_price=trade_price,
                price=trade_price,
                spot_price=resolved_spot,
                bid=bid,
                ask=ask,
                volume=volume,
                open_interest=oi,
                vol_oi_ratio=vol_oi_ratio if vol_oi_ratio != float("inf") else 999.99,
                notional=notional,
                underlying_notional=underlying_notional,
                aggressiveness=aggressiveness,
                trade_type=trade_type,
                sentiment=sentiment,
                iv=iv,
                implied_volatility=iv,
                hv_30=hv_contract,
                historical_volatility=hv_contract,
                iv_burst_score=iv_burst_score,
                iv_burst_detected=iv_burst_detected,
                iv_expansion_flag=iv_burst_detected,
                description=desc,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            anomalies.append(record)

        # Sort anomalies by premium notional descending
        anomalies.sort(key=lambda x: x.notional, reverse=True)
        return anomalies

    except Exception as exc:  # noqa: BLE001 — never raises (CONSTRAINT #6)
        logger.debug("scan_unusual_options_activity failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Net Order Flow Sentiment Aggregator
# ---------------------------------------------------------------------------


def calculate_net_flow_sentiment(
    symbol: str,
    uoa_records: Optional[Sequence[Union[UOARecord, Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """
    Aggregates institutional directional options flow into a normalized sentiment score.

    Formula:
        Sentiment Score = (Bullish Notional - Bearish Notional) / Total Directional Notional in [-1.0, +1.0]

    Returns:
        Dictionary with sentiment score, label, notional breakdowns, volume, and top active strikes.
    """
    clean_sym = str(symbol or "").upper().strip()
    records_to_process = uoa_records if uoa_records is not None else load_uoa_records()
    filtered: List[Union[UOARecord, Dict[str, Any]]] = []

    for r in records_to_process:
        r_sym = r.symbol if isinstance(r, UOARecord) else str(r.get("symbol") or "")
        if not clean_sym or r_sym.upper().strip() == clean_sym:
            filtered.append(r)


    bullish_notional = 0.0
    bearish_notional = 0.0
    neutral_notional = 0.0
    call_volume = 0
    put_volume = 0
    strikes_volume: Dict[float, int] = {}
    strikes_notional: Dict[float, float] = {}
    strikes_call_volume: Dict[float, int] = {}
    strikes_put_volume: Dict[float, int] = {}

    for r in filtered:
        notional = float(r.notional if isinstance(r, UOARecord) else (r.get("notional") or 0.0))
        sentiment = str(r.sentiment if isinstance(r, UOARecord) else (r.get("sentiment") or "")).upper()
        opt_type = str(r.option_type if isinstance(r, UOARecord) else (r.get("option_type") or "")).lower()
        vol = int(r.volume if isinstance(r, UOARecord) else (r.get("volume") or 0))
        strike = float(r.strike if isinstance(r, UOARecord) else (r.get("strike") or 0.0))

        if strike > 0:
            strikes_volume[strike] = strikes_volume.get(strike, 0) + vol
            strikes_notional[strike] = strikes_notional.get(strike, 0.0) + notional
            if opt_type == "call":
                strikes_call_volume[strike] = strikes_call_volume.get(strike, 0) + vol
            elif opt_type == "put":
                strikes_put_volume[strike] = strikes_put_volume.get(strike, 0) + vol

        if opt_type == "call":
            call_volume += vol
        elif opt_type == "put":
            put_volume += vol

        if sentiment == "BULLISH":
            bullish_notional += notional
        elif sentiment == "BEARISH":
            bearish_notional += notional
        else:
            neutral_notional += notional

    total_directional_notional = bullish_notional + bearish_notional
    total_notional = total_directional_notional + neutral_notional

    if total_directional_notional > 0:
        sentiment_score = round((bullish_notional - bearish_notional) / total_directional_notional, 4)
    else:
        sentiment_score = 0.0

    # Categorize sentiment label
    if sentiment_score >= 0.60:
        sentiment_label = "VERY_BULLISH"
    elif sentiment_score >= 0.20:
        sentiment_label = "BULLISH"
    elif sentiment_score <= -0.60:
        sentiment_label = "VERY_BEARISH"
    elif sentiment_score <= -0.20:
        sentiment_label = "BEARISH"
    else:
        sentiment_label = "NEUTRAL"

    # Call / Put volume ratio
    if put_volume > 0:
        call_put_ratio = round(float(call_volume) / float(put_volume), 2)
    elif call_volume > 0:
        call_put_ratio = float("inf")
    else:
        call_put_ratio = 1.0

    # Top active strikes. `option_type` is the side with more volume at that strike
    # (a real, computed majority classification -- never fabricated); `notional` is
    # the real combined call+put notional traded at that strike.
    sorted_strikes = sorted(strikes_volume.items(), key=lambda x: x[1], reverse=True)[:5]
    top_strikes = [
        {
            "strike": k,
            "volume": v,
            "option_type": "CALL" if strikes_call_volume.get(k, 0) >= strikes_put_volume.get(k, 0) else "PUT",
            "notional": round(strikes_notional.get(k, 0.0), 2),
        }
        for k, v in sorted_strikes
    ]

    return {
        "symbol": clean_sym,
        "sentiment_score": sentiment_score,
        "sentiment_label": sentiment_label,
        "bullish_notional": round(bullish_notional, 2),
        "bearish_notional": round(bearish_notional, 2),
        "neutral_notional": round(neutral_notional, 2),
        "total_notional": round(total_notional, 2),
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_put_ratio": call_put_ratio if call_put_ratio != float("inf") else 999.99,
        "top_active_strikes": top_strikes,
        "record_count": len(filtered),
    }


# ---------------------------------------------------------------------------
# Storage & Read Helpers
# ---------------------------------------------------------------------------


def save_uoa_records(
    records: Sequence[Union[UOARecord, Dict[str, Any]]],
    path: Optional[Union[str, Path]] = None,
) -> str:
    """Persists UOA records to output JSON file (atomic write)."""
    p = Path(path) if path else _default_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    serializable = [r.to_dict() if isinstance(r, UOARecord) else r for r in records]
    p.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    return str(p)


def load_uoa_records(path: Optional[Union[str, Path]] = None) -> List[UOARecord]:
    """Loads persisted UOA records from output JSON file (never raises)."""
    p = Path(path) if path else _default_path()
    try:
        if not p.exists():
            return []
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        records: List[UOARecord] = []
        valid_fields = set(UOARecord.__dataclass_fields__.keys())
        for item in raw:
            if isinstance(item, dict):
                clean_kwargs = {k: v for k, v in item.items() if k in valid_fields}
                records.append(UOARecord(**clean_kwargs))
        return records
    except Exception as exc:  # noqa: BLE001 — never raises
        logger.debug("load_uoa_records failed: %s", exc)
        return []


def get_symbol_flow_sentiment(
    symbol: str,
    path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Computes sentiment breakdown for a symbol from persisted UOA stream."""
    records = load_uoa_records(path)
    return calculate_net_flow_sentiment(symbol, records)


def _fetch_live_options_chain_map(
    symbol: str,
    max_expirations: int = LIVE_SCAN_MAX_EXPIRATIONS,
) -> Optional[Dict[str, Any]]:
    """Fetches a real multi-expiration options chain for `symbol` via `CompositeOptionsProvider`.

    Mirrors the exact live-chain-fetch pattern already used by
    `pilots/options_gex.py::get_options_gex_profile`, `pilots/vol_mispricing.py`, and
    `pilots/har_volatility.py` (lazy `from data.market_data import get_options_provider`
    inside the function body, never at module scope). Returns an `{expiration: chain}`
    mapping (yfinance-style objects carrying `.calls`/`.puts` DataFrames) on success, or
    `None` on any failure or empty result.

    Deliberately never falls back to a synthetic/simulated chain (CONSTRAINT #4): this
    module scans for REAL institutional order flow, so "the provider has nothing" must
    degrade to "no data", never a plausible-looking fabricated substitute.
    """
    try:
        from data.market_data import get_options_provider

        provider = get_options_provider()
        expirations = provider.fetch_options_chain(symbol)
        if not expirations or not isinstance(expirations, (list, tuple)):
            return None

        chain_map: Dict[str, Any] = {}
        for exp in list(expirations)[:max_expirations]:
            chain = provider.fetch_options_chain(symbol, str(exp))
            if chain:
                chain_map[str(exp)] = chain
        return chain_map or None
    except Exception as exc:  # noqa: BLE001 — never raises (CONSTRAINT #6)
        logger.debug("_fetch_live_options_chain_map failed for %s: %s", symbol, exc)
        return None


def _resolve_live_spot_price(symbol: str) -> Optional[float]:
    """Resolves the current spot price for `symbol` via the general market data provider.

    Same pattern as `pilots/options_gex.py::get_options_gex_profile`'s spot resolution.
    Returns `None` (never a fabricated price — CONSTRAINT #4) on any failure.
    """
    try:
        from data.market_data import get_provider

        market_provider = get_provider()
        if market_provider is not None:
            quote = market_provider.get_latest_quote(symbol)
            price = getattr(quote, "price", None)
            if price and float(price) > 0:
                return float(price)
    except Exception as exc:  # noqa: BLE001 — never raises (CONSTRAINT #6)
        logger.debug("_resolve_live_spot_price failed for %s: %s", symbol, exc)
    return None


def get_unusual_options_activity(
    symbols: Optional[List[str]] = None,
    min_vol_oi: Optional[float] = None,
    min_notional: Optional[float] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Public accessor for unusual options activity flow — a read-through cache.

    Read order:
    1. Persisted UOA records (`load_uoa_records()`), filtered by `symbols`/thresholds.
    2. On a miss, and only for a caller-supplied, bounded `symbols` list (never an
       unfiltered universe-wide scan inside one synchronous read request), fetches a
       REAL options chain per symbol via `CompositeOptionsProvider`
       (`data/market_data.py` — see `_fetch_live_options_chain_map`), scans it for
       genuine anomalies, and persists any findings via `save_uoa_records()` so this
       call and future reads (including `signals/options_flow_sentiment.py`'s
       `pre_compute`) see real data instead of a structurally-empty result.

    Honesty (CONSTRAINT #4): never fabricates a record. A request with no `symbols`
    and nothing persisted yet, or a provider miss for every requested symbol, degrades
    to `[]` rather than a synthetic/simulated scan.
    """
    persisted = load_uoa_records()
    if persisted:
        records = [r.to_dict() if hasattr(r, "to_dict") else asdict(r) for r in persisted]
        if symbols:
            clean_syms = {s.upper().strip() for s in symbols}
            records = [r for r in records if str(r.get("symbol", "")).upper() in clean_syms]
        if min_vol_oi is not None:
            records = [r for r in records if float(r.get("vol_oi_ratio", 0)) >= min_vol_oi]
        if min_notional is not None:
            records = [r for r in records if float(r.get("notional", 0)) >= min_notional]
        if records:
            return records[:limit]

    # Nothing usable persisted. A live scan only makes sense for a bounded, caller-named
    # symbol list — scanning "the whole market" synchronously inside one read request
    # isn't viable, so an unfiltered request with nothing persisted yet honestly
    # returns [] rather than a meaningless scan of an empty chain.
    if not symbols:
        return []

    new_records: List[UOARecord] = []
    for sym in sorted({s.upper().strip() for s in symbols if s and s.strip()}):
        chain_map = _fetch_live_options_chain_map(sym)
        if not chain_map:
            continue
        spot_price = _resolve_live_spot_price(sym)
        scanned = scan_unusual_options_activity(chain_data=chain_map, spot_price=spot_price)
        new_records.extend(scanned)

    if not new_records:
        return []

    # Persist newly-discovered records alongside whatever was already on disk so later
    # reads (and OptionsFlowSentimentSignal.pre_compute) build on real accumulated flow
    # instead of each request clobbering the last one's findings. Trimmed to the most
    # recent MAX_PERSISTED_UOA_RECORDS (by timestamp) so a read-triggered write can't
    # grow output/unusual_options_flow.json without bound.
    try:
        merged = list(persisted) + new_records
        if len(merged) > MAX_PERSISTED_UOA_RECORDS:
            merged.sort(key=lambda r: r.timestamp or "", reverse=True)
            merged = merged[:MAX_PERSISTED_UOA_RECORDS]
        save_uoa_records(merged)
    except Exception as exc:  # noqa: BLE001 — never raises (CONSTRAINT #6)
        logger.debug("get_unusual_options_activity: failed to persist new records: %s", exc)

    # Dispatch whale sweep alerts for qualifying records (non-blocking, condition-deduped)
    for rec in new_records:
        try:
            from pilots.options_alerts import dispatch_uoa_whale_alert
            dispatch_uoa_whale_alert(rec)
        except Exception as exc:  # noqa: BLE001 — never raises (CONSTRAINT #6)
            logger.debug("UOA whale alert dispatch failed for %s: %s", getattr(rec, "contract_symbol", ""), exc)

    records = [r.to_dict() for r in new_records]
    if min_vol_oi is not None:
        records = [r for r in records if float(r.get("vol_oi_ratio", 0)) >= min_vol_oi]
    if min_notional is not None:
        records = [r for r in records if float(r.get("notional", 0)) >= min_notional]
    records.sort(key=lambda r: r.get("notional", 0.0), reverse=True)
    return records[:limit]


def get_flow_sentiment(symbol: str) -> Dict[str, Any]:
    """Public accessor for options flow sentiment for symbol."""
    return get_symbol_flow_sentiment(symbol)


def to_flow_sentiment_response(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reshapes calculate_net_flow_sentiment()/get_flow_sentiment()'s internal result
    (call_put_ratio -- the shape every existing test in tests/test_unusual_options_flow.py
    asserts on) into the FlowSentimentData contract webapp/src/api/types.ts,
    webapp/src/api/mock.ts, and webapp/src/components/options/UnusualFlowFeed.tsx
    already agree on (put_call_ratio -- the RECIPROCAL of call_put_ratio, not a mere
    rename: webapp/src/components/options/UnusualFlowFeed.tsx reads
    sentiment.put_call_ratio.toFixed(2) unconditionally, so the live endpoint was handing
    the frontend `undefined` -- the exact "Cannot read properties of undefined (reading
    'toFixed')" bug class this fix addresses -- every time this panel opened.

    `put_call_ratio: number` is non-optional in the frontend contract, so this is derived
    directly from call_volume/put_volume (always present, real integers -- never a
    fabricated 0) using the same "no puts/calls at all" clamp-to-999.99 and "both zero"
    convention calculate_net_flow_sentiment() already applies to its own call_put_ratio,
    rather than inverting that field's own lossy sentinel.

    Kept as a separate step from get_flow_sentiment() itself (applied at the API handler
    for GET /pilots/options/flow/sentiment instead) so every existing caller/test of the
    pure computation is unaffected, and so a test that mocks get_flow_sentiment() directly
    still exercises the real reshape the live endpoint applies.
    """
    response = dict(raw)
    response.pop("call_put_ratio", None)

    try:
        call_volume = float(raw.get("call_volume") or 0.0)
        put_volume = float(raw.get("put_volume") or 0.0)
    except (TypeError, ValueError):
        call_volume = put_volume = 0.0

    if call_volume > 0:
        response["put_call_ratio"] = round(put_volume / call_volume, 4)
    elif put_volume > 0:
        response["put_call_ratio"] = 999.99
    else:
        response["put_call_ratio"] = 1.0

    return response

