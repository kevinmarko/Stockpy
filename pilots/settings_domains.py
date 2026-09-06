"""Domain taxonomy for Settings Reference (GET /settings/reference).

Classifies every Settings field into one of 14 functional domains:
1. Financial/Risk/Sizing
2. Execution/Brokers
3. Options Desk
4. Market Data/DB
5. Universe/Watchlist
6. Forecasting/ML
7. ETF Transmission
8. Sentiment/News/Attention
9. AI/LLM/RAG
10. Orchestrator/Daemon/Jobs
11. Alerting/Observability
12. Strategy Overlays
13. Filesystem/Bootstrap
14. RLHF
"""

from __future__ import annotations

from typing import Dict, List
from settings import Settings

DOMAINS: List[str] = [
    "Financial/Risk/Sizing",
    "Execution/Brokers",
    "Options Desk",
    "Market Data/DB",
    "Universe/Watchlist",
    "Forecasting/ML",
    "ETF Transmission",
    "Sentiment/News/Attention",
    "AI/LLM/RAG",
    "Orchestrator/Daemon/Jobs",
    "Alerting/Observability",
    "Strategy Overlays",
    "Filesystem/Bootstrap",
    "RLHF",
]

_OVERRIDE_DOMAINS: Dict[str, str] = {
    # Financial / Risk / Sizing
    "ADVISORY_ONLY": "Financial/Risk/Sizing",
    "MAX_CORRELATION": "Financial/Risk/Sizing",
    "DAILY_LOSS_LIMIT_PCT": "Financial/Risk/Sizing",
    "MAX_ORDER_RATE_PER_MIN": "Financial/Risk/Sizing",
    "RISK_GATE_ENFORCE_MARKET_HOURS": "Financial/Risk/Sizing",
    "META_LABEL_MIN_CONFIDENCE": "Financial/Risk/Sizing",
    "KILLSWITCH_VIX_THRESHOLD": "Financial/Risk/Sizing",
    "KILLSWITCH_SAHM_THRESHOLD": "Financial/Risk/Sizing",
    "KILLSWITCH_OAS_THRESHOLD": "Financial/Risk/Sizing",
    "KILLSWITCH_VIX_THRESHOLD_AGREED": "Financial/Risk/Sizing",
    "KILLSWITCH_SAHM_THRESHOLD_AGREED": "Financial/Risk/Sizing",
    "MACRO_REGIME_GATE_ENABLED": "Financial/Risk/Sizing",
    "MACRO_GATE_WRITES_ENABLED": "Financial/Risk/Sizing",
    "MULTIFACTOR_MICROCAP_THRESHOLD": "Financial/Risk/Sizing",
    "BETA_LOOKBACK_DAYS": "Financial/Risk/Sizing",
    "ADVISORY_MAX_CONCURRENCY": "Financial/Risk/Sizing",
    "EXCURSION_INTRADAY_ENABLED": "Financial/Risk/Sizing",
    "EVAL_BROKER_TRADES_ENABLED": "Financial/Risk/Sizing",

    # Execution / Brokers
    "DRY_RUN": "Execution/Brokers",
    "FLATTEN_ON_KILL": "Execution/Brokers",
    "EXECUTION_PRIORITY_QUEUE_ENABLED": "Execution/Brokers",
    "EXECUTION_QUEUE_LEAK_RATE_PER_SEC": "Execution/Brokers",
    "OFI_SHIELD_ENABLED": "Execution/Brokers",
    "PAPER_TRADING_START_DATE": "Execution/Brokers",
    "OVERNIGHT_LIQUIDITY_DEPTH_HEURISTIC": "Execution/Brokers",
    "QUEUE_SOURCE_MAX_AGE_SECONDS": "Execution/Brokers",
    "FOLLOW_API_TOKEN": "Execution/Brokers",
    "FOLLOW_MIN_AMOUNT": "Execution/Brokers",
    "PILOTS_TOP_N": "Execution/Brokers",

    # Options Desk
    "PAPER_OPTIONS_AUTO_EXECUTE_ENABLED": "Options Desk",

    # Market Data / DB
    "MCP_DATABASE_URL_RO": "Market Data/DB",
    "DATABASE_URL": "Market Data/DB",
    "DB_ECHO": "Market Data/DB",
    "SQLITE_BUSY_TIMEOUT_MS": "Market Data/DB",
    "SQLITE_WAL_AUTOCHECKPOINT": "Market Data/DB",
    "PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE": "Market Data/DB",
    "HISTORICAL_STORE_ENABLED": "Market Data/DB",
    "FUNDAMENTALS_SOURCE": "Market Data/DB",
    "FUNDAMENTALS_REFRESH_DAYS": "Market Data/DB",
    "FUNDAMENTALS_CACHE_TTL_SECONDS": "Market Data/DB",
    "FUNDAMENTALS_NEG_CACHE_TTL_SECONDS": "Market Data/DB",
    "MACRO_REFRESH_HOURS": "Market Data/DB",
    "BARS_BACKFILL_DAYS": "Market Data/DB",
    "DATA_FETCH_TASK_TIMEOUT_SECONDS": "Market Data/DB",
    "FRED_REQUEST_TIMEOUT_SECONDS": "Market Data/DB",
    "ALPACA_REQUEST_TIMEOUT_SECONDS": "Market Data/DB",
    "PIT_CAPTURE_ENABLED": "Market Data/DB",

    # Universe / Watchlist
    "DEFAULT_TICKERS": "Universe/Watchlist",
    "WATCHLIST": "Universe/Watchlist",
    "AGENTIC_DISCOVERY_ENABLED": "Universe/Watchlist",
    "AGENTIC_MAX_CANDIDATES": "Universe/Watchlist",
    "WATCH_RULES_FILE": "Universe/Watchlist",

    # Forecasting / ML
    "META_LABELING_ENABLED": "Forecasting/ML",

    # Strategy Overlays
    "STRATEGY_WRITES_ENABLED": "Strategy Overlays",
    "CIRCUIT_BREAKER_ENABLED": "Strategy Overlays",
    "CIRCUIT_BREAKER_VOLATILITY_Z_THRESHOLD": "Strategy Overlays",
    "CIRCUIT_BREAKER_VPIN_THRESHOLD": "Strategy Overlays",
    "CIRCUIT_BREAKER_OFI_THRESHOLD": "Strategy Overlays",
    "CIRCUIT_BREAKER_LOSS_VELOCITY_WINDOW_MINS": "Strategy Overlays",
    "CIRCUIT_BREAKER_REFERENCE_SYMBOL": "Strategy Overlays",
    "USE_DUAL_MOMENTUM_OVERLAY": "Strategy Overlays",
    "SECTOR_SIMILARITY_EMBEDDER": "Strategy Overlays",
    "SECTOR_SIMILARITY_MODEL": "Strategy Overlays",
    "SECTOR_SIMILARITY_POOLING": "Strategy Overlays",

    # Sentiment / News / Attention
    "SECTOR_HEAT_ENABLED": "Sentiment/News/Attention",
    "SECTOR_HEAT_SMOOTHING_SIGMA": "Sentiment/News/Attention",
    "SECTOR_HEAT_LOOKBACK_DAYS": "Sentiment/News/Attention",

    # AI / LLM / RAG
    "RATIONALE_VERBOSITY": "AI/LLM/RAG",
    "GRAVITY_REQUIRE_NATIVE": "AI/LLM/RAG",

    # Orchestrator / Daemon / Jobs
    "GENERAL_SETTINGS_WRITES_ENABLED": "Orchestrator/Daemon/Jobs",
    "AUTOMATION_WRITES_ENABLED": "Orchestrator/Daemon/Jobs",
    "DEAD_LETTER_RETRY_ENABLED": "Orchestrator/Daemon/Jobs",
    "COMMAND_EXECUTION_ENABLED": "Orchestrator/Daemon/Jobs",
    "JOBS_API_ENABLED": "Orchestrator/Daemon/Jobs",
    "PILOTS_API_ENABLED": "Orchestrator/Daemon/Jobs",
    "PILOTS_API_PORT": "Orchestrator/Daemon/Jobs",
    "RUNTIME_FLAGS_REFRESH_ENABLED": "Orchestrator/Daemon/Jobs",
    "RUNTIME_FLAGS_REFRESH_INTERVAL_SECONDS": "Orchestrator/Daemon/Jobs",
    "ADVISORY_REUSE_PIPELINE_COMPUTE": "Orchestrator/Daemon/Jobs",
    "PIPELINE_STEP_TIMEOUT_SECONDS": "Orchestrator/Daemon/Jobs",
    "DAEMON_SHUTDOWN_TIMEOUT_SECONDS": "Orchestrator/Daemon/Jobs",
    "PROGRESS_POLL_SECONDS": "Orchestrator/Daemon/Jobs",
    "DASHBOARD_REFRESH_SECONDS": "Orchestrator/Daemon/Jobs",

    # Alerting / Observability
    "PIPELINE_STALL_ALERT_ENABLED": "Alerting/Observability",
    "PIPELINE_STALL_ALERT_SECONDS": "Alerting/Observability",
    "WS_RISK_STREAM_INTERVAL_SECONDS": "Alerting/Observability",
    "BROWSER_DIAGNOSTICS_ENABLED": "Alerting/Observability",
    "BROWSER_DIAGNOSTICS_TIMEOUT_SECONDS": "Alerting/Observability",
    "SNAPSHOT_HISTORY_DAYS": "Alerting/Observability",
    "SNAPSHOT_CONVICTION_DELTA_THRESHOLD": "Alerting/Observability",

    # Filesystem / Bootstrap
    "LOG_LEVEL": "Filesystem/Bootstrap",
    "LOCAL_DATA_ROOT": "Filesystem/Bootstrap",
    "OUTPUT_DIR": "Filesystem/Bootstrap",
    "NO_VENV_REEXEC": "Filesystem/Bootstrap",
    "GCLOUD_BIN": "Filesystem/Bootstrap",
    "CORS_ALLOWED_ORIGINS": "Filesystem/Bootstrap",
    "MCP_OAUTH_ENABLED": "Filesystem/Bootstrap",
    "MCP_OAUTH_PASSWORD": "Filesystem/Bootstrap",
    "MCP_OAUTH_ISSUER_URL": "Filesystem/Bootstrap",
    "MCP_OAUTH_MULTI_USER_ENABLED": "Filesystem/Bootstrap",
    "MCP_OAUTH_USERS": "Filesystem/Bootstrap",
    "MCP_HTTP_BEARER_TOKEN": "Filesystem/Bootstrap",
}


