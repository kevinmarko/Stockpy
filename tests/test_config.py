"""
tests/test_config.py
=====================
docs/plans/CONFIG_SCHEMA_PLAN.md Phase C0 — turns the plan's one-time audit of
``config.COLUMN_SCHEMA`` into a regression-tested, machine-checkable
contract, and exercises ``Config.validate_config()`` in CI for the first
time (previously only invoked via ``python config.py``'s ``__main__`` block).

Two classes:

  * ``TestColumnSchemaIntegrity`` — pins COLUMN_SCHEMA's shape: exact entry
    count, no duplicate keys/headers, every entry has all three of
    header/key/format, every format is one of the five strings
    ``database_setup.type_map()`` actually understands.
  * ``TestValidateConfig`` — calls ``Config.validate_config()`` directly
    (happy path + duplicate-key/duplicate-header failure paths), closing the
    "never run outside ``python config.py``'s CLI" gap called out in the plan.

NOTE (2026-09, step 4e): a third class, ``TestAdvisoryColumnCoverage`` (calling
``reporting/sheet_publisher.py::rec_to_sheet_row`` with a synthetic
``Recommendation``/``AccountSnapshot`` and asserting the exact set of
``COLUMN_SCHEMA`` keys the advisory path populated vs. left for the
orchestrator-only path), moved to
``legacy/tests/test_advisory_column_coverage.py`` when the Google Sheet
publisher (and ``rec_to_sheet_row`` with it) was retired to ``legacy/``.

Numeric snapshot (docs/plans/CONFIG_SCHEMA_PLAN.md Phase C1 changed the pre-existing
86/27/8/59 split by fixing the 8-key silent-drop bug in ``rec_to_sheet_row``):
COLUMN_SCHEMA now has 91 entries (86 original + 5 new
"# --- ADVISORY METADATA ---" columns). Of those 91: 33 are populated by the
advisory path (the original 27, plus "Div Yield" which now maps onto an
existing key instead of a wrong one, plus the 4 surviving new ADVISORY
METADATA columns -- Score/Forecast_30_Pct/Advisory_Conviction/
Advisory_Position_Pct/Advisory_Data_Quality is actually 5 new columns, one of
which, Score, was already counted among... see the explicit list below,
which is the actual source of truth this test asserts against, not this
prose summary); 58 are orchestrator-only / not advisory-populated.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

import config
from database_setup import PANDAS_TO_SQLITE_TYPES


# ---------------------------------------------------------------------------
# TestColumnSchemaIntegrity
# ---------------------------------------------------------------------------

class TestColumnSchemaIntegrity:
    """Pins COLUMN_SCHEMA's current shape. If this test breaks because you
    deliberately added/removed/renamed a column, update the pinned numbers
    below IN THE SAME COMMIT as the schema change -- do not "fix" this test
    by loosening the assertion. If it breaks and you did NOT intend to touch
    COLUMN_SCHEMA, that's exactly the drift this test exists to catch."""

    # Update deliberately, in the same commit as any COLUMN_SCHEMA change.
    EXPECTED_COLUMN_COUNT = 108  # 116 -> 108: step 4f trimmed 8 dead options/ETF columns

    def test_exact_column_count(self) -> None:
        assert len(config.COLUMN_SCHEMA) == self.EXPECTED_COLUMN_COUNT, (
            "config.COLUMN_SCHEMA's entry count changed. If this was "
            "deliberate, update TestColumnSchemaIntegrity.EXPECTED_COLUMN_COUNT "
            "(and the derived counts in TestAdvisoryColumnCoverage below) in "
            "the same commit."
        )

    def test_no_duplicate_keys(self) -> None:
        keys = [c["key"] for c in config.COLUMN_SCHEMA]
        dupes = {k for k in keys if keys.count(k) > 1}
        assert not dupes, f"Duplicate COLUMN_SCHEMA keys: {sorted(dupes)}"

    def test_no_duplicate_headers(self) -> None:
        headers = [c["header"] for c in config.COLUMN_SCHEMA]
        dupes = {h for h in headers if headers.count(h) > 1}
        assert not dupes, f"Duplicate COLUMN_SCHEMA headers: {sorted(dupes)}"

    def test_every_entry_has_header_key_format(self) -> None:
        required = {"header", "key", "format"}
        for i, col in enumerate(config.COLUMN_SCHEMA):
            missing = required - set(col.keys())
            assert not missing, f"COLUMN_SCHEMA[{i}] ({col!r}) missing keys: {missing}"

    def test_every_format_is_a_known_type_map_format(self) -> None:
        """Tighter than database_setup.type_map()'s own tolerant behavior
        (an unrecognized format silently degrades to TEXT, per
        test_database_setup.py::test_unknown_format_falls_back_to_text) --
        this test asserts every live COLUMN_SCHEMA entry uses one of the
        formats type_map() actually maps, so a typo'd format string is
        caught here rather than silently degrading in production."""
        known_formats = set(PANDAS_TO_SQLITE_TYPES.keys())
        assert known_formats == {"string", "number", "currency", "currency_large", "percent"}
        for col in config.COLUMN_SCHEMA:
            assert col["format"] in known_formats, (
                f"COLUMN_SCHEMA entry {col!r} has an unrecognized format "
                f"'{col['format']}' -- not one of {sorted(known_formats)}."
            )

    def test_headers_and_keys_are_non_empty_strings(self) -> None:
        for col in config.COLUMN_SCHEMA:
            assert isinstance(col["header"], str) and col["header"].strip()
            assert isinstance(col["key"], str) and col["key"].strip()

    def test_get_internal_keys_is_consistent(self) -> None:
        keys = config.get_internal_keys()
        assert keys == [col["key"] for col in config.COLUMN_SCHEMA]
        # get_headers()/get_rename_mapping() fed only the archived Google
        # Sheet sink and were removed in the 2026-09 schema trim (step 4f).
        assert not hasattr(config, "get_headers")
        assert not hasattr(config, "get_rename_mapping")

    def test_dead_options_and_etf_columns_are_trimmed(self) -> None:
        """Step 4f removed the eight always-NaN/blank columns."""
        keys = set(config.get_internal_keys())
        for gone in (
            "True_IVR", "VRP", "Realized_Vol_Rank", "Option Strategy",
            "ETF_Ownership_Pct", "ETF_Comovement_R2", "ETF_Primary_Wrapper",
            "ETF_Transmission_Multiplier",
        ):
            assert gone not in keys

    def test_dashboard_schema_dynamically_covers_every_column_schema_key(self) -> None:
        """config.DashboardSchema is built dynamically from COLUMN_SCHEMA at
        import time -- confirm every key gets a schema column (this is the
        documented automatic-drift-safety mechanism for *types*)."""
        schema_cols = set(config.DashboardSchema.columns.keys())
        for col in config.COLUMN_SCHEMA:
            assert col["key"] in schema_cols


