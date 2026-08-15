"""
Unit and Integration Tests for SEC Rule 606 Reporter & Execution Audit Store
=============================================================================
Tests:
  1. ExecutionAuditStore persistence, indexing, querying, and filtering.
  2. Price improvement calculation & limit order classification heuristics.
  3. SEC Rule 606(a)(1) quarterly metrics calculation (category breakdown,
     venue routing percentages, net PFOF / rebates per 100 shares, price
     improvement statistics).
  4. Empty-record handling and zero-division resilience.
  5. Markdown, DataFrame, JSON, and CSV report export pipelines.
  6. Read-only database guardrails.
  7. Strict AST dependency safety.
"""

import ast
from datetime import datetime, timezone
from pathlib import Path
import pytest
import pandas as pd

from data.execution_audit_store import (
    ExecutionAuditStore,
    calculate_price_improvement,
    classify_limit_order,
    normalize_order_type,
    get_quarter_date_range,
    ORDER_CATEGORIES,
    ORDER_CATEGORY_MARKET,
    ORDER_CATEGORY_MARKETABLE_LIMIT,
    ORDER_CATEGORY_NON_MARKETABLE_LIMIT,
    ORDER_CATEGORY_OTHER,
)
from execution.sec_rule_606_reporter import SecRule606Reporter


@pytest.fixture
def mem_store(tmp_path):
    """Fixture providing a fresh SQLite file-backed ExecutionAuditStore."""
    db_file = tmp_path / "test_execution_audit.db"
    return ExecutionAuditStore(sqlite_path=str(db_file))


