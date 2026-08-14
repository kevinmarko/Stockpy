import React, { useState, useEffect } from "react";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { theme } from "../../theme";
import type {
  SorAnalysisResponse,
  LeggingSimulationResponse,
  SorLeg,
} from "../../api/types";

interface SmartOrderRouterViewProps {
  initialSymbol?: string;
  spotPrice?: number;
  onClose?: () => void;
  onRouteOrder?: (policy: string, details: SorAnalysisResponse) => void;
}

export const SmartOrderRouterView: React.FC<SmartOrderRouterViewProps> = ({
  initialSymbol = "SPY",
  spotPrice = 546.50,
  onClose,
  onRouteOrder,
}) => {
  const [symbol] = useState<string>(initialSymbol);
  const [spreadPreset, setSpreadPreset] = useState<"BULL_PUT" | "BEAR_CALL" | "IRON_CONDOR" | "STRANGLE">("BULL_PUT");
  const [latencyMs, setLatencyMs] = useState<number>(250);
  const [selectedRoute, setSelectedRoute] = useState<"COB_NET_PACKAGE" | "LEG_PASSIVE_FIRST" | "SPLIT_DIRECT">("LEG_PASSIVE_FIRST");
  const [routingStatus, setRoutingStatus] = useState<string | null>(null);

  // Generate legs based on preset and spot price
  const getPresetLegs = (preset: string, spot: number): SorLeg[] => {
    const roundedSpot = Math.round(spot);
    switch (preset) {
      case "BULL_PUT":
        return [
          { strike: roundedSpot - 5, option_type: "PUT", action: "SELL", bid: 3.10, ask: 3.25, mid: 3.175 },
          { strike: roundedSpot - 10, option_type: "PUT", action: "BUY", bid: 1.80, ask: 1.95, mid: 1.875 },
        ];
      case "BEAR_CALL":
        return [
          { strike: roundedSpot + 5, option_type: "CALL", action: "SELL", bid: 2.90, ask: 3.05, mid: 2.975 },
          { strike: roundedSpot + 10, option_type: "CALL", action: "BUY", bid: 1.65, ask: 1.80, mid: 1.725 },
        ];
      case "IRON_CONDOR":
        return [
          { strike: roundedSpot - 10, option_type: "PUT", action: "BUY", bid: 1.20, ask: 1.30, mid: 1.25 },
          { strike: roundedSpot - 5, option_type: "PUT", action: "SELL", bid: 2.80, ask: 2.95, mid: 2.875 },
          { strike: roundedSpot + 5, option_type: "CALL", action: "SELL", bid: 2.70, ask: 2.85, mid: 2.775 },
          { strike: roundedSpot + 10, option_type: "CALL", action: "BUY", bid: 1.15, ask: 1.25, mid: 1.20 },
        ];
      case "STRANGLE":
        return [
          { strike: roundedSpot - 5, option_type: "PUT", action: "BUY", bid: 3.10, ask: 3.25, mid: 3.175 },
          { strike: roundedSpot + 5, option_type: "CALL", action: "BUY", bid: 2.90, ask: 3.05, mid: 2.975 },
        ];
      default:
        return [
          { strike: roundedSpot - 5, option_type: "PUT", action: "SELL", bid: 3.10, ask: 3.25, mid: 3.175 },
          { strike: roundedSpot - 10, option_type: "PUT", action: "BUY", bid: 1.80, ask: 1.95, mid: 1.875 },
        ];
    }
  };

  const legs = getPresetLegs(spreadPreset, spotPrice);

  const sorAnalysis = useApi<SorAnalysisResponse>(
    () =>
      api.analyzeOptionsRouting({
        symbol,
        spot_price: spotPrice,
        legs,
        latency_ms: latencyMs,
      }),
    [symbol, spreadPreset, latencyMs, spotPrice]
  );

  const leggingSim = useApi<LeggingSimulationResponse>(
    () =>
      api.simulateOptionsLegging({
        symbol,
        spot_price: spotPrice,
        latency_seconds: latencyMs / 1000,
        num_simulations: 1000,
        legs,
      }),
    [symbol, spreadPreset, latencyMs, spotPrice]
  );

  // Auto-sync selected route recommendation from backend
  useEffect(() => {
    if (sorAnalysis.data?.recommended_route) {
      setSelectedRoute(sorAnalysis.data.recommended_route);
    }
  }, [sorAnalysis.data?.recommended_route]);

  const handleRouteOrder = () => {
    if (!sorAnalysis.data) return;
    const policyName =
      selectedRoute === "COB_NET_PACKAGE"
        ? "Complex Order Book (Atomic Net Package)"
        : selectedRoute === "LEG_PASSIVE_FIRST"
        ? "Synthetic Legging (Passive First)"
        : "Direct Leg Split Routing";

    setRoutingStatus(
      `Order routed via ${policyName}. Sent multi-leg package for ${symbol} @ $${sorAnalysis.data.cob_net_price.toFixed(2)} with est. savings $${sorAnalysis.data.expected_savings.toFixed(2)}.`
    );

    if (onRouteOrder) {
      onRouteOrder(selectedRoute, sorAnalysis.data);
    }
  };

  const sor = sorAnalysis.data;
  const sim = leggingSim.data;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, color: theme.textPrimary }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: 12,
          padding: "16px",
          background: theme.surface,
          borderRadius: 12,
          border: `1px solid ${theme.border}`,
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: "1.3rem", fontWeight: 700 }}>
              🔀 Multi-Leg Smart Order Router (SOR) & Legging Desk
            </span>
            <span
              style={{
                fontSize: "0.75rem",
                padding: "2px 8px",
                borderRadius: 10,
                background: `${theme.accent}25`,
                color: theme.accent,
                fontWeight: 600,
              }}
            >
              Phase 18
            </span>
          </div>
          <div style={{ fontSize: "0.85rem", color: theme.textSecondary, marginTop: 4 }}>
            Complex Order Book (COB) atomic package pricing vs. Synthetic Legging spread capture. Evaluates adverse selection and hung leg hazard.
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            onClick={() => {
              sorAnalysis.reload();
              leggingSim.reload();
            }}
            style={{
              padding: "6px 12px",
              background: theme.surface2,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 8,
              fontSize: "0.85rem",
              cursor: "pointer",
            }}
          >
            ↻ Re-Analyze
          </button>
          {onClose && (
            <button
              onClick={onClose}
              style={{
                padding: "6px 12px",
                background: theme.surface2,
                border: `1px solid ${theme.border}`,
                color: theme.textSecondary,
                borderRadius: 8,
                fontSize: "0.85rem",
                cursor: "pointer",
              }}
            >
              ✕ Close
            </button>
          )}
        </div>
      </div>

      {/* Routing Success / Execution Toast */}
      {routingStatus && (
        <div
          style={{
            padding: "12px 16px",
            borderRadius: 8,
            background: `${theme.growth}20`,
            border: `1px solid ${theme.growth}`,
            color: theme.growth,
            fontSize: "0.9rem",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>⚡ {routingStatus}</span>
          <button
            onClick={() => setRoutingStatus(null)}
            style={{ background: "transparent", border: "none", color: "inherit", cursor: "pointer", fontWeight: 700 }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Preset Strategy & Latency Controller Bar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 16,
          padding: "12px 16px",
          background: theme.surface2,
          borderRadius: 8,
          border: `1px solid ${theme.border}`,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: "0.85rem", color: theme.textSecondary, fontWeight: 600 }}>Strategy Preset:</span>
          {(
            [
              { id: "BULL_PUT", label: "Bull Put Spread" },
              { id: "BEAR_CALL", label: "Bear Call Spread" },
              { id: "IRON_CONDOR", label: "Iron Condor" },
              { id: "STRANGLE", label: "Long Strangle" },
            ] as const
          ).map((p) => (
            <button
              key={p.id}
              onClick={() => setSpreadPreset(p.id)}
              style={{
                padding: "6px 12px",
                borderRadius: 6,
                border: `1px solid ${spreadPreset === p.id ? theme.accent : theme.border}`,
                background: spreadPreset === p.id ? theme.accent : theme.surface,
                color: spreadPreset === p.id ? "#000" : theme.textPrimary,
                fontSize: "0.8rem",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {p.label}
            </button>
          ))}
        </div>

        {/* Latency Slider */}
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: "0.85rem", color: theme.textSecondary, fontWeight: 600 }}>
            Execution Latency: <b style={{ color: theme.accent }}>{latencyMs} ms</b>
          </span>
          <input
            type="range"
            min={50}
            max={3000}
            step={50}
            value={latencyMs}
            onChange={(e) => setLatencyMs(Number(e.target.value))}
            style={{ width: 140, cursor: "pointer", accentColor: theme.accent }}
          />
        </div>
      </div>

      {/* Main SOR Cards Grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16 }}>
        {/* Panel 1: Complex Order Book vs Synthetic Legging Comparison */}
        <div
          style={{
            background: theme.surface,
            borderRadius: 12,
            border: `1px solid ${theme.border}`,
            padding: 16,
            display: "flex",
            flexDirection: "column",
            gap: 14,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: "1rem", fontWeight: 700 }}>Execution Policy & Route Comparison</span>
            {sor && (
              <span
                style={{
                  fontSize: "0.75rem",
                  padding: "3px 8px",
                  borderRadius: 6,
                  background: `${theme.growth}20`,
                  color: theme.growth,
                  fontWeight: 700,
                }}
              >
                Recommended: {sor.recommended_route.replace(/_/g, " ")}
              </span>
            )}
          </div>

          {/* Pricing Comparison Grid */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
            <div style={{ background: theme.surface2, padding: 12, borderRadius: 8, textAlign: "center" }}>
              <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>COB Net Mid Price</div>
              <div style={{ fontSize: "1.2rem", fontWeight: 700, marginTop: 4 }}>
                ${sor?.cob_net_price.toFixed(2) ?? "—"}
              </div>
              <div style={{ fontSize: "0.7rem", color: theme.textMuted, marginTop: 2 }}>Atomic Package</div>
            </div>

            <div style={{ background: theme.surface2, padding: 12, borderRadius: 8, textAlign: "center" }}>
              <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>Natural Bid/Ask</div>
              <div style={{ fontSize: "1.2rem", fontWeight: 700, marginTop: 4, color: theme.decline }}>
                ${sor?.cob_natural_price.toFixed(2) ?? "—"}
              </div>
              <div style={{ fontSize: "0.7rem", color: theme.textMuted, marginTop: 2 }}>Worst-case cross</div>
            </div>

            <div style={{ background: theme.surface2, padding: 12, borderRadius: 8, textAlign: "center" }}>
              <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>Synthetic Legging</div>
              <div style={{ fontSize: "1.2rem", fontWeight: 700, marginTop: 4, color: theme.growth }}>
                ${sor?.synthetic_net_price.toFixed(2) ?? "—"}
              </div>
              <div style={{ fontSize: "0.7rem", color: theme.growth, marginTop: 2 }}>
                +${sor?.expected_savings.toFixed(2) ?? "0.00"} Edge
              </div>
            </div>
          </div>

          {/* Rationale Banner */}
          {sor?.rationale && (
            <div
              style={{
                fontSize: "0.8rem",
                color: theme.textPrimary,
                background: theme.surface2,
                padding: "10px 12px",
                borderRadius: 6,
                borderLeft: `3px solid ${theme.accent}`,
              }}
            >
              💡 <b>Router Policy Rationale:</b> {sor.rationale}
            </div>
          )}

          {/* Legs Breakdown & Sequence Table */}
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={{ fontSize: "0.85rem", fontWeight: 700, color: theme.textSecondary }}>
              Execution Sequence & Leg Fill Priority
            </span>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8rem", textAlign: "left" }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${theme.border}`, color: theme.textSecondary }}>
                    <th style={{ padding: "6px 8px" }}>Leg</th>
                    <th style={{ padding: "6px 8px" }}>Action</th>
                    <th style={{ padding: "6px 8px", textAlign: "right" }}>Bid / Ask</th>
                    <th style={{ padding: "6px 8px", textAlign: "right" }}>Mid</th>
                    <th style={{ padding: "6px 8px" }}>Priority</th>
                    <th style={{ padding: "6px 8px" }}>Fill Style</th>
                  </tr>
                </thead>
                <tbody>
                  {sor?.legs_breakdown.map((l, i) => (
                    <tr key={i} style={{ borderBottom: `1px solid ${theme.borderStrong}` }}>
                      <td style={{ padding: "6px 8px", fontWeight: 600 }}>
                        {l.strike} {l.option_type}
                      </td>
                      <td style={{ padding: "6px 8px" }}>
                        <span
                          style={{
                            padding: "1px 6px",
                            borderRadius: 4,
                            fontSize: "0.7rem",
                            fontWeight: 700,
                            background: l.action === "BUY" ? `${theme.growth}20` : `${theme.decline}20`,
                            color: l.action === "BUY" ? theme.growth : theme.decline,
                          }}
                        >
                          {l.action}
                        </span>
                      </td>
                      <td style={{ padding: "6px 8px", textAlign: "right" }}>
                        ${l.bid.toFixed(2)} - ${l.ask.toFixed(2)}
                      </td>
                      <td style={{ padding: "6px 8px", textAlign: "right", fontWeight: 600 }}>
                        ${l.mid.toFixed(2)}
                      </td>
                      <td style={{ padding: "6px 8px" }}>
                        <span
                          style={{
                            padding: "1px 6px",
                            borderRadius: 4,
                            fontSize: "0.7rem",
                            background: l.fill_priority === 1 ? `${theme.accent}25` : theme.surface3,
                            color: l.fill_priority === 1 ? theme.accent : theme.textSecondary,
                            fontWeight: 600,
                          }}
                        >
                          Priority #{l.fill_priority}
                        </span>
                      </td>
                      <td style={{ padding: "6px 8px", fontWeight: 600, color: l.fill_style === "PASSIVE" ? theme.growth : theme.caution }}>
                        {l.fill_style}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Panel 2: Legging Hazard & Monte Carlo Simulation Chart */}
        <div
          style={{
            background: theme.surface,
            borderRadius: 12,
            border: `1px solid ${theme.border}`,
            padding: 16,
            display: "flex",
            flexDirection: "column",
            gap: 14,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: "1rem", fontWeight: 700 }}>
              Expected Edge vs Hung Leg Hazard ({sim?.num_simulations ?? 1000} MC Sims)
            </span>
            <span style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
              Latency: {latencyMs}ms ({(latencyMs / 1000).toFixed(2)}s)
            </span>
          </div>

          {/* Hazard KPIs */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
            <div style={{ background: theme.surface2, padding: 10, borderRadius: 8, textAlign: "center" }}>
              <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>Hung Leg Probability</div>
              <div
                style={{
                  fontSize: "1.1rem",
                  fontWeight: 700,
                  marginTop: 2,
                  color: (sim?.hung_leg_rate ?? 0) > 0.1 ? theme.decline : theme.caution,
                }}
              >
                {sim ? `${(sim.hung_leg_rate * 100).toFixed(1)}%` : "—"}
              </div>
            </div>
            <div style={{ background: theme.surface2, padding: 10, borderRadius: 8, textAlign: "center" }}>
              <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>Expected Net Edge</div>
              <div style={{ fontSize: "1.1rem", fontWeight: 700, marginTop: 2, color: theme.growth }}>
                {sim ? `+$${sim.expected_edge_dollars.toFixed(2)}` : "—"}
              </div>
            </div>
            <div style={{ background: theme.surface2, padding: 10, borderRadius: 8, textAlign: "center" }}>
              <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>P95 Adverse Selection</div>
              <div style={{ fontSize: "1.1rem", fontWeight: 700, marginTop: 2, color: theme.decline }}>
                {sim ? `$${sim.p95_adverse_selection.toFixed(2)}` : "—"}
              </div>
            </div>
          </div>

          {/* Latency Curve Visualization */}
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", color: theme.textSecondary }}>
              <span>Latency Decay Curve</span>
              <span>Green: Edge ($) | Orange: Hazard (%)</span>
            </div>
            <div
              style={{
                height: 110,
                display: "flex",
                alignItems: "flex-end",
                gap: 6,
                background: theme.surface2,
                padding: "12px 10px 6px 10px",
                borderRadius: 8,
                border: `1px solid ${theme.borderStrong}`,
              }}
            >
              {sim?.latency_curve.map((pt) => {
                const isSelected = Math.abs(pt.latency_ms - latencyMs) < 150;
                const edgeHeight = Math.max(8, (pt.expected_edge / 35) * 80);
                const hazardHeight = Math.max(8, (pt.hung_leg_rate / 0.3) * 80);

                return (
                  <div
                    key={pt.latency_ms}
                    onClick={() => setLatencyMs(pt.latency_ms)}
                    style={{
                      flex: 1,
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      gap: 2,
                      cursor: "pointer",
                      opacity: isSelected ? 1 : 0.65,
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "flex-end", gap: 2, width: "100%", height: 80 }}>
                      {/* Edge Bar */}
                      <div
                        style={{
                          flex: 1,
                          height: `${edgeHeight}%`,
                          background: theme.growth,
                          borderRadius: "2px 2px 0 0",
                        }}
                      />
                      {/* Hazard Bar */}
                      <div
                        style={{
                          flex: 1,
                          height: `${hazardHeight}%`,
                          background: theme.caution,
                          borderRadius: "2px 2px 0 0",
                        }}
                      />
                    </div>
                    <span style={{ fontSize: "0.65rem", color: isSelected ? theme.accent : theme.textSecondary, fontWeight: 600 }}>
                      {pt.latency_ms}ms
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* PnL Distribution Histogram */}
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
              Execution PnL Distribution ($/package edge captured)
            </span>
            <div
              style={{
                height: 80,
                display: "flex",
                alignItems: "flex-end",
                gap: 3,
                background: theme.surface2,
                padding: "8px 6px 4px 6px",
                borderRadius: 8,
              }}
            >
              {sim?.pnl_distribution.map((bin, i) => {
                const isLoss = bin.bin_edge < 0;
                const barHeight = Math.max(6, bin.probability * 300);

                return (
                  <div
                    key={i}
                    title={`$${bin.bin_edge}: ${(bin.probability * 100).toFixed(1)}%`}
                    style={{
                      flex: 1,
                      height: `${barHeight}%`,
                      background: isLoss ? theme.decline : theme.growth,
                      borderRadius: "2px 2px 0 0",
                      opacity: 0.85,
                    }}
                  />
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {/* 1-Click Execution Routing Submission Card */}
      <div
        style={{
          background: theme.surface,
          borderRadius: 12,
          border: `1px solid ${theme.border}`,
          padding: 16,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 16,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span style={{ fontSize: "0.9rem", fontWeight: 700 }}>Select Routing Policy:</span>
          <div style={{ display: "flex", gap: 6 }}>
            {(
              [
                { id: "COB_NET_PACKAGE", label: "🔒 COB Atomic Package" },
                { id: "LEG_PASSIVE_FIRST", label: "⚡ Synthetic Legging (Passive First)" },
                { id: "SPLIT_DIRECT", label: "🔀 Direct Split" },
              ] as const
            ).map((r) => (
              <button
                key={r.id}
                onClick={() => setSelectedRoute(r.id)}
                style={{
                  padding: "8px 14px",
                  borderRadius: 6,
                  border: `1px solid ${selectedRoute === r.id ? theme.accent : theme.border}`,
                  background: selectedRoute === r.id ? theme.accent : theme.surface2,
                  color: selectedRoute === r.id ? "#000" : theme.textPrimary,
                  fontWeight: 600,
                  fontSize: "0.85rem",
                  cursor: "pointer",
                }}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>

        <button
          onClick={handleRouteOrder}
          style={{
            background: theme.growth,
            color: "#000",
            border: "none",
            borderRadius: 8,
            padding: "10px 24px",
            fontWeight: 700,
            fontSize: "0.95rem",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 8,
            transition: "all 0.15s ease",
          }}
        >
          🚀 Route & Execute via {selectedRoute.replace(/_/g, " ")}
        </button>
      </div>
    </div>
  );
};
