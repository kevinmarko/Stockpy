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
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # --- Financial constants ---
    RISK_FREE_RATE: float = 0.045
    MARKET_RISK_PREMIUM: float = 0.055
    REQUIRED_RETURN_RATE: float = 0.08
    MAX_PORTFOLIO_HEAT: float = 0.06

    # --- Runtime / IO ---
    OUTPUT_DIR: Path = Field(default=Path("./output"), description="Directory for generated reports.")
    DEFAULT_TICKERS: list[str] = Field(default_factory=lambda: ["AAPL", "MSFT", "JNJ", "AGNC"])
    LOG_LEVEL: str = "INFO"

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
