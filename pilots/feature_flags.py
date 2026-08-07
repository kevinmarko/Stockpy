"""
Feature flags registry for the Pilots API.
Surfaces the dangerous keys (admin/write/execution gates) and read-only
diagnostic/data features into a unified list for the Feature Flags settings screen.
"""

import settings_keysets

DIAGNOSTIC_FLAG_REASONS: dict[str, str] = {
    "SECTOR_HEAT_ENABLED": "Enables Sector Heat Factor computation from GDELT article volume.",
    "WIKIPEDIA_ATTENTION_ENABLED": "Enables Attention Score computation from Wikipedia pageviews.",
    "ETF_HOLDINGS_ENABLED": "Enables fetching ETF constituent baskets for exposure analysis.",
    "ETF_TRANSMISSION_ENABLED": "Enables ETF volatility-transmission measurement columns.",
    "MARKET_DATA_LATENCY_TRACKING_ENABLED": "Tracks and surfaces real-time market data feed latency.",
    "SENTIMENT_INDEX_ENABLED": "Computes composite sentiment index from news and reviews.",
    "EDGAR_FULLTEXT_ENABLED": "Enables full-text ingestion of 10-K/10-Q SEC filings."
}

# The unified set of all feature flag keys exposed in the Feature Flags screen.
# Inherits settings_keysets.DANGEROUS_KEYS so any future addition there is
# automatically exposed here.
FEATURE_FLAG_KEYS: frozenset[str] = frozenset(
    settings_keysets.DANGEROUS_KEYS | set(DIAGNOSTIC_FLAG_REASONS.keys())
)
