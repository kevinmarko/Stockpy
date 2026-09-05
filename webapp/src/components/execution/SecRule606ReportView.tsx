import React, { useState } from "react";
import {
  FileText,
  Download,
  Layers,
  Building2,
  RefreshCw,
} from "lucide-react";
import { theme } from "../../theme";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import type {
  SecRule606ReportResponse,
  SecRule606VenueRow,
} from "../../api/types";
import DemoDataBadge from "../DemoDataBadge";

export interface SecRule606ReportViewProps {
  initialYear?: number;
  initialQuarter?: number;
  className?: string;
}

/** SecRule606VenueRow after client-side normalization -- order_count,
 * executed_shares, and pct_of_total_shares are always real numbers here,
 * regardless of which raw backend shape (venues_overall vs. by_category)
 * the row originated from. */
type DisplayVenueRow = SecRule606VenueRow & {
  order_count: number;
  executed_shares: number;
  pct_of_total_shares: number;
};

export const SecRule606ReportView: React.FC<SecRule606ReportViewProps> = ({
  initialYear = 2026,
  initialQuarter = 1,
  className = "",
}) => {
  const [selectedYear, setSelectedYear] = useState<number>(initialYear);
  const [selectedQuarter, setSelectedQuarter] = useState<number>(initialQuarter);
  const [assetFilter, setAssetFilter] = useState<"all" | "equity" | "option">("all");
  const [activeCategoryTab, setActiveCategoryTab] = useState<string>("all");

  const isOptionParam =
    assetFilter === "all" ? undefined : assetFilter === "option" ? true : false;

  const { data: report, loading, error, reload } = useApi<SecRule606ReportResponse>(
    () =>
      api.getSecRule606Report({
        year: selectedYear,
        quarter: selectedQuarter,
        is_option: isOptionParam,
      }),
    [selectedYear, selectedQuarter, assetFilter]
  );

  const handleExportJson = () => {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `SEC_Rule_606_Report_${report.header.period}_${assetFilter}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (loading && !report) {
    return (
      <div style={{ padding: 24, textAlign: "center", color: theme.textSecondary }}>
        <RefreshCw size={20} className="icon-spin" style={{ display: "inline-block", marginRight: 8 }} />
        Loading SEC Rule 606 Execution Quality report...
      </div>
    );
  }

  if (error && !report) {
    return (
      <div style={{ padding: 16, color: theme.decline, background: "rgba(239,68,68,0.1)", borderRadius: 6 }}>
        Error loading SEC Rule 606 report: {String(error)}
      </div>
    );
  }

  const summary = report?.summary;
  const categories = report?.order_category_breakdown ? Object.values(report.order_category_breakdown) : [];

  // The backend's "venues_overall" rows use total_orders/total_shares while
  // its "by_category" rows use order_count/executed_shares and omit
  // pct_of_total_shares entirely -- see the SecRule606VenueRow comment in
  // api/types.ts. Normalize both shapes into one fully-populated row here
  // so every render below sees a real number, not a name it doesn't have.
  const normalizeVenueRow = (v: SecRule606VenueRow, totalSharesPeriod: number): DisplayVenueRow => {
    const executedShares = v.executed_shares ?? v.total_shares ?? 0;
    return {
      ...v,
      order_count: v.order_count ?? v.total_orders ?? 0,
      executed_shares: executedShares,
      pct_of_total_shares:
        v.pct_of_total_shares ??
        (totalSharesPeriod > 0 ? (executedShares / totalSharesPeriod) * 100 : 0),
    };
  };

  let venueRows: DisplayVenueRow[] = [];
  if (report?.venue_breakdown) {
    const totalSharesPeriod = summary?.total_shares || 0;
    const rawRows =
      activeCategoryTab === "all"
        ? report.venue_breakdown.venues_overall || []
        : report.venue_breakdown.by_category?.[activeCategoryTab] || [];
    venueRows = rawRows.map((v) => normalizeVenueRow(v, totalSharesPeriod));
  }

  return (
    <div
      className={`sec-606-report-container ${className}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 16,
        background: theme.base,
        color: theme.textPrimary,
        borderRadius: 8,
        padding: 16,
        border: `1px solid ${theme.border}`,
      }}
    >
      {/* Header Bar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 12,
          borderBottom: `1px solid ${theme.border}`,
          paddingBottom: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 8,
              background: "rgba(56, 189, 248, 0.15)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: theme.accent,
            }}
          >
            <FileText size={20} />
          </div>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <h2 style={{ margin: 0, fontSize: "1.15rem", fontWeight: 700 }}>
                SEC Rule 606(a)(1) Order Routing & Execution Quality
              </h2>
              <DemoDataBadge />
            </div>
            <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
              Quarterly Public Regulatory Disclosure • Venue Routing Percentages • PFOF / Net Rebate & Price Improvement
            </div>
          </div>
        </div>

        {/* Filter Controls & Export */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {/* Period Selector */}
          <select
            value={`${selectedYear}-Q${selectedQuarter}`}
            onChange={(e) => {
              const [y, q] = e.target.value.split("-Q");
              setSelectedYear(Number(y));
              setSelectedQuarter(Number(q));
            }}
            style={{
              padding: "6px 10px",
              background: theme.surface,
              color: theme.textPrimary,
              border: `1px solid ${theme.border}`,
              borderRadius: 4,
              fontSize: "0.8rem",
            }}
          >
            <option value="2026-Q1">2026 - Q1</option>
            <option value="2025-Q4">2025 - Q4</option>
            <option value="2025-Q3">2025 - Q3</option>
            <option value="2025-Q2">2025 - Q2</option>
          </select>

          {/* Asset Type Filter */}
          <select
            value={assetFilter}
            onChange={(e) => setAssetFilter(e.target.value as any)}
            style={{
              padding: "6px 10px",
              background: theme.surface,
              color: theme.textPrimary,
              border: `1px solid ${theme.border}`,
              borderRadius: 4,
              fontSize: "0.8rem",
            }}
          >
            <option value="all">All Securities</option>
            <option value="equity">Equities Only</option>
            <option value="option">Listed Options Only</option>
          </select>

          <button
            onClick={() => reload()}
            style={{
              background: theme.surface,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 4,
              padding: "6px 12px",
              fontSize: "0.75rem",
              display: "flex",
              alignItems: "center",
              gap: 4,
              cursor: "pointer",
            }}
          >
            <RefreshCw size={13} />
            Refresh
          </button>

          <button
            onClick={handleExportJson}
            disabled={!report}
            style={{
              background: theme.accent,
              color: "#000",
              border: "none",
              borderRadius: 4,
              padding: "6px 12px",
              fontSize: "0.75rem",
              fontWeight: 700,
              display: "flex",
              alignItems: "center",
              gap: 4,
              cursor: report ? "pointer" : "not-allowed",
            }}
          >
            <Download size={13} />
            Export SEC 606 JSON
          </button>
        </div>
      </div>

      {/* Executive Summary Cards */}
      {summary && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
            gap: 10,
          }}
        >
          <SummaryCard
            label="Total Routed Orders"
            value={summary.total_orders.toLocaleString()}
            sub={`Shares: ${(summary.total_shares / 1e6).toFixed(2)}M`}
            color={theme.accent}
          />
          <SummaryCard
            label="Total Notional Volume"
            value={`$${(summary.total_notional / 1e6).toFixed(1)}M`}
            sub="Executed Principal"
            color={theme.textPrimary}
          />
          <SummaryCard
            label="Price Improvement Rate"
            value={`${summary.overall_price_improvement_rate.toFixed(1)}%`}
            sub={`${summary.price_improved_orders_count.toLocaleString()} Orders`}
            color={theme.growth}
          />
          <SummaryCard
            label="Total Price Improvement"
            value={`$${summary.total_price_improvement_dollars.toLocaleString()}`}
            sub={`Avg: $${summary.overall_avg_price_improvement_per_order_dollars.toFixed(2)}/ord`}
            color={theme.growth}
          />
          <SummaryCard
            label="Net PFOF / Maker Rebates"
            value={`$${summary.total_net_rebate_dollars.toLocaleString()}`}
            sub={`${summary.overall_rebate_per_hundred_shares_cents.toFixed(1)}¢ / 100 shares`}
            color={theme.textPrimary}
          />
        </div>
      )}

      {/* Order Category Breakdown Table */}
      <div style={{ background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 6, padding: 12 }}>
        <div style={{ fontSize: "0.85rem", fontWeight: 700, marginBottom: 10, display: "flex", alignItems: "center", gap: 6 }}>
          <Layers size={15} color={theme.accent} />
          1. Customer Order Category Breakdown (SEC Rule 606(a)(1)(i))
        </div>

        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.75rem" }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${theme.border}`, color: theme.textSecondary, textAlign: "left" }}>
                <th style={{ padding: "6px 8px" }}>Order Category</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>Total Orders</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>% Orders</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>Executed Shares</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>% Shares</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>Net PFOF / Rebate</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>¢ / 100 Shares</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>Price Imp. Rate</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>Total Price Imp.</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>Avg Imp. / Order</th>
              </tr>
            </thead>
            <tbody>
              {categories.map((cat) => (
                <tr key={cat.category} style={{ borderBottom: `1px solid rgba(255,255,255,0.05)` }}>
                  <td style={{ padding: "6px 8px", fontWeight: 700, textTransform: "capitalize" }}>
                    {cat.category.replace(/_/g, " ")}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>{cat.order_count.toLocaleString()}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right", color: theme.accent, fontWeight: 600 }}>
                    {cat.pct_of_total_orders.toFixed(2)}%
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>{cat.executed_shares.toLocaleString()}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>{cat.pct_of_total_shares.toFixed(2)}%</td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>${cat.net_fee_rebate_dollars.toFixed(2)}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>
                    {cat.rebate_per_hundred_shares_cents.toFixed(1)}¢
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", color: theme.growth, fontWeight: 700 }}>
                    {cat.price_improvement_rate.toFixed(1)}%
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", color: theme.growth }}>
                    ${cat.total_price_improvement_dollars.toFixed(2)}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>
                    ${cat.avg_price_improvement_per_order_dollars.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Venue Breakdown Section */}
      <div style={{ background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 6, padding: 12 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 10,
            marginBottom: 10,
          }}
        >
          <div style={{ fontSize: "0.85rem", fontWeight: 700, display: "flex", alignItems: "center", gap: 6 }}>
            <Building2 size={15} color={theme.growth} />
            2. Execution Venue Routing & Price Improvement Breakdown (SEC Rule 606(a)(1)(ii))
          </div>

          {/* Category Filter Tabs */}
          <div style={{ display: "flex", gap: 4 }}>
            {[
              { id: "all", label: "All Venues" },
              { id: "market", label: "Market" },
              { id: "marketable_limit", label: "Mkt Limit" },
              { id: "non_marketable_limit", label: "Non-Mkt Limit" },
              { id: "other", label: "Other" },
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveCategoryTab(tab.id)}
                style={{
                  padding: "4px 8px",
                  fontSize: "0.72rem",
                  borderRadius: 4,
                  border: "none",
                  background: activeCategoryTab === tab.id ? theme.accent : theme.base,
                  color: activeCategoryTab === tab.id ? "#000" : theme.textSecondary,
                  fontWeight: activeCategoryTab === tab.id ? 700 : 400,
                  cursor: "pointer",
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Venue Market Share Visualizer Bar */}
        {venueRows.length > 0 && (
          <div style={{ marginBottom: 12, background: theme.base, padding: 10, borderRadius: 6, border: `1px solid ${theme.border}` }}>
            <div style={{ fontSize: "0.72rem", color: theme.textSecondary, marginBottom: 6 }}>
              Venue Routing Market Share (% Total Order Flow):
            </div>
            <div style={{ display: "flex", height: 16, borderRadius: 4, overflow: "hidden", gap: 2 }}>
              {venueRows.map((v, idx) => {
                const colors = ["#38bdf8", "#10b981", "#818cf8", "#f59e0b", "#a855f7", "#ec4899"];
                const color = colors[idx % colors.length];
                return (
                  <div
                    key={v.venue}
                    style={{
                      width: `${Math.max(2, v.pct_of_total_orders)}%`,
                      background: color,
                      height: "100%",
                    }}
                    title={`${v.venue}: ${v.pct_of_total_orders}%`}
                  />
                );
              })}
            </div>
          </div>
        )}

        {/* Venue Routing Table */}
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.75rem" }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${theme.border}`, color: theme.textSecondary, textAlign: "left" }}>
                <th style={{ padding: "6px 8px" }}>Execution Venue</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>Routed Orders</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>% Total Orders</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>Executed Shares</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>% Shares</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>Net PFOF / Rebate</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>¢ / 100 Shares</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>Price Imp. Rate</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>Total Price Imp.</th>
                <th style={{ padding: "6px 8px", textAlign: "right" }}>Avg Imp. / Share</th>
              </tr>
            </thead>
            <tbody>
              {venueRows.map((venue) => (
                <tr key={venue.venue} style={{ borderBottom: `1px solid rgba(255,255,255,0.05)` }}>
                  <td style={{ padding: "6px 8px", fontWeight: 700 }}>{venue.venue}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>{venue.order_count.toLocaleString()}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right", color: theme.accent, fontWeight: 600 }}>
                    {venue.pct_of_total_orders.toFixed(2)}%
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>{venue.executed_shares.toLocaleString()}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>{venue.pct_of_total_shares.toFixed(2)}%</td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>${venue.net_fee_rebate_dollars.toFixed(2)}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>
                    {venue.rebate_per_hundred_shares_cents.toFixed(1)}¢
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", color: theme.growth, fontWeight: 700 }}>
                    {venue.price_improvement_rate.toFixed(1)}%
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", color: theme.growth }}>
                    ${venue.total_price_improvement_dollars.toFixed(2)}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>
                    {venue.avg_price_improvement_per_share_cents.toFixed(2)}¢
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

interface SummaryCardProps {
  label: string;
  value: string;
  sub: string;
  color?: string;
}

const SummaryCard: React.FC<SummaryCardProps> = ({ label, value, sub, color = theme.textPrimary }) => (
  <div
    style={{
      background: theme.surface,
      border: `1px solid ${theme.border}`,
      borderRadius: 6,
      padding: "10px 12px",
      display: "flex",
      flexDirection: "column",
      gap: 2,
    }}
  >
    <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>{label}</div>
    <div style={{ fontSize: "1.15rem", fontWeight: 800, color }}>{value}</div>
    <div style={{ fontSize: "0.68rem", color: theme.textSecondary }}>{sub}</div>
  </div>
);
