# =============================================================================
# MODULE: ROBINHOOD PORTFOLIO SNAPSHOT  (READ-ONLY, ADVISORY ONLY)
# File: data/robinhood_portfolio.py
#
# ADVISORY ONLY — this module fetches account state (positions, equity,
# dividends) strictly for analysis.  It contains NO order-submission,
# order-modification, or order-cancellation code of any kind.  Do NOT add
# any execution function here under any circumstances.
#
# Description:
#   Authenticates to Robinhood via TOTP (RFC 6238) and returns a clean,
#   typed AccountSnapshot that includes:
#     - Per-symbol PortfolioPosition (qty, avg cost, current price, P/L,
#       dividends received per symbol)
#     - Account-level buying power and total equity
#     - Total dividends received across all symbols
#
#   A daily cache at cache/account_snapshot.json prevents repeated logins
#   on the same day.  The cache is an advisory fallback — stale data is
#   surfaced (age_hours / is_stale) rather than hidden or errored.
#
# Authentication env vars (loaded from .env via python-dotenv / pydantic-settings):
#   RH_USERNAME   — Robinhood account email
#   RH_PASSWORD   — Robinhood account password
#   RH_MFA_SECRET — Base32 TOTP secret from the Robinhood MFA setup page
#
# Dependencies:
#   pyotp>=2.9.0   (generates the 6-digit TOTP code)
#   robin_stocks>=3.1.0  (already in requirements.txt)
# =============================================================================

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pyotp
import robin_stocks.robinhood as r

from dto_models import RobinhoodPositionDTO

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache location — one level above data/ (project root) / cache /
# ---------------------------------------------------------------------------
_CACHE_PATH: Path = Path(__file__).parent.parent / "cache" / "account_snapshot.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    """Return the stripped value of *name* from os.environ, or raise.

    Provides a clear, actionable error message naming exactly which variable
    is missing so the developer knows what to add to .env.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is missing or empty. "
            f"Add '{name}=' to your .env file and restart."
        )
    return value


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PortfolioPosition:
    """Immutable snapshot of one equity position as reported by Robinhood.

    All monetary fields are in USD.  ``unrealized_pl_pct`` is expressed as a
    percentage (e.g. 12.5 means +12.5 %).
    """

    symbol: str
    quantity: float
    average_cost: float        # per-share average cost basis
    current_price: float       # last known price from Robinhood build_holdings
    market_value: float        # quantity * current_price (or Robinhood's equity field)
    unrealized_pl: float       # market_value - (quantity * average_cost)
    unrealized_pl_pct: float   # unrealized_pl / cost_basis * 100
    dividends_received: float  # cumulative paid + reinvested dividends for this symbol
    name: str                  # human-readable company name

    # ---- serialization ----

    def to_dict(self) -> dict:
        """Serialize to a plain dict.  Safe to pass to json.dumps()."""
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "average_cost": self.average_cost,
            "current_price": self.current_price,
            "market_value": self.market_value,
            "unrealized_pl": self.unrealized_pl,
            "unrealized_pl_pct": self.unrealized_pl_pct,
            "dividends_received": self.dividends_received,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PortfolioPosition":
        """Deserialize from a dict (e.g. parsed from the JSON cache)."""
        return cls(
            symbol=str(d["symbol"]),
            quantity=float(d["quantity"]),
            average_cost=float(d["average_cost"]),
            current_price=float(d["current_price"]),
            market_value=float(d["market_value"]),
            unrealized_pl=float(d["unrealized_pl"]),
            unrealized_pl_pct=float(d["unrealized_pl_pct"]),
            dividends_received=float(d["dividends_received"]),
            name=str(d["name"]),
        )


