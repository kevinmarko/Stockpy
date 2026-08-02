"""
scripts/verify_fmp_bars.py
===========================
NETWORK-DEPENDENT. NOT part of the pytest suite -- this is a manual operator
gate, run by hand, not collected by CI. (It lives outside ``tests/`` and its
filename does not match ``python_files = test_*.py`` in pytest.ini, so it is
excluded from collection twice over, deliberately.)

Purpose
-------
FMP's ``/historical-price-eod/full`` looks like the obvious bars source and it
is WRONG. The incumbent ``data/market_data.py::YFinanceProvider`` fetches bars
via ``Ticker.history(..., auto_adjust=True)`` -- split AND dividend adjusted.
FMP's ``light`` and ``full`` EOD variants are SPLIT-ONLY. The plan's
recommendation, and ``settings.FMP_BARS_ADJUSTMENT``'s default, is
``dividend-adjusted``. This script proves -- or disproves -- that FMP's
``dividend-adjusted`` variant actually matches yfinance's ``auto_adjust=True``
convention closely enough to be a safe swap, by pulling the same trailing
window for the same symbols from both sources and diffing the closes.

A silent adjustment mismatch corrupts every return series, indicator, GARCH
fit, backtest and stored ``price_bars`` row it touches, and it does so
PLAUSIBLY -- nothing fails loudly, the numbers just quietly stop meaning what
they did. This script is the one thing standing between "looks like a safe
default" and "corrupted every downstream calculation." Per the plan: it must
exist and PASS before wave 2 (quotes/bars) is allowed to set
``FMP_BARS_ADJUSTMENT`` to anything other than its default, or before
``FMP_BARS_ENABLED`` is ever flipped on.

Field-shape note (verification honesty)
----------------------------------------
``data/fmp_client.py::historical_eod`` returns FMP's raw parsed JSON --
this script does the field extraction itself. The price-field NAME differs
by variant (live-probed by agent F5 via the FMP MCP connector during this
integration's wave-1 documentation pass, the same probe method the plan's
own "Verified account facts" section used -- NOT independently re-verified by
whoever runs this script later):
    - ``dividend-adjusted`` / ``non-split-adjusted``: ``adjClose``
    - ``full``: ``close``
    - ``light``: ``price`` (no OHLC breakdown at all, close-only)
``_extract_close`` below tries all three field names so the script keeps
working (or fails with a clear, named error) regardless of ``--variant``.

Usage
-----
    python scripts/verify_fmp_bars.py
    python scripts/verify_fmp_bars.py --symbols KO,JNJ,AAPL --years 2
    python scripts/verify_fmp_bars.py --symbols MSFT --variant full   # expected to FAIL

``--variant`` defaults to whatever ``settings.FMP_BARS_ADJUSTMENT`` is
currently set to (itself defaulting to ``"dividend-adjusted"``) rather than a
hardcoded literal, so running this script always re-validates whatever an
operator has actually configured -- if someone sets
``FMP_BARS_ADJUSTMENT=full`` believing it to be correct, this script is what
catches it.

Exit codes
----------
    0  PASS -- every symbol's max relative close diff is below the threshold.
    1  FAIL -- at least one symbol exceeded the threshold, could not be
       fetched from one of the two sources, or had zero overlapping trading
       dates to compare (an inconclusive run is never reported as a pass).
    2  Could not run at all -- e.g. FMP_API_KEY is not configured. Distinct
       from 1 so "the check failed" and "the check never ran" are never
       conflated in a script or CI log.

Requires network access and a configured ``FMP_API_KEY``. Fails fast with a
clear message (not a stack trace) when the key is absent, per the plan's
"fail fast ... rather than partially running" instruction.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

# Repo-root import shim so `python scripts/verify_fmp_bars.py` works from
# anywhere -- mirrors scripts/backfill_sentiment_history.py /
# scripts/backfill_edgar_fundamentals.py's identical shim.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Venv re-exec + .env loading -- must run before any third-party/project
# import below (see scripts/_bootstrap.py's module docstring for why).
from scripts._bootstrap import bootstrap  # noqa: E402
bootstrap()

from data.fmp_client import FMPUnavailable, historical_eod  # noqa: E402
from settings import settings  # noqa: E402

DEFAULT_SYMBOLS = "KO,JNJ,AAPL"
DEFAULT_YEARS = 2

# The gate. < 1e-4 max relative close diff -> conventions match. This is the
# plan's own number (see "Bars -- the biggest silent-corruption risk"), not a
# derived statistical threshold -- it is a deliberately tight tolerance for a
# change with this much silent-corruption blast radius.
THRESHOLD = 1e-4

# Price-field names to try, most-specific first, per variant's live-probed
# shape (see module docstring). Tried in this fixed order for every variant
# since it's cheap and correct even for variants not explicitly probed.
_PRICE_FIELD_CANDIDATES = ("adjClose", "close", "price")


class VerificationError(RuntimeError):
    """Raised for a per-symbol fetch/parse failure. Caught by main() and
    reported as a named, non-fatal-to-the-run failure for that symbol --
    never a raw traceback."""


def _extract_close(row: dict) -> float:
    for field in _PRICE_FIELD_CANDIDATES:
        if field in row and row[field] is not None:
            return float(row[field])
    raise VerificationError(
        f"FMP row has none of {_PRICE_FIELD_CANDIDATES} -- keys present: "
        f"{sorted(row.keys())}. FMP's response shape may have changed; "
        "update _PRICE_FIELD_CANDIDATES."
    )


def _fetch_yfinance_close(symbol: str, start: date, end: date) -> pd.Series:
    """Trailing-window daily closes via yfinance, matching
    ``data/market_data.py::YFinanceProvider.get_intraday_bars``'s exact call
    convention: ``auto_adjust=True`` (split AND dividend adjusted).
    """
    import yfinance as yf  # type: ignore

    df = yf.Ticker(symbol).history(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),  # yfinance end is exclusive
        auto_adjust=True,
    )
    if df is None or df.empty:
        raise VerificationError(f"yfinance returned no bars for {symbol!r}")
    if "Close" not in df.columns:
        raise VerificationError(
            f"yfinance bars for {symbol!r} have no 'Close' column: {list(df.columns)}"
        )
    close = df["Close"].astype(float)
    if close.index.tz is not None:
        close.index = close.index.tz_localize(None)
    close.index = pd.to_datetime(close.index).normalize()
    return close.sort_index()


def _fetch_fmp_close(symbol: str, start: date, end: date, variant: str) -> pd.Series:
    """Trailing-window daily closes via FMP's ``/historical-price-eod/{variant}``."""
    try:
        raw = historical_eod(
            symbol,
            variant=variant,
            from_date=start.isoformat(),
            to_date=end.isoformat(),
        )
    except FMPUnavailable as exc:
        raise VerificationError(f"FMP request failed for {symbol!r}: {exc}") from exc
    except ValueError as exc:
        # historical_eod validates `variant` before any network call.
        raise VerificationError(str(exc)) from exc

    if not isinstance(raw, list) or not raw:
        raise VerificationError(
            f"FMP returned no bars for {symbol!r} (variant={variant!r}): {raw!r}"
        )

    dates, closes = [], []
    for row in raw:
        if not isinstance(row, dict) or "date" not in row:
            raise VerificationError(
                f"Unexpected FMP row shape for {symbol!r}: {row!r}"
            )
        dates.append(pd.to_datetime(row["date"]).normalize())
        closes.append(_extract_close(row))
    series = pd.Series(closes, index=pd.DatetimeIndex(dates), dtype=float)
    return series.sort_index()


