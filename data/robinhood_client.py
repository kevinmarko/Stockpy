# =============================================================================
# MODULE: ROBINHOOD CLIENT
# File: data/robinhood_client.py
#
# Description
# -----------
# Connects to Robinhood to fetch:
#   1. User holdings, cost basis, and accumulated dividends, mapped to
#      RobinhoodPositionDTOs (legacy "build_holdings + get_dividends" path).
#   2. (Task 1.4) Automated ticker discovery across all active holdings AND
#      every user-defined Robinhood "Lists" (watchlist), deduplicated to a
#      single sorted universe so the analytical pipeline never silently misses
#      an idiosyncratic position the operator is tracking.
#
# Discovery API
# -------------
# Two new public functions support Task 1.4 (Portfolio & Watchlist Sync Engine):
#
#   - ``discover_watchlists(client)`` returns ``{watchlist_name: [tickers]}``.
#     Reads every watchlist via robin_stocks' ``get_all_watchlists()`` and
#     ``get_watchlist_by_name()``; never raises on per-watchlist parse errors —
#     a single bad list is logged and skipped (dead-letter resilience).
#
#   - ``discover_universe(client, extra_files=None)`` returns a sorted, deduped
#     list of symbols from holdings ∪ all RH watchlists ∪ any plain-text
#     file paths in ``extra_files`` (one ticker/line, ``#`` = comment).  The
#     file path list is also derivable from the ``SYNC_WATCHLIST_FILES`` env
#     var (colon-separated) for headless CI use.
#
# Both functions short-circuit cleanly when the client is not authenticated,
# returning empty containers instead of raising — they must never crash the
# orchestrator or the GUI.
# =============================================================================

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import robin_stocks.robinhood as r

from dto_models import RobinhoodPositionDTO
from settings import settings

logger = logging.getLogger("RobinhoodClient")

# Env var: colon-separated list of additional plain-text watchlist files.
# Each file holds one ticker per line; '#' begins a comment. Empty / missing
# files are skipped silently. Surfaced here (not in settings.py) because the
# value is consumed exclusively by the discovery layer.
_WATCHLIST_FILES_ENV: str = "SYNC_WATCHLIST_FILES"


