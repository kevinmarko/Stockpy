import React, { useState } from "react";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { theme } from "../../theme";
import type { ScenarioMatrixResponse, ScenarioMatrixCell, HistoricalScenarioPreset } from "../../api/types";

interface ScenarioHeatmapProps {
  initialData?: ScenarioMatrixResponse | null;
  onRefresh?: () => void;
}

type MetricMode = "pnl_dollar" | "pnl_pct" | "net_delta" | "net_gamma" | "net_theta" | "net_vega";

export const ScenarioHeatmap: React.FC<ScenarioHeatmapProps> = ({ initialData, onRefresh }) => {
  const query = useApi(() => api.getScenarioMatrix());
  const matrixData = initialData || query.data;

  const [selectedMetric, setSelectedMetric] = useState<MetricMode>("pnl_dollar");
  const [selectedTimeSlice, setSelectedTimeSlice] = useState<number>(0);
  const [hoveredCell, setHoveredCell] = useState<ScenarioMatrixCell | null>(null);
  const [selectedPreset, setSelectedPreset] = useState<HistoricalScenarioPreset | null>(null);

  if (query.loading && !matrixData) {
    return (
      <div style={{ padding: 24, textAlign: "center", color: theme.textSecondary, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
        Loading 2D Scenario Matrix & Stress Grid...
      </div>
    );
  }

  if (query.error && !matrixData) {
    return (
      <div style={{ padding: 24, textAlign: "center", color: theme.decline, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
        Failed to load scenario matrix: {query.error}
      </div>
    );
  }

  if (!matrixData) {
    return null;
  }

  const { spot_shifts, iv_shifts, time_slices, matrix, historical_scenarios, current_portfolio_value } = matrixData;

  // Filter cells by selected days_forward
  const currentCells = matrix.filter((c) => c.days_forward === selectedTimeSlice);

  // Compute metric ranges for dynamic color scaling
  const values = currentCells.map((c) => c[selectedMetric]);
  const minVal = Math.min(...values, 0);
  const maxVal = Math.max(...values, 0);

  const getCellColor = (val: number) => {
    if (selectedMetric === "pnl_dollar" || selectedMetric === "pnl_pct") {
      if (val > 0) {
        const ratio = maxVal > 0 ? Math.min(1, val / maxVal) : 0;
        return `rgba(16, 185, 129, ${0.15 + ratio * 0.55})`;
      } else if (val < 0) {
        const ratio = minVal < 0 ? Math.min(1, val / minVal) : 0;
        return `rgba(239, 68, 68, ${0.15 + ratio * 0.55})`;
      }
      return "rgba(255, 255, 255, 0.03)";
    } else if (selectedMetric === "net_delta") {
      if (val > 0) {
        const ratio = maxVal > 0 ? Math.min(1, val / maxVal) : 0;
        return `rgba(56, 189, 248, ${0.15 + ratio * 0.5})`;
      } else if (val < 0) {
        const ratio = minVal < 0 ? Math.min(1, val / minVal) : 0;
        return `rgba(245, 158, 11, ${0.15 + ratio * 0.5})`;
      }
      return "rgba(255, 255, 255, 0.03)";
    } else {
      // Gamma, Theta, Vega: neutral blue-violet ramp
      const absMax = Math.max(Math.abs(minVal), Math.abs(maxVal)) || 1;
      const ratio = Math.min(1, Math.abs(val) / absMax);
      return val >= 0
        ? `rgba(99, 102, 241, ${0.15 + ratio * 0.5})`
        : `rgba(239, 68, 68, ${0.15 + ratio * 0.5})`;
    }
  };

  const formatCellValue = (cell: ScenarioMatrixCell) => {
    const val = cell[selectedMetric];
    switch (selectedMetric) {
      case "pnl_dollar":
        return `${val >= 0 ? "+" : ""}$${val.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
      case "pnl_pct":
        return `${val >= 0 ? "+" : ""}${(val * 100).toFixed(1)}%`;
      case "net_delta":
        return `${val >= 0 ? "+" : ""}${val.toFixed(1)}Δ`;
      case "net_gamma":
        return `${val.toFixed(3)}Γ`;
      case "net_theta":
        return `${val >= 0 ? "+" : ""}$${val.toFixed(1)}Θ`;
      case "net_vega":
        return `${val >= 0 ? "+" : ""}$${val.toFixed(1)}𝒱`;
    }
  };

  return (
    <div
      style={{
        background: theme.surface,
        borderRadius: 8,
        border: `1px solid ${theme.border}`,
        padding: 20,
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      {/* Header controls */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
            <span>🗺️ Multi-Dimensional Scenario Matrix &amp; Stress Grid</span>
            <span style={{ fontSize: 11, color: theme.textSecondary, fontWeight: 400 }}>
              (Spot Price × IV Shock × Time Decay)
            </span>
          </h2>
          <div style={{ fontSize: 12, color: theme.textSecondary, marginTop: 4 }}>
            Mark-to-market stress test across Spot (±10%), Implied Volatility (±20%), and holding horizon.
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {/* Metric Selector */}
          <div style={{ display: "flex", background: theme.base, borderRadius: 6, padding: 2, border: `1px solid ${theme.border}` }}>
            {(
              [
                { key: "pnl_dollar", label: "P&L ($)" },
                { key: "pnl_pct", label: "P&L (%)" },
                { key: "net_delta", label: "Delta (Δ)" },
                { key: "net_gamma", label: "Gamma (Γ)" },
                { key: "net_theta", label: "Theta (Θ)" },
                { key: "net_vega", label: "Vega (𝒱)" },
              ] as const
            ).map((m) => (
              <button
                key={m.key}
                onClick={() => setSelectedMetric(m.key)}
                style={{
                  padding: "4px 8px",
                  fontSize: 11,
                  fontWeight: selectedMetric === m.key ? 600 : 400,
                  background: selectedMetric === m.key ? theme.surface3 : "transparent",
                  color: selectedMetric === m.key ? theme.textPrimary : theme.textSecondary,
                  border: "none",
                  borderRadius: 4,
                  cursor: "pointer",
                  transition: "all 0.15s ease",
                }}
              >
                {m.label}
              </button>
            ))}
          </div>

          <button
            onClick={() => {
              query.reload();
              if (onRefresh) onRefresh();
            }}
            disabled={query.loading}
            style={{
              padding: "6px 12px",
              background: theme.surface2,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 4,
              cursor: query.loading ? "not-allowed" : "pointer",
              fontSize: 12,
              fontWeight: 500,
            }}
          >
            {query.loading ? "Refreshing..." : "↻ Refresh"}
          </button>
        </div>
      </div>

      {/* Time Horizon Slider / Pills */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "10px 14px",
          background: theme.base,
          borderRadius: 6,
          border: `1px solid ${theme.border}`,
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 600, color: theme.textSecondary }}>Time Decay Slice:</span>
        <div style={{ display: "flex", gap: 8, flex: 1 }}>
          {time_slices.map((t) => (
            <button
              key={t}
              onClick={() => setSelectedTimeSlice(t)}
              style={{
                padding: "4px 12px",
                fontSize: 12,
                fontWeight: selectedTimeSlice === t ? 600 : 400,
                background: selectedTimeSlice === t ? theme.accent : theme.surface,
                color: selectedTimeSlice === t ? "#000" : theme.textPrimary,
                border: `1px solid ${selectedTimeSlice === t ? theme.accent : theme.border}`,
                borderRadius: 4,
                cursor: "pointer",
              }}
            >
              {t === 0 ? "Today (T+0)" : `+${t} Days (T+${t})`}
            </button>
          ))}
        </div>
        <div style={{ fontSize: 12, color: theme.textMuted }}>
          Current Portfolio Equity: ${current_portfolio_value.toLocaleString("en-US", { minimumFractionDigits: 2 })}
        </div>
      </div>

      {/* 2D Heatmap Grid Table */}
      <div style={{ overflowX: "auto" }}>
        <table
          style={{
            width: "100%",
            borderCollapse: "separate",
            borderSpacing: 3,
            textAlign: "center",
            fontSize: 11,
          }}
        >
          <thead>
            <tr>
              <th
                style={{
                  padding: "6px 8px",
                  color: theme.textMuted,
                  fontWeight: 600,
                  textAlign: "left",
                  fontSize: 10,
                }}
              >
                IV Shock \ Spot Shift
              </th>
              {spot_shifts.map((s) => (
                <th
                  key={s}
                  style={{
                    padding: "6px 8px",
                    color: s === 0 ? theme.accent : theme.textSecondary,
                    fontWeight: 600,
                    fontSize: 11,
                    background: s === 0 ? "rgba(56, 189, 248, 0.08)" : "transparent",
                    borderRadius: 4,
                  }}
                >
                  <div>{s > 0 ? `+${(s * 100).toFixed(0)}%` : `${(s * 100).toFixed(0)}%`}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {iv_shifts
              .slice()
              .reverse()
              .map((iv) => (
                <tr key={iv}>
                  <td
                    style={{
                      padding: "6px 10px",
                      color: iv === 0 ? theme.accent : theme.textSecondary,
                      fontWeight: 600,
                      textAlign: "left",
                      whiteSpace: "nowrap",
                      background: iv === 0 ? "rgba(56, 189, 248, 0.08)" : "transparent",
                      borderRadius: 4,
                    }}
                  >
                    {iv > 0 ? `+${(iv * 100).toFixed(0)}% IV` : `${(iv * 100).toFixed(0)}% IV`}
                  </td>
                  {spot_shifts.map((s) => {
                    const cell = currentCells.find(
                      (c) => Math.abs(c.spot_shift_pct - s) < 1e-4 && Math.abs(c.iv_shift_pct - iv) < 1e-4
                    );

                    if (!cell) {
                      return (
                        <td key={s} style={{ padding: "8px 6px", background: "rgba(255,255,255,0.02)", borderRadius: 4 }}>
                          —
                        </td>
                      );
                    }

                    const bg = getCellColor(cell[selectedMetric]);
                    const isBase = s === 0 && iv === 0;

                    return (
                      <td
                        key={s}
                        onMouseEnter={() => setHoveredCell(cell)}
                        onMouseLeave={() => setHoveredCell(null)}
                        style={{
                          padding: "8px 6px",
                          background: bg,
                          border: isBase ? `1px solid ${theme.accent}` : `1px solid transparent`,
                          borderRadius: 4,
                          fontWeight: isBase ? 700 : 500,
                          cursor: "pointer",
                          transition: "transform 0.1s ease, box-shadow 0.1s ease",
                          color: theme.textPrimary,
                        }}
                      >
                        {formatCellValue(cell)}
                      </td>
                    );
                  })}
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      {/* Hovered Cell Detail Strip */}
      {hoveredCell && (
        <div
          style={{
            padding: "10px 14px",
            background: theme.surface2,
            borderRadius: 6,
            border: `1px solid ${theme.borderStrong}`,
            display: "flex",
            flexWrap: "wrap",
            gap: 16,
            alignItems: "center",
            fontSize: 12,
          }}
        >
          <div>
            <span style={{ color: theme.textSecondary }}>Scenario: </span>
            <strong>
              Spot {hoveredCell.spot_shift_pct >= 0 ? "+" : ""}
              {(hoveredCell.spot_shift_pct * 100).toFixed(0)}%
              {hoveredCell.spot_price != null ? ` ($${hoveredCell.spot_price.toFixed(2)})` : ""}, IV{" "}
              {hoveredCell.iv_shift_pct >= 0 ? "+" : ""}
              {(hoveredCell.iv_shift_pct * 100).toFixed(0)}%, +{hoveredCell.days_forward}d
            </strong>
          </div>
          <div>
            <span style={{ color: theme.textSecondary }}>P&amp;L: </span>
            <strong style={{ color: hoveredCell.pnl_dollar >= 0 ? theme.growth : theme.decline }}>
              {hoveredCell.pnl_dollar >= 0 ? "+" : ""}${hoveredCell.pnl_dollar.toLocaleString()} (
              {(hoveredCell.pnl_pct * 100).toFixed(2)}%)
            </strong>
          </div>
          <div>
            <span style={{ color: theme.textSecondary }}>Portfolio Value: </span>
            <strong>${hoveredCell.portfolio_value.toLocaleString("en-US", { minimumFractionDigits: 2 })}</strong>
          </div>
          <div style={{ marginLeft: "auto", display: "flex", gap: 12, color: theme.textMuted }}>
            <span>Δ: {hoveredCell.net_delta.toFixed(1)}</span>
            <span>Γ: {hoveredCell.net_gamma.toFixed(4)}</span>
            <span>Θ: ${hoveredCell.net_theta.toFixed(1)}/d</span>
            <span>𝒱: ${hoveredCell.net_vega.toFixed(1)}</span>
          </div>
        </div>
      )}

      {/* Historical Stress Presets */}
      {historical_scenarios && historical_scenarios.length > 0 && (
        <div style={{ borderTop: `1px solid ${theme.border}`, paddingTop: 14 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: theme.textSecondary, marginBottom: 8 }}>
            ⚡ Historical Tail Stress Projections
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
            {historical_scenarios.map((sc) => {
              const isSelected = selectedPreset?.id === sc.id;
              return (
                <div
                  key={sc.id}
                  onClick={() => setSelectedPreset(isSelected ? null : sc)}
                  style={{
                    padding: "10px 12px",
                    background: isSelected ? "rgba(239, 68, 68, 0.12)" : theme.base,
                    border: `1px solid ${isSelected ? theme.decline : theme.border}`,
                    borderRadius: 6,
                    cursor: "pointer",
                    transition: "all 0.15s ease",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div style={{ fontWeight: 600, fontSize: 12, color: theme.textPrimary }}>{sc.name}</div>
                    <div
                      style={{
                        fontWeight: 700,
                        fontSize: 12,
                        color: sc.projected_pnl_dollar >= 0 ? theme.growth : theme.decline,
                      }}
                    >
                      {sc.projected_pnl_dollar >= 0
                        ? `+$${sc.projected_pnl_dollar.toLocaleString()}`
                        : `-$${Math.abs(sc.projected_pnl_dollar).toLocaleString()}`}
                    </div>
                  </div>
                  <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 3 }}>{sc.description}</div>
                  <div style={{ fontSize: 10, color: theme.decline, marginTop: 4, fontWeight: 500 }}>
                    Impact: {(sc.projected_pnl_pct * 100).toFixed(2)}% Equity Shock
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
