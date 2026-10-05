"""
legacy/tests/test_advisory_column_coverage.py
==============================================
ARCHIVED (2026-09, step 4e). Not collected by pytest (testpaths = tests in
pytest.ini) — kept so it can be restored if needed. See legacy/README.md.

Split out of tests/test_config.py's ``TestAdvisoryColumnCoverage`` when the
Google Sheet publisher (``reporting/sheet_publisher.py``, with its
``rec_to_sheet_row`` row builder) moved to ``legacy/reporting/``.

Living, breakable contract for which COLUMN_SCHEMA keys the (now archived)
advisory-path Sheets row builder, ``legacy/reporting/sheet_publisher.py::
rec_to_sheet_row``, populated vs. left for the orchestrator-only path
(main_orchestrator.py). See docs/plans/CONFIG_SCHEMA_PLAN.md sections (c)/(e)
for the full audit this originally pinned.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import config
from engine.advisory import Recommendation
from legacy.reporting.sheet_publisher import rec_to_sheet_row


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
    path (main.py via legacy/reporting/sheet_publisher.py::rec_to_sheet_row)
    actually populated vs. left for the orchestrator-only path
    (main_orchestrator.py). See docs/plans/CONFIG_SCHEMA_PLAN.md sections (c)/(e)
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
      - "Sizing_Was_Capped"/"Sizing_Binding_Constraint" (sizing/position_sizer.py
        guardrail telemetry) map onto rec.sizing_was_capped/
        .sizing_binding_constraint -- advisory's OWN sizing decision, not
        kelly_raw's (see rec_to_sheet_row's inline comment).
    """

    # The complete, exact set of COLUMN_SCHEMA *keys* that
    # rec_to_sheet_row()'s output dict maps onto after Phase C1. If this
    # changes because you deliberately fixed/wired another column, update
    # this set (and KNOWN_UNMAPPED_ORCHESTRATOR_ONLY_COLUMNS below, since
    # they are complements within COLUMN_SCHEMA) in the same commit.
    KNOWN_ADVISORY_MAPPED_KEYS = frozenset({
        "Symbol", "Price",
        "Action Signal", "Advice", "Actionable Advice Signal", "Score",
        "Kelly Target", "Sizing_Was_Capped", "Sizing_Binding_Constraint", "Edge Ratio",
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
    #
    # NOTE (2026-09, step 4e): this set is pinned against COLUMN_SCHEMA as it
    # stood when this test still ran in CI. It is not kept in sync with later
    # schema trims (e.g. step 4f) since this file is archived and no longer
    # collected -- restoring this test would need re-auditing both sets
    # against the then-current COLUMN_SCHEMA first.
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
        "MFE", "MAE", "BF_Allocation", "BF_Selection", "BF_Interaction", "Portfolio_Heat",
        "XSec_12_1M", "XSec_Momentum_Rank",
        "Value_Z", "Quality_Z", "LowVol_Z", "Size_Z", "Multifactor_Composite",
        "News_Sentiment", "Earnings_Date", "Correlation_Cluster",
        "Credibility_Weighted_Sentiment", "Bot_Activity_Ratio",
        "Aggregated_Source_Credibility",
        "Sector_Heat_Factor", "Attention_Score",
        "ETF_Ownership_Pct", "ETF_Comovement_R2", "ETF_Primary_Wrapper",
        # Orchestrator-only: composed in sizing/position_sizer.py::size_position(),
        # which engine/advisory.py deliberately does not route through (it keeps
        # its own tighter, decoupled CONFIG["max_single_position_pct"] cap).
        "ETF_Transmission_Multiplier",
        # FMP diagnostic feeds -- orchestrator-only by construction: every one
        # is written by a pipeline/production_steps.py::_apply_fmp_* helper
        # inside StrategyEvalStep, and the advisory path (main.py ->
        # engine/advisory.py -> rec_to_sheet_row) never runs those steps, so it
        # has no source for any of them.
        "Analyst_Target_Consensus", "Analyst_Target_Upside", "Analyst_Grade_Score",
        "Days_To_Earnings", "Last_EPS_Surprise_Pct",
        "Insider_Buy_Sell_Ratio",
        "Sector_PE", "Sector_1D_Change",
        "Next_Macro_Event", "Next_Macro_Event_Date",
        # Symbol-rating subsystem diagnostics (rating/symbol_rating_store.py)
        # -- orchestrator-only by construction: no dashboard_df write path
        # for these two exists on the advisory side, and rec_to_sheet_row
        # never sets them.
        "Symbol_Rating_Consecutive_Bad_Cycles", "Symbol_Rating_Excluded",
        # Google Trends ASVI diagnostic (pipeline/production_steps.py) --
        # orchestrator-only by construction, matching Sector_Heat_Factor/
        # Attention_Score's precedent above: written only inside
        # StrategyEvalStep's _apply_google_trends_asvi helper, which the
        # advisory path (main.py -> engine/advisory.py -> rec_to_sheet_row)
        # never runs.
        "Google_Trends_ASVI",
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
        assert len(mapped) == 35
        assert len(unmapped) == 81
        assert len(mapped) + len(unmapped) == len(config.COLUMN_SCHEMA) == 116

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
        filter steps (legacy/reporting/sheet_publisher.py:~180-188) confirming
        the 5 originally-dropped-and-now-fixed fields land in the final,
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
