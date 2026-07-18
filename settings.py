"""Centralized, environment-driven runtime configuration for the InvestYo Quant Platform. All secrets, financial constants, feature flags, and machine-specific paths are sourced here via pydantic-settings (environment / .env) instead of being hardcoded across the engines and orchestrators."""

# =============================================================================
# MODULE: RUNTIME CONFIGURATION
# File: settings.py
# Description: Centralized, environment-driven runtime configuration for the
#              InvestYo Quant Platform. All secrets, financial constants, and
#              machine-specific paths are sourced here (via environment / .env)
#              instead of being hardcoded across the engines and orchestrators.
# =============================================================================

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict  # type: ignore

logger = logging.getLogger(__name__)

# A FRED API key was previously hardcoded in main.py / main_orchestrator.py and
# committed to git history. If the live key still equals that value it is
# compromised and MUST be rotated. We store only the SHA-256 digest of the leaked
# key (never the literal) so the platform can detect reuse without re-embedding
# the secret anywhere in the source tree.
LEAKED_FRED_KEY_SHA256 = "d18938214ce633f15694ee7d77ecf69f5ea7654615c478f5f37b968dd7e8824e"
FRED_ROTATION_URL = "https://fred.stlouisfed.org/docs/api/api_key.html"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# Shared interval-validation policy for the persistent orchestrator daemon's
# live timer setter (Piece 2 of the queue-composition/live-interval-setter
# work). Three independent call sites need the SAME [min, max]-or-zero rule
# and must never drift apart: desktop/daemon_runtime.py's
# OrchestratorDaemon.set_interval (the actual runtime setter),
# api/control_api.py's PUT /interval pydantic body, and
# api/pilots_api.py's PUT /automation/schedule/interval pydantic body. They
# can't share a validator by importing each other (control_api.py must not
# import pilots_api.py; importing desktop.daemon_runtime into pilots_api.py
# would drag main_orchestrator into a module whose own AST guard forbids the
# heavy engines) -- but all three already import this module, so the shared
# policy lives here instead. 0 always means "disabled" (no timer, on-demand
# only); any nonzero value must fall in [INTERVAL_MIN_SECONDS,
# INTERVAL_MAX_SECONDS] -- a sub-60s interval would fire faster than a cycle
# can complete (degenerate, not dangerous: trigger_run() just returns
# ALREADY_RUNNING every time), and there's no reason to allow it.
INTERVAL_MIN_SECONDS = 60
INTERVAL_MAX_SECONDS = 86400


def validate_interval_seconds(v: int) -> int:
    """Shared validation for a daemon-timer interval value in seconds.

    Raises ``ValueError`` (not a bespoke exception type) so it can be reused
    verbatim inside a pydantic ``field_validator`` (pydantic wraps a
    ``ValueError`` raised inside a validator into its own ``ValidationError``
    automatically) as well as from a plain setter with no pydantic involved.
    """
    if v != 0 and not (INTERVAL_MIN_SECONDS <= v <= INTERVAL_MAX_SECONDS):
        raise ValueError(
            f"interval_seconds must be 0 or in [{INTERVAL_MIN_SECONDS}, "
            f"{INTERVAL_MAX_SECONDS}], got {v}"
        )
    return v