class TestPriceImprovementAndClassification:
    """Test mathematical calculations for price improvement and order type classification."""

    def test_price_improvement_buy(self):
        # BUY order: NBBO Ask is 150.10, fill at 150.05 -> $0.05 improvement per share * 100 shares = $5.00
        pi = calculate_price_improvement(
            side="buy",
            fill_price=150.05,
            nbbo_bid=150.00,
            nbbo_ask=150.10,
            shares=100.0,
        )
        assert pytest.approx(pi, 1e-4) == 5.00

        # BUY order: fill at 150.10 (at ask) -> 0.0 improvement
        pi_at_ask = calculate_price_improvement(
            side="BUY",
            fill_price=150.10,
            nbbo_bid=150.00,
            nbbo_ask=150.10,
            shares=100.0,
        )
        assert pi_at_ask == 0.0

        # BUY order: fill at 150.12 (worse than ask) -> 0.0 improvement
        pi_worse = calculate_price_improvement(
            side="buy",
            fill_price=150.12,
            nbbo_bid=150.00,
            nbbo_ask=150.10,
            shares=100.0,
        )
        assert pi_worse == 0.0

    def test_price_improvement_sell(self):
        # SELL order: NBBO Bid is 150.00, fill at 150.04 -> $0.04 improvement per share * 200 shares = $8.00
        pi = calculate_price_improvement(
            side="sell",
            fill_price=150.04,
            nbbo_bid=150.00,
            nbbo_ask=150.10,
            shares=200.0,
        )
        assert pytest.approx(pi, 1e-4) == 8.00

        # SELL order: fill at 150.00 (at bid) -> 0.0 improvement
        pi_at_bid = calculate_price_improvement(
            side="SELL",
            fill_price=150.00,
            nbbo_bid=150.00,
            nbbo_ask=150.10,
            shares=200.0,
        )
        assert pi_at_bid == 0.0

    def test_price_improvement_edge_cases(self):
        # Missing NBBO or None prices or zero shares
        assert calculate_price_improvement("buy", None, 100.0, 101.0, 50.0) == 0.0
        assert calculate_price_improvement("buy", 100.0, 100.0, None, 50.0) == 0.0
        assert calculate_price_improvement("sell", 100.0, None, 101.0, 50.0) == 0.0
        assert calculate_price_improvement("buy", 100.0, 99.0, 101.0, 0.0) == 0.0

    def test_classify_limit_order(self):
        # BUY limit >= ask is Marketable Limit
        assert classify_limit_order("buy", 100.50, nbbo_bid=100.00, nbbo_ask=100.50) == ORDER_CATEGORY_MARKETABLE_LIMIT
        assert classify_limit_order("buy", 100.55, nbbo_bid=100.00, nbbo_ask=100.50) == ORDER_CATEGORY_MARKETABLE_LIMIT

        # BUY limit < ask is Non-Marketable Limit
        assert classify_limit_order("buy", 100.45, nbbo_bid=100.00, nbbo_ask=100.50) == ORDER_CATEGORY_NON_MARKETABLE_LIMIT

        # SELL limit <= bid is Marketable Limit
        assert classify_limit_order("sell", 100.00, nbbo_bid=100.00, nbbo_ask=100.50) == ORDER_CATEGORY_MARKETABLE_LIMIT
        assert classify_limit_order("sell", 99.95, nbbo_bid=100.00, nbbo_ask=100.50) == ORDER_CATEGORY_MARKETABLE_LIMIT

        # SELL limit > bid is Non-Marketable Limit
        assert classify_limit_order("sell", 100.10, nbbo_bid=100.00, nbbo_ask=100.50) == ORDER_CATEGORY_NON_MARKETABLE_LIMIT

    def test_normalize_order_type(self):
        assert normalize_order_type("market") == ORDER_CATEGORY_MARKET
        assert normalize_order_type("MKT") == ORDER_CATEGORY_MARKET
        assert normalize_order_type("Marketable_Limit") == ORDER_CATEGORY_MARKETABLE_LIMIT
        assert normalize_order_type("marketable limit") == ORDER_CATEGORY_MARKETABLE_LIMIT
        assert normalize_order_type("non_marketable_limit") == ORDER_CATEGORY_NON_MARKETABLE_LIMIT
        assert normalize_order_type("nonmarketable limit") == ORDER_CATEGORY_NON_MARKETABLE_LIMIT
        assert normalize_order_type("limit") == ORDER_CATEGORY_NON_MARKETABLE_LIMIT
        assert normalize_order_type("stop") == ORDER_CATEGORY_OTHER
        assert normalize_order_type("other") == ORDER_CATEGORY_OTHER
        assert normalize_order_type("") == ORDER_CATEGORY_OTHER

    def test_get_quarter_date_range(self):
        q1_start, q1_end = get_quarter_date_range(2026, 1)
        assert q1_start.month == 1 and q1_start.day == 1
        assert q1_end.month == 3 and q1_end.day == 31

        q2_start, q2_end = get_quarter_date_range(2026, 2)
        assert q2_start.month == 4 and q2_start.day == 1
        assert q2_end.month == 6 and q2_end.day == 30

        q3_start, q3_end = get_quarter_date_range(2026, 3)
        assert q3_start.month == 7 and q3_start.day == 1
        assert q3_end.month == 9 and q3_end.day == 30

        q4_start, q4_end = get_quarter_date_range(2026, 4)
        assert q4_start.month == 10 and q4_start.day == 1
        assert q4_end.month == 12 and q4_end.day == 31

        with pytest.raises(ValueError):
            get_quarter_date_range(2026, 5)


