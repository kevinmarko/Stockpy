"""
scripts/build_ticker_sector_map.py
===================================
One-shot network-enrichment CLI: builds ``forecasting/data/ticker_sectors.csv``
(``symbol,sector`` columns, yfinance-style sector names) by pulling the S&P 500
population from ``universe_engine.get_sp500_constituents(date.today())`` and
each ticker's yfinance ``.info['sector']``.

This is the "real" production population builder. The committed
``forecasting/data/ticker_sectors.csv`` shipped in this repo is a small, honest,
hand-curated SEED/DEMO population (~30 well-known real tickers spanning all 11
sectors of the legacy heuristic) — it is NOT the output of a real run of this
script. Regenerate the real artifact by actually running this script with
network access and yfinance installed:

    python scripts/build_ticker_sector_map.py --output forecasting/data/ticker_sectors.csv

Design notes
------------
* Mirrors this repo's established CLI convention (see
  ``scripts/preflight_check.py``): repo-root ``sys.path`` bootstrap,
  ``argparse``, ``def main(argv=None) -> int``, ``sys.exit(main())``.
* Dead-letter resilient: one bad ticker (network error, missing 'sector' key,
  yfinance raising) is logged and skipped — it never aborts the whole run
  (CLAUDE.md convention: "Loops over tickers ... wrap each ticker in
  try/except so one bad symbol doesn't abort the whole run").
* ``yfinance`` is imported lazily, inside ``fetch_ticker_sector``, not at
  module top — matching this codebase's optional-import convention (see
  ``TENSORFLOW_AVAILABLE``/``PROPHET_AVAILABLE`` in ``forecasting_engine.py``)
  so this module stays importable, and its ``--help``/argparse plumbing stays
  testable, in environments (like this repo's default ``.venv``) that do not
  have yfinance installed. Only ``fetch_ticker_sector`` itself requires
  yfinance to be importable, and only when actually invoked.
* This script makes real network calls (Wikipedia scrape via
  ``universe_engine`` + one yfinance HTTP call per ticker) and is NOT intended
  to be run in this sandboxed environment or exercised by the offline test
  suite -- tests instead monkeypatch ``fetch_ticker_sector`` directly.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("build_ticker_sector_map")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


def fetch_ticker_sector(symbol: str) -> Optional[str]:
    """Return ``yfinance.Ticker(symbol).info.get('sector')``, or ``None``.

    A single, small, testable seam around the one network-touching call in
    this script — callers/tests monkeypatch this function directly rather
    than mocking yfinance internals. Never raises: any exception (network
    error, malformed response, yfinance not installed) is caught and logged,
    degrading to ``None`` so the caller's per-ticker try/except loop treats
    it identically to a genuinely-missing sector.
    """
    try:
        import yfinance as yf  # lazy import -- see module docstring

        info = yf.Ticker(symbol).info or {}
        sector = info.get("sector")
        if isinstance(sector, str) and sector.strip():
            return sector.strip()
        return None
    except Exception as exc:  # dead-letter: one bad ticker must never abort the run
        logger.debug("fetch_ticker_sector(%s) failed: %s", symbol, exc)
        return None


def _get_sp500_universe() -> List[str]:
    """Pull today's S&P 500 constituent list via ``universe_engine``.

    Returns an empty list (never raises) if the Wikipedia scrape fails and no
    local cache exists — the CLI logs this clearly and exits non-zero rather
    than fabricating a ticker population.
    """
    from datetime import date

    from universe_engine import get_sp500_constituents

    return get_sp500_constituents(date.today())


def build_ticker_sector_map(
    tickers: List[str],
    *,
    limit: Optional[int] = None,
    sleep_seconds: float = 0.1,
) -> List[dict]:
    """Enrich ``tickers`` with sectors, one dead-letter-safe row per symbol.

    Skips (does not write) any ticker whose sector could not be determined --
    never writes a row with a blank/missing sector.
    """
    if limit is not None:
        tickers = tickers[:limit]

    rows: List[dict] = []
    total = len(tickers)
    for i, symbol in enumerate(tickers, start=1):
        try:
            sector = fetch_ticker_sector(symbol)
            if sector:
                rows.append({"symbol": symbol, "sector": sector})
                logger.info("[%d/%d] %s -> %s", i, total, symbol, sector)
            else:
                logger.info("[%d/%d] %s -> (no sector found, skipping)", i, total, symbol)
        except Exception as exc:  # belt-and-suspenders -- build_ticker_sector_map itself
            logger.warning("[%d/%d] %s failed unexpectedly: %s", i, total, symbol, exc)
        finally:
            if sleep_seconds > 0 and i < total:
                time.sleep(sleep_seconds)

    return rows


def _write_csv(rows: List[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "sector"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns 0 on success, non-zero on total failure.

    Parameters
    ----------
    argv:
        Argument list. ``None`` uses ``sys.argv[1:]``. Pass an explicit list
        (e.g. ``["--help"]``) to drive this from tests without a subprocess.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Build forecasting/data/ticker_sectors.csv by pulling the S&P 500 "
            "population and each ticker's yfinance sector. Makes real network "
            "calls -- not intended for offline/sandboxed use."
        )
    )
    parser.add_argument(
        "--output",
        default=str(_REPO_ROOT / "forecasting" / "data" / "ticker_sectors.csv"),
        help="Output CSV path (default: forecasting/data/ticker_sectors.csv)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of tickers processed (useful for smoke testing).",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.1,
        help="Polite delay between per-ticker yfinance calls (default: 0.1s).",
    )
    args = parser.parse_args(argv)

    try:
        tickers = _get_sp500_universe()
    except Exception as exc:
        logger.error("Failed to load S&P 500 universe: %s", exc)
        return 1

    if not tickers:
        logger.error("S&P 500 universe came back empty -- nothing to enrich.")
        return 1

    rows = build_ticker_sector_map(tickers, limit=args.limit, sleep_seconds=args.sleep_seconds)

    if not rows:
        logger.error("No ticker produced a valid sector -- refusing to write an empty CSV.")
        return 1

    output_path = Path(args.output)
    _write_csv(rows, output_path)
    logger.info("Wrote %d rows to %s", len(rows), output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
