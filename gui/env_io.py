"""
gui/env_io.py
=============
Safe, allowlist-bounded read/write layer for the project ``.env`` file, used by
the Command Center's **Settings Manager** and **Strategy Matrix** tabs.

Why a dedicated module
----------------------
The GUI lets an operator tune non-secret runtime parameters (risk-free rate,
Kelly fraction, default tickers, signal weights, disabled modules, …) without
hand-editing ``.env``.  Doing this safely requires three guarantees that this
module centralizes and enforces:

1.  **Secrets are never written and never echoed in cleartext.**  Keys in
    :data:`SECRET_KEYS` (API keys, passwords, TOTP secrets, webhooks) are
    read-only from the GUI's perspective: :func:`read_settings` returns a masked
    placeholder (``"•••• set"`` / ``"(unset)"``) for them, and
    :func:`write_setting` raises :class:`SecretWriteError` if asked to modify one
    (CONSTRAINT #3).

2.  **Only known tunables are writable.**  :func:`write_setting` rejects any key
    not in :data:`ALLOWED_KEYS`, so a GUI bug or a crafted form value cannot
    inject arbitrary keys into ``.env``.

3.  **Values are serialized exactly as pydantic-settings expects.**  List/dict
    fields (``DEFAULT_TICKERS``, ``SIGNAL_WEIGHTS``, ``DISABLED_SIGNAL_MODULES``)
    are JSON-encoded so ``settings.Settings()`` re-parses them on the next
    launch; scalars are written verbatim.

The module uses ``python-dotenv`` (already a dependency) — ``dotenv_values`` for
reading and ``set_key`` for writing — so existing comments and unrelated keys in
``.env`` are preserved across edits.

Classification completeness
----------------------------
Every field on ``settings.Settings`` must appear in exactly one of
:data:`ALLOWED_KEYS`, :data:`SECRET_KEYS`, or :data:`EXCLUDED_FROM_GUI` (the
last for filesystem paths and fail-closed command flags that are deliberately
neither — see that set's own docstring). ``tests/test_gui_env_io.py::
test_every_settings_field_is_classified`` enforces this so a batch of new
``Settings`` fields can never again ship without a corresponding allowlist
decision (the gap this module accumulated before the 2026-08 audit).

Persistence model
------------------
Writes land in ``.env`` and therefore take effect on the **next** orchestrator /
GUI launch (``Settings()`` reads ``.env`` once at process start; there is no
hot-reload).  The Settings tab makes this explicit to the operator.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from dotenv import dotenv_values, set_key

logger = logging.getLogger(__name__)

# Repo root = parent of the gui/ package directory.
_REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = _REPO_ROOT / ".env"

# ---------------------------------------------------------------------------
# Key classification
# ---------------------------------------------------------------------------
# NON-secret tunables the GUI may write. Each maps to a pydantic Settings field.
# Keep this list aligned with settings.py; anything not here is rejected.
ALLOWED_KEYS: tuple[str, ...] = (
    # Financial constants
    "RISK_FREE_RATE",
    "MARKET_RISK_PREMIUM",
    "REQUIRED_RETURN_RATE",
    "MAX_PORTFOLIO_HEAT",
    # Position sizing
    "KELLY_FRACTION",
    "KELLY_CAP",
    "VOL_TARGET",
    "MAX_LEVERAGE",
    "MAX_POSITION_WEIGHT",
    # Portfolio-level gross exposure cap + cap-aware escalation + cap-event
    # audit/alerting (sizing/position_sizer.py, sizing/cap_audit_store.py)
    "MAX_PORTFOLIO_GROSS",
    "SIZING_CAP_ESCALATION_ENABLED",
    "SIZING_CAP_ESCALATION_THRESHOLD_CYCLES",
    "SIZING_CAP_ESCALATION_FACTOR",
    "SIZING_CAP_AUDIT_ENABLED",
    "SIZING_CAP_ALERT_ENABLED",
    "SIZING_CAP_ALERT_THRESHOLD_PCT",
    # Risk gate
    "MAX_CORRELATION",
    "DAILY_LOSS_LIMIT_PCT",
    "MAX_ORDER_RATE_PER_MIN",
    "EXECUTION_PRIORITY_QUEUE_ENABLED",
    "EXECUTION_QUEUE_LEAK_RATE_PER_SEC",
    "EXCURSION_INTRADAY_ENABLED",
    "HMM_RISK_OFF_BLOCK_THRESHOLD",
    "RISK_GATE_ENFORCE_MARKET_HOURS",
    "MACRO_REGIME_GATE_ENABLED",
    # Meta-labeling
    "META_LABEL_MIN_CONFIDENCE",
    "FORECAST_BACKFILL_HORIZONS",
    "FORECAST_BACKFILL_LOOKBACK_YEARS",
    "FORECAST_BACKFILL_MOMENTUM_WINDOW",
    "FORECAST_BACKFILL_VOL_SHORT_WINDOW",
    "FORECAST_BACKFILL_VOL_LONG_WINDOW",
    "FORECAST_BACKFILL_RSI_WINDOW",
    "FORECAST_BACKFILL_MACD_FAST",
    "FORECAST_BACKFILL_MACD_SLOW",
    "FORECAST_BACKFILL_VOL_RATIO_WINDOW",
    "FORECAST_BACKFILL_TRAIN_SPLIT",
    "FORECAST_BACKFILL_N_ESTIMATORS",
    "FORECAST_BACKFILL_MAX_DEPTH",
    "FORECAST_BACKFILL_RANDOM_STATE",
    "FORECAST_BACKFILL_CLASSIFIER_TYPE",
    # Observability / runtime
    "DASHBOARD_REFRESH_SECONDS",
    # Poll interval (seconds) for the Launcher tab's live pipeline-progress bar
    # (see reporting/progress.py + gui/orchestrator_runner.py::compute_run_progress).
    # Non-secret; scalar (not JSON-encoded).
    "PROGRESS_POLL_SECONDS",
    "LOG_LEVEL",
    "DRY_RUN",
    # Persistent orchestrator daemon cutover flag. Non-secret (no credential
    # material); the command token that actually guards the daemon's
    # POST /run is ORCHESTRATOR_DAEMON_TOKEN, which stays in SECRET_KEYS.
    "ORCHESTRATOR_DAEMON_ENABLED",
    # Total seconds budgeted for the daemon's graceful teardown (see
    # settings.py's own field docstring for the full shutdown-budget ladder).
    # Non-secret; a GUI bug here can only make shutdown less graceful, never
    # leak a credential or enable a dangerous action -- unlike
    # AUTOMATION_WRITES_ENABLED, which is
    # deliberately excluded from this allowlist for that reason.
    "DAEMON_SHUTDOWN_TIMEOUT_SECONDS",
    # The daemon's internal timer cadence. Writable via the Pilots API's
    # PUT /automation/schedule/interval (api/pilots_api.py) and the GUI. A
    # write here takes effect on the daemon's NEXT restart, not immediately
    # (no live setter exists yet — see the Data & Automation plan's deferred
    # Phase 4); the API's response makes that explicit via its own
    # `applies: "next_daemon_restart"` field rather than implying a live change.
    "ORCHESTRATOR_INTERVAL_SECONDS",
    # Hosts api/pilots_api.py inside the orchestrator daemon process on
    # PILOTS_API_PORT. Non-secret; the follow write-path's command token
    # (FOLLOW_API_TOKEN) stays in SECRET_KEYS.
    "PILOTS_API_ENABLED",
    "PILOTS_API_PORT",
    "JOBS_API_ENABLED",
    # Persisted Pilots-PWA analytics artifacts (options premium matrix + pairs
    # radar). When on, the pipeline writes output/options_matrix.json /
    # output/pairs.json for the AST-guarded Pilots API to read. Non-secret.
    "OPTIONS_MATRIX_ENABLED",
    "PAIRS_SNAPSHOT_ENABLED",
    "PAIRS_SNAPSHOT_MAX_PAIRS",
    # Execution mode toggle — paper sandbox vs. live endpoint. Writeable from
    # the Strategy Matrix tab's global Simulation/Paper/Live selector. Never a
    # secret: the broker keys themselves are SECRET_KEYS.
    "ALPACA_PAPER",
    "MARKET_DATA_PROVIDER",
    "MARKET_DATA_QUOTE_TTL_SECONDS",
    "MARKET_DATA_BARS_TTL_SECONDS",
    # Opt-in real-time WS quote ingestion (data/market_data_ws.py). Non-secret
    # tunables only; Alpaca credentials stay in SECRET_KEYS.
    "MARKET_DATA_WS_ENABLED",
    "MARKET_DATA_WS_STALE_SECONDS",
    "MARKET_DATA_WS_SYMBOLS",
    # Forecasting / fundamentals tunables (non-secret; see forecasting_engine.py
    # + data/market_data.py). FINNHUB_API_KEY stays in SECRET_KEYS below.
    "FORECAST_USE_GARCH_SIGMA",   # bool — GJR-GARCH sigma into Monte Carlo (rollback lever)
    "FORECAST_PROPHET_WEIGHT",    # float [0,1] — Prophet ensemble overlay weight
    "FORECAST_SKILL_WEIGHTING_ENABLED",  # bool — opt-in inverse-RMSE skill-weighted blend
    "FORECAST_SKILL_WINDOW_DAYS", # int — rolling RMSE window (days) for skill weighting
    "FORECAST_MODEL_PERSISTENCE_ENABLED",  # bool — opt-in CNN-LSTM/Prophet artifact persistence
    "FORECAST_MODEL_RETRAIN_DAYS",         # int — persisted-model staleness window (days)
    # CNN-LSTM/TensorFlow deadlock fix (issue #381, docs/known_issues/
    # cnn_lstm_tf_deadlock.md). Non-secret ops tunables, no credential material.
    "CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED",  # bool — run CNN-LSTM fit/predict in an isolated subprocess
    "CNN_LSTM_PROCESS_POOL_WORKERS",          # int — persistent isolation-pool worker count
    "CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS",    # int — per-call isolation timeout (seconds)
    "ADVISORY_REUSE_PIPELINE_COMPUTE",     # bool — OUTPUT-CHANGING: reuse pipeline GARCH/forecast in advisory overlay
    "FUNDAMENTALS_SOURCE",        # "yahoo" | "yfinance_info"
    "BETA_LOOKBACK_DAYS",         # int — beta computation lookback (days)
    "ADVISORY_ONLY",              # bool — execution quarantine
    # Universe / signals (JSON-encoded)
    "DEFAULT_TICKERS",
    "SIGNAL_WEIGHTS",
    "DISABLED_SIGNAL_MODULES",
    # Sector->model/horizon forecast config (JSON-encoded; see _JSON_KEYS).
    # GUI-writable. Empty dict/default path preserves today's hardcoded
    # per-sector forecast heuristic (backward-compatible).
    "SECTOR_FORECAST_CONFIG_PATH",
    "SECTOR_FORECAST_CONFIGS",
    # State API CORS policy — non-secret list of allowed browser origins
    # (JSON-encoded; see _JSON_KEYS). GUI-writable.
    "CORS_ALLOWED_ORIGINS",
    # Prompt Registry tunables (non-secret; credentials live in SECRET_KEYS below).
    # See docs/PROMPT_REGISTRY_PLAN.md §8 and settings.PROMPT_REGISTRY_*.
    "PROMPT_REGISTRY_ENABLED",   # bool master switch (baseline-only when False)
    "PROMPT_REGISTRY_BACKEND",   # "http" | "local" | "firestore"
    "PROMPT_REGISTRY_PINS",      # JSON dict {"prompt_id": "version"} — rollback lever
    # Tier 9 — Claude + Gemini commentary toggles (non-secret).  Credentials
    # (ANTHROPIC_API_KEY / GEMINI_API_KEY) live in SECRET_KEYS below per
    # CONSTRAINT #3 — they are NEVER GUI-writable.
    "LLM_COMMENTARY_ENABLED",            # bool master switch (default False)
    "LLM_COMMENTARY_RATIONALE_PROVIDER", # "claude" | "none"
    "LLM_COMMENTARY_ALERT_PROVIDER",     # "gemini" | "none"
    # Age bound (hours) for TRANSIENT last-call verdicts in llm/status_store.py
    # (non-secret scalar; auth/ok verdicts are fingerprint-bound, not age-bound).
    "LLM_STATUS_MAX_AGE_HOURS",
    # AI Control Center toggles (non-secret).  These master switches were
    # previously settable only by hand-editing .env; the Control Center tab
    # surfaces them.  Provider credentials (ANTHROPIC/GEMINI/OPENAI keys) stay
    # in SECRET_KEYS below — CONSTRAINT #3, never GUI-writable.
    "GRAVITY_AI_RUNNER_ENABLED",         # bool — Gravity AI runner (Claude+Gemini)
    "OPAL_RESEARCH_ENABLED",             # bool — Opal research agent (OpenAI or Gemini)
    "OPAL_RESEARCH_PROVIDER",            # "openai" | "gemini" | "none"
    "OPAL_RESEARCH_MODEL",               # e.g. "gpt-4o" or "gemini-2.5-flash"
    # AI-Assisted Credibility Filtering (Sentiment Pipeline Phase 2 PR2,
    # signals/credibility.py). Non-secret; provider credentials (ANTHROPIC/
    # GEMINI/OPENAI keys) stay in SECRET_KEYS below — CONSTRAINT #3.
    "SENTIMENT_LLM_VERIFICATION_ENABLED",            # bool master switch (default False)
    "SENTIMENT_LLM_VERIFICATION_PROVIDER",           # "claude" | "gemini" | "openai" | "none"
    "SENTIMENT_LLM_VERIFICATION_MAX_CALLS_PER_CYCLE",  # int — per-batch LLM call budget
    # RAG-Powered Portfolio Contextualizer (Phase 2 PR3; non-secret). Provider
    # credentials (ANTHROPIC/GEMINI/OPENAI keys) stay in SECRET_KEYS below —
    # CONSTRAINT #3, never GUI-writable.
    "RAG_PORTFOLIO_CONTEXT_ENABLED",      # bool — master switch
    "RAG_PORTFOLIO_CONTEXT_PROVIDER",     # "claude" | "gemini" | "none"
    "RAG_EMBEDDING_PROVIDER",             # "openai" | "gemini"
    "RAG_INDEX_MAX_DOCUMENTS",            # int — FAISS index FIFO eviction cap
    "RAG_RETRIEVAL_TOP_K",                # int — nearest-neighbor count per query
    "RAG_INDEX_LOOKBACK_DAYS",            # int — indexing scan window (days)
    # ETF volatility-transmission risk overlay (Ben-David, Franzoni & Moussawi
    # 2018, Journal of Finance 73(6)) — holdings ingestion (data/etf_holdings.py),
    # market-residualized measurement columns + portfolio covariance inflation
    # (risk/etf_transmission.py), and the per-name sizing derate
    # (sizing/position_sizer.py). All 19 keys below are non-secret: SEC N-PORT
    # and the optional iShares CSV endpoint are both unauthenticated, so there
    # is no credential material anywhere in this family. Every default
    # reproduces today's exact no-op behavior (master switches default False;
    # numeric knobs are only ever consulted once their master switch is True).
    "ETF_HOLDINGS_ENABLED",
    "ETF_HOLDINGS_TICKERS",
    "ETF_HOLDINGS_REFRESH_DAYS",
    "ETF_HOLDINGS_ISSUER_CSV_ENABLED",
    "ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE",
    "ETF_HOLDINGS_CIRCUIT_BREAKER_THRESHOLD",
    "ETF_TRANSMISSION_ENABLED",
    "ETF_HOLDINGS_MARKET_PROXY",
    "ETF_TRANSMISSION_WRAPPERS",
    "ETF_TRANSMISSION_EXCLUDED_SYMBOLS",
    "ETF_TRANSMISSION_WINDOW_DAYS",
    "ETF_TRANSMISSION_MIN_OBS",
    "ETF_TRANSMISSION_SIZING_ENABLED",
    "ETF_TRANSMISSION_MAX_DERATE",
    "ETF_TRANSMISSION_OWNERSHIP_REFERENCE",
    "ETF_TRANSMISSION_MIN_MULTIPLIER",
    "ETF_TRANSMISSION_PORTFOLIO_ENABLED",
    "ETF_TRANSMISSION_COV_INFLATION",
    "ETF_TRANSMISSION_COV_WINDOW_DAYS",
    # Financial Modeling Prep (data/fmp_client.py + its consumers). All 24 keys
    # below are non-secret operational tunables; the credential itself
    # (FMP_API_KEY) is in SECRET_KEYS — CONSTRAINT #3, never GUI-writable.
    # Every default reproduces today's exact behavior: the eight feed master
    # switches default False, and the client-tuning knobs are only ever
    # consulted once a request is actually being made. FMP_ECON_INDICATORS is a
    # comma-separated STRING (the SENTIMENT_SOURCES convention), NOT a
    # _JSON_KEY — do not add it there.
    "FMP_BASE_URL",                       # str  — API base (the '/stable' family)
    "FMP_TIMEOUT_SECONDS",                # float — per-request HTTP timeout
    "FMP_MIN_REQUEST_INTERVAL_SECONDS",   # float — issuance spacing (0.25 = 240/min)
    "FMP_MAX_RETRIES",                    # int  — retries on 429/5xx only
    "FMP_RETRY_BACKOFF_SECONDS",          # float — exponential-backoff base
    "FMP_COOLDOWN_THRESHOLD",             # int  — consecutive failures that open the breaker
    "FMP_COOLDOWN_SECONDS",               # float — how long the breaker stays open
    "FMP_QUOTES_ENABLED",                 # bool — also needs MARKET_DATA_PROVIDER=fmp
    "FMP_BARS_ENABLED",                   # bool — also needs MARKET_DATA_PROVIDER=fmp
    "FMP_FUNDAMENTALS_ENABLED",           # bool — also needs FUNDAMENTALS_SOURCE=fmp
    "FMP_ANALYST_ENABLED",                # bool — diagnostic analyst columns
    "FMP_EARNINGS_ENABLED",               # bool — Earnings_Date second source + surprises
    "FMP_MACRO_ENABLED",                  # bool — treasury/econ into macro_history
    "FMP_INSIDER_ENABLED",                # bool — per-symbol insider statistics
    "FMP_SECTOR_SNAPSHOT_ENABLED",        # bool — 2 dated sector snapshots per cycle
    "FMP_FALLBACK_ENABLED",               # bool — fall through to Alpaca/yfinance/Yahoo
    "FMP_QUOTES_REALTIME",                # bool — label FMP quotes real-time (unverified on Starter)
    "FMP_BARS_ADJUSTMENT",                # str  — EOD variant; 'dividend-adjusted' matches yfinance
    "FMP_ANALYST_REFRESH_HOURS",          # int  — analyst cadence gate
    "FMP_EARNINGS_REFRESH_HOURS",         # int  — earnings cadence gate
    "FMP_INSIDER_REFRESH_DAYS",           # int  — insider cadence gate
    "FMP_INSIDER_MIN_LAG_DAYS",           # int  — quarter-close lag before an aggregate is read
    "FMP_ECON_INDICATORS",                # str  — comma-separated series names (not JSON)
    "FMP_MAX_SECONDS_PER_CYCLE",          # float — per-cycle wall-clock budget
    # Robinhood execution bridge & portfolio controls
    "ROBINHOOD_EXECUTION_MODE",
    "ROBINHOOD_MAX_NOTIONAL_PER_ORDER",
    "ROBINHOOD_LIMIT_BUFFER_BPS",
    "ROBINHOOD_AUTO_REFRESH_ENABLED",
    # GUI-writable by operator decision (previously excluded as a "hand-set
    # only" master switch -- see settings.py's own BROKERAGE_CONNECT_ENABLED
    # field docstring). The brokerage-credential connect/disconnect endpoints
    # remain gated by two further independent checks regardless of this
    # flag's own writability: FOLLOW_API_TOKEN and a loopback-only request
    # check (api/pilots_api.py::require_brokerage_connect_enabled).
    "BROKERAGE_CONNECT_ENABLED",
    # Concurrency limits
    "ADVISORY_MAX_CONCURRENCY",
    "FORECAST_MAX_CONCURRENCY",
    "DATA_FETCH_MAX_CONCURRENCY",
    # Historical persistence & DB controls
    "HISTORICAL_STORE_ENABLED",
    "BARS_BACKFILL_DAYS",
    "FUNDAMENTALS_REFRESH_DAYS",
    "MACRO_REFRESH_HOURS",
    "PIT_CAPTURE_ENABLED",
    "SNAPSHOT_HISTORY_DAYS",
    "SNAPSHOT_CONVICTION_DELTA_THRESHOLD",
    # GUI-writable by operator decision (previously excluded from this
    # allowlist as "hand-set only" master switches -- see settings.py's own
    # UNIVERSE_SYNC_ENABLED/AGENTIC_DISCOVERY_ENABLED field docstrings for the
    # endpoint-level safeguards that remain independent of this flag:
    # STATE_API_TOKEN / FOLLOW_API_TOKEN command-token guards respectively).
    "UNIVERSE_SYNC_ENABLED",
    "AGENTIC_DISCOVERY_ENABLED",
    "NEWS_HISTORY_CAPTURE_ENABLED",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    # Multi-source sentiment & attention pipeline (webapp /settings/sentiment,
    # api/pilots_api.py's _SENTIMENT_GROUPS). Every key here is a REAL
    # settings.py Field verified against Settings.model_fields — see that
    # module's _SENTIMENT_GROUPS comment for why this matters (extra="ignore"
    # means a fabricated key would silently do nothing on write).
    # SENTIMENT_LLM_VERIFICATION_ENABLED/_PROVIDER/_MAX_CALLS_PER_CYCLE are
    # already listed above (pre-dating this block) and also served by this
    # editor — not repeated here.
    "SENTIMENT_INGESTION_ENABLED",
    "SENTIMENT_SOURCES",
    "SENTIMENT_COMMENT_SOURCES",
    "SENTIMENT_MAX_DOCUMENTS_PER_CYCLE",
    "SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE",
    "SENTIMENT_INGESTION_LOOKBACK_DAYS",
    "SENTIMENT_CIRCUIT_BREAKER_THRESHOLD",
    "SENTIMENT_PIT_MIN_MONTHS",
    "SENTIMENT_AUDIT_ENABLED",
    "SENTIMENT_DESENTENCIZE_ENABLED",
    "SENTIMENT_INDEX_ENABLED",
    "SENTIMENT_SOCIAL_BLEND_WEIGHT",
    # Heuristic credibility-composite band that qualifies a document for LLM
    # verification (SENTIMENT_LLM_VERIFICATION_ENABLED's own gate). Plain
    # float thresholds, no credential material -- siblings of the already-
    # allowlisted SENTIMENT_LLM_VERIFICATION_ENABLED/_PROVIDER/_MAX_CALLS_PER_CYCLE.
    "SENTIMENT_LLM_VERIFICATION_BORDERLINE_LOW",
    "SENTIMENT_LLM_VERIFICATION_BORDERLINE_HIGH",
    "STOCKTWITS_ENABLED",
    "REDDIT_BACKFILL_MAX_PAGES",
    "EDGAR_MAX_CONCURRENCY",
    "GDELT_MIN_REQUEST_INTERVAL_SECONDS",
    "GDELT_MAX_RETRIES",
    "GDELT_RETRY_BACKOFF_SECONDS",
    "GDELT_COOLDOWN_THRESHOLD",
    "GDELT_COOLDOWN_SECONDS",
    "NEWS_LOOKBACK_DAYS",
    "FINBERT_ENABLED",
    "FINBERT_BATCH_SIZE",
    "FINBERT_SCORE_CACHE_ENABLED",
    "FINNHUB_RATE_LIMIT_PER_MIN",
    "NEWS_EARNINGS_SUPPRESS_HOURS",
    "NEWS_EARNINGS_DAMPEN_DAYS",
    "GOOGLE_NEWS_LOOKBACK_WINDOW",
    "EDGAR_FULLTEXT_ENABLED",
    "EDGAR_FULLTEXT_FORMS",
    "EDGAR_FULLTEXT_CHUNK_TOKENS",
    "SECTOR_HEAT_ENABLED",
    "SECTOR_HEAT_SMOOTHING_SIGMA",
    "SECTOR_HEAT_LOOKBACK_DAYS",
    "WIKIPEDIA_ATTENTION_ENABLED",
    "WIKIPEDIA_ATTENTION_LOOKBACK_DAYS",
    "PYTRENDS_ENABLED",
    "ATTENTION_INGESTION_MAX_SECONDS_PER_CYCLE",
    "ATTENTION_CIRCUIT_BREAKER_THRESHOLD",
    # Related Sector Selection tunables (webapp /settings/sector-selection,
    # api/pilots_api.py's _SECTOR_SELECTION_GROUPS) — data/sector_selection_heat.py's
    # semantic-similarity feature backing the SectorSelection.tsx screen.
    "SECTOR_SELECTION_ENABLED",
    "SECTOR_SELECTION_TOP_N",
    "SECTOR_SELECTION_W1",
    "SECTOR_SELECTION_W2",
    "SECTOR_SELECTION_HEAT_LOOKBACK_DAYS",
    "SECTOR_SELECTION_HEAT_A",
    "SECTOR_SELECTION_HEAT_B",
    "SECTOR_SELECTION_HEAT_C",
    "SECTOR_SIMILARITY_EMBEDDER",
    "SECTOR_SIMILARITY_MODEL",
    "SECTOR_SIMILARITY_POOLING",
    # BERT-LLA Neural Forecaster
    "BERT_LLA_ENABLED",
    "BERT_LLA_BLEND_ENABLED",
    "BERT_LLA_ABLATION_ENABLED",
    "BERT_LLA_WINDOW_SIZE",
    "BERT_LLA_MIN_SENTIMENT_COVERAGE",
    "FORECAST_CNN_LSTM_WALKFORWARD_SCALING",
    # Additional Observability & Dual Momentum controls
    "NTFY_DASHBOARD_URL",
    "RATIONALE_VERBOSITY",
    "ALERT_DEDUP_WINDOW_SECONDS",
    "USE_DUAL_MOMENTUM_OVERLAY",
    "DUAL_MOMENTUM_SAFE_ASSET",
    "DUAL_MOMENTUM_RISKY_ASSETS",
    "FLATTEN_ON_KILL",
    # --- 2026-08 allowlist audit residual (post PR #560 merge) ----------------
    # PR #560 independently classified most of the settings-parity audit's
    # original 138-field gap; everything below is what was STILL unclassified
    # after that merge (enforced by
    # tests/test_gui_env_io.py::test_every_settings_field_is_classified).
    "AGENTIC_MAX_CANDIDATES",
    "ALERT_CHANNELS",
    "ALERT_SMTP_PORT",
    "ALERT_EMAIL_SMTP_PORT",
    "ALPACA_KEY_ROTATED_DATE",
    "FRED_KEY_ROTATED_DATE",
    "PAPER_TRADING_START_DATE",
    "CORRELATION_CLUSTER_LOOKBACK_DAYS",
    "CORRELATION_CLUSTER_THRESHOLD",
    "DATA_FRESHNESS_TTL_SECONDS",
    "FINNHUB_RATE_LIMIT_PER_MIN",
    "FOLLOW_MIN_AMOUNT",
    "FORECAST_SKILL_MIN_OBS",
    "FUNDAMENTALS_CACHE_TTL_SECONDS",
    "FUNDAMENTALS_NEG_CACHE_TTL_SECONDS",
    "HMM_N_STATES",
    "HMM_RETRAIN_FREQ_DAYS",
    "LLM_COMMENTARY_TIMEOUT_SECONDS",
    "MARKET_DATA_WS_RECONNECT_BASE_SECONDS",
    "MARKET_DATA_WS_RECONNECT_MAX_SECONDS",
    "META_LABELING_ENABLED",
    "MULTIFACTOR_MICROCAP_THRESHOLD",
    "OPAL_RESEARCH_TIMEOUT_SECONDS",
    "OPTIONS_TRUE_IVR_ENABLED",
    "ORCHESTRATOR_API_PORT",
    "PILOTS_TOP_N",
    "PROMPT_CACHE_KEEP_VERSIONS",
    "PROMPT_MAX_CHARS",
    "PROMPT_REGISTRY_REFRESH_SECONDS",
    "QUEUE_SOURCE_MAX_AGE_SECONDS",
    # Per-regime signal weight overrides merged onto SIGNAL_WEIGHTS (JSON dict;
    # see _JSON_KEYS).
    "REGIME_SIGNAL_WEIGHTS",
    # Related Sector Selection (data/sector_selection_heat.py) -- semantic
    # similarity + Gaussian-response Sector Heat term. Not covered by PR #560.
    "SECTOR_SELECTION_ENABLED",
    "SECTOR_SELECTION_TOP_N",
    "SECTOR_SELECTION_HEAT_A",
    "SECTOR_SELECTION_HEAT_B",
    "SECTOR_SELECTION_HEAT_C",
    "SECTOR_SELECTION_HEAT_LOOKBACK_DAYS",
    "SECTOR_SELECTION_W1",
    "SECTOR_SELECTION_W2",
    "SECTOR_SIMILARITY_EMBEDDER",
    "SECTOR_SIMILARITY_MODEL",
    "SECTOR_SIMILARITY_POOLING",
    "VALIDATION_HARNESS_OOS_GATE_ENABLED",
)

# Keys whose VALUES must never be returned in cleartext nor written by the GUI.
# These are credentials / webhooks; they remain editable only by hand-editing
# .env outside the app (CONSTRAINT #3).
SECRET_KEYS: tuple[str, ...] = (
    "FRED_API_KEY",
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "FINNHUB_API_KEY",
    "ROBINHOOD_USERNAME",
    "ROBINHOOD_PASSWORD",
    "RH_USERNAME",
    "RH_PASSWORD",
    "RH_MFA_SECRET",
    # Postgres/Supabase DSN — may embed user:pass@host; never logged, never
    # returned in cleartext by the GUI (CONSTRAINT #3).
    "DATABASE_URL",
    # Optional dedicated read-only Postgres DSN (a restricted ROLE with no write
    # grants) for db_config.create_readonly_db_engine(). Same treatment as
    # DATABASE_URL — may embed credentials, never logged, never GUI-writable.
    "MCP_DATABASE_URL_RO",
    "ALERT_WEBHOOK_URL",
    # Bearer token for the read-only State API (api/state_api.py). Treated like a
    # webhook/token secret — masked, never GUI-writable (CONSTRAINT #3).
    "STATE_API_TOKEN",
    # Bearer token guarding POST /run on the orchestrator Control API
    # (api/control_api.py). Same secret treatment as STATE_API_TOKEN.
    "ORCHESTRATOR_DAEMON_TOKEN",
    # Bearer token guarding the follow WRITE-path on the Pilots API (PUT
    # /follows, POST /pilots/{id}/follow) and reused as the fail-closed
    # command-token gate for every *_WRITES_ENABLED/*_ENABLED master switch
    # above (BROKERAGE_CONNECT_ENABLED, AGENTIC_DISCOVERY_ENABLED, etc.). Its
    # own settings.py Field docstring already states "SECRET -- never
    # GUI-writable... Like ORCHESTRATOR_DAEMON_TOKEN" -- this was a pre-existing
    # gap (present in neither list, so read_settings() would have echoed it in
    # cleartext if ever set in .env) rather than a deliberate omission.
    "FOLLOW_API_TOKEN",
    "DISCORD_WEBHOOK_URL",
    "SLACK_WEBHOOK_URL",
    # ntfy.sh push topic (alerting.notify(), also used by the Tier 8 Robinhood
    # execution-queue notifier in execution/queue_builder.py). Functions like a
    # bearer token: anyone who knows the topic name can publish to or read it —
    # alerting.py's own docstring says to "keep the topic unguessable" — so it
    # is classified alongside the webhook URLs, never GUI-writable.
    "NTFY_TOPIC",
    "ALERT_EMAIL_FROM",
    "ALERT_EMAIL_TO",
    "ALERT_SMTP_HOST",
    "ALERT_SMTP_USER",
    "ALERT_SMTP_PASSWORD",
    # alerting_mcp/notifier.py's own SMTP credential — distinct from
    # ALERT_SMTP_PASSWORD above (observability/alerts.py's channel).
    "ALERT_EMAIL_SMTP_PASSWORD",
    # Prompt Registry credentials — 4 separate roles (read / publish / sign / url).
    # Never GUI-writable; edit .env by hand only (CONSTRAINT #3).
    "PROMPT_REGISTRY_URL",           # protected HTTPS manifest endpoint
    "PROMPT_REGISTRY_TOKEN",         # bearer read-token
    "PROMPT_REGISTRY_PUBLISH_TOKEN", # higher-privilege publish credential
    "PROMPT_REGISTRY_SIGNING_KEY",   # HMAC-SHA256 verification key
    "PROMPT_REGISTRY_CREDENTIALS",   # Firestore service-account JSON blob
    # Tier 9 — Claude + Gemini commentary credentials.  CONSTRAINT #3 — these
    # are NEVER GUI-writable; hand-edit .env to set / rotate them.
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    # OpenAI credential for Opal, the research agent (Tier 9 Scope 4,
    # llm/research.py).  CONSTRAINT #3 — never GUI-writable; hand-edit .env.
    "OPENAI_API_KEY",
    # data/sentiment_sources.py's RedditSource OAuth2 script-app credentials
    # (Sentiment Pipeline Phase 3). CONSTRAINT #3 — never GUI-writable.
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    # SEC EDGAR requires this to identify the requester per its fair-access
    # policy — not a credential in the auth sense, but a per-operator value
    # that shouldn't be GUI-editable any more than the other source configs
    # above; classified here rather than ALLOWED_KEYS for the same reason.
    "EDGAR_USER_AGENT",
    # Financial Modeling Prep credential (data/fmp_client.py). CONSTRAINT #3 —
    # masked in the GUI, never GUI-writable; hand-edit .env to set/rotate it.
    # Its 24 non-secret operational tunables live in ALLOWED_KEYS above.
    "FMP_API_KEY",
    # --- 2026-08 allowlist audit residual (post PR #560 merge) ----------------
    # alerting_mcp/notifier.py family (distinct from observability/alerts.py's
    # ALERT_SMTP_HOST/ALERT_WEBHOOK_URL/NTFY_TOPIC, all already secret above) —
    # mirrors those exact siblings: a webhook/topic/hostname in this alerting
    # channel is treated as sensitive alongside the credential fields it's
    # grouped with in settings.py, even though a hostname/topic alone isn't a
    # credential in the strict sense (same judgment call already made for
    # ALERT_SMTP_HOST and NTFY_TOPIC). ALERT_EMAIL_SMTP_PORT/ALERT_SMTP_PORT stay
    # non-secret in ALLOWED_KEYS above — only the host/webhook/topic move here.
    "ALERT_EMAIL_SMTP_HOST",
    "ALERT_NTFY_TOPIC",
    "ALERT_SLACK_WEBHOOK_URL",  # description literally says "Secret"
    # Reddit API User-Agent header. Not a credential in the auth sense (REDDIT_
    # CLIENT_ID/SECRET already cover that), but a per-operator identifying value
    # for a third-party API — classified here for the exact same reason
    # EDGAR_USER_AGENT is (see that key's own comment above), not ALLOWED_KEYS.
    "REDDIT_USER_AGENT",
)

# ---------------------------------------------------------------------------
# Deliberately excluded from BOTH allowlists
# ---------------------------------------------------------------------------
# Fields on settings.Settings that are neither GUI-writable non-secret tunables
# nor secrets to mask -- each is either (a) a filesystem path (editing it from
# the GUI has no clear safety benefit and risks pointing the app at a bogus
# location), or (b) a fail-closed master switch gating a real side effect
# (arbitrary command execution, a paid LLM call exposed over a fail-open HTTP
# API, a .env-writing endpoint) that must stay hand-set-only per this
# codebase's established "a GUI bug must never flip this on" pattern (see e.g.
# tests/test_pilots_api.py's `*_is_not_gui_writable` tests). NOTE:
# BROKERAGE_CONNECT_ENABLED/UNIVERSE_SYNC_ENABLED/AGENTIC_DISCOVERY_ENABLED
# were in this class too until PR #560's "per explicit operator decision"
# reclassified them into ALLOWED_KEYS above (each stays independently gated
# by its own command-token/loopback check downstream) -- they are NOT here.
# This set exists purely so tests/test_gui_env_io.py can assert every
# settings.py field is accounted for -- it grants no capability and is not
# consulted by read_settings/write_setting/write_many (unclassified access
# stays rejected via ALLOWED_KEYS/SECRET_KEYS exactly as before).
EXCLUDED_FROM_GUI: frozenset[str] = frozenset(
    {
        # --- Filesystem paths -------------------------------------------------
        "OUTPUT_DIR",
        "PROMPT_CACHE_DIR",
        "WATCH_RULES_FILE",
        "ALERT_FILE_PATH",
        "GRAVITY_AI_RUNNER_OUTPUT_PATH",
        "LLM_COMMENTARY_CACHE_PATH",
        # --- Fail-closed command / write / paid-API-exposure flags -----------
        # (hand-set in .env only; several already pinned by dedicated
        # `test_*_is_not_gui_writable` tests in tests/test_pilots_api.py)
        "AI_GENERATION_API_ENABLED",
        "AUTOMATION_WRITES_ENABLED",
        "BROKERAGE_REFRESH_ENABLED",
        "COMMAND_EXECUTION_ENABLED",
        "DEAD_LETTER_RETRY_ENABLED",
        "GENERAL_SETTINGS_WRITES_ENABLED",
        "LLM_WRITES_ENABLED",
        "MACRO_GATE_WRITES_ENABLED",
        "PROMPT_REGISTRY_WRITES_ENABLED",
        "RAG_QUERY_API_ENABLED",
        "STRATEGY_WRITES_ENABLED",
    }
)

# Keys whose values are JSON-encoded structures (lists/dicts) in .env.
_JSON_KEYS: frozenset[str] = frozenset(
    {
        "DEFAULT_TICKERS",
        "SIGNAL_WEIGHTS",
        "DISABLED_SIGNAL_MODULES",
        "SECTOR_FORECAST_CONFIGS",  # dict[str, dict] per-sector forecast overrides
        "CORS_ALLOWED_ORIGINS",  # list[str] of allowed browser origins
        "PROMPT_REGISTRY_PINS",  # dict[str, str] {"prompt_id": "version"}
        # ETF volatility-transmission overlay: three ticker/symbol lists.
        "ETF_HOLDINGS_TICKERS",
        "ETF_TRANSMISSION_WRAPPERS",
        "ETF_TRANSMISSION_EXCLUDED_SYMBOLS",
        # Multi-horizon forecast backfill list
        "FORECAST_BACKFILL_HORIZONS",
        "DUAL_MOMENTUM_RISKY_ASSETS",
        # Per-regime signal weight overrides (dict[str, dict[str, float]])
        "REGIME_SIGNAL_WEIGHTS",
    }
)

_MASK_SET = "•••• set"
_MASK_UNSET = "(unset)"


class SecretWriteError(RuntimeError):
    """Raised when the GUI attempts to write a key classified as a secret."""


class DisallowedKeyError(RuntimeError):
    """Raised when the GUI attempts to write a key outside :data:`ALLOWED_KEYS`."""


def _raw_env() -> Dict[str, Optional[str]]:
    """Return the raw ``.env`` key→value mapping (empty dict if no file)."""
    if not ENV_PATH.exists():
        return {}
    try:
        return dict(dotenv_values(ENV_PATH))
    except Exception as exc:  # pragma: no cover - dotenv parse failure is rare
        logger.warning("Failed to parse %s: %s", ENV_PATH, exc)
        return {}


def mask_secret(value: Optional[str]) -> str:
    """Return a masked placeholder for a secret value (never the cleartext)."""
    return _MASK_SET if value else _MASK_UNSET


def read_settings() -> Dict[str, str]:
    """Read displayable settings from ``.env``.

    Secret keys are masked; allowlisted (non-secret) keys are returned verbatim.
    Keys present in ``.env`` but in neither list are returned verbatim too, so
    the operator can still see them — but :func:`write_setting` will refuse to
    edit anything outside :data:`ALLOWED_KEYS`.

    Returns
    -------
    dict[str, str]
        Mapping of env key → display string.  Always safe to render in the GUI:
        no secret cleartext is ever included.
    """
    raw = _raw_env()
    display: Dict[str, str] = {}
    for key, value in raw.items():
        if key in SECRET_KEYS:
            display[key] = mask_secret(value)
        else:
            display[key] = "" if value is None else str(value)
    return display


def get_value(key: str, default: str = "") -> str:
    """Return the cleartext value of a NON-secret allowlisted key from ``.env``.

    Raises
    ------
    SecretWriteError
        If ``key`` is a secret — secret cleartext must never leave this module.
    """
    if key in SECRET_KEYS:
        raise SecretWriteError(
            f"Refusing to return cleartext for secret key '{key}'."
        )
    raw = _raw_env()
    value = raw.get(key)
    return default if value is None else str(value)


def is_secret(key: str) -> bool:
    """True if ``key`` is classified as a secret (masked, never GUI-writable)."""
    return key in SECRET_KEYS


def _encode_value(key: str, value: Any) -> str:
    """Serialize a Python value to its ``.env`` string form for ``key``.

    JSON keys (lists/dicts) are ``json.dumps``-encoded so pydantic-settings
    re-parses them; booleans become lowercase ``true``/``false``; everything
    else is ``str()``-coerced.
    """
    if key in _JSON_KEYS:
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def write_setting(key: str, value: Any) -> str:
    """Write a single NON-secret tunable to ``.env`` (preserving other lines).

    Parameters
    ----------
    key:
        Must be in :data:`ALLOWED_KEYS`; must NOT be in :data:`SECRET_KEYS`.
    value:
        Python value.  JSON keys accept list/dict; scalars accept str/number/bool.

    Returns
    -------
    str
        The encoded string actually written to ``.env`` (handy for confirmation
        messages / tests).

    Raises
    ------
    SecretWriteError
        If ``key`` is a secret.
    DisallowedKeyError
        If ``key`` is not in the allowlist.
    """
    if key in SECRET_KEYS:
        raise SecretWriteError(
            f"Refusing to write secret key '{key}' from the GUI. "
            "Edit secrets directly in .env (CONSTRAINT #3)."
        )
    if key not in ALLOWED_KEYS:
        raise DisallowedKeyError(
            f"Key '{key}' is not in the GUI-writable allowlist (ALLOWED_KEYS)."
        )

    encoded = _encode_value(key, value)
    # Ensure the file exists so set_key can operate on it.
    ENV_PATH.touch(exist_ok=True)
    # quote_mode="auto" keeps simple scalars unquoted and quotes JSON/space values.
    set_key(str(ENV_PATH), key, encoded, quote_mode="auto")
    logger.info("Wrote .env setting %s (value length=%d).", key, len(encoded))
    return encoded


def write_many(updates: Dict[str, Any]) -> List[str]:
    """Write multiple allowlisted settings; returns the keys successfully written.

    Each entry is validated independently by :func:`write_setting`; a single bad
    key raises before any subsequent writes, so callers should pre-validate with
    :func:`is_secret` / membership in :data:`ALLOWED_KEYS` if partial writes are
    undesirable.  This dead-letter-free behavior is intentional: settings writes
    are cheap to retry and we prefer a loud failure over silent partial state.
    """
    written: List[str] = []
    for key, value in updates.items():
        write_setting(key, value)
        written.append(key)
    return written


def write_many_atomic(updates: Dict[str, Any]) -> List[str]:
    """All-or-nothing multi-key ``.env`` write.

    :func:`write_many` validates each key lazily and applies ``set_key`` one at a
    time, so a failure on key *N* leaves keys ``1..N-1`` written — a half-applied
    config. That is tolerable for independent scalars, but NOT for a logical unit
    like ``SIGNAL_WEIGHTS`` + ``DISABLED_SIGNAL_MODULES``: new weights paired with
    a stale disabled-set silently changes what the platform recommends.

    This variant validates EVERY key first (same :class:`SecretWriteError` /
    :class:`DisallowedKeyError` rules as :func:`write_setting`), then applies each
    ``set_key`` to a temporary COPY of ``.env`` and ``os.replace``\\ s it into place
    — the same write-then-rename idiom as ``execution/kill_switch.py::activate`` and
    ``reporting/options_snapshot.py``. ``python-dotenv``'s ``set_key`` is reused
    verbatim (same quoting / comment preservation), only pointed at the temp path.
    :func:`write_many` is left unchanged.

    Residual limitation (documented, not fixed here): there is no file lock, so a
    concurrent writer (e.g. the Streamlit Settings tab) can still last-writer-wins
    clobber this write. That race pre-exists for every ``.env`` writer in the repo
    and is not introduced by this function.

    Returns
    -------
    list[str]
        The keys written (in input order).

    Raises
    ------
    SecretWriteError
        If any key is a secret. Raised before any write; ``.env`` is untouched.
    DisallowedKeyError
        If any key is outside the allowlist. Raised before any write.
    """
    # Validate EVERY key up front — nothing is written unless all pass.
    for key in updates:
        if key in SECRET_KEYS:
            raise SecretWriteError(
                f"Refusing to write secret key '{key}' from the GUI. "
                "Edit secrets directly in .env (CONSTRAINT #3)."
            )
        if key not in ALLOWED_KEYS:
            raise DisallowedKeyError(
                f"Key '{key}' is not in the GUI-writable allowlist (ALLOWED_KEYS)."
            )

    target = ENV_PATH.resolve()  # follow symlinks — replace the real file
    target.touch(exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    # copy2 preserves mode/timestamps so a 0600 .env stays 0600.
    shutil.copy2(target, tmp)
    try:
        for key, value in updates.items():
            set_key(str(tmp), key, _encode_value(key, value), quote_mode="auto")
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    logger.info("Atomically wrote .env settings: %s", ", ".join(updates.keys()))
    return list(updates.keys())


def allowlisted_keys() -> Iterable[str]:
    """Return the GUI-writable keys (stable order) for rendering the form."""
    return ALLOWED_KEYS