class TestExecutionAuditStorePersistence:
    """Test CRUD operations, persistence, and queries in ExecutionAuditStore."""

    def test_record_single_and_query(self, mem_store):
        rec_id = mem_store.record_audit({
            "order_id": "ORD-001",
            "client_order_id": "CL-001",
            "symbol": "AAPL",
            "side": "buy",
            "venue": "CITADEL",
            "order_type": "Market",
            "routing_timestamp": datetime(2026, 2, 15, 14, 30, 0, tzinfo=timezone.utc),
            "fill_price": 185.20,
            "nbbo_bid": 185.18,
            "nbbo_ask": 185.25,
            "executed_shares": 100.0,
            "maker_taker_fee_rebate": 0.30,  # $0.30 rebate received
            "price_improvement": 5.00,
            "is_option": False,
        })
        assert rec_id >= 1
        assert mem_store.count() == 1

        records = mem_store.get_records(symbol="AAPL")
        assert len(records) == 1
        r = records[0]
        assert r["order_id"] == "ORD-001"
        assert r["symbol"] == "AAPL"
        assert r["venue"] == "CITADEL"
        assert r["order_type"] == "Market"
        assert r["fill_price"] == 185.20
        assert r["executed_shares"] == 100.0
        assert r["maker_taker_fee_rebate"] == 0.30
        assert r["price_improvement"] == 5.00
        assert r["is_option"] is False

    def test_batch_record_and_filtering(self, mem_store):
        batch = [
            {
                "order_id": "ORD-001",
                "symbol": "SPY",
                "side": "buy",
                "venue": "ARCA",
                "order_type": "Market",
                "routing_timestamp": datetime(2026, 1, 10, 10, 0, 0),
                "fill_price": 500.0,
                "nbbo_bid": 499.98,
                "nbbo_ask": 500.02,
                "executed_shares": 200.0,
                "maker_taker_fee_rebate": -0.60,  # taker fee paid
            },
            {
                "order_id": "ORD-002",
                "symbol": "QQQ",
                "side": "sell",
                "venue": "VIRTU",
                "order_type": "Marketable Limit",
                "routing_timestamp": datetime(2026, 2, 20, 11, 0, 0),
                "fill_price": 430.0,
                "nbbo_bid": 429.95,
                "nbbo_ask": 430.05,
                "executed_shares": 100.0,
                "maker_taker_fee_rebate": 0.40,
            },
            {
                "order_id": "ORD-003",
                "symbol": "AAPL",
                "side": "buy",
                "venue": "ARCA",
                "order_type": "Non-Marketable Limit",
                "routing_timestamp": datetime(2026, 4, 5, 12, 0, 0),  # Q2
                "fill_price": 180.0,
                "nbbo_bid": 179.95,
                "nbbo_ask": 180.05,
                "executed_shares": 300.0,
                "maker_taker_fee_rebate": 0.90,  # maker rebate
            },
        ]
        inserted = mem_store.record_audits(batch)
        assert inserted == 3
        assert mem_store.count() == 3

        # Filter by venue
        arca_records = mem_store.get_records(venue="ARCA")
        assert len(arca_records) == 2

        # Filter by quarter
        q1_records = mem_store.get_records_for_quarter(2026, 1)
        assert len(q1_records) == 2

        q2_records = mem_store.get_records_for_quarter(2026, 2)
        assert len(q2_records) == 1
        assert q2_records[0]["symbol"] == "AAPL"

        # Clear records
        deleted = mem_store.clear_records()
        assert deleted == 3
        assert mem_store.count() == 0

    def test_readonly_store_guardrail(self, tmp_path):
        db_file = tmp_path / "ro_test.db"
        # Write 1 record with write-store
        writer = ExecutionAuditStore(sqlite_path=str(db_file))
        writer.record_audit({
            "order_id": "ORD-RO",
            "symbol": "MSFT",
            "venue": "IEX",
            "order_type": "Market",
            "routing_timestamp": datetime(2026, 1, 15, 10, 0, 0),
            "executed_shares": 50.0,
        })

        # Open in readonly mode
        reader = ExecutionAuditStore(sqlite_path=str(db_file), readonly=True)
        records = reader.get_records()
        assert len(records) == 1
        assert records[0]["symbol"] == "MSFT"

        # Writes must raise
        with pytest.raises(RuntimeError):
            reader.record_audit({"order_id": "ORD-FAIL", "symbol": "NVDA", "venue": "NYSE", "order_type": "Market"})

        with pytest.raises(RuntimeError):
            reader.clear_records()


