import React, { useState } from "react";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { theme, alpha } from "../../theme";
import type { VpinMetricsResponse, VpinBucket } from "../../api/types";

interface VpinGaugeProps {
  initialSymbol?: string;
  onSelectTicker?: (symbol: string) => void;
  onClose?: () => void;
}

export const VpinGauge: React.FC<VpinGaugeProps> = ({
  initialSymbol = "SPY",
  onSelectTicker,
  onClose,
}) => {
  const [selectedSymbol, setSelectedSymbol] = useState<string>(initialSymbol);
  const [hoveredBucket, setHoveredBucket] = useState<VpinBucket | null>(null);

  const query = useApi<VpinMetricsResponse>(
    () => api.getVpinMetrics(selectedSymbol),
    [selectedSymbol]
  );

  const data = query.data;
  const vpinVal = data?.vpin ?? 0.25;
  const vpinPct = Number((vpinVal * 100).toFixed(1));
  const regime = data?.regime ?? "MODERATE";

  const isLow = regime === "LOW";
  const isModerate = regime === "MODERATE";
  const isHigh = regime === "HIGH_TOXICITY";

  const regimeColor = isLow
    ? theme.growth
    : isModerate
    ? theme.caution
    : theme.decline;

  const regimeBg = isLow
    ? alpha(theme.growth, "20")
    : isModerate
    ? alpha(theme.caution, "20")
    : alpha(theme.decline, "20");

  // Semicircle gauge parameters (SVG)
  const radius = 80;
  const cx = 110;
  const cy = 100;
  const strokeWidth = 14;

  // Gauge angle in degrees: -180 deg to 0 deg
  const clampedVpin = Math.max(0, Math.min(1, vpinVal));
  const angleDeg = -180 + clampedVpin * 180;
  const angleRad = (angleDeg * Math.PI) / 180;

  // Needle tip
  const needleLen = radius - 10;
  const needleX = cx + needleLen * Math.cos(angleRad);
  const needleY = cy + needleLen * Math.sin(angleRad);

  const activeSymbols = ["SPY", "QQQ", "TSLA", "NVDA", "AAPL", "MSFT"];

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
              ⏱ Options VPIN Toxicity Meter & Microstructure Risk Desk
            </span>
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
              Phase 17
            </span>
          </div>
          <div
            style={{
              fontSize: "0.85rem",
              color: theme.textSecondary,
              marginTop: 4,
            }}
          >
            Volume-Synchronized Probability of Toxicity (Easley, López de Prado,
            O'Hara 2012) via Bulk Volume Classification (BVC). Flags adverse
            selection risk when VPIN &gt; 35%.
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

      {/* Symbol Pill Selector */}
      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          padding: "10px 16px",
          background: theme.surface2,
          borderRadius: 8,
          border: `1px solid ${theme.border}`,
          overflowX: "auto",
        }}
      >
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

      {/* High Toxicity Warning Banner */}
      {data?.warning_message && (
        <div
          style={{
            padding: "12px 16px",
            borderRadius: 8,
            background: alpha(theme.decline, "20"),
            border: `1px solid ${theme.decline}`,
            color: theme.decline,
            fontSize: "0.9rem",
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <span style={{ fontSize: "1.2rem" }}>⚠️</span>
          <span>{data.warning_message}</span>
        </div>
      )}

      {query.loading && !data && (
        <div
          style={{
            padding: 40,
            textAlign: "center",
            color: theme.textSecondary,
          }}
        >
          Computing Volume-Synchronized Probability of Toxicity for {selectedSymbol}...
        </div>
      )}

      {data && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
            gap: 16,
          }}
        >
          {/* Card 1: Circular / Semicircular VPIN Toxicity Meter */}
          <div
            style={{
              background: theme.surface,
              borderRadius: 12,
              border: `1px solid ${theme.border}`,
              padding: 20,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 16,
            }}
          >
            <div
              style={{
                width: "100%",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span style={{ fontSize: "1rem", fontWeight: 700 }}>
                VPIN Toxicity Arc Meter
              </span>
              <span
                style={{
                  fontSize: "0.75rem",
                  padding: "3px 8px",
                  borderRadius: 6,
                  background: regimeBg,
                  color: regimeColor,
                  fontWeight: 700,
                }}
              >
                {regime.replace("_", " ")}
              </span>
            </div>

            {/* Gauge SVG */}
            <div style={{ position: "relative", width: 220, height: 120 }}>
              <svg width="220" height="120" viewBox="0 0 220 120">
                {/* Arc Background Track */}
                <path
                  d="M 30,100 A 80,80 0 0,1 190,100"
                  fill="none"
                  stroke={theme.surface3}
                  strokeWidth={strokeWidth}
                  strokeLinecap="round"
                />

                {/* Safe Band: 0% to 20% (-180deg to -144deg) */}
                <path
                  d="M 30,100 A 80,80 0 0,1 45.3,53.0"
                  fill="none"
                  stroke={theme.growth}
                  strokeWidth={strokeWidth}
                  strokeLinecap="round"
                  opacity={0.85}
                />

                {/* Moderate Band: 20% to 35% (-144deg to -117deg) */}
                <path
                  d="M 45.3,53.0 A 80,80 0 0,1 73.7,28.7"
                  fill="none"
                  stroke={theme.caution}
                  strokeWidth={strokeWidth}
                  opacity={0.85}
                />

                {/* Toxic Band: 35% to 100% (-117deg to 0deg) */}
                <path
                  d="M 73.7,28.7 A 80,80 0 0,1 190,100"
                  fill="none"
                  stroke={theme.decline}
                  strokeWidth={strokeWidth}
                  strokeLinecap="round"
                  opacity={0.85}
                />

                {/* Needle */}
                <line
                  x1={cx}
                  y1={cy}
                  x2={needleX}
                  y2={needleY}
                  stroke="#ffffff"
                  strokeWidth={3}
                  strokeLinecap="round"
                />
                <circle cx={cx} cy={cy} r={6} fill="#ffffff" />
              </svg>
            </div>

            {/* Readout Numbers */}
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 4,
              }}
            >
              <div
                style={{
                  fontSize: "2.2rem",
                  fontWeight: 800,
                  color: regimeColor,
                }}
              >
                {vpinPct}%
              </div>
              <div
                style={{
                  fontSize: "0.8rem",
                  color: theme.textSecondary,
                }}
              >
                Probability of Informed Trading ({selectedSymbol})
              </div>
            </div>

            {/* Threshold Bands Legend */}
            <div
              style={{
                width: "100%",
                display: "flex",
                justifyContent: "space-between",
                background: theme.surface2,
                padding: "8px 12px",
                borderRadius: 8,
                fontSize: "0.75rem",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: theme.growth,
                  }}
                />
                <span>Safe (&lt;20%)</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: theme.caution,
                  }}
                />
                <span>Moderate (20-35%)</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: theme.decline,
                  }}
                />
                <span>Toxic (&gt;35%)</span>
              </div>
            </div>

            {/* Summary KPI Badges */}
            <div
              style={{
                width: "100%",
                display: "grid",
                gridTemplateColumns: "repeat(3, 1fr)",
                gap: 8,
                textAlign: "center",
              }}
            >
              <div
                style={{
                  background: theme.surface2,
                  padding: 8,
                  borderRadius: 6,
                }}
              >
                <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>
                  Toxicity %ile
                </div>
                <div style={{ fontSize: "0.95rem", fontWeight: 700 }}>
                  {data.toxicity_percentile != null ? `${data.toxicity_percentile}th` : "—"}
                </div>
              </div>
              <div
                style={{
                  background: theme.surface2,
                  padding: 8,
                  borderRadius: 6,
                }}
              >
                <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>
                  Bucket Size
                </div>
                <div style={{ fontSize: "0.95rem", fontWeight: 700 }}>
                  {data.bucket_size.toLocaleString()} sh
                </div>
              </div>
              <div
                style={{
                  background: theme.surface2,
                  padding: 8,
                  borderRadius: 6,
                }}
              >
                <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>
                  Defensive Gate
                </div>
                <div
                  style={{
                    fontSize: "0.95rem",
                    fontWeight: 700,
                    color: isHigh ? theme.decline : theme.textPrimary,
                  }}
                >
                  +{Number(data.defensive_spread_concession ?? 0).toFixed(2)} $
                </div>
              </div>
            </div>
          </div>

          {/* Card 2: Volume-Synchronized Trade Imbalance History Bar Chart */}
          <div
            style={{
              background: theme.surface,
              borderRadius: 12,
              border: `1px solid ${theme.border}`,
              padding: 20,
              display: "flex",
              flexDirection: "column",
              gap: 12,
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <div>
                <span style={{ fontSize: "1rem", fontWeight: 700 }}>
                  Volume Bucket Imbalance History (N={data.num_buckets})
                </span>
                <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
                  Volume-Synchronized Buy vs Sell Partitioning | |V^B - V^S| Imbalance
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: "0.75rem" }}>
                <span style={{ color: theme.growth }}>■ Buy Volume</span>
                <span style={{ color: theme.decline }}>■ Sell Volume</span>
              </div>
            </div>

            {/* Bar Chart Container */}
            <div
              style={{
                height: 180,
                display: "flex",
                alignItems: "flex-end",
                gap: 2,
                background: theme.surface2,
                padding: "16px 10px 10px 10px",
                borderRadius: 8,
                border: `1px solid ${theme.borderStrong}`,
                position: "relative",
              }}
            >
              {data.buckets.map((b) => {
                const buyFraction = b.buy_volume / b.total_volume;
                const sellFraction = b.sell_volume / b.total_volume;
                const isHovered = hoveredBucket?.bucket_index === b.bucket_index;

                return (
                  <div
                    key={b.bucket_index}
                    onMouseEnter={() => setHoveredBucket(b)}
                    onMouseLeave={() => setHoveredBucket(null)}
                    style={{
                      flex: 1,
                      height: "100%",
                      display: "flex",
                      flexDirection: "column-reverse",
                      justifyContent: "flex-start",
                      cursor: "pointer",
                      opacity: isHovered ? 1 : 0.85,
                      transform: isHovered ? "scaleY(1.05)" : "none",
                      transition: "transform 0.1s ease, opacity 0.1s ease",
                    }}
                  >
                    {/* Buy Vol Bar Segment */}
                    <div
                      style={{
                        height: `${buyFraction * 100}%`,
                        background: theme.growth,
                        borderTopLeftRadius: sellFraction === 0 ? 2 : 0,
                        borderTopRightRadius: sellFraction === 0 ? 2 : 0,
                      }}
                    />
                    {/* Sell Vol Bar Segment */}
                    <div
                      style={{
                        height: `${sellFraction * 100}%`,
                        background: theme.decline,
                        borderTopLeftRadius: 2,
                        borderTopRightRadius: 2,
                      }}
                    />
                  </div>
                );
              })}
            </div>

            {/* Hovered Bucket Detail Readout */}
            <div
              style={{
                minHeight: 52,
                padding: "8px 12px",
                background: theme.surface2,
                borderRadius: 6,
                fontSize: "0.8rem",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                flexWrap: "wrap",
                gap: 8,
              }}
            >
              {hoveredBucket ? (
                <>
                  <span>
                    Bucket <b>#{hoveredBucket.bucket_index}</b> ({hoveredBucket.total_volume.toLocaleString()} sh)
                  </span>
                  <span>
                    Price: <b>${hoveredBucket.price_start.toFixed(2)} → ${hoveredBucket.price_end.toFixed(2)}</b> (
                    <span
                      style={{
                        color:
                          hoveredBucket.price_change >= 0
                            ? theme.growth
                            : theme.decline,
                      }}
                    >
                      {hoveredBucket.price_change >= 0 ? "+" : ""}
                      {hoveredBucket.price_change.toFixed(2)}
                    </span>
                    )
                  </span>
                  <span>
                    Buy: <b style={{ color: theme.growth }}>{hoveredBucket.buy_volume.toLocaleString()}</b> / Sell:{" "}
                    <b style={{ color: theme.decline }}>{hoveredBucket.sell_volume.toLocaleString()}</b>
                  </span>
                  <span>
                    Imbalance: <b>{hoveredBucket.imbalance.toLocaleString()} sh</b>
                  </span>
                </>
              ) : (
                <span style={{ color: theme.textSecondary, fontStyle: "italic" }}>
                  Hover over any volume bucket bar to inspect detailed price progression and order imbalance.
                </span>
              )}
            </div>

            {/* Microstructure Math Card */}
            <div
              style={{
                fontSize: "0.75rem",
                color: theme.textMuted,
                background: theme.surface2,
                padding: "8px 12px",
                borderRadius: 6,
                lineHeight: 1.4,
              }}
            >
              📐 <b>BVC Formula:</b> V_τ^B = V_τ · Φ(ΔP_τ / σ_ΔP), V_τ^S = V_τ - V_τ^B.
              VPIN measures the rolling average order imbalance normalized by total volume over N={data.num_buckets} buckets.
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