@dataclass(frozen=True)
class AccountSnapshot:
    """Immutable point-in-time view of the full Robinhood account.

    ``fetched_at`` is always UTC-aware (``datetime.now(timezone.utc)``).
    Monetary fields are in USD.
    """

    positions: dict          # symbol (str) -> PortfolioPosition
    buying_power: float      # unallocated cash available to trade
    total_equity: float      # portfolio equity at time of snapshot
    total_dividends: float   # sum of all paid + reinvested dividends across symbols
    fetched_at: datetime     # UTC-aware timestamp of the live fetch

    # ---- freshness helpers ----

    def age_hours(self) -> float:
        """Floating-point hours elapsed since this snapshot was fetched."""
        delta = datetime.now(timezone.utc) - self.fetched_at
        return delta.total_seconds() / 3600.0

    def is_stale(self, max_age_hours: float = 20.0) -> bool:
        """True when the snapshot is older than *max_age_hours*.

        Callers can surface this to the user ("account data is 25 h old")
        without crashing — stale data is informational, not an error.
        """
        return self.age_hours() > max_age_hours

    # ---- serialization ----

    def to_dict(self) -> dict:
        """Serialize to a plain dict.  No credentials are ever included."""
        return {
            "positions": {sym: pos.to_dict() for sym, pos in self.positions.items()},
            "buying_power": self.buying_power,
            "total_equity": self.total_equity,
            "total_dividends": self.total_dividends,
            # ISO 8601 with timezone offset — round-trips losslessly via fromisoformat
            "fetched_at": self.fetched_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AccountSnapshot":
        """Deserialize from a dict (e.g. parsed from the JSON cache)."""
        positions: dict[str, PortfolioPosition] = {
            sym: PortfolioPosition.from_dict(pos_data)
            for sym, pos_data in d["positions"].items()
        }
        return cls(
            positions=positions,
            buying_power=float(d["buying_power"]),
            total_equity=float(d["total_equity"]),
            total_dividends=float(d["total_dividends"]),
            # fromisoformat preserves the tz-offset stored by isoformat()
            fetched_at=datetime.fromisoformat(d["fetched_at"]),
        )


# ---------------------------------------------------------------------------
# Authentication (private)
# ---------------------------------------------------------------------------

def _login() -> None:
    """Authenticate to Robinhood using TOTP or SMS MFA.

    Reads RH_USERNAME, RH_PASSWORD, and optional RH_MFA_SECRET from os.environ
    (populated from .env by python-dotenv / pydantic-settings at startup).
    Raises RuntimeError if any required credential is missing or if login fails.

    ``store_session=True`` persists the session pickle in ~/.tokens so that
    subsequent logins on the same device reuse the stored OAuth token,
    minimising MFA prompts and avoiding spurious "new device" notifications.
    Passing ``mfa_code=`` selects the TOTP path; ``robin-stocks`` >= 3.4
    removed the legacy ``by_sms=`` kwarg and infers the path from whether
    ``mfa_code`` is supplied. If RH_MFA_SECRET is not set, falls back to 
    interactive MFA prompting in the terminal.
    """
    username = _require_env("RH_USERNAME")
    password = _require_env("RH_PASSWORD")
    mfa_secret = os.environ.get("RH_MFA_SECRET", "").strip()

    if mfa_secret:
        # Generate the current 6-digit TOTP code from the base32 secret.
        # pyotp.TOTP.now() honours the RFC 6238 30-second window automatically.
        mfa_code = pyotp.TOTP(mfa_secret).now()
        
        result = r.login(
            username,
            password,
            store_session=True,  # persist ~/.tokens pickle for same-device reuse
            mfa_code=mfa_code,
        )
    else:
        logger.info("RH_MFA_SECRET is missing or empty. Falling back to interactive MFA login.")
        result = r.login(
            username,
            password,
            store_session=True,  # persist ~/.tokens pickle for same-device reuse
        )

    if not isinstance(result, dict) or "access_token" not in result:
        raise RuntimeError(
            "Robinhood login failed — no access_token in login response. "
            "Check RH_USERNAME and RH_PASSWORD."
        )
    logger.info("Robinhood login succeeded.")


# ---------------------------------------------------------------------------
# Live fetch (private)
# ---------------------------------------------------------------------------

def _fetch_live_snapshot() -> AccountSnapshot:
    """Authenticate and pull a fresh snapshot from Robinhood.  READ ONLY.

    Workflow:
      1. TOTP login via _login().
      2. robin_stocks.build_holdings() → per-symbol price + quantity + cost.
      3. robin_stocks.get_dividends() → correlate paid/reinvested totals
         to symbols via instrument UUID extracted from the dividend URL.
      4. robin_stocks.load_portfolio_profile() → total equity.
      5. robin_stocks.load_account_profile()   → buying power.
      6. Build PortfolioPosition objects; isolate per-symbol failures.
      7. Return a fully populated AccountSnapshot.

    Per-symbol failures are logged as warnings and the symbol is skipped;
    they never abort the whole snapshot.
    """
    _login()

    # ------------------------------------------------------------------ #
    # Holdings — dict[symbol, {quantity, average_buy_price, price, ...}]
    # ------------------------------------------------------------------ #
    holdings: dict = r.build_holdings() or {}

    # Map instrument UUID → symbol for dividend correlation.
    # robin_stocks stores the instrument UUID in holdings[sym]["id"].
    instrument_to_symbol: dict[str, str] = {}
    for symbol, data in holdings.items():
        inst_id: str = str(data.get("id") or "").strip()
        if inst_id:
            instrument_to_symbol[inst_id] = symbol

    # ------------------------------------------------------------------ #
    # Dividends — only 'paid' and 'reinvested' states count as realised.
    # Each dividend record's "instrument" field is a URL:
    #   https://api.robinhood.com/instruments/{uuid}/
    # Extract the UUID from the URL to correlate back to the symbol.
    # ------------------------------------------------------------------ #
    dividends_by_symbol: dict[str, float] = {}
    total_dividends: float = 0.0

    dividends_raw: list = r.get_dividends() or []
    for div in dividends_raw:
        if div.get("state") not in ("paid", "reinvested"):
            continue
        try:
            amount = float(div.get("amount") or 0.0)
            inst_url: str = str(div.get("instrument") or "")
            # Extract UUID: strip trailing slash, take last path segment.
            inst_id = inst_url.rstrip("/").rsplit("/", 1)[-1] if inst_url else ""
            sym = instrument_to_symbol.get(inst_id)
            if sym:
                dividends_by_symbol[sym] = dividends_by_symbol.get(sym, 0.0) + amount
            total_dividends += amount
        except Exception as exc:
            logger.warning("Skipping unparseable dividend record: %s", exc)

    # ------------------------------------------------------------------ #
    # Portfolio profile (equity) and account profile (buying power).
    #
    # robin_stocks 3.x exposes these at the top-level namespace:
    #   r.load_portfolio_profile() → {"equity": "...", ...}
    #   r.load_account_profile()   → {"buying_power": "...", ...}
    # Fall back to allied fields when the primary field is absent
    # (e.g. outside market hours the "equity" key may be 0 and
    # "extended_hours_equity" carries the current value).
    # ------------------------------------------------------------------ #
    portfolio_profile: dict = r.load_portfolio_profile() or {}
    account_profile: dict = r.load_account_profile() or {}

    equity_str: str = (
        portfolio_profile.get("equity")
        or portfolio_profile.get("extended_hours_equity")
        or "0"
    )
    buying_power_str: str = (
        account_profile.get("buying_power")
        or account_profile.get("cash")
        or "0"
    )
    total_equity = float(equity_str or 0.0)
    buying_power = float(buying_power_str or 0.0)

    # ------------------------------------------------------------------ #
    # Build per-symbol PortfolioPosition objects.
    # Wrap each in try/except: one bad symbol must never abort the rest.
    # ------------------------------------------------------------------ #
    positions: dict[str, PortfolioPosition] = {}
    for symbol, data in holdings.items():
        try:
            qty = float(data.get("quantity") or 0.0)
            avg_cost = float(data.get("average_buy_price") or 0.0)
            current_price = float(data.get("price") or 0.0)
            # Use Robinhood's pre-computed equity value when available;
            # fall back to qty * price if the field is missing or null.
            market_value = float(data.get("equity") or (qty * current_price))
            cost_basis = qty * avg_cost
            unrealized_pl = market_value - cost_basis
            unrealized_pl_pct = (
                (unrealized_pl / cost_basis * 100.0) if cost_basis > 0.0 else 0.0
            )
            divs = dividends_by_symbol.get(symbol, 0.0)

            positions[symbol] = PortfolioPosition(
                symbol=symbol,
                quantity=qty,
                average_cost=avg_cost,
                current_price=current_price,
                market_value=market_value,
                unrealized_pl=unrealized_pl,
                unrealized_pl_pct=unrealized_pl_pct,
                dividends_received=divs,
                name=str(data.get("name") or symbol),
            )
        except Exception as exc:
            logger.warning(
                "Skipping position %s — parse error: %s", symbol, exc
            )

    snapshot = AccountSnapshot(
        positions=positions,
        buying_power=buying_power,
        total_equity=total_equity,
        total_dividends=total_dividends,
        fetched_at=datetime.now(timezone.utc),
    )
    logger.info(
        "Live Robinhood snapshot fetched: %d positions, equity=%.2f, buying_power=%.2f",
        len(positions),
        total_equity,
        buying_power,
    )
    return snapshot


# ---------------------------------------------------------------------------
# Cache helpers (private)
# ---------------------------------------------------------------------------

def _write_cache(snapshot: AccountSnapshot) -> None:
    """Atomically serialize *snapshot* to _CACHE_PATH (write-then-rename).

    Creates the cache/ directory if it does not exist.  No credentials or
    secret values are ever included in the serialized payload.
    """
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE_PATH.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(snapshot.to_dict(), fh, indent=2)
        tmp.replace(_CACHE_PATH)
        logger.debug("Account snapshot cached → %s", _CACHE_PATH)
    except Exception as exc:
        logger.warning("Failed to write account cache: %s", exc)
        tmp.unlink(missing_ok=True)


def _read_cache() -> Optional[AccountSnapshot]:
    """Load and parse a cached snapshot.

    Returns None if the file is absent, unreadable, or contains invalid JSON
    — the caller decides what to do next (live-fetch or stale-return).
    """
    if not _CACHE_PATH.exists():
        return None
    try:
        with _CACHE_PATH.open("r", encoding="utf-8") as fh:
            return AccountSnapshot.from_dict(json.load(fh))
    except Exception as exc:
        logger.warning("Account cache unreadable (%s) — ignoring.", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_account_snapshot(
    max_age_hours: float = 20.0,
    force: bool = False,
) -> AccountSnapshot:
    """Return a Robinhood account snapshot, using a daily cache when fresh.

    Parameters
    ----------
    max_age_hours:
        Maximum acceptable cache age.  Default 20 h ensures a morning run
        always fetches fresh data while same-day repeat calls use the cache
        (avoiding repeated TOTP logins and Robinhood network calls).
    force:
        When True, bypass the cache unconditionally and re-authenticate +
        re-fetch from Robinhood even if a fresh cache exists.

    Returns
    -------
    AccountSnapshot
        A fully populated snapshot.  On live-fetch failure when a cache
        exists, returns the cached (possibly stale) snapshot — is_stale()
        will be True and age_hours() will reflect the true age.  Only
        raises when the live fetch fails AND no cache exists at all.

    Cache behaviour:
        First call of the day → authenticates, fetches, writes cache, returns.
        Subsequent same-day calls → returns instantly from cache, NO network.
        force=True → always re-authenticates and refreshes cache.
        Live-fetch failure + cache present → returns stale cache, logs warning.
        Live-fetch failure + no cache     → raises the original exception.
    """
    # ---- Tier 1: DB-first read (fastest — no JSON I/O, no network) ----
    if not force:
        try:
            from data.historical_store import HistoricalStore
            _store = HistoricalStore()
            _db_snap = _store.latest_account_snapshot()
            if _db_snap is not None and not _db_snap.is_stale(max_age_hours):
                logger.info(
                    "Using DB-cached account snapshot (age %.1fh)",
                    _db_snap.age_hours(),
                )
                return _db_snap
        except Exception as _exc:
            logger.debug("DB snapshot read failed, falling through: %s", _exc)

    # ---- Tier 2: JSON cache ----
    if not force:
        cached = _read_cache()
        if cached is not None and not cached.is_stale(max_age_hours):
            logger.info(
                "Using cached Robinhood snapshot (age %.1f h < %.1f h limit).",
                cached.age_hours(),
                max_age_hours,
            )
            return cached

    # ---- Tier 3: live fetch ----
    try:
        snapshot = _fetch_live_snapshot()
        _write_cache(snapshot)
        try:
            from data.historical_store import HistoricalStore
            _store = HistoricalStore()
            _store.save_account_snapshot(snapshot)
        except Exception as _exc:
            logger.warning("DB snapshot write failed (non-fatal): %s", _exc)
        return snapshot
    except Exception as exc:
        logger.error("Live Robinhood fetch failed: %s", exc)
        cached = _read_cache()
        if cached is not None:
            logger.warning(
                "Returning stale Robinhood snapshot (age %.1f h) after live-fetch failure.",
                cached.age_hours(),
            )
            return cached
        # No cache and no live data — propagate so the caller can handle it.
        raise


def account_snapshot_to_robinhood_positions(
    snapshot: Optional[AccountSnapshot],
) -> dict[str, RobinhoodPositionDTO]:
    """Convert an AccountSnapshot's positions into the RobinhoodPositionDTO shape
    that main_orchestrator.py's run_pipeline(robinhood_positions=...) parameter
    expects (dto_models.RobinhoodPositionDTO: ticker, shares, average_cost,
    total_dividends).

    This is a pure field-mapping adapter -- no network calls, no fabrication:
    quantity -> shares, average_cost -> average_cost (both already USD/per-share
    in PortfolioPosition), dividends_received -> total_dividends. Returns an
    empty dict for an empty/None snapshot (never raises -- CONSTRAINT #6).
    """
    try:
        if snapshot is None or not snapshot.positions:
            return {}
        return {
            symbol: RobinhoodPositionDTO(
                ticker=symbol,
                shares=pos.quantity,
                average_cost=pos.average_cost,
                total_dividends=pos.dividends_received,
            )
            for symbol, pos in snapshot.positions.items()
        }
    except Exception as exc:
        logger.warning("account_snapshot_to_robinhood_positions failed: %s", exc)
        return {}


def logout() -> None:
    """Log out of the active Robinhood session.

    Errors are swallowed and logged; logout failure must never crash the
    analysis pipeline.
    """
    try:
        r.logout()
        logger.info("Robinhood session logged out.")
    except Exception as exc:
        logger.warning("Robinhood logout error (ignored): %s", exc)
