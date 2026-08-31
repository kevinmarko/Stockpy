"""Client for fetching Google Trends data with rate-limiting and overlapping windows."""
import logging
import time
from datetime import datetime, timedelta
from typing import List

import pandas as pd
from pytrends.request import TrendReq
from requests.exceptions import RequestException

from settings import settings

logger = logging.getLogger(__name__)

# Track consecutive failures for the cooldown (process-wide)
_consecutive_failures = 0
_cooldown_until = 0.0
_last_request_time = 0.0

def fetch_overlapping_windows(
    query_term: str,
    start_date: str,
    end_date: str,
    window_days: int = 90,
    overlap_days: int = 30
) -> List[pd.Series]:
    """
    Fetch Google Trends data for a query term over a date range, breaking the range
    into overlapping windows.

    Args:
        query_term: The search term to query.
        start_date: Start date string in 'YYYY-MM-DD' format.
        end_date: End date string in 'YYYY-MM-DD' format.
        window_days: The size of each fetch window in days.
        overlap_days: The number of days consecutive windows should overlap.

    Returns:
        A list of pandas Series objects containing the trend data for each window,
        or an empty list if a failure or cooldown prevents fetching.
    """
    global _consecutive_failures, _cooldown_until, _last_request_time
    
    if not settings.GOOGLE_TRENDS_ENABLED:
        logger.warning("Google Trends ASVI is disabled via settings.")
        return []

    if window_days <= overlap_days:
        logger.warning("window_days must be greater than overlap_days.")
        return []
        
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    
    if start_dt > end_dt:
        return []

    pytrends = TrendReq(hl='en-US', tz=360)
    results: List[pd.Series] = []
    
    current_start = start_dt
    
    while current_start < end_dt:
        current_end = current_start + timedelta(days=window_days)
        if current_end > end_dt:
            current_end = end_dt
            
        timeframe = f"{current_start.strftime('%Y-%m-%d')} {current_end.strftime('%Y-%m-%d')}"
        
        # Enforce shared rate limiter / cooldown
        now = time.time()
        if settings.GOOGLE_TRENDS_COOLDOWN_THRESHOLD > 0 and _consecutive_failures >= settings.GOOGLE_TRENDS_COOLDOWN_THRESHOLD:
            if now < _cooldown_until:
                logger.warning(
                    "Google Trends client is in cooldown until %s. Skipping fetch for %s.",
                    datetime.fromtimestamp(_cooldown_until).isoformat(),
                    query_term
                )
                return []
            else:
                # Cooldown expired, tentatively reset
                _consecutive_failures = 0

        # Enforce minimum request interval
        if settings.GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS > 0:
            elapsed = time.time() - _last_request_time
            if elapsed < settings.GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS:
                time.sleep(settings.GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS - elapsed)

        attempt = 0
        success = False
        series: pd.Series = pd.Series(dtype='float64')
        max_retries = max(0, settings.GOOGLE_TRENDS_MAX_RETRIES)
        
        while attempt <= max_retries and not success:
            _last_request_time = time.time()
            try:
                pytrends.build_payload(kw_list=[query_term], timeframe=timeframe)
                df = pytrends.interest_over_time()
                
                # Check if empty
                if df.empty or query_term not in df.columns:
                    # Sometimes empty responses happen if no data
                    logger.warning("Google Trends returned empty data for %s during %s", query_term, timeframe)
                    break # Not a failure per se, just no data, so break retry loop
                    
                series = df[query_term]
                success = True
                _consecutive_failures = 0 # reset on success
                
            except Exception as e: # Catch all, including requests and pytrends exceptions
                attempt += 1
                _consecutive_failures += 1
                logger.warning(
                    "Google Trends request failed (attempt %d/%d) for %s: %s",
                    attempt, max_retries + 1, query_term, e
                )
                
                # Apply cooldown if threshold hit
                if settings.GOOGLE_TRENDS_COOLDOWN_THRESHOLD > 0 and _consecutive_failures >= settings.GOOGLE_TRENDS_COOLDOWN_THRESHOLD:
                    _cooldown_until = time.time() + settings.GOOGLE_TRENDS_COOLDOWN_SECONDS
                    logger.warning("Google Trends cooldown threshold reached. Cooldown activated.")
                    return []
                    
                if attempt <= max_retries:
                    # Simple backoff (pytrends doesn't expose headers easily, so we just do exponential)
                    time.sleep(5.0 * (2 ** (attempt - 1)))
        
        if success:
            results.append(series)
        
        # Advance the window
        current_start = current_end - timedelta(days=overlap_days)
        # Prevent infinite loop if overlap >= window_days, handled by check above
        if current_end >= end_dt:
            break

    return results

def reset_limiter_state():
    """Reset the rate limiter state (useful for tests)."""
    global _consecutive_failures, _cooldown_until, _last_request_time
    _consecutive_failures = 0
    _cooldown_until = 0.0
    _last_request_time = 0.0

