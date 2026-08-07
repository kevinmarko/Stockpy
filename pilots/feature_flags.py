"""Feature Flags domain registry."""

from typing import Dict
from settings_keysets import DANGEROUS_KEYS

# 7 Tier-2 diagnostic/data features that we also want to surface in Feature Flags
DIAGNOSTIC_FLAG_REASONS: Dict[str, str] = {
    "SECTOR_HEAT_ENABLED": "Enables the Sector Heat diagnostic panel (requires Alpaca credentials).",
    "WIKIPEDIA_ATTENTION_ENABLED": "Enables Wikipedia pageviews diagnostic column.",
    "ETF_HOLDINGS_ENABLED": "Enables live ETF constituent-holdings ingestion.",
    "ETF_TRANSMISSION_ENABLED": "Enables daily transmission of ETF constituent changes.",
    "MARKET_DATA_LATENCY_TRACKING_ENABLED": "Enables latency instrumentation on quote fetches.",
    "SENTIMENT_INDEX_ENABLED": "Enables News Sentiment Index diagnostic column.",
    "EDGAR_FULLTEXT_ENABLED": "Enables EDGAR full-text search capability for fundamentals."
}

# The complete set of feature flags (Admin gates + Diagnostic features + explicitly-gated keys)
FEATURE_FLAG_KEYS = DANGEROUS_KEYS | frozenset(DIAGNOSTIC_FLAG_REASONS) | frozenset([
    "AGENTIC_DISCOVERY_ENABLED",
    "BROKERAGE_CONNECT_ENABLED", 
    "RLHF_CALIBRATION_ENABLED"
])
