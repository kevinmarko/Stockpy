"""scripts/verify_fmp_profile.py
==============================
NETWORK-DEPENDENT. NOT part of the pytest suite -- this is a manual operator
gate, run by hand, not collected by CI. (It lives outside ``tests/`` and its
filename does not match ``python_files = test_*.py`` in pytest.ini, so it is
excluded from collection twice over, deliberately.)

Purpose
-------
Verify that FMP's ``/profile`` endpoint returns valid company profile data
including a non-empty company description for specified symbols, via the
gated ``data.fmp_client.company_profile`` wrapper.

Usage
-----
    python scripts/verify_fmp_profile.py
    python scripts/verify_fmp_profile.py --symbols AAPL,MSFT,GOOGL

Exit codes
----------
    0  PASS -- every symbol returned a valid profile with a non-empty description.
    1  FAIL -- at least one symbol failed to return a profile or had an
       insufficient/missing description.
    2  Could not run at all -- e.g. FMP_API_KEY is not configured or
       FMP_PROFILE_ENABLED is False.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Repo-root import shim so `python scripts/verify_fmp_profile.py` works from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._bootstrap import bootstrap  # noqa: E402
bootstrap()

from data.fmp_client import company_profile  # noqa: E402
from settings import settings  # noqa: E402

DEFAULT_SYMBOLS = "AAPL,MSFT,GOOGL"
MIN_DESCRIPTION_LENGTH = 20


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--symbols",
        default=DEFAULT_SYMBOLS,
        help=f"Comma-separated symbols (default: {DEFAULT_SYMBOLS}).",
    )
    args = parser.parse_args(argv)

    if not getattr(settings, "FMP_API_KEY", None):
        print(
            "ERROR: FMP_API_KEY is not configured (settings.FMP_API_KEY is empty).\n"
            "This script requires network access AND a live FMP key -- set "
            "FMP_API_KEY in .env and re-run. Refusing to partially run.",
            file=sys.stderr,
        )
        return 2

    if not getattr(settings, "FMP_PROFILE_ENABLED", True):
        print(
            "ERROR: FMP_PROFILE_ENABLED is False (settings.FMP_PROFILE_ENABLED is False).\n"
            "Enable FMP_PROFILE_ENABLED in .env or settings to run this check.",
            file=sys.stderr,
        )
        return 2

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("ERROR: --symbols resolved to an empty list.", file=sys.stderr)
        return 2

    print(f"Verifying FMP company profile for {len(symbols)} symbol(s):\n")

    results: Dict[str, Dict[str, Any]] = {}
    errors: Dict[str, str] = {}

    for symbol in symbols:
        try:
            profile_data = company_profile(symbol)
        except Exception as exc:
            errors[symbol] = f"{type(exc).__name__}: {exc}"
            print(f"  {symbol:6s}  ERROR: {type(exc).__name__}: {exc}")
            continue

        if not profile_data or not isinstance(profile_data, dict):
            errors[symbol] = "no profile record returned"
            print(f"  {symbol:6s}  ERROR: no profile record returned")
            continue

        company_name = profile_data.get("companyName") or profile_data.get("name") or "N/A"
        sector = profile_data.get("sector") or "N/A"
        desc = profile_data.get("description")

        if not desc or not isinstance(desc, str) or len(desc.strip()) < MIN_DESCRIPTION_LENGTH:
            errors[symbol] = f"description missing or too short (< {MIN_DESCRIPTION_LENGTH} chars)"
            print(
                f"  {symbol:6s}  name={company_name[:20]:20s}  sector={sector[:15]:15s}  "
                f"desc_len={len(str(desc or '')):4d}  [ERROR: description missing/too short]"
            )
            continue

        results[symbol] = {
            "name": company_name,
            "sector": sector,
            "desc_len": len(desc.strip()),
        }
        print(
            f"  {symbol:6s}  name={company_name[:20]:20s}  sector={sector[:15]:15s}  "
            f"desc_len={len(desc.strip()):4d}  [OK]"
        )

    print()
    if errors:
        print(
            f"FAIL: {len(errors)}/{len(symbols)} symbol(s) could not be verified: "
            f"{', '.join(sorted(errors))}"
        )
        return 1

    print(f"PASS: all {len(symbols)} symbol(s) successfully verified with genuine company profiles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