class TestSecRule606ReporterQuarterlyMetrics:
    """Test SEC Rule 606(a)(1) quarterly metrics computation with known synthetic datasets."""

    @pytest.fixture
    def populated_store(self, tmp_path):
        """Creates a store with known orders in 2026-Q1:
        Total: 4 orders, 1,000 shares total.
        - Order 1 (Market): 400 shares routed to CITADEL, fee/rebate: +$1.20, price improvement: $4.00 ($0.01/sh)
        - Order 2 (Market): 100 shares routed to VIRTU, fee/rebate: +$0.25, price improvement: $0.00
        - Order 3 (Marketable Limit): 200 shares routed to CITADEL, fee/rebate: +$0.60, price improvement: $2.00 ($0.01/sh)
        - Order 4 (Non-Marketable Limit): 300 shares routed to ARCA, fee/rebate: +$0.90, price improvement: $0.00
        """
        db_file = tmp_path / "sec606_q1.db"
        store = ExecutionAuditStore(sqlite_path=str(db_file))
        orders = [
            {
                "order_id": "O-1",
                "symbol": "AAPL",
                "side": "buy",
                "venue": "CITADEL",
                "order_type": "Market",
                "routing_timestamp": datetime(2026, 1, 15, 14, 0, 0),
                "fill_price": 150.00,
                "executed_shares": 400.0,
                "maker_taker_fee_rebate": 1.20,
                "price_improvement": 4.00,
                "is_option": False,
            },
            {
                "order_id": "O-2",
                "symbol": "MSFT",
                "side": "buy",
                "venue": "VIRTU",
                "order_type": "Market",
                "routing_timestamp": datetime(2026, 2, 10, 15, 0, 0),
                "fill_price": 400.00,
                "executed_shares": 100.0,
                "maker_taker_fee_rebate": 0.25,
                "price_improvement": 0.00,
                "is_option": False,
            },
            {
                "order_id": "O-3",
                "symbol": "GOOGL",
                "side": "buy",
                "venue": "CITADEL",
                "order_type": "Marketable Limit",
                "routing_timestamp": datetime(2026, 3, 5, 11, 0, 0),
                "fill_price": 175.00,
                "executed_shares": 200.0,
                "maker_taker_fee_rebate": 0.60,
                "price_improvement": 2.00,
                "is_option": False,
            },
            {
                "order_id": "O-4",
                "symbol": "AMZN",
                "side": "sell",
                "venue": "ARCA",
                "order_type": "Non-Marketable Limit",
                "routing_timestamp": datetime(2026, 3, 20, 9, 35, 0),
                "fill_price": 180.00,
                "executed_shares": 300.0,
                "maker_taker_fee_rebate": 0.90,
                "price_improvement": 0.00,
                "is_option": False,
            },
        ]
        store.record_audits(orders)
        return store

    def test_quarterly_report_overall_metrics(self, populated_store):
        reporter = SecRule606Reporter(audit_store=populated_store)
        report = reporter.generate_quarterly_report(year=2026, quarter=1)

        summary = report["summary"]
        assert summary["total_orders"] == 4
        assert summary["total_shares"] == 1000.0
        # Total Net Rebate: 1.20 + 0.25 + 0.60 + 0.90 = $2.95
        assert pytest.approx(summary["total_net_rebate_dollars"], 1e-4) == 2.95
        # Net Rebate / 100 shares: (2.95 / 1000) * 100 = $0.295 / 100 shares (29.5 cents)
        assert pytest.approx(summary["overall_rebate_per_hundred_shares_dollars"], 1e-4) == 0.295
        assert pytest.approx(summary["overall_rebate_per_hundred_shares_cents"], 1e-2) == 29.50

        # Price improvement: O-1 ($4.00) and O-3 ($2.00) -> 2 out of 4 orders (50.0%)
        assert summary["price_improved_orders_count"] == 2
        assert pytest.approx(summary["overall_price_improvement_rate"], 1e-2) == 50.0
        # Total Price Improvement: $6.00, Avg per order: $6.00 / 4 = $1.50
        assert pytest.approx(summary["total_price_improvement_dollars"], 1e-4) == 6.00
        assert pytest.approx(summary["overall_avg_price_improvement_per_order_dollars"], 1e-4) == 1.50

    def test_order_category_breakdown(self, populated_store):
        reporter = SecRule606Reporter(audit_store=populated_store)
        report = reporter.generate_quarterly_report(year=2026, quarter=1)
        cats = report["order_category_breakdown"]

        # Market Orders: 2 orders (50.0%), 500 shares (50.0%)
        mkt = cats[ORDER_CATEGORY_MARKET]
        assert mkt["order_count"] == 2
        assert pytest.approx(mkt["pct_of_total_orders"], 1e-2) == 50.0
        assert mkt["executed_shares"] == 500.0
        assert pytest.approx(mkt["pct_of_total_shares"], 1e-2) == 50.0
        assert pytest.approx(mkt["net_fee_rebate_dollars"], 1e-4) == 1.45
        assert pytest.approx(mkt["rebate_per_hundred_shares_cents"], 1e-2) == 29.00
        assert mkt["price_improved_orders_count"] == 1
        assert pytest.approx(mkt["price_improvement_rate"], 1e-2) == 50.0

        # Marketable Limit Orders: 1 order (25.0%), 200 shares (20.0%)
        mkt_lim = cats[ORDER_CATEGORY_MARKETABLE_LIMIT]
        assert mkt_lim["order_count"] == 1
        assert pytest.approx(mkt_lim["pct_of_total_orders"], 1e-2) == 25.0
        assert mkt_lim["executed_shares"] == 200.0
        assert pytest.approx(mkt_lim["price_improvement_rate"], 1e-2) == 100.0
        assert pytest.approx(mkt_lim["total_price_improvement_dollars"], 1e-4) == 2.00

        # Non-Marketable Limit Orders: 1 order (25.0%), 300 shares (30.0%)
        non_mkt_lim = cats[ORDER_CATEGORY_NON_MARKETABLE_LIMIT]
        assert non_mkt_lim["order_count"] == 1
        assert pytest.approx(non_mkt_lim["pct_of_total_orders"], 1e-2) == 25.0
        assert non_mkt_lim["executed_shares"] == 300.0
        assert non_mkt_lim["price_improvement_rate"] == 0.0

        # Other Orders: 0 orders (0.0%)
        other = cats[ORDER_CATEGORY_OTHER]
        assert other["order_count"] == 0
        assert other["pct_of_total_orders"] == 0.0

    def test_venue_breakdown_by_category_and_overall(self, populated_store):
        reporter = SecRule606Reporter(audit_store=populated_store)
        report = reporter.generate_quarterly_report(year=2026, quarter=1)

        # Venues Overall
        venues_overall = report["venue_breakdown"]["venues_overall"]
        # CITADEL: 2 orders (50%), 600 shares (60%)
        # ARCA: 1 order (25%), 300 shares (30%)
        # VIRTU: 1 order (25%), 100 shares (10%)
        assert len(venues_overall) == 3
        citadel = next(v for v in venues_overall if v["venue"] == "CITADEL")
        assert citadel["total_orders"] == 2
        assert pytest.approx(citadel["pct_of_total_orders"], 1e-2) == 50.0
        assert citadel["total_shares"] == 600.0
        assert pytest.approx(citadel["pct_of_total_shares"], 1e-2) == 60.0
        assert pytest.approx(citadel["total_price_improvement_dollars"], 1e-4) == 6.00

        # Venues by Category (Market)
        market_venues = report["venue_breakdown"]["by_category"][ORDER_CATEGORY_MARKET]
        assert len(market_venues) == 2
        citadel_mkt = next(v for v in market_venues if v["venue"] == "CITADEL")
        assert citadel_mkt["order_count"] == 1
        assert pytest.approx(citadel_mkt["pct_of_category_orders"], 1e-2) == 50.0
        assert citadel_mkt["executed_shares"] == 400.0
        assert pytest.approx(citadel_mkt["pct_of_category_shares"], 1e-2) == 80.0


