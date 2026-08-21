import React, { useState } from "react";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { useMutation } from "../../hooks/useMutation";
import { theme, alpha } from "../../theme";
import type {
  DispersionOpportunity,
  DispersionBasketResponse,
  DispersionExecutionResult,
} from "../../api/types";

interface DispersionScannerProps {
  initialIndex?: string;
  onTradeExecuted?: (result: DispersionExecutionResult) => void;
  onSelectTicker?: (symbol: string) => void;
  onClose?: () => void;
}

export const DispersionScanner: React.FC<DispersionScannerProps> = ({
  initialIndex,
  onTradeExecuted,
  onSelectTicker,
  onClose,
}) => {
  const [selectedIndexSymbol, setSelectedIndexSymbol] = useState<string>(initialIndex || "QQQ");
  const [filterLongDispersionOnly, setFilterLongDispersionOnly] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [isExecuting, setIsExecuting] = useState(false);
  const [statusMessage, setStatusMessage] = useState<{ text: string; type: "success" | "error" } | null>(null);

  const query = useApi<DispersionBasketResponse>(
    () => api.getDispersionOpportunities(),
    []
  );

  const executeMutation = useMutation((opp: DispersionOpportunity) =>
    api.executeDispersionBasket({
      opportunity_id: opp.id,
      index_symbol: opp.index_symbol,
      regime: opp.regime,
    })
  );

  const opportunities: DispersionOpportunity[] = query.data?.opportunities || [];

  const handleExecuteBasket = async (opp: DispersionOpportunity) => {
    setIsExecuting(true);
    setStatusMessage(null);
    try {
      const res = await executeMutation.run(opp);
      if (res && res.ok) {
        setStatusMessage({
          text: res.message || `Successfully executed Dispersion Basket on ${opp.index_symbol}. (${res.legs_count} legs executed)`,
          type: "success",
        });
        if (onTradeExecuted) {
          onTradeExecuted(res);
        }
      } else {
        setStatusMessage({
          text: executeMutation.error || `Failed to execute dispersion basket on ${opp.index_symbol}.`,
          type: "error",
        });
      }
    } finally {
      setIsExecuting(false);
    }
  };

  const filteredOpportunities = opportunities.filter((o) => {
    if (filterLongDispersionOnly && o.regime !== "LONG_DISPERSION") return false;
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toUpperCase();
      const matchIndex = o.index_symbol.toUpperCase().includes(q);
      const matchConst = o.constituents.some((c) => c.symbol.toUpperCase().includes(q));
      if (!matchIndex && !matchConst) return false;
    }
    return true;
  });

  const activeOpp =
    filteredOpportunities.find((o) => o.index_symbol.toUpperCase() === selectedIndexSymbol.toUpperCase()) ||
    filteredOpportunities[0] ||
    opportunities[0];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, color: theme.textPrimary }}>
      {/* Header & Controls */}
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
            <span style={{ fontSize: "1.3rem", fontWeight: 700 }}>🌐 Options Dispersion & Implied Correlation Scanner</span>
            <span
              style={{
                fontSize: "0.75rem",
                padding: "2px 8px",
                borderRadius: 10,
                background: alpha(theme.accent, "25"),
                color: theme.accent,
                fontWeight: 600,
              }}
            >
              Phase 15
            </span>
          </div>
          <div style={{ fontSize: "0.85rem", color: theme.textSecondary, marginTop: 4 }}>
            Decomposes index variance into weighted constituent straddles to harvest correlation risk premia (Δρ = ρ_implied − ρ_realized).
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button
            onClick={() => query.reload()}
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
            ↻ Refresh
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

      {/* Status Message */}
      {statusMessage && (
        <div
          style={{
            padding: "12px 16px",
            borderRadius: 8,
            background: statusMessage.type === "success" ? alpha(theme.growth, "20") : alpha(theme.decline, "20"),
            border: `1px solid ${statusMessage.type === "success" ? theme.growth : theme.decline}`,
            color: statusMessage.type === "success" ? theme.growth : theme.decline,
            fontSize: "0.9rem",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>{statusMessage.text}</span>
          <button
            onClick={() => setStatusMessage(null)}
            style={{ background: "transparent", border: "none", color: "inherit", cursor: "pointer", fontWeight: 700 }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Index Selector & Filter Bar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 12,
          padding: "12px 16px",
          background: theme.surface2,
          borderRadius: 8,
          border: `1px solid ${theme.border}`,
        }}
      >
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: "0.85rem", color: theme.textSecondary, fontWeight: 600 }}>Target Index:</span>
          {opportunities.map((opp) => {
            const isSelected = activeOpp?.index_symbol === opp.index_symbol;
            return (
              <button
                key={opp.index_symbol}
                onClick={() => setSelectedIndexSymbol(opp.index_symbol)}
                style={{
                  padding: "6px 14px",
                  borderRadius: 20,
                  border: `1px solid ${isSelected ? theme.accent : theme.border}`,
                  background: isSelected ? theme.accent : theme.surface,
                  color: isSelected ? "#000" : theme.textPrimary,
                  fontWeight: 600,
                  fontSize: "0.85rem",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <span>{opp.index_symbol}</span>
                <span
                  style={{
                    fontSize: "0.7rem",
                    padding: "1px 5px",
                    borderRadius: 8,
                    background: isSelected ? "rgba(0,0,0,0.2)" : alpha(theme.growth, "30"),
                    color: isSelected ? "#000" : theme.growth,
                  }}
                >
                  Δρ +{(opp.correlation_spread * 100).toFixed(0)}%
                </span>
              </button>
            );
          })}
        </div>

        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <button
            onClick={() => setFilterLongDispersionOnly(!filterLongDispersionOnly)}
            style={{
              padding: "6px 12px",
              borderRadius: 8,
              border: `1px solid ${filterLongDispersionOnly ? theme.growth : theme.border}`,
              background: filterLongDispersionOnly ? alpha(theme.growth, "20") : theme.surface,
              color: filterLongDispersionOnly ? theme.growth : theme.textSecondary,
              fontSize: "0.8rem",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            {filterLongDispersionOnly ? "✓ Long Dispersion (Δρ ≥ +15%) Only" : "Filter: All Regimes"}
          </button>

          <input
            type="text"
            placeholder="Filter constituents..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              padding: "6px 12px",
              background: theme.surface,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 8,
              fontSize: "0.85rem",
              width: 180,
            }}
          />
        </div>
      </div>

      {query.loading && !query.data && (
        <div style={{ padding: 40, textAlign: "center", color: theme.textSecondary }}>
          Loading dispersion models and implied correlation surfaces...
        </div>
      )}

      {activeOpp && (
        <>
          {/* Top Panel: Correlation Spread Gauge & Vega Neutrality Meter */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
              gap: 16,
            }}
          >
            {/* Correlation Spread Gauge Card */}
            <div
              style={{
                background: theme.surface,
                borderRadius: 12,
                border: `1px solid ${theme.border}`,
                padding: 16,
                display: "flex",
                flexDirection: "column",
                gap: 12,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "0.9rem", fontWeight: 700, color: theme.textSecondary }}>
                  PAIRWISE CORRELATION SPREAD (Δρ)
                </span>
                <span
                  style={{
                    fontSize: "0.75rem",
                    fontWeight: 700,
                    padding: "3px 8px",
                    borderRadius: 6,
                    background:
                      activeOpp.regime === "LONG_DISPERSION"
                        ? alpha(theme.growth, "25")
                        : activeOpp.regime === "SHORT_DISPERSION"
                        ? alpha(theme.decline, "25")
                        : `${theme.surface3}`,
                    color:
                      activeOpp.regime === "LONG_DISPERSION"
                        ? theme.growth
                        : activeOpp.regime === "SHORT_DISPERSION"
                        ? theme.decline
                        : theme.textSecondary,
                  }}
                >
                  {activeOpp.regime.replace("_", " ")}
                </span>
              </div>

              {/* Gauge Numbers */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginTop: 4 }}>
                <div>
                  <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>Implied Correlation (ρ_implied)</div>
                  <div style={{ fontSize: "1.4rem", fontWeight: 700, color: theme.accent }}>
                    {(activeOpp.implied_correlation * 100).toFixed(1)}%
                  </div>
                </div>
                <div style={{ textAlign: "center" }}>
                  <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>Spread (Δρ)</div>
                  <div
                    style={{
                      fontSize: "1.6rem",
                      fontWeight: 800,
                      color: activeOpp.correlation_spread >= 0.15 ? theme.growth : theme.textPrimary,
                    }}
                  >
                    {activeOpp.correlation_spread >= 0 ? "+" : ""}
                    {(activeOpp.correlation_spread * 100).toFixed(1)}%
                  </div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>30D Realized (ρ_realized)</div>
                  <div style={{ fontSize: "1.4rem", fontWeight: 700, color: theme.textPrimary }}>
                    {(activeOpp.realized_correlation * 100).toFixed(1)}%
                  </div>
                </div>
              </div>

              {/* Spread Visual Progress Bar */}
              <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
                <div
                  style={{
                    height: 10,
                    width: "100%",
                    background: theme.surface3,
                    borderRadius: 5,
                    position: "relative",
                    overflow: "hidden",
                  }}
                >
                  {/* Realized marker */}
                  <div
                    style={{
                      position: "absolute",
                      left: 0,
                      width: `${Math.min(100, activeOpp.realized_correlation * 100)}%`,
                      height: "100%",
                      background: theme.textSecondary,
                      opacity: 0.5,
                    }}
                  />
                  {/* Implied overlay */}
                  <div
                    style={{
                      position: "absolute",
                      left: `${Math.min(100, activeOpp.realized_correlation * 100)}%`,
                      width: `${Math.max(0, (activeOpp.implied_correlation - activeOpp.realized_correlation) * 100)}%`,
                      height: "100%",
                      background: activeOpp.correlation_spread >= 0.15 ? theme.growth : theme.accent,
                    }}
                  />
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.7rem", color: theme.textSecondary }}>
                  <span>0% (Decoupled)</span>
                  <span>Realized vs Implied Premia</span>
                  <span>100% (Locked)</span>
                </div>
              </div>

              <div style={{ fontSize: "0.8rem", color: theme.textSecondary, background: theme.surface2, padding: "8px 12px", borderRadius: 6 }}>
                💡 <b>Trade Rationale:</b> {activeOpp.trade_recommendation}
              </div>
            </div>

            {/* Vega Neutrality & Index Straddle Card */}
            <div
              style={{
                background: theme.surface,
                borderRadius: 12,
                border: `1px solid ${theme.border}`,
                padding: 16,
                display: "flex",
                flexDirection: "column",
                gap: 12,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "0.9rem", fontWeight: 700, color: theme.textSecondary }}>
                  VEGA NEUTRALITY & INDEX STRADDLE
                </span>
                <span
                  style={{
                    fontSize: "0.75rem",
                    fontWeight: 700,
                    padding: "3px 8px",
                    borderRadius: 6,
                    background: alpha(theme.growth, "25"),
                    color: theme.growth,
                  }}
                >
                  Vega Neutral: {activeOpp.vega_neutrality_ratio.toFixed(2)}x
                </span>
              </div>

              {/* Index Straddle Specs */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
                <div style={{ background: theme.surface2, padding: 8, borderRadius: 6 }}>
                  <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>Index Spot & IV</div>
                  <div style={{ fontSize: "1rem", fontWeight: 600 }}>
                    ${activeOpp.index_spot.toFixed(2)} <span style={{ fontSize: "0.8rem", color: theme.accent }}>({(activeOpp.index_iv * 100).toFixed(1)}% IV)</span>
                  </div>
                </div>
                <div style={{ background: theme.surface2, padding: 8, borderRadius: 6 }}>
                  <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>Short Straddle</div>
                  <div style={{ fontSize: "1rem", fontWeight: 600 }}>
                    {activeOpp.index_straddle_contracts}x {activeOpp.index_straddle_strike.toFixed(0)} Strike
                  </div>
                </div>
                <div style={{ background: theme.surface2, padding: 8, borderRadius: 6 }}>
                  <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>Straddle Price</div>
                  <div style={{ fontSize: "1rem", fontWeight: 600, color: theme.growth }}>
                    ${activeOpp.index_straddle_price.toFixed(2)} Mid
                  </div>
                </div>
              </div>

              {/* Vega Balance Breakdown */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "0.85rem", marginTop: 2 }}>
                <div>
                  <span style={{ color: theme.textSecondary }}>Index Vega: </span>
                  <b style={{ color: theme.decline }}>-${activeOpp.index_vega_total.toFixed(1)}</b>
                </div>
                <div>
                  <span style={{ color: theme.textSecondary }}>Constituents Vega: </span>
                  <b style={{ color: theme.growth }}>+${activeOpp.constituents_vega_total.toFixed(1)}</b>
                </div>
                <div>
                  <span style={{ color: theme.textSecondary }}>Net Vega: </span>
                  <b style={{ color: Math.abs(activeOpp.net_vega) < 10 ? theme.growth : theme.accent }}>
                    {activeOpp.net_vega >= 0 ? "+" : ""}${activeOpp.net_vega.toFixed(1)} $/vol
                  </b>
                </div>
              </div>

              {/* Execute Action */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "auto", paddingTop: 8 }}>
                <div>
                  <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>Net Premium Est.</div>
                  <div style={{ fontSize: "1.1rem", fontWeight: 700, color: theme.growth }}>
                    +${activeOpp.net_premium_estimate.toFixed(2)}
                  </div>
                </div>

                <button
                  onClick={() => handleExecuteBasket(activeOpp)}
                  disabled={isExecuting}
                  style={{
                    background: theme.growth,
                    color: "#000",
                    border: "none",
                    borderRadius: 8,
                    padding: "10px 20px",
                    fontWeight: 700,
                    fontSize: "0.95rem",
                    cursor: isExecuting ? "not-allowed" : "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    opacity: isExecuting ? 0.7 : 1,
                    transition: "all 0.15s ease",
                  }}
                >
                  {isExecuting ? "⚡ Executing Basket..." : "⚡ Execute Dispersion Basket"}
                </button>
              </div>
            </div>
          </div>

          {/* Constituent Basket Straddles Table */}
          <div
            style={{
              background: theme.surface,
              borderRadius: 12,
              border: `1px solid ${theme.border}`,
              padding: 16,
              display: "flex",
              flexDirection: "column",
              gap: 12,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
              <div>
                <span style={{ fontSize: "1rem", fontWeight: 700 }}>Constituent Straddles ({activeOpp.constituents.length} Components)</span>
                <span style={{ fontSize: "0.8rem", color: theme.textSecondary, marginLeft: 8 }}>
                  Expiration: {activeOpp.expiration} ({activeOpp.dte} DTE)
                </span>
              </div>
              <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
                Vega-weighted allocation to match index vega ($V_basket ≈ $V_index)
              </div>
            </div>

            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${theme.borderStrong}`, color: theme.textSecondary, textAlign: "left" }}>
                    <th style={{ padding: "8px 12px" }}>Constituent</th>
                    <th style={{ padding: "8px 12px" }}>Weight</th>
                    <th style={{ padding: "8px 12px" }}>Spot</th>
                    <th style={{ padding: "8px 12px" }}>ATM IV</th>
                    <th style={{ padding: "8px 12px" }}>30D Realized</th>
                    <th style={{ padding: "8px 12px" }}>IV − RV</th>
                    <th style={{ padding: "8px 12px" }}>Straddle Strike</th>
                    <th style={{ padding: "8px 12px" }}>Straddle Mid</th>
                    <th style={{ padding: "8px 12px" }}>Vega/Straddle</th>
                    <th style={{ padding: "8px 12px" }}>Allocated</th>
                    <th style={{ padding: "8px 12px", textAlign: "right" }}>Leg Action</th>
                  </tr>
                </thead>
                <tbody>
                  {activeOpp.constituents.map((c) => {
                    const spread = c.realized_vol_30d != null ? (c.atm_iv - c.realized_vol_30d) * 100 : null;
                    return (
                      <tr
                        key={c.symbol}
                        onClick={() => onSelectTicker && onSelectTicker(c.symbol)}
                        style={{
                          borderBottom: `1px solid ${theme.border}`,
                          cursor: onSelectTicker ? "pointer" : "default",
                        }}
                      >
                        <td style={{ padding: "10px 12px", fontWeight: 700, color: theme.accent }}>
                          {c.symbol}
                        </td>
                        <td style={{ padding: "10px 12px" }}>
                          {(c.weight * 100).toFixed(1)}%
                        </td>
                        <td style={{ padding: "10px 12px" }}>
                          ${c.spot_price.toFixed(2)}
                        </td>
                        <td style={{ padding: "10px 12px" }}>
                          {(c.atm_iv * 100).toFixed(1)}%
                        </td>
                        <td style={{ padding: "10px 12px", color: theme.textSecondary }}>
                          {c.realized_vol_30d != null ? `${(c.realized_vol_30d * 100).toFixed(1)}%` : "—"}
                        </td>
                        <td style={{ padding: "10px 12px", color: spread != null && spread >= 5 ? theme.growth : theme.textPrimary, fontWeight: 600 }}>
                          {spread != null ? `+${spread.toFixed(1)}%` : "—"}
                        </td>
                        <td style={{ padding: "10px 12px" }}>
                          ${c.straddle_strike.toFixed(1)}
                        </td>
                        <td style={{ padding: "10px 12px" }}>
                          ${c.straddle_mid.toFixed(2)}
                        </td>
                        <td style={{ padding: "10px 12px" }}>
                          ${c.vega_per_straddle.toFixed(2)}
                        </td>
                        <td style={{ padding: "10px 12px", fontWeight: 700 }}>
                          {c.contracts_allocated}x
                        </td>
                        <td style={{ padding: "10px 12px", textAlign: "right" }}>
                          <span
                            style={{
                              padding: "3px 8px",
                              borderRadius: 6,
                              background: alpha(theme.growth, "20"),
                              color: theme.growth,
                              fontWeight: 700,
                              fontSize: "0.75rem",
                            }}
                          >
                            LONG STRADDLE
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
};