class Settings(BaseSettings):
    """Single source of truth for runtime configuration.

    Values are resolved (in precedence order) from: explicit init kwargs,
    environment variables, then a local ``.env`` file, then the defaults below.
    Field names are case-insensitive (``FRED_API_KEY`` / ``fred_api_key``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # =========================================================================
    # FIELD SECTIONS (in declaration order below)
    # -------------------------------------------------------------------------
    #   1.  Secrets / credentials .............. FRED, Alpaca, State API token
    #   2.  Market-data layer .................. provider, Finnhub, cache TTLs
    #   3.  Robinhood — legacy SMS login ....... ROBINHOOD_USERNAME/PASSWORD
    #   4.  Robinhood — portfolio TOTP ......... RH_USERNAME/PASSWORD/MFA_SECRET
    #   5.  Order management / broker .......... DRY_RUN, ADVISORY_ONLY, webhook
    #   6.  Pre-trade risk gate ................ correlation, loss limit, HMM
    #   7.  Kill switch ........................ FLATTEN_ON_KILL
    #   8.  Observability / alerts ............. Discord/Slack/email/SMTP, dash
    #   9.  Key rotation / preflight dates ..... paper-start, FRED/Alpaca rotated
    #   10. Financial constants ................ risk-free, premium, heat
    #   11. Position sizing .................... Kelly, vol-target, leverage caps
    #   12. Runtime / IO ....................... OUTPUT_DIR, tickers, log, concurrency, CORS origins
    #   13. Signal weights ..................... flat + regime overrides + disabled
    #   14. Multifactor ........................ microcap threshold
    #   15. Meta-labeling ...................... min-confidence hard gate
    #   16. Historical persistence ............. store flag, backfill, refresh
    #   17. Forecast skill weighting ........... window, min-obs
    #   18. Macro regime gate .................. MACRO_REGIME_GATE_ENABLED
    #   19. Snapshot diff / Δ-band ............. history days, conviction delta
    #   20. Symbol watch alerts ................ WATCH_RULES_FILE
    #   21. Rationale verbosity ................ standard | verbose
    #   22. News catalyst ...................... lookback, FinBERT, earnings gate
    #   23. Correlation clusters ............... lookback, threshold
    #   24. Dual-momentum overlay .............. safe/risky assets
    #
    # NOTE: field names are intentionally FLAT (e.g. settings.KELLY_CAP). The
    # sections are documentation only — do NOT nest fields into sub-models, as
    # ~200 call sites and the .env contract depend on the flat names.
    # =========================================================================

    # --- 1. Secrets / credentials (resolved from the environment) ---
    # FRED is required for *live* macro data. It is left empty by default so the
    # platform can still import and fall back to MockDataEngine; the live path
    # calls ``ensure_fred_configured()`` to fail clearly when it is missing.
    FRED_API_KEY: str = Field(
        default="", description="FRED API key. Required for live macroeconomic data."
    )
    ALPACA_API_KEY: Optional[str] = Field(default=None, description="Alpaca API key (optional).")
    ALPACA_SECRET_KEY: Optional[str] = Field(default=None, description="Alpaca secret key (optional).")
    ALPACA_PAPER: bool = Field(default=True, description="Use Alpaca paper-trading endpoint.")
    STATE_API_TOKEN: Optional[str] = Field(
        default=None,
        description=(
            "Bearer token for the read-only State API (api/state_api.py). "
            "SECRET — never GUI-writable, never logged. When unset, the API's "
            "data endpoints are unauthenticated (fail-open for local use)."
        ),
    )
    ORCHESTRATOR_DAEMON_TOKEN: Optional[str] = Field(
        default=None,
        description=(
            "Bearer token guarding POST /run on the orchestrator Control API "
            "(api/control_api.py). SECRET — never GUI-writable, never logged. "
            "Unlike STATE_API_TOKEN, this is FAIL-CLOSED: when unset, the "
            "command endpoint is disabled entirely (403) rather than open — "
            "triggering a real pipeline run is a materially different risk "
            "than reading already-persisted state."
        ),
    )
    ORCHESTRATOR_API_PORT: int = Field(
        default=8601,
        description=(
            "TCP port the orchestrator Control API (api/control_api.py) binds "
            "to when hosted inside the orchestrator daemon process "
            "(desktop/orchestrator_daemon.py). Bound to 127.0.0.1 only."
        ),
    )
    FOLLOW_API_TOKEN: Optional[str] = Field(
        default=None,
        description=(
            "Bearer token guarding the follow WRITE-path on the Pilots API "
            "(api/pilots_api.py — PUT /follows, POST /pilots/{id}/follow). "
            "SECRET — never GUI-writable, never logged. Like "
            "ORCHESTRATOR_DAEMON_TOKEN and unlike STATE_API_TOKEN, this is "
            "FAIL-CLOSED: when unset, the follow endpoints are disabled "
            "entirely (403) rather than open — persisting a follow that "
            "produces a gated order queue is a materially different risk than "
            "reading already-persisted Pilot state. Read endpoints on the same "
            "API use the fail-open STATE_API_TOKEN instead."
        ),
    )
    PILOTS_API_ENABLED: bool = Field(
        default=False,
        description=(
            "Host the Pilots API (api/pilots_api.py) inside the persistent "
            "orchestrator daemon process (desktop/orchestrator_daemon.py) on "
            "PILOTS_API_PORT, alongside the existing Control API. False "
            "(default) preserves today's exact behavior — pilots_api.py "
            "remains a manually-launched standalone `uvicorn` service, "
            "unaffected by the daemon's lifecycle. Only takes effect when the "
            "daemon entrypoint itself is run — does not require "
            "ORCHESTRATOR_DAEMON_ENABLED (that flag controls the DESKTOP "
            "SHELL's choice of subprocess; this flag controls what the "
            "daemon PROCESS hosts once launched, by either path)."
        ),
    )
    PILOTS_API_PORT: int = Field(
        default=8602,
        description=(
            "TCP port the Pilots API (api/pilots_api.py) binds to when hosted "
            "inside the orchestrator daemon process (PILOTS_API_ENABLED=True). "
            "Bound to 127.0.0.1 only. Matches the port used in the documented "
            "standalone launch command (`uvicorn api.pilots_api:app --port 8602`)."
        ),
    )

    # --- Market-data layer (data/market_data.py) ---
    # Explicit provider override.  When absent the platform auto-selects:
    # Alpaca (if keys present) → yfinance (zero config, ~15-min delayed).
    MARKET_DATA_PROVIDER: Optional[str] = Field(
        default=None,
        description=(
            "Force a specific market-data backend: 'alpaca' or 'yfinance'. "
            "When unset the platform auto-selects based on key availability."
        ),
    )
    FINNHUB_API_KEY: Optional[str] = Field(
        default=None,
        description=(
            "Finnhub API key used ONLY by the news_catalyst signal "
            "(signals/news_catalyst.py — company news / earnings headlines). "
            "Free tier available at https://finnhub.io. Fundamentals are NO "
            "longer sourced from Finnhub: they are Yahoo statement-derived "
            "(data/yahoo_fundamentals.py) with a yfinance .info fallback, so "
            "an absent key only disables the news catalyst signal (no crash)."
        ),
    )
    # TTL (seconds) for the in-process quote cache in CompositeProvider.
    # Prevents redundant network calls within a single refresh cycle.
    # Quotes must NOT be persisted to disk — cache is in-process only.
    MARKET_DATA_QUOTE_TTL_SECONDS: int = Field(
        default=30,
        description="In-process quote cache TTL in seconds (never persisted to disk).",
    )
    MARKET_DATA_BARS_TTL_SECONDS: int = Field(
        default=900,
        description=(
            "In-process OHLCV intraday-bars cache TTL in seconds (never persisted "
            "to disk). Bars are daily-resolution, so a few-minutes TTL collapses "
            "the repeated per-symbol history fetches (universe pre-fetch + advisory "
            "refetch + GUI panels) into a single network pull within the window. "
            "Defaults to 15 min to align with DATA_FRESHNESS_TTL_SECONDS (the "
            "cross-cycle persisted-freshness gate); this is the in-process, "
            "single-cycle companion to that gate."
        ),
    )
    # Cross-cycle data-freshness gate (persisted marker, see main_orchestrator.
    # _data_is_fresh / _mark_data_refreshed). When an INTERVAL-triggered daemon
    # cycle finds the last successful data pull was younger than this TTL, it
    # SKIPS the network refresh entirely rather than re-pulling every 5 min.
    # Manual "Run Pipeline" / --refresh / any non-interval trigger always
    # bypasses the gate (force=True). 0 disables the gate (every cycle pulls,
    # the pre-gate behavior). Unlike MARKET_DATA_BARS_TTL_SECONDS (in-process,
    # dies with the process), this survives daemon restarts via a small marker
    # file in OUTPUT_DIR, so a fresh daemon does not immediately re-pull.
    DATA_FRESHNESS_TTL_SECONDS: int = Field(
        default=900,
        description=(
            "Skip an interval-triggered daemon refresh when the last successful "
            "data pull was younger than this many seconds (default 15 min). "
            "Manual/forced runs always bypass. 0 disables the gate."
        ),
    )
    # TTL (seconds) for the in-process fundamentals cache in FinnhubProvider
    # and CompositeProvider.  Fundamentals are quarterly/slow-moving, so a
    # multi-hour TTL is safe and prevents the free Finnhub tier (60 calls/min)
    # from being exhausted by repeated orchestrator passes.  Both positive AND
    # empty responses are cached so 429-rate-limited symbols don't re-trigger
    # network calls within the window.
    FUNDAMENTALS_CACHE_TTL_SECONDS: int = Field(
        default=21_600,
        description="In-process fundamentals cache TTL in seconds (default 6 h).",
    )
    # Shorter TTL specifically for NEGATIVE (empty-dict) fundamentals responses
    # -- a provider that was rate-limited or briefly down would otherwise stay
    # "no data" for the full positive-cache TTL (up to 6 h) even after it
    # recovers. Negative results are re-tried much sooner than positive ones.
    FUNDAMENTALS_NEG_CACHE_TTL_SECONDS: int = Field(
        default=900,
        description="In-process NEGATIVE (empty) fundamentals cache TTL in seconds (default 15 min).",
    )
    # Sliding-window call budget for FinnhubProvider (per 60 s).  Free tier is
    # 60 calls/minute; we default to 50 to leave headroom for the two auxiliary
    # endpoints (quote, company_profile2) that ``get_fundamentals`` invokes.
    FINNHUB_RATE_LIMIT_PER_MIN: int = Field(
        default=50,
        description="Finnhub sliding-window call budget per 60 s (free tier ceiling: 60).",
    )
    BETA_LOOKBACK_DAYS: int = Field(
        default=504,
        description=(
            "Trailing calendar days of daily returns used to compute beta in the "
            "Yahoo-derived fundamentals engine (Cov(stock,SPY)/Var(SPY)). ~2 years."
        ),
    )
    FUNDAMENTALS_SOURCE: str = Field(
        default="yahoo",
        description=(
            "Primary fundamentals backend: 'yahoo' (statement-derived, default) or "
            "'yfinance_info' (raw .info fallback). Finnhub is no longer a fundamentals source."
        ),
    )
    # --- Robinhood Integration (legacy data/robinhood_client.py — SMS login) ---
    ROBINHOOD_USERNAME: Optional[str] = Field(default=None, description="Robinhood username (email).")
    ROBINHOOD_PASSWORD: Optional[str] = Field(default=None, description="Robinhood password.")
    # --- Robinhood portfolio snapshot (data/robinhood_portfolio.py — TOTP login) ---
    # Read-only; used for account state only. No order functions anywhere in that
    # module. data/robinhood_portfolio.py reads these directly from os.environ so
    # they are never stored in a Settings object (avoiding accidental logging);
    # they are declared here for .env documentation + pydantic-settings consistency.
    RH_USERNAME: Optional[str] = Field(
        default=None,
        description="Robinhood account email for TOTP-authenticated read-only portfolio snapshot.",
    )
    RH_PASSWORD: Optional[str] = Field(
        default=None,
        description="Robinhood account password for TOTP-authenticated read-only portfolio snapshot.",
    )
    RH_MFA_SECRET: Optional[str] = Field(
        default=None,
        description=(
            "Base32 TOTP secret from the Robinhood MFA setup page. Used to generate "
            "the 6-digit code via pyotp.TOTP(RH_MFA_SECRET).now() — never logged or cached."
        ),
    )
    # --- Order management (execution/order_manager.py) ---
    # When True the orchestrator logs intended orders but never submits them.
    # Override via CLI --dry-run flag or DRY_RUN=true in .env.
    DRY_RUN: bool = Field(default=False, description="Log orders but do not submit to broker.")

    # --- Advisory-only mode (Tier 5.1, 2026-06) ---
    # When True (the project default), the entire broker-execution surface is
    # quarantined: main_orchestrator._execute_broker_orders() returns
    # immediately with an INFO log, the GUI Strategy Matrix mode toggle is
    # disabled, and preflight_check.py drops the broker-readiness checks
    # (alpaca_configured / alpaca_paper_mode / dry_run_disabled) in favour of
    # a single advisory_only_active check.  This is a HARDER guarantee than
    # DRY_RUN: DRY_RUN is enforced inside OrderManager (which can be bypassed
    # by a future caller); ADVISORY_ONLY is enforced at the orchestrator-level
    # ``_execute_broker_orders`` gate AND surfaced in every GUI tab as a
    # persistent banner, so the operator cannot click into Live by mistake.
    #
    # Set to False ONLY if you have explicitly re-enabled the broker stack
    # and intend to submit orders.  Both flags must agree (ADVISORY_ONLY=false
    # AND DRY_RUN=false AND ALPACA_PAPER=false) to reach a live submission.
    ADVISORY_ONLY: bool = Field(
        default=True,
        description=(
            "When True, ALL broker order submission is suppressed. The pipeline "
            "still runs end-to-end (signals, sizing, HTML report, JSON payload) "
            "but main_orchestrator._execute_broker_orders() returns immediately "
            "and the GUI Strategy Matrix execution-mode toggle is disabled. "
            "Set False ONLY when broker execution is intentionally re-enabled."
        ),
    )
    # --- Robinhood execution bridge (Tier 8, 2026-06) ---
    # Independent of ADVISORY_ONLY (which gates the Alpaca surface).  The
    # Robinhood Trading MCP is consumed by a Claude Code agent, NOT the headless
    # pipeline, so this flag only governs whether `execution/queue_builder.py`
    # emits a gated, dry-run `output/execution_queue.json` for that agent.
    #   off    — (default) emit nothing; zero behaviour change.
    #   review — paper/dry-run: emit the queue; the agent only ever calls the
    #            MCP `review_equity_order` (simulate), never `place_equity_order`.
    #   live   — the queue marks `allow_place=true` only when the risk gate passes
    #            AND the kill switch is clear; the agent STILL requires per-trade
    #            human confirmation before calling `place_equity_order`.
    # Rollout is strictly off -> review -> live; you never start at live.  An
    # unrecognised value coerces to `off` (fail-safe) via the validator below.
    ROBINHOOD_EXECUTION_MODE: str = Field(
        default="off",
        description="Robinhood execution-queue mode: off | review | live (default off).",
    )
    # Hard per-order notional ceiling (USD) applied when building the queue.
    # 0.0 means "unset" — the execution agent treats 0.0 as 'must configure a
    # cap before any live placement'.
    ROBINHOOD_MAX_NOTIONAL_PER_ORDER: float = Field(
        default=0.0,
        description="Max USD notional per Robinhood order when building the queue (0 = unset).",
    )
    # Limit-order buffer in basis points (1 bps = 0.01%) applied when building the
    # execution queue.  0 (default) = MARKET orders, byte-identical to the legacy
    # behaviour.  A positive value flips every emitted intent to a LIMIT order and
    # stamps `limit_offset_bps` on it; the ACTUAL limit_price stays null in the
    # queue and is resolved DOWNSTREAM by the robinhood-execution skill from a live
    # MCP quote at review time, applying:
    #     BUY  limit <= quote * (1 + bps/10000)
    #     SELL limit >= quote * (1 - bps/10000)
    # (the headless pipeline has no live price, so it only carries the buffer).
    ROBINHOOD_LIMIT_BUFFER_BPS: int = Field(
        default=0,
        description=(
            "Limit-order buffer in basis points for the Robinhood queue "
            "(0 = MARKET orders; >0 = LIMIT orders, price resolved downstream)."
        ),
    )
    # execution/compose.py (cross-Pilot + advisory queue composer) reads a
    # per-source JSON file (output/queue_sources/<source_id>.json) for the
    # advisory pipeline and for every actively-followed Pilot. A follow's
    # source file is written only when the operator explicitly (re-)follows
    # via plan_follow -- there is no background job that keeps it fresh
    # (the "re-plan all follows" auto-refresh idea was deliberately cut from
    # this feature -- see docs/AUTOPILOT_PLAN.md). Left unchecked, a
    # weeks-old target netted against today's account holdings would be
    # computed from a dead snapshot. Rather than silently netting against
    # arbitrarily stale data (or picking a threshold nobody chose), a single
    # source older than this is treated as CORRUPT for composition purposes:
    # the whole compose_and_emit() call is refused (nothing is written; the
    # last known-good execution_queue.json is left in place) rather than
    # emitting an order sized from a stale claim. 7 days is a conservative,
    # explicitly-owned default -- not a "correct" number, a judgment call:
    # long enough that a follow set once doesn't need re-confirming daily,
    # short enough that month-old Pilot rankings can never silently drive an
    # order. Applies uniformly to every source, including the advisory one
    # (freshly written every main.py cycle in normal operation, so this only
    # ever bites the advisory source when the pipeline itself hasn't run in
    # a week).
    QUEUE_SOURCE_MAX_AGE_SECONDS: float = Field(
        default=604800.0,
        description=(
            "Max age (seconds) of a queue_sources/*.json file before "
            "execution.compose.compose_and_emit refuses to compose (writes "
            "nothing, leaves the last queue in place). Default 7 days -- a "
            "deliberate, conservative judgment call, not a derived constant."
        ),
    )

    # Slack / Discord incoming-webhook URL for reconciliation drift alerts.
    ALERT_WEBHOOK_URL: Optional[str] = Field(
        default=None,
        description="Webhook URL for CRITICAL drift alerts (Slack/Discord incoming webhook).",
    )

    # --- Pre-trade risk gate (execution/risk_gate.py) ---
    MAX_CORRELATION: float = Field(
        default=0.85,
        description="Max absolute pairwise return correlation before a new position is blocked.",
    )
    DAILY_LOSS_LIMIT_PCT: float = Field(
        default=0.02,
        description="Halt new BUY orders when intraday P&L drops below this fraction of start-of-day equity.",
    )
    MAX_ORDER_RATE_PER_MIN: int = Field(
        default=10,
        description="Maximum order submissions in any 60-second rolling window.",
    )
    HMM_RISK_OFF_BLOCK_THRESHOLD: float = Field(
        default=0.80,
        description="Block new long orders when HMM risk-off probability exceeds this.",
    )
    RISK_GATE_ENFORCE_MARKET_HOURS: bool = Field(
        default=True,
        description="Block orders outside NYSE RTH (09:30–16:00 ET).",
    )

    # --- HMM regime detector (regime/hmm_regime.py, macro_engine.py) ---
    HMM_N_STATES: int = Field(
        default=3,
        description="Number of hidden states for the Gaussian HMM regime detector (bull/sideways/bear).",
    )
    HMM_RETRAIN_FREQ_DAYS: int = Field(
        default=7,
        description="Minimum days between HMM refits; fit() calls within this window of the last real fit are no-ops.",
    )

    # --- Kill switch (execution/kill_switch.py) ---
    # When True and the kill switch fires, a CRITICAL reminder is logged to flatten
    # open positions manually. Automatic flattening is a future extension.
    FLATTEN_ON_KILL: bool = Field(
        default=False,
        description="Log CRITICAL position-flatten reminder when kill switch activates.",
    )

    # --- Observability / alerts (observability/alerts.py, gui/panels/observability.py) ---
    DISCORD_WEBHOOK_URL: Optional[str] = Field(
        default=None,
        description="Discord incoming-webhook URL for alert dispatch.",
    )
    SLACK_WEBHOOK_URL: Optional[str] = Field(
        default=None,
        description="Slack incoming-webhook URL for alert dispatch.",
    )
    ALERT_FILE_PATH: Optional[str] = Field(
        default=None,
        description="Absolute path for JSON-lines alert log file. None = disabled.",
    )
    ALERT_EMAIL_FROM: Optional[str] = Field(default=None, description="SMTP sender address.")
    ALERT_EMAIL_TO: Optional[str] = Field(
        default=None,
        description="Comma-separated recipient addresses for email alerts.",
    )
    ALERT_SMTP_HOST: Optional[str] = Field(default=None, description="SMTP server hostname.")
    ALERT_SMTP_PORT: int = Field(default=587, description="SMTP server port (587=STARTTLS).")
    ALERT_SMTP_USER: Optional[str] = Field(default=None, description="SMTP authentication username.")
    ALERT_SMTP_PASSWORD: Optional[str] = Field(default=None, description="SMTP authentication password.")
    ALERT_DEDUP_WINDOW_SECONDS: int = Field(
        default=900,
        description=(
            "TTL (seconds) for observability.alerts.send_alert()'s optional "
            "dedup_key suppression window. 900s (15 min) is chosen to be long "
            "enough to absorb a tight retry/poll loop or a condition that "
            "re-evaluates every pipeline cycle (an alert storm) while still "
            "short enough that a genuinely new occurrence of the same "
            "condition is re-surfaced well within a single trading session. "
            "Only applies to callers that opt in via dedup_key; omitting it "
            "reproduces the pre-dedup always-fires behavior exactly."
        ),
    )
    # --- alerting_mcp/notifier.py (the standalone MCP push-notifier) --------
    # These are read via os.getenv() inside alerting_mcp/notifier.py, which is a
    # separate subsystem from observability/alerts.py above (note the distinct
    # ALERT_EMAIL_SMTP_* names vs. ALERT_SMTP_* used by observability/alerts.py).
    # Declared here for discoverability/consistency; the notifier keeps reading
    # os.getenv directly so it stays importable without a full Settings() load.
    ALERT_NTFY_TOPIC: Optional[str] = Field(
        default=None,
        description="ntfy.sh topic for alerting_mcp push notifications. Unset = ntfy channel disabled.",
    )
    ALERT_EMAIL_SMTP_HOST: Optional[str] = Field(
        default=None,
        description="SMTP hostname for alerting_mcp email alerts (e.g. smtp.gmail.com).",
    )
    ALERT_EMAIL_SMTP_PORT: int = Field(
        default=587,
        description="SMTP port for alerting_mcp email alerts (587 = STARTTLS).",
    )
    ALERT_EMAIL_SMTP_PASSWORD: Optional[str] = Field(
        default=None,
        description="SMTP app-password for alerting_mcp email alerts. Secret; unset = email channel disabled.",
    )
    ALERT_SLACK_WEBHOOK_URL: Optional[str] = Field(
        default=None,
        description="Slack incoming-webhook URL for alerting_mcp Slack alerts. Secret; unset = Slack channel disabled.",
    )
    ALERT_CHANNELS: Optional[str] = Field(
        default=None,
        description="Comma-separated active alerting_mcp channels (e.g. 'ntfy,email,slack'). Unset defaults to 'ntfy'.",
    )
    DASHBOARD_REFRESH_SECONDS: int = Field(
        default=1800, description="Auto-refresh interval for the Streamlit observability dashboard (seconds). Default 1800 = 30 min."
    )
    PROGRESS_POLL_SECONDS: int = Field(
        default=5, description="Poll interval (seconds) for the Launcher pipeline-progress indicator."
    )
    # ISO date string (YYYY-MM-DD) recording when paper trading began.
    # Used by scripts/preflight_check.py to verify >= 90 days of paper history.
    PAPER_TRADING_START_DATE: Optional[str] = Field(
        default=None,
        description="ISO date (YYYY-MM-DD) when paper trading began. Required by preflight check.",
    )

    # ISO date string (YYYY-MM-DD) recording when FRED_API_KEY was last rotated.
    # Used by scripts/preflight_check.py::check_key_rotation_recent to surface a
    # warning when the key has not been rotated within the recommended 90-day window.
    # Set this whenever you generate a new key at:
    #   https://fred.stlouisfed.org/docs/api/api_key.html
    # Advisory-only operators still benefit from rotating the FRED key to limit
    # blast radius if the key leaks from logs or shared .env files.
    FRED_KEY_ROTATED_DATE: Optional[str] = Field(
        default=None,
        description=(
            "ISO date (YYYY-MM-DD) when FRED_API_KEY was last rotated. "
            "Set after generating a new key to keep the rotation reminder current. "
            "Unset = key-age check skipped (warning-level PASS, not blocking)."
        ),
    )
    ALPACA_KEY_ROTATED_DATE: Optional[str] = Field(
        default=None,
        description=(
            "ISO date (YYYY-MM-DD) when ALPACA_API_KEY was last rotated. "
            "Auto-skipped by preflight when ADVISORY_ONLY=True (paper keys have "
            "no blast-radius risk when the broker surface is quarantined). "
            "Unset = key-age check skipped (warning-level PASS, not blocking)."
        ),
    )

    # --- Financial constants ---
    RISK_FREE_RATE: float = 0.045
    MARKET_RISK_PREMIUM: float = 0.055
    REQUIRED_RETURN_RATE: float = 0.08
    MAX_PORTFOLIO_HEAT: float = 0.06

    # --- Position sizing (sizing/kelly.py, sizing/vol_target.py) ---
    KELLY_FRACTION: float = 0.5   # half-Kelly
    KELLY_CAP: float = 0.20
    VOL_TARGET: float = 0.10
    MAX_LEVERAGE: float = 2.0
    # Hard ceiling on any single-name position weight, applied as a final clamp
    # in StrategyEngine._calculate_kelly_sizing regardless of sizing path (Kelly
    # or volatility-target fallback). Chosen as the middle ground between the
    # old score-bracket system's hard 25% cap and the new vol-target fallback's
    # uncapped-up-to-MAX_LEVERAGE (2.0x) behavior: 1.0 = up to 100% of capital
    # in one name, but no added leverage on top of full allocation.
    MAX_POSITION_WEIGHT: float = 1.0

    # --- Runtime / IO ---
    OUTPUT_DIR: Path = Field(default=Path("./output"), description="Directory for generated reports.")
    DEFAULT_TICKERS: list[str] = Field(default_factory=lambda: ["AAPL", "MSFT", "JNJ", "AGNC"])
    CORS_ALLOWED_ORIGINS: list[str] = Field(
        # http://localhost:3000 is the classic CRA/Node dev-server convention;
        # the 5173 pair (both host spellings, since browsers treat localhost
        # and 127.0.0.1 as distinct origins) is Vite's default port, used by
        # webapp/ (the Pilots PWA, api/pilots_api.py's consumer) — without
        # these, `npm run dev` + `uvicorn api.pilots_api:app` fails CORS on a
        # fresh clone with zero .env configuration.
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        description=(
            "Allowed browser origins for the read-only State API / Pilots API "
            "CORS policy. JSON array in .env, e.g. "
            '["http://localhost:3000", "https://app.example.com"].'
        ),
    )
    LOG_LEVEL: str = "INFO"
    # Number of worker threads for the per-symbol advisory loop in main.run_once().
    # Each engine.advisory.evaluate() call is independent (per-call engine
    # construction, read-only shared inputs), so the loop parallelizes safely.
    # The win is mostly network I/O (per-symbol quote fetch) plus native-compute
    # sections (numpy/pandas/statsmodels/arch release the GIL). Concurrent
    # HistoricalStore fundamentals writes are serialized by its busy_timeout.
    # Set to 1 to force the original sequential, fully-deterministic path.
    ADVISORY_MAX_CONCURRENCY: int = Field(
        default=8,
        description=(
            "Worker-thread count for the per-symbol advisory loop in "
            "main.run_once(). 1 = sequential (original behavior). Results are "
            "always reassembled in deterministic symbol order regardless of value."
        ),
    )
    # Number of worker threads for the per-ticker forecasting loop in
    # main_orchestrator.run_pipeline(). Each ForecastingEngine.generate_forecast()
    # call fits models on local arrays and returns a dict — the engine is stateless
    # across tickers, so the loop parallelizes safely. The win is native-compute
    # sections (numpy/pandas/statsmodels/arch/keras release the GIL). Each ticker's
    # try/except Monte-Carlo fallback still isolates per-ticker failures.
    # Set to 1 to force the original sequential, fully-deterministic path.
    FORECAST_MAX_CONCURRENCY: int = Field(
        default=8,
        description=(
            "Worker-thread count for the per-ticker forecasting loop in "
            "main_orchestrator.run_pipeline(). 1 = sequential (original behavior). "
            "Results are always reassembled deterministically by symbol regardless "
            "of value."
        ),
    )
    FORECAST_USE_GARCH_SIGMA: bool = Field(
        default=True,
        description=(
            "Use the GJR-GARCH(1,1) volatility estimate (annualized, converted to "
            "daily via /sqrt(252)) as the Monte Carlo sigma instead of naive "
            "historical stdev. False restores the pre-GARCH log-return-std behavior."
        ),
    )
    FORECAST_PROPHET_WEIGHT: float = Field(
        default=0.25,
        description=(
            "Weight given to the Prophet 30-day forecast when blending it into the "
            "static ensemble at the 30-day horizon: final = base*(1-w) + prophet*w. "
            "0.0 disables Prophet's influence on the blend."
        ),
    )
    FORECAST_MODEL_PERSISTENCE_ENABLED: bool = Field(
        default=False,
        description=(
            "Opt-in: persist the trained CNN-LSTM (.keras + both MinMaxScalers) and "
            "Prophet model to disk per ticker (forecasting/model_persistence.py) "
            "instead of retraining from scratch every cycle. Split train from "
            "inference the same way regime/hmm_regime.py's HMMRegimeDetector does: "
            "a fresh model is fit only when no cached artifact exists for the "
            "ticker or it is older than FORECAST_MODEL_RETRAIN_DAYS; otherwise the "
            "cached model is loaded and only inference (predict) runs. "
            "Behavior-preserving BETWEEN retrains (same fitted weights -> same "
            "forecast for repeated calls); only changes WHEN a fit happens. "
            "When False (the default) every call retrains from scratch, matching "
            "pre-persistence behavior exactly -- matches the FORECAST_USE_GARCH_SIGMA "
            "opt-in convention. Requires TensorFlow/Prophet to be installed; a "
            "missing library or a corrupt/unreadable cached artifact degrades to a "
            "fresh fit (never raises)."
        ),
    )
    FORECAST_MODEL_RETRAIN_DAYS: int = Field(
        default=7,
        description=(
            "Days a persisted CNN-LSTM/Prophet model artifact remains valid before "
            "the next generate_forecast() call for that ticker triggers a fresh fit "
            "(mirrors regime/hmm_regime.py's HMMRegimeDetector(retrain_freq_days=7) "
            "convention). Only consulted when FORECAST_MODEL_PERSISTENCE_ENABLED=True."
        ),
    )
    ADVISORY_REUSE_PIPELINE_COMPUTE: bool = Field(
        default=False,
        description=(
            "Opt-in, OUTPUT-CHANGING: main_orchestrator.py's advisory overlay "
            "(engine.advisory.evaluate(), run AFTER run_pipeline() has already "
            "GARCH-fit and forecast-fit every ticker once) reuses run_pipeline's "
            "already-computed dashboard_df['GARCH_Vol'] / dashboard_df['Forecast_30'] "
            "for that ticker instead of independently refitting GJR-GARCH and the "
            "full ARIMA/Holt-Winters/CNN-LSTM/Prophet forecast ensemble a SECOND "
            "time -- eliminating the single largest redundant CPU cost per cycle. "
            "advisory.evaluate() only trusts a precomputed value when it is a real "
            "positive number; a missing/zero/failed upstream value falls through to "
            "the original independent fit (dead-letter safe -- CONSTRAINT #6), so "
            "this can only ever REMOVE a redundant fit, never silently drop one that "
            "already ran. StrategyEngine.evaluate_security() is deliberately NOT "
            "reused here (run_pipeline's own call omits context_extras, unlike "
            "advisory.evaluate()'s -- reusing it would silently zero out the "
            "cross-sectional-momentum/multifactor signal contributions), so scoring "
            "itself is always freshly computed with correct context. Because a fresh "
            "independent fit and a reused one are not guaranteed bit-identical "
            "(CNN-LSTM's random weight init, GARCH's numerical optimizer), turning "
            "this on can move Advisory_* column values slightly -- hence default "
            "False and its own opt-in flag, unlike the byte-identical PR A hot-path "
            "changes. When False (the default), every advisory-overlay call passes "
            "precomputed_garch=None/precomputed_forecast=None, reproducing the exact "
            "pre-dedup behavior."
        ),
    )
    # Number of worker threads for DataEngine.fetch_technical_raw() and
    # fetch_fundamentals_raw() (data_engine.py). Both were originally a serial
    # `for symbol in tickers:` loop making one blocking yfinance HTTP call at a
    # time -- pure I/O wait, so a thread pool collapses wall-clock time to
    # roughly N/workers. Each ticker's fetch is still isolated in try/except
    # (dead-letter resilience) regardless of concurrency. The bounded worker
    # count also serves as the de-facto rate limit, replacing the old serial
    # sleep(0.1)-every-5-tickers throttle in fetch_fundamentals_raw (which only
    # made sense when fetches didn't overlap).
    # Set to 1 to force the original sequential path.
    DATA_FETCH_MAX_CONCURRENCY: int = Field(
        default=8,
        description=(
            "Worker-thread count for DataEngine.fetch_technical_raw()/"
            "fetch_fundamentals_raw() in data_engine.py. 1 = sequential "
            "(original behavior). Results are always reassembled deterministically "
            "by symbol regardless of value."
        ),
    )
    # Worker threads for the SEC EDGAR backfill's per-ticker companyfacts fetch
    # (scripts/backfill_edgar_fundamentals.py). Defaults to 4, LOWER than the
    # DATA_FETCH sibling above, because this is a MEMORY knob, NOT a rate-limit
    # knob: unlike the DATA_FETCH loop, the worker count here does NOT serve as
    # the de-facto rate limit -- edgar_fundamentals._throttle() (a thread-safe
    # 150ms gap) already guarantees SEC's ≤10 req/s limit for ANY worker count.
    # A large filer's parsed companyfacts JSON is 50-150 MB resident, so 8
    # concurrent could hold ~1.2 GB vs ~600 MB at 4. And because json.loads /
    # get_all_filed_dates hold the GIL, only the download wait parallelizes --
    # the speedup is real but sublinear past ~4. Set to 1 for the original
    # sequential path.
    EDGAR_MAX_CONCURRENCY: int = Field(
        default=4,
        description=(
            "Worker-thread count for the SEC EDGAR backfill per-ticker fetch in "
            "scripts/backfill_edgar_fundamentals.py. 1 = sequential. A memory "
            "knob, not a rate-limit knob (the throttle enforces SEC's limit at "
            "any value). Results are reassembled deterministically by ticker."
        ),
    )
    # Refresh cadence (seconds) for the persistent orchestrator daemon's
    # internal timer thread (desktop/daemon_runtime.py). 0 (the default)
    # disables the timer entirely -- the daemon then only runs cycles when
    # explicitly triggered (on-demand via the future command API). The
    # standalone entrypoint's --interval CLI flag overrides this when passed.
    ORCHESTRATOR_INTERVAL_SECONDS: int = Field(
        default=0,
        description=(
            "Seconds between automatic orchestrator daemon cycles. 0 = "
            "on-demand only (no internal timer). Overridable via the "
            "daemon entrypoint's --interval flag."
        ),
    )
    # Cutover flag for the persistent orchestrator daemon (desktop/
    # daemon_runtime.py + desktop/orchestrator_daemon.py + api/control_api.py).
    # False (the default) preserves today's exact behavior everywhere: the
    # desktop shell's always-on refresh loop spawns `main.py --interval N`
    # (gui.orchestrator_runner.launch_scheduled_advisory), and the Launcher
    # tab's manual "Run Pipeline" button spawns a fresh
    # `main_orchestrator.py` subprocess per click. True switches BOTH to the
    # warm daemon: desktop/engine_supervisor.start_engine spawns
    # `python -m desktop.orchestrator_daemon --interval N` instead (still a
    # supervised subprocess -- the warm-engine benefit is entirely internal
    # to that process), and gui.orchestrator_runner.launch_orchestrator()
    # triggers a cycle over the Control API (gui/daemon_client.py) against
    # an already-running daemon instead of spawning a new process, falling
    # back to the old subprocess path if the daemon is unreachable.
    ORCHESTRATOR_DAEMON_ENABLED: bool = Field(
        default=False,
        description=(
            "Route the desktop shell's always-on refresh loop and the "
            "Launcher tab's manual run trigger through the persistent "
            "orchestrator daemon instead of spawning a fresh subprocess per "
            "cycle. False (default) preserves today's exact subprocess "
            "behavior everywhere."
        ),
    )
    SIGNAL_WEIGHTS: dict[str, float] = Field(
        default_factory=lambda: {
            "macro_regime": 45.0,
            "graham_value": 15.0,
            "dividend_quality": 25.0,
            "macd_momentum": 15.0,
            "aroon_trend": 15.0,
            "forecast_alignment": 10.0,
            "relative_strength": 10.0,
            "rsi_extremes": 20.0,
            "sortino_drawdown": 10.0,
            "edge_garch": 35.0,
            "timeseries_momentum": 15.0,
            "cross_sectional_momentum": 15.0,
            "rsi2_mean_reversion": 10.0,
            "multifactor": 15.0,
            # MUST stay 0.0: regime_multiplier carries the HMM second opinion
            # as a position-sizing multiplier (StrategyEngine reads its
            # confidence field directly), not a score contribution -- its
            # compute() always returns score=0.0 regardless of this weight,
            # but the explicit 0.0 here documents and lets Gravity audit the
            # "no directional alpha" invariant structurally.
            "regime_multiplier": 0.0,
            # LightGBM cross-sectional ranker (one ensemble member — modest weight
            # until the model accumulates enough history to earn a larger share).
            "lgbm_ranker": 0.10,
            # News / earnings catalyst (Tier 2.4) — modest weight until the
            # module accumulates a track record (FinBERT or lexicon fallback).
            "news_catalyst": 10.0,
        },
        description="Weights for individual quantitative signal modules."
    )

    # --- Regime-Conditional Signal Weights (Tier 2.1) ---
    # Optional per-regime weight overrides.  When non-empty, SignalAggregator
    # merges these on top of the flat SIGNAL_WEIGHTS for the current macro
    # regime, so e.g. mean-reversion can be boosted in RISK ON and suppressed
    # in RECESSION without touching the default dict.
    #
    # Format (JSON in .env):
    #   REGIME_SIGNAL_WEIGHTS={
    #     "RISK ON":      {"rsi2_mean_reversion": 20.0, "timeseries_momentum": 25.0},
    #     "RECESSION":    {"rsi2_mean_reversion": 0.0, "macro_regime": 60.0},
    #     "CREDIT EVENT": {"rsi2_mean_reversion": 0.0, "macro_regime": 60.0},
    #     "_default":     {}
    #   }
    #
    # Only keys listed in a regime dict are overridden; all other modules
    # keep their SIGNAL_WEIGHTS values.  An empty dict (the project default)
    # preserves the flat-dict behavior exactly — fully backward-compatible.
    # "_default" is used as a catch-all when the current regime has no entry.
    REGIME_SIGNAL_WEIGHTS: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description=(
            "Per-regime signal weight overrides merged onto SIGNAL_WEIGHTS. "
            "Keys are macro regime names ('RISK ON', 'RECESSION', etc.) or "
            "'_default' for catch-all. Empty dict (default) = flat weights for "
            "all regimes (backward-compatible)."
        ),
    )

    # --- Per-Sector Forecast Model/Horizon Config (empirical walk-forward backtest) ---
    # Replaces a hardcoded per-sector forecast-model heuristic in
    # forecasting_engine.py with one derived from an offline walk-forward
    # backtest (see validation/sector_forecast_backtest.py). The backtest writes
    # a committed JSON artifact; ForecastingEngine loads it at init via
    # SECTOR_FORECAST_CONFIG_PATH, with SECTOR_FORECAST_CONFIGS layered on top as
    # an optional per-sector override, falling back to the hardcoded heuristic
    # when both are absent/invalid.
    SECTOR_FORECAST_CONFIG_PATH: Optional[str] = Field(
        default="forecasting/sector_configs.json",
        description=(
            "Path to the committed per-sector forecast config artifact (model+horizon "
            "per sector, derived from an offline walk-forward backtest — see "
            "validation/sector_forecast_backtest.py). Loaded once at ForecastingEngine "
            "init; the hardcoded default dict is used as fallback when the file is "
            "missing or invalid. Offline/deterministic at runtime — no network."
        ),
    )
    SECTOR_FORECAST_CONFIGS: dict[str, dict] = Field(
        default_factory=dict,
        description=(
            "Optional per-sector override merged OVER the artifact/hardcoded default. "
            "JSON dict in .env, e.g. {\"Technology\": {\"days\": 30, \"model\": \"MC\"}}. "
            "Empty dict (the default) leaves the artifact/hardcoded default unchanged "
            "(fully backward-compatible)."
        ),
    )

    # --- Database Backend (db_config.py — dual-backend seam) ---
    # Full SQLAlchemy connection URL. When unset (None), the platform's
    # SQLAlchemy ORM stores (transactions_store, volatility/iv_engine) resolve
    # to the local quant_platform.db SQLite file — today's behavior, unchanged.
    # Set to a postgresql://user:pass@host/db URL to move the trades / iv_history
    # tables to Postgres. May embed credentials — this value is NEVER logged.
    DATABASE_URL: Optional[str] = Field(
        default=None,
        description=(
            "Full SQLAlchemy DB URL (postgresql://… or sqlite:///…). None → local "
            "quant_platform.db. May embed credentials; never logged."
        ),
    )
    DB_POOL_SIZE: int = Field(
        default=5,
        description=(
            "SQLAlchemy connection pool size (Postgres backend only; ignored for SQLite)."
        ),
    )
    DB_MAX_OVERFLOW: int = Field(
        default=10,
        description=(
            "SQLAlchemy pool max overflow connections (Postgres backend only; ignored for SQLite)."
        ),
    )
    # Optional dedicated read-only Postgres DSN for db_config.create_readonly_db_engine().
    # `postgresql_readonly=True` (used when this is unset) is a session GUC any
    # connected client can flip back — defense-in-depth, not a hard boundary. Set
    # this to a DSN authenticating as a RESTRICTED ROLE with no INSERT/UPDATE/
    # DELETE/DDL grants (see db_config.py's create_readonly_db_engine docstring
    # for the CREATE ROLE script) to get a genuine database-ENFORCED read-only
    # boundary, matching SQLite's mode=ro. Only consulted on the Postgres branch;
    # SQLite ignores this (mode=ro is already a hard boundary there). None →
    # today's postgresql_readonly-only behavior, unchanged. May embed
    # credentials; never logged (CONSTRAINT #3).
    MCP_DATABASE_URL_RO: Optional[str] = Field(
        default=None,
        description=(
            "Optional read-only Postgres DSN (a restricted ROLE with no write "
            "grants) for the MCP query surface. None → falls back to "
            "postgresql_readonly=True on the primary DATABASE_URL. Never logged."
        ),
    )

    # --- Historical Persistence (data/historical_store.py, Tier 2.3) ---
    # Gates all DB read/write routing through HistoricalStore.  Setting False
    # reproduces today's behavior exactly — every call goes directly to the
    # live provider without touching the DB.
    HISTORICAL_STORE_ENABLED: bool = Field(
        default=True,
        description=(
            "Master flag for HistoricalStore DB routing. When True, OHLCV bars "
            "and account snapshots are read from / written to quant_platform.db. "
            "First call for a symbol = full BARS_BACKFILL_DAYS backfill; "
            "subsequent calls = delta only. Set False to reproduce pre-Tier-2.3 "
            "behavior (all fetches go directly to the live provider)."
        ),
    )
    BARS_BACKFILL_DAYS: int = Field(
        default=504,
        description=(
            "Number of calendar days to backfill on first-ever fetch for a symbol "
            "(≈ 2 years of trading days). Subsequent fetches are incremental."
        ),
    )
    FUNDAMENTALS_REFRESH_DAYS: int = Field(
        default=1,
        description=(
            "Maximum age (calendar days) of a cached fundamentals row before "
            "HistoricalStore.get_fundamentals() refetches from the provider. "
            "1 = daily refresh. Fundamentals rarely change intraday, so 1 day "
            "is the recommended minimum. Set 0 to always refetch."
        ),
    )
    MACRO_REFRESH_HOURS: int = Field(
        default=12,
        description=(
            "Minimum age (hours) of the most-recent macro_history row before "
            "HistoricalStore.get_macro() triggers a FRED top-up. FRED publishes "
            "VIXCLS daily and T10Y2Y daily; 12 h ensures we top up at most twice "
            "per day while not running stale for longer than half a trading session."
        ),
    )
    PIT_CAPTURE_ENABLED: bool = Field(
        default=True,
        description=(
            "When True, the orchestrator writes TODAY's cross-sectional PIT "
            "feature snapshot to ml/data/cache/ (via ml.data.store.PITFeatureStore) "
            "right after signal pre_compute, so the ML training panel accumulates "
            "real point-in-time snapshots for future incremental retrains. "
            "Dead-lettered: any capture failure is logged and never crashes the "
            "pipeline. Set False to disable forward-going capture entirely."
        ),
    )
    NEWS_HISTORY_CAPTURE_ENABLED: bool = Field(
        default=True,
        description=(
            "When True, NewsCatalystSignal.pre_compute() writes each cycle's "
            "live news-sentiment scores to HistoricalStore's news_history table "
            "(via HistoricalStore.save_news_sentiment()), forward-archiving "
            "real point-in-time history so a genuine backtest becomes possible "
            "after enough history accumulates. No backtest reads this table "
            "yet. Dead-lettered: any capture failure is logged and never "
            "crashes the pipeline. Set False to disable forward-going capture "
            "entirely."
        ),
    )

    # --- Forecast Ensemble Skill Weighting (Tier 2.2) ---
    # Controls the rolling-window RMSE tracker that weights ARIMA / Monte Carlo /
    # Holt-Winters / CNN-LSTM by inverse recent error rather than fixed fractions.
    # Persisted to forecast_errors table in quant_platform.db.
    FORECAST_SKILL_WEIGHTING_ENABLED: bool = Field(
        default=False,
        description=(
            "Opt-in activation of inverse-RMSE skill-weighted multi-model forecast "
            "blending (ARIMA / Monte Carlo / Holt-Winters / CNN-LSTM weighted by "
            "recent realized accuracy via forecasting.forecast_tracker.ForecastTracker). "
            "When False (the default) the static sector-preference blend is used "
            "unchanged — matching the FORECAST_USE_GARCH_SIGMA opt-in convention. "
            "When True, a persistent ForecastTracker is threaded into every "
            "ForecastingEngine construction, self-provisioning its forecast_errors "
            "table in quant_platform.db (no migration required)."
        ),
    )
    FORECAST_SKILL_WINDOW_DAYS: int = Field(
        default=180,
        description=(
            "Rolling window (calendar days) over which per-model RMSE is computed "
            "for inverse-skill forecast blending. Increase for stability; decrease "
            "for faster adaptation. Cold-start equal weighting applies when fewer "
            "than FORECAST_SKILL_MIN_OBS completed observations exist. MUST exceed "
            "the max forecast horizon (90d): a 'completed' row for horizon 90 needs "
            "forecast_ts ≤ now-85d, while the window only counts forecast_ts ≥ "
            "now-WINDOW; with WINDOW=60 those two bands are mutually exclusive so "
            "h=60/h=90 could never warm up. 180 gives every horizon a real "
            "eligibility band."
        ),
    )
    FORECAST_SKILL_MIN_OBS: int = Field(
        default=30,
        description=(
            "Minimum number of completed (actualized) forecast rows required per "
            "model before skill-based weighting activates. Below this threshold, "
            "all models receive equal weight (cold-start fallback)."
        ),
    )

    # --- Macro Regime Gate (execution/risk_gate.py + gui/ Observability tab) ---
    # When True (default), the macro kill-switch check in PreTradeRiskGate blocks
    # all new BUY orders whenever MacroEconomicDTO.killSwitch is True (i.e. Sahm
    # Rule ≥ 0.5 OR VIX > 30 OR credit spread > 6%).  Setting False disables the
    # veto so technical signals can run freely — useful when idiosyncratic
    # volatility triggers a false-positive systemic alarm.
    #
    # WARNING: disabling this gate bypasses recession/credit-event protection.
    # The GUI Observability tab shows a persistent warning banner when it is off.
    # Always re-enable before deploying to live trading (preflight_check.py
    # raises if MACRO_REGIME_GATE_ENABLED=false AND ALPACA_PAPER=false).
    MACRO_REGIME_GATE_ENABLED: bool = Field(
        default=True,
        description=(
            "When True, MacroEconomicDTO.killSwitch vetoes new BUY orders during "
            "RECESSION/CREDIT EVENT regimes. Set False to let technical signals "
            "run without macro override (idiosyncratic-volatility hybrid mode)."
        ),
    )

    # --- Signal module enable/disable (gui/ command center, signals/aggregator.py) ---
    # Names of signal modules that the operator has disabled (e.g. via the GUI
    # Strategy Matrix tab). SignalAggregator.aggregate() skips any module whose
    # name appears here — its weighted contribution is dropped from final_score
    # exactly like a regime-gated module, and it does not affect the
    # meta_label_composite. An empty list (the default) reproduces the legacy
    # behavior where every registered module contributes. Persisted to .env as a
    # JSON array (e.g. DISABLED_SIGNAL_MODULES=["rsi2_mean_reversion"]) so the
    # choice survives across launches and is honored by BOTH orchestrators.
    DISABLED_SIGNAL_MODULES: list[str] = Field(
        default_factory=list,
        description=(
            "Signal module names to exclude from SignalAggregator.aggregate(). "
            "JSON array in .env, e.g. [\"rsi2_mean_reversion\"]. Empty = all active."
        ),
    )

    # --- Pilots (pilots/ package, api/pilots_api.py) ---
    # Stockpy's own signal-module weight-blends packaged as copyable "Pilots".
    # A Pilot's holdings are derived purely from the persisted
    # output/state_snapshot.json signals[] (re-blending each module's raw score
    # under the Pilot's custom weight vector — no engine imports on the read
    # path). PILOTS_TOP_N caps the number of names any single Pilot advertises /
    # mirrors, so both the Pilot-detail holdings list and the gated follow queue
    # stay bounded.
    PILOTS_TOP_N: int = Field(
        default=20,
        description=(
            "Maximum number of top-scoring holdings a single Pilot surfaces "
            "(pilots/scoring.py::pilot_holdings) and mirrors into the gated "
            "follow queue (pilots/mirror.py). Positive scores only, normalized "
            "to target weights before the top-N cut."
        ),
    )
    # Minimum dollar amount the Pilots PWA accepts for a "Follow" allocation.
    # A UX floor surfaced by api/pilots_api.py's POST /pilots/{id}/follow response
    # (min_amount) and enforced client-side by the Follow modal — NOT a broker
    # constraint; the gated queue itself is bounded by ROBINHOOD_MAX_NOTIONAL_PER_ORDER.
    FOLLOW_MIN_AMOUNT: float = Field(
        default=100.0,
        description=(
            "Minimum USD amount accepted for a Pilot follow allocation, surfaced "
            "as `min_amount` in the follow API response and enforced in the PWA "
            "Follow modal. Not a broker constraint."
        ),
    )
    # Master switch for the Pilots API's brokerage-credential intake endpoints
    # (api/pilots_api.py POST /brokerage/connect, /brokerage/disconnect —
    # see data/brokerage_credentials.py). Default False: credential intake over
    # HTTP is a deliberate departure from this project's normal hand-edit-.env
    # posture, so it must be explicitly opted into. Deliberately NOT in
    # gui/env_io.py's ALLOWED_KEYS — a GUI bug must never be able to flip this
    # on; set it by hand in .env. The endpoints are ALSO gated by
    # FOLLOW_API_TOKEN (fail-closed command token, reused from the follow
    # write-path) and a loopback-only check — three independent gates, not one.
    BROKERAGE_CONNECT_ENABLED: bool = Field(
        default=False,
        description=(
            "Enables the Pilots API's brokerage-credential connect/disconnect "
            "endpoints. Off by default; also requires FOLLOW_API_TOKEN and a "
            "loopback (127.0.0.1) request. Never GUI-writable."
        ),
    )
    # Master switch for the Pilots API's Data & Automation WRITE endpoints
    # (api/pilots_api.py PUT /automation/schedule/interval, POST /automation/resume
    # — see the Data & Automation plan). Mirrors BROKERAGE_CONNECT_ENABLED exactly:
    # default False, deliberately NOT in gui/env_io.py's ALLOWED_KEYS (a GUI bug
    # must never be able to flip this on; hand-set in .env only). Deliberately
    # does NOT gate POST /automation/run or POST /automation/pause — those already
    # sit behind require_command_token alone, matching the existing
    # POST /pilots/{id}/follow precedent (which writes an order queue under
    # FOLLOW_API_TOKEN alone, no master flag); gating a run trigger or pause more
    # strictly than the most sensitive endpoint already shipped would invert the
    # risk ordering. Reserved for the two writes with a real persistence/rollback
    # cost: an .env edit and re-enabling live order submission.
    AUTOMATION_WRITES_ENABLED: bool = Field(
        default=False,
        description=(
            "Enables PUT /automation/schedule/interval and POST /automation/resume "
            "on the Pilots API. Off by default; also requires FOLLOW_API_TOKEN. "
            "Never GUI-writable. POST /automation/run and /automation/pause are "
            "NOT gated by this flag (require_command_token alone, matching the "
            "follow write-path's existing risk posture)."
        ),
    )
    # Master switch for the Pilots API's Strategy Matrix WRITE endpoint
    # (api/pilots_api.py PUT /strategy/modules — signal weights + disabled-module
    # set -> .env). A DEDICATED flag, not AUTOMATION_WRITES_ENABLED: that flag was
    # scoped to the daemon interval and kill-switch resume; signal-weight tuning
    # changes WHAT THE PLATFORM RECOMMENDS and must not ride in on it. Mirrors
    # BROKERAGE_CONNECT_ENABLED exactly: default False, deliberately NOT in
    # gui/env_io.py's ALLOWED_KEYS (a GUI bug must never flip it on; hand-set in
    # .env only), and also requires FOLLOW_API_TOKEN. GET /strategy/matrix is
    # read-only and NOT gated by this flag (require_read_token alone, matching
    # GET /brokerage/status).
    STRATEGY_WRITES_ENABLED: bool = Field(
        default=False,
        description=(
            "Enables PUT /strategy/modules on the Pilots API (signal weights + "
            "disabled-module set -> .env). Off by default; also requires "
            "FOLLOW_API_TOKEN. Never GUI-writable — hand-set in .env only, so "
            "signal tuning cannot ride in on AUTOMATION_WRITES_ENABLED."
        ),
    )
    # --- Pilots PWA: persisted analytics artifacts (options matrix + pairs radar) ---
    # The options premium matrix (technical_options_engine) and pairs radar
    # (pairs/ + signals.pairs_trading) are computed live in the Streamlit GUI but
    # persisted nowhere, so the AST-guarded Pilots API (which must never import the
    # heavy engines) cannot surface them. When enabled, the pipeline's
    # StateSnapshotStep writes reporting/options_snapshot.py -> output/options_matrix.json
    # and reporting/pairs_snapshot.py -> output/pairs.json, which the pure
    # pilots.options / pilots.pairs readers then serve. Default OFF so fresh
    # clones / CI are unaffected (mirrors the FORECAST_*_ENABLED opt-in convention).
    OPTIONS_MATRIX_ENABLED: bool = Field(
        default=False,
        description=(
            "When True, the pipeline persists the per-symbol options premium "
            "directive matrix to output/options_matrix.json for the Pilots PWA "
            "(GET /options, GET /symbols/{ticker}/options). Default False."
        ),
    )
    PAIRS_SNAPSHOT_ENABLED: bool = Field(
        default=False,
        description=(
            "When True, the pipeline persists the cointegrated pairs radar "
            "(ranking + current spread state) to output/pairs.json for the "
            "Pilots PWA (GET /pairs). Expensive O(n^2) scan; default False."
        ),
    )
    PAIRS_SNAPSHOT_MAX_PAIRS: int = Field(
        default=20,
        description=(
            "Maximum number of cointegrated pairs persisted to output/pairs.json "
            "by reporting/pairs_snapshot.py (find_cointegrated_pairs max_pairs)."
        ),
    )

    # --- Multifactor signal (signals/multifactor.py) ---
    MULTIFACTOR_MICROCAP_THRESHOLD: float = Field(
        default=300_000_000.0,
        description=(
            "Tickers with Market Cap below this (USD) are excluded from the "
            "cross-sectional z-scoring population in signals/multifactor.py "
            "and receive a neutral (0.0) score rather than fabricated factor "
            "exposure."
        ),
    )

    # --- Meta-labeling (ml/meta_labeling.py) ---
    # Hard gate: if any primary signal's MetaLabeler returns P(correct) below
    # this threshold, SignalAggregator sets meta_label_composite = 0.0, which
    # zeroes the Kelly Target for that cycle. Only applies when a MetaLabeler
    # is registered for that signal in global_meta_registry; default is 1.0
    # (no-op) when no MetaLabeler is registered.
    META_LABEL_MIN_CONFIDENCE: float = Field(
        default=0.4,
        description=(
            "Minimum meta-label probability for a primary signal to contribute "
            "to sizing. If predict_proba < META_LABEL_MIN_CONFIDENCE, the "
            "meta_label_composite is forced to 0.0 (position zeroed for the cycle)."
        ),
    )
    # Master switch for the runtime registration of trained meta-labelers
    # (ml/meta_bootstrap.bootstrap_meta_registry). When True (default), both
    # entry points attempt to load any saved meta-labeler pickle at startup and
    # register it into global_meta_registry so the aggregator's meta_hard_gate
    # can fire. When no saved model exists this is a strict no-op (behavior
    # identical to the pre-meta-label platform). Set to False to disable all
    # meta-label registration regardless of saved models.
    META_LABELING_ENABLED: bool = Field(
        default=True,
        description=(
            "Enable startup registration of trained meta-labelers into "
            "global_meta_registry (ml/meta_bootstrap.py). No-op when no saved "
            "model exists; set False to disable meta-labeling entirely."
        ),
    )

    # --- Snapshot rotation & Δ-band diff (scripts/snapshot_diff.py) ---
    # Each orchestrator/advisory run writes output/state_snapshot.json AND
    # a rotated copy under output/history/state_snapshot_<UTC>.json. The
    # daily HTML report reads the two most-recent rotated snapshots and
    # renders a "Δ Since Last Run" band at the top of the report so the
    # operator sees, at a glance, which signals flipped, which holdings
    # were added/dropped, and which conviction scores moved materially.
    # Rotation pruning, the conviction-delta threshold for "material", and
    # the on-disk history directory name are operator-tunable.
    SNAPSHOT_HISTORY_DAYS: int = Field(
        default=30,
        description=(
            "Rotated state-snapshot files older than this many days are "
            "pruned from OUTPUT_DIR/history on every run. 0 disables pruning."
        ),
    )
    SNAPSHOT_CONVICTION_DELTA_THRESHOLD: float = Field(
        default=0.2,
        description=(
            "Per-symbol conviction (advisory_conviction) deltas with absolute "
            "value at or above this threshold are surfaced in the Δ Since Last "
            "Run band. Smaller moves are suppressed as noise."
        ),
    )

    # --- Symbol watch alerts (watch_engine.py, Tier 1.4) ---
    # Path to the YAML file that defines symbol-watch alert rules.  Evaluated
    # at the end of every run_once() cycle; missing file = no rules (no-op).
    # Rule types: action_change, conviction_above, conviction_below.
    # See watch_rules.yaml at the project root for the full schema.
    WATCH_RULES_FILE: str = Field(
        default="watch_rules.yaml",
        description=(
            "Path to watch_rules.yaml.  Defines per-symbol ntfy push-alert "
            "rules (action_change, conviction_above, conviction_below).  "
            "Missing file = no rules active (silent no-op)."
        ),
    )

    # --- Rationale verbosity (engine/advisory.py, Task 1.5) ---
    # Controls how much narrative detail the per-symbol advisory rationale
    # produces.  Standard mode (the default) is a single terse paragraph
    # citing the top 2-3 drivers — suitable for dashboards and notifications.
    # Verbose mode appends four labelled sections:
    #   [A] Regime context — HMM probability + FRED macro snapshot
    #   [B] Historical calibration — strategy win-rate and Kelly edge estimate
    #   [C] Signal invalidation thresholds — the conditions that void the
    #       current recommendation (RSI flip points, macro gates, sector veto)
    #   [D] Indicator theory notes — first-line __doc__ of each active
    #       signal module (pulled dynamically from signals.registry)
    # Valid values: "standard" (default) | "verbose"
    RATIONALE_VERBOSITY: str = Field(
        default="standard",
        description=(
            "Advisory rationale depth. 'standard' = top 2-3 driver paragraph "
            "(default). 'verbose' = adds regime context [A], historical "
            "calibration [B], invalidation thresholds [C], and indicator "
            "theory notes [D]. Set RATIONALE_VERBOSITY=verbose in .env."
        ),
    )

    # --- News Catalyst Signal (Tier 2.4, signals/news_catalyst.py) ---
    # Controls how far back to pull Finnhub company_news headlines and
    # whether to use the FinBERT neural sentiment scorer (requires
    # `pip install transformers` and either PyTorch or TensorFlow).
    # When FINBERT_ENABLED=false or transformers is unavailable, a curated
    # 80-word financial keyword lexicon is used instead — no accuracy loss
    # on very short headlines, ~10-15% worse on multi-sentence summaries.
    NEWS_LOOKBACK_DAYS: int = Field(
        default=7,
        description=(
            "Calendar days of Finnhub company_news headlines to score per "
            "symbol per pre_compute cycle. Longer windows add latency; the "
            "free Finnhub tier provides ~3 months of history."
        ),
    )
    FINBERT_ENABLED: bool = Field(
        default=True,
        description=(
            "When True and `transformers` is installed, uses ProsusAI/FinBERT "
            "for headline sentiment.  When False (or transformers unavailable), "
            "falls back to the built-in keyword lexicon.  Set False to avoid "
            "the ~200 MB model download on first use."
        ),
    )

    # --- Tier 9: Claude + Gemini commentary integration (llm/) ---
    # Master switch.  When False (the default) the platform behaves byte-
    # identically to pre-Tier-9: ZERO SDK imports, ZERO network calls, the
    # deterministic template rationale and alert text remain the single SoT.
    # CONSTRAINT: API keys live in SECRET_KEYS (gui/env_io.SECRET_KEYS); the
    # toggles below live in ALLOWED_KEYS so the Strategy Matrix tab can flip
    # them without ever touching a credential.  CONSTRAINT #3.
    LLM_COMMENTARY_ENABLED: bool = Field(
        default=False,
        description=(
            "Tier 9 master switch.  When True AND the relevant provider key "
            "is set, on-demand LLM commentary is generated by the CLI and "
            "alert dispatchers.  evaluate() never calls an LLM in-cycle; "
            "cadence is on-demand only (CLI + GUI button)."
        ),
    )
    LLM_COMMENTARY_RATIONALE_PROVIDER: str = Field(
        default="claude",
        description=(
            "Provider for analyst rationale generation.  'claude' (default), "
            "'gemini', or 'none' (disable rationale LLM regardless of master "
            "switch).  Either provider works for either job — this and "
            "LLM_COMMENTARY_ALERT_PROVIDER are independent, operator-chosen."
        ),
    )
    LLM_COMMENTARY_ALERT_PROVIDER: str = Field(
        default="gemini",
        description=(
            "Provider for alert commentary generation.  'gemini' (default), "
            "'claude', or 'none' (disable alert LLM regardless of master "
            "switch).  Either provider works for either job — this and "
            "LLM_COMMENTARY_RATIONALE_PROVIDER are independent, operator-chosen."
        ),
    )
    LLM_COMMENTARY_CACHE_PATH: str = Field(
        default="output/llm_commentary_cache.json",
        description=(
            "JSON cache for LLM commentary results.  Day-bucketed; safe to "
            "delete manually.  Lives under output/ which is gitignored."
        ),
    )
    LLM_COMMENTARY_TIMEOUT_SECONDS: int = Field(
        default=8,
        description=(
            "Hard wall-clock timeout per provider call.  Exceeding it counts "
            "as a soft failure (returns None; caller falls back to template)."
        ),
    )
    LLM_STATUS_MAX_AGE_HOURS: float = Field(
        default=24.0,
        description=(
            "Age bound for TRANSIENT last-call verdicts (rate_limit / network / "
            "timeout / schema / unknown) recorded in output/llm_status.json by "
            "llm/status_store.py.  Past this many hours a transient verdict is "
            "reported with source='expired' and never claimed as current.  "
            "Deliberately does NOT bound 'auth' or 'ok' verdicts — those are "
            "properties of the KEY and are invalidated by a key change "
            "(fingerprint mismatch), not by the clock."
        ),
    )
    ANTHROPIC_API_KEY: Optional[str] = Field(
        default=None,
        description=(
            "Anthropic API key for the Claude provider.  Required whenever "
            "either LLM_COMMENTARY_RATIONALE_PROVIDER or "
            "LLM_COMMENTARY_ALERT_PROVIDER is set to 'claude'.  Unset → that "
            "job's LLM disabled, template fallback kicks in."
        ),
    )
    GEMINI_API_KEY: Optional[str] = Field(
        default=None,
        description=(
            "Google AI Studio key for the Gemini provider.  Required whenever "
            "either LLM_COMMENTARY_RATIONALE_PROVIDER or "
            "LLM_COMMENTARY_ALERT_PROVIDER is set to 'gemini' (also used for "
            "chart-pattern vision).  Unset → that job's LLM disabled, "
            "template fallback kicks in."
        ),
    )

    # --- Tier 9 / Scope 4: Opal research agent (llm/research.py, OpenAI/GPT) ---
    # A separate, independent opt-in master switch from LLM_COMMENTARY_ENABLED —
    # Opal's front-of-pipeline research brief can run without per-symbol
    # commentary enabled, and vice versa. Default False: zero `openai` SDK
    # reach and zero network calls when off (CONSTRAINT #6 opt-in contract).
    OPAL_RESEARCH_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for Opal, the OpenAI/GPT front-of-pipeline research "
            "agent (Tier 9 Scope 4).  Off by default — zero `openai` import and "
            "zero network calls when False.  When True AND OPENAI_API_KEY is "
            "set, generate_research_brief() produces a grounded, qualitative "
            "ResearchBrief threaded into the Claude rationale prompt."
        ),
    )
    OPAL_RESEARCH_PROVIDER: str = Field(
        default="openai",
        description=(
            "Provider for Opal research-brief generation.  'openai' (default), "
            "'gemini', or 'none' (disable regardless of the master switch).  "
            "Requires the matching API key (OPENAI_API_KEY or GEMINI_API_KEY)."
        ),
    )
    OPAL_RESEARCH_MODEL: str = Field(
        default="gpt-4o",
        description=(
            "Model name for Opal's structured-output research brief calls, "
            "interpreted per the active OPAL_RESEARCH_PROVIDER (an OpenAI model "
            "name when 'openai', a Gemini model name when 'gemini').  Left at "
            "the OpenAI-flavored default, a 'gemini' provider choice falls back "
            "to GeminiProvider's own model default instead of using this value."
        ),
    )
    OPAL_RESEARCH_TIMEOUT_SECONDS: int = Field(
        default=15,
        description=(
            "Hard wall-clock timeout per OpenAIProvider call.  Exceeding it "
            "counts as a soft failure (returns None; caller skips Opal for "
            "this cycle)."
        ),
    )
    OPENAI_API_KEY: Optional[str] = Field(
        default=None,
        description=(
            "OpenAI API key for the Opal research agent.  Unset → Opal "
            "disabled, no research brief generated (byte-identical to today)."
        ),
    )

    # --- Tier 9 / Scope 2: Gravity AI audit runner (engine/gravity_ai_runner.py) ---
    # A separate opt-in master switch from LLM_COMMENTARY_ENABLED so an
    # operator can run on-demand AI audits (uses both Claude + Gemini) without
    # having to also enable per-symbol rationale commentary.  Default False:
    # the existing Python-only Gravity steps in `Gravity AI Review Suite.py`
    # continue to run unchanged.  When True AND both API keys are set, the CLI
    # `python -m engine.gravity_ai_runner [STEP]` calls Claude as the primary
    # auditor and Gemini as the cross-checker; both responses are validated
    # against `llm.schemas.GravityAuditStepResult` and disagreement on
    # status is surfaced explicitly (the runner never picks a winner).
    GRAVITY_AI_RUNNER_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for the AI Gravity audit runner (Claude auditor + "
            "Gemini cross-checker).  Off by default — the existing Python-only "
            "Gravity audit pipeline is unchanged when False.  When True, on-"
            "demand CLI runs both models against the 7 audit prompts in "
            "ai_verification_prompts.py and writes output/gravity_ai_audit.json."
        ),
    )
    GRAVITY_AI_RUNNER_OUTPUT_PATH: str = Field(
        default="output/gravity_ai_audit.json",
        description=(
            "Where the runner writes the per-step Claude + Gemini verdicts.  "
            "Lives under output/ which is gitignored."
        ),
    )
    NEWS_EARNINGS_SUPPRESS_HOURS: float = Field(
        default=48.0,
        description=(
            "Hours before next earnings date within which the news catalyst "
            "score is forced to 0.0.  Pre-earnings headlines are unreliable "
            "catalysts — the outcome isn't observable yet."
        ),
    )
    NEWS_EARNINGS_DAMPEN_DAYS: float = Field(
        default=7.0,
        description=(
            "Days before next earnings within which the news catalyst score "
            "is multiplied by 0.5 (50% dampening).  Beyond this window the "
            "full score is used."
        ),
    )

    # --- Correlation Cluster Awareness (Tier 2.5, research_engine.py) ---
    # Controls the on-demand hierarchical clustering computed in the GUI
    # Reports tab.  These settings are read by the GUI; the orchestrator
    # does NOT run cluster analysis automatically (on-demand only).
    CORRELATION_CLUSTER_LOOKBACK_DAYS: int = Field(
        default=60,
        description=(
            "Calendar days of daily returns used to build the pairwise "
            "correlation matrix for hierarchical clustering. 60 days ≈ 3 "
            "months, enough to capture a medium-term co-movement regime."
        ),
    )
    CORRELATION_CLUSTER_THRESHOLD: float = Field(
        default=0.4,
        description=(
            "Dendrogram cut-distance for cluster assignment.  Uses the "
            "Lopez de Prado distance d=sqrt(0.5*(1-rho)).  At 0.4, stocks "
            "with |correlation| > 0.68 merge into the same cluster.  "
            "Lower = tighter (fewer, smaller clusters); higher = looser."
        ),
    )

    # --- Dual Momentum allocator overlay ---
    USE_DUAL_MOMENTUM_OVERLAY: bool = Field(
        default=False,
        description=(
            "When True, the Dual Momentum allocator pre-screens the ticker list each "
            "run. If the allocator selects the safe asset (BIL), tickers in the risky "
            "universes (SPY, VEU) have their Kelly Target set to 0.0."
        ),
    )
    DUAL_MOMENTUM_SAFE_ASSET: str = Field(
        default="BIL",
        description="Ticker used as the safe/defensive asset in the Dual Momentum overlay.",
    )
    DUAL_MOMENTUM_RISKY_ASSETS: list[str] = Field(
        default_factory=lambda: ["SPY", "VEU"],
        description="Risky ETFs compared in the Dual Momentum cross-sectional filter.",
    )

    # ── Prompt Registry (prompt_registry/ package) ───────────────────────────
    # Versioned, cryptographically-signed, remotely-updatable store for every
    # AI-facing instruction.  Default: disabled (baseline-only, zero network).
    # See docs/PROMPT_REGISTRY_PLAN.md §8 for the full security model.
    #
    # CONSTRAINT #3 — the four credential fields are Optional[str] secrets:
    # they are masked by gui/env_io.read_settings() and raise SecretWriteError
    # on any GUI write attempt.  Only ENABLED / BACKEND / PINS are in
    # ALLOWED_KEYS (GUI-writable tunables).

    PROMPT_REGISTRY_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch.  False (default) → baseline-only, zero network calls. "
            "Set True to enable remote manifest fetch and cache."
        ),
    )
    PROMPT_REGISTRY_BACKEND: str = Field(
        default="http",
        description=(
            "Storage backend: 'http' (default, protected HTTPS endpoint), "
            "'local' (LocalJSONStore from a file path), or 'firestore' (lazy import)."
        ),
    )
    PROMPT_REGISTRY_URL: Optional[str] = Field(
        default=None,
        description=(
            "HTTPS URL of the protected registry manifest endpoint "
            "(e.g. a private GitHub raw URL or S3 presigned object).  "
            "SECRET — never GUI-writable, never logged."
        ),
    )
    PROMPT_REGISTRY_TOKEN: Optional[str] = Field(
        default=None,
        description=(
            "Bearer token sent as Authorization header to PROMPT_REGISTRY_URL.  "
            "Read-only credential; the publish token is separate.  "
            "SECRET — never GUI-writable, never logged."
        ),
    )
    PROMPT_REGISTRY_PUBLISH_TOKEN: Optional[str] = Field(
        default=None,
        description=(
            "Higher-privilege credential required by 'python -m prompt_registry publish'. "
            "The platform runtime never needs this.  "
            "SECRET — never GUI-writable, never logged."
        ),
    )
    PROMPT_REGISTRY_SIGNING_KEY: Optional[str] = Field(
        default=None,
        description=(
            "Shared HMAC-SHA256 key used by signing.verify() to authenticate every "
            "fetched prompt version.  A failed verification falls through to the "
            "disk cache → committed baseline (fail-closed).  "
            "SECRET — never GUI-writable, never logged."
        ),
    )
    PROMPT_REGISTRY_PINS: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "JSON object mapping prompt IDs to pinned version strings "
            '(e.g. {"master_preprompt": "1.2.0"}).  '
            "Overrides the remote \'latest\' pointer for each pinned ID.  "
            "GUI-writable from the Prompts tab (ALLOWED_KEYS); persisted to .env "
            "via gui/env_io.write_setting."
        ),
    )
    PROMPT_REGISTRY_REFRESH_SECONDS: int = Field(
        default=0,
        description=(
            "0 (default) = fetch only at launch / on explicit sync() call "
            "(CONSTRAINT #5 — no always-on daemon).  "
            "Positive value: long-running processes may re-sync on this interval."
        ),
    )
    PROMPT_CACHE_DIR: str = Field(
        default="output/prompt_cache",
        description=(
            "Directory for the signed-version disk cache.  "
            "Each prompt ID gets a sub-directory; up to PROMPT_CACHE_KEEP_VERSIONS "
            "signed .json files are kept per ID for offline rollback."
        ),
    )
    PROMPT_CACHE_KEEP_VERSIONS: int = Field(
        default=5,
        description=(
            "Number of signed versions to retain on disk per prompt ID.  "
            "Older versions are pruned by CacheManager.write() so rollback works "
            "offline up to this depth."
        ),
    )
    PROMPT_MAX_CHARS: int = Field(
        default=50_000,
        description=(
            "Hard upper bound on prompt body size enforced by guardrails.validate_prompt(). "
            "Bodies exceeding this are rejected as a denial-of-service mitigation."
        ),
    )

    @field_validator("OUTPUT_DIR")
    @classmethod
    def _ensure_output_dir(cls, value: Path) -> Path:
        """Coerce to ``Path`` and create the directory if it does not exist."""
        path = Path(value)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @field_validator("ROBINHOOD_EXECUTION_MODE")
    @classmethod
    def _coerce_robinhood_mode(cls, value: str) -> str:
        """Fail-safe: any value outside {off, review, live} collapses to ``off``.

        A typo, stale env value, or injection can never accidentally arm
        execution — the worst case is the inert default.
        """
        v = str(value or "").strip().lower()
        return v if v in {"off", "review", "live"} else "off"

    @field_validator("SECTOR_FORECAST_CONFIGS")
    @classmethod
    def _validate_sector_forecast_configs(cls, value: dict) -> dict:
        """Fail-safe: drop any entry that doesn't validate. A malformed override can
        never corrupt the engine — worst case is the artifact/hardcoded default is
        used for that sector instead.

        NOTE: ``validation/sector_config_io.py`` (owned by a concurrently-authored
        agent) supplies the real ``validate_sector_config_entry`` normalizer. This
        import is deliberately inside the function body (not module top) so a
        missing/broken validation package can never crash settings.py's own
        import — the except branch below treats the override as empty in that
        case. End-to-end integration against the real sector_config_io.py is
        exercised by a separate cross-cutting test outside this module's test
        file.
        """
        try:
            from validation.sector_config_io import validate_sector_config_entry
        except Exception:
            # validation package unavailable/broken — never let a settings import
            # crash the whole process; treat the override as empty.
            return {}
        cleaned: dict = {}
        for sector, raw in (value or {}).items():
            entry = validate_sector_config_entry(raw)
            if entry is not None:
                cleaned[sector] = entry
        return cleaned

    @property
    def fred_key_is_leaked(self) -> bool:
        """True if the configured FRED key is the known-compromised value.

        Compared by SHA-256 digest so the leaked literal is never stored here.
        """
        return bool(self.FRED_API_KEY) and _sha256(self.FRED_API_KEY) == LEAKED_FRED_KEY_SHA256

    def ensure_fred_configured(self) -> None:
        """Raise a clear error if no FRED API key is configured.

        Call this on the live data path before constructing a real DataEngine.
        """
        if not self.FRED_API_KEY:
            raise RuntimeError(
                "FRED_API_KEY is not configured. Set it as an environment variable "
                "or in a local .env file (see .env.example). "
                f"Obtain a free key at {FRED_ROTATION_URL}"
            )

    def warn_if_fred_key_leaked(self, log: logging.Logger = logger) -> bool:
        """Emit a CRITICAL warning if the configured key is the leaked one.

        Returns True when the leaked key was detected.
        """
        if self.fred_key_is_leaked:
            log.critical(
                "FRED_API_KEY matches the previously leaked, hardcoded value and is "
                "COMPROMISED. Rotate it immediately at %s and update your .env file.",
                FRED_ROTATION_URL,
            )
            return True
        return False


# Module-level singleton imported across the platform.
settings = Settings()