class TestEmptyRecordsHandling:
    """Test zero-division resilience and structure formatting when no records exist."""

    def test_empty_quarter_report(self, mem_store):
        reporter = SecRule606Reporter(audit_store=mem_store)
        report = reporter.generate_quarterly_report(year=2026, quarter=3)

        assert report["summary"]["total_orders"] == 0
        assert report["summary"]["total_shares"] == 0.0
        assert report["summary"]["total_net_rebate_dollars"] == 0.0
        assert report["summary"]["overall_price_improvement_rate"] == 0.0
        assert report["summary"]["overall_rebate_per_hundred_shares_dollars"] == 0.0
        assert report["summary"]["overall_avg_price_improvement_per_order_dollars"] == 0.0

        # Verify all 4 standard categories exist
        cats = report["order_category_breakdown"]
        for cat_name in ORDER_CATEGORIES:
            assert cat_name in cats
            assert cats[cat_name]["order_count"] == 0
            assert cats[cat_name]["pct_of_total_orders"] == 0.0

        # Check venue breakdowns
        assert report["venue_breakdown"]["venues_overall"] == []
        for cat_name in ORDER_CATEGORIES:
            assert report["venue_breakdown"]["by_category"][cat_name] == []

    def test_generate_report_from_empty_dataframe(self):
        reporter = SecRule606Reporter(audit_store=None, sqlite_path=":memory:")
        report = reporter.generate_report_for_records(pd.DataFrame())
        assert report["summary"]["total_orders"] == 0
        assert report["summary"]["total_shares"] == 0.0


