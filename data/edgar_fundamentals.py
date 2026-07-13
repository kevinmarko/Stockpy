import json
import logging
import time
import urllib.request
import urllib.error
from typing import Dict, Any, Optional
import math

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

USER_AGENT = "InvestYo_Quant_Platform (beforecoast@gmail.com)"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_URL_TEMPLATE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

_cik_cache = {}
_last_request_time = 0.0
_REQUEST_DELAY = 0.15  # 10 req/sec limit, so 150ms delay is safe

def _throttle():
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _REQUEST_DELAY:
        time.sleep(_REQUEST_DELAY - elapsed)
    _last_request_time = time.time()

def _http_get(url: str) -> bytes:
    _throttle()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        logger.warning("HTTP %d for %s", exc.code, url)
        raise

def get_cik(symbol: str) -> Optional[str]:
    """Resolve a symbol to its 10-digit CIK string. Caches in memory."""
    symbol = symbol.upper()
    if not _cik_cache:
        try:
            data = json.loads(_http_get(SEC_TICKERS_URL).decode("utf-8"))
            for entry in data.values():
                _cik_cache[entry["ticker"].upper()] = str(entry["cik_str"]).zfill(10)
        except Exception as exc:
            logger.warning("Failed to fetch SEC tickers: %s", exc)
            return None
    
    return _cik_cache.get(symbol)

def fetch_companyfacts(cik: str) -> Dict[str, Any]:
    """Fetch the raw companyfacts XBRL JSON from EDGAR."""
    url = SEC_FACTS_URL_TEMPLATE.format(cik=cik)
    try:
        return json.loads(_http_get(url).decode("utf-8"))
    except Exception as exc:
        logger.warning("Failed to fetch facts for CIK %s: %s", cik, exc)
        return {}

def extract_latest_fact(us_gaap: Dict[str, Any], fact_name: str, max_date: str) -> Optional[float]:
    """Extract the latest fact value filed ON OR BEFORE max_date."""
    if fact_name not in us_gaap:
        return None
    
    units = us_gaap[fact_name].get("units", {})
    if "USD" in units:
        data = units["USD"]
    elif "shares" in units:
        data = units["shares"]
    elif "USD/shares" in units:
        data = units["USD/shares"]
    elif "pure" in units:
        data = units["pure"]
    else:
        # Get the first available unit array
        if not units:
            return None
        data = list(units.values())[0]

    latest_val = None
    latest_filed = ""
    
    for point in data:
        filed = point.get("filed", "")
        if filed and filed <= max_date:
            if filed >= latest_filed:
                latest_filed = filed
                latest_val = point.get("val")
                
    return latest_val

def compute_pit_ratios(facts: Dict[str, Any], report_date: str, price: float, shares: float) -> Dict[str, float]:
    """Compute fundamental ratios as they would have appeared on report_date."""
    _NAN = float("nan")
    out = {
        "pe_ratio": _NAN,
        "pb_ratio": _NAN,
        "roe": _NAN,
        "dividend_yield": _NAN,
        "market_cap": _NAN,
        "eps": _NAN,
        "operating_margin": _NAN,
        "debt_to_equity": _NAN,
    }
    
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        return out
        
    eps = extract_latest_fact(us_gaap, "EarningsPerShareDiluted", report_date)
    if eps is None:
        eps = extract_latest_fact(us_gaap, "EarningsPerShareBasic", report_date)
        
    equity = extract_latest_fact(us_gaap, "StockholdersEquity", report_date)
    net_income = extract_latest_fact(us_gaap, "NetIncomeLoss", report_date)
    op_income = extract_latest_fact(us_gaap, "OperatingIncomeLoss", report_date)
    revenue = extract_latest_fact(us_gaap, "Revenues", report_date)
    if revenue is None:
        revenue = extract_latest_fact(us_gaap, "SalesRevenueNet", report_date)
    
    dividends = extract_latest_fact(us_gaap, "PaymentsOfDividends", report_date)
    if dividends is None:
        dividends = extract_latest_fact(us_gaap, "PaymentsOfDividendsCommonStock", report_date)
        
    debt = extract_latest_fact(us_gaap, "LongTermDebt", report_date)
        
    if eps is not None:
        out["eps"] = float(eps)
        if np.isfinite(price) and price > 0 and float(eps) > 0:
            out["pe_ratio"] = price / float(eps)
            
    if equity is not None and shares > 0:
        book_value = float(equity) / shares
        if book_value > 0 and np.isfinite(price) and price > 0:
            out["pb_ratio"] = price / book_value
            
    if net_income is not None and equity is not None and float(equity) > 0:
        out["roe"] = float(net_income) / float(equity)
        
    if np.isfinite(price) and price > 0 and shares > 0:
        out["market_cap"] = price * shares
        
    if dividends is not None and out["market_cap"] is not _NAN and out["market_cap"] > 0:
        # dividends paid is total dollar amount over the period (usually annual if 10-K).
        # We assume it's annual total dividend paid if we take the latest.
        out["dividend_yield"] = float(dividends) / out["market_cap"]
        
    if op_income is not None and revenue is not None and float(revenue) > 0:
        out["operating_margin"] = float(op_income) / float(revenue)
        
    if debt is not None and equity is not None and float(equity) > 0:
        out["debt_to_equity"] = (float(debt) / float(equity)) * 100.0

    return out
