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