class TestReportExportsAndFormatting:
    """Test markdown, DataFrame, JSON, and CSV export functionality."""

    @pytest.fixture
    def sample_report(self, mem_store):
        mem_store.record_audits([
            {
                "order_id": "ORD-1",
                "symbol": "SPY",
                "venue": "ARCA",
                "order_type": "Market",
                "routing_timestamp": datetime(2026, 1, 15, 10, 0, 0),
                "fill_price": 500.0,
                "executed_shares": 100.0,
                "maker_taker_fee_rebate": 0.20,
                "price_improvement": 2.00,
            }
        ])
        reporter = SecRule606Reporter(audit_store=mem_store)
        return reporter.generate_quarterly_report(year=2026, quarter=1)

    def test_markdown_generation(self, mem_store, sample_report):
        reporter = SecRule606Reporter(audit_store=mem_store)
        md = reporter.generate_markdown_summary(sample_report)
        assert "# SEC Rule 606(a)(1) Order Routing Report" in md
        assert "Total Orders Routed" in md
        assert "Order Category Breakdown" in md
        assert "Venue Routing Breakdown" in md
        assert "ARCA" in md

    def test_summary_tables(self, mem_store, sample_report):
        reporter = SecRule606Reporter(audit_store=mem_store)
        tables = reporter.generate_summary_tables(sample_report)
        assert "categories" in tables
        assert "venues_overall" in tables
        assert "venues_by_category" in tables

        assert isinstance(tables["categories"], pd.DataFrame)
        assert len(tables["categories"]) == 4  # 4 standard SEC categories

        assert isinstance(tables["venues_overall"], pd.DataFrame)
        assert len(tables["venues_overall"]) == 1
        assert tables["venues_overall"].iloc[0]["venue"] == "ARCA"

    def test_export_json_and_csv(self, mem_store, sample_report, tmp_path):
        reporter = SecRule606Reporter(audit_store=mem_store)
        json_path = tmp_path / "sec_606.json"
        reporter.export_json(sample_report, json_path)
        assert json_path.exists()
        assert json_path.stat().st_size > 0

        csv_dir = tmp_path / "csv_out"
        written = reporter.export_csv(sample_report, csv_dir)
        assert "categories" in written
        assert "venues_overall" in written
        assert "venues_by_category" in written
        for name, p in written.items():
            assert Path(p).exists()
            assert Path(p).stat().st_size > 0


class TestAstSafetyAndNoHeavyEngineImports:
    """Verify that both modules maintain strict AST safety without heavy engine imports."""

    def test_execution_audit_store_ast_safety(self):
        store_path = Path("data/execution_audit_store.py")
        assert store_path.exists()
        tree = ast.parse(store_path.read_text(encoding="utf-8"))

        forbidden_prefixes = ["main_orchestrator", "pipeline", "strategies", "ml", "pilots", "gui"]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_prefixes:
                        assert not alias.name.startswith(forbidden), f"Forbidden import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for forbidden in forbidden_prefixes:
                        assert not node.module.startswith(forbidden), f"Forbidden import from: {node.module}"

    def test_sec_rule_606_reporter_ast_safety(self):
        reporter_path = Path("execution/sec_rule_606_reporter.py")
        assert reporter_path.exists()
        tree = ast.parse(reporter_path.read_text(encoding="utf-8"))

        forbidden_prefixes = ["main_orchestrator", "pipeline", "strategies", "ml", "pilots", "gui"]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_prefixes:
                        assert not alias.name.startswith(forbidden), f"Forbidden import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for forbidden in forbidden_prefixes:
                        assert not node.module.startswith(forbidden), f"Forbidden import from: {node.module}"