class RobinhoodClient:
    """Encapsulates Robinhood API interactions and DTO mapping.

    Authentication uses the legacy SMS path (``by_sms=True``).  For TOTP-based
    read-only account snapshots see :mod:`data.robinhood_portfolio`.
    """

    def __init__(self) -> None:
        self.username: Optional[str] = settings.ROBINHOOD_USERNAME
        self.password: Optional[str] = settings.ROBINHOOD_PASSWORD
        self.is_authenticated: bool = False

    def login(self) -> bool:
        """Authenticate with Robinhood. Prompts for SMS MFA in the terminal if needed."""
        if not self.username or not self.password:
            logger.info("Robinhood credentials missing. Skipping Robinhood integration.")
            return False

        try:
            login_result = r.login(self.username, self.password, by_sms=True)
            if login_result and "access_token" in login_result:
                self.is_authenticated = True
                logger.info("Successfully authenticated with Robinhood.")
                return True
            logger.warning("Robinhood authentication failed.")
            return False
        except Exception as exc:  # noqa: BLE001 - login network errors are non-fatal upstream
            logger.error("Robinhood login error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Holdings + dividends (legacy path; unchanged behaviour)
    # ------------------------------------------------------------------

    def fetch_positions(self) -> Dict[str, RobinhoodPositionDTO]:
        """Fetch active holdings and associated historical dividends."""
        if not self.is_authenticated:
            return {}

        try:
            holdings = r.build_holdings() or {}
            dividends_raw = r.get_dividends() or []

            positions_dtos: Dict[str, RobinhoodPositionDTO] = {}
            instrument_urls: Dict[str, str] = {}

            for ticker, data in holdings.items():
                shares = float(data.get("quantity", 0.0) or 0.0)
                avg_cost = float(data.get("average_buy_price", 0.0) or 0.0)
                positions_dtos[ticker] = RobinhoodPositionDTO(
                    ticker=ticker,
                    shares=shares,
                    average_cost=avg_cost,
                    total_dividends=0.0,
                )
                inst_id = data.get("id")
                if inst_id:
                    instrument_urls[inst_id] = ticker

            for d in dividends_raw:
                if d.get("state") not in ("paid", "reinvested"):
                    continue
                inst_field = d.get("instrument") or ""
                try:
                    inst_id = inst_field.rstrip("/").rsplit("/", 1)[-1]
                except Exception:  # noqa: BLE001 - skip malformed records, never abort
                    continue
                if inst_id in instrument_urls:
                    ticker = instrument_urls[inst_id]
                    positions_dtos[ticker].total_dividends += float(
                        d.get("amount", 0.0) or 0.0
                    )

            logger.info(
                "Successfully fetched %d positions from Robinhood.", len(positions_dtos)
            )
            return positions_dtos

        except Exception as exc:  # noqa: BLE001
            logger.error("Error fetching Robinhood positions: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Task 1.4 — Automated ticker discovery
    # ------------------------------------------------------------------

    def list_watchlist_names(self) -> List[str]:
        """Return the names of every Robinhood "Lists" entry on the account.

        Returns an empty list (never raises) when unauthenticated or when the
        robin_stocks call fails — discovery is best-effort and must not crash
        the orchestrator.
        """
        if not self.is_authenticated:
            return []
        try:
            wl_raw = r.get_all_watchlists() or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not list Robinhood watchlists: %s", exc)
            return []

        names: List[str] = []
        # robin_stocks returns {"results": [{"display_name": "...", ...}, ...]}
        # but older fixtures may return a bare list; accept both shapes.
        results = wl_raw.get("results", wl_raw) if isinstance(wl_raw, dict) else wl_raw
        if not isinstance(results, list):
            return []
        for entry in results:
            if not isinstance(entry, dict):
                continue
            name = entry.get("display_name") or entry.get("name")
            if name:
                names.append(str(name).strip())
        return names


def _sanitize_tickers(raw: Iterable[object]) -> List[str]:
    """Uppercase, strip, dedupe, sort an iterable of ticker-like values.

    Non-string entries are silently dropped — the goal is a clean symbol list,
    not a parser.  Order is deterministic (sorted) so callers can diff runs.
    """
    out: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        s = item.strip().upper()
        if s and not s.startswith("#"):
            out.add(s)
    return sorted(out)


def _watchlist_tickers(name: str) -> List[str]:
    """Return the tickers in a single Robinhood watchlist by display name.

    Per-list failures (deleted list, transient network) are logged and yield an
    empty list — they must not propagate to the discovery caller.
    """
    try:
        rows = r.get_watchlist_by_name(name) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read Robinhood watchlist %r: %s", name, exc)
        return []

    syms: List[str] = []
    # robin_stocks shape: list[{"symbol": "AAPL", ...}, ...]; tolerate dict-of-results.
    if isinstance(rows, dict):
        rows = rows.get("results", [])
    if not isinstance(rows, list):
        return []
    for row in rows:
        if isinstance(row, dict) and row.get("symbol"):
            syms.append(str(row["symbol"]))
        elif isinstance(row, str):
            syms.append(row)
    return _sanitize_tickers(syms)


def discover_watchlists(client: RobinhoodClient) -> Dict[str, List[str]]:
    """Return ``{watchlist_name: [tickers]}`` across every Robinhood list.

    Empty mapping is returned when the client is not authenticated; the caller
    can then fall back to local files / DEFAULT_TICKERS.  Per-list failures are
    swallowed (logged) so one bad list cannot break the whole discovery run.
    """
    if not client.is_authenticated:
        return {}

    out: Dict[str, List[str]] = {}
    for name in client.list_watchlist_names():
        out[name] = _watchlist_tickers(name)
    return out


def _file_tickers(path: Path) -> List[str]:
    """Read tickers from a plain-text file (one per line, '#' = comment)."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read watchlist file %s: %s", path, exc)
        return []
    return _sanitize_tickers(lines)


def _watchlist_files_from_env() -> List[Path]:
    """Parse the ``SYNC_WATCHLIST_FILES`` env var into a list of Paths.

    Colon-separated to match shell PATH conventions; whitespace and empty
    components are dropped.  Missing files are not validated here — caller
    decides whether to warn (we want headless CI runs to tolerate absences).
    """
    raw = os.environ.get(_WATCHLIST_FILES_ENV, "").strip()
    if not raw:
        return []
    return [Path(p.strip()).expanduser() for p in raw.split(":") if p.strip()]


def discover_universe(
    client: RobinhoodClient,
    extra_files: Optional[Iterable[Path]] = None,
) -> List[str]:
    """Return a sorted, deduped universe of every symbol the operator tracks.

    Sources combined (union):
      - Robinhood holdings (via ``client.fetch_positions()``) — always included
        when the client is authenticated.
      - Every Robinhood "Lists" entry (via :func:`discover_watchlists`).
      - Plain-text watchlist files: ``extra_files`` argument plus the
        ``SYNC_WATCHLIST_FILES`` env var (colon-separated paths).

    Per-source failures are logged and skipped — the function never raises on
    a discovery error.  An empty list means nothing was discoverable from any
    source (callers can then fall back to ``settings.DEFAULT_TICKERS``).
    """
    universe: set[str] = set()

    # Source A — holdings (the source of truth for owned shares).
    if client.is_authenticated:
        try:
            positions = client.fetch_positions()
            universe.update(positions.keys())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not include holdings in discovery: %s", exc)

    # Source B — every RH watchlist.
    for tickers in discover_watchlists(client).values():
        universe.update(tickers)

    # Source C — file-backed watchlists (CLI/CI convenience).
    file_paths: List[Path] = list(_watchlist_files_from_env())
    if extra_files:
        file_paths.extend(Path(p) for p in extra_files)
    for path in file_paths:
        universe.update(_file_tickers(path))

    return _sanitize_tickers(universe)
