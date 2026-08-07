import re

file_path = "settings.py"
with open(file_path, "r") as f:
    content = f.read()

keys = [
    "CACHE_LONG_SHORT_WRITES_ENABLED", "GENERAL_SETTINGS_WRITES_ENABLED",
    "STRATEGY_WRITES_ENABLED", "LLM_WRITES_ENABLED", "AUTOMATION_WRITES_ENABLED",
    "MACRO_GATE_WRITES_ENABLED", "PROMPT_REGISTRY_WRITES_ENABLED",
    "DEAD_LETTER_RETRY_ENABLED", "RAG_QUERY_API_ENABLED", "AI_GENERATION_API_ENABLED",
    "BROKERAGE_CONNECT_ENABLED", "BROKERAGE_REFRESH_ENABLED", "UNIVERSE_SYNC_ENABLED",
    "AGENTIC_DISCOVERY_ENABLED", "JOBS_API_ENABLED", "PILOTS_API_ENABLED",
    "SECTOR_HEAT_ENABLED", "WIKIPEDIA_ATTENTION_ENABLED", "ETF_HOLDINGS_ENABLED",
    "ETF_TRANSMISSION_ENABLED", "MARKET_DATA_LATENCY_TRACKING_ENABLED",
    "SENTIMENT_INDEX_ENABLED", "EDGAR_FULLTEXT_ENABLED"
]

for key in keys:
    # Pattern to match the specific field block and replace default=False with default=True
    pattern = r"(" + key + r"\s*:\s*bool\s*=\s*Field\(\s*)default=False"
    content = re.sub(pattern, r"\g<1>default=True", content, count=1)
    
    # Also attempt to replace "False (the default)" with "False (default off)" or something, but actually we can just replace "False (default)" with "False" or "False (disabled)"
    # Let's see how they are phrased.
    
with open(file_path, "w") as f:
    f.write(content)
