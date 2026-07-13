"""
tests/test_config.py
=====================
docs/CONFIG_SCHEMA_PLAN.md Phase C0 — turns the plan's one-time audit of
``config.COLUMN_SCHEMA`` into a regression-tested, machine-checkable
contract, and exercises ``Config.validate_config()`` in CI for the first
time (previously only invoked via ``python config.py``'s ``__main__`` block).

Three classes:

  * ``TestColumnSchemaIntegrity`` — pins COLUMN_SCHEMA's shape: exact entry
    count, no duplicate keys/headers, every entry has all three of
    header/key/format, every format is one of the five strings
    ``database_setup.type_map()`` actually understands.
  * ``TestValidateConfig`` — calls ``Config.validate_config()`` directly
    (happy path + duplicate-key/duplicate-header failure paths), closing the
    "never run outside ``python config.py``'s CLI" gap called out in the plan.
  * ``TestAdvisoryColumnCoverage`` — calls
    ``reporting/sheet_publisher.py::rec_to_sheet_row`` with a synthetic
    ``Recommendation``/``AccountSnapshot`` and asserts the exact set of
    ``COLUMN_SCHEMA`` keys the advisory path populates vs. leaves for the
    orchestrator-only path, so a future rename/drop of either function's
    keys breaks this test intentionally rather than silently drifting.

Numeric snapshot (docs/CONFIG_SCHEMA_PLAN.md Phase C1 changed the pre-existing
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

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

import config
from database_setup import PANDAS_TO_SQLITE_TYPES
from engine.advisory import Recommendation
from reporting.sheet_publisher import rec_to_sheet_row


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
    EXPECTED_COLUMN_COUNT = 91

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

    def test_get_headers_get_internal_keys_get_rename_mapping_are_consistent(self) -> None:
        headers = config.get_headers()
        keys = config.get_internal_keys()
        rename = config.get_rename_mapping()
        assert len(headers) == len(config.COLUMN_SCHEMA)
        assert len(keys) == len(config.COLUMN_SCHEMA)
        assert len(rename) == len(config.COLUMN_SCHEMA)
        for col in config.COLUMN_SCHEMA:
            assert rename[col["key"]] == col["header"]

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
# TestAdvisoryColumnCoverage
# ---------------------------------------------------------------------------

def _make_position() -> MagicMock:
    pos = MagicMock()
    pos.quantity = 10.0
    pos.average_cost = 100.0
    pos.dividends_received = 5.0
    return pos


def _make_snapshot() -> MagicMock:
    snap = MagicMock()
    snap.positions = {"AAPL": _make_position()}
    return snap


def _make_recommendation() -> Recommendation:
    return Recommendation(
        symbol="AAPL",
        action="BUY",
        strategy="test_strategy",
        conviction=0.72,
        rationale="AAPL: strong momentum and healthy dividend coverage.",
        suggested_position_pct=0.03,
        forecast=210.0,
        key_indicators={
            "score": 61.5,
            "rsi": 58.0,
            "rsi_2": 22.0,
            "macd_line": 0.8,
            "atr": 2.1,
            "aroon_osc": 35.0,
            "sortino": 1.4,
            "max_drawdown": -0.11,
            "rs_vs_spy": 0.06,
            "garch_vol": 0.21,
            "forecast_30d_pct": 0.045,
            "dividend_yield": 0.0065,
            "kelly_raw": 0.055,
        },
        data_quality="OK",
        buy_range="Buy Zone: $195.00 - $200.00",
        sell_range="Sell Zone: $215.00 - $225.00 | Stop @ $190.00",
    )


class TestAdvisoryColumnCoverage:
    """Living, breakable contract for which COLUMN_SCHEMA keys the advisory
    path (main.py via reporting/sheet_publisher.py::rec_to_sheet_row)
    actually populates vs. leaves for the orchestrator-only path
    (main_orchestrator.py). See docs/CONFIG_SCHEMA_PLAN.md sections (c)/(e)
    for the full audit this pins.

    Phase C1 fixed the original 8-key silent-drop bug in rec_to_sheet_row:
      - "Div Yield" now maps onto its correct existing COLUMN_SCHEMA key
        (previously mis-keyed "Dividend Yield", matching neither key nor
        header).
      - "Score", "Forecast_30_Pct", "Advisory_Conviction",
        "Advisory_Position_Pct", "Advisory_Data_Quality" are new
        "# --- ADVISORY METADATA ---" COLUMN_SCHEMA entries.
      - "Advisory_Action" and "Advisory_Rationale" were removed from
        rec_to_sheet_row entirely (confirmed genuine duplicates of
        "Action Signal" and "Advice"/"Strategy Explainer Notes" -- see the
        PR description for the full case-by-case reasoning) -- they are not
        expected to appear anywhere below.
    """

    # The complete, exact set of COLUMN_SCHEMA *keys* that
    # rec_to_sheet_row()'s output dict maps onto after Phase C1. If this
    # changes because you deliberately fixed/wired another column, update
    # this set (and KNOWN_UNMAPPED_ORCHESTRATOR_ONLY_COLUMNS below, since
    # they are complements within COLUMN_SCHEMA) in the same commit.
    KNOWN_ADVISORY_MAPPED_KEYS = frozenset({
        "Symbol", "Price",
        "Action Signal", "Advice", "Actionable Advice Signal", "Score",
        "Kelly Target", "Edge Ratio",
        "RSI", "RSI_2", "MACD_Line", "ATR", "Aroon Oscillator",
        "Sortino Ratio", "Max Drawdown", "RS vs SPY", "GARCH_Vol",
        "Forecast_30", "Forecast_30_Pct",
        "Div Yield",
        "buyRange", "sellRange", "Option Strategy",
        "Robinhood Shares", "Robinhood Avg Cost", "Robinhood Dividends",
        "Robinhood Advice",
        "Advisory_Conviction", "Advisory_Position_Pct", "Advisory_Data_Quality",
        "Strategy Explainer Notes", "Macro Status", "HMM_Risk_On_Probability",
    })

    # The complement: every other COLUMN_SCHEMA key, which the advisory path
    # leaves blank ("") and only main_orchestrator.py's full pipeline can
    # populate (per CLAUDE.md: "use it for production runs that need all
    # 50+ dashboard columns populated"). Computed once at import time below
    # and asserted to equal (COLUMN_SCHEMA keys - KNOWN_ADVISORY_MAPPED_KEYS)
    # so the two sets can never silently drift apart from each other.
    KNOWN_UNMAPPED_ORCHESTRATOR_ONLY_COLUMNS = frozenset({
        "sector", "shortName", "Market Cap",
        "Target_Days", "ARIMA", "MC_Target", "MC_Lower", "MC_Upper",
        "Quality Score", "Graham Num", "Gordon Fair Value", "P/E", "Book Value",
        "DPS", "Institutional Velocity", "DPH", "Leverage Distress Factor",
        "Volume", "MACD_Signal", "SMA_5", "SMA_50", "SMA_200",
        "Aroon Up", "Aroon Down", "Coppock Curve", "Chandelier Exit",
        "RS-MACD", "Realized_Vol_Rank", "True_IVR", "VRP", "Options IV Edge",
        "ROC_12M", "ROC_6M", "Momentum_Vol_Scaled",
        "VaR 95", "Beta", "CoVaR Proxy", "Realized Slippage",
        "Forecast_10", "Forecast_60", "Forecast_90",
        "Forecast_30_Prophet_Lower", "Forecast_30_Prophet_Upper",
        "MFE", "MAE", "BF_Allocation", "BF_Selection", "Portfolio_Heat",
        "XSec_12_1M", "XSec_Momentum_Rank",
        "Value_Z", "Quality_Z", "LowVol_Z", "Size_Z", "Multifactor_Composite",
        "News_Sentiment", "Earnings_Date", "Correlation_Cluster",
    })

    def test_mapped_and_unmapped_sets_are_exact_complements_of_column_schema(self) -> None:
        """The two pinned sets above must partition COLUMN_SCHEMA's keys
        exactly -- no overlap, no gaps. This is the "living contract" the
        plan calls for: any COLUMN_SCHEMA edit that isn't reflected in one
        of the two sets above fails here."""
        all_keys = set(config.get_internal_keys())
        mapped = self.KNOWN_ADVISORY_MAPPED_KEYS
        unmapped = self.KNOWN_UNMAPPED_ORCHESTRATOR_ONLY_COLUMNS

        assert mapped & unmapped == set(), "mapped/unmapped sets must be disjoint"
        assert mapped | unmapped == all_keys, (
            "mapped ∪ unmapped must equal every COLUMN_SCHEMA key. "
            f"Keys in COLUMN_SCHEMA but in neither set: {all_keys - (mapped | unmapped)}; "
            f"keys in one of the sets but no longer in COLUMN_SCHEMA: {(mapped | unmapped) - all_keys}"
        )
        assert len(mapped) == 33
        assert len(unmapped) == 58
        assert len(mapped) + len(unmapped) == len(config.COLUMN_SCHEMA) == 91

    def test_rec_to_sheet_row_emits_exactly_the_known_mapped_keys(self) -> None:
        """AST/behavioral cross-check: call rec_to_sheet_row() for real and
        assert its output dict's keys are EXACTLY KNOWN_ADVISORY_MAPPED_KEYS
        -- no more (an un-pinned new field silently added), no fewer (a
        pinned field silently removed or renamed without updating this
        test)."""
        rec = _make_recommendation()
        snapshot = _make_snapshot()
        row = rec_to_sheet_row(rec, snapshot, price=207.50)

        assert set(row.keys()) == self.KNOWN_ADVISORY_MAPPED_KEYS

    def test_rec_to_sheet_row_keys_all_resolve_to_real_column_schema_keys(self) -> None:
        """Every key rec_to_sheet_row() emits must be a real COLUMN_SCHEMA
        key today (not just at the time this test was written) -- this is
        exactly the check that would have caught the original 8-key silent
        drop: a key that matches neither a COLUMN_SCHEMA key nor header is
        dropped by write_recommendations()'s column filter with zero
        warning."""
        rec = _make_recommendation()
        snapshot = _make_snapshot()
        row = rec_to_sheet_row(rec, snapshot, price=207.50)

        schema_keys = set(config.get_internal_keys())
        unmapped = set(row.keys()) - schema_keys
        assert not unmapped, (
            f"rec_to_sheet_row() emits keys with no COLUMN_SCHEMA slot -- "
            f"these are SILENTLY DROPPED before reaching the Sheet: {sorted(unmapped)}"
        )

    def test_previously_dropped_fields_now_survive_rename_and_filter(self) -> None:
        """End-to-end reproduction of write_recommendations()'s rename +
        filter steps (reporting/sheet_publisher.py:~180-188) confirming the
        5 originally-dropped-and-now-fixed fields land in the final,
        header-keyed row -- not just that rec_to_sheet_row()'s raw dict
        contains them."""
        rec = _make_recommendation()
        snapshot = _make_snapshot()
        row = rec_to_sheet_row(rec, snapshot, price=207.50)

        rename_map = config.get_rename_mapping()
        final_headers = config.get_headers()
        renamed = {rename_map.get(k, k): v for k, v in row.items()}
        final_row = {h: renamed[h] for h in final_headers if h in renamed}

        previously_dropped_now_fixed = {
            "Div Yield": rec.key_indicators["dividend_yield"],
            "Advisory Score": rec.key_indicators["score"],
            "Forecast 30D % Change": rec.key_indicators["forecast_30d_pct"],
            "Advisory Conviction": rec.conviction,
            "Advisory Position %": rec.suggested_position_pct,
            "Advisory Data Quality": rec.data_quality,
        }
        for header, expected_value in previously_dropped_now_fixed.items():
            assert header in final_row, (
                f"{header!r} did not survive rename+filter -- still being "
                f"silently dropped."
            )
            if isinstance(expected_value, float):
                assert final_row[header] == pytest.approx(expected_value, rel=1e-3)
            else:
                assert final_row[header] == expected_value

    def test_removed_duplicate_fields_do_not_appear(self) -> None:
        """Advisory_Action and Advisory_Rationale were confirmed genuine
        duplicates (of Action Signal, and of Advice/Strategy Explainer
        Notes respectively) and removed from rec_to_sheet_row entirely --
        pin that they no longer appear in its output."""
        rec = _make_recommendation()
        snapshot = _make_snapshot()
        row = rec_to_sheet_row(rec, snapshot, price=207.50)

        assert "Advisory_Action" not in row
        assert "Advisory_Rationale" not in row

    def test_advisory_metadata_section_keys_present_in_column_schema(self) -> None:
        """The 5 new ADVISORY METADATA COLUMN_SCHEMA entries exist with the
        expected key/format."""
        by_key = {c["key"]: c for c in config.COLUMN_SCHEMA}
        expected = {
            "Score": "number",
            "Forecast_30_Pct": "percent",
            "Advisory_Conviction": "percent",
            "Advisory_Position_Pct": "percent",
            "Advisory_Data_Quality": "string",
        }
        for key, fmt in expected.items():
            assert key in by_key, f"Expected new ADVISORY METADATA key {key!r} missing from COLUMN_SCHEMA"
            assert by_key[key]["format"] == fmt
