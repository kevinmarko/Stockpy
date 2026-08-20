"""
scripts/verify_fmp_screener.py
================================
NETWORK-DEPENDENT. NOT part of the pytest suite -- this is a manual operator
gate, run by hand, not collected by CI. (It lives outside ``tests/`` and its
filename does not match ``python_files = test_*.py`` in pytest.ini, so it is
excluded from collection twice over, deliberately -- same convention as
``scripts/verify_fmp_bars.py``, which this script mirrors.)

Purpose
-------
``data/fmp_client.py::search_name``/``search_symbol``/``company_screener``/
``available_sectors``/``available_industries`` and the
``data/fmp_screener.py`` dispatcher built on top of them were live-verified
2026-08 (see ``docs/FMP_INTEGRATION.md`` §9) via an EXTERNAL FMP MCP
connector -- a separate, real FMP account, not this repo's own
``FMP_API_KEY``/tier, and not through this repo's own ``_fmp_get``
throttle/retry/cooldown path. That is a real, disclosed gap: field names and
overall shape are confirmed, but whether the OPERATOR'S OWN key/tier can
actually reach these five endpoints was never checked. This script closes
that gap, run by the operator against their own configured key.

What it checks
---------------
Five calls, each requiring the RESPONSE TO ACTUALLY CONTAIN the field names
``data/fmp_screener.py`` depends on -- an entitlement-denial 200 body, an
empty list, or a renamed field would otherwise look like "it ran" while
silently returning nothing useful downstream:
  1. ``available_sectors()``      -- non-empty list of ``{"sector": str}``.
  2. ``available_industries()``   -- non-empty list of ``{"industry": str}``.
  3. ``search_name("Apple")``     -- at least one row with a ``symbol`` field
                                      containing "AAPL".
  4. ``search_symbol("AAPL")``    -- at least one row with a ``symbol`` field
                                      containing "AAPL" (ticker search may hit
                                      a different endpoint path than name
                                      search -- both are checked independently
                                      since ``search_symbols()`` only falls
                                      back to this one when name search is
                                      empty).
  5. ``company_screener(sector="Technology", marketCapMoreThan=1e11,
     isActivelyTrading=True, limit=5)`` -- non-empty list where every row
     carries ``symbol``/``companyName``/``sector``/``marketCap``, and
     ``sector`` actually reads back ``"Technology"`` (proves the filter
     itself is honored server-side, not just that SOME rows came back).

Usage
-----
    python scripts/verify_fmp_screener.py

Exit codes
----------
    0  PASS -- all five checks succeeded with the expected shape.
    1  FAIL -- at least one check returned nothing, an unexpected shape, or
       raised (a real answer that says "this doesn't work today").
    2  Could not run at all -- e.g. FMP_API_KEY is not configured. Distinct
       from 1 so "the check failed" and "the check never ran" are never
       conflated, per ``scripts/verify_fmp_bars.py``'s identical convention.

Requires network access and a configured ``FMP_API_KEY``. Fails fast with a
clear message (not a stack trace) when the key is absent.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List

# Repo-root import shim so `python scripts/verify_fmp_screener.py` works from
# anywhere -- mirrors scripts/verify_fmp_bars.py's identical shim.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Venv re-exec + .env loading -- must run before any third-party/project
# import below (see scripts/_bootstrap.py's module docstring for why).
from scripts._bootstrap import bootstrap  # noqa: E402
bootstrap()

from data.fmp_client import (  # noqa: E402
    FMPUnavailable,
    available_industries,
    available_sectors,
    company_screener,
    search_name,
    search_symbol,
)
from settings import settings  # noqa: E402


class VerificationError(RuntimeError):
    """Raised for a per-check failure. Caught by main() and reported as a
    named, non-fatal-to-the-run failure for that check -- never a raw
    traceback standing in for a verdict."""


def _check_available_sectors() -> str:
    raw = available_sectors()
    if not isinstance(raw, list) or not raw:
        raise VerificationError(f"expected a non-empty list, got: {raw!r}")
    sectors = [r.get("sector") for r in raw if isinstance(r, dict)]
    if not any(sectors):
        raise VerificationError(f"no row carried a 'sector' field: {raw[:3]!r}")
    return f"{len(sectors)} sectors, e.g. {sectors[:3]}"


def _check_available_industries() -> str:
    raw = available_industries()
    if not isinstance(raw, list) or not raw:
        raise VerificationError(f"expected a non-empty list, got: {raw!r}")
    industries = [r.get("industry") for r in raw if isinstance(r, dict)]
    if not any(industries):
        raise VerificationError(f"no row carried an 'industry' field: {raw[:3]!r}")
    return f"{len(industries)} industries, e.g. {industries[:3]}"


def _check_search_name() -> str:
    raw = search_name("Apple", limit=5)
    if not isinstance(raw, list) or not raw:
        raise VerificationError(f"expected a non-empty list, got: {raw!r}")
    symbols: List[str] = [str(r.get("symbol", "")) for r in raw if isinstance(r, dict)]
    if not any("AAPL" in s.upper() for s in symbols):
        raise VerificationError(
            f"no row's symbol contained 'AAPL' -- symbols returned: {symbols!r}"
        )
    return f"{len(raw)} rows, symbols include {[s for s in symbols if 'AAPL' in s.upper()]}"


def _check_search_symbol() -> str:
    raw = search_symbol("AAPL", limit=5)
    if not isinstance(raw, list) or not raw:
        raise VerificationError(f"expected a non-empty list, got: {raw!r}")
    symbols: List[str] = [str(r.get("symbol", "")) for r in raw if isinstance(r, dict)]
    if not any("AAPL" in s.upper() for s in symbols):
        raise VerificationError(
            f"no row's symbol contained 'AAPL' -- symbols returned: {symbols!r}"
        )
    return f"{len(raw)} rows, symbols include {[s for s in symbols if 'AAPL' in s.upper()]}"


def _check_company_screener() -> str:
    raw = company_screener(
        sector="Technology", marketCapMoreThan=1e11, isActivelyTrading=True, limit=5,
    )
    if not isinstance(raw, list) or not raw:
        raise VerificationError(f"expected a non-empty list, got: {raw!r}")
    missing_fields = []
    wrong_sector = []
    for row in raw:
        if not isinstance(row, dict):
            missing_fields.append(row)
            continue
        for field in ("symbol", "companyName", "sector", "marketCap"):
            if field not in row:
                missing_fields.append((row.get("symbol", "?"), field))
        if row.get("sector") != "Technology":
            wrong_sector.append((row.get("symbol", "?"), row.get("sector")))
    if missing_fields:
        raise VerificationError(f"row(s) missing expected field(s): {missing_fields!r}")
    if wrong_sector:
        raise VerificationError(
            f"sector='Technology' filter was NOT honored server-side -- "
            f"got rows with a different sector: {wrong_sector!r}"
        )
    symbols = [r.get("symbol") for r in raw]
    return f"{len(raw)} Technology rows (mkt cap > $100B), symbols: {symbols}"


CHECKS = [
    ("available_sectors", _check_available_sectors),
    ("available_industries", _check_available_industries),
    ("search_name('Apple')", _check_search_name),
    ("search_symbol('AAPL')", _check_search_symbol),
    ("company_screener(sector='Technology', ...)", _check_company_screener),
]


def main() -> int:
    if not settings.FMP_API_KEY:
        print(
            "ERROR: FMP_API_KEY is not configured (settings.FMP_API_KEY is empty).\n"
            "This script requires network access AND a live FMP key -- set "
            "FMP_API_KEY in .env and re-run. Refusing to partially run.",
            file=sys.stderr,
        )
        return 2

    print("Verifying the Symbol Search & Sector/Industry Screener feed against "
          "your own FMP_API_KEY/tier (data/fmp_client.py, real network calls):\n")

    failures = 0
    for label, check_fn in CHECKS:
        try:
            detail = check_fn()
        except FMPUnavailable as exc:
            print(f"  {label:45s}  FAIL: FMP request failed: {exc}")
            failures += 1
            continue
        except VerificationError as exc:
            print(f"  {label:45s}  FAIL: {exc}")
            failures += 1
            continue
        except Exception as exc:  # never let a raw traceback stand in for a verdict
            print(f"  {label:45s}  FAIL: {type(exc).__name__}: {exc}")
            failures += 1
            continue
        print(f"  {label:45s}  OK: {detail}")

    print()
    if failures:
        print(f"FAIL: {failures}/{len(CHECKS)} check(s) failed against your own "
              f"FMP_API_KEY/tier -- do not trust FMP_SCREENER_ENABLED=True as a "
              f"working path until these pass.")
        return 1

    print(f"PASS: all {len(CHECKS)} checks succeeded against your own FMP_API_KEY/tier.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
