import React, { useState } from "react";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { theme } from "../../theme";
import type { GexProfileResponse, GexStrikePoint } from "../../api/types";

interface GexProfileViewProps {
  initialSymbol?: string;
  spotPrice?: number;
  onSelectTicker?: (symbol: string) => void;
  onSelectStrike?: (strike: number) => void;
  onClose?: () => void;
}

export const GexProfileView: React.FC<GexProfileViewProps> = ({
  initialSymbol = "SPY",
  spotPrice: initialSpot,
  onSelectTicker,
  onSelectStrike,
  onClose,
}) => {
  const [selectedSymbol, setSelectedSymbol] = useState<string>(initialSymbol);
  const [customTicker, setCustomTicker] = useState<string>("");
  const [hoveredStrike, setHoveredStrike] = useState<GexStrikePoint | null>(null);
  const [viewMode, setViewMode] = useState<"chart" | "table">("chart");

  const query = useApi<GexProfileResponse>(
    () => api.getOptionsGexProfile(selectedSymbol),
    [selectedSymbol]
  );

  const data = query.data;
  const currentSpot = data?.spot_price || initialSpot || 500;
  // net_gex is a raw dollar figure (not pre-scaled to millions) -- divide by
  // 1e6 for the existing "$...M" display convention.
  const netGex = data?.net_gex ?? 0;
  const netGexM = netGex / 1e6;
  const isVolDampener = data?.gamma_regime === "POSITIVE_GAMMA";
  const regimeColor = isVolDampener ? theme.growth : theme.decline;
  const regimeBg = isVolDampener ? `${theme.growth}20` : `${theme.decline}20`;

  const activeSymbols = ["SPY", "QQQ", "TSLA", "NVDA", "AAPL", "MSFT"];

  const handleCustomTickerSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (customTicker.trim()) {
      const sym = customTicker.trim().toUpperCase();
      setSelectedSymbol(sym);
      if (onSelectTicker) onSelectTicker(sym);
      setCustomTicker("");
    }
  };

  // Find max absolute GEX for scaling chart bars
  const maxAbsGex = React.useMemo(() => {
    if (!data?.strikes?.length) return 100;
    let maxVal = 1;
    for (const s of data.strikes) {
      maxVal = Math.max(maxVal, Math.abs(s.call_gex), Math.abs(s.put_gex), Math.abs(s.net_gex));
    }
    return maxVal;
  }, [data?.strikes]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 16,
        color: theme.textPrimary,
      }}
    >
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
            <span style={{ fontSize: "1.3rem", fontWeight: 700 }}>
              ⚡ Options Gamma Exposure (GEX) & Dealer Hedging Desk
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
              Phase 20
            </span>
          </div>
          <div
            style={{
              fontSize: "0.85rem",
              color: theme.textSecondary,
              marginTop: 4,
            }}
          >
            Strike-by-strike Dealer Gamma Positioning, Zero-Gamma Flip, and Gamma Walls. Quantifies dealer hedging reflexivity and volatility dampening/acceleration regimes.
          </div>
        </div>

        <div
          style={{
            display: "flex",
            gap: 8,
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          <div style={{ display: "flex", background: theme.surface2, borderRadius: 8, padding: 2, border: `1px solid ${theme.border}` }}>
            <button
              onClick={() => setViewMode("chart")}
              style={{
                padding: "6px 12px",
                background: viewMode === "chart" ? theme.accent : "transparent",
                color: viewMode === "chart" ? "#000" : theme.textSecondary,
                border: "none",
                borderRadius: 6,
                fontSize: "0.8rem",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              📊 GEX Chart
            </button>
            <button
              onClick={() => setViewMode("table")}
              style={{
                padding: "6px 12px",
                background: viewMode === "table" ? theme.accent : "transparent",
                color: viewMode === "table" ? "#000" : theme.textSecondary,
                border: "none",
                borderRadius: 6,
                fontSize: "0.8rem",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              📋 Strike Ladder
            </button>
          </div>

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

      {/* Symbol Pill Selector & Search */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 12,
          padding: "10px 16px",
          background: theme.surface2,
          borderRadius: 8,
          border: `1px solid ${theme.border}`,
        }}
      >
        <div style={{ display: "flex", gap: 8, alignItems: "center", overflowX: "auto" }}>
          <span
            style={{
              fontSize: "0.85rem",
              color: theme.textSecondary,
              fontWeight: 600,
              whiteSpace: "nowrap",
            }}
          >
            Active Tickers:
          </span>
          {activeSymbols.map((sym) => {
            const isSelected = selectedSymbol.toUpperCase() === sym.toUpperCase();
            return (
              <button
                key={sym}
                onClick={() => {
                  setSelectedSymbol(sym);
                  if (onSelectTicker) onSelectTicker(sym);
                }}
                style={{
                  padding: "6px 14px",
                  borderRadius: 20,
                  border: `1px solid ${isSelected ? theme.accent : theme.border}`,
                  background: isSelected ? theme.accent : theme.surface,
                  color: isSelected ? "#000" : theme.textPrimary,
                  fontWeight: 600,
                  fontSize: "0.85rem",
                  cursor: "pointer",
                  transition: "all 0.15s ease",
                }}
              >
                {sym}
              </button>
            );
          })}
        </div>

        <form onSubmit={handleCustomTickerSubmit} style={{ display: "flex", gap: 6 }}>
          <input
            type="text"
            placeholder="Custom Ticker (e.g. AMD)"
            value={customTicker}
            onChange={(e) => setCustomTicker(e.target.value)}
            style={{
              padding: "6px 10px",
              background: theme.base,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 6,
              fontSize: "0.85rem",
              width: 150,
            }}
          />
          <button
            type="submit"
            style={{
              padding: "6px 12px",
              background: theme.surface3,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 6,
              fontSize: "0.85rem",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Lookup
          </button>
        </form>
      </div>

      {query.loading && !data && (
        <div
          style={{
            padding: 40,
            textAlign: "center",
            color: theme.textSecondary,
          }}
        >
          Calculating Dealer Gamma Exposure Profile & Strike Walls for {selectedSymbol}...
        </div>
      )}

      {query.error && (
        <div
          style={{
            padding: 16,
            background: `${theme.decline}20`,
            color: theme.decline,
            borderRadius: 8,
            border: `1px solid ${theme.decline}`,
          }}
        >
          Error loading GEX Profile: {query.error}
        </div>
      )}

      {data && (
        <>
          {/* Volatility Regime & Gamma Walls KPI Banner */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: 12,
            }}
          >
            {/* KPI 1: Net Dealer GEX */}
            <div
              style={{
                background: theme.surface,
                borderRadius: 10,
                padding: "14px 16px",
                border: `1px solid ${theme.border}`,
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              <span style={{ fontSize: "0.75rem", color: theme.textSecondary, fontWeight: 600 }}>
                Total Net Dealer GEX
              </span>
              <div
                style={{
                  fontSize: "1.5rem",
                  fontWeight: 800,
                  color: netGex >= 0 ? theme.growth : theme.decline,
                }}
              >
                {netGex >= 0 ? "+" : ""}${netGexM.toFixed(1)}M
              </div>
              <span style={{ fontSize: "0.75rem", color: theme.textMuted }}>
                Spot Price: <b>${currentSpot.toFixed(2)}</b>
              </span>
            </div>

            {/* KPI 2: Volatility Regime Indicator */}
            <div
              style={{
                background: theme.surface,
                borderRadius: 10,
                padding: "14px 16px",
                border: `1px solid ${regimeColor}40`,
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "0.75rem", color: theme.textSecondary, fontWeight: 600 }}>
                  Volatility Regime
                </span>
                <span
                  style={{
                    fontSize: "0.7rem",
                    padding: "2px 6px",
                    borderRadius: 4,
                    background: regimeBg,
                    color: regimeColor,
                    fontWeight: 700,
                  }}
                >
                  {isVolDampener ? "POSITIVE GAMMA" : "NEGATIVE GAMMA"}
                </span>
              </div>
              <div style={{ fontSize: "1.2rem", fontWeight: 800, color: regimeColor }}>
                {isVolDampener ? "🛡️ Vol Dampener" : "⚡ Vol Accelerator"}
              </div>
              <span style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
                {isVolDampener
                  ? "Dealers buy dips & sell rips (suppresses realized volatility)"
                  : "Dealers sell dips & buy rallies (amplifies tail moves)"}
              </span>
            </div>

            {/* KPI 3: Zero-Gamma Flip Level */}
            <div
              style={{
                background: theme.surface,
                borderRadius: 10,
                padding: "14px 16px",
                border: `1px solid ${theme.border}`,
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              <span style={{ fontSize: "0.75rem", color: theme.textSecondary, fontWeight: 600 }}>
                Zero-Gamma Flip Level
              </span>
              <div style={{ fontSize: "1.5rem", fontWeight: 800, color: theme.caution }}>
                ${data.zero_gamma_flip.toFixed(2)}
              </div>
              <span style={{ fontSize: "0.75rem", color: theme.textMuted }}>
                {currentSpot >= data.zero_gamma_flip
                  ? `+${((currentSpot / data.zero_gamma_flip - 1) * 100).toFixed(1)}% above flip boundary`
                  : `-${((1 - currentSpot / data.zero_gamma_flip) * 100).toFixed(1)}% below flip boundary`}
              </span>
            </div>

            {/* KPI 4: Major Call / Put Gamma Walls */}
            <div
              style={{
                background: theme.surface,
                borderRadius: 10,
                padding: "14px 16px",
                border: `1px solid ${theme.border}`,
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}
            >
              <span style={{ fontSize: "0.75rem", color: theme.textSecondary, fontWeight: 600 }}>
                Major Gamma Walls
              </span>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "0.8rem", color: theme.growth, fontWeight: 600 }}>
                  📈 Call Wall: ${data.call_wall_strike.toFixed(2)}
                </span>
                <span style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
                  Pin Resistance
                </span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "0.8rem", color: theme.decline, fontWeight: 600 }}>
                  📉 Put Wall: ${data.put_wall_strike.toFixed(2)}
                </span>
                <span style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
                  Support Floor
                </span>
              </div>
            </div>
          </div>

          {/* Positioning Bias Banner */}
          {data.regime_description && (
            <div
              style={{
                padding: "10px 14px",
                background: theme.surface2,
                borderRadius: 8,
                border: `1px solid ${theme.border}`,
                fontSize: "0.85rem",
                color: theme.textSecondary,
                lineHeight: 1.5,
              }}
            >
              💡 <b>Hedging Dynamics:</b> {data.regime_description} Dealer hedging flow: ${(data.dealer_hedging_flow / 1e6).toFixed(2)}M per 1% move.
            </div>
          )}

          {/* Strike-by-Strike Profile View (Chart or Table) */}
          {viewMode === "chart" ? (
            <div
              style={{
                background: theme.surface,
                borderRadius: 12,
                border: `1px solid ${theme.border}`,
                padding: 20,
                display: "flex",
                flexDirection: "column",
                gap: 16,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                <div>
                  <span style={{ fontSize: "1.05rem", fontWeight: 700 }}>
                    Strike-by-Strike Gamma Exposure ($M GEX per $1 move)
                  </span>
                  <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
                    Call GEX (Green) vs Put GEX (Red) & Net GEX. Hover over strikes for granular exposure data.
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 14, fontSize: "0.75rem" }}>
                  <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <span style={{ width: 10, height: 10, background: theme.growth, borderRadius: 2 }} />
                    Call GEX
                  </span>
                  <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <span style={{ width: 10, height: 10, background: theme.decline, borderRadius: 2 }} />
                    Put GEX
                  </span>
                  <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <span style={{ width: 10, height: 2, background: theme.accent }} />
                    Net GEX
                  </span>
                  <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <span style={{ width: 10, height: 2, background: theme.caution, borderTop: "2px dashed #f59e0b" }} />
                    Zero-Flip
                  </span>
                </div>
              </div>

              {/* Bidirectional Bar Chart */}
              <div
                style={{
                  minHeight: 320,
                  display: "flex",
                  alignItems: "stretch",
                  gap: 4,
                  background: theme.surface2,
                  padding: "20px 12px 10px 12px",
                  borderRadius: 8,
                  border: `1px solid ${theme.borderStrong}`,
                  overflowX: "auto",
                  position: "relative",
                }}
              >
                {data.strikes.map((s) => {
                  const isCallWall = s.strike === data.call_wall_strike;
                  const isPutWall = s.strike === data.put_wall_strike;
                  const isFlipStrike = Math.abs(s.strike - data.zero_gamma_flip) < 2.5;
                  const isNearSpot = Math.abs(s.strike - currentSpot) < 2.0;
                  const isHovered = hoveredStrike?.strike === s.strike;

                  const callHeightPct = Math.min(100, (s.call_gex / maxAbsGex) * 100);
                  const putHeightPct = Math.min(100, (Math.abs(s.put_gex) / maxAbsGex) * 100);
                  const netHeightPct = Math.min(100, (Math.abs(s.net_gex) / maxAbsGex) * 100);

                  return (
                    <div
                      key={s.strike}
                      onMouseEnter={() => setHoveredStrike(s)}
                      onMouseLeave={() => setHoveredStrike(null)}
                      onClick={() => onSelectStrike && onSelectStrike(s.strike)}
                      style={{
                        flex: 1,
                        minWidth: 28,
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "center",
                        cursor: "pointer",
                        position: "relative",
                        background: isHovered
                          ? "rgba(255,255,255,0.06)"
                          : isNearSpot
                          ? "rgba(56, 189, 248, 0.05)"
                          : "transparent",
                        borderRadius: 4,
                        padding: "4px 2px",
                        transition: "all 0.1s ease",
                      }}
                    >
                      {/* Top Badges for Walls */}
                      <div style={{ height: 18, fontSize: "0.6rem", fontWeight: 700, textAlign: "center" }}>
                        {isCallWall && <span style={{ color: theme.growth }}>CW</span>}
                        {isPutWall && <span style={{ color: theme.decline }}>PW</span>}
                        {isFlipStrike && <span style={{ color: theme.caution }}>FLIP</span>}
                      </div>

                      {/* Upper half: Call GEX (0 to +Max) */}
                      <div
                        style={{
                          flex: 1,
                          width: "100%",
                          display: "flex",
                          alignItems: "flex-end",
                          justifyContent: "center",
                          position: "relative",
                          borderBottom: `1px solid ${theme.borderStrong}`,
                        }}
                      >
                        <div
                          style={{
                            width: "70%",
                            height: `${callHeightPct}%`,
                            background: theme.growth,
                            borderTopLeftRadius: 3,
                            borderTopRightRadius: 3,
                            opacity: isHovered ? 1 : 0.8,
                          }}
                        />
                        {/* Net GEX indicator dot */}
                        {s.net_gex > 0 && (
                          <div
                            style={{
                              position: "absolute",
                              bottom: `${netHeightPct}%`,
                              width: 6,
                              height: 6,
                              borderRadius: "50%",
                              background: theme.accent,
                              transform: "translateY(3px)",
                            }}
                          />
                        )}
                      </div>

                      {/* Lower half: Put GEX (0 to -Max) */}
                      <div
                        style={{
                          flex: 1,
                          width: "100%",
                          display: "flex",
                          alignItems: "flex-start",
                          justifyContent: "center",
                          position: "relative",
                        }}
                      >
                        <div
                          style={{
                            width: "70%",
                            height: `${putHeightPct}%`,
                            background: theme.decline,
                            borderBottomLeftRadius: 3,
                            borderBottomRightRadius: 3,
                            opacity: isHovered ? 1 : 0.8,
                          }}
                        />
                        {/* Net GEX indicator dot */}
                        {s.net_gex < 0 && (
                          <div
                            style={{
                              position: "absolute",
                              top: `${netHeightPct}%`,
                              width: 6,
                              height: 6,
                              borderRadius: "50%",
                              background: theme.accent,
                              transform: "translateY(-3px)",
                            }}
                          />
                        )}
                      </div>

                      {/* Strike Label */}
                      <div
                        style={{
                          fontSize: "0.68rem",
                          fontWeight: isNearSpot || isCallWall || isPutWall ? 700 : 400,
                          color: isNearSpot
                            ? theme.accent
                            : isCallWall
                            ? theme.growth
                            : isPutWall
                            ? theme.decline
                            : theme.textSecondary,
                          marginTop: 4,
                          transform: "rotate(-45deg)",
                          whiteSpace: "nowrap",
                        }}
                      >
                        ${s.strike}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Hover Details Card */}
              <div
                style={{
                  minHeight: 52,
                  padding: "10px 14px",
                  background: theme.surface2,
                  borderRadius: 8,
                  fontSize: "0.8rem",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  flexWrap: "wrap",
                  gap: 12,
                }}
              >
                {hoveredStrike ? (
                  <>
                    <span>
                      Strike: <b style={{ fontSize: "0.95rem" }}>${hoveredStrike.strike.toFixed(2)}</b>
                    </span>
                    <span>
                      Call GEX: <b style={{ color: theme.growth }}>+${(hoveredStrike.call_gex / 1e6).toFixed(1)}M</b> (OI: {hoveredStrike.call_oi?.toLocaleString() ?? "—"})
                    </span>
                    <span>
                      Put GEX: <b style={{ color: theme.decline }}>${(hoveredStrike.put_gex / 1e6).toFixed(1)}M</b> (OI: {hoveredStrike.put_oi?.toLocaleString() ?? "—"})
                    </span>
                    <span>
                      Net GEX:{" "}
                      <b style={{ color: hoveredStrike.net_gex >= 0 ? theme.growth : theme.decline }}>
                        {hoveredStrike.net_gex >= 0 ? "+" : ""}${(hoveredStrike.net_gex / 1e6).toFixed(1)}M
                      </b>
                    </span>
                    <span>
                      Gamma Concentration: <b>{hoveredStrike.gamma_concentration_pct?.toFixed(1) ?? "—"}%</b>
                    </span>
                  </>
                ) : (
                  <span style={{ color: theme.textSecondary, fontStyle: "italic" }}>
                    Hover over any strike bar in the profile chart to inspect granular Call, Put, and Net GEX figures.
                  </span>
                )}
              </div>
            </div>
          ) : (
            /* Strike Ladder Table View */
            <div
              style={{
                background: theme.surface,
                borderRadius: 12,
                border: `1px solid ${theme.border}`,
                overflow: "hidden",
              }}
            >
              <div style={{ padding: "14px 16px", borderBottom: `1px solid ${theme.border}` }}>
                <span style={{ fontSize: "1rem", fontWeight: 700 }}>
                  {selectedSymbol} GEX Strike Exposure Ladder
                </span>
              </div>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem", textAlign: "left" }}>
                  <thead>
                    <tr style={{ background: theme.surface2, borderBottom: `1px solid ${theme.border}`, color: theme.textSecondary }}>
                      <th style={{ padding: "10px 14px" }}>Strike</th>
                      <th style={{ padding: "10px 14px", textAlign: "right" }}>Call GEX ($M)</th>
                      <th style={{ padding: "10px 14px", textAlign: "right" }}>Call OI</th>
                      <th style={{ padding: "10px 14px", textAlign: "right" }}>Put GEX ($M)</th>
                      <th style={{ padding: "10px 14px", textAlign: "right" }}>Put OI</th>
                      <th style={{ padding: "10px 14px", textAlign: "right" }}>Net GEX ($M)</th>
                      <th style={{ padding: "10px 14px", textAlign: "center" }}>Special Wall</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.strikes.map((s) => {
                      const isCallWall = s.strike === data.call_wall_strike;
                      const isPutWall = s.strike === data.put_wall_strike;
                      const isZeroFlip = Math.abs(s.strike - data.zero_gamma_flip) < 2.5;
                      const isNearSpot = Math.abs(s.strike - currentSpot) < 2.0;

                      return (
                        <tr
                          key={s.strike}
                          onClick={() => onSelectStrike && onSelectStrike(s.strike)}
                          style={{
                            borderBottom: `1px solid ${theme.border}`,
                            background: isNearSpot ? `${theme.accent}15` : "transparent",
                            cursor: "pointer",
                          }}
                        >
                          <td style={{ padding: "10px 14px", fontWeight: 700 }}>
                            ${s.strike.toFixed(2)} {isNearSpot && <span style={{ color: theme.accent, fontSize: "0.75rem" }}>(Spot)</span>}
                          </td>
                          <td style={{ padding: "10px 14px", textAlign: "right", color: theme.growth, fontWeight: 600 }}>
                            +${(s.call_gex / 1e6).toFixed(1)}M
                          </td>
                          <td style={{ padding: "10px 14px", textAlign: "right" }}>
                            {s.call_oi?.toLocaleString() ?? "—"}
                          </td>
                          <td style={{ padding: "10px 14px", textAlign: "right", color: theme.decline, fontWeight: 600 }}>
                            ${(s.put_gex / 1e6).toFixed(1)}M
                          </td>
                          <td style={{ padding: "10px 14px", textAlign: "right" }}>
                            {s.put_oi?.toLocaleString() ?? "—"}
                          </td>
                          <td
                            style={{
                              padding: "10px 14px",
                              textAlign: "right",
                              fontWeight: 700,
                              color: s.net_gex >= 0 ? theme.growth : theme.decline,
                            }}
                          >
                            {s.net_gex >= 0 ? "+" : ""}${(s.net_gex / 1e6).toFixed(1)}M
                          </td>
                          <td style={{ padding: "10px 14px", textAlign: "center" }}>
                            {isCallWall && (
                              <span style={{ fontSize: "0.7rem", padding: "2px 6px", borderRadius: 4, background: `${theme.growth}25`, color: theme.growth, fontWeight: 700 }}>
                                CALL WALL
                              </span>
                            )}
                            {isPutWall && (
                              <span style={{ fontSize: "0.7rem", padding: "2px 6px", borderRadius: 4, background: `${theme.decline}25`, color: theme.decline, fontWeight: 700 }}>
                                PUT WALL
                              </span>
                            )}
                            {isZeroFlip && (
                              <span style={{ fontSize: "0.7rem", padding: "2px 6px", borderRadius: 4, background: `${theme.caution}25`, color: theme.caution, fontWeight: 700 }}>
                                ZERO FLIP
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Microstructure Math Reference Card */}
          <div
            style={{
              fontSize: "0.75rem",
              color: theme.textMuted,
              background: theme.surface2,
              padding: "12px 16px",
              borderRadius: 8,
              lineHeight: 1.5,
              border: `1px solid ${theme.border}`,
            }}
          >
            📐 <b>Dealer Gamma Formula:</b> GEX = Σ (Γ_i · OI_i · S² · 100 · Sign_i). In Positive Gamma regimes (Spot &gt; Zero-Flip), market makers are structurally long gamma and must counter-trade price moves (buying dips, selling rips), compressing volatility. In Negative Gamma regimes (Spot &lt; Zero-Flip), market makers are short gamma and must trade with momentum (selling dips, buying rallies), accelerating cascade risks.
          </div>
        </>
      )}
    </div>
  );
};
