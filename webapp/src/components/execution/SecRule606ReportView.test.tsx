import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SecRule606ReportView } from "./SecRule606ReportView";
import { api } from "../../api/client";
import type { SecRule606ReportResponse } from "../../api/types";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getSecRule606Report: vi.fn(),
    },
  };
});

const mockReportData: SecRule606ReportResponse = {
  header: {
    report_type: "SEC Rule 606(a)(1) Order Routing & Execution Quality Report",
    period: "2026-Q1",
    year: 2026,
    quarter: 1,
    start_date: "2026-01-01T00:00:00Z",
    end_date: "2026-03-31T23:59:59Z",
    is_option: null,
    created_at: "2026-08-15T14:30:00Z",
  },
  summary: {
    total_orders: 14250,
    total_shares: 2850000,
    total_notional: 412500000,
    total_net_rebate_dollars: 3420.5,
    total_price_improvement_dollars: 18950.25,
    overall_price_improvement_rate: 84.21,
    overall_share_price_improvement_rate: 84.59,
    overall_rebate_per_hundred_shares_dollars: 0.12,
    overall_rebate_per_hundred_shares_cents: 12.0,
    overall_avg_price_improvement_per_order_dollars: 1.33,
    price_improved_orders_count: 12000,
  },
  order_category_breakdown: {
    market: {
      category: "market",
      order_count: 6200,
      pct_of_total_orders: 43.51,
      executed_shares: 1250000,
      pct_of_total_shares: 43.86,
      net_fee_rebate_dollars: 1625.0,
      rebate_per_hundred_shares_dollars: 0.13,
      rebate_per_hundred_shares_cents: 13.0,
      price_improved_orders_count: 5760,
      price_improvement_rate: 92.9,
      price_improved_shares_count: 1160000,
      price_improved_shares_rate: 92.8,
      total_price_improvement_dollars: 11450.25,
      avg_price_improvement_per_order_dollars: 1.85,
      avg_price_improvement_per_improved_order_dollars: 1.99,
      avg_price_improvement_per_share_cents: 0.92,
      avg_price_improvement_per_improved_share_cents: 0.99,
    },
    marketable_limit: {
      category: "marketable_limit",
      order_count: 5150,
      pct_of_total_orders: 36.14,
      executed_shares: 1020000,
      pct_of_total_shares: 35.79,
      net_fee_rebate_dollars: 1326.0,
      rebate_per_hundred_shares_dollars: 0.13,
      rebate_per_hundred_shares_cents: 13.0,
      price_improved_orders_count: 4580,
      price_improvement_rate: 88.93,
      price_improved_shares_count: 910000,
      price_improved_shares_rate: 89.22,
      total_price_improvement_dollars: 6320.0,
      avg_price_improvement_per_order_dollars: 1.23,
      avg_price_improvement_per_improved_order_dollars: 1.38,
      avg_price_improvement_per_share_cents: 0.62,
      avg_price_improvement_per_improved_share_cents: 0.69,
    },
  },
  venue_breakdown: {
    by_category: {},
    venues_overall: [
      {
        venue: "CITADEL SECURITIES LLC",
        order_count: 5420,
        pct_of_total_orders: 38.04,
        executed_shares: 1120000,
        pct_of_total_shares: 39.3,
        net_fee_rebate_dollars: 1456.2,
        rebate_per_hundred_shares_dollars: 0.13,
        rebate_per_hundred_shares_cents: 13.0,
        price_improved_orders_count: 4820,
        price_improvement_rate: 88.93,
        total_price_improvement_dollars: 8420.5,
        avg_price_improvement_per_order_dollars: 1.55,
        avg_price_improvement_per_share_cents: 0.75,
      },
      {
        venue: "VIRTU FINANCIAL BD LLC",
        order_count: 3560,
        pct_of_total_orders: 24.98,
        executed_shares: 720000,
        pct_of_total_shares: 25.26,
        net_fee_rebate_dollars: 936.0,
        rebate_per_hundred_shares_dollars: 0.13,
        rebate_per_hundred_shares_cents: 13.0,
        price_improved_orders_count: 3100,
        price_improvement_rate: 87.08,
        total_price_improvement_dollars: 5120.3,
        avg_price_improvement_per_order_dollars: 1.44,
        avg_price_improvement_per_share_cents: 0.71,
      },
    ],
  },
};

describe("SecRule606ReportView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders compliance header, executive KPIs, categories, and venues", async () => {
    vi.mocked(api.getSecRule606Report).mockResolvedValueOnce(mockReportData);

    render(<SecRule606ReportView />);

    await waitFor(() => {
      expect(screen.getByText("SEC Rule 606(a)(1) Order Routing & Execution Quality")).toBeInTheDocument();
    });

    expect(screen.getByText("14,250")).toBeInTheDocument(); // Total orders
    expect(screen.getByText("$412.5M")).toBeInTheDocument(); // Notional
    expect(screen.getByText("84.2%")).toBeInTheDocument(); // Price improvement rate
    expect(screen.getByText("CITADEL SECURITIES LLC")).toBeInTheDocument();
    expect(screen.getByText("VIRTU FINANCIAL BD LLC")).toBeInTheDocument();
  });

  it("triggers data reload when period is changed", async () => {
    vi.mocked(api.getSecRule606Report).mockResolvedValue(mockReportData);

    render(<SecRule606ReportView />);

    await waitFor(() => {
      expect(screen.getByText("SEC Rule 606(a)(1) Order Routing & Execution Quality")).toBeInTheDocument();
    });

    const periodSelect = screen.getByDisplayValue("2026 - Q1");
    fireEvent.change(periodSelect, { target: { value: "2025-Q4" } });

    await waitFor(() => {
      expect(api.getSecRule606Report).toHaveBeenCalledWith({
        year: 2025,
        quarter: 4,
        is_option: undefined,
      });
    });
  });

  it("handles api error gracefully", async () => {
    vi.mocked(api.getSecRule606Report).mockRejectedValueOnce(new Error("Compliance DB unavailable"));

    render(<SecRule606ReportView />);

    await waitFor(() => {
      expect(screen.getByText(/Compliance DB unavailable/i)).toBeInTheDocument();
    });
  });
});
