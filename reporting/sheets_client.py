"""Centralized Google Sheets client and Sheet constants.

Single source of truth for gspread service-account access. Previously the auth
pattern (``gspread.service_account(filename=CREDENTIALS_FILE)``) was duplicated
inside ``main._write_to_sheet`` and ``main._load_tickers_from_sheet2``; both now
route through :func:`get_service_account_client` here.

``gspread`` is imported lazily inside the helper so importing this module never
requires the optional dependency, and any auth error degrades to ``None``
(logged) rather than propagating — the Sheet is a best-effort sink.
"""

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants (moved verbatim from main.py)
# ---------------------------------------------------------------------------
CREDENTIALS_FILE = "credentials.json"   # Google Sheets service-account key
SHEET_NAME = "Stock Dashboard Py"
TAB_NAME_OUTPUT = "FidelityData_Automated"


def get_service_account_client():
    """Return a gspread service-account client, or None when credentials.json is absent.

    Centralizes the auth pattern previously duplicated inside main._write_to_sheet
    and main._load_tickers_from_sheet2. gspread is imported lazily so importing this
    module never requires the dependency. Any auth error degrades to None (logged),
    never raises — the Sheet is best-effort.
    """
    import os

    if not os.path.exists(CREDENTIALS_FILE):
        logger.info("Sheet client skipped — %s not found.", CREDENTIALS_FILE)
        return None

    try:
        import gspread  # type: ignore[import]

        return gspread.service_account(filename=CREDENTIALS_FILE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not create Google Sheets client: %s", exc)
        return None