def classify_field(key: str) -> str:
    """Classify a Settings field name into one of the 14 functional domains."""
    if key in _OVERRIDE_DOMAINS:
        return _OVERRIDE_DOMAINS[key]

    if key.startswith("RLHF_"):
        return "RLHF"
    if key.startswith("ETF_"):
        return "ETF Transmission"

    if (
        key.startswith("OPTIONS_")
        or key.startswith("OPTION_")
        or key.startswith("0DTE")
        or "0DTE" in key
        or key.startswith("GEX_")
        or key.startswith("VPIN_")
        or key.startswith("HAR_VOL_")
        or key.startswith("LOB_")
        or key.startswith("DISPERSION_")
        or key.startswith("EARNINGS_CRUSH_")
        or key.startswith("VOL_MISPRICING_")
        or key.startswith("GAMMA_SCALPER_")
        or key.startswith("COPULA_")
        or key.startswith("DIFFUSION_")
        or key.startswith("HRP_")
        or key.startswith("MAX_OPTION_")
    ):
        return "Options Desk"

    if (
        key.startswith("ROBINHOOD_")
        or key.startswith("RH_")
        or key.startswith("ALPACA_")
        or key.startswith("BROKER_")
        or key.startswith("BROKERAGE_")
        or key.startswith("LIVE_TRADE_")
        or key.startswith("PAPER_BROKER_")
        or key.startswith("PAPER_TRADES_")
        or key.startswith("MULTI_BROKER_")
        or key.startswith("FIX_")
        or key.startswith("ORDER_")
        or key.startswith("EXECUTION_")
    ):
        return "Execution/Brokers"

    if (
        key.startswith("FORECAST_")
        or key.startswith("FORECASTING_")
        or key.startswith("PROPHET_")
        or key.startswith("CNN_LSTM_")
        or key.startswith("LSTM_")
        or key.startswith("LIGHTGBM_")
        or key.startswith("LGBM_")
        or key.startswith("MODEL_REGISTRY_")
        or key.startswith("META_LABEL_")
        or key.startswith("FEATURE_DRIFT_")
        or key.startswith("TRAIN_")
        or key.startswith("RETRAIN_")
        or key.startswith("COVARIATE_")
        or key.startswith("PSI_")
    ):
        return "Forecasting/ML"

    if (
        key.startswith("SENTIMENT_")
        or key.startswith("NEWS_")
        or key.startswith("FINBERT_")
        or key.startswith("GDELT_")
        or key.startswith("REDDIT_")
        or key.startswith("EDGAR_")
        or key.startswith("GOOGLE_NEWS_")
        or key.startswith("GOOGLE_TRENDS_")
        or key.startswith("TRENDS_")
        or key.startswith("PYTRENDS_")
        or key.startswith("WIKIPEDIA_")
        or key.startswith("ATTENTION_")
        or key.startswith("ASVI_")
        or key.startswith("STOCKTWITS_")
        or key.startswith("BERT_LLA_")
    ):
        return "Sentiment/News/Attention"

    if (
        key.startswith("AI_")
        or key.startswith("LLM_")
        or key.startswith("OPAL_")
        or key.startswith("GEMINI_")
        or key.startswith("CLAUDE_")
        or key.startswith("OPENAI_")
        or key.startswith("LOCAL_LLM_")
        or key.startswith("ANTHROPIC_")
        or key.startswith("RAG_")
        or key.startswith("QDRANT_")
        or key.startswith("FAISS_")
        or key.startswith("PROMPT_")
        or key.startswith("JULES_")
        or key.startswith("GRAVITY_AI_")
    ):
        return "AI/LLM/RAG"

    if (
        key.startswith("FMP_")
        or key.startswith("MARKET_DATA_")
        or key.startswith("HISTORICAL_STORE_")
        or key.startswith("FRED_")
        or key.startswith("YAHOO_")
        or key.startswith("FINNHUB_")
        or key.startswith("DATA_")
        or key.startswith("DATABASE_")
        or key.startswith("DB_")
        or key.startswith("BARS_")
    ):
        return "Market Data/DB"

    if (
        key.startswith("WATCHLIST")
        or key.startswith("UNIVERSE_")
        or key.startswith("SYNC_WATCHLIST_")
        or key.startswith("CLOSED_POSITION_")
        or key.startswith("SYMBOL_RATING_")
        or key.startswith("DISCOVERY_")
        or key.startswith("TICKER_")
        or key.startswith("DEFAULT_TICKERS")
        or key.startswith("ACTIVE_UNIVERSE_")
        or key.startswith("SCREENER_")
    ):
        return "Universe/Watchlist"

    if (
        key.startswith("ORCHESTRATOR_")
        or key.startswith("DAEMON_")
        or key.startswith("PIPELINE_")
        or key.startswith("JOB_")
        or key.startswith("JOBS_")
        or key.startswith("COMMAND_")
        or key.startswith("DEAD_LETTER_")
    ):
        return "Orchestrator/Daemon/Jobs"

    if (
        key.startswith("ALERT_")
        or key.startswith("NTFY_")
        or key.startswith("EMAIL_")
        or key.startswith("DISCORD_")
        or key.startswith("SLACK_")
        or key.startswith("WEBHOOK_")
        or key.startswith("SENTRY_")
        or key.startswith("STATE_API_")
        or key.startswith("OBSERVABILITY_")
    ):
        return "Alerting/Observability"

    if (
        key.startswith("CACHE_LONG_SHORT_")
        or key.startswith("SECTOR_SELECTION_")
        or key.startswith("SECTOR_FORECAST_")
        or key.startswith("CORRELATION_CLUSTER_")
        or key.startswith("PAIRS_")
        or key.startswith("DUAL_MOMENTUM_")
        or key.startswith("SIGNAL_")
        or key.startswith("DISABLED_SIGNAL_")
        or key.startswith("REGIME_")
        or key.startswith("HMM_")
        or key.startswith("CIRCUIT_BREAKER_")
        or key.startswith("VALIDATION_")
        or key.startswith("STRATEGY_")
    ):
        return "Strategy Overlays"

    if (
        key.startswith("KELLY_")
        or key.startswith("VOL_TARGET")
        or key.startswith("MAX_")
        or key.startswith("SIZING_")
        or key.startswith("RISK_")
        or key.startswith("MARKET_")
        or key.startswith("REQUIRED_")
        or key.startswith("PORTFOLIO_")
        or key.startswith("COST_MODEL_")
    ):
        return "Financial/Risk/Sizing"

    return "Filesystem/Bootstrap"


# Static dictionary mapping all Settings fields to their domain name
KEY_DOMAIN: Dict[str, str] = {
    key: classify_field(key) for key in Settings.model_fields
}