# ---------------------------------------------------------------------------
# TestValidateConfig
# ---------------------------------------------------------------------------

class TestValidateConfig:
    """Exercises Config.validate_config() directly in CI -- previously only
    ever invoked via ``python config.py``'s __main__ block (grep confirms
    zero other callers), so a duplicate key/header added to COLUMN_SCHEMA
    would silently ship without this test."""

    def test_validate_config_passes_on_real_schema(self) -> None:
        # Must not raise against the actual, live COLUMN_SCHEMA.
        config.Config.validate_config()

    def test_validate_config_raises_on_duplicate_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        broken = [
            {"header": "Ticker", "key": "Symbol", "format": "string"},
            {"header": "Ticker Again", "key": "Symbol", "format": "number"},
        ]
        monkeypatch.setattr(config, "COLUMN_SCHEMA", broken)
        with pytest.raises(ValueError, match="Duplicate keys"):
            config.Config.validate_config()

    def test_validate_config_raises_on_duplicate_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        broken = [
            {"header": "Ticker", "key": "Symbol", "format": "string"},
            {"header": "Ticker", "key": "Symbol2", "format": "number"},
        ]
        monkeypatch.setattr(config, "COLUMN_SCHEMA", broken)
        with pytest.raises(ValueError, match="Duplicate headers"):
            config.Config.validate_config()


# ---------------------------------------------------------------------------
# TestFMPDiagnosticColumns
# ---------------------------------------------------------------------------

