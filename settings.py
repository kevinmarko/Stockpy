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

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict  # type: ignore

logger = logging.getLogger(__name__)

# Repo-root anchor for `.env` resolution — the SINGLE source of truth every
# other `.env` locator in the codebase must import instead of re-deriving.
# Before this, three independent mechanisms disagreed with each other:
#   (1) pydantic-settings' own `env_file=".env"` below, resolved against the
#       process CWD;
#   (2) a bare `load_dotenv()` call (main.py / main_orchestrator.py /
#       app_shell.py), which uses python-dotenv's `find_dotenv()` and walks
#       UP from the calling file's directory to filesystem root — in a git
#       worktree with no `.env` of its own, this silently finds a PARENT
#       checkout's `.env` instead;
#   (3) gui/env_io.py and data/brokerage_credentials.py, which each
#       independently re-derived a repo-root-anchored path.
# Anchoring all three to this one constant makes `.env` resolution identical
# regardless of CWD or worktree, and makes it impossible for a stray
# `load_dotenv()` to reach across into a sibling checkout.
ENV_PATH = Path(__file__).resolve().parent / ".env"

# A FRED API key was previously hardcoded in main.py / main_orchestrator.py and
# committed to git history. If the live key still equals that value it is
# compromised and MUST be rotated. We store only the SHA-256 digest of the leaked
# key (never the literal) so the platform can detect reuse without re-embedding
# the secret anywhere in the source tree.
LEAKED_FRED_KEY_SHA256 = "d18938214ce633f15694ee7d77ecf69f5ea7654615c478f5f37b968dd7e8824e"
FRED_ROTATION_URL = "https://fred.stlouisfed.org/docs/api/api_key.html"


def _sha256(value: str) -> str:
    # Not password/credential storage or verification -- this is a
    # known-leaked-value canary check (see LEAKED_FRED_KEY_SHA256 above):
    # the digest is only ever compared against ONE specific, already-public,
    # already-compromised plaintext to detect whether an operator's env
    # var still equals it. A slow/salted password hash (bcrypt/scrypt/
    # Argon2) defends a *secret* value against offline brute force; there is
    # no secret being protected here (the attacker, by definition, already
    # has the leaked plaintext), so SHA-256 is the right tool, not a
    # weakness. CodeQL's py/weak-sensitive-data-hashing query flags this
    # call anyway (alert #5) because it classifies `FRED_API_KEY` as
    # sensitive/credential-like data purely by name -- reviewed false
    # positive.
    # codeql[py/weak-sensitive-data-hashing]
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


# Bounds for settings.DAEMON_SHUTDOWN_TIMEOUT_SECONDS -- see that field's
# docstring for the full shutdown-budget ladder this bounds the TOP of.
# 0 is rejected deliberately: it would make every join/poll instant, i.e. an
# unconditional SIGKILL-equivalent, which is the opposite of this setting's
# purpose. 120s is a generous ceiling -- past that, "graceful shutdown" has
# stopped being meaningfully different from "just SIGKILL it".
DAEMON_SHUTDOWN_TIMEOUT_MIN_SECONDS = 1.0
DAEMON_SHUTDOWN_TIMEOUT_MAX_SECONDS = 120.0


def validate_daemon_shutdown_timeout(v: float) -> float:
    """Shared validation for settings.DAEMON_SHUTDOWN_TIMEOUT_SECONDS.

    Mirrors validate_interval_seconds's shape (plain ValueError, reusable
    both inside a pydantic field_validator and from a plain setter).
    """
    if not (DAEMON_SHUTDOWN_TIMEOUT_MIN_SECONDS <= v <= DAEMON_SHUTDOWN_TIMEOUT_MAX_SECONDS):
        raise ValueError(
            f"DAEMON_SHUTDOWN_TIMEOUT_SECONDS must be in "
            f"[{DAEMON_SHUTDOWN_TIMEOUT_MIN_SECONDS}, {DAEMON_SHUTDOWN_TIMEOUT_MAX_SECONDS}], got {v}"
        )
    return v


