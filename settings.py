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

    # --- Secrets / credentials (resolved from the environment) ---
    # FRED is required for *live* macro data. It is left empty by default so the
    # platform can still import and fall back to MockDataEngine; the live path
    # calls ``ensure_fred_configured()`` to fail clearly when it is missing.
    FRED_API_KEY: str = Field(
        default="", description="FRED API key. Required for live macroeconomic data."
    )
    ALPACA_API_KEY: Optional[str] = Field(default=None, description="Alpaca API key (optional).")
    ALPACA_SECRET_KEY: Optional[str] = Field(default=None, description="Alpaca secret key (optional).")
    ALPACA_PAPER: bool = Field(default=True, description="Use Alpaca paper-trading endpoint.")

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
            "Finnhub API key for fundamental data (company_basic_financials). "
            "Free tier available at https://finnhub.io. "
            "When absent, fundamentals fall back to yfinance .info (no crash)."
        ),
    )
    # TTL (seconds) for the in-process quote cache in CompositeProvider.
    # Prevents redundant network calls within a single refresh cycle.
    # Quotes must NOT be persisted to disk — cache is in-process only.
    MARKET_DATA_QUOTE_TTL_SECONDS: int = Field(
        default=30,
        description="In-process quote cache TTL in seconds (never persisted to disk).",
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
    # Sliding-window call budget for FinnhubProvider (per 60 s).  Free tier is
    # 60 calls/minute; we default to 50 to leave headroom for the two auxiliary
    # endpoints (quote, company_profile2) that ``get_fundamentals`` invokes.
    FINNHUB_RATE_LIMIT_PER_MIN: int = Field(
        default=50,
        description="Finnhub sliding-window call budget per 60 s (free tier ceiling: 60).",
    )
    # --- Robinhood Integration (legacy data/robinhood_client.py — SMS login) ---
    ROBINHOOD_USERNAME: Optional[str] = Field(default=None, description="Robinhood username (email).")
    ROBINHOOD_PASSWORD: Optional[str] = Field(default=None, description="Robinhood password.")
    # --- Robinhood portfolio snapshot (data/robinhood_portfolio.py — TOTP login) ---
    # Read-only; used for account state only. No order functions anywhere in that module.
    RH_USERNAME: Optional[str] = Field(default=None, description="Robinhood account email for TOTP-authenticated read-only portfolio snapshot.")
    RH_PASSWORD: Optional[str] = Field(default=None, description="Robinhood account password for TOTP-authenticated read-only portfolio snapshot.")
    RH_MFA_SECRET: Optional[str] = Field(default=None, description="Base32 TOTP secret from the Robinhood MFA setup page. Never logged or cached.")
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

    # --- Kill switch (execution/kill_switch.py) ---
    # When True and the kill switch fires, a CRITICAL reminder is logged to flatten
    # open positions manually. Automatic flattening is a future extension.
    FLATTEN_ON_KILL: bool = Field(
        default=False,
        description="Log CRITICAL position-flatten reminder when kill switch activates.",
    )

    # --- Observability / alerts (observability/alerts.py, observability/dashboard.py) ---
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
    DASHBOARD_REFRESH_SECONDS: int = Field(
        default=1800, description="Auto-refresh interval for the Streamlit observability dashboard (seconds). Default 1800 = 30 min."
    )
    # ISO date string (YYYY-MM-DD) recording when paper trading began.
    # Used by scripts/preflight_check.py to verify >= 90 days of paper history.
    PAPER_TRADING_START_DATE: Optional[str] = Field(
        default=None,
        description="ISO date (YYYY-MM-DD) when paper trading began. Required by preflight check.",
    )

    # --- Robinhood portfolio snapshot (data/robinhood_portfolio.py) ---
    # These three variables feed the TOTP-based read-only portfolio fetch.
    # data/robinhood_portfolio.py reads them directly from os.environ so that
    # they are never stored in a Settings object (avoiding accidental logging).
    # They are declared here for .env documentation and pydantic-settings
    # auto-loading consistency only.
    RH_USERNAME: Optional[str] = Field(
        default=None,
        description="Robinhood account email for read-only portfolio snapshot.",
    )
    RH_PASSWORD: Optional[str] = Field(
        default=None,
        description="Robinhood account password for read-only portfolio snapshot.",
    )
    RH_MFA_SECRET: Optional[str] = Field(
        default=None,
        description=(
            "Base32 TOTP secret from the Robinhood MFA setup page. "
            "Used by data/robinhood_portfolio.py to generate the 6-digit code "
            "via pyotp.TOTP(RH_MFA_SECRET).now() — never logged or cached."
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
    LOG_LEVEL: str = "INFO"
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
        },
        description="Weights for individual quantitative signal modules."
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

    @field_validator("OUTPUT_DIR")
    @classmethod
    def _ensure_output_dir(cls, value: Path) -> Path:
        """Coerce to ``Path`` and create the directory if it does not exist."""
        path = Path(value)
        path.mkdir(parents=True, exist_ok=True)
        return path

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