class TestFMPDiagnosticColumns:
    """The ten FMP diagnostic-feed COLUMN_SCHEMA entries (analyst, earnings,
    insider, sector snapshot, economics calendar). All ten are populated by
    ``pipeline/production_steps.py::_apply_fmp_*`` behind their own
    ``FMP_*_ENABLED`` gate -- see ``config.COLUMN_SCHEMA``'s "FMP DIAGNOSTIC
    FEEDS" section."""

    EXPECTED: Dict[str, str] = {
        "Analyst_Target_Consensus": "currency",
        "Analyst_Target_Upside": "percent",
        "Analyst_Grade_Score": "number",
        "Days_To_Earnings": "number",
        "Last_EPS_Surprise_Pct": "percent",
        "Insider_Buy_Sell_Ratio": "number",
        "Sector_PE": "number",
        "Sector_1D_Change": "percent",
        "Next_Macro_Event": "string",
        "Next_Macro_Event_Date": "string",
    }

    def test_all_ten_keys_present_with_expected_format(self) -> None:
        by_key = {c["key"]: c for c in config.COLUMN_SCHEMA}
        for key, fmt in self.EXPECTED.items():
            assert key in by_key, f"Expected FMP diagnostic key {key!r} missing from COLUMN_SCHEMA"
            assert by_key[key]["format"] == fmt, (
                f"{key!r} format drifted: expected {fmt!r}, got {by_key[key]['format']!r}"
            )

    def test_headers_are_unique_and_non_empty(self) -> None:
        by_key = {c["key"]: c for c in config.COLUMN_SCHEMA}
        headers = [by_key[k]["header"] for k in self.EXPECTED]
        assert all(isinstance(h, str) and h.strip() for h in headers)
        assert len(set(headers)) == len(headers)

    def test_no_duplicate_earnings_date_column_was_added(self) -> None:
        """The FMP earnings feed becomes a SECOND source for the EXISTING
        news-catalyst ``Earnings_Date`` column rather than duplicating it.
        A second entry would silently break Config.validate_config()'s
        duplicate-key guard, but pin the intent explicitly here too."""
        keys = config.get_internal_keys()
        assert keys.count("Earnings_Date") == 1

    def test_each_new_column_gets_a_dashboard_schema_column_of_the_right_dtype(self) -> None:
        """DashboardSchema is built dynamically from COLUMN_SCHEMA, so all ten
        must be present; the eight numeric-format columns (currency/percent/
        number) must map to a nullable float column, and the two string-
        format columns (the economics-calendar event name/date) must map to
        a nullable str column -- either way, gate-off must validate."""
        schema_cols = config.DashboardSchema.columns
        for key, fmt in self.EXPECTED.items():
            assert key in schema_cols, f"{key!r} missing from config.DashboardSchema"
            assert schema_cols[key].nullable is True, (
                f"{key!r} must be nullable -- every FMP diagnostic column is "
                "NaN whenever its gate is off (CONSTRAINT #4)."
            )
            assert fmt in ("currency", "percent", "number", "string")
            if fmt == "string":
                assert str(schema_cols[key].dtype) == "str"
            else:
                assert str(schema_cols[key].dtype) == "float64"

    def test_nan_filled_frame_validates_against_dashboard_schema(self) -> None:
        """A gate-off cycle emits NaN for all eight; that must be a VALID
        dashboard frame, not a schema violation."""
        import numpy as np
        import pandas as pd

        row = _valid_dashboard_row_for_fmp_test()
        for key in self.EXPECTED:
            row[key] = np.nan
        df = pd.DataFrame([row])
        # Raises SchemaError on failure; a clean return is the assertion.
        config.DashboardSchema.validate(df, lazy=True)


def _valid_dashboard_row_for_fmp_test() -> Dict[str, Any]:
    """Build one schema-conformant row from config.COLUMN_SCHEMA.

    Mirrors tests/test_dashboard_validation.py::_valid_dashboard_row; kept
    local so this file has no cross-test-module import.
    """
    row: Dict[str, Any] = {}
    for col in config.COLUMN_SCHEMA:
        key = col["key"]
        if key == "Symbol":
            row[key] = "AAPL"
        elif col["format"] in ("currency", "currency_large", "percent", "number"):
            row[key] = 1.0
        else:
            row[key] = "x"
    return row