def _compare(yf_close: pd.Series, fmp_close: pd.Series) -> Optional[Dict[str, float]]:
    """Inner-join on shared dates and compute abs relative diff. ``None`` when
    there are zero overlapping trading dates -- an inconclusive comparison,
    never silently treated as a pass."""
    combined = pd.concat(
        [yf_close.rename("yf"), fmp_close.rename("fmp")], axis=1, join="inner"
    ).dropna()
    if combined.empty:
        return None
    rel_diff = (combined["fmp"] - combined["yf"]).abs() / combined["yf"].abs()
    return {
        "n": int(len(combined)),
        "max": float(rel_diff.max()),
        "mean": float(rel_diff.mean()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--symbols", default=DEFAULT_SYMBOLS,
        help=f"Comma-separated symbols (default: {DEFAULT_SYMBOLS}).",
    )
    parser.add_argument(
        "--years", type=int, default=DEFAULT_YEARS,
        help=f"Trailing years of daily bars to compare (default: {DEFAULT_YEARS}).",
    )
    parser.add_argument(
        "--variant", default=None,
        help=(
            "FMP EOD variant to test. Defaults to the CURRENTLY CONFIGURED "
            "settings.FMP_BARS_ADJUSTMENT (itself 'dividend-adjusted' by "
            "default), so a plain run always re-validates whatever is actually "
            "set, not just the recommended default."
        ),
    )
    args = parser.parse_args()

    if not settings.FMP_API_KEY:
        print(
            "ERROR: FMP_API_KEY is not configured (settings.FMP_API_KEY is empty).\n"
            "This script requires network access AND a live FMP key -- set "
            "FMP_API_KEY in .env and re-run. Refusing to partially run.",
            file=sys.stderr,
        )
        return 2

    variant = args.variant or settings.FMP_BARS_ADJUSTMENT
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("ERROR: --symbols resolved to an empty list.", file=sys.stderr)
        return 2

    end = date.today()
    start = end - timedelta(days=round(365.25 * args.years))

    print(f"FMP EOD variant under test: {variant!r}")
    print(
        "Reminder: FMP's 'light' and 'full' EOD variants are SPLIT-ONLY (no "
        "dividend adjustment) and are EXPECTED TO FAIL this exact check against "
        "yfinance's auto_adjust=True (split+dividend) convention -- only "
        "'dividend-adjusted' is expected to pass.\n"
    )
    print(f"Comparing {len(symbols)} symbol(s) over {start.isoformat()}..{end.isoformat()}:\n")

    results: Dict[str, Optional[Dict[str, float]]] = {}
    errors: Dict[str, str] = {}

    for symbol in symbols:
        try:
            yf_close = _fetch_yfinance_close(symbol, start, end)
            fmp_close = _fetch_fmp_close(symbol, start, end, variant)
        except VerificationError as exc:
            errors[symbol] = str(exc)
            print(f"  {symbol:6s}  ERROR: {exc}")
            continue
        except Exception as exc:  # never let a raw traceback stand in for a verdict
            errors[symbol] = f"{type(exc).__name__}: {exc}"
            print(f"  {symbol:6s}  ERROR: {type(exc).__name__}: {exc}")
            continue

        cmp = _compare(yf_close, fmp_close)
        results[symbol] = cmp
        if cmp is None:
            errors[symbol] = "zero overlapping trading dates between yfinance and FMP"
            print(f"  {symbol:6s}  ERROR: zero overlapping trading dates")
            continue
        status = "OK" if cmp["max"] < THRESHOLD else "MISMATCH"
        print(
            f"  {symbol:6s}  n_dates={cmp['n']:4d}  "
            f"max_rel_diff={cmp['max']:.2e}  mean_rel_diff={cmp['mean']:.2e}  [{status}]"
        )

    print()
    if errors:
        print(f"FAIL: {len(errors)}/{len(symbols)} symbol(s) could not be verified: "
              f"{', '.join(sorted(errors))}")
        print("FAIL: adjustment conventions do NOT match — do not enable FMP_BARS_ENABLED")
        return 1

    worst = max(cmp["max"] for cmp in results.values() if cmp is not None)
    if worst < THRESHOLD:
        print(f"Worst max relative close diff across all symbols: {worst:.2e} (threshold {THRESHOLD:.0e})")
        print("PASS: adjustment conventions match")
        return 0

    print(f"Worst max relative close diff across all symbols: {worst:.2e} (threshold {THRESHOLD:.0e})")
    print("FAIL: adjustment conventions do NOT match — do not enable FMP_BARS_ENABLED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
