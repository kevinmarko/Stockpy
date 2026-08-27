"""
InvestYo Quant Platform - SEC Rule 606(a)(1) Execution Quality Reporter
========================================================================
Generates quarterly and custom-period order routing disclosure reports
in compliance with SEC Rule 606(a)(1).

Computes:
  1. Breakdown of customer orders by order category (Market, Marketable Limit,
     Non-Marketable Limit, Other).
  2. Venue routing percentages and share volume per venue.
  3. Net Payment for Order Flow (PFOF) / Net Rebates (total dollars, $/100 shares,
     and cents/100 shares).
  4. Price improvement statistics (rates, total dollar value, average improvement
     per order, and average improvement per share).

AST-Safe: Depends only on stdlib, sqlite3, numpy, pandas, and data.execution_audit_store.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from data.execution_audit_store import (
    ORDER_CATEGORIES,
    ORDER_CATEGORY_MARKET,
    ORDER_CATEGORY_MARKETABLE_LIMIT,
    ORDER_CATEGORY_NON_MARKETABLE_LIMIT,
    ORDER_CATEGORY_OTHER,
    ExecutionAuditStore,
    get_quarter_date_range,
    normalize_order_type,
)

logger = logging.getLogger(__name__)


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division guarding against ZeroDivisionError and NaN/Inf."""
    if denominator == 0.0 or not math.isfinite(denominator) or not math.isfinite(numerator):
        return default
    res = numerator / denominator
    return float(res) if math.isfinite(res) else default


def _safe_pct(numerator: float, denominator: float) -> float:
    """Calculate percentage (0.0 to 100.0) safely, rounded to 4 decimal places."""
    return round(_safe_div(numerator, denominator, 0.0) * 100.0, 4)