class Settings(BaseSettings):
    """Single source of truth for runtime configuration.

    Values are resolved (in precedence order) from: explicit init kwargs,
    environment variables, then a local ``.env`` file, then the defaults below.
    Field names are case-insensitive (``FRED_API_KEY`` / ``fred_api_key``).
    """

    model_config = SettingsConfigDict(
        env_file=ENV_PATH,
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
    #   4.  Robinhood — portfolio login ........ RH_USERNAME/PASSWORD, device-approval login timeouts
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
    #   25. Financial Modeling Prep ............ key, client tuning, 8 feed gates
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
    FRED_REQUEST_TIMEOUT_SECONDS: float = Field(
        default=10.0,
        description=(
            "Per-request socket timeout (seconds), scoped narrowly via "
            "socket.setdefaulttimeout() around DataEngine's fredapi calls "
            "(fetch_macro_raw_detailed/fetch_macro_history) -- "
            "fredapi.Fred.get_series() calls a bare urlopen() with no timeout "
            "parameter and no session-injection hook (confirmed against the "
            "installed library source), so a stalled FRED connection used to "
            "block forever with no way to recover (see "
            "docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md). "
            "Mirrors FMP_TIMEOUT_SECONDS' per-request scope, NOT "
            "FMP_MAX_SECONDS_PER_CYCLE's whole-cycle budget -- "
            "fetch_macro_history() issues 8 series calls, so its worst case is "
            "8x this value."
        ),
    )
    ALPACA_API_KEY: Optional[str] = Field(default=None, description="Alpaca API key (optional).")
    ALPACA_SECRET_KEY: Optional[str] = Field(default=None, description="Alpaca secret key (optional).")
    ALPACA_PAPER: bool = Field(default=True, description="Use Alpaca paper-trading endpoint.")

    FMP_PAPER_STARTING_CASH: float = Field(
        default=100000.0,
        description="Starting cash balance seeded into a fresh FMPPaperBroker "
        "virtual account (data/paper_account_store.py) the first time it's "
        "constructed. Only takes effect when BROKER_BACKEND='fmp_paper'.",
    )
    BROKER_BACKEND: str = Field(
        default="fmp_paper",
        description="Selects the active broker backend in main_orchestrator.py's "
        "_execute_broker_orders ('alpaca' or 'fmp_paper' — see "
        "execution/fmp_paper_broker.py). Defaults to 'fmp_paper'. "
        "main_orchestrator.py includes a runtime force-fallback guard "
        "(execution/broker_selection.py::resolve_broker_backend) that forces "
        "'alpaca' if 'fmp_paper' is used while the run is genuinely going live "
        "(ADVISORY_ONLY=False and ALPACA_PAPER=False), and "
        "check_broker_backend_matches_live_intent in scripts/preflight_check.py "
        "blocks starting the pipeline in that same configuration. 'robinhood' is "
        "a documented-but-not-yet-implemented future value reserved for an "
        "eventual RobinhoodBroker — any unrecognized value (including "
        "'robinhood' today) falls through to 'alpaca'; see "
        "docs/architecture/execution.md's 'Future extension point — automated "
        "Robinhood execution (not implemented)' section.",
    )
    MULTI_BROKER_GATEWAY_ENABLED: bool = Field(
        default=False,
        description=(
            "When True, broker_live_execution_mcp.py::_get_broker() routes live-order "
            "MCP calls through execution.multi_broker_gateway.MultiBrokerGateway "
            "(health monitoring, latency tracking, automated circuit-breaker "
            "failover across the configured broker adapters) instead of "
            "execution.broker_selection.resolve_broker_backend()'s single-broker "
            "resolution. False (default) preserves today's exact single-broker "
            "behavior; falls back to resolve_broker_backend() if the gateway has "
            "no active adapter or raises."
        ),
    )
    PAPER_BROKER_WRITES_ENABLED: bool = Field(
        default=True,
        description=(
            "Gates every write/execution endpoint on the options desk's paper "
            "broker (api/pilots_api.py's require_paper_broker_writes_enabled "
            "dependency, alongside the command token on each route): "
            "POST /pilots/paper-broker/reset, /brokerage/options/order, "
            "/pilots/paper-broker/strategy-options/execute, "
            "/pilots/paper-broker/manage-exits, /pilots/paper-broker/roll, "
            "/pilots/paper-broker/delta-hedge/execute, "
            "/pilots/options/meta-model/retrain, "
            "/pilots/paper-broker/settle-expired, "
            "/pilots/options/earnings-crush/execute, "
            "/pilots/options/mispricing/execute, "
            "/pilots/options/dispersion/execute, "
            "/pilots/options/zero-dte/execute, and "
            "/pilots/options/0dte/manage-exits. If False, all of these are blocked."
        ),
    )
    PAPER_OPTIONS_AUTO_EXECUTE_ENABLED: bool = Field(
        default=False,
        description="Automatically execute valid options strategy directives into the paper broker every cycle.",
    )
    PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED: bool = Field(
        default=False,
        description=(
            "Bridge each PaperAccountStore closed trade into the real transactions_store "
            "'trades' ledger (via record_trade+close_trade), so sizing.kelly and "
            "evaluation_engine's MAE/MFE/calibration warm up on simulated paper fills. "
            "Defaults False: 'trades' has no paper/live discriminator column, and it feeds "
            "strategy_engine.py, main_orchestrator.py, pilots/mirror.py, and MCP reporting "
            "tools -- mixing simulated PnL into that ledger by default would be a silent "
            "data-integrity change to what those consumers report as real performance."
        ),
    )
    MAX_OPTION_NOTIONAL_PER_TRADE: float = Field(
        default=2500.0,
        description="Max risk notional collateral per automated options paper trade.",
    )
    MAX_CONCURRENT_OPTION_POSITIONS: int = Field(
        default=10,
        description="Max total concurrent open option positions in the paper broker.",
    )
    OPTIONS_META_LABELER_ENABLED: bool = Field(
        default=True,
        description="Enable Stage 4 ML meta-labeling for automated options trade gating and sizing.",
    )
    OPTIONS_RISK_FREE_RATE: float = Field(
        default=0.045,
        description="Annualized risk-free interest rate for options pricing and Greeks calculation.",
    )
    OPTIONS_AUTO_EXIT_ENABLED: bool = Field(
        default=False,
        description="Automatically manage and exit option positions on profit target, stop loss, or DTE threshold.",
    )
    OPTIONS_PROFIT_TARGET_PCT: float = Field(
        default=0.50,
        description="Profit target percentage threshold to trigger automated exit (e.g. 0.50 for 50% max profit).",
    )
    OPTIONS_STOP_LOSS_MULTIPLE: float = Field(
        default=2.0,
        description="Stop loss multiple of max credit/debit to trigger automated exit (e.g. 2.0 for 200% loss).",
    )
    OPTIONS_MANAGE_DTE_THRESHOLD: int = Field(
        default=21,
        description="DTE threshold at or below which options positions are proactively closed/rolled (e.g. 21 days).",
    )
    OPTIONS_DELTA_HEDGE_ENABLED: bool = Field(
        default=False,
        description="Enable automatic dynamic SPY delta hedging for options paper portfolio.",
    )
    OPTIONS_DELTA_HEDGE_BAND_SPY_SHARES: float = Field(
        default=25.0,
        description="Deadband threshold in SPY delta shares before triggering a dynamic delta hedge order.",
    )
    OPTIONS_EARNINGS_CRUSH_ENABLED: bool = Field(
        default=False,
        description="Enable automated pre-earnings volatility crush option trading.",
    )
    OPTIONS_EARNINGS_MIN_EDGE: float = Field(
        default=1.25,
        description="Minimum ratio of implied move over historical median realized move to qualify for earnings crush trade.",
    )
    OPTIONS_EARNINGS_WING_MULTIPLIER: float = Field(
        default=1.20,
        description="Multiplier on expected move to set outer wings for earnings crush Iron Condors.",
    )
    OPTIONS_ALERT_WEBHOOK_URL: Optional[str] = Field(
        default=None,
        description="Dedicated webhook URL for real-time options alerts (UOA whale sweeps, earnings crush, delta hedging).",
    )
    OPTIONS_0DTE_ENABLED: bool = Field(
        default=False,
        description="Enable automated 0DTE options momentum breakout trading and lifecycle management.",
    )
    OPTIONS_0DTE_PROFIT_TARGET_PCT: float = Field(
        default=0.75,
        description="Profit target percentage threshold to trigger 0DTE exit (e.g. 0.75 for +75% gain in premium).",
    )
    OPTIONS_0DTE_STOP_LOSS_PCT: float = Field(
        default=0.30,
        description="Stop loss percentage threshold to trigger 0DTE exit (e.g. 0.30 for -30% loss).",
    )
    OPTIONS_0DTE_HARD_EXIT_TIME: str = Field(
        default="15:45",
        description="Mandatory hard exit time (ET, HH:MM) to close all open 0DTE positions and avoid pin/settlement risk.",
    )
    OPTIONS_DRL_RISK_AVERSION_GAMMA: float = Field(
        default=0.10,
        description=(
            "Avellaneda-Stoikov (2008) absolute risk-aversion parameter gamma for "
            "ml/drl_market_maker.py's DRL/AS market-making simulation engine — "
            "controls inventory-skew strength in the reservation price R(s,q,t) = "
            "s - q*gamma*sigma^2*(T-t) and the optimal quoting half-spread. "
            "Matches this module's prior hardcoded DEFAULT_GAMMA default."
        ),
    )
    OPTIONS_VPIN_TOXICITY_THRESHOLD: float = Field(
        default=0.35,
        description=(
            "pilots/options_vpin.py's VPIN (Volume-Synchronized Probability of "
            "Informed Trading / Toxicity) toxicity gating threshold from the "
            "Easley/Lopez de Prado/O'Hara literature — VPIN above this value is "
            "classified HIGH_TOXICITY (vs. LOW/MODERATE) and triggers defensive "
            "spread-widening via apply_defensive_spread_concession(). Promoted "
            "from the module's prior hardcoded DEFAULT_TOXICITY_THRESHOLD."
        ),
    )
    OPTIONS_SOR_LEGGING_LATENCY_SECONDS: float = Field(
        default=2.0,
        description=(
            "pilots/options_sor.py's simulate_legging_execution() assumed "
            "inter-leg execution latency (seconds) between the passive leg "
            "filling and the active leg completing — drives the Monte Carlo "
            "spot-drift window (dt_years) underlying every hung-leg-probability "
            "and adverse-selection-cost estimate in the legging hazard "
            "simulator. Promoted from the function's prior hardcoded "
            "latency_seconds=2.0 default."
        ),
    )
    OPTIONS_LOB_DEFAULT_MARKET_ORDER_RATE: float = Field(
        default=5.0,
        description=(
            "pilots/lob_simulator.py's DEFAULT_MARKET_ORDER_RATE — the Cont/Stoikov/"
            "Talreja (2010) market-order Poisson arrival rate theta (orders/sec) used "
            "as the default/fallback across calculate_cont_stoikov_fill_probability(), "
            "evaluate_optimal_queue_level(), and simulate_queue_fill() (the live "
            "POST /pilots/options/lob/simulate-queue resolver) whenever a caller "
            "doesn't supply an empirically-measured rate from "
            "compute_lob_arrival_rates(). Promoted from the module's prior "
            "hardcoded DEFAULT_MARKET_ORDER_RATE = 5.0 default."
        ),
    )
    OPTIONS_GEX_SEARCH_RANGE_PCT: float = Field(
        default=0.20,
        description=(
            "pilots/options_gex.py's calculate_zero_gamma_flip() relative search "
            "radius (+/- pct of spot) for the initial Brent's-method/bisection "
            "bracket used to solve for the Zero-Gamma Flip Point (S*) — the spot "
            "price where aggregate dealer Net GEX crosses zero. Directly "
            "determines whether zero_gamma_flip/distance_to_flip_pct come back "
            "populated or None for a given chain (a search range too narrow for "
            "a symbol's actual OI distribution silently degrades to 'no flip "
            "found' before the function's own secondary +/-40-60% expanded-grid "
            "fallback ever engages). Promoted from the module's prior hardcoded "
            "DEFAULT_SEARCH_RANGE_PCT default; pure promotion, not a behavior "
            "change."
        ),
    )



    LIVE_TRADE_EXECUTION_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for broker_live_execution_mcp.py's execute_live_trade/"
            "confirm_live_trade tool pair — the standalone MCP server that places "
            "real Alpaca/FMP orders. Defaults False: this changes what the "
            "platform can do with real capital, so it does not follow the "
            "2026-08-03 'new admin capabilities default True' convention (which "
            "explicitly excludes anything changing trading behavior) — it "
            "follows BROKER_BACKEND/AUTOMATION_WRITES_ENABLED's precedent "
            "instead. Must be True, together with LIVE_TRADE_APPROVAL_ENABLED, "
            "before a real order can ever be placed through this path."
        ),
    )
    LIVE_TRADE_APPROVAL_ENABLED: bool = Field(
        default=False,
        description=(
            "Gates POST /pilots/execution/{token}/approve and "
            "/pilots/execution/{token}/reject on the Pilots API — the ONLY way "
            "a live-trade proposal's status can become 'approved', and "
            "therefore the only path that lets confirm_live_trade actually "
            "submit an order. Defaults False, the same real-capital carve-out "
            "as LIVE_TRADE_EXECUTION_ENABLED."
        ),
    )
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
    MCP_HTTP_BEARER_TOKEN: Optional[str] = Field(
        default=None,
        description=(
            "Bearer token gating the entire streamable-http MCP transport "
            "(investyo_mcp_server.py --transport streamable-http). SECRET — "
            "never GUI-writable, never logged. FAIL-CLOSED: the server refuses "
            "to start in that transport mode without it — this transport is "
            "meant for a tunneled/remote MCP Apps SDK connector, a materially "
            "different exposure than the default local stdio transport. "
            "Single-purpose — must never be reused for FOLLOW_API_TOKEN, "
            "ORCHESTRATOR_DAEMON_TOKEN, or any other write surface's token."
        ),
    )
    MCP_OAUTH_ENABLED: bool = Field(
        default=False,
        description=(
            "Switches investyo_mcp_server.py --transport streamable-http from "
            "static bearer-token auth (MCP_HTTP_BEARER_TOKEN) to a full OAuth "
            "2.1 authorization server (mcp_oauth_provider.py), so claude.ai's "
            "custom-connector UI — which has no static-bearer-token field — "
            "can connect. RFC 7591 dynamic client registration is "
            "unauthenticated by design, so this flag alone does not gate "
            "access: MCP_OAUTH_PASSWORD, checked at the /login form, is the "
            "real trust boundary once a client can register itself and start "
            "an auth flow. False (default) preserves today's exact bearer-only "
            "behavior — mcp_oauth_provider.py is never imported. Carries no "
            "secret material, so per the 2026-08-08 operator decision (see "
            "gui/env_io.py's ALLOWED_KEYS) it is GUI-writable; it decides "
            "which authorization-server endpoints (/register, /authorize, "
            "/token, /revoke) are live on the streamable-http transport, a "
            "bigger risk than an ordinary GUI-writable tunable, so it is also "
            "a settings_keysets.DANGEROUS_KEYS member requiring typed "
            "confirmation on write regardless of which editor is used."
        ),
    )
    MCP_OAUTH_ISSUER_URL: Optional[str] = Field(
        default=None,
        description=(
            "The externally-reachable base URL (scheme + host, no path) that "
            "investyo_mcp_server.py advertises as its OAuth issuer when "
            "MCP_OAUTH_ENABLED is True — must match the stable/named tunnel "
            "hostname the server is actually reached through, since OAuth has "
            "an issuer-identity concept the plain bearer-token transport "
            "doesn't. Not a secret — it's a public hostname, the same value "
            "an operator would put in a browser address bar. Required "
            "(construction fails) when MCP_OAUTH_ENABLED is True."
        ),
    )
    MCP_OAUTH_PASSWORD: Optional[str] = Field(
        default=None,
        description=(
            "Passphrase gating the OAuth /login form (investyo_mcp_server.py "
            "--auth-mode oauth, MCP_OAUTH_ENABLED=True). SECRET — never "
            "GUI-writable, never logged. This is the actual trust boundary "
            "for the OAuth authorization flow, since dynamic client "
            "registration (RFC 7591) itself is unauthenticated by design. "
            "Required (fails closed) when MCP_OAUTH_ENABLED is True — an "
            "empty/unset password is never treated as 'anything passes'."
        ),
    )
    MCP_OAUTH_MULTI_USER_ENABLED: bool = Field(
        default=False,
        description=(
            "Switches the OAuth /login form from the single-passphrase check "
            "(MCP_OAUTH_PASSWORD) to per-user credentials in oauth_users "
            "(mcp_oauth_store.py), provisioned via scripts/manage_oauth_users.py. "
            "False (default) preserves today's exact single-password behavior. "
            "GUI-writable (non-secret) but a settings_keysets.DANGEROUS_KEYS "
            "member -- flipping it changes the entire auth trust boundary, the "
            "same risk class MCP_OAUTH_ENABLED itself already carries."
        ),
    )
    PILOTS_API_ENABLED: bool = Field(
        default=True,
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
    JOBS_API_ENABLED: bool = Field(
        default=True,
        description=(
            "Enable background process execution and SSE log streaming endpoints "
            "on the orchestrator Control API (api/control_api.py). False (default) "
            "preserves fail-closed behavior for jobs execution."
        ),
    )
    COMMAND_EXECUTION_ENABLED: bool = Field(
        default=False,
        description=(
            "Enable the 'command' job type on the orchestrator Control API's "
            "POST /jobs (api/_jobs.py) — lets the webapp's Commands screen "
            "actually run a manifest-listed CLI target (not just compose/copy "
            "it), gated on top of the existing JOBS_API_ENABLED + "
            "ORCHESTRATOR_DAEMON_TOKEN guard already protecting POST /jobs. "
            "False (default) preserves today's compose-only behavior. Carries "
            "no secret material, so per the 2026-08-08 operator decision (see "
            "gui/env_io.py's ALLOWED_KEYS) it is GUI-writable; it gates "
            "execution of the global kill switch, a forced Robinhood re-login, "
            "and arbitrary flags to the orchestrators, a materially bigger "
            "risk than the fixed 7-job-type dispatch JOBS_API_ENABLED alone "
            "already covers, so it is also a settings_keysets.DANGEROUS_KEYS "
            "member requiring typed confirmation on write regardless of which "
            "editor is used."
        ),
    )

    # --- Market-data layer (data/market_data.py) ---
    # Explicit provider override.  When absent the platform auto-selects:
    # Alpaca (if keys present) → yfinance (zero config, ~15-min delayed).
    # NOTE: FMP is deliberately NOT part of that auto-select ladder — see the
    # description below and section 25.
    MARKET_DATA_PROVIDER: Optional[str] = Field(
        default="fmp",
        description=(
            "Force a specific market-data backend: 'fmp', 'alpaca' or "
            "'yfinance'. Defaults to 'fmp' by explicit operator decision. "
            "When set to 'fmp', quotes and bars are routed to FMP if "
            "FMP_QUOTES_ENABLED / FMP_BARS_ENABLED are True (the two-gate convention). "
            "Note: operator accepted risk of switching primary price provider to FMP "
            "before full live eyeball verification against market open."
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
    # --- Jules coding-agent API (data/jules_client.py) --------------------
    # Google's Jules (https://jules.googleapis.com) — an external, autonomous
    # coding agent that can be pointed at a connected GitHub repo and, in
    # AUTO_CREATE_PR mode, opens a real unsupervised PR when it finishes. See
    # docs/JULES_INTEGRATION.md for the full setup/safety writeup.
    JULES_API_KEY: Optional[str] = Field(
        default=None,
        description=(
            "Jules coding-agent API key (https://jules.google.com — see "
            "docs/JULES_INTEGRATION.md). SECRET — masked in the GUI, never "
            "GUI-writable (CONSTRAINT #3). When absent, data/jules_client.py "
            "short-circuits every request with zero network cost and both "
            "list_jules_sources/dispatch_jules_task degrade to a clear "
            "'not configured' message — no crash. Setting it alone changes "
            "NOTHING: JULES_ENABLED must also be explicitly turned on."
        ),
    )
    JULES_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for the Jules coding-agent integration "
            "(data/jules_client.py, investyo_mcp_server.py's "
            "list_jules_sources/dispatch_jules_task tools, "
            "scripts/jules_dispatch.py). Default False and deliberately NOT "
            "covered by the 2026-08-07 'new admin/write capabilities default "
            "True' policy: that policy applies to internal Stockpy "
            "capabilities gated behind this platform's own command tokens; "
            "Jules is a third-party autonomous agent that opens real PRs on "
            "the operator's actual GitHub repo, with no internal trust "
            "boundary standing between 'flag on' and 'PR created' beyond "
            "each dispatch call's own confirm=True argument. This field is "
            "also a settings_keysets.DANGEROUS_KEYS member — flipping it "
            "requires typed confirmation through any settings editor that "
            "exposes it, on top of the per-call confirm gate."
        ),
    )
    JULES_REQUEST_TIMEOUT_SECONDS: int = Field(
        default=30,
        description=(
            "HTTP timeout (seconds) for every data/jules_client.py request "
            "(list_sources, dispatch_session)."
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
    MARKET_DATA_LATENCY_TRACKING_ENABLED: bool = Field(
        default=False,
        description=(
            "Automatic, in-process instrumentation (market_data_latency.py) of "
            "CompositeProvider.get_latest_quote's real (non-cache-hit) fetch "
            "path -- records the gap between a provider's own quote timestamp "
            "and local ingestion time to an in-memory ring buffer (never "
            "persisted to disk; clears on process restart), feeding Mission "
            "Control's per-symbol data-latency heatmap "
            "(pilots/observability.py::latency_heatmap_summary). False (the "
            "default) is a complete no-op -- zero recording, zero overhead on "
            "the quote-fetch hot path -- matching this codebase's convention "
            "that new diagnostic instrumentation defaults off even when "
            "read-only (e.g. ETF_HOLDINGS_ENABLED, SECTOR_HEAT_ENABLED)."
        ),
    )
    BROWSER_DIAGNOSTICS_ENABLED: bool = Field(
        default=False,
        description=(
            "Enables genuine Chromium-based headless captures (browser_diagnostics.py) "
            "for MCP webapp introspection tools (inspect_webapp_screen, audit_webapp_vitals, "
            "compare_screen_snapshots). Requires a separate `playwright install chromium` "
            "step by the operator. When False (the default) or when playwright is missing, "
            "those tools degrade cleanly to their existing HTTP-only or mocked fallbacks."
        ),
    )
    BROWSER_DIAGNOSTICS_TIMEOUT_SECONDS: float = Field(
        default=15.0,
        description=(
            "Maximum wall-clock time allowed for a headless Chromium capture before it "
            "is dead-lettered and degraded to the fallback path. Protects the API "
            "event loop from hanging on a stalled DOM."
        ),
    )
    EXCURSION_INTRADAY_ENABLED: bool = Field(
        default=False,
        description=(
            "Opt-in (Phase-1 audit item B2): evaluation_engine.calculate_edge_ratio "
            "consumes hourly bars (MarketDataProvider.get_intraday_bars(..., "
            "interval='1h')) over the trade hold window instead of daily bars, "
            "for finer Maximum Favorable/Adverse Excursion (MFE/MAE) resolution "
            "on same-day or short holds. Daily bars are already genuine (not "
            "fabricated) and adequate for multi-day holds; this only adds "
            "intraday precision. Any hourly-fetch failure (provider error, "
            "unsupported interval, empty result) degrades to the existing "
            "daily-bar path rather than raising -- never blocks the excursion "
            "calculation. False (the default) reproduces pre-existing "
            "daily-only behavior exactly -- matches the FORECAST_USE_GARCH_SIGMA "
            "opt-in convention."
        ),
    )
    MARKET_DATA_WS_ENABLED: bool = Field(
        default=False,
        description=(
            "Opt-in: subscribe to Alpaca's real-time StockDataStream WebSocket for "
            "quotes, SUPPLEMENTING (never replacing) the REST-polling "
            "CompositeProvider -- see data/market_data_ws.py. Only takes effect "
            "when the active quote provider is AlpacaProvider; otherwise a no-op "
            "with an INFO log. False (default) reproduces the exact current "
            "REST-only behavior -- matches the FORECAST_USE_GARCH_SIGMA opt-in "
            "convention. Any WS failure (connect, subscribe, disconnect, missing "
            "credentials) degrades to the existing REST path -- never crashes "
            "the pipeline."
        ),
    )
    MARKET_DATA_WS_STALE_SECONDS: int = Field(
        default=10,
        description=(
            "Max age (seconds) of a WebSocket-delivered quote before it is "
            "treated as stale and the REST path is used instead."
        ),
    )
    MARKET_DATA_WS_SYMBOLS: Optional[str] = Field(
        default=None,
        description=(
            "Comma-separated symbol override for the WS subscription. None "
            "(default) falls back to the WATCHLIST env var, then to no "
            "subscription (WS ingestion becomes a no-op, logged)."
        ),
    )
    MARKET_DATA_WS_RECONNECT_BASE_SECONDS: float = Field(
        default=1.0, description="Initial WS reconnect backoff (seconds)."
    )
    MARKET_DATA_WS_RECONNECT_MAX_SECONDS: float = Field(
        default=30.0, description="Max WS reconnect backoff (seconds)."
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
        default="fmp",
        description=(
            "Primary fundamentals backend: 'fmp' (Financial Modeling Prep, default by "
            "explicit operator decision), 'yahoo' (statement-derived), or 'yfinance_info' "
            "(raw .info fallback). Finnhub is no longer a fundamentals source. "
            "'fmp' requires FMP_FUNDAMENTALS_ENABLED=true; with either half missing "
            "the Yahoo path is used as fallback when FMP_FALLBACK_ENABLED is True. "
            "Note: operator accepted risk of switching primary fundamentals provider to FMP "
            "before full live eyeball verification."
        ),
    )

    # --- 25. Financial Modeling Prep (data/fmp_client.py) ---
    # One HTTP seam (data/fmp_client.py) shared by every FMP consumer, because
    # the rate limit is per-ACCOUNT: per-concern limiters would blow the budget
    # by construction. Every capability gate below defaults to True by explicit
    # operator decision for the full FMP rollout.
    FMP_API_KEY: Optional[str] = Field(
        default=None,
        description=(
            "Financial Modeling Prep API key (https://financialmodelingprep.com). "
            "SECRET — masked in the GUI, never GUI-writable (CONSTRAINT #3). "
            "When absent, data/fmp_client.py short-circuits every request with "
            "zero network cost and every FMP-backed feed degrades to its "
            "existing source or to NaN — no crash, no fabricated default. "
            "Setting it alone changes NOTHING: each feed also needs its own "
            "FMP_*_ENABLED gate (and, for quotes/bars/fundamentals, an "
            "explicit MARKET_DATA_PROVIDER / FUNDAMENTALS_SOURCE of 'fmp')."
        ),
    )
    # ── FMP client tuning (throttle / retry / breaker) ───────────────────
    # FMP's Starter tier publishes 300 req/min, but the enforcement semantics
    # (per-key vs. per-IP, burst-tolerant vs. strict) could not be verified
    # from this sandbox — so these are a conservative choice targeting ~240/min
    # (80% of the ceiling), NOT a documented contract. They are settings
    # precisely so an operator can tune them against observed behaviour.
    # FMP_MIN_REQUEST_INTERVAL_SECONDS=0 with FMP_MAX_RETRIES=0 and
    # FMP_COOLDOWN_THRESHOLD=0 reproduces un-throttled behaviour exactly.
    FMP_BASE_URL: str = Field(
        default="https://financialmodelingprep.com/stable",
        description=(
            "Base URL every data/fmp_client.py request is built from "
            "(f'{FMP_BASE_URL}/{path}'). The '/stable' family is the one the "
            "verified endpoint paths belong to; pointing this at the legacy "
            "'/api/v3' family would 404 or return a different response shape."
        ),
    )
    FMP_TIMEOUT_SECONDS: float = Field(
        default=10.0,
        description=(
            "Per-request HTTP timeout (seconds) for data/fmp_client.py. A "
            "timeout is treated as a transport error: never retried (an "
            "immediate retry of a timeout just times out again at full cost) "
            "but it does count toward FMP_COOLDOWN_THRESHOLD."
        ),
    )
    FMP_MIN_REQUEST_INTERVAL_SECONDS: float = Field(
        default=0.25,
        description=(
            "Minimum seconds between FMP request ISSUANCE, shared process-wide "
            "across every FMP consumer (the budget is per-ACCOUNT, so one "
            "limiter is the only correct design). 0.25 s = 240 req/min by "
            "construction, 80% of the published Starter ceiling. Honest cost: "
            "~100 requests/cycle at this spacing is ~25 s of SERIALIZED "
            "issuance — FMP turns N parallel yfinance calls into N serialized "
            "ones, so DATA_FETCH_MAX_CONCURRENCY buys nothing on this path "
            "(FMP_MAX_SECONDS_PER_CYCLE is the guard). 0 disables spacing "
            "entirely; with FMP_MAX_RETRIES=0 and FMP_COOLDOWN_THRESHOLD=0 "
            "that reproduces un-throttled behaviour exactly."
        ),
    )
    FMP_MAX_RETRIES: int = Field(
        default=2,
        description=(
            "Retries after an FMP HTTP 429/5xx before the request is given up "
            "on, with exponential backoff from FMP_RETRY_BACKOFF_SECONDS (a "
            "Retry-After response header, when present and parseable, takes "
            "precedence over the computed wait). Only 429/5xx are retried: a "
            "404 is a bad symbol rather than an overloaded host, a 401 is a "
            "rejected key, and a 403 is a plan entitlement — none of the three "
            "improves by being asked again. 0 disables retrying; with "
            "FMP_MIN_REQUEST_INTERVAL_SECONDS=0 and FMP_COOLDOWN_THRESHOLD=0 "
            "that reproduces un-throttled behaviour exactly."
        ),
    )
    FMP_RETRY_BACKOFF_SECONDS: float = Field(
        default=2.0,
        description=(
            "Base seconds for the FMP retry backoff; attempt N waits "
            "FMP_RETRY_BACKOFF_SECONDS * 2**N unless the server sent a "
            "Retry-After header. The backoff counts toward the issuance "
            "spacing rather than being added on top of it."
        ),
    )
    FMP_COOLDOWN_THRESHOLD: int = Field(
        default=5,
        description=(
            "Consecutive FAILED FMP requests — 429, 5xx, or transport error "
            "alike — after which FMP calls are SKIPPED outright (no sleep, no "
            "request) for FMP_COOLDOWN_SECONDS, so an outage costs one round "
            "of failures and then falls straight through to the existing "
            "provider instead of paying a full timeout per symbol. Counting "
            "transport errors too is deliberate: from the caller's side "
            "'refusing us' and 'not answering us' have identical cost and "
            "identical remedy. 401 and 403 deliberately do NOT count — neither "
            "is evidence the host is unhealthy, and a cooldown cannot fix "
            "either. Requiring CONSECUTIVE failures keeps one flaky socket "
            "from opening it; a single served response clears the run and any "
            "open cooldown. 0 disables the cooldown; with "
            "FMP_MIN_REQUEST_INTERVAL_SECONDS=0 and FMP_MAX_RETRIES=0 that "
            "reproduces un-throttled behaviour exactly."
        ),
    )
    FMP_COOLDOWN_SECONDS: float = Field(
        default=300.0,
        description=(
            "How long the FMP cooldown stays open once FMP_COOLDOWN_THRESHOLD "
            "consecutive failed requests have been seen. Affects FMP only — "
            "every other data source keeps running normally throughout, and "
            "the cooldown self-expires so a recovered account resumes without "
            "operator action (unlike a per-process latch, which would pin a "
            "multi-hour daemon on the fallback after a single bad minute)."
        ),
    )
    # ── FMP feed master gates (all default True by explicit operator decision) ───
    FMP_QUOTES_ENABLED: bool = Field(
        default=True,
        description=(
            "Two-gate capability switch for FMP-sourced quotes, independent "
            "of FMP_BARS_ENABLED. Defaults True by explicit operator decision. "
            "Requires MARKET_DATA_PROVIDER='fmp' to route get_latest_quote() to FMP. "
            "Note: operator accepted risk of switching primary quote provider to FMP."
        ),
    )
    FMP_BARS_ENABLED: bool = Field(
        default=True,
        description=(
            "Two-gate capability switch for FMP-sourced OHLCV bars, "
            "independent of FMP_QUOTES_ENABLED. Defaults True by explicit operator decision, "
            "accepting that scripts/verify_fmp_bars.py has not been run against a live "
            "account in this sandbox (no live-market network access). Requires "
            "MARKET_DATA_PROVIDER='fmp'. Read FMP_BARS_ADJUSTMENT before changing it: "
            "'dividend-adjusted' matches incumbent yfinance split+dividend adjustment, and an "
            "adjustment-convention mismatch corrupts every return series, indicator, GARCH fit "
            "and backtest -- and does so PLAUSIBLY (nothing fails loudly)."
        ),
    )
    FMP_FUNDAMENTALS_ENABLED: bool = Field(
        default=True,
        description=(
            "Two-gate capability switch for FMP-sourced fundamentals. "
            "Defaults True by explicit operator decision. Requires "
            "FUNDAMENTALS_SOURCE='fmp' to route get_fundamentals() to FMP."
        ),
    )
    FMP_ANALYST_ENABLED: bool = Field(
        default=True,
        description=(
            "Master switch for the FMP analyst feed (price-target consensus + "
            "grades summary) as DIAGNOSTIC dashboard columns. Defaults True by "
            "explicit operator decision. Single gate — diagnostic only. "
            "Deliberately never a SignalModule and never in SIGNAL_WEIGHTS: "
            "FMP serves only the CURRENT consensus and targets get revised, so "
            "there is no point-in-time history to backtest against."
        ),
    )
    FMP_EARNINGS_ENABLED: bool = Field(
        default=True,
        description=(
            "Master switch for the FMP earnings calendar/surprise feed. Defaults "
            "True by explicit operator decision. When on, FMP becomes a SECOND "
            "source for the existing Earnings_Date column and, unlike Finnhub, "
            "is not limited to a 30-day forward window. Single gate."
        ),
    )
    FMP_NEWS_ENABLED: bool = Field(
        default=True,
        description=(
            "Master switch for the FMP company-news feed (data.fmp_client."
            "stock_news, wrapping /news/stock). Defaults True by explicit "
            "operator decision. When True AND FMP_API_KEY is set, FMP becomes "
            "the PRIMARY provider for company headlines (signals/news_catalyst.py::"
            "fetch_company_headlines dispatches FMP-first, falling back to "
            "Finnhub only on an FMP failure) and 'fmp_news' becomes eligible "
            "for SENTIMENT_SOURCES. Verified live 2026-08 against a real FMP "
            "key: /news/stock returns >=6 months of real history (vs. "
            "Finnhub's free-tier ~3-month cap) with working from/to date-"
            "window + page/limit pagination -- the one FMP data-fetch flag "
            "with genuine live verification behind it, not just a probe. "
            "Deliberately does NOT touch /news/press-releases -- that "
            "endpoint returned a plan-entitlement rejection ('Restricted "
            "Endpoint') against the account this integration was verified "
            "with; see docs/FMP_INTEGRATION.md."
        ),
    )
    FMP_MACRO_ENABLED: bool = Field(
        default=True,
        description=(
            "Master switch for the FMP macro feed (treasury rates + the named "
            "series in FMP_ECON_INDICATORS), written into the EXISTING "
            "macro_history table under FRED-compatible series IDs. Defaults True "
            "by explicit operator decision. FMP SUPPLEMENTS FRED, it cannot replace it: "
            "VIXCLS and BAMLH0A0HYM2 (HY OAS) have no Starter equivalent and "
            "compute_hmm_risk_on_probability needs VIXCLS. Single gate."
        ),
    )
    FMP_ECON_CALENDAR_ENABLED: bool = Field(
        default=True,
        description=(
            "Master switch for the FMP economics calendar feed (/economics-calendar) "
            "as DIAGNOSTIC dashboard columns (Next_Macro_Event, Next_Macro_Event_Date). "
            "Defaults True by explicit operator decision. Broadcasts market-wide upcoming "
            "macro events (CPI, FOMC, NFP) to all rows. Single gate, one request per cycle. "
            "Deliberately never a SignalModule and never in SIGNAL_WEIGHTS: diagnostic only. "
            "Note: this endpoint's Starter-tier entitlement status has not been confirmed "
            "against a live account -- degrades gracefully to empty/NaN on entitlement rejection."
        ),
    )
    FMP_INSIDER_ENABLED: bool = Field(
        default=True,
        description=(
            "Master switch for the FMP insider-trading statistics feed "
            "(diagnostic Insider_Buy_Sell_Ratio column). Defaults True by "
            "explicit operator decision. Gated by FMP_INSIDER_MIN_LAG_DAYS "
            "to avoid lookahead revisions. Single gate."
        ),
    )
    FMP_SECTOR_SNAPSHOT_ENABLED: bool = Field(
        default=True,
        description=(
            "Master switch for the dated FMP sector P/E + sector performance "
            "snapshots (diagnostic Sector_PE / Sector_1D_Change columns). "
            "Defaults True by explicit operator decision. Two requests per "
            "cycle total regardless of universe size. Single gate."
        ),
    )
    FMP_OPTIONS_HEALTH_ENABLED: bool = Field(
        default=True,
        description=(
            "Master switch for the FMP fundamental-health overlay bundled into "
            "the options premium-directive matrix (reporting/options_snapshot.py"
            "::write_options_matrix -> technical_options_engine.build_premium_"
            "directive). Defaults True by explicit operator decision. When True, "
            "gates Altman Z-Score + Piotroski F-Score, Net Debt/EBITDA + FCF Yield, "
            "and 30-day realized volatility."
        ),
    )
    FMP_OPTIONS_CONTEXT_ENABLED: bool = Field(
        default=True,
        description=(
            "Master switch for the FMP market/qualitative-context overlay "
            "bundled into the options premium-directive matrix (reporting/"
            "options_snapshot.py::write_options_matrix -> technical_options_"
            "engine.build_premium_directive). Defaults True by explicit operator "
            "decision. Gates recent news headlines and peer-comparison ticker group."
        ),
    )
    FMP_PEERS_ENABLED: bool = Field(
        default=True,
        description=(
            "Master switch for the on-demand GET /data/peers/{symbol} "
            "endpoint (api/data_api.py) for the webapp's 'Suggest peers for "
            "this ticker' affordance on SymbolComparison. Defaults True by "
            "explicit operator decision."
        ),
    )
    FMP_UNIVERSE_ENABLED: bool = Field(
        default=True,
        description=(
            "Master switch for using FMP's historical S&P 500 constituent-"
            "changes endpoint (data/fmp_client.py::historical_sp500_changes) "
            "as the PRIMARY source for universe_engine.py's point-in-time "
            "survivorship-bias reconstruction, with the legacy Wikipedia "
            "'Selected changes' table scrape demoted to a fallback -- that "
            "table was removed from the live Wikipedia page entirely as of "
            "2026-08, which is what necessitated this feed. Defaults True by "
            "explicit operator decision, accepting that this endpoint has NOT "
            "been verified against a live FMP account (endpoint path/field "
            "names are best-effort from public docs only). "
            "data/fmp_universe.py::fetch_sp500_changes_via_fmp short-circuits "
            "to [] on any failure, falling through to the Wikipedia path. "
            "Wikipedia's current-constituents table (unaffected by the "
            "removal above) always stays the source of truth for the CURRENT "
            "roster regardless of this flag; only the historical changes "
            "half is FMP-eligible."
        ),
    )
    FMP_SCREENER_ENABLED: bool = Field(
        default=True,
        description=(
            "Master switch for the symbol-search/sector-industry-screener "
            "feed (data/fmp_screener.py, wrapping data/fmp_client.py's "
            "search_name/search_symbol/company_screener/available_sectors/"
            "available_industries) behind GET /data/symbol-search, "
            "GET /data/screener, and GET /data/screener/filters "
            "(api/data_api.py) -- one flag covers all four, matching "
            "FMP_PEERS_ENABLED's single-gate reasoning since they're the "
            "same read-only, on-demand call-site cadence. Defaults True by "
            "explicit operator decision, accepting that this endpoint set "
            "has NOT been verified against the operator's own FMP_API_KEY/"
            "tier in this sandbox (verified live 2026-08 only via an "
            "external FMP MCP connector on a different account -- see "
            "docs/FMP_INTEGRATION.md §9). data/fmp_screener.py degrades to "
            "[] on any failure, never raises."
        ),
    )
    # ── FMP behavior knobs ───────────────────────────────────────────────
    FMP_FALLBACK_ENABLED: bool = Field(
        default=True,
        description=(
            "When True (default), an FMP failure falls through to the existing "
            "provider chain for that kind (quotes/bars: FMP -> Alpaca if keyed "
            "-> yfinance; fundamentals: FMP -> Yahoo statement-derived -> raw "
            "yfinance .info), logging a WARNING naming the provider, symbol "
            "and exception so a silent fallback can never masquerade as "
            "success. When False the chain is [primary] only and a failure "
            "propagates exactly as it does today -- use it to prove FMP is "
            "actually serving, rather than being quietly rescued."
        ),
    )
    FMP_QUOTES_REALTIME: bool = Field(
        default=True,
        description=(
            "Whether FMP-served quotes may be labelled real-time (is_stale=False). "
            "Defaults True by explicit operator decision, NOT because this was "
            "confirmed: whether /quote is genuinely real-time on the Starter tier "
            "could not be verified from this sandbox, and claiming freshness that "
            "was not measured is exactly the kind of quiet fabrication CONSTRAINT "
            "#4 exists to prevent. Confirm against a live market open before "
            "trusting is_stale=False on this path in a live-capital deployment."
        ),
    )
    FMP_BARS_ADJUSTMENT: str = Field(
        default="dividend-adjusted",
        description=(
            "Which /historical-price-eod variant FMP bars are pulled from: "
            "'dividend-adjusted' (default), 'light', 'full', or "
            "'non-split-adjusted'. THIS IS NOT A COSMETIC CHOICE. 'light' and "
            "'full' are SPLIT-ONLY, while the incumbent yfinance path uses "
            "history(auto_adjust=True) — split AND dividend adjusted — so "
            "'dividend-adjusted' is the variant that MATCHES today's data. "
            "'full' is the obvious-looking pick and it is wrong. Changing this "
            "silently corrupts every return series, every indicator, every "
            "GARCH fit and every backtest, and it does so plausibly: nothing "
            "fails loudly, the numbers just quietly stop meaning what they "
            "did. Run scripts/verify_fmp_bars.py (max abs relative close diff "
            "< 1e-4) before trusting any value here, and note that price_bars "
            "has a (symbol, date) PK — flipping this on an existing DB SPLICES "
            "two adjustment conventions into one series at the cutover date, "
            "which no test catches. Delete price_bars and re-backfill instead."
        ),
    )
    FMP_ANALYST_REFRESH_HOURS: int = Field(
        default=24,
        description=(
            "Hours before a symbol's cached FMP analyst consensus is "
            "re-fetched. Analyst targets move on a multi-day cadence, so a "
            "24 h cadence costs one request per symbol per DAY instead of per "
            "cycle — the single largest steady-state rate-limit saving after "
            "batch-quote. Only consulted when FMP_ANALYST_ENABLED is True."
        ),
    )
    FMP_EARNINGS_REFRESH_HOURS: int = Field(
        default=12,
        description=(
            "Hours before a symbol's cached FMP earnings rows are re-fetched. "
            "Shorter than the analyst cadence because a reported actual lands "
            "on a known day and is worth picking up the same session. Only "
            "consulted when FMP_EARNINGS_ENABLED is True."
        ),
    )
    FMP_INSIDER_REFRESH_DAYS: int = Field(
        default=7,
        description=(
            "Days before a symbol's cached FMP insider statistics are "
            "re-fetched. Quarterly aggregates that only move as late Form 4s "
            "land, so a weekly cadence loses nothing. Only consulted when "
            "FMP_INSIDER_ENABLED is True."
        ),
    )
    FMP_INSIDER_MIN_LAG_DAYS: int = Field(
        default=45,
        description=(
            "Minimum days a quarter must have been CLOSED before its FMP "
            "insider aggregate is consumed. Form 4s keep landing after a "
            "quarter ends, so a freshly-closed quarter's aggregate is still "
            "changing underneath us and reading it early means reading a "
            "number that will be revised. 45 is a deliberate CONSERVATIVE "
            "JUDGMENT CALL, not a derived constant — there is no published "
            "filing-completeness curve behind it; it is set here so an "
            "operator can widen it if they observe late revisions."
        ),
    )
    FMP_NEWS_PAGE_LIMIT: int = Field(
        default=100,
        description=(
            "Articles requested per /news/stock page (the 'limit' query "
            "param). 100 matches the page size verified live 2026-08 against "
            "a real FMP key over a multi-day window. Only consulted when "
            "FMP_NEWS_ENABLED is True."
        ),
    )
    FMP_NEWS_MAX_PAGES: int = Field(
        default=10,
        description=(
            "Hard ceiling on pages fetched per symbol per call into "
            "data.fmp_client.stock_news, bounding a wide backfill window "
            "(e.g. scripts/backfill_news_history.py --months 6) so a dense "
            "news day/symbol cannot loop indefinitely. Once the ceiling is "
            "reached the remaining (older) articles in the window are simply "
            "not fetched -- callers that need full coverage should narrow "
            "--months or accept the honest gap (CONSTRAINT #4: never a "
            "fabricated substitute for the missing pages, just fewer real "
            "rows). Only consulted when FMP_NEWS_ENABLED is True."
        ),
    )
    FMP_ECON_INDICATORS: str = Field(
        default="unemploymentRate",
        description=(
            "FMP /economic-indicators series name fetched when "
            "FMP_MACRO_ENABLED is True (currently 'unemploymentRate' alone is "
            "supported by data/fmp_macro.py::fetch_unemployment_rate). "
            "Note these series ARE revised and FMP serves the latest vintage, "
            "so they are not point-in-time safe (the same limitation FRED "
            "already has here) and must stay out of the PIT audit."
        ),
    )
    FMP_MAX_SECONDS_PER_CYCLE: float = Field(
        default=120.0,
        description=(
            "Wall-clock budget (seconds) for ALL FMP requests in one pipeline "
            "cycle, following the ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE "
            "precedent. Needed because FMP_MIN_REQUEST_INTERVAL_SECONDS makes "
            "issuance serial: ~100 requests at 0.25 s spacing is ~25 s of pure "
            "waiting, and a cold cache is several times that. Once the budget "
            "is spent, the remaining symbols degrade to NaN for that cycle "
            "rather than overrunning it — an honest gap, never a fabricated "
            "value (CONSTRAINT #4)."
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
    # RH_MFA_SECRET (Base32 TOTP secret) was removed when Robinhood login moved
    # to device-approval push (data.robinhood_login_worker) — passing an
    # mfa_code short-circuits the push workflow entirely, so a TOTP secret is
    # no longer usable here. See docs/settings_liveness.json history / the PR
    # that retired it for the full rationale.
    #
    # data.robinhood_portfolio.fetch_account_snapshot()'s Tier 3 (live fetch) runs
    # automatically whenever the cached snapshot is older than max_age_hours (default
    # 20h) — every one of its ~8 call sites (GUI panels, the MCP server, the Pilots/
    # data APIs, portfolio_sync, llm_commentary) inherits this. Under device-approval
    # login, an unattended Tier-3 attempt can never succeed (it needs a human to tap
    # approve on their phone) — it only ever raises RobinhoodApprovalRequired and
    # dead-letters. Default False reflects that: live login only happens when
    # explicitly forced (--refresh-account, or the webapp's Connect/Refresh flows,
    # both of which run the login in a supervised, killable worker a human is
    # expected to be watching); every other caller gets the best available cached
    # snapshot regardless of staleness rather than spawning a doomed login attempt
    # every cycle. Set True to restore the old always-attempt behavior (meaningful
    # only if a future login mode restores unattended capability).
    ROBINHOOD_AUTO_REFRESH_ENABLED: bool = Field(
        default=False,
        description=(
            "When True, fetch_account_snapshot() automatically re-logs-in to "
            "Robinhood whenever the cached snapshot exceeds max_age_hours. Default "
            "False: device-approval login needs a human to tap approve, so an "
            "unattended background attempt can never succeed — live login only "
            "happens when explicitly forced (--refresh-account, or the webapp's "
            "Connect/Refresh flows); all other callers get the cached snapshot "
            "regardless of staleness."
        ),
    )
    # data/robinhood_login.py's killable-subprocess login worker. All three
    # default to values measured/estimated against robin_stocks' own device-
    # approval wait windows (authentication.py's two 120s walls back-to-back)
    # plus interpreter startup and network round-trips -- see
    # docs/known_issues or the introducing PR for the full derivation.
    RH_LOGIN_DEADLINE_SECONDS: int = Field(
        default=180,
        description=(
            "Hard wall-clock deadline for one device-approval login attempt, from "
            "worker start to a terminal result. The worker is SIGKILLed if it "
            "hasn't produced a result by then -- about the longest a human will "
            "hold their phone waiting for a push notification."
        ),
    )
    RH_LOGIN_GRACE_SECONDS: int = Field(
        default=5,
        description=(
            "SIGTERM-to-SIGKILL grace period when a login worker is cancelled or "
            "hits its deadline."
        ),
    )
    RH_LOGIN_STARTUP_SECONDS: int = Field(
        default=30,
        description=(
            "If the login worker process hasn't emitted its first 'started' event "
            "within this many seconds, it's treated as a failed launch (bad "
            "interpreter, import error) and killed rather than waited out for the "
            "full RH_LOGIN_DEADLINE_SECONDS."
        ),
    )
    # --- Broker closed-trade ingest (data/broker_fills_store.py, 2026-08) ---
    # Root cause fixed: data/robinhood_orders.py's FIFO reconstruction engine
    # existed but was never fed a real fetcher (the only production caller,
    # pilots/realized.py, deliberately injects an empty one to avoid triggering
    # a login on a web request), and even a real fetch would have crashed on a
    # dead `from data.robinhood_portfolio import _login` import left over from
    # the device-approval login rewrite. These settings gate the fix: the
    # login worker now also ingests real filled-order history into a durable
    # store (data/broker_fills_store.py) during a `--refresh-account` login,
    # instead of the operator's real sells being invisible everywhere in the
    # platform. See docs/known_issues/robinhood_order_history_window_and_fifo_limits.md.
    BROKER_TRADE_INGEST_ENABLED: bool = Field(
        default=True,
        description=(
            "When True (the fix itself -- a deliberate exception to 'new "
            "settings default to today's behavior'), the Robinhood login "
            "worker also fetches and durably persists the operator's real "
            "filled-order history during a `refresh` login, alongside the "
            "account snapshot it already fetches. Set False to restore the "
            "old behavior where no real broker trade ever reaches the "
            "platform."
        ),
    )
    RH_ORDER_INGEST_BUDGET_SECONDS: int = Field(
        default=60,
        description=(
            "Wall-clock budget for the in-worker orders ingest (pagination + "
            "instrument-symbol resolution), bounded well inside "
            "RH_LOGIN_DEADLINE_SECONDS so a slow ingest can never turn a "
            "successful account-snapshot refresh into a reported login "
            "timeout. On exhaustion the worker logs a WARNING, persists "
            "whatever resolved so far, and moves on."
        ),
    )
    RH_ORDER_SYMBOL_RESOLVE_MAX: int = Field(
        default=200,
        description=(
            "Cap on the number of instrument-URL -> ticker symbol resolutions "
            "(one Robinhood API call each) performed per ingest. A durable "
            "resolver cache (data/broker_fills_store.py's "
            "BrokerInstrumentSymbol table) means only the FIRST ingest for a "
            "given instrument pays this cost; later ingests reuse the cached "
            "resolution. An order whose instrument can't be resolved within "
            "the cap is skipped (never fabricated), same as an unresolvable "
            "instrument today."
        ),
    )
    # --- Universe retention for recently-closed positions (2026-08) ---
    # Without this, a sold-to-zero symbol drops out of held/watchlist/discovered
    # instantly and silently -- no "sold" state, just gone from analysis. This
    # keeps it visible to the advisory pipeline for a bounded window after the
    # sell, keyed off the operator's REAL Robinhood sell fills (see
    # BROKER_TRADE_INGEST_ENABLED above), not internal paper trades.
    CLOSED_POSITION_RETENTION_DAYS: int = Field(
        default=180,
        description=(
            "Keep a fully-sold symbol in the analysis universe (main.py's "
            "_build_universe, data/portfolio_sync.py's resolve_universe) for "
            "this many days after its most recent real Robinhood SELL fill, "
            "even after it drops out of held positions. A deliberate change "
            "to today's behavior. 0 disables retention entirely, restoring "
            "the pre-2026-08 universe exactly. A symbol that was bought but "
            "never sold is unaffected -- retention keys off the last SELL "
            "only."
        ),
    )
    CLOSED_POSITION_RETENTION_MAX_SYMBOLS: int = Field(
        default=25,
        description=(
            "Cap on how many recently-closed symbols CLOSED_POSITION_RETENTION_DAYS "
            "can add to the universe in one cycle (most-recently-sold first). "
            "Bounds the added per-cycle pipeline cost (a bars fetch plus a full "
            "advisory evaluation per retained symbol) on an account with a "
            "long trading history."
        ),
    )
    # --- Broker-trade fallback for MAE/MFE/Edge Ratio (evaluation_engine.py) ---
    EVAL_BROKER_TRADES_ENABLED: bool = Field(
        default=False,
        description=(
            "When True, evaluate_portfolio() falls back to broker-reconstructed "
            "closed trades (data/broker_fills_store.py) for MAE/MFE/'Edge "
            "Ratio' on symbols with no internal transactions_store trade "
            "history. Internal history always wins when present. Default "
            "False preserves today's exact behavior (those metrics stay NaN "
            "for a symbol with no internal trade history). This NEVER writes "
            "transactions_store's `trades` table and never reaches any "
            "position-sizing path -- see data/broker_fills_store.py's module "
            "docstring for why that isolation is structural, not just "
            "convention. A broker-reconstructed trade carries no `conviction` "
            "(the platform never issued a recommendation for a manual "
            "discretionary trade), so evaluation_engine.calibration_curve's "
            "existing conviction dropna already excludes these rows "
            "regardless of this flag."
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
    # Heuristic multiplier used by the check_overnight_liquidity MCP tool to approximate
    # depth notional from Average Daily Volume (ADV) in the absence of real Level-2 data.
    OVERNIGHT_LIQUIDITY_DEPTH_HEURISTIC: float = Field(
        default=0.01,
        description="Heuristic multiplier (e.g. 0.01 = 1% ADV) to approximate overnight depth notional.",
    )
    # execution/compose.py (cross-Pilot + advisory queue composer) reads a
    # per-source JSON file (output/queue_sources/<source_id>.json) for the
    # advisory pipeline and for every actively-followed Pilot. A follow's
    # source file is written only when the operator explicitly (re-)follows
    # via plan_follow -- there is no background job that keeps it fresh
    # (the "re-plan all follows" auto-refresh idea was deliberately cut from
    # this feature -- see docs/plans/AUTOPILOT_PLAN.md). Left unchecked, a
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

    # --- FIX 4.4 Gateway (execution/fix_gateway.py) ---
    FIX_GATEWAY_ENABLED: bool = Field(default=True, description="Master switch for the simulated FIX 4.4 gateway's route/session endpoints (POST /pilots/execution/fix/route and session management). Defaults True since this module is fully simulated -- it never touches real capital or a real venue connection -- following this repo's 2026-08-03 convention that new admin/execution capabilities default ON unless they change live trading behavior.")
    FIX_HEARTBEAT_INTERVAL_SECONDS: int = Field(default=30, description="Heartbeat interval (seconds) for FixSession -- was previously hardcoded as the class constructor default; now operator-configurable.")

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
    EXECUTION_PRIORITY_QUEUE_ENABLED: bool = Field(
        default=False,
        description=(
            "Opt-in: route OrderIntents through execution/priority_queue.py's "
            "leaky-bucket priority queue before submission, prioritizing "
            "risk-reducing (SELL/TRIM) intents over new BUYs when nearing the "
            "submission-rate budget. Does NOT replace or bypass "
            "MAX_ORDER_RATE_PER_MIN's hard cap (execution/risk_gate.py) or "
            "execution/kill_switch.py -- both remain the sole authorization "
            "gate, checked at submission exactly as before. False (default) "
            "preserves the exact current sequential per-row submission order "
            "-- matches the FORECAST_USE_GARCH_SIGMA opt-in convention."
        ),
    )
    EXECUTION_QUEUE_LEAK_RATE_PER_SEC: float = Field(
        default=2.0,
        description=(
            "Leaky-bucket drain rate (order submissions/sec) when "
            "EXECUTION_PRIORITY_QUEUE_ENABLED=true. Only paces submission "
            "ordering within a single cycle's queue drain -- independent of "
            "MAX_ORDER_RATE_PER_MIN's separate 60s rolling-window cap."
        ),
    )
    HMM_RISK_OFF_BLOCK_THRESHOLD: float = Field(
        default=0.80,
        description="Block new long orders when HMM risk-off probability exceeds this. The Gaussian HMM models the underlying market regime. A higher value means the system is less likely to block trades (more aggressive), while a lower value makes it more sensitive to volatility and bear market conditions, halting long entries sooner.",
    )
    RISK_GATE_ENFORCE_MARKET_HOURS: bool = Field(
        default=True,
        description="Block orders outside NYSE RTH (09:30–16:00 ET).",
    )

    # --- Dynamic Circuit Breaker & Flash Guard (execution/dynamic_circuit_breaker.py) ---
    CIRCUIT_BREAKER_VOLATILITY_Z_THRESHOLD: float = Field(
        default=3.5,
        description="Volatility jump Z-score threshold to trigger SOFT_HALT (VOLATILITY_BURST_HALT).",
    )
    CIRCUIT_BREAKER_VPIN_THRESHOLD: float = Field(
        default=0.40,
        description="Volume-Synchronized Probability of Toxicity threshold to trigger FLASH_CRASH_SHIELD.",
    )
    CIRCUIT_BREAKER_OFI_THRESHOLD: float = Field(
        default=1000.0,
        description="Order Flow Imbalance threshold (selling pressure) to trigger FLASH_CRASH_SHIELD.",
    )
    CIRCUIT_BREAKER_LOSS_VELOCITY_WINDOW_MINS: float = Field(
        default=30.0,
        description="Loss velocity rolling time window in minutes relative to daily loss limit.",
    )
    CIRCUIT_BREAKER_ENABLED: bool = Field(
        default=False,
        description="Master switch for automatic live circuit-breaker updates. Live when enabled: volatility-jump detector, VPIN (coarse bar-level BVC approximation), and the loss-velocity brake (sampled from PaperAccountStore equity). OFI remains unwired (no configured provider populates bid/ask size), so the compound OFI+VPIN flash-crash shield still cannot trigger automatically even with VPIN now real — see docstring on the daemon updater (desktop/daemon_runtime.py::maybe_update_circuit_breaker) for full scope. Defaults False to preserve today's exact (inert) behavior.",
    )
    CIRCUIT_BREAKER_REFERENCE_SYMBOL: str = Field(
        default="SPY",
        description="Reference symbol used for the live volatility-jump circuit-breaker updater's baseline/reactive vol computation.",
    )

    # --- HMM regime detector (regime/hmm_regime.py, macro_engine.py) ---
    HMM_N_STATES: int = Field(
        default=3,
        description="Number of hidden states for the Gaussian HMM regime detector (bull/sideways/bear). A 3-state model typically classifies high, medium, and low volatility regimes. Changing this alters the fundamental clustering behavior of the regime model.",
    )
    HMM_RETRAIN_FREQ_DAYS: int = Field(
        default=7,
        description="Minimum days between HMM refits; fit() calls within this window of the last real fit are no-ops. A lower number means the model adapts faster to sudden market shifts (like flash crashes), but increases computational overhead and may cause temporary over-sensitivity to noise.",
    )
    HMM_COVARIANCE_TYPE: str = Field(
        default="diag",
        description="Covariance structure for Gaussian HMM (diag, full, spherical, tied). 'diag' assumes diagonal covariance; 'full' models cross-feature correlations.",
    )
    HMM_N_ITER: int = Field(
        default=150,
        description="Maximum number of EM iterations when fitting the Gaussian HMM.",
    )
    HMM_TOL: float = Field(
        default=1e-4,
        description="Convergence threshold for Gaussian HMM EM fitting algorithm.",
    )
    HMM_RISK_ON_DOWNGRADE_THRESHOLD: float = Field(
        default=0.30,
        description="Threshold below which rules-based RISK ON regime is downgraded to NEUTRAL if HMM risk-on probability is low.",
    )
    HMM_RISK_OFF_AGREEMENT_THRESHOLD: float = Field(
        default=0.70,
        description="HMM risk-off agreement threshold (1 - risk_on_prob) above which lowered kill-switch thresholds trigger during RECESSION.",
    )
    HMM_CREDIT_SPREAD_FEATURE_ENABLED: bool = Field(
        default=False,
        description="Whether to include High Yield OAS credit spread (BAMLH0A0HYM2) in the HMM feature matrix.",
    )
    HMM_INFLATION_FEATURE_ENABLED: bool = Field(
        default=False,
        description="Whether to include 10-Year Breakeven Inflation Rate (T10YIE) in the HMM feature matrix.",
    )
    HMM_VOL_TERM_SPREAD_FEATURE_ENABLED: bool = Field(
        default=False,
        description="Whether to include 20D-60D volatility term structure spread in the HMM feature matrix.",
    )
    HMM_STANDARDIZE_FEATURES_ENABLED: bool = Field(
        default=False,
        description="Whether to causally rolling-z-score standardize the HMM feature matrix (252d rolling window, min_periods=20) before fitting, on top of HMMRegimeDetector's own full-sample scaling.",
    )
    HMM_N_INITS: int = Field(
        default=1,
        description="Number of random-restart EM fits per HMM refit; the best-scoring fit (by in-sample log-likelihood) is kept. Higher values reduce sensitivity to poor EM local optima at the cost of proportionally more compute per refit.",
    )
    KILLSWITCH_VIX_THRESHOLD_AGREED: float = Field(
        default=25.0,
        description="Lowered VIX threshold for kill switch activation when rules-based regime is RECESSION and HMM confirms risk-off.",
    )
    KILLSWITCH_SAHM_THRESHOLD_AGREED: float = Field(
        default=0.30,
        description="Lowered Sahm rule threshold for kill switch activation when rules-based regime is RECESSION and HMM confirms risk-off.",
    )
    OPTIONS_VRP_THRESHOLD: float = Field(
        default=0.02,
        description="Minimum Volatility Risk Premium (VRP) required to authorize premium selling (e.g. credit spreads). VRP is the difference between Implied Volatility and Realized Volatility. A higher threshold (e.g. 0.03 = 3%) demands a larger premium buffer before entering trades, increasing selectivity and safety but reducing trade frequency.",
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
    # Optional deep-link URL (e.g. "http://localhost:8501") appended to each
    # watch-rule notification body so the operator can jump straight to the
    # dashboard (main._run_cycle -> watch_engine.dispatch_watch_alerts). Never
    # a secret -- see watch_engine.dispatch_watch_alerts's own docstring.
    NTFY_DASHBOARD_URL: Optional[str] = Field(
        default=None,
        description=(
            "Deep-link URL appended to watch-rule ntfy notifications so the "
            "operator can jump to the dashboard. None = link omitted."
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
    WS_RISK_STREAM_INTERVAL_SECONDS: float = Field(default=1.0, description="Poll interval (seconds) for the /ws/risk/portfolio WebSocket stream -- was previously a hardcoded asyncio.sleep(1.0).")
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

    # --- Portfolio-level gross exposure cap (sizing/position_sizer.py) ---
    # Applied ACROSS a cycle's whole universe (after every name's own
    # MAX_POSITION_WEIGHT clamp), on top of -- not instead of -- the per-name
    # ceiling above. Uses apply_portfolio_gross_cap(): the risk-aware
    # portfolio_vol_target path when a covariance matrix is supplied, else a
    # sum-of-|weight| gross-exposure fallback.
    # Calibrated to 2.0 (200% gross exposure ceiling, matching standard Reg-T
    # margin and institutional 130/30 - 2x leverage boundaries). In advisory
    # mode (5% max per name across 20 symbols = 1.0x), this is non-binding;
    # in execution mode with high Kelly allocations across >8 concurrent names,
    # it applies a uniform scalar reducing leverage to 2.0x while preserving
    # relative cross-sectional signal conviction.
    MAX_PORTFOLIO_GROSS: float = 2.0

    # --- Cap-aware escalation (sizing/position_sizer.py + sizing/cap_audit_store.py) ---
    # Opt-in (default False): a name that binds the same hard sizing ceiling
    # for >= SIZING_CAP_ESCALATION_THRESHOLD_CYCLES consecutive cycles gets
    # its weight further scaled by SIZING_CAP_ESCALATION_FACTOR. Disabled by
    # default so existing deployments see no behavior change until an
    # operator explicitly enables it.
    SIZING_CAP_ESCALATION_ENABLED: bool = False
    SIZING_CAP_ESCALATION_THRESHOLD_CYCLES: int = 5
    SIZING_CAP_ESCALATION_FACTOR: float = 0.5

    # --- Cap-event audit + alerting (sizing/cap_audit_store.py) ---
    # Durable log of every cycle's capping events, independent of the
    # in-memory dashboard_df. Best-effort write (see RunHistoryStore
    # precedent): a DB failure only logs a warning, never blocks a run.
    SIZING_CAP_AUDIT_ENABLED: bool = True
    # Opt-in (default False): KELLY_CAP binding is a routine, expected event
    # for an established aggregate-Kelly book, not itself a new risk signal --
    # an unconditional alert here would start emitting brand-new WARNING
    # console/file log lines for every EXISTING deployment the moment this
    # ships, regardless of whether the operator wants sizing-cap alerting at
    # all (the console/file channels in observability.alerts.send_alert are
    # always-on, independent of whether discord/slack/email are configured).
    # Mirrors this repo's convention for new default-off behavior toggles
    # (FORECAST_SKILL_WEIGHTING_ENABLED, ORCHESTRATOR_DAEMON_ENABLED, etc.).
    SIZING_CAP_ALERT_ENABLED: bool = False
    # Fires observability.alerts.send_alert("WARNING", ...) -- the platform's
    # unified multi-channel dispatcher (console/file always-on, plus
    # discord/slack/email when configured; NOT the separate legacy
    # ALERT_WEBHOOK_URL POST, which is order_manager.py's reconciliation-drift-
    # specific mechanism) -- when SIZING_CAP_ALERT_ENABLED is True AND the
    # fraction of names capped in one cycle meets or exceeds this threshold.
    SIZING_CAP_ALERT_THRESHOLD_PCT: float = 0.30

    # --- Symbol rating history (rating/symbol_rating.py, rating/symbol_rating_store.py) ---
    # Durable per-symbol GOOD/BAD rating history, built on top of the
    # existing per-cycle final_score / Action Signal. Diagnostic-only by
    # default -- mirrors SIZING_CAP_AUDIT_ENABLED -- no symbol is ever
    # excluded from tracking/buying by this flag alone.
    SYMBOL_RATING_ENABLED: bool = True
    # A symbol's final_score below this is classified BAD this cycle.
    # Matches strategy_engine.py's own RISK REDUCE cutoff -- the existing
    # single source of truth for "this score is bad", not a fresh
    # independent threshold.
    SYMBOL_RATING_BAD_SCORE_THRESHOLD: float = 35.0
    # Opt-in (default False): when True, a non-held symbol rated BAD for
    # SYMBOL_RATING_DROP_THRESHOLD_CYCLES consecutive cycles is subtracted
    # from the resolved tracked universe (data/portfolio_sync.py::resolve_universe,
    # main.py::_build_universe) -- stops being fetched, scored, or bought.
    # Defaults False like every other live-trading-behavior flag in this
    # codebase (SIZING_CAP_ESCALATION_ENABLED, ETF_TRANSMISSION_SIZING_ENABLED)
    # so nothing changes silently on a git pull for a live capital account.
    # A currently-held position is NEVER excluded regardless of this flag --
    # see rating/symbol_rating.py::should_exclude.
    SYMBOL_RATING_AUTO_DROP_ENABLED: bool = False
    # Consecutive BAD-rated cycles (non-held symbols only) before auto-drop,
    # when SYMBOL_RATING_AUTO_DROP_ENABLED is True. Mirrors
    # SIZING_CAP_ESCALATION_THRESHOLD_CYCLES's default.
    SYMBOL_RATING_DROP_THRESHOLD_CYCLES: int = 5

    # --- ETF volatility-transmission sizing derate (risk/etf_transmission.py) ---
    # Ben-David, Franzoni & Moussawi (2018, JF): ETF arbitrage transmits a
    # shock in one constituent to its healthy peers, so a heavily ETF-wrapped
    # name carries extra non-fundamental, non-diversifiable variance that the
    # per-name Kelly / vol-target formulas structurally cannot see. Applied as
    # a bounded post-multiplier in sizing/position_sizer.py::size_position
    # step 3 (NOT as vol inflation into Kelly -- see risk/etf_transmission.py's
    # module docstring for why that lever is broken).
    ETF_TRANSMISSION_SIZING_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for the per-name ETF-volatility-transmission "
            "position-sizing derate. False (the default) is a complete no-op: "
            "no multiplier is computed, ETF_Transmission_Multiplier stays NaN "
            "in config.COLUMN_SCHEMA, and size_position() composes exactly "
            "the pre-change weight (the derate it receives is the identity "
            "1.0). Independent of any ETF holdings/co-movement MEASUREMENT "
            "being available -- with coverage missing the derate is still 1.0, "
            "never NaN, because a data outage must never relax a risk limit."
        ),
    )
    ETF_TRANSMISSION_MAX_DERATE: float = Field(
        default=0.30,
        description=(
            "Largest fraction of a name's composed sizing weight this overlay "
            "may ever remove, reached only at (or past) "
            "ETF_TRANSMISSION_OWNERSHIP_REFERENCE ETF ownership AND a "
            "constituent-on-ETF return R-squared of 1.0. 0.30 = at most a 30% "
            "haircut. Only consulted once ETF_TRANSMISSION_SIZING_ENABLED is "
            "True."
        ),
    )
    ETF_TRANSMISSION_OWNERSHIP_REFERENCE: float = Field(
        default=0.20,
        description=(
            "ETF-ownership FRACTION of shares outstanding (0.20 = 20%) at "
            "which the ownership factor of the derate saturates at 1.0; "
            "ownership beyond this point does not escalate the haircut "
            "further. Only consulted once ETF_TRANSMISSION_SIZING_ENABLED is "
            "True."
        ),
    )
    ETF_TRANSMISSION_MIN_MULTIPLIER: float = Field(
        default=0.50,
        description=(
            "Hard lower bound on the transmission multiplier -- no combination "
            "of ETF ownership, co-movement, or knob settings can shrink a "
            "position below this fraction of its otherwise-composed weight "
            "through this overlay (it is a risk derate, not a kill switch; "
            "exiting a name is the signal layer's job, not this one's). Only "
            "consulted once ETF_TRANSMISSION_SIZING_ENABLED is True."
        ),
    )

    # --- Runtime / IO ---
    LOCAL_DATA_ROOT: Path = Field(
        default=Path.home() / ".stockpy_local",
        description=(
            "Machine-global root for ALL locally-generated model/data artifacts "
            "(trained models, SQLite DBs, caches, logs) -- lives OUTSIDE every "
            "git worktree/checkout on purpose. This repo runs many worktrees "
            "simultaneously; untracked files are worktree-local in git, so a "
            "model trained in one worktree was previously invisible from every "
            "other one even though nothing was deleted. Every worktree/checkout "
            "on this machine reads/writes the SAME physical files here with zero "
            "per-worktree .env setup. Override via the LOCAL_DATA_ROOT env var "
            "to relocate to an external drive/NAS/cloud-synced folder. "
            "OUTPUT_DIR and every other LOCAL_DATA_ROOT-relative module constant "
            "derive their default from this value -- see "
            "docs/architecture/data-layer.md."
        ),
    )
    OUTPUT_DIR: Optional[Path] = Field(
        default=None,
        description="Directory for generated reports. Defaults to <LOCAL_DATA_ROOT>/output when unset.",
    )
    NO_VENV_REEXEC: bool = Field(
        default=False,
        description=(
            "Opt-out flag for scripts/_bootstrap.py: when True, suppresses "
            "automatic re-execution under .venv's interpreter when invoked under "
            "an external Python environment."
        ),
    )
    DEFAULT_TICKERS: list[str] = Field(default_factory=lambda: ["AAPL", "MSFT", "JNJ", "AGNC"])
    SYNC_WATCHLIST_FILES: Optional[str] = Field(
        default=None,
        description=(
            "Colon-separated paths (shell PATH convention) to additional "
            "plain-text watchlist files (one ticker per line, '#' = comment) "
            "consumed by data.robinhood_client.discover_universe(). Missing "
            "files are tolerated silently. See data/portfolio_sync.py's "
            "Portfolio & Watchlist Synchronization docs for the full union."
        ),
    )
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
    FORECAST_CNN_LSTM_WALKFORWARD_SCALING: bool = Field(
        default=False,
        description=(
            "Opt-in, stricter alternative to ForecastingEngine.fit_scalers_on_train's "
            "single train/reserve MinMaxScaler split. That split is already leak-free "
            "for the live single-shot forecast (the emitted forecast never depends on "
            "future data relative to inference time), but an EARLY training window's "
            "scale still reflects statistics pooled from LATER rows within the train "
            "span via the one shared scaler. When True, ForecastingEngine.run_cnn_lstm_"
            "forecast builds training windows via fit_scalers_walkforward_windows "
            "instead: each supervised window is scaled using only an expanding "
            "min/max computed from rows strictly at/before that window's own end "
            "(vectorized via numpy cumulative min/max, not a per-window sklearn "
            "refit). The final live inference window is unaffected either way -- it "
            "still uses the train-span scaler, since at inference time 'now' truly is "
            "the most recent data available. False (the default) reproduces "
            "pre-existing behavior exactly -- matches the FORECAST_USE_GARCH_SIGMA "
            "opt-in convention. Intended for high-fidelity walk-forward backtesting, "
            "not the live pipeline; costs more compute per fit."
        ),
    )

    # --- Strategy validation harness OOS gate (validation/harness.py) ---
    VALIDATION_HARNESS_OOS_GATE_ENABLED: bool = Field(
        default=False,
        description=(
            "Opt-in fix for StrategyValidationHarness's deployability gate. Two "
            "related integrity gaps: (1) report.sharpe/max_dd/sortino/calmar/"
            "hit_rate/avg_trade_pct/turnover were computed from "
            "self.strategy_fn(X, y, X, y) -- a 'test' set IDENTICAL to the "
            "training set, i.e. an IN-SAMPLE number feeding the 'net-of-cost "
            "Sharpe > 0.5' / 'MaxDD < 30%' deployability criteria -- while only "
            "PBO/DSR were genuinely out-of-sample (via CombinatorialPurgedCV). "
            "(2) CombinatorialPurgedCV's own DSR/PBO Sharpes were computed on "
            "GROSS (cost-free) returns even though the in-sample Sharpe/MaxDD "
            "leg applied _apply_cost_model's turnover-scaled cost -- an "
            "inconsistent cost basis between the two gate legs. When True, "
            "run_cpcv_evaluation applies the same turnover-scaled cost model to "
            "every CPCV path's train/test returns before any Sharpe/PBO/DSR/"
            "drawdown statistic is computed from them, and the harness's "
            "reported sharpe/max_dd/sortino/calmar/hit_rate/avg_trade_pct/"
            "turnover become the MEAN of each metric computed independently on "
            "every CPCV path's own genuinely held-out (purged+embargoed) OOS "
            "returns for the DSR-selected strategy, instead of the full-sample "
            "in-sample fit -- see run_cpcv_evaluation's docstring for why this "
            "is a per-path mean rather than one concatenated equity curve "
            "(CPCV's combinatorial test blocks are deliberately reused across "
            "paths). equity_curve/benchmark_curve/macro_benchmark_curve are "
            "UNCHANGED either way (still the full-sample series) -- a single "
            "non-overlapping OOS equity curve needs the AFML CPCV backtest-"
            "path-recombination algorithm, not implemented here (a real, "
            "separate follow-up, not silently faked). False (the default) "
            "reproduces pre-existing behavior exactly: every currently-recorded "
            "docs/VALIDATION_STRATEGY_FIX_LOG.md PBO/DSR/Sharpe/MaxDD baseline "
            "for the registered STRATEGY_REGISTRY fleet was measured with this "
            "flag off, and this sandboxed dev/CI environment has no live-market "
            "network access to re-verify the fleet against the corrected "
            "numbers -- flipping this on requires re-running "
            "scripts/refresh_validations.py against live data and updating that "
            "log, exactly like this codebase's other opt-in correctness levers "
            "(e.g. FORECAST_CNN_LSTM_WALKFORWARD_SCALING above, "
            "ETF_TRANSMISSION_SIZING_ENABLED)."
        ),
    )

    # --- DSR single-trial correction (validation/metrics.py) ---
    VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED: bool = Field(
        default=False,
        description=(
            "Opt-in fix for validation/metrics.py::deflated_sharpe_ratio's "
            "n_trials<=1 shortcut, which unconditionally returns 1.0 (a "
            "perfect deflated Sharpe) for any single-trial strategy instead "
            "of actually computing the DSR test statistic -- so a strategy "
            "with only one configuration always passes the 'DSR > 0.95' "
            "deployability gate regardless of how weak its observed Sharpe, "
            "skew, or kurtosis actually are. This bug is directly relied on "
            "today by 5 STRATEGY_REGISTRY strategies that hit DSR=1.000 "
            "exactly via this shortcut -- multifactor_lowvol_size, "
            "garch_vol_target, cross_sectional_momentum, "
            "relative_strength_xsec, timeseries_momentum (confirmed in "
            "docs/VALIDATION_STRATEGY_FIX_LOG.md) -- and are currently "
            "recorded deployable=True, so the corrected math ships opt-in "
            "rather than silently changing any currently-recorded verdict. "
            "False (the default) reproduces the pre-existing `return 1.0` "
            "shortcut byte-for-byte. True sets sr_0 = 0.0 (mathematically "
            "correct: with genuinely only one trial there is no "
            "multiple-testing selection-bias penalty to deflate for) and "
            "falls through to compute the REAL z_stat/norm.cdf from the "
            "actual sr_observed/skew/kurtosis/n_observations, instead of "
            "short-circuiting to a hardcoded perfect pass. Flipping this on "
            "requires a follow-up session with live-market data access to "
            "re-run scripts/refresh_validations.py against the 5 strategies "
            "named above and update docs/VALIDATION_STRATEGY_FIX_LOG.md "
            "before this can ever change what's actually live -- exactly "
            "like this codebase's other opt-in correctness levers (e.g. "
            "VALIDATION_HARNESS_OOS_GATE_ENABLED above)."
        ),
    )

    # --- LGBM ranker native MultiIndex CPCV (ml/lgbm_ranker.py) ---
    LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED: bool = Field(
        default=False,
        description=(
            "Opt-in: LGBMCrossSectionalRanker.train() calls "
            "CombinatorialPurgedCV.split() directly on the (date, ticker) "
            "MultiIndex panel (PR #648's native MultiIndex support) instead of "
            "flattening to a date-only index first before purging/embargoing. "
            "Default False preserves today's exact flatten-path behavior for "
            "every existing caller -- train()'s own use_native_multiindex_cv "
            "kwarg always overrides this when explicitly passed (True or "
            "False); this setting is only consulted when a caller leaves that "
            "kwarg unset (None). The native path additionally REQUIRES an "
            "explicit t1 (raises ValueError otherwise) -- CombinatorialPurgedCV "
            "cannot safely synthesize a default t1 across a MultiIndex -- while "
            "the flatten path keeps silently synthesizing a 'next row' default "
            "t1 when none is supplied, exactly as it always has."
        ),
    )
    BERT_LLA_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for the BERT-LLA multi-horizon forecaster "
            "(forecasting/bert_lla.py -- PyTorch dual-LSTM + self-attention, "
            "three registered ablations: lstm_baseline, lstm_attention, "
            "bert_lla). False (the default) is a complete no-op: "
            "ForecastingEngine.run_bert_lla_forecast() returns the zero "
            "sentinel without ever touching torch. Requires the optional "
            "torch package (already in requirements-optional.txt for local "
            "FinBERT inference) -- absent, the same zero-sentinel behavior "
            "applies regardless of this flag."
        ),
    )
    BERT_LLA_BLEND_ENABLED: bool = Field(
        default=False,
        description=(
            "Whether the 'bert_lla' ablation's price (not lstm_baseline/"
            "lstm_attention -- those are comparison-only and NEVER blend-"
            "eligible regardless of this flag) is added to "
            "ForecastingEngine's model_forecasts dict and therefore "
            "influences the live skill-weighted blended forecast. False "
            "(the default): bert_lla still RECORDS to forecast_errors for "
            "the webapp's model-comparison chart, but its error history "
            "accrues honestly before it can ever move a recommendation -- "
            "mirrors FORECAST_SKILL_WEIGHTING_ENABLED's 'measure first, act "
            "later' posture. Only consulted once BERT_LLA_ENABLED is True."
        ),
    )
    BERT_LLA_ABLATION_ENABLED: bool = Field(
        default=False,
        description=(
            "When True, generate_forecast() runs all three BERT-LLA "
            "ablations (lstm_baseline, lstm_attention, bert_lla) instead of "
            "just 'bert_lla' alone -- three PyTorch trainings per ticker per "
            "cycle instead of one. False (the default) keeps the marginal "
            "compute cost to a single model. Only consulted once "
            "BERT_LLA_ENABLED is True."
        ),
    )
    BERT_LLA_WINDOW_SIZE: int = Field(
        default=22,
        description=(
            "Lookback window (trading days) BERT-LLA's LSTM layers consume, "
            "replacing the CNN-LSTM path's hardcoded LSTM_LOOKBACK=60 -- "
            "matches the source methodology's 22-trading-day window. Only "
            "consulted once BERT_LLA_ENABLED is True."
        ),
    )
    BERT_LLA_MIN_SENTIMENT_COVERAGE: float = Field(
        default=0.5,
        description=(
            "Hard gate for the 'bert_lla' ablation specifically (not "
            "lstm_baseline/lstm_attention, which consume no sentiment): the "
            "minimum fraction of rows in the feature window that must have "
            "an OBSERVED composite-sentiment-index reading "
            "(signals.sentiment_index) before training proceeds. Below this "
            "threshold, run_bert_lla_forecast returns the zero sentinel "
            "rather than training on a mostly mask-zeroed sentiment channel "
            "(CONSTRAINT #4) -- SENTIMENT_INGESTION_ENABLED defaults False "
            "and SENTIMENT_PIT_MIN_MONTHS=6 is this platform's own bar for "
            "trusting sentiment history, so this gate will bind for months "
            "after an operator first enables sentiment ingestion, by "
            "design. Only consulted once BERT_LLA_ENABLED is True."
        ),
    )
    CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED: bool = Field(
        default=True,
        description=(
            "Fix for the CNN-LSTM/TensorFlow deadlock documented in "
            "docs/known_issues/cnn_lstm_tf_deadlock.md (issue #381). Root cause: "
            "TensorFlow and pyarrow each ship an independently-compiled copy of the "
            "same Abseil sync primitive; whichever library's Python-level init runs "
            "first in the PROCESS wins that symbol, and if pandas/pyarrow initialize "
            "first, the first real multi-threaded TF eager op (a Conv1D/LSTM .fit()) "
            "deadlocks forever. Reordering forecasting_engine.py's own imports "
            "(always-on, unconditional) only helps when this module is the first "
            "thing in the whole process to touch pandas -- true in an isolated test "
            "script, false in main.py/main_orchestrator.py/pipeline/production_steps.py, "
            "which all import pandas before forecasting_engine is ever reached (those "
            "three files carry their own guarded `import tensorflow` before their own "
            "`import pandas` as a defense-in-depth second layer -- see Fix 2 in the "
            "doc -- but that convention is unenforced for any OTHER entry point, "
            "script, or notebook that happens to reach this code path). When True "
            "(the default), ForecastingEngine.run_cnn_lstm_forecast runs the actual "
            "TF-touching work (model fit+predict, and cached-model load+predict) in "
            "a persistent worker pool (repo-root cnn_lstm_process_pool.py) whose "
            "worker module (repo-root cnn_lstm_worker.py -- deliberately NOT inside "
            "forecasting/, since that package's __init__ eagerly imports pandas) "
            "imports tensorflow before anything else and runs as its own genuine OS "
            "process, launched via subprocess.Popen -- a fresh interpreter per "
            "worker means the parent process's import order can no longer matter, "
            "unlike the module-level reorder alone or the entry-point guards. This "
            "is what actually removes the process-scope constraint, rather than "
            "merely mitigating it by convention: it protects EVERY caller, known or "
            "not, not just the three files that remember the guard. As of "
            "2026-08-04 (Round 8 of the known-issues doc), workers are launched with "
            "subprocess.Popen rather than multiprocessing -- a second, distinct "
            "deadlock (unrelated to the Abseil ODR collision above) was found in "
            "multiprocessing-managed worker processes specifically; see Round 8 for "
            "the full ablation matrix. All feature engineering / windowing / "
            "scaling stays in the parent process unchanged (pandas-only, never "
            "touches TF). Any subprocess failure (timeout, a dead/unresponsive "
            "worker, real training exception) is caught by run_cnn_lstm_forecast's "
            "existing outer try/except and degrades to the zero-result sentinel -- "
            "never crashes the pipeline (CONSTRAINT #6). This default flipped True "
            "on 2026-07-31 (Round 7 of the known-issues doc) once Round 6 "
            "(2026-07-27) verified subprocess isolation end-to-end against the real "
            "native deadlock on real production data in the actual macOS arm64 + "
            "Framework-Python environment the deadlock was originally confirmed on "
            "-- the earlier caveat about this being verified only against the "
            "mocked test suite no longer applies. Set False only to restore the "
            "legacy in-process path (byte-identical to this flag's original "
            "pre-2026-07-31 default); doing so re-exposes the process-scope "
            "import-order hazard for any entry point that doesn't carry its own "
            "guarded `import tensorflow` before `import pandas`/`import pyarrow`."
        ),
    )
    CNN_LSTM_PROCESS_POOL_WORKERS: int = Field(
        default=1,
        description=(
            "Worker-process count for the CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED "
            "pool (repo-root cnn_lstm_process_pool.py). Workers are persistent "
            "(survive across tickers/cycles, each pays the TensorFlow import cost "
            "only once) so CNN-LSTM fits queued from pipeline/production_steps.py's "
            "per-ticker ThreadPoolExecutor fan-out share this fixed-size pool rather "
            "than spawning a fresh interpreter per ticker. Shipped default is 1; "
            "an operator tuning for higher pipeline concurrency on multi-core "
            "systems can increase this (recommended: 3 workers) while monitoring "
            "per-worker TensorFlow process memory."
        ),
    )
    CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS: int = Field(
        default=300,
        description=(
            "Max seconds to wait for a single CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED "
            "fit-or-predict call before giving up and falling back to the zero-result "
            "sentinel (never blocks the pipeline indefinitely -- the entire point of "
            "this fix is to replace an unbounded hang with a bounded, recoverable "
            "failure). 50 epochs with EarlyStopping(patience=5) on the modest window "
            "sizes this codebase trains on should complete well within the default."
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
    # Per-sub-fetch bound for main_orchestrator.py::fetch_all_data_async()'s three
    # concurrent asyncio.to_thread() tasks (macro/fundamentals/technical). Added
    # 2026-08 after a real incident: none of the three had ANY timeout, and a
    # stalled FRED connection (via DataEngine.fetch_macro_raw()) blocked the
    # entire "data" pipeline stage -- and therefore the whole cycle -- forever,
    # with nothing else re-triggering a fresh cycle. See
    # docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md. A timeout
    # here is caught by the SAME existing per-task dead-letter isinstance(x,
    # Exception) handling already used for a raised exception (TimeoutError is
    # an Exception subclass) -- no new fallback logic needed.
    DATA_FETCH_TASK_TIMEOUT_SECONDS: float = Field(
        default=180.0,
        description=(
            "Per-sub-fetch timeout (seconds) for each of the three concurrent "
            "tasks in fetch_all_data_async() (macro/fundamentals/technical). "
            "On expiry that ONE sub-fetch degrades to its existing empty-dict "
            "dead-letter sentinel (matching a raised exception's handling "
            "exactly) rather than blocking the cycle forever. Grounded in "
            "FMP_MAX_SECONDS_PER_CYCLE (120.0, the fundamentals/technical "
            "path's own internal FMP wall-clock budget) plus headroom for the "
            "yfinance fallback path, while staying well below any hang that "
            "should be treated as abnormal."
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
    # Read-only stall watchdog, added 2026-08 after a real incident: a pipeline
    # cycle wedged in the "data" stage (unbounded FRED call, see
    # DATA_FETCH_TASK_TIMEOUT_SECONDS / FRED_REQUEST_TIMEOUT_SECONDS above and
    # docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md) for 2.5
    # days with nothing surfacing that fact -- the daemon's Control/Pilots APIs
    # stayed responsive throughout (they run on a separate thread from the
    # cycle), so nothing looked obviously broken until an operator happened to
    # ask. Deliberately alert-only, never auto-restart/auto-cancel: forcibly
    # killing a mid-flight cycle risks corrupting partial state, and this same
    # process hosts the APIs the webapp depends on -- trading a silent hang for
    # a guaranteed outage on every future stall would be a regression, not a
    # fix. Defaults to True (a deliberate deviation from this repo's usual
    # "new instrumentation defaults off" convention): this is pure read-only
    # alerting with zero external blast radius when no webhook/email channel is
    # configured (observability.alerts._active_channels() always includes
    # "console"), it targets a proven, costly incident, and it never mutates
    # anything.
    PIPELINE_STALL_ALERT_ENABLED: bool = Field(
        default=True,
        description=(
            "Enable OrchestratorDaemon.maybe_alert_on_pipeline_stall(), which "
            "fires a WARNING (via observability.alerts.send_alert, "
            "dedup_key='pipeline_stall') whenever output/progress.json reports "
            "state='running' with no update for longer than "
            "PIPELINE_STALL_ALERT_SECONDS. Read-only -- never restarts the "
            "daemon or cancels the wedged cycle."
        ),
    )
    PIPELINE_STALL_ALERT_SECONDS: int = Field(
        default=1800,
        description=(
            "Threshold (seconds) of no progress.json update while "
            "state='running' before the stall alert fires. Set well above "
            "DATA_FETCH_TASK_TIMEOUT_SECONDS's worst case and any legitimate "
            "single pipeline-stage duration -- 1800s (30 min) is two orders of "
            "magnitude below the multi-hour/multi-day hang this was added to "
            "catch."
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
    # Gate automatic interval-triggered pipeline cycles to extended market hours.
    ORCHESTRATOR_EXTENDED_HOURS_ONLY: bool = Field(
        default=True,
        description=(
            "Skip automatic interval-triggered pipeline cycles (daemon timer and "
            "main.py --interval) outside the 4am-8pm ET weekday window "
            "(engine.advisory_agent.is_extended_hours) -- not strict 9:30-16:00 RTH. "
            "Manual/on-demand triggers (webapp buttons, API calls) are never gated. "
            "No holiday calendar is applied (same known limitation as "
            "is_us_market_open); default True fixes previously-unconditional 24/7 "
            "automatic runs."
        ),
    )
    # Cross-process settings hot-reload: whether the persistent orchestrator
    # daemon (desktop/orchestrator_daemon.py) periodically re-checks
    # output/runtime_flags.json for changes written by ANOTHER process (e.g.
    # a Pilots-PWA settings PUT served by a separate `api/pilots_api.py`
    # process) and applies them onto its own long-lived `settings` singleton.
    # A store write served by the daemon's OWN process (PILOTS_API_ENABLED=True,
    # hosting pilots_api inside the daemon) already applies immediately via
    # the settings-store write path's own in-process apply -- this
    # flag only matters for a store write from a DIFFERENT process. True
    # (the default) enables cross-process hot-reloading: the daemon
    # periodically re-reads the store and applies changes without a
    # restart. False reproduces the original behavior, where a
    # cross-process write only took effect on the daemon's next restart.
    RUNTIME_FLAGS_REFRESH_ENABLED: bool = Field(
        default=True,
        description=(
            "Periodically re-check output/runtime_flags.json for changes "
            "written by another process and apply them onto this daemon's "
            "live settings. True (default) enables cross-process hot-reloading "
            "of live-safe settings without requiring a full restart."
        ),
    )
    # Poll cadence for the refresher above. Irrelevant when the flag is off.
    # Deliberately independent of ORCHESTRATOR_INTERVAL_SECONDS (the pipeline
    # cycle cadence) -- a settings check is a single os.stat() plus, only on
    # a real change, one validated re-apply, cheap enough to poll far more
    # often than a full pipeline cycle without meaningful cost.
    RUNTIME_FLAGS_REFRESH_INTERVAL_SECONDS: int = Field(
        default=30,
        gt=0,
        description=(
            "Seconds between the orchestrator daemon's checks of "
            "output/runtime_flags.json for cross-process changes. Only "
            "consulted when RUNTIME_FLAGS_REFRESH_ENABLED is True."
        ),
    )
    # Total wall-clock budget (2026-07 fix) for desktop/orchestrator_daemon.py's
    # _teardown() -- ONE explicit, published number every parent supervisor
    # sizes its own kill timeout against, replacing what used to be an
    # unreconciled sum of independent hardcoded values (5s Control-API join +
    # 5s Pilots-API join + a 10s daemon.shutdown() call that ITSELF hardcoded
    # a 5s timer-thread join before its own poll -- 20-25s nobody had actually
    # added up). Enforced as a single monotonic deadline captured at the top
    # of _teardown(): each stage gets min(its own fixed ceiling, time left on
    # the deadline), so raising or lowering this number can only ever be a
    # STRICT tightening or loosening of the total, never a surprise from one
    # sub-stage alone.
    #
    # Does NOT wait out an in-flight pipeline cycle -- daemon.shutdown()
    # polls for one, then gives up and returns anyway; a cycle can take
    # minutes and there is no safe way to abort mid-flight (see
    # pipeline/runner.py's own docstring on why). This budget only bounds the
    # genuinely-bounded stages: uvicorn drain, timer-thread join, and that
    # final poll's own grace period.
    #
    # 25.0 is the outer bound of every configuration reachable BEFORE this
    # fix (20s with the Pilots API off, 25s with it on -- production, via
    # launchd, runs with it on) -- so this default cannot make any existing
    # deployment's teardown budget shorter than it already was. Raising it
    # without ALSO raising the outer supervisor timeouts that must exceed it
    # (launch_app.command's SHUTDOWN_GRACE_SECONDS, launchd's ExitTimeOut,
    # systemd's TimeoutStopSec) makes shutdown WORSE, not better -- the
    # daemon would simply get SIGKILLed mid-teardown at a different point.
    # See docs/RUNBOOK.md's shutdown-budget-ladder table.
    DAEMON_SHUTDOWN_TIMEOUT_SECONDS: float = Field(
        default=25.0,
        description=(
            "Total seconds budgeted for the orchestrator daemon's graceful "
            "teardown (Control API + Pilots API drain, timer-thread join, "
            "final in-flight-run poll). Does not wait out an in-flight "
            "pipeline cycle. Must stay below the outer supervisor timeouts "
            "(launch_app.command, launchd ExitTimeOut, systemd "
            "TimeoutStopSec) or shutdown gets worse, not better."
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
            # Sector-Neutral Earnings-Quality Rank (accrual quality +
            # gross profitability, ranked within sector) — 15.0 matches the
            # magnitude convention used by the other multi-input
            # cross-sectional modules (cross_sectional_momentum, multifactor).
            # NOTE: as of introduction, its raw inputs (accrual_ratio,
            # gross_profitability) are not yet populated anywhere in the live
            # per-cycle data path, so this module contributes 0.0 (neutral,
            # via its own WARNING-logged missing-column guard) until a
            # follow-up data-plumbing task wires them in — see
            # docs/signals/sector_quality_rank.md's Data Availability Gap.
            "sector_quality_rank": 15.0,
            # VRP options-premium-selling regime gate (True_IVR > 50, VRP >
            # 2%, VIX < 30, no CREDIT EVENT) -- modest starting weight,
            # matching the convention for a module still building a track
            # record (lgbm_ranker: 0.10, news_catalyst: 10.0). Only scores
            # WHETHER the regime favors selling premium; does not price or
            # select strikes itself.
            "vrp_premium_selling": 10.0,
            # Institutional options order flow net sentiment score in [-1.0, 1.0]
            # based on aggressive sweeps vs bids from Unusual Options Activity.
            "options_flow_sentiment": 10.0,
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
    SENTIMENT_INGESTION_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for NewsCatalystSignal.pre_compute()'s multi-source "
            "ingestion step (data/sentiment_sources.py's CompositeSentimentSource "
            "-- Yahoo RSS/GDELT/Reddit/EDGAR). False (the default) is a complete "
            "no-op: no network call is attempted for any symbol, matching this "
            "codebase's convention for opt-in networked features "
            "(ORCHESTRATOR_DAEMON_ENABLED, GRAVITY_AI_RUNNER_ENABLED "
            "default False the same way). This exists "
            "because two of the four sources (Yahoo RSS, GDELT) need no API key "
            "and so have no other way to stay quiet by default -- unlike "
            "Finnhub/Reddit/EDGAR, which already degrade to a no-op when their "
            "credentials are absent. Set True in .env to actually start "
            "accumulating sentiment_ingestion_audit history; until then, "
            "SENTIMENT_PIT_MIN_MONTHS never starts counting."
        ),
    )
    SENTIMENT_AUDIT_ENABLED: bool = Field(
        default=True,
        description=(
            "When True, sentiment-ingestion sources write each ingested "
            "document to HistoricalStore's sentiment_ingestion_audit table "
            "(via HistoricalStore.save_sentiment_documents()) -- the per-"
            "document point-in-time archive underlying the credibility-"
            "weighted sentiment signal (Sentiment Pipeline Phase 2+). Same "
            "on/off shape as NEWS_HISTORY_CAPTURE_ENABLED. Dead-lettered: any "
            "capture failure is logged and never crashes the pipeline. Has no "
            "effect while SENTIMENT_INGESTION_ENABLED is False (nothing is ever "
            "fetched to archive in the first place)."
        ),
    )
    SENTIMENT_PIT_MIN_MONTHS: int = Field(
        default=6,
        description=(
            "Minimum months of accumulated point-in-time history in "
            "news_history / sentiment_ingestion_audit required before the "
            "validation harness may make a deployability claim for a "
            "sentiment-derived signal -- matches the '~6-12 months' policy "
            "already documented in docs/signals/news_catalyst.md. Read by "
            "the validation gating check, never re-typed as a literal "
            "elsewhere. A future gating check should apply this PER SOURCE "
            "GROUP via HistoricalStore.get_sentiment_archive_depth_by_source() "
            "-- institutional sources (gdelt/edgar/finnhub, backfillable via "
            "scripts/backfill_sentiment_history.py with zero credibility bias) "
            "can honestly satisfy this bar much sooner than Reddit/live-only "
            "accumulation; one blended check across all sources would "
            "overstate confidence in whichever source is actually shallowest."
        ),
    )

    # --- Multi-source sentiment ingestion (Sentiment Pipeline Phase 3,
    # data/sentiment_sources.py) ---
    # Free-first sources by default (Yahoo RSS, GDELT, Reddit, SEC/EDGAR,
    # existing Finnhub). Each source is independently try/excepted in
    # CompositeSentimentSource -- one source's outage or missing credentials
    # never blocks the others (CONSTRAINT #6). A paid feed can be added later
    # as a SentimentSource subclass without changing this list's shape.
    SENTIMENT_SOURCES: str = Field(
        default="yahoo_rss,gdelt,reddit,edgar",
        description=(
            "Comma-separated list of enabled data/sentiment_sources.py "
            "provider names. Mirrors the MARKET_DATA_PROVIDER selection "
            "pattern in data/market_data.py, but as a fan-out set rather "
            "than a mutually-exclusive choice -- every listed source "
            "contributes documents each cycle. Removing a name disables "
            "that source without touching code. 'finnhub' is EXCLUDED from "
            "the default: NewsCatalystSignal.pre_compute() already fetches "
            "and scores Finnhub headlines directly every cycle (writing to "
            "news_history); adding 'finnhub' here too would double-fetch the "
            "same API per symbol per cycle. Add it explicitly only if the "
            "direct Finnhub path is ever retired in favor of this composite. "
            "'fmp_news' (data/sentiment_sources.py's FMPNewsSource) is "
            "EXCLUDED from the default for the IDENTICAL reason, even though "
            "FMP is this codebase's primary market-data/fundamentals/news "
            "provider (MARKET_DATA_PROVIDER='fmp', FUNDAMENTALS_SOURCE='fmp', "
            "FMP_NEWS_ENABLED default True): NewsCatalystSignal.pre_compute()'s "
            "_score_via_provider() -> signals.news_catalyst."
            "fetch_company_headlines() already calls the SAME data.fmp_client."
            "stock_news() endpoint FMP-first, per symbol, every cycle, whenever "
            "FMP_NEWS_ENABLED+FMP_API_KEY are set -- adding 'fmp_news' here too "
            "would fire a second /news/stock call per symbol per cycle for the "
            "same headlines. This is a double-fetch, not a broader/different "
            "data pull: FMPNewsSource.fetch() and _fetch_company_headlines_fmp() "
            "both paginate the same stock_news(symbol, from_date, to_date, page, "
            "limit) call with only a lookback-window difference (this composite "
            "path's SENTIMENT_INGESTION_LOOKBACK_DAYS vs. the direct path's "
            "NEWS_LOOKBACK_DAYS). Add it explicitly only if the direct FMP "
            "headline path in news_catalyst.py is ever retired in favor of "
            "this composite, mirroring the 'finnhub' guidance above."
        ),
    )
    SENTIMENT_COMMENT_SOURCES: str = Field(
        default="reddit,stocktwits",
        description=(
            "Comma-separated subset of SENTIMENT_SOURCES provider names "
            "classified as investor-forum COMMENT sources (subjective, "
            "retail-authored) rather than NEWS sources (objective, "
            "editorially-published) -- see data/sentiment_source_class.py's "
            "classify_source(). Every source_name NOT listed here is treated "
            "as news. 'stocktwits' is a real source (data.sentiment_sources."
            "StockTwitsSource) but is not itself in SENTIMENT_SOURCES' "
            "default fan-out and requires STOCKTWITS_ENABLED to actually "
            "fetch anything -- listing it here only pre-classifies it, it "
            "does not enable it."
        ),
    )
    SENTIMENT_INDEX_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for the composite sentiment index S_t = "
            "w1*news_score + w2*review_score (signals/sentiment_index.py). "
            "False (the default) is a complete no-op: no "
            "sentiment_ingestion_audit read is attempted. Reuses "
            "SECTOR_SELECTION_W1/SECTOR_SELECTION_W2 for w1/w2 rather than "
            "a second, redundant weight pair -- see that pair's own "
            "description and signals/sentiment_index.py's module docstring."
        ),
    )
    SENTIMENT_INGESTION_LOOKBACK_DAYS: int = Field(
        default=1,
        description=(
            "Calendar days of lookback each CompositeSentimentSource.fetch_all() "
            "cycle requests from every enabled source (Yahoo RSS/GDELT/Reddit/"
            "EDGAR/Finnhub). Deliberately shorter than NEWS_LOOKBACK_DAYS "
            "(the Finnhub-only headline signal's own 7-day window): these are "
            "higher-velocity sources meant to be polled frequently, with the "
            "rolling dedup hash absorbing any overlap between cycles rather "
            "than relying on a wide backward window."
        ),
    )
    # ── GDELT shared rate limiter ────────────────────────────────────────
    # GDELT publishes no hard rate-limit number, so these are a conservative
    # choice tunable against observed behaviour, NOT a documented contract.
    # One budget is shared by both GDELT consumers (GDELTSource's per-symbol
    # artlist calls and GDELTVolumeSource's per-sector timelinevol calls) --
    # see data/sentiment_sources.py's "GDELT shared rate limiter" section.
    # GDELT_MIN_REQUEST_INTERVAL_SECONDS=0 with GDELT_MAX_RETRIES=0 and
    # GDELT_COOLDOWN_THRESHOLD=0 reproduces the pre-limiter behaviour exactly.
    GDELT_MIN_REQUEST_INTERVAL_SECONDS: float = Field(
        default=5.0,
        description=(
            "Minimum seconds between GDELT DOC API request ISSUANCE, shared "
            "process-wide across GDELTSource and GDELTVolumeSource. Sized for "
            "the case that actually breaks without it: a multi-month "
            "backfill (scripts/backfill_sentiment_history.py) fires ~26 "
            "windowed requests PER SYMBOL, which unthrottled draws HTTP 429 "
            "for substantially all of them. Note the interaction with "
            "SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE: these sleeps count "
            "against that budget, so a live cycle covers fewer symbols' GDELT "
            "documents per pass than an (unthrottled, largely 429-ing) one "
            "attempted to. 0 disables spacing entirely."
        ),
    )
    GDELT_MAX_RETRIES: int = Field(
        default=2,
        description=(
            "Retries after a GDELT HTTP 429/5xx before the request is given "
            "up on, with exponential backoff from "
            "GDELT_RETRY_BACKOFF_SECONDS (a Retry-After response header, when "
            "present and parseable, takes precedence over the computed wait). "
            "0 disables retrying."
        ),
    )
    GDELT_RETRY_BACKOFF_SECONDS: float = Field(
        default=5.0,
        description=(
            "Base seconds for the GDELT retry backoff; attempt N waits "
            "GDELT_RETRY_BACKOFF_SECONDS * 2**N unless the server sent a "
            "Retry-After header."
        ),
    )
    GDELT_COOLDOWN_THRESHOLD: int = Field(
        default=3,
        description=(
            "Consecutive FAILED GDELT requests -- 429, 5xx, or transport error "
            "alike -- after which GDELT calls "
            "are SKIPPED outright (no sleep, no request) for "
            "GDELT_COOLDOWN_SECONDS. This is what stops an "
            "already-throttled IP from turning a long backfill into hours of "
            "certain-to-fail requests while the other sentiment sources "
            "starve for wall-clock budget. Counting transport errors too is "
            "deliberate: measured 2026-07-29, GDELT stopped 429-ing and started "
            "read-timing-out instead, and a 429-only breaker left a 26-window "
            "backfill grinding at 10s per window for the same zero result. A "
            "single served response "
            "clears the count and any open cooldown. 0 disables the cooldown."
        ),
    )
    GDELT_COOLDOWN_SECONDS: float = Field(
        default=300.0,
        description=(
            "How long the GDELT cooldown stays open once "
            "GDELT_COOLDOWN_THRESHOLD consecutive failed "
            "requests have been seen. Affects GDELT only -- every other "
            "sentiment source keeps running normally throughout."
        ),
    )
    SENTIMENT_DESENTENCIZE_ENABLED: bool = Field(
        default=False,
        description=(
            "When True, ingested document text has periods replaced with "
            "semicolons before FinBERT scoring (a real but marginal trick to "
            "discourage sentence-boundary truncation on run-on social posts). "
            "Off by default: it can corrupt numerics ($4.50), cashtags "
            "($AAPL), and abbreviations (U.S.) -- see "
            "tests/test_sentiment_sources.py's desentencize-safety cases "
            "before enabling."
        ),
    )
    REDDIT_CLIENT_ID: str = Field(
        default="",
        description="Reddit API OAuth2 script-app client ID. Empty disables RedditSource.",
    )
    REDDIT_CLIENT_SECRET: str = Field(
        default="",
        description="Reddit API OAuth2 script-app client secret. Empty disables RedditSource.",
    )
    REDDIT_USER_AGENT: str = Field(
        default="stockpy-sentiment-ingestion/0.1",
        description=(
            "User-Agent header sent with every Reddit API request, per "
            "Reddit's API rules (a generic/missing User-Agent is rate-limited "
            "more aggressively). Operators should set this to something "
            "identifying their own deployment."
        ),
    )
    REDDIT_BACKFILL_MAX_PAGES: int = Field(
        default=10,
        description=(
            "Max pages RedditSource.fetch() will paginate through (via the "
            "'after' cursor, 100 posts/page) when `since` is far enough in "
            "the past that a single day/week 't=' bucket wouldn't cover it. "
            "Bounds a historical-backfill request from paginating unbounded; "
            "a live per-cycle call with a recent `since` typically stops "
            "after 1 page. Backfilled posts' credibility sub-scores still "
            "reflect the author's CURRENT account state, not their state at "
            "post time -- see RedditSource's docstring."
        ),
    )
    STOCKTWITS_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for data/sentiment_sources.py's StockTwitsSource "
            "(free, uncredentialed -- unlike RedditSource, no OAuth "
            "registration needed). False (the default) is a complete "
            "no-op: no StockTwits request is attempted. Also requires "
            "'stocktwits' to be added to SENTIMENT_SOURCES -- this flag "
            "alone does not add it to the fan-out list, matching "
            "EDGAR_FULLTEXT_ENABLED's own two-gate pattern (a feature "
            "flag plus a source/form membership check). StockTwits' "
            "public endpoint has tightened over time and may rate-limit "
            "or require auth in some deployments; a failed request "
            "degrades to no documents this cycle, exactly like a missing "
            "Reddit credential -- Reddit remains the primary comment "
            "source (see docs/RUNBOOK.md)."
        ),
    )
    EDGAR_USER_AGENT: str = Field(
        default="",
        description=(
            "User-Agent header sent with every SEC EDGAR request, per SEC's "
            "fair-access policy (must identify the requester, e.g. "
            "'Company Name admin@example.com'). Empty disables EdgarSource "
            "rather than send a non-compliant request that risks an IP block."
        ),
    )
    SENTIMENT_MAX_DOCUMENTS_PER_CYCLE: int = Field(
        default=2000,
        description=(
            "Per-cycle document budget shared across all symbols in "
            "CompositeSentimentSource -- the 'bounded queue with backpressure' "
            "this pipeline runs locally instead of a distributed queue. Once "
            "reached, lower-priority sources (social feeds) are skipped for "
            "the remainder of the cycle while higher-priority sources "
            "(Finnhub, EDGAR) keep running; never touches order/broker code "
            "under any pressure condition."
        ),
    )
    SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE: float = Field(
        default=60.0,
        description=(
            "Hard wall-clock ceiling (seconds) for CompositeSentimentSource's "
            "entire per-cycle ingestion run (set via reset_cycle(), checked in "
            "fetch_all()). A single unreachable/slow host can otherwise stack "
            "up its per-request timeout across every remaining symbol with no "
            "overall ceiling -- once this budget elapses, ingestion is skipped "
            "for the rest of the cycle (fails fast and moves on) rather than "
            "stalling the whole pipeline refresh."
        ),
    )
    SENTIMENT_CIRCUIT_BREAKER_THRESHOLD: int = Field(
        default=3,
        description=(
            "Consecutive failures (timeout/connection error) for a single "
            "source within one cycle before CompositeSentimentSource trips a "
            "circuit breaker and skips that source for the rest of the cycle "
            "-- avoids re-attempting a source that's clearly down (e.g. an "
            "unreachable host) for every remaining symbol in the universe."
        ),
    )

    # --- AI-Assisted Credibility Filtering (Sentiment Pipeline Phase 2 PR2,
    # signals/credibility.py) ---
    # Replaces the hardcoded S_verification=1.0 placeholder with a real,
    # budget-bounded LLM check for documents whose HEURISTIC credibility
    # composite (S_authority + S_humanity) falls in a borderline band --
    # clearly-trusted or clearly-bot-flagged documents never pay the LLM
    # cost, and institutional sources (finnhub/yahoo_rss/gdelt/edgar) are
    # skipped entirely. Opt-in, default False -- preserves today's exact
    # S_verification=1.0-for-everyone behavior, matching the
    # FORECAST_USE_GARCH_SIGMA opt-in convention.
    SENTIMENT_LLM_VERIFICATION_ENABLED: bool = Field(
        default=False,
        description=(
            "When True, signals/credibility.py's score_documents() calls an "
            "LLM (via llm/providers.py's LLMProvider.call_structured -- the "
            "same soft-fail contract as every other LLM integration in this "
            "codebase, CONSTRAINT #6) to verify documents whose heuristic "
            "credibility composite falls in "
            "[SENTIMENT_LLM_VERIFICATION_BORDERLINE_LOW, "
            "SENTIMENT_LLM_VERIFICATION_BORDERLINE_HIGH]. False (the "
            "default) is a complete no-op: every document's S_verification "
            "stays the hardcoded 1.0 placeholder, byte-identical to "
            "pre-PR2 behavior. Requires "
            "SENTIMENT_LLM_VERIFICATION_PROVIDER to also be set to a real "
            "provider ('claude'/'gemini'/'openai') and that provider's API "
            "key to be configured -- otherwise still a no-op."
        ),
    )
    SENTIMENT_LLM_VERIFICATION_PROVIDER: str = Field(
        default="none",
        description=(
            "Which LLMProvider backs sentiment-document verification -- "
            "'claude' | 'gemini' | 'openai' | 'none'. 'none' (the default) "
            "disables the LLM call even when "
            "SENTIMENT_LLM_VERIFICATION_ENABLED is True. Resolved by "
            "llm.router.get_sentiment_verification_provider(), mirroring "
            "OPAL_RESEARCH_PROVIDER's flexible-routing shape."
        ),
    )
    SENTIMENT_LLM_VERIFICATION_MAX_CALLS_PER_CYCLE: int = Field(
        default=25,
        description=(
            "Per-batch cap on real LLM calls made by "
            "signals.credibility.score_documents(). Once reached, remaining "
            "borderline documents silently fall back to the S_verification="
            "1.0 placeholder rather than blocking ingestion -- the same "
            "'bounded queue with backpressure' philosophy as "
            "SENTIMENT_MAX_DOCUMENTS_PER_CYCLE. A doc_hash cache hit does "
            "NOT count against this budget."
        ),
    )
    SENTIMENT_LLM_VERIFICATION_BORDERLINE_LOW: float = Field(
        default=0.3,
        description=(
            "Lower bound (inclusive) of the heuristic credibility-composite "
            "band ((S_authority + S_humanity) / 2) that qualifies a document "
            "for LLM verification. Documents scoring below this are already "
            "clearly low-trust (bot-like/low-authority) -- an LLM call would "
            "not change the outcome, so it is skipped to control cost."
        ),
    )
    SENTIMENT_LLM_VERIFICATION_BORDERLINE_HIGH: float = Field(
        default=0.7,
        description=(
            "Upper bound (inclusive) of the heuristic credibility-composite "
            "band ((S_authority + S_humanity) / 2) that qualifies a document "
            "for LLM verification. Documents scoring above this are already "
            "clearly high-trust -- an LLM call would not change the "
            "outcome, so it is skipped to control cost."
        ),
    )

    # --- Sentiment/attention data source scaffolding (Sentiment Pipeline
    # Phase 4 groundwork) ---
    # Configuration surface for four follow-on sources/features that are not
    # yet implemented as of this commit: a Google News RSS SentimentSource, an
    # EDGAR full-text-search (EFTS) extension to the existing 8-K-only EdgarSource,
    # a GDELT-based cross-sectional "Sector Heat Factor" attention signal, and a
    # Wikipedia-pageviews (+ optional pytrends) attention signal. Every field
    # below defaults to a value that preserves today's exact behavior -- nothing
    # new is enabled, fetched, or computed until a follow-on branch both wires
    # the consuming code AND an operator opts in via .env, matching this file's
    # existing SENTIMENT_INGESTION_ENABLED/EDGAR_USER_AGENT opt-in conventions.
    GOOGLE_NEWS_LOOKBACK_WINDOW: str = Field(
        default="7d",
        description=(
            "Lookback window passed as Google News RSS's `when:` query "
            "parameter (e.g. 'https://news.google.com/rss/search?q=...+when:7d'). "
            "Accepts Google News' own shorthand ('1h', '1d', '7d', ...). "
            "Has no effect until a Google News SentimentSource is added to "
            "data/sentiment_sources.py and 'google_news' is added to "
            "SENTIMENT_SOURCES."
        ),
    )
    EDGAR_FULLTEXT_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for the SEC EDGAR full-text search (EFTS) "
            "additions to EdgarSource -- fetching and chunking 10-K/10-Q "
            "filing text (per EDGAR_FULLTEXT_FORMS), not just the existing "
            "8-K RSS feed. False (the default) is a complete no-op: the "
            "existing 8-K-only RSS path in EdgarSource is completely "
            "unaffected by this flag either way and keeps running whenever "
            "'edgar' is enabled in SENTIMENT_SOURCES. Set True only once the "
            "EFTS ingestion code exists downstream."
        ),
    )
    EDGAR_FULLTEXT_FORMS: str = Field(
        default="8-K,10-K,10-Q",
        description=(
            "Comma-separated SEC form types the EDGAR full-text search "
            "additions request from EFTS when EDGAR_FULLTEXT_ENABLED is "
            "True. Mirrors the SENTIMENT_SOURCES fan-out-list convention."
        ),
    )
    EDGAR_FULLTEXT_CHUNK_TOKENS: int = Field(
        default=512,
        description=(
            "Maximum tokens per filing-text chunk when the EDGAR full-text "
            "search additions split a long 10-K/10-Q into pieces for FinBERT "
            "scoring (FinBERT's own input window is far smaller than a full "
            "filing). Only consulted once EDGAR_FULLTEXT_ENABLED is True."
        ),
    )
    SECTOR_HEAT_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for the GDELT article-volume-based 'Sector Heat "
            "Factor' attention feature (cross-sectional news-volume z-score "
            "per sector). False (the default) is a complete no-op: no GDELT "
            "query is attempted and Sector_Heat_Factor stays NaN in "
            "config.COLUMN_SCHEMA. Independent of SENTIMENT_SOURCES' "
            "existing 'gdelt' entry, which feeds per-document sentiment "
            "ingestion, not this aggregate attention signal."
        ),
    )
    SECTOR_HEAT_SMOOTHING_SIGMA: float = Field(
        default=1.0,
        description=(
            "Gaussian smoothing sigma applied to the raw daily GDELT "
            "article-volume series before computing the Sector Heat Factor "
            "-- higher values smooth out more day-to-day noise at the cost "
            "of responsiveness. Only consulted once SECTOR_HEAT_ENABLED is "
            "True."
        ),
    )
    SECTOR_HEAT_LOOKBACK_DAYS: int = Field(
        default=7,
        description=(
            "Calendar days of GDELT article-volume history used to compute "
            "each cycle's Sector Heat Factor. Only consulted once "
            "SECTOR_HEAT_ENABLED is True."
        ),
    )

    # --- Sector Selection Heat (data/sector_selection_heat.py) ---
    # A DIFFERENT feature from SECTOR_HEAT_* above despite the name overlap
    # -- see docs/signals/sector_heat_factor.md's "Two features, one name"
    # section and docs/signals/sector_selection.md. This one drives
    # semantic Related Sector Selection's ranking coefficient (cosine
    # similarity x this Gaussian-response heat term), not the
    # Sector_Heat_Factor dashboard column.
    SECTOR_SELECTION_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for the semantic Related Sector Selection "
            "feature's Gaussian-response Sector Heat term "
            "(data.sector_selection_heat.compute_spec_sector_heat). False "
            "(the default) is a complete no-op: no sentiment_ingestion_audit "
            "read is attempted. Independent of SECTOR_HEAT_ENABLED."
        ),
    )
    SECTOR_SELECTION_HEAT_LOOKBACK_DAYS: int = Field(
        default=22,
        description=(
            "Trailing TRADING days (weekdays only -- no holiday calendar, "
            "same documented limitation as "
            "HistoricalStore.resolve_trading_day) of sentiment_ingestion_"
            "audit news+comment volume summed per candidate sector before "
            "min-max normalization. 22 trading days matches the "
            "MDPI-methodology lookback this feature is modeled on. Only "
            "consulted once SECTOR_SELECTION_ENABLED is True."
        ),
    )
    SECTOR_SELECTION_HEAT_A: float = Field(
        default=0.8,
        description=(
            "Gaussian amplitude 'a' in SHF = a * exp(-(x-b)^2 / (2c^2)), "
            "where x is the min-max-normalized combined news+review volume "
            "across candidate sectors. Empirically calibrated in the "
            "source methodology; kept as a setting rather than a literal "
            "so a future recalibration doesn't require a code change."
        ),
    )
    SECTOR_SELECTION_HEAT_B: float = Field(
        default=1.0,
        description="Gaussian center 'b' in SHF = a * exp(-(x-b)^2 / (2c^2)).",
    )
    SECTOR_SELECTION_HEAT_C: float = Field(
        default=0.6,
        description="Gaussian width 'c' in SHF = a * exp(-(x-b)^2 / (2c^2)).",
    )
    SECTOR_SIMILARITY_EMBEDDER: str = Field(
        default="sbert",
        description=(
            "Embedding backend for Related Sector Selection's semantic-"
            "similarity term ('sbert' | 'openai' | 'none'). 'sbert' (the "
            "default) uses a local sentence-transformers model -- see "
            "SECTOR_SIMILARITY_MODEL -- with zero network calls and zero "
            "marginal API cost, matching this codebase's free-first, local-"
            "model posture (local FinBERT, embedded faiss). 'openai' routes "
            "through llm.router.get_sector_embedding_provider() instead "
            "(paid, network-dependent). 'none' disables the similarity term "
            "entirely -- every cosine_similarity is NaN. Only consulted "
            "once SECTOR_SELECTION_ENABLED is True."
        ),
    )
    SECTOR_SIMILARITY_MODEL: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description=(
            "Hugging Face model id loaded when SECTOR_SIMILARITY_EMBEDDER="
            "'sbert'. Requires the optional sentence-transformers package "
            "(requirements-optional.txt) -- absent, SBERT_AVAILABLE is "
            "False and every cosine_similarity degrades to NaN, never a "
            "fabricated value (CONSTRAINT #4)."
        ),
    )
    SECTOR_SIMILARITY_POOLING: str = Field(
        default="max",
        description=(
            "Pooling strategy applied to SBERT token embeddings ('max' | "
            "'mean'). 'max' matches the source methodology's specified "
            "max-pooling. NOTE: sentence-transformers/all-MiniLM-L6-v2 "
            "ships configured for MEAN pooling and was trained that way -- "
            "max-pooled output from this checkpoint is off-distribution. "
            "Kept as a setting (default 'max', spec-faithful) rather than "
            "silently substituting 'mean' so the difference is measurable, "
            "not assumed; see docs/signals/sector_selection.md."
        ),
    )
    SECTOR_SELECTION_TOP_N: int = Field(
        default=3,
        description=(
            "Default number of top-ranked related sectors selected per "
            "target symbol. The webapp Sector Selection panel's N slider "
            "overrides this per-request; this is only the engine/API "
            "default when no override is supplied."
        ),
    )
    SECTOR_SELECTION_W1: float = Field(
        default=0.4,
        description=(
            "Default news-volume weight, mirrored from the composite "
            "sentiment index S_t = w1*news + w2*review "
            "(signals/sentiment_index.py) for consistency, though Sector "
            "Selection's own ranking formula (cosine_similarity * SHF) "
            "does not currently consume w1/w2 directly -- reserved for the "
            "webapp panel's weight sliders per the source methodology's "
            "UI spec."
        ),
    )
    SECTOR_SELECTION_W2: float = Field(
        default=0.1,
        description="Default review-volume weight -- see SECTOR_SELECTION_W1.",
    )

    WIKIPEDIA_ATTENTION_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for the Wikipedia-pageviews-based attention "
            "feature (per-symbol article pageview volume as a retail-"
            "attention proxy). False (the default) is a complete no-op: no "
            "Wikimedia Pageviews API call is attempted and Attention_Score "
            "stays NaN in config.COLUMN_SCHEMA."
        ),
    )
    WIKIPEDIA_ATTENTION_LOOKBACK_DAYS: int = Field(
        default=30,
        description=(
            "Calendar days of Wikipedia pageview history used to compute "
            "each cycle's attention baseline/z-score. Only consulted once "
            "WIKIPEDIA_ATTENTION_ENABLED is True."
        ),
    )
    PYTRENDS_ENABLED: bool = Field(
        default=False,
        description=(
            "Best-effort optional Google Trends overlay (via the unofficial "
            "'pytrends' library) on top of the Wikipedia-pageviews attention "
            "feature. False (the default) -- pytrends is an unmaintained, "
            "rate-limit-fragile scraper of an undocumented Google endpoint "
            "per the source research for this feature, so it must NEVER be "
            "load-bearing: any consumer of this flag must treat a pytrends "
            "failure/timeout as a soft-fail (CONSTRAINT #6) and fall back to "
            "Wikipedia-only attention scoring, never block or crash the "
            "pipeline on it."
        ),
    )
    ATTENTION_INGESTION_MAX_SECONDS_PER_CYCLE: float = Field(
        default=60.0,
        description=(
            "Hard wall-clock ceiling (seconds) for "
            "compute_attention_scores_for_universe()'s entire per-cycle loop "
            "over the symbol universe. Mirrors "
            "SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE for the identical risk "
            "shape: a slow/unreachable Wikipedia Pageviews endpoint can "
            "otherwise stack its per-request timeout across every remaining "
            "symbol with no overall ceiling -- once this budget elapses, "
            "every remaining symbol degrades to NaN (never fabricated) for "
            "the rest of the cycle instead of continuing to attempt fetches. "
            "Only consulted once WIKIPEDIA_ATTENTION_ENABLED is True."
        ),
    )
    ATTENTION_CIRCUIT_BREAKER_THRESHOLD: int = Field(
        default=3,
        description=(
            "Consecutive no-score outcomes (exception or NaN) within one "
            "compute_attention_scores_for_universe() cycle before Wikipedia "
            "(and the optional pytrends overlay) is skipped for the rest of "
            "that cycle's symbols -- avoids burning the wall-clock budget on "
            "a source that's clearly failing for every remaining symbol. "
            "Mirrors SENTIMENT_CIRCUIT_BREAKER_THRESHOLD. Only consulted once "
            "WIKIPEDIA_ATTENTION_ENABLED is True."
        ),
    )

    # ── ETF volatility transmission (risk/etf_transmission.py) ───────────────
    # Ben-David, Franzoni & Moussawi (2018), "Do ETFs Increase Volatility?",
    # Journal of Finance 73(6). DIAGNOSTIC-ONLY measurement columns
    # (ETF_Ownership_Pct / ETF_Comovement_R2 / ETF_Primary_Wrapper) -- nothing
    # in scoring, sizing, or execution reads them yet. Same opt-in house style
    # as SECTOR_HEAT_* / WIKIPEDIA_ATTENTION_* above: the master switch
    # defaults False and is a complete no-op (zero network calls, all three
    # columns NaN).
    ETF_TRANSMISSION_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for the ETF volatility-transmission measurement "
            "columns (risk/etf_transmission.py, wired by "
            "pipeline/production_steps.py::_apply_etf_transmission). False "
            "(the default) is a complete no-op: no ETF-holdings or ETF-bars "
            "fetch is attempted and ETF_Ownership_Pct / ETF_Comovement_R2 / "
            "ETF_Primary_Wrapper stay NaN in config.COLUMN_SCHEMA."
        ),
    )
    ETF_HOLDINGS_MARKET_PROXY: str = Field(
        default="SPY",
        description=(
            "Broad-market ETF used as the MARKET leg when residualizing both "
            "the stock and its ETF-composite returns. Deliberately EXCLUDED "
            "from the ownership-weighted return composite itself -- a naive "
            "(non-residualized) R2 is high for every large-cap regardless of "
            "ETF wrapping, so it would ship a market-beta derate wearing an "
            "ETF costume. Consequence, by design: a name whose only covered "
            "wrapper IS this proxy has an identically-zero residual and "
            "therefore a NaN ETF_Comovement_R2, never a fabricated number."
        ),
    )
    ETF_TRANSMISSION_WRAPPERS: list[str] = Field(
        default_factory=lambda: [
            "SPY", "QQQ", "IWM", "DIA",
            "XLB", "XLC", "XLE", "XLF", "XLI",
            "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
        ],
        description=(
            "Candidate wrapper ETFs whose baskets are fetched each cycle to "
            "measure how heavily each universe name is ETF-wrapped (JSON "
            "array in .env). Coverage is explicitly partial -- a name held "
            "only by wrappers outside this list reads NaN rather than a "
            "fabricated low ownership. Only consulted once "
            "ETF_TRANSMISSION_ENABLED is True."
        ),
    )
    ETF_TRANSMISSION_EXCLUDED_SYMBOLS: list[str] = Field(
        default_factory=list,
        description=(
            "Extra universe symbols that are THEMSELVES funds and must never "
            "be measured against their own basket (ownership/co-movement "
            "against itself is 1.0/1.0 -- a maximum derate for a trivially "
            "wrong reason). Everything in ETF_TRANSMISSION_WRAPPERS plus "
            "ETF_HOLDINGS_MARKET_PROXY is excluded automatically; this list "
            "covers funds an operator holds that are not themselves wrappers "
            "(e.g. VOO, VTI, ARKK). JSON array in .env."
        ),
    )
    ETF_TRANSMISSION_WINDOW_DAYS: int = Field(
        default=60,
        description=(
            "Rolling window (trading days) for the market-residualized "
            "co-movement R2. Mirrors processing_engine.calculate_rolling_beta's "
            "default 60-day beta window. Only consulted once "
            "ETF_TRANSMISSION_ENABLED is True."
        ),
    )
    ETF_TRANSMISSION_MIN_OBS: int = Field(
        default=60,
        description=(
            "Minimum aligned overlapping return observations required before "
            "an ETF_Comovement_R2 is reported at all. Defaults to the full "
            "window (NaN-until-full-window-coverage): a name added to a "
            "wrapper last week has no tethered history, so a partial-window "
            "R2 would UNDERSTATE transmission with a confident-looking "
            "number. Missing beats understated (CONSTRAINT #4)."
        ),
    )

    # ── ETF Transmission: Portfolio-Level Covariance (sizing/position_sizer.py) ──
    # The mechanism raises COVARIANCE between co-held names, not any single
    # name's own variance -- so the portfolio-wide gross-exposure cap
    # (apply_portfolio_gross_cap's existing cov_matrix path, see
    # sizing/vol_target.py::portfolio_vol_target) is where it genuinely
    # belongs, not a second per-name lever alongside ETF_TRANSMISSION_SIZING_ENABLED
    # above. False (the default) is a complete no-op: cov_matrix=None is
    # passed to apply_portfolio_gross_cap exactly as before this feature
    # existed, reproducing today's sum-of-|weight| fallback byte-for-byte.
    ETF_TRANSMISSION_PORTFOLIO_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for routing the portfolio-level gross-exposure cap "
            "through apply_portfolio_gross_cap's risk-aware cov_matrix path, "
            "using an ETF-co-ownership-inflated covariance matrix "
            "(risk.etf_transmission.build_transmission_adjusted_cov) instead "
            "of the sum-of-|weight| fallback. False (the default) is a "
            "complete no-op: cov_matrix=None every cycle, byte-identical to "
            "pre-feature behavior. Requires ETF_HOLDINGS_ENABLED (a holdings "
            "source) to produce anything other than the same fallback -- "
            "with no holdings data the covariance build degrades gracefully "
            "back to cov_matrix=None rather than fabricating overlap."
        ),
    )
    ETF_TRANSMISSION_COV_INFLATION: float = Field(
        default=0.25,
        description=(
            "Fractional inflation applied to the OFF-DIAGONAL covariance "
            "entry of each symbol pair, scaled by their pairwise ETF "
            "co-ownership overlap (cosine similarity of ETF-basket weight "
            "vectors, in [0, 1]): cov_adj[i,j] = cov[i,j] * (1 + "
            "ETF_TRANSMISSION_COV_INFLATION * overlap[i,j]) for i != j. The "
            "diagonal (each name's own variance) is never touched -- this "
            "models the paper's actual claim (arbitrage raises CO-MOVEMENT "
            "between co-held names), not a claim about any single name's "
            "own volatility, which risk.etf_transmission.transmission_multiplier "
            "already handles separately via ETF_TRANSMISSION_SIZING_ENABLED. "
            "Only consulted once ETF_TRANSMISSION_PORTFOLIO_ENABLED is True."
        ),
    )
    ETF_TRANSMISSION_COV_WINDOW_DAYS: int = Field(
        default=60,
        description=(
            "Trailing trading-day window of aligned daily returns used to "
            "estimate the base covariance matrix before ETF co-ownership "
            "inflation. Mirrors ETF_TRANSMISSION_WINDOW_DAYS's default. If "
            "fewer than this many fully-overlapping return observations "
            "exist across the cycle's universe, the covariance build is "
            "skipped for that cycle (falls back to cov_matrix=None) rather "
            "than estimating a covariance matrix off a short, noisy sample. "
            "Only consulted once ETF_TRANSMISSION_PORTFOLIO_ENABLED is True."
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
            "forecast_ts ≤ now-90d (the full horizon must elapse before "
            "ForecastTracker.update_actuals actualizes it — see "
            "docs/known_issues/forecast_tracker_early_actualization.md), while the "
            "window only counts forecast_ts ≥ now-WINDOW; with WINDOW=60 those two "
            "bands are mutually exclusive so h=60/h=90 could never warm up. 180 "
            "gives every horizon a real eligibility band."
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
    # posture, so it must be explicitly opted into. GUI-writable (added to
    # gui/env_io.py's ALLOWED_KEYS by operator decision) — the endpoints remain
    # gated by TWO further independent checks: FOLLOW_API_TOKEN (fail-closed
    # command token, reused from the follow write-path) and a loopback-only
    # check, so flipping this flag alone is not sufficient to enable intake.
    BROKERAGE_CONNECT_ENABLED: bool = Field(
        default=True,
        description=(
            "Enables the Pilots API's brokerage-credential connect/disconnect "
            "endpoints. On by default; also requires FOLLOW_API_TOKEN and a "
            "loopback (127.0.0.1) request."
        ),
    )
    # Master switch for the Pilots API's Data & Automation WRITE endpoints
    # (api/pilots_api.py PUT /automation/schedule/interval, POST /automation/resume
    # — see the Data & Automation plan). Default False. GUI-writable (added to
    # gui/env_io.py's ALLOWED_KEYS 2026-08-08 by operator decision — carries no
    # secret material; also a settings_keysets.DANGEROUS_KEYS member, requiring
    # typed confirmation on write regardless of editor) — the endpoint remains
    # gated by FOLLOW_API_TOKEN independently of this flag. Deliberately
    # does NOT gate POST /automation/run or POST /automation/pause — those already
    # sit behind require_command_token alone, matching the existing
    # POST /pilots/{id}/follow precedent (which writes an order queue under
    # FOLLOW_API_TOKEN alone, no master flag); gating a run trigger or pause more
    # strictly than the most sensitive endpoint already shipped would invert the
    # risk ordering. Reserved for the two writes with a real persistence/rollback
    # cost: an .env edit and re-enabling live order submission.
    AUTOMATION_WRITES_ENABLED: bool = Field(
        default=True,
        description=(
            "Enables PUT /automation/schedule/interval and POST /automation/resume "
            "on the Pilots API. On by default; also requires FOLLOW_API_TOKEN. "
            "GUI-writable by operator decision (2026-08-08) — the endpoint "
            "remains gated by FOLLOW_API_TOKEN regardless. POST /automation/run "
            "and /automation/pause are NOT gated by this flag "
            "(require_command_token alone, matching the follow write-path's "
            "existing risk posture)."
        ),
    )
    # Master switch for the Pilots API's Strategy Matrix WRITE endpoint
    # (api/pilots_api.py PUT /strategy/modules — signal weights + disabled-module
    # set -> .env). A DEDICATED flag, not AUTOMATION_WRITES_ENABLED: that flag was
    # scoped to the daemon interval and kill-switch resume; signal-weight tuning
    # changes WHAT THE PLATFORM RECOMMENDS and must not ride in on it. Default
    # False. GUI-writable (added to gui/env_io.py's ALLOWED_KEYS 2026-08-08 by
    # operator decision — carries no secret material; also a
    # settings_keysets.DANGEROUS_KEYS member, requiring typed confirmation on
    # write regardless of editor), and also requires FOLLOW_API_TOKEN.
    # GET /strategy/matrix is read-only and NOT gated by this flag
    # (require_read_token alone, matching GET /brokerage/status).
    STRATEGY_WRITES_ENABLED: bool = Field(
        default=True,
        description=(
            "Enables PUT /strategy/modules on the Pilots API (signal weights + "
            "disabled-module set -> .env). On by default; also requires "
            "FOLLOW_API_TOKEN. GUI-writable by operator decision (2026-08-08) — "
            "the endpoint remains gated by FOLLOW_API_TOKEN regardless, so "
            "signal tuning cannot ride in on AUTOMATION_WRITES_ENABLED."
        ),
    )
    # Master switch for the Pilots API's AI Control Center WRITE endpoint
    # (api/pilots_api.py PUT /llm/setting — LLM capability toggles + provider
    # selection -> .env). A DEDICATED flag, not AUTOMATION_WRITES_ENABLED or
    # STRATEGY_WRITES_ENABLED: those were scoped to the daemon interval/kill-switch
    # resume and to signal-weight tuning respectively — flipping an AI capability
    # (which provider narrates a rationale, whether the Gravity AI runner or Opal
    # research agent can fire) is its own risk class and must not ride in on
    # either. Default False. GUI-writable (added to gui/env_io.py's ALLOWED_KEYS
    # 2026-08-08 by operator decision — carries no secret material; also a
    # settings_keysets.DANGEROUS_KEYS member, requiring typed confirmation on
    # write regardless of editor), and also requires FOLLOW_API_TOKEN.
    # GET /llm/status is read-only and NOT gated by this flag (require_read_token
    # alone, matching GET /brokerage/status and GET /strategy/matrix).
    LLM_WRITES_ENABLED: bool = Field(
        default=True,
        description=(
            "Enables PUT /llm/setting on the Pilots API (LLM capability toggles + "
            "provider selection -> .env). On by default; also requires "
            "FOLLOW_API_TOKEN. GUI-writable by operator decision (2026-08-08) — "
            "the endpoint remains gated by FOLLOW_API_TOKEN regardless, so "
            "AI-capability writes cannot ride in on AUTOMATION_WRITES_ENABLED or "
            "STRATEGY_WRITES_ENABLED."
        ),
    )
    # Master switch for the Pilots API's Agentic Trading Discovery WRITE endpoint
    # (api/pilots_api.py PUT /agentic/scan-config -> output/scan_configs.json, read
    # by the .claude/skills/agentic-discovery/ Claude Code skill). A DEDICATED flag,
    # not AUTOMATION_WRITES_ENABLED/STRATEGY_WRITES_ENABLED/LLM_WRITES_ENABLED: this
    # changes WHAT THE AGENT DISCOVERS (which symbols get scanned and fed toward the
    # gated order queue), its own risk class, and must not ride in on any of those.
    # Default False; GUI-writable (added to gui/env_io.py's ALLOWED_KEYS by operator
    # decision) — the endpoint remains gated by FOLLOW_API_TOKEN independently of
    # this flag. GET /agentic/status and GET /agentic/discovery are read-only
    # and NOT gated by this flag (require_read_token alone, matching GET
    # /brokerage/status, GET /strategy/matrix, and GET /llm/status).
    AGENTIC_DISCOVERY_ENABLED: bool = Field(
        default=True,
        description=(
            "Enables PUT /agentic/scan-config on the Pilots API (Robinhood broker-scan "
            "config -> output/scan_configs.json, consumed by the agentic-discovery "
            "skill). On by default; also requires FOLLOW_API_TOKEN."
        ),
    )
    # Master switch for the Pilots API's general Settings Manager WRITE endpoint
    # (api/pilots_api.py PUT /settings/tunables -- the ~30+ non-secret runtime
    # tunables this editor owns, including KELLY_FRACTION/MAX_LEVERAGE/
    # DAILY_LOSS_LIMIT_PCT and other sizing/risk-gate/forecasting knobs). A
    # DEDICATED flag, not AUTOMATION_WRITES_ENABLED/STRATEGY_WRITES_ENABLED/
    # LLM_WRITES_ENABLED/AGENTIC_DISCOVERY_ENABLED: those were scoped to the
    # daemon interval/kill-switch resume, signal-weight tuning, AI-capability
    # selection, and discovery-scan configuration respectively — sizing and
    # risk-gate tunables are their own risk class (they change how large a
    # position gets and when the risk gate blocks an order, not just what gets
    # scanned or which LLM narrates) and must not ride in on any of those.
    # Mirrors AUTOMATION_WRITES_ENABLED / STRATEGY_WRITES_ENABLED /
    # LLM_WRITES_ENABLED exactly: default True. GUI-writable (added to
    # gui/env_io.py's ALLOWED_KEYS 2026-08-08 by operator decision — carries no
    # secret material; also a settings_keysets.DANGEROUS_KEYS member, requiring
    # typed confirmation on write regardless of editor), and also requires
    # FOLLOW_API_TOKEN. GET /settings/tunables is read-only and NOT gated by
    # this flag (require_read_token alone, matching GET /brokerage/status, GET
    # /strategy/matrix, GET /llm/status, and GET /agentic/status).
    GENERAL_SETTINGS_WRITES_ENABLED: bool = Field(
        default=True,
        description=(
            "Enables PUT /settings/tunables on the Pilots API (general runtime "
            "tunables -- Kelly sizing, risk gate, forecasting, market data, "
            "runtime/ops -> .env). On by default; also requires "
            "FOLLOW_API_TOKEN. GUI-writable by operator decision (2026-08-08) — "
            "the endpoint remains gated by FOLLOW_API_TOKEN regardless, so "
            "sizing/risk-gate tuning cannot ride in on any other writes-enabled "
            "flag."
        ),
    )
    # RLHF Calibration Review Queue (rlhf_calibration_store.py) -- an AI trading
    # agent proposes a hypothetical PAPER trade (symbol/action/rationale/
    # confidence/technical-context) and a human operator rates it 1-5 stars
    # with an optional corrective comment. Entirely separate from real
    # trading: no capital, no broker, no TransactionsStore/BrokerBase/
    # OrderManager involvement (see that module's docstring for why mixing
    # hypothetical proposals into TransactionsStore would be dangerous).
    RLHF_CALIBRATION_ENABLED: bool = Field(
        default=True,
        description=(
            "Master switch for the RLHF Calibration Review Queue's write "
            "endpoints (POST /rlhf/proposals, POST /rlhf/proposals/{id}/review, "
            "POST /rlhf/export-sft). Paper-only -- no capital, broker, or "
            "execution risk -- so this ships active by default per this "
            "repo's 2026-08-03 convention that new admin/write capabilities "
            "default ON. Also requires FOLLOW_API_TOKEN at the endpoint "
            "level regardless of this flag's value."
        ),
    )
    RLHF_CALIBRATION_CONFIDENCE_THRESHOLD: float = Field(
        default=0.8,
        description=(
            "Confidence [0,1] at or above which a new proposal is "
            "auto-approved (skips mandatory human review) when "
            "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED is True."
        ),
    )
    RLHF_CALIBRATION_AUTO_APPROVE_ENABLED: bool = Field(
        default=False,
        description=(
            "When True, a proposal whose confidence clears "
            "RLHF_CALIBRATION_CONFIDENCE_THRESHOLD is marked reviewed "
            "automatically (auto_approved=True, human_rating stays null -- "
            "never a fabricated rating) instead of waiting for a human. "
            "Default False: this changes what counts as 'reviewed' without a "
            "human in the loop, so it stays opt-in rather than defaulting on "
            "like RLHF_CALIBRATION_ENABLED."
        ),
    )
    RLHF_CALIBRATION_AUTO_EXPORT_SFT_ENABLED: bool = Field(
        default=False,
        description=(
            "When True, a proposal that receives a 5-star human_rating is "
            "automatically appended to the SFT JSONL export the moment the "
            "review is submitted, instead of requiring a separate "
            "POST /rlhf/export-sft call. Default False (opt-in)."
        ),
    )
    # Master switch for api/data_api.py's three on-demand AI generation endpoints
    # (POST /data/ai/commentary|chart|research/{symbol} -- Claude analyst note,
    # Gemini chart-vision read, Opal research brief). A DEDICATED flag, distinct
    # from LLM_COMMENTARY_ENABLED/OPAL_RESEARCH_ENABLED (those gate whether the
    # underlying CAPABILITY exists at all, originally for the Streamlit desktop
    # button only) and from GENERAL_SETTINGS_WRITES_ENABLED (an .env config
    # write, not a paid external API call). This flag instead gates whether
    # that capability is remotely TRIGGERABLE over HTTP at all: api/data_api.py
    # is fail-open by design (see its module docstring) when STATE_API_TOKEN is
    # unset -- the documented zero-config default -- so without this flag,
    # anyone able to reach the data API could trigger real, paid Claude/Gemini/
    # Opal calls the instant an operator turns on the Streamlit-side capability
    # flag for their own desktop use. Default False: nothing is remotely
    # triggerable until this is explicitly, separately opted into. GUI-writable
    # (added to gui/env_io.py's ALLOWED_KEYS 2026-08-08 by operator decision —
    # carries no secret material; also a settings_keysets.DANGEROUS_KEYS
    # member, requiring typed confirmation on write regardless of editor).
    # Turning it back to False (and restarting the data API process)
    # immediately stops all three endpoints (403), on top of each generator's
    # own existing capability flag as a second, independent kill switch.
    AI_GENERATION_API_ENABLED: bool = Field(
        default=True,
        description=(
            "Enables POST /data/ai/{commentary,chart,research}/{symbol} on the "
            "Data API. On by default -- exposing paid Claude/Gemini/Opal calls "
            "over a fail-open HTTP API is its own risk/cost class, separate from "
            "the underlying capability being enabled for the Streamlit GUI. "
            "GUI-writable by operator decision (2026-08-08)."
        ),
    )
    # Master switch for the Pilots API's RAG query endpoint (POST /rag/query,
    # api/pilots_api.py -- wires agents/rag_orchestrator.py's run_rag_query()
    # into a real HTTP surface for the first time; that module previously had
    # no production caller at all). A DEDICATED flag, NOT
    # AI_GENERATION_API_ENABLED: that one's own description enumerates the
    # three specific /data/ai/* endpoints on the Data API it gates -- reusing
    # it here would silently widen what it controls beyond its documented
    # scope. Same risk class as AI_GENERATION_API_ENABLED (a paid external LLM
    # call, via llm/router.py::get_rationale_provider, reachable over an API
    # gated only by require_command_token otherwise), so it gets the same
    # fail-closed treatment: off by default. GUI-writable (added to
    # gui/env_io.py's ALLOWED_KEYS 2026-08-08 by operator decision — carries no
    # secret material; also a settings_keysets.DANGEROUS_KEYS member, requiring
    # typed confirmation on write regardless of editor).
    RAG_QUERY_API_ENABLED: bool = Field(
        default=True,
        description=(
            "Enables POST /rag/query on the Pilots API (agents/rag_orchestrator.py's "
            "run_rag_query, calling a paid LLM provider). On by default -- see "
            "AI_GENERATION_API_ENABLED for the same risk-class reasoning. "
            "GUI-writable by operator decision (2026-08-08)."
        ),
    )
    # Master switch for the Pilots API's Macro Regime Gate WRITE endpoint
    # (api/pilots_api.py PUT /observability/macro-gate -- flips
    # MACRO_REGIME_GATE_ENABLED itself to .env). A DEDICATED flag, not
    # GENERAL_SETTINGS_WRITES_ENABLED/STRATEGY_WRITES_ENABLED/AUTOMATION_WRITES_ENABLED/
    # LLM_WRITES_ENABLED: this is not a sizing/forecasting
    # tunable riding alongside dozens of others -- it is THE operator-controlled
    # bypass for PreTradeRiskGate.macro_kill_switch_check (the recession/credit-event
    # BUY veto; see risk_gate.py). Disabling it, even accidentally via a shared
    # flag some unrelated feature also gates, would silently remove that veto. Its
    # own risk class, must not ride in on any sibling flag. Mirrors
    # GENERAL_SETTINGS_WRITES_ENABLED / STRATEGY_WRITES_ENABLED /
    # LLM_WRITES_ENABLED exactly: default True. GUI-writable (added to
    # gui/env_io.py's ALLOWED_KEYS 2026-08-08 by operator decision — carries no
    # secret material; also a settings_keysets.DANGEROUS_KEYS member, requiring
    # typed confirmation on write regardless of editor), and also requires
    # FOLLOW_API_TOKEN. Note MACRO_REGIME_GATE_ENABLED itself (the key this
    # endpoint writes) IS already in ALLOWED_KEYS -- the Streamlit GUI's
    # Observability tab has written it directly for a long time
    # (gui/panels/observability.py); this flag governs only whether the NEW
    # Pilots-API/webapp write path may do the same, it does not change the
    # target key's own GUI-writability. GET /observability/summary is
    # read-only and NOT gated by this flag (require_read_token alone, matching
    # every other GET here).
    MACRO_GATE_WRITES_ENABLED: bool = Field(
        default=True,
        description=(
            "Enables PUT /observability/macro-gate on the Pilots API (flips "
            "MACRO_REGIME_GATE_ENABLED -> .env). On by default; also requires "
            "FOLLOW_API_TOKEN. GUI-writable by operator decision (2026-08-08) — "
            "the endpoint remains gated by FOLLOW_API_TOKEN regardless, so this "
            "recession/credit-event BUY-veto bypass cannot ride in on any other "
            "writes-enabled flag."
        ),
    )
    # Master switch for the Pilots API's on-demand brokerage-refresh endpoint
    # (api/pilots_api.py POST /brokerage/refresh -- forces a live Robinhood
    # re-login + account-snapshot fetch, bypassing the daily cache; the webapp/
    # API equivalent of `python3 main.py --refresh-account` and the Streamlit
    # GUI's "Force fresh login" checkbox on Live Inventory/Paper Monitor). A
    # DEDICATED flag, NOT BROKERAGE_CONNECT_ENABLED: that flag scopes credential
    # INTAKE (verifying and persisting NEW username/password) and clearing them
    # on disconnect: it does not receive any credential material and instead
    # re-uses whatever is already configured, but every call is still a real,
    # live network login against the operator's actual brokerage account and
    # must not ride in on a flag named for a different action. Mirrors
    # AUTOMATION_WRITES_ENABLED / STRATEGY_WRITES_ENABLED / LLM_WRITES_ENABLED /
    # GENERAL_SETTINGS_WRITES_ENABLED /
    # MACRO_GATE_WRITES_ENABLED exactly: default True. GUI-writable (added to
    # gui/env_io.py's ALLOWED_KEYS 2026-08-08 by operator decision — carries no
    # secret material; also a settings_keysets.DANGEROUS_KEYS member, requiring
    # typed confirmation on write regardless of editor), and also requires
    # FOLLOW_API_TOKEN and the same loopback-only check as /brokerage/connect
    # and /brokerage/disconnect. GET /brokerage/status and GET /portfolio
    # remain read-only and are NOT gated by this flag (require_read_token
    # alone).
    BROKERAGE_REFRESH_ENABLED: bool = Field(
        default=True,
        description=(
            "Enables POST /brokerage/refresh on the Pilots API (forces a live "
            "Robinhood re-login + account-snapshot fetch, bypassing the daily "
            "cache). On by default; also requires FOLLOW_API_TOKEN and a "
            "loopback (127.0.0.1) request. GUI-writable by operator decision "
            "(2026-08-08) — the endpoint remains gated by FOLLOW_API_TOKEN and "
            "the loopback check regardless, so on-demand refresh cannot ride "
            "in on BROKERAGE_CONNECT_ENABLED (a different action: credential "
            "intake, not re-use of already-configured credentials)."
        ),
    )
    # Cap on candidates GET /agentic/discovery returns (and on what the
    # agentic-discovery skill is expected to write per scan) — keeps the Discovery
    # section of the Agentic Trading tab bounded regardless of how many symbols a
    # broker scan matches. Read live here (never re-typed as a literal in the reader
    # or the webapp) per this repo's "thresholds come from settings" convention.
    AGENTIC_MAX_CANDIDATES: int = Field(
        default=25,
        description=(
            "Max candidates GET /agentic/discovery returns from "
            "output/scan_candidates.json."
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
    FINBERT_BATCH_SIZE: int = Field(
        default=16,
        description=(
            "Headlines per forward pass in signals.news_catalyst.score_headlines() "
            "when a real FinBERT pipeline is loaded. Replaces the old one-headline-"
            "at-a-time scoring loop; 16-32 is a reasonable CPU batch size. Only "
            "consulted when a pipeline is active -- the lexicon fallback path "
            "scores every headline independently regardless of this value."
        ),
    )
    FINBERT_SCORE_CACHE_ENABLED: bool = Field(
        default=True,
        description=(
            "Cache FinBERT/lexicon headline scores by a SHA-256 content hash "
            "of the headline text (data/historical_store.py's "
            "finbert_score_cache table), so an unchanged headline seen again "
            "in a later cycle's lookback window is not re-scored. Pure "
            "performance optimization with identical outputs (content-hash "
            "keyed, not date-keyed -- see the finbert_score_cache DDL comment "
            "for why this carries no lookahead risk), so it defaults on. "
            "Still degrades gracefully to 'score fresh, skip the cache' when "
            "settings.HISTORICAL_STORE_ENABLED is False or the DB is "
            "otherwise unavailable (CONSTRAINT #6)."
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
            "chart-pattern vision and Gemini Live chat).  Unset → that job's LLM disabled, "
            "template fallback kicks in."
        ),
    )
    GEMINI_LIVE_CHAT_ENABLED: bool = Field(
        default=True,
        description=(
            "Master switch for the Gemini Live bidirectional WebSocket voice/audio "
            "streaming endpoint (/ws/chat/live). Active by default when GEMINI_API_KEY is present."
        ),
    )
    GEMINI_LIVE_CHAT_MODEL: str = Field(
        default="gemini-3.1-flash-live-preview",
        description=(
            "Gemini model for real-time bidirectional WebSocket live streaming "
            "conversations over the Live API."
        ),
    )
    GEMINI_LIVE_VOICE_NAME: str = Field(
        default="Aoede",
        description=(
            "Voice preset name for Gemini Live audio output (e.g. Aoede, Puck, "
            "Charon, Fenrir, Kore)."
        ),
    )
    GEMINI_CHAT_MODEL: str = Field(
        default="gemini-2.5-flash",
        description=(
            "Default Gemini model name for the REST Server-Sent Events (SSE) "
            "text chat endpoint (POST /api/chat)."
        ),
    )
    LOCAL_LLM_BASE_URL: Optional[str] = Field(
        default=None,
        description=(
            "Base URL for OpenAI-compatible local or open-source LLM server "
            "(e.g. http://localhost:11434/v1 for Ollama, vLLM, LM Studio, OpenRouter)."
        ),
    )
    LOCAL_LLM_MODEL: str = Field(
        default="llama3.3",
        description=(
            "Default model slug for local or open-source LLM requests "
            "(e.g. llama3.3, deepseek-r1, qwen2.5, mistral)."
        ),
    )
    LOCAL_LLM_API_KEY: Optional[str] = Field(
        default=None,
        description=(
            "Optional API key or bearer token for local/self-hosted LLM server or OpenRouter."
        ),
    )
    AI_CHAT_DEFAULT_PROVIDER: str = Field(
        default="gemini",
        description=(
            "Default AI chat provider routing: 'gemini', 'anthropic', 'openai', 'local', or 'auto'."
        ),
    )
    AI_CHAT_DEFAULT_MODEL: Optional[str] = Field(
        default="gemini-2.5-flash",
        description=(
            "Optional explicit override for default chat model slug across all providers."
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
    SENTIMENT_SOCIAL_BLEND_WEIGHT: float = Field(
        default=0.4,
        description=(
            "Weight in [0, 1] on the multi-source credibility-weighted social "
            "sentiment component of NewsCatalystSignal.compute()'s blended "
            "score; the Finnhub-headline component gets (1 - this weight) -- "
            "the two always sum to 1.0 by construction (fixes the reviewed "
            "plan's M6 finding: unnormalized w1=0.4/w2=0.1 weights). Applied "
            "only when multi-source social documents exist for a symbol this "
            "trading day; gracefully degrades to headline-only (weight 1.0) "
            "otherwise -- never a fabricated social score (CONSTRAINT #4)."
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
    # See docs/plans/PROMPT_REGISTRY_PLAN.md §8 for the full security model.
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

    # --- Phase 2 PR3: RAG-Powered Portfolio Contextualizer -------------------
    # Retrieves the already-ingested sentiment corpus (sentiment_ingestion_audit,
    # see data/historical_store.py) via an embedded FAISS vector index
    # (data/rag_index.py) and blends it with the deterministic sector-exposure
    # summary (engine/portfolio_exposure.py) into an optional LLM-generated
    # portfolio context note (engine/portfolio_context.py). Off by default —
    # matches the FORECAST_USE_GARCH_SIGMA opt-in convention: zero embedding
    # calls, zero LLM calls, zero vector-store I/O anywhere in the pipeline
    # until explicitly enabled AND a provider key is configured.
    RAG_PORTFOLIO_CONTEXT_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for the RAG-powered portfolio contextualizer. "
            "When False (the default), generate_portfolio_context_note() returns "
            "the deterministic sector-exposure summary only — no retrieval, no "
            "embedding calls, no LLM call. When True AND "
            "RAG_PORTFOLIO_CONTEXT_PROVIDER is not 'none', a best-effort retrieval "
            "over the indexed sentiment corpus feeds a structured LLM note "
            "(PortfolioContextNote) alongside the always-present exposure summary."
        ),
    )
    RAG_PORTFOLIO_CONTEXT_PROVIDER: str = Field(
        default="none",
        description=(
            "LLM provider used for the final portfolio-context call — 'claude', "
            "'gemini', or 'none' (disable regardless of the master switch). "
            "Requires the matching API key (ANTHROPIC_API_KEY or GEMINI_API_KEY, "
            "both classified as SECRET_KEYS — never GUI-writable)."
        ),
    )
    RAG_EMBEDDING_PROVIDER: str = Field(
        default="openai",
        description=(
            "LLM provider used for embed_texts() calls when indexing the "
            "sentiment corpus and embedding a retrieval query — 'openai' "
            "(text-embedding-3-small) or 'gemini' (text-embedding-004). "
            "Requires the matching API key (OPENAI_API_KEY or GEMINI_API_KEY)."
        ),
    )
    RAG_INDEX_MAX_DOCUMENTS: int = Field(
        default=5000,
        description=(
            "Maximum number of documents retained in the embedded FAISS index "
            "(data/rag_index.py). When indexing would exceed this cap, the "
            "oldest rows are evicted (FIFO by insertion order) so the index "
            "stays bounded on a single-operator desktop machine."
        ),
    )
    RAG_RETRIEVAL_TOP_K: int = Field(
        default=5,
        description=(
            "Number of nearest documents returned by "
            "DocumentVectorStore.search() for one portfolio-context query."
        ),
    )
    RAG_INDEX_LOOKBACK_DAYS: int = Field(
        default=90,
        description=(
            "How many trailing days of sentiment_ingestion_audit are scanned "
            "for not-yet-embedded documents each time "
            "generate_portfolio_context_note() runs (engine/portfolio_context.py's "
            "_index_pending_documents, the only production caller of "
            "DocumentVectorStore.index_new_documents()). Already-indexed rows "
            "(tracked in rag_indexed_docs) are always skipped regardless of this "
            "window, so raising it only widens how far back a first-ever index "
            "pass will look, not how much gets re-embedded on subsequent calls."
        ),
    )

    # ── ETF Holdings Ingestion (data/etf_holdings.py) ────────────────────────
    # Feeds the planned "ETF volatility transmission" risk overlay grounded in
    # Ben-David, Franzoni & Moussawi (2018), "Do ETFs Increase Volatility?",
    # Journal of Finance 73(6):2471-2535.  Nothing in the platform consumes
    # these holdings yet — this is a self-contained data-layer capability.
    ETF_HOLDINGS_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for live ETF constituent-holdings ingestion "
            "(SEC N-PORT primary, optional iShares CSV secondary). False "
            "(the default) is a complete no-op: data.etf_holdings."
            "get_etf_holdings() returns {} immediately with ZERO network "
            "calls and zero DB reads, so a fresh clone / CI run never "
            "touches EDGAR. Nothing in the platform consumes ETF holdings "
            "yet — enabling this only populates the etf_holdings cache "
            "table; it changes no score, weight, or order."
        ),
    )
    ETF_HOLDINGS_TICKERS: list[str] = Field(
        default_factory=lambda: [
            "SPY", "IVV", "VOO", "QQQ", "DIA", "IWM",
            "XLK", "XLF", "XLV", "XLE", "XLI",
            "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
        ],
        description=(
            "ETF wrapper suite whose constituent holdings are ingested each "
            "refresh — the broad-market and sector wrappers that account for "
            "the bulk of US single-name ETF ownership. JSON-encoded list in "
            ".env (e.g. ETF_HOLDINGS_TICKERS='[\"SPY\",\"QQQ\"]'). Only "
            "consulted once ETF_HOLDINGS_ENABLED is True. A ticker whose "
            "holdings cannot be resolved is simply ABSENT from the result "
            "(never present with a fabricated empty or zero-weight holdings "
            "list — CONSTRAINT #4)."
        ),
    )
    ETF_HOLDINGS_REFRESH_DAYS: int = Field(
        default=7,
        description=(
            "Age (days, measured on the cached row's fetched_at) beyond "
            "which an ETF's cached etf_holdings rows are re-fetched from the "
            "source. Deliberately coarse: SEC N-PORT reports three month-ends "
            "per filing and publishes ~60 days after quarter end, so the "
            "underlying data changes at most monthly and is 1-5 months stale "
            "by construction — polling faster than this only burns SEC "
            "requests for identical rows. Only consulted once "
            "ETF_HOLDINGS_ENABLED is True."
        ),
    )
    ETF_HOLDINGS_ISSUER_CSV_ENABLED: bool = Field(
        default=False,
        description=(
            "Opt-in SECONDARY holdings source: the iShares issuer CSV "
            "endpoint, consulted ONLY when SEC N-PORT produced nothing for a "
            "symbol. False (the default) means the iShares endpoint is never "
            "contacted — N-PORT is the sole source. Never the default "
            "because issuer files are undocumented, unversioned, and can "
            "change shape without notice; N-PORT is a regulatory filing with "
            "a fixed schema. Enabling this also imports a family "
            "constraint: iShares covers IVV plus the iShares sector suite, "
            "and the two ETF families must never be mixed inside one "
            "composite (see data/etf_holdings.py). Only consulted once "
            "ETF_HOLDINGS_ENABLED is True."
        ),
    )
    ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE: float = Field(
        default=60.0,
        description=(
            "Hard wall-clock ceiling (seconds) for get_etf_holdings()'s "
            "entire per-cycle loop over the ETF universe. Mirrors "
            "ATTENTION_INGESTION_MAX_SECONDS_PER_CYCLE for the identical "
            "risk shape: a slow-but-responding EDGAR endpoint can otherwise "
            "stack its per-request timeout across every remaining ETF with "
            "no overall ceiling — once this budget elapses, remaining ETFs "
            "are served from the cache only (no network) and any ETF with no "
            "cached rows is simply absent from the result, never fabricated. "
            "Only consulted once ETF_HOLDINGS_ENABLED is True."
        ),
    )
    ETF_HOLDINGS_CIRCUIT_BREAKER_THRESHOLD: int = Field(
        default=3,
        description=(
            "Consecutive no-holdings outcomes (exception or empty result) "
            "within one get_etf_holdings() cycle before the live source is "
            "skipped for the rest of that cycle's ETFs — avoids burning the "
            "wall-clock budget on a source that is clearly failing for every "
            "remaining symbol. Mirrors ATTENTION_CIRCUIT_BREAKER_THRESHOLD. "
            "Only consulted once ETF_HOLDINGS_ENABLED is True."
        ),
    )
    OPTIONS_TRUE_IVR_ENABLED: bool = Field(
        default=False,
        description=(
            "Opt-in: wires a real, options-chain-derived True_IVR into "
            "technical_options_engine.build_premium_directive() -- the GUI Technical "
            "Options Matrix tab, the get_options_directive MCP tool, "
            "api/metrics_api.py, execution/options_queue_builder.py, and every other "
            "build_premium_directive caller -- instead of leaving true IV rank "
            "exclusive to main_orchestrator.py's pipeline/production_steps.py::"
            "OptionsAnalysisStep path. When True, build_premium_directive fetches a "
            "live 30-calendar-day ATM IV via volatility.iv_engine.get_30d_atm_iv() "
            "(a fresh, lightweight DataEngine constructed with no FRED key purely "
            "for its fetch_options_chain() -- CompositeProvider/data/market_data.py "
            "has no chain-shaped method to reuse, so this mirrors exactly what "
            "OptionsAnalysisStep already does rather than inventing a second "
            "convention) and ranks it against the SAME iv_history table "
            "(volatility.iv_engine.IVHistoryStore) OptionsAnalysisStep writes to via "
            "calculate_true_ivr() -- strictly prior days only, never a lookahead. "
            "The result is surfaced as a NEW True_IVR row key alongside the "
            "existing realized-vol-only IVR_Proxy (never replacing it -- both stay "
            "so provenance is honest); generate_strategy_pricing_matrix's true_ivr "
            "argument prefers True_IVR over IVR_Proxy when the flag is on and a "
            "finite value was computed, falling back to IVR_Proxy exactly as today "
            "otherwise. Any failure at any step -- no live chain data, an empty "
            "iv_history table during warm-start (this repo's dev/CI sandboxes never "
            "populate GUI/MCP-path history since only OptionsAnalysisStep's "
            "orchestrator path writes to it), a network error, or any exception -- "
            "degrades to float('nan') for True_IVR and never crashes or changes "
            "IVR_Proxy/Cash-Wait fallback behavior (CONSTRAINT #4/#6). False (the "
            "default) reproduces today's exact behavior byte-for-byte -- no new "
            "network call, no new DB read, True_IVR always NaN. Enabling this adds "
            "one live options-chain fetch per symbol per render (GUI)/per call "
            "(MCP) -- a real, non-trivial network cost the realized-vol proxy never "
            "had."
        ),
    )
    # Master switch for the Pilots API's dead-letter retry endpoint
    # (api/pilots_api.py POST /dead-letter/retry -- spawns a real single-symbol
    # `main.py` subprocess via gui.orchestrator_runner.launch_symbol_retry, the
    # SAME launcher the Streamlit Launcher tab's dead-letter Retry button
    # already calls). A DEDICATED flag, per this codebase's established
    # pattern (see BROKERAGE_REFRESH_ENABLED /
    # GENERAL_SETTINGS_WRITES_ENABLED / MACRO_GATE_WRITES_ENABLED above): a
    # write with a real persistence/subprocess/network cost gets its OWN
    # flag, never rides in on an unrelated one (e.g. AUTOMATION_WRITES_ENABLED,
    # which is scoped to the daemon interval and kill-switch resume). Default
    # False. GUI-writable (added to gui/env_io.py's ALLOWED_KEYS 2026-08-08 by
    # operator decision — carries no secret material, so it's not in
    # SECRET_KEYS either; also a settings_keysets.DANGEROUS_KEYS member,
    # requiring typed confirmation on write regardless of editor), and also
    # requires FOLLOW_API_TOKEN. GET /dead-letter is read-only and NOT gated
    # by this flag (require_read_token alone, matching every other GET here).
    DEAD_LETTER_RETRY_ENABLED: bool = Field(
        default=True,
        description=(
            "Enables POST /dead-letter/retry on the Pilots API (re-runs main.py "
            "for one dead-lettered symbol, advisory-only -- no orders). Off by "
            "default; also requires FOLLOW_API_TOKEN. GUI-writable by operator "
            "decision (2026-08-08) — the endpoint remains gated by "
            "FOLLOW_API_TOKEN regardless, so a single-symbol pipeline re-run "
            "cannot ride in on any other writes-enabled flag."
        ),
    )

    # ── Pilots PWA parity: Prompt Registry writes / Universe sync (2026-07) ──
    # Two independent, dedicated fail-closed master-switch flags for
    # api/pilots_api.py's `PUT /prompts/pin` and api/data_api.py's
    # `POST /data/sync` respectively (see .claude/skills/pilots-endpoint/
    # SKILL.md §1's "fail-closed command + dedicated master flag" tier).
    # Both default to False (today's exact behavior — neither endpoint exists
    # in a reachable form until explicitly enabled). PROMPT_REGISTRY_WRITES_ENABLED
    # is GUI-writable (added to gui/env_io.py's ALLOWED_KEYS 2026-08-08 by
    # operator decision — carries no secret material, so it's not in
    # SECRET_KEYS either; also a settings_keysets.DANGEROUS_KEYS member,
    # requiring typed confirmation on write regardless of editor), exactly
    # like STRATEGY_WRITES_ENABLED / AUTOMATION_WRITES_ENABLED above.
    # UNIVERSE_SYNC_ENABLED (below) is GUI-writable by operator decision — see
    # its own Field description.
    PROMPT_REGISTRY_WRITES_ENABLED: bool = Field(
        default=True,
        description=(
            "FAIL-CLOSED master switch for api/pilots_api.py's `PUT /prompts/pin` "
            "(pins/clears a prompt ID's PROMPT_REGISTRY_PINS entry -- changes WHICH "
            "PROMPT TEXT THE PLATFORM ACTUALLY RUNS, a real behavioral change, not "
            "merely a config tunable). A DEDICATED flag, not "
            "STRATEGY_WRITES_ENABLED/GENERAL_SETTINGS_WRITES_ENABLED/etc: pinning a "
            "prompt version is its own risk class and must not ride in on a sibling "
            "flag scoped to a different concern. `GET /prompts` and "
            "`GET /prompts/{id}` are read-only and NOT gated by this flag "
            "(require_read_token alone, matching every other GET). Sits BEHIND the "
            "fail-closed FOLLOW_API_TOKEN command-token guard, same tier as "
            "PUT /strategy/modules. Effective only on the next daemon restart -- "
            "there is no live setter for .env-sourced config in this codebase. "
            "GUI-writable by operator decision (2026-08-08)."
        ),
    )
    UNIVERSE_SYNC_ENABLED: bool = Field(
        default=True,
        description=(
            "FAIL-CLOSED master switch for api/data_api.py's `POST /data/sync` "
            "(runs data.portfolio_sync.async_sync_now() -- a live Robinhood/broker "
            "read plus a DEFAULT_TICKERS .env write). A DEDICATED flag: this is a "
            "real broker call with a real .env side effect, a materially different "
            "risk from every fail-open GET on this API. GUI-writable (added to "
            "gui/env_io.py's ALLOWED_KEYS by operator decision) -- the endpoint "
            "remains gated by the STATE_API_TOKEN command-token guard below "
            "independently of this flag. `GET /data/sync-report` "
            "remains read-only and NOT gated by this flag. Sits behind the "
            "fail-closed require_write_token guard (STATE_API_TOKEN), matching this "
            "module's existing PUT /data/universe posture. POST /data/sync never "
            "forces an interactive-MFA live login (fetch_account_snapshot is always "
            "called with force=False) -- a headless HTTP handler must never block "
            "on stdin that will never arrive."
        ),
    )

    # --- 26. Forecast Backfill & Meta-Labeling Settings (ml/forecast_backfill.py) ---
    FORECAST_BACKFILL_HORIZONS: list[int] = Field(
        default_factory=lambda: [10, 30, 60, 90],
        description=(
            "Forecast horizons in days for multi-horizon forecast backfilling "
            "and meta-labeling (e.g. 10, 30, 60, 90 days)."
        ),
    )
    FORECAST_BACKFILL_LOOKBACK_YEARS: int = Field(
        default=4,
        description=(
            "Default backfill lookback window in years, used when the caller "
            "doesn't supply an explicit start_date (e.g. the webapp's 'Run "
            "Forecast Backfill' button, which always omits it). Computed "
            "relative to end_date at run time, not a fixed calendar date -- "
            "so the window keeps rolling forward on every re-run instead of "
            "growing unbounded."
        ),
    )
    FORECAST_BACKFILL_MOMENTUM_WINDOW: int = Field(
        default=252,
        description="Lookback window in trading days for primary TSMOM & CSMOM signals (default 252 days = 1 year).",
    )
    FORECAST_BACKFILL_VOL_SHORT_WINDOW: int = Field(
        default=20,
        description="Short rolling volatility lookback window in days (default 20 days).",
    )
    FORECAST_BACKFILL_VOL_LONG_WINDOW: int = Field(
        default=50,
        description="Long rolling volatility lookback window in days (default 50 days).",
    )
    FORECAST_BACKFILL_RSI_WINDOW: int = Field(
        default=14,
        description="RSI calculation lookback window in days (default 14 days).",
    )
    FORECAST_BACKFILL_MACD_FAST: int = Field(
        default=12,
        description="MACD fast exponential moving average span (default 12).",
    )
    FORECAST_BACKFILL_MACD_SLOW: int = Field(
        default=26,
        description="MACD slow exponential moving average span (default 26).",
    )
    FORECAST_BACKFILL_VOL_RATIO_WINDOW: int = Field(
        default=20,
        description="Volume moving average ratio lookback window in days (default 20 days).",
    )
    FORECAST_BACKFILL_TRAIN_SPLIT: float = Field(
        default=0.80,
        description="Chronological train/test split fraction (default 0.80 = 80% train, 20% test).",
    )
    FORECAST_BACKFILL_N_ESTIMATORS: int = Field(
        default=100,
        description="Number of trees/estimators for forecast meta-label classifier (default 100).",
    )
    FORECAST_BACKFILL_MAX_DEPTH: int = Field(
        default=5,
        description="Maximum tree depth for forecast meta-label classifier (default 5).",
    )
    FORECAST_BACKFILL_RANDOM_STATE: int = Field(
        default=42,
        description="Random state seed for reproducibility in forecast meta-label classifier (default 42).",
    )
    FORECAST_BACKFILL_CLASSIFIER_TYPE: str = Field(
        default="random_forest",
        description="Classifier algorithm for forecast backfilling ('random_forest' or 'lightgbm').",
    )
    # Master switch for the async, job-based forecast-backfill endpoints
    # (POST /pilots/forecast_backfill/run, POST /pilots/forecast_backfill/
    # cancel/{job_id} -- see ml/forecast_backfill_job.py / api/pilots_api.py).
    # False by default: this spawns an isolated, CPU-bound subprocess
    # (ml/forecast_backfill_worker.py) that trains and OVERWRITES the
    # meta-labeler model artifacts (ml/models/meta_*.pkl) feeding the live
    # meta_label_composite score -- a materially heavier and more
    # consequential action than an ordinary config-toggle write. GUI-writable
    # (gui/env_io.py's ALLOWED_KEYS) like every other non-secret tunable, per
    # explicit operator decision, but also a settings_keysets.DANGEROUS_KEYS
    # member (SAFETY_CRITICAL_KEY_REASONS), requiring typed confirmation on
    # write regardless of editor -- the same treatment as the other
    # 2026-08-08 "moved here from HAND_SET_ONLY_KEYS" flags in that module.
    # The endpoints remain independently gated by the FOLLOW_API_TOKEN
    # command token regardless of this flag's own value.
    FORECAST_BACKFILL_ENABLED: bool = Field(
        default=False,
        description=(
            "Enables POST /pilots/forecast_backfill/run and "
            "POST /pilots/forecast_backfill/cancel/{job_id} on the Pilots "
            "API. False by default. GUI-writable like any other non-secret "
            "tunable, but requires typed confirmation on write (a "
            "settings_keysets.DANGEROUS_KEYS member) because flipping it "
            "spawns a subprocess that retrains and overwrites production "
            "ml/models/meta_*.pkl artifacts read by live inference. "
            "GET /pilots/forecast_backfill and "
            "GET /pilots/forecast_backfill/status/{job_id} remain read-only "
            "and are NOT gated by this flag."
        ),
    )
    FORECAST_BACKFILL_DEADLINE_SECONDS: int = Field(
        default=5400,
        description=(
            "Hard wall-clock deadline for one forecast-backfill run, from "
            "worker start to a terminal result. The worker process group is "
            "SIGKILLed if it hasn't produced a result by then. Generous "
            "relative to data/robinhood_login.py's RH_LOGIN_DEADLINE_SECONDS "
            "(180s, bounded by how long a human will wait for a push "
            "notification) -- this job is a CPU-bound multi-ticker, "
            "multi-horizon model training run, not a human-approval wait. "
            "Sized (90 minutes) to cover a full run over today's real "
            "operator universe (~500 tickers), not just a small test "
            "fixture -- ml/forecast_backfill.py's AgenticForecastBackfiller "
            "genuinely exceeded the prior 1800s (30 min) default at that "
            "scale because two of its stages are not yet "
            "vectorized/checkpointed. This sandbox has no live-market "
            "network access to precisely re-measure full-universe runtime, "
            "so this is a deliberately generous, documented safety margin "
            "rather than a tightly-tuned number. It remains a hard backstop "
            "even after the pipeline's own perf fixes land -- a stuck "
            "worker must still be reaped eventually."
        ),
    )

    @field_validator("OUTPUT_DIR")
    @classmethod
    def _ensure_output_dir(cls, value: Optional[Path]) -> Optional[Path]:
        """Coerce to ``Path`` and create the directory if it does not exist.

        A ``None`` value (unset) is a no-op passthrough — the model_validator
        below fills it in from ``LOCAL_DATA_ROOT`` once every field has
        resolved, since a static field_validator cannot read another field's
        value.
        """
        if value is None:
            return None
        path = Path(value)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Unable to create output directory %s (%s). Proceeding anyway.",
                path,
                exc,
            )
        return path

    @field_validator("DAEMON_SHUTDOWN_TIMEOUT_SECONDS")
    @classmethod
    def _validate_daemon_shutdown_timeout(cls, value: float) -> float:
        return validate_daemon_shutdown_timeout(value)

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

    @field_validator("HMM_COVARIANCE_TYPE")
    @classmethod
    def _coerce_hmm_covariance_type(cls, value: str) -> str:
        """Coerce HMM covariance type to lower-case, falling back to 'diag' if invalid."""
        v = str(value or "").strip().lower()
        return v if v in {"diag", "full", "spherical", "tied"} else "diag"

    @field_validator("HMM_N_ITER")
    @classmethod
    def _validate_hmm_n_iter(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("HMM_N_ITER must be greater than 0")
        return value

    @field_validator("HMM_TOL")
    @classmethod
    def _validate_hmm_tol(cls, value: float) -> float:
        if value <= 0.0:
            raise ValueError("HMM_TOL must be greater than 0")
        return value

    @field_validator("HMM_N_INITS")
    @classmethod
    def _validate_hmm_n_inits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("HMM_N_INITS must be greater than 0")
        return value

    @field_validator("HMM_RISK_ON_DOWNGRADE_THRESHOLD", "HMM_RISK_OFF_AGREEMENT_THRESHOLD")
    @classmethod
    def _validate_hmm_probability_thresholds(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("Probability thresholds must be between 0.0 and 1.0")
        return value

    @field_validator("KILLSWITCH_VIX_THRESHOLD_AGREED", "KILLSWITCH_SAHM_THRESHOLD_AGREED")
    @classmethod
    def _validate_killswitch_thresholds(cls, value: float) -> float:
        if value < 0.0:
            raise ValueError("Killswitch thresholds must be non-negative")
        return value


    CACHE_LONG_SHORT_ENABLED: bool = Field(
        default=False,
        description="Master switch for the Cache Long/Short tax-loss-harvesting "
        "advisory strategy. False (the default) is a complete no-op reproducing "
        "today's exact behavior: the background TLH/correlation-drift scanner in "
        "main_orchestrator.py never starts, and every read endpoint returns an "
        "honest empty/disabled shape. Advisory only in this version -- no broker "
        "order is ever submitted regardless of this flag. This is a trading-"
        "behavior flag, not an admin/API capability, so it keeps the opt-in "
        "default per the 2026-08-03 convention-change carve-out above.",
    )
    CACHE_LONG_SHORT_WRITES_ENABLED: bool = Field(
        default=True,
        description="Dedicated fail-closed flag for POST /pilots/cache-long-short/* "
        "write endpoints (start, approve-bulk) -- persists a new tracked position "
        "or marks a TLH recommendation approved. Its own risk class, must not "
        "ride in on AUTOMATION_WRITES_ENABLED/STRATEGY_WRITES_ENABLED (this "
        "changes what a trading strategy recommends). GUI-writable (added to "
        "gui/env_io.py's ALLOWED_KEYS 2026-08-08 by operator decision -- carries "
        "no secret material; also a settings_keysets.DANGEROUS_KEYS member, "
        "requiring typed confirmation on write regardless of editor). The "
        "POST /pilots/cache-long-short/* endpoints it guards remain "
        "independently gated by their own command token regardless.",
    )
    CACHE_LONG_SHORT_MIN_CORRELATION: float = Field(default=0.75, description="Min correlation to trigger drift alert")
    CACHE_LONG_SHORT_TLH_THRESHOLD_PCT: float = Field(default=0.05, description="Percentage loss to trigger TLH")
    CACHE_LONG_SHORT_SCAN_INTERVAL_SECONDS: int = Field(default=3600, description="Interval for cache l/s worker loop")
    CACHE_LONG_SHORT_PROXY_CANDIDATES: list[str] = Field(
        default_factory=lambda: ["SPY", "QQQ", "XLK", "XLF", "XLV", "XLE"],
        description="Candidate proxy ETFs find_correlated_proxy() screens "
        "against for a concentrated ticker's hedge leg.",
    )
    OPTIONS_COPULA_ZSCORE_ENTRY_THRESHOLD: float = Field(
        default=2.0,
        description="pilots/copula_stat_arb.py's pairs-trading entry/exit "
        "z-score band: |Z_t| >= this value triggers a LONG_SPREAD/SHORT_SPREAD "
        "entry signal (default matches the module's prior hardcoded literal, "
        "so this is a no-op until an operator changes it). Read by "
        "generate_copula_stat_arb_signals' and evaluate_copula_stat_arb_pair's "
        "z_entry/z_entry_threshold parameter defaults.",
    )

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

    # Missing fields flagged by the codebase auditor (scripts/auditor/
    # stockpy_codebase_auditor.py's undeclared_env_var check).
    #
    # RH_LOGIN_WORKER and KEY are deliberately NOT declared here:
    #  - RH_LOGIN_WORKER is a structural in-process marker
    #    (os.environ.get("RH_LOGIN_WORKER") == "1", string comparison, never
    #    read via settings.X) set only by data/robinhood_login_worker.py to
    #    prove a Robinhood device-approval login is running inside its
    #    required isolated subprocess. Declaring it as a normal bool Settings
    #    field would invite setting it in .env, which would defeat that
    #    isolation guard for the main process — see .env.example's comment
    #    on it and CLAUDE.md's "Robinhood login moved from TOTP MFA to
    #    device-approval push" section.
    #  - KEY was a false positive: the auditor's undeclared_env_var check is
    #    a raw regex over file text (not AST-based), and it matched the
    #    literal string `os.environ.get("key")` inside a comment in
    #    scripts/measure_settings_census.py illustrating that script's own
    #    pattern-matching, not a real env var read anywhere in the codebase.
    WATCHLIST: str = Field(
        default="",
        description="Comma-separated list of symbols to always include in the universe.",
    )
    GCLOUD_BIN: str = Field(
        default="gcloud",
        description="Path to the gcloud binary for environment integrations.",
    )
    GRAVITY_REQUIRE_NATIVE: bool = Field(
        default=False,
        description="Require native implementation for Gravity Review Suite.",
    )
    QDRANT_COLLECTION: str = Field(
        default="",
        description="Qdrant collection name for RAG orchestrator.",
    )
    QDRANT_URL: str = Field(
        default="",
        description="Qdrant URL for RAG orchestrator.",
    )

    @model_validator(mode="after")
    def _derive_local_data_root_paths(self) -> "Settings":
        """Fill in OUTPUT_DIR and OUTPUT_DIR-relative cache paths from
        LOCAL_DATA_ROOT once every field has resolved.

        A Field(default=...) expression cannot read another field's resolved
        value, so OUTPUT_DIR's "defaults to <LOCAL_DATA_ROOT>/output" behavior
        has to live here rather than as a static default. An operator's
        explicit OUTPUT_DIR=/custom/path override always wins -- this only
        ever fills in a still-None value.

        The three str-typed cache-path fields below are only re-anchored
        under the resolved OUTPUT_DIR if their value still EXACTLY matches
        the documented relative-default literal (i.e. the operator never
        overrode it) -- an explicit override (any other string) is left
        untouched.
        """
        try:
            self.LOCAL_DATA_ROOT.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Unable to create LOCAL_DATA_ROOT directory %s (%s). Proceeding anyway.",
                self.LOCAL_DATA_ROOT,
                exc,
            )

        if self.OUTPUT_DIR is None:
            resolved_output_dir = self.LOCAL_DATA_ROOT / "output"
            try:
                resolved_output_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning(
                    "Unable to create output directory %s (%s). Proceeding anyway.",
                    resolved_output_dir,
                    exc,
                )
            self.OUTPUT_DIR = resolved_output_dir

        if self.PROMPT_CACHE_DIR == "output/prompt_cache":
            self.PROMPT_CACHE_DIR = str(self.OUTPUT_DIR / "prompt_cache")
        if self.LLM_COMMENTARY_CACHE_PATH == "output/llm_commentary_cache.json":
            self.LLM_COMMENTARY_CACHE_PATH = str(self.OUTPUT_DIR / "llm_commentary_cache.json")
        if self.GRAVITY_AI_RUNNER_OUTPUT_PATH == "output/gravity_ai_audit.json":
            self.GRAVITY_AI_RUNNER_OUTPUT_PATH = str(self.OUTPUT_DIR / "gravity_ai_audit.json")

        return self

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


# =============================================================================
# Runtime settings store (read path) — layered ON TOP of the singleton above.
# =============================================================================
# `runtime_flags.apply_overrides` lets a field value come from
# `output/runtime_flags.json` in addition to real env vars and `.env`. It is a
# NO-OP unless that file exists, which it does not on any install today (the
# writer that creates it is a separate, later change). See runtime_flags.py's
# module docstring for the JSON shape, the precedence rule
# (real shell env > store > .env > default), and why this is a post-construction
# `setattr` layer rather than a pydantic settings source.
#
# Placement: this MUST run after `settings = Settings()` — it mutates the
# already-constructed singleton, because 146+ modules do
# `from settings import settings` and bind the OBJECT, so the singleton can
# never be reconstructed or reassigned once this module finishes importing.
#
# The import is deferred to here (rather than the module header) and wrapped so
# that ANY failure — a missing/corrupt runtime_flags.py, an unanticipated bug
# inside it, a broken python-dotenv install — degrades to "no overrides
# applied" instead of breaking `import settings` for the ENTIRE application.
# Every entry point in this platform imports this module; a raise here is a
# total outage. runtime_flags.py is independently defensive per CONSTRAINT #6;
# this is the outermost belt-and-suspenders net, not a substitute for that.
#
# RUNTIME_FLAGS_REPORT is descriptive only — nothing reads it to make a
# decision. A later task surfaces it to an operator (notably
# `.skipped_env_pinned`, which explains why a store edit did not take effect).
try:
    import runtime_flags as _runtime_flags

    RUNTIME_FLAGS_REPORT = _runtime_flags.apply_overrides(settings)
except Exception:  # pragma: no cover - defensive; must never break the import
    logger.warning(
        "runtime_flags override layer failed to load; continuing with "
        "environment/.env-sourced settings only.",
        exc_info=True,
    )
    RUNTIME_FLAGS_REPORT = None