class SecRule606Reporter:
    """SEC Rule 606(a)(1) quarterly metrics and compliance report generator."""

    def __init__(
        self,
        audit_store: Optional[ExecutionAuditStore] = None,
        *,
        db_url: Optional[str] = None,
        sqlite_path: Optional[str] = None,
    ) -> None:
        if audit_store is not None:
            self.store = audit_store
        else:
            self.store = ExecutionAuditStore(db_url=db_url, sqlite_path=sqlite_path)

    def generate_quarterly_report(
        self,
        year: int,
        quarter: int,
        is_option: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Compute SEC Rule 606(a)(1) metrics for a specific calendar quarter (Q1-Q4).

        Args:
            year: Calendar year (e.g. 2026).
            quarter: Calendar quarter (1, 2, 3, or 4).
            is_option: Filter for option orders (True), equity orders (False), or all (None).

        Returns:
            Structured dictionary containing header metadata, executive summary,
            order category breakdown, and venue routing breakdowns.
        """
        start_dt, end_dt = get_quarter_date_range(year, quarter)
        records = self.store.get_records_for_quarter(year=year, quarter=quarter, is_option=is_option)

        period_str = f"{year}-Q{quarter}"
        return self._compute_report(
            records=records,
            period_label=period_str,
            start_date=start_dt.isoformat(),
            end_date=end_dt.isoformat(),
            year=year,
            quarter=quarter,
            is_option=is_option,
        )

    def generate_report_for_date_range(
        self,
        start_date: Union[datetime, str],
        end_date: Union[datetime, str],
        is_option: Optional[bool] = None,
        period_label: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute SEC Rule 606(a)(1) metrics for a custom date range."""
        records = self.store.get_records_for_date_range(
            start_date=start_date,
            end_date=end_date,
            is_option=is_option,
        )

        start_str = start_date.isoformat() if isinstance(start_date, datetime) else str(start_date)
        end_str = end_date.isoformat() if isinstance(end_date, datetime) else str(end_date)
        label = period_label or f"{start_str} to {end_str}"

        return self._compute_report(
            records=records,
            period_label=label,
            start_date=start_str,
            end_date=end_str,
            year=None,
            quarter=None,
            is_option=is_option,
        )

    def generate_report_for_records(
        self,
        records: Union[List[Dict[str, Any]], pd.DataFrame],
        period_label: str = "Custom Dataset",
        is_option: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Compute SEC Rule 606(a)(1) metrics directly from a list of dicts or pandas DataFrame."""
        if isinstance(records, pd.DataFrame):
            records_list = records.to_dict(orient="records")
        else:
            records_list = list(records or [])

        now_iso = datetime.now(timezone.utc).isoformat()
        return self._compute_report(
            records=records_list,
            period_label=period_label,
            start_date=now_iso,
            end_date=now_iso,
            year=None,
            quarter=None,
            is_option=is_option,
        )

    def _compute_report(
        self,
        records: List[Dict[str, Any]],
        period_label: str,
        start_date: str,
        end_date: str,
        year: Optional[int],
        quarter: Optional[int],
        is_option: Optional[bool],
    ) -> Dict[str, Any]:
        """Core aggregation and calculation engine for SEC 606 metrics."""
        now_utc = datetime.now(timezone.utc).isoformat()

        # Handle empty records gracefully
        if not records:
            return self._build_empty_report(
                period_label=period_label,
                start_date=start_date,
                end_date=end_date,
                year=year,
                quarter=quarter,
                is_option=is_option,
                created_at=now_utc,
            )

        df = pd.DataFrame(records)

        # Ensure all expected columns exist with proper types
        expected_cols = {
            "order_id": "",
            "symbol": "",
            "side": "",
            "venue": "UNKNOWN",
            "order_type": ORDER_CATEGORY_OTHER,
            "routing_timestamp": None,
            "fill_price": 0.0,
            # NaN, not 0.0 -- a missing NBBO must never read as "NBBO was
            # exactly $0.00" (CONSTRAINT #4). Only reachable via
            # generate_report_for_records() called with hand-built dicts
            # that omit these keys; get_records()/_row_to_dict always
            # supplies real values (nbbo_bid/nbbo_ask=None -> NaN here,
            # nbbo_available=False).
            "nbbo_bid": float("nan"),
            "nbbo_ask": float("nan"),
            "executed_shares": 0.0,
            "maker_taker_fee_rebate": 0.0,
            "price_improvement": 0.0,
            # True only for a record whose price_improvement figure is a
            # genuine NBBO-backed measurement -- see
            # data/execution_audit_store.py::ExecutionAuditRecord.nbbo_available's
            # docstring. Defaults False for records/callers that predate this
            # field, matching the honest "unmeasured" interpretation of an
            # absent column rather than assuming coverage.
            "nbbo_available": False,
            "is_option": False,
        }
        for col, default_val in expected_cols.items():
            if col not in df.columns:
                df[col] = default_val

        # Normalize order types and clean numeric columns
        df["order_type"] = df["order_type"].astype(str).apply(normalize_order_type)
        df["venue"] = df["venue"].astype(str).str.upper().str.strip()
        df["executed_shares"] = pd.to_numeric(df["executed_shares"], errors="coerce").fillna(0.0)
        df["fill_price"] = pd.to_numeric(df["fill_price"], errors="coerce").fillna(0.0)
        df["maker_taker_fee_rebate"] = pd.to_numeric(df["maker_taker_fee_rebate"], errors="coerce").fillna(0.0)
        df["price_improvement"] = pd.to_numeric(df["price_improvement"], errors="coerce").fillna(0.0)
        # Price improvement cannot be negative
        df["price_improvement"] = df["price_improvement"].clip(lower=0.0)
        df["nbbo_available"] = df["nbbo_available"].fillna(False).astype(bool)

        df["is_price_improved"] = df["price_improvement"] > 1e-6
        df["notional"] = df["executed_shares"] * df["fill_price"]

        total_orders = len(df)
        total_shares = float(df["executed_shares"].sum())
        total_notional = float(df["notional"].sum())
        total_net_rebate = float(df["maker_taker_fee_rebate"].sum())
        total_price_improvement = float(df["price_improvement"].sum())

        improved_orders_count = int(df["is_price_improved"].sum())
        improved_shares_count = float(df.loc[df["is_price_improved"], "executed_shares"].sum())

        # NBBO-covered subset -- the ONLY orders a price-improvement RATE can
        # honestly be computed against (CONSTRAINT #4). An order with no
        # NBBO is, by construction, never flagged price-improved
        # (calculate_price_improvement can't measure it), so denominating
        # the rate by total_orders instead of nbbo_covered_orders silently
        # deflates it toward 0% as coverage drops -- exactly the confirmed
        # audit finding (2026-08-24) where production coverage is
        # structurally ~0% today and every rate read as a false "0% improved"
        # rather than the honest "0% measurable". Dollar SUMS
        # (total_price_improvement, cat_pi, v_pi below) don't have this
        # problem -- an uncovered order already contributes exactly $0.00 to
        # them, which is correct -- only RATE denominators need this fix.
        nbbo_covered_df = df[df["nbbo_available"]]
        nbbo_covered_orders = len(nbbo_covered_df)
        nbbo_covered_shares = float(nbbo_covered_df["executed_shares"].sum())
        nbbo_coverage_pct = _safe_pct(nbbo_covered_orders, total_orders)

        overall_pi_rate = _safe_pct(improved_orders_count, nbbo_covered_orders)
        overall_share_pi_rate = _safe_pct(improved_shares_count, nbbo_covered_shares)
        overall_rebate_per_100_dollars = round(_safe_div(total_net_rebate, total_shares) * 100.0, 4)
        overall_rebate_per_100_cents = round(overall_rebate_per_100_dollars * 100.0, 2)
        overall_avg_pi_per_order = round(_safe_div(total_price_improvement, total_orders), 4)

        # 1. Order Category Breakdown
        category_breakdown: Dict[str, Dict[str, Any]] = {}
        for cat in ORDER_CATEGORIES:
            cat_df = df[df["order_type"] == cat]
            cat_orders = len(cat_df)
            cat_shares = float(cat_df["executed_shares"].sum())
            cat_rebate = float(cat_df["maker_taker_fee_rebate"].sum())
            cat_pi = float(cat_df["price_improvement"].sum())
            cat_pi_orders = int(cat_df["is_price_improved"].sum())
            cat_pi_shares = float(cat_df.loc[cat_df["is_price_improved"], "executed_shares"].sum())
            cat_nbbo_df = cat_df[cat_df["nbbo_available"]]
            cat_nbbo_orders = len(cat_nbbo_df)
            cat_nbbo_shares = float(cat_nbbo_df["executed_shares"].sum())

            cat_rebate_per_100_dlr = round(_safe_div(cat_rebate, cat_shares) * 100.0, 4)
            cat_rebate_per_100_cnt = round(cat_rebate_per_100_dlr * 100.0, 2)

            category_breakdown[cat] = {
                "category": cat,
                "order_count": cat_orders,
                "pct_of_total_orders": _safe_pct(cat_orders, total_orders),
                "executed_shares": cat_shares,
                "pct_of_total_shares": _safe_pct(cat_shares, total_shares),
                "net_fee_rebate_dollars": round(cat_rebate, 4),
                "rebate_per_hundred_shares_dollars": cat_rebate_per_100_dlr,
                "rebate_per_hundred_shares_cents": cat_rebate_per_100_cnt,
                # nbbo_coverage_pct: what fraction of this category's orders
                # could a price-improvement rate even be measured for --
                # always report this alongside price_improvement_rate so a
                # low/zero rate is never misread as "measured, not improved"
                # when it may really mean "not measurable" (CONSTRAINT #4).
                "nbbo_covered_orders_count": cat_nbbo_orders,
                "nbbo_coverage_pct": _safe_pct(cat_nbbo_orders, cat_orders),
                "price_improved_orders_count": cat_pi_orders,
                "price_improvement_rate": _safe_pct(cat_pi_orders, cat_nbbo_orders),
                "price_improved_shares_count": cat_pi_shares,
                "price_improved_shares_rate": _safe_pct(cat_pi_shares, cat_nbbo_shares),
                "total_price_improvement_dollars": round(cat_pi, 4),
                "avg_price_improvement_per_order_dollars": round(_safe_div(cat_pi, cat_orders), 4),
                "avg_price_improvement_per_improved_order_dollars": round(_safe_div(cat_pi, cat_pi_orders), 4),
                "avg_price_improvement_per_share_cents": round(_safe_div(cat_pi, cat_shares) * 100.0, 4),
                "avg_price_improvement_per_improved_share_cents": round(_safe_div(cat_pi, cat_pi_shares) * 100.0, 4),
            }

        # 2. Venue Breakdown per Order Category
        venue_breakdown_by_category: Dict[str, List[Dict[str, Any]]] = {}
        for cat in ORDER_CATEGORIES:
            cat_df = df[df["order_type"] == cat]
            cat_orders = len(cat_df)
            cat_shares = float(cat_df["executed_shares"].sum())

            venues_in_cat: List[Dict[str, Any]] = []
            if cat_orders > 0:
                for venue_name, vdf in cat_df.groupby("venue"):
                    v_orders = len(vdf)
                    v_shares = float(vdf["executed_shares"].sum())
                    v_rebate = float(vdf["maker_taker_fee_rebate"].sum())
                    v_pi = float(vdf["price_improvement"].sum())
                    v_pi_orders = int(vdf["is_price_improved"].sum())
                    v_pi_shares = float(vdf.loc[vdf["is_price_improved"], "executed_shares"].sum())
                    v_nbbo_orders = int(vdf["nbbo_available"].sum())

                    v_rebate_per_100_dlr = round(_safe_div(v_rebate, v_shares) * 100.0, 4)
                    v_rebate_per_100_cnt = round(v_rebate_per_100_dlr * 100.0, 2)

                    venues_in_cat.append({
                        "venue": str(venue_name),
                        "order_count": v_orders,
                        "pct_of_category_orders": _safe_pct(v_orders, cat_orders),
                        "pct_of_total_orders": _safe_pct(v_orders, total_orders),
                        "executed_shares": v_shares,
                        "pct_of_category_shares": _safe_pct(v_shares, cat_shares),
                        "net_fee_rebate_dollars": round(v_rebate, 4),
                        "rebate_per_hundred_shares_dollars": v_rebate_per_100_dlr,
                        "rebate_per_hundred_shares_cents": v_rebate_per_100_cnt,
                        "nbbo_covered_orders_count": v_nbbo_orders,
                        "nbbo_coverage_pct": _safe_pct(v_nbbo_orders, v_orders),
                        "price_improved_orders_count": v_pi_orders,
                        "price_improvement_rate": _safe_pct(v_pi_orders, v_nbbo_orders),
                        "price_improved_shares_count": v_pi_shares,
                        "total_price_improvement_dollars": round(v_pi, 4),
                        "avg_price_improvement_per_order_dollars": round(_safe_div(v_pi, v_orders), 4),
                        "avg_price_improvement_per_share_cents": round(_safe_div(v_pi, v_shares) * 100.0, 4),
                        "avg_price_improvement_per_improved_share_cents": round(_safe_div(v_pi, v_pi_shares) * 100.0, 4),
                    })

                # Sort by venue order volume descending
                venues_in_cat.sort(key=lambda x: (x["order_count"], x["executed_shares"]), reverse=True)

            venue_breakdown_by_category[cat] = venues_in_cat

        # 3. Overall Venues Ranking (Across All Categories)
        venues_overall: List[Dict[str, Any]] = []
        for venue_name, vdf in df.groupby("venue"):
            v_orders = len(vdf)
            v_shares = float(vdf["executed_shares"].sum())
            v_rebate = float(vdf["maker_taker_fee_rebate"].sum())
            v_pi = float(vdf["price_improvement"].sum())
            v_pi_orders = int(vdf["is_price_improved"].sum())
            v_pi_shares = float(vdf.loc[vdf["is_price_improved"], "executed_shares"].sum())
            v_nbbo_orders = int(vdf["nbbo_available"].sum())

            v_rebate_per_100_dlr = round(_safe_div(v_rebate, v_shares) * 100.0, 4)
            v_rebate_per_100_cnt = round(v_rebate_per_100_dlr * 100.0, 2)

            venues_overall.append({
                "venue": str(venue_name),
                "total_orders": v_orders,
                "pct_of_total_orders": _safe_pct(v_orders, total_orders),
                "total_shares": v_shares,
                "pct_of_total_shares": _safe_pct(v_shares, total_shares),
                "net_fee_rebate_dollars": round(v_rebate, 4),
                "rebate_per_hundred_shares_dollars": v_rebate_per_100_dlr,
                "rebate_per_hundred_shares_cents": v_rebate_per_100_cnt,
                "nbbo_covered_orders_count": v_nbbo_orders,
                "nbbo_coverage_pct": _safe_pct(v_nbbo_orders, v_orders),
                "price_improved_orders_count": v_pi_orders,
                "price_improvement_rate": _safe_pct(v_pi_orders, v_nbbo_orders),
                "price_improved_shares_count": v_pi_shares,
                "total_price_improvement_dollars": round(v_pi, 4),
                "avg_price_improvement_per_order_dollars": round(_safe_div(v_pi, v_orders), 4),
                "avg_price_improvement_per_share_cents": round(_safe_div(v_pi, v_shares) * 100.0, 4),
            })

        venues_overall.sort(key=lambda x: (x["total_orders"], x["total_shares"]), reverse=True)

        return {
            "header": {
                "report_type": "SEC Rule 606(a)(1) Order Routing & Execution Quality Report",
                "period": period_label,
                "year": year,
                "quarter": quarter,
                "start_date": start_date,
                "end_date": end_date,
                "is_option": is_option,
                "created_at": now_utc,
            },
            "summary": {
                "total_orders": total_orders,
                "total_shares": total_shares,
                "total_notional": round(total_notional, 2),
                "total_net_rebate_dollars": round(total_net_rebate, 4),
                "total_price_improvement_dollars": round(total_price_improvement, 4),
                "overall_price_improvement_rate": overall_pi_rate,
                "overall_share_price_improvement_rate": overall_share_pi_rate,
                "overall_rebate_per_hundred_shares_dollars": overall_rebate_per_100_dollars,
                "overall_rebate_per_hundred_shares_cents": overall_rebate_per_100_cents,
                "overall_avg_price_improvement_per_order_dollars": overall_avg_pi_per_order,
                "price_improved_orders_count": improved_orders_count,
                # CONSTRAINT #4: always report alongside the price-improvement
                # figures above so "0% improved" can never be read without
                # also seeing "0% measurable" -- see this store's
                # nbbo_available column docstring and
                # docs/known_issues/sec_606_price_improvement_fabricated_zero.md.
                "nbbo_covered_orders_count": nbbo_covered_orders,
                "nbbo_coverage_pct": nbbo_coverage_pct,
            },
            "order_category_breakdown": category_breakdown,
            "venue_breakdown": {
                "by_category": venue_breakdown_by_category,
                "venues_overall": venues_overall,
            },
        }

    def _build_empty_report(
        self,
        period_label: str,
        start_date: str,
        end_date: str,
        year: Optional[int],
        quarter: Optional[int],
        is_option: Optional[bool],
        created_at: str,
    ) -> Dict[str, Any]:
        """Construct an empty, zero-filled report structure when no records exist."""
        empty_cat_breakdown = {}
        empty_venues_by_cat = {}
        for cat in ORDER_CATEGORIES:
            empty_cat_breakdown[cat] = {
                "category": cat,
                "order_count": 0,
                "pct_of_total_orders": 0.0,
                "executed_shares": 0.0,
                "pct_of_total_shares": 0.0,
                "net_fee_rebate_dollars": 0.0,
                "rebate_per_hundred_shares_dollars": 0.0,
                "rebate_per_hundred_shares_cents": 0.0,
                "nbbo_covered_orders_count": 0,
                "nbbo_coverage_pct": 0.0,
                "price_improved_orders_count": 0,
                "price_improvement_rate": 0.0,
                "price_improved_shares_count": 0.0,
                "price_improved_shares_rate": 0.0,
                "total_price_improvement_dollars": 0.0,
                "avg_price_improvement_per_order_dollars": 0.0,
                "avg_price_improvement_per_improved_order_dollars": 0.0,
                "avg_price_improvement_per_share_cents": 0.0,
                "avg_price_improvement_per_improved_share_cents": 0.0,
            }
            empty_venues_by_cat[cat] = []

        return {
            "header": {
                "report_type": "SEC Rule 606(a)(1) Order Routing & Execution Quality Report",
                "period": period_label,
                "year": year,
                "quarter": quarter,
                "start_date": start_date,
                "end_date": end_date,
                "is_option": is_option,
                "created_at": created_at,
            },
            "summary": {
                "total_orders": 0,
                "total_shares": 0.0,
                "total_notional": 0.0,
                "total_net_rebate_dollars": 0.0,
                "total_price_improvement_dollars": 0.0,
                "overall_price_improvement_rate": 0.0,
                "overall_share_price_improvement_rate": 0.0,
                "overall_rebate_per_hundred_shares_dollars": 0.0,
                "overall_rebate_per_hundred_shares_cents": 0.0,
                "overall_avg_price_improvement_per_order_dollars": 0.0,
                "price_improved_orders_count": 0,
                "nbbo_covered_orders_count": 0,
                "nbbo_coverage_pct": 0.0,
            },
            "order_category_breakdown": empty_cat_breakdown,
            "venue_breakdown": {
                "by_category": empty_venues_by_cat,
                "venues_overall": [],
            },
        }

    def generate_summary_tables(self, report: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
        """Convert a report dictionary into a set of clean pandas DataFrames
        for presentation, export, or downstream analysis.
        """
        # 1. Category Overview Table
        cat_rows = list(report.get("order_category_breakdown", {}).values())
        df_categories = pd.DataFrame(cat_rows) if cat_rows else pd.DataFrame()

        # 2. Venues Overall Table
        overall_rows = report.get("venue_breakdown", {}).get("venues_overall", [])
        df_venues_overall = pd.DataFrame(overall_rows) if overall_rows else pd.DataFrame()

        # 3. Venues By Category Table (Flattened)
        by_cat_rows = []
        by_cat = report.get("venue_breakdown", {}).get("by_category", {})
        for cat, venues in by_cat.items():
            for v in venues:
                row = dict(v)
                row["category"] = cat
                by_cat_rows.append(row)
        df_venues_by_category = pd.DataFrame(by_cat_rows) if by_cat_rows else pd.DataFrame()

        return {
            "categories": df_categories,
            "venues_overall": df_venues_overall,
            "venues_by_category": df_venues_by_category,
        }

    def generate_markdown_summary(self, report: Dict[str, Any]) -> str:
        """Generate a GitHub Flavored Markdown formatted compliance report."""
        hdr = report.get("header", {})
        summary = report.get("summary", {})
        cats = report.get("order_category_breakdown", {})
        venues_overall = report.get("venue_breakdown", {}).get("venues_overall", [])

        lines = [
            f"# SEC Rule 606(a)(1) Order Routing Report: {hdr.get('period', 'N/A')}",
            f"**Report Period:** `{hdr.get('start_date', '')}` to `{hdr.get('end_date', '')}`  ",
            f"**Generated:** `{hdr.get('created_at', '')}`  ",
            f"**Asset Class Filter:** `{'Options' if hdr.get('is_option') is True else ('Equities' if hdr.get('is_option') is False else 'All')}`",
            "",
            "## 1. Executive Summary",
            "",
            "| Metric | Value |",
            "| :--- | :--- |",
            f"| **Total Orders Routed** | `{summary.get('total_orders', 0):,}` |",
            f"| **Total Executed Shares/Contracts** | `{summary.get('total_shares', 0.0):,.2f}` |",
            f"| **Total Notional Traded** | `${summary.get('total_notional', 0.0):,.2f}` |",
            f"| **Net PFOF / Fee / Rebate ($)** | `${summary.get('total_net_rebate_dollars', 0.0):,.4f}` |",
            f"| **Net Rebate per 100 Shares** | `${summary.get('overall_rebate_per_hundred_shares_dollars', 0.0):.4f}` (`{summary.get('overall_rebate_per_hundred_shares_cents', 0.0):.2f}¢`) |",
            f"| **NBBO Coverage (orders measurable for PI)** | `{summary.get('nbbo_covered_orders_count', 0):,}` of `{summary.get('total_orders', 0):,}` (`{summary.get('nbbo_coverage_pct', 0.0):.2f}%`) |",
            f"| **Price Improved Orders Count** | `{summary.get('price_improved_orders_count', 0):,}` |",
            f"| **Price Improvement Rate (of NBBO-covered orders)** | `{summary.get('overall_price_improvement_rate', 0.0):.2f}%` |",
            f"| **Total Price Improvement** | `${summary.get('total_price_improvement_dollars', 0.0):,.4f}` |",
            f"| **Avg Price Improvement / Order** | `${summary.get('overall_avg_price_improvement_per_order_dollars', 0.0):.4f}` |",
            "",
        ]
        if summary.get("nbbo_coverage_pct", 0.0) < 100.0:
            lines.append(
                f"> ⚠️ **NBBO data was unavailable for {100.0 - summary.get('nbbo_coverage_pct', 0.0):.2f}% "
                f"of orders this period.** Price improvement rate/dollar figures above reflect only the "
                f"{summary.get('nbbo_coverage_pct', 0.0):.2f}% of orders with a real NBBO measurement at "
                f"routing time -- they are NOT a claim that the remaining orders had zero improvement."
            )
        lines += [
            "",
            "## 2. Order Category Breakdown",
            "",
            "| Category | Orders | % of Total | Shares | % Shares | Net Rebate ($) | Rebate/100sh (¢) | PI Rate (%) | Total PI ($) | Avg PI/Order ($) |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        for cat_name in ORDER_CATEGORIES:
            c = cats.get(cat_name, {})
            lines.append(
                f"| **{cat_name}** | {c.get('order_count', 0):,} | {c.get('pct_of_total_orders', 0.0):.2f}% | "
                f"{c.get('executed_shares', 0.0):,.0f} | {c.get('pct_of_total_shares', 0.0):.2f}% | "
                f"${c.get('net_fee_rebate_dollars', 0.0):,.2f} | {c.get('rebate_per_hundred_shares_cents', 0.0):.2f}¢ | "
                f"{c.get('price_improvement_rate', 0.0):.2f}% | ${c.get('total_price_improvement_dollars', 0.0):,.2f} | "
                f"${c.get('avg_price_improvement_per_order_dollars', 0.0):.4f} |"
            )

        lines.extend([
            "",
            "## 3. Venue Routing Breakdown (Overall)",
            "",
            "| Venue | Orders | % Orders | Shares | % Shares | Net Rebate ($) | Rebate/100sh (¢) | PI Rate (%) | Total PI ($) | Avg PI/Order ($) |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ])

        if venues_overall:
            for v in venues_overall:
                lines.append(
                    f"| **{v.get('venue', 'UNKNOWN')}** | {v.get('total_orders', 0):,} | {v.get('pct_of_total_orders', 0.0):.2f}% | "
                    f"{v.get('total_shares', 0.0):,.0f} | {v.get('pct_of_total_shares', 0.0):.2f}% | "
                    f"${v.get('net_fee_rebate_dollars', 0.0):,.2f} | {v.get('rebate_per_hundred_shares_cents', 0.0):.2f}¢ | "
                    f"{v.get('price_improvement_rate', 0.0):.2f}% | ${v.get('total_price_improvement_dollars', 0.0):,.2f} | "
                    f"${v.get('avg_price_improvement_per_order_dollars', 0.0):.4f} |"
                )
        else:
            lines.append("| *No venue routing data available for this period* | - | - | - | - | - | - | - | - | - |")

        lines.append("")
        return "\n".join(lines)

    def export_json(self, report: Dict[str, Any], filepath: Union[str, Path]) -> None:
        """Export full report structure to a JSON file."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        logger.info("SEC Rule 606 report exported to JSON: %s", path)

    def export_csv(self, report: Dict[str, Any], base_filepath: Union[str, Path]) -> Dict[str, Path]:
        """Export report tables to CSV files. Returns dictionary of written file paths."""
        base_path = Path(base_filepath)
        tables = self.generate_summary_tables(report)
        written_paths = {}

        for table_name, df_table in tables.items():
            if base_path.is_dir() or not base_path.suffix:
                base_path.mkdir(parents=True, exist_ok=True)
                csv_path = base_path / f"sec_606_{table_name}.csv"
            else:
                stem = base_path.stem
                csv_path = base_path.parent / f"{stem}_{table_name}.csv"
                csv_path.parent.mkdir(parents=True, exist_ok=True)

            df_table.to_csv(csv_path, index=False)
            written_paths[table_name] = csv_path
            logger.info("SEC Rule 606 table '%s' exported to %s", table_name, csv_path)

        return written_paths
