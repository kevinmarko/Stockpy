import React, { useState } from "react";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { theme } from "../../theme";
import type {
  UnusualOptionTrade,
  UnusualOptionsFlowResponse,
  FlowSentimentResponse,
  FlowSentimentData,
} from "../../api/types";

interface UnusualFlowFeedProps {
  initialSymbol?: string;
  onSelectTicker?: (symbol: string) => void;
  onClose?: () => void;
}

export const UnusualFlowFeed: React.FC<UnusualFlowFeedProps> = ({
  initialSymbol,
  onSelectTicker,
  onClose,
}) => {
  const [symbolFilter, setSymbolFilter] = useState<string>(initialSymbol || "");
  const [minVolOi, setMinVolOi] = useState<number>(3.0);
  const [minNotional, setMinNotional] = useState<number>(100000);
  const [sentimentFilter, setSentimentFilter] = useState<"ALL" | "BULLISH" | "BEARISH">("ALL");
  const [tradeTypeFilter, setTradeTypeFilter] = useState<"ALL" | "SWEEP" | "BLOCK">("ALL");
  const [selectedTickerForSentiment, setSelectedTickerForSentiment] = useState<string>(
    initialSymbol || "NVDA"
  );

  const flowQuery = useApi<UnusualOptionsFlowResponse>(
    () =>
      api.getUnusualOptionsFlow({
        symbol: symbolFilter.trim() ? symbolFilter.trim().toUpperCase() : undefined,
        min_vol_oi: minVolOi > 0 ? minVolOi : undefined,
        min_notional: minNotional > 0 ? minNotional : undefined,
      }),
    [symbolFilter, minVolOi, minNotional]
  );

  const sentimentQuery = useApi<FlowSentimentResponse>(
    () => api.getOptionsFlowSentiment(selectedTickerForSentiment),
    [selectedTickerForSentiment]
  );

  const trades: UnusualOptionTrade[] = flowQuery.data?.trades || [];
  const sentiment: FlowSentimentData | undefined = sentimentQuery.data?.sentiment;

  // Filter client-side for sentiment and trade type
  const filteredTrades = trades.filter((t) => {
    if (sentimentFilter !== "ALL" && t.sentiment !== sentimentFilter) return false;
    if (tradeTypeFilter !== "ALL" && t.trade_type !== tradeTypeFilter) return false;
    return true;
  });

  const totalFilteredNotional = filteredTrades.reduce((acc, t) => acc + t.notional, 0);
  const bullishTradesCount = filteredTrades.filter((t) => t.sentiment === "BULLISH").length;
  const bearishTradesCount = filteredTrades.filter((t) => t.sentiment === "BEARISH").length;

  const formatNotional = (n: number) => {
    if (n >= 1000000) {
      return `$${(n / 1000000).toFixed(2)}M`;
    }
    return `$${(n / 1000).toFixed(0)}k`;
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
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
              <span>🌊 Unusual Options Activity &amp; Order Flow Feed</span>
            </h2>
            <span
              style={{
                fontSize: 11,
                fontWeight: 600,
                padding: "2px 8px",
                borderRadius: 4,
                background: "rgba(99, 102, 241, 0.15)",
                color: "#818cf8",
              }}
            >
              Phase 11
            </span>
          </div>
          <div style={{ fontSize: 13, color: theme.textSecondary, marginTop: 4 }}>
            Institutional order flow scanner: Aggressive sweeps, block trades ($V/\text&#123;OI&#125; \ge 3.0\times$), and real-time flow sentiment gauge.
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            onClick={() => {
              flowQuery.reload();
              sentimentQuery.reload();
            }}
            disabled={flowQuery.loading}
            style={{
              padding: "6px 12px",
              background: theme.surface2,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 4,
              cursor: flowQuery.loading ? "not-allowed" : "pointer",
              fontSize: 12,
              fontWeight: 500,
            }}
          >
            {flowQuery.loading ? "Streaming..." : "🔄 Refresh Flow"}
          </button>
          {onClose && (
            <button
              onClick={onClose}
              style={{
                padding: "6px 12px",
                background: "transparent",
                border: `1px solid ${theme.border}`,
                color: theme.textSecondary,
                borderRadius: 4,
                cursor: "pointer",
                fontSize: 12,
              }}
            >
              ✕ Close
            </button>
          )}
        </div>
      </div>

      {/* Institutional Flow Sentiment Gauge Component */}
      {sentiment && (
        <div
          style={{
            padding: 16,
            background: theme.base,
            borderRadius: 8,
            border: `1px solid ${theme.border}`,
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 14, fontWeight: 700, color: theme.textPrimary }}>
                🎯 Institutional Net Flow Sentiment:
              </span>
              <span style={{ fontSize: 14, fontWeight: 700, color: theme.accent }}>
                {sentiment.symbol}
              </span>
              <span
                style={{
                  fontSize: 12,
                  fontWeight: 700,
                  padding: "2px 8px",
                  borderRadius: 4,
                  background:
                    sentiment.sentiment_score > 0.15
                      ? "rgba(16, 185, 129, 0.15)"
                      : sentiment.sentiment_score < -0.15
                      ? "rgba(239, 68, 68, 0.15)"
                      : "rgba(148, 163, 184, 0.15)",
                  color:
                    sentiment.sentiment_score > 0.15
                      ? theme.growth
                      : sentiment.sentiment_score < -0.15
                      ? theme.decline
                      : theme.textSecondary,
                }}
              >
                {sentiment.sentiment_score > 0.15
                  ? `BULLISH (${(sentiment.sentiment_score * 100).toFixed(0)}%)`
                  : sentiment.sentiment_score < -0.15
                  ? `BEARISH (${(sentiment.sentiment_score * 100).toFixed(0)}%)`
                  : `NEUTRAL (${(sentiment.sentiment_score * 100).toFixed(0)}%)`}
              </span>
            </div>

            <div style={{ display: "flex", gap: 16, fontSize: 12, color: theme.textSecondary }}>
              <span>
                🟢 Bullish: <strong>{formatNotional(sentiment.bullish_notional)}</strong>
              </span>
              <span>
                🔴 Bearish: <strong>{formatNotional(sentiment.bearish_notional)}</strong>
              </span>
              <span>
                P/C Ratio: <strong>{sentiment.put_call_ratio.toFixed(2)}</strong>
              </span>
            </div>
          </div>

          {/* Visual Sentiment Meter [-100% to +100%] */}
          <div style={{ position: "relative", width: "100%", height: 24, marginTop: 4 }}>
            {/* Background Bar */}
            <div
              style={{
                width: "100%",
                height: 10,
                borderRadius: 5,
                background: "linear-gradient(to right, #ef4444 0%, #64748b 50%, #10b981 100%)",
                position: "absolute",
                top: 7,
              }}
            />
            {/* Center Zero Mark */}
            <div
              style={{
                position: "absolute",
                left: "50%",
                top: 2,
                width: 2,
                height: 20,
                background: theme.textPrimary,
                opacity: 0.6,
                transform: "translateX(-50%)",
              }}
            />
            {/* Needle / Indicator Position: maps score [-1.0, 1.0] -> [0%, 100%] */}
            <div
              style={{
                position: "absolute",
                left: `${Math.min(100, Math.max(0, ((sentiment.sentiment_score + 1.0) / 2.0) * 100))}%`,
                top: 0,
                width: 14,
                height: 24,
                background: "#ffffff",
                border: "2px solid #0f172a",
                borderRadius: 3,
                transform: "translateX(-50%)",
                boxShadow: "0 2px 6px rgba(0,0,0,0.5)",
                transition: "left 0.3s ease-out",
              }}
              title={`Sentiment Score: ${(sentiment.sentiment_score * 100).toFixed(1)}%`}
            />
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: theme.textMuted, marginTop: -6 }}>
            <span>-100% (Max Bearish Flow)</span>
            <span>0% (Neutral)</span>
            <span>+100% (Max Bullish Flow)</span>
          </div>

          {/* Top Active Strikes Pill List */}
          {sentiment.top_active_strikes && sentiment.top_active_strikes.length > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginTop: 2 }}>
              <span style={{ fontSize: 11, color: theme.textSecondary, fontWeight: 600 }}>
                High-Volume Nodes:
              </span>
              {sentiment.top_active_strikes.map((s, idx) => (
                <span
                  key={idx}
                  style={{
                    fontSize: 11,
                    padding: "2px 6px",
                    borderRadius: 4,
                    background: s.option_type === "CALL" ? "rgba(16, 185, 129, 0.12)" : "rgba(239, 68, 68, 0.12)",
                    color: s.option_type === "CALL" ? theme.growth : theme.decline,
                    border: `1px solid ${s.option_type === "CALL" ? "rgba(16, 185, 129, 0.3)" : "rgba(239, 68, 68, 0.3)"}`,
                  }}
                >
                  ${s.strike} {s.option_type} ({formatNotional(s.notional)})
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Filter Controls Row */}
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", justifyContent: "space-between" }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          {/* Ticker Search */}
          <input
            type="text"
            placeholder="Filter Ticker (e.g. NVDA)..."
            value={symbolFilter}
            onChange={(e) => {
              const val = e.target.value;
              setSymbolFilter(val);
              if (val.trim()) {
                setSelectedTickerForSentiment(val.trim().toUpperCase());
              }
            }}
            style={{
              padding: "6px 10px",
              background: theme.base,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 4,
              fontSize: 12,
              width: 160,
            }}
          />

          {/* Min V/OI Filter */}
          <select
            value={minVolOi}
            onChange={(e) => setMinVolOi(Number(e.target.value))}
            style={{
              padding: "6px 10px",
              background: theme.base,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 4,
              fontSize: 12,
            }}
          >
            <option value={0}>All V/OI Ratios</option>
            <option value={3.0}>V/OI &ge; 3.0x (Anomalous)</option>
            <option value={4.0}>V/OI &ge; 4.0x (High Conviction)</option>
            <option value={5.0}>V/OI &ge; 5.0x (Extreme Spike)</option>
          </select>

          {/* Min Notional Filter */}
          <select
            value={minNotional}
            onChange={(e) => setMinNotional(Number(e.target.value))}
            style={{
              padding: "6px 10px",
              background: theme.base,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 4,
              fontSize: 12,
            }}
          >
            <option value={0}>All Notionals</option>
            <option value={100000}>Notional &ge; $100k</option>
            <option value={500000}>Notional &ge; $500k</option>
            <option value={1000000}>Notional &ge; $1.0M</option>
          </select>

          {/* Sentiment Filter Pills */}
          <div style={{ display: "flex", gap: 4 }}>
            <button
              onClick={() => setSentimentFilter("ALL")}
              style={{
                padding: "5px 10px",
                background: sentimentFilter === "ALL" ? theme.accent : theme.surface2,
                color: sentimentFilter === "ALL" ? "#000" : theme.textPrimary,
                border: "none",
                borderRadius: 4,
                fontSize: 11,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              All Sentiment
            </button>
            <button
              onClick={() => setSentimentFilter("BULLISH")}
              style={{
                padding: "5px 10px",
                background: sentimentFilter === "BULLISH" ? theme.growth : theme.surface2,
                color: sentimentFilter === "BULLISH" ? "#000" : theme.textPrimary,
                border: "none",
                borderRadius: 4,
                fontSize: 11,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              🟢 Bullish ({bullishTradesCount})
            </button>
            <button
              onClick={() => setSentimentFilter("BEARISH")}
              style={{
                padding: "5px 10px",
                background: sentimentFilter === "BEARISH" ? theme.decline : theme.surface2,
                color: sentimentFilter === "BEARISH" ? "#fff" : theme.textPrimary,
                border: "none",
                borderRadius: 4,
                fontSize: 11,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              🔴 Bearish ({bearishTradesCount})
            </button>
          </div>

          {/* Trade Type Filter */}
          <div style={{ display: "flex", gap: 4 }}>
            <button
              onClick={() => setTradeTypeFilter("ALL")}
              style={{
                padding: "5px 10px",
                background: tradeTypeFilter === "ALL" ? theme.surface3 : theme.surface2,
                color: theme.textPrimary,
                border: `1px solid ${theme.border}`,
                borderRadius: 4,
                fontSize: 11,
                cursor: "pointer",
              }}
            >
              All Types
            </button>
            <button
              onClick={() => setTradeTypeFilter("SWEEP")}
              style={{
                padding: "5px 10px",
                background: tradeTypeFilter === "SWEEP" ? theme.surface3 : theme.surface2,
                color: theme.accent,
                border: `1px solid ${theme.border}`,
                borderRadius: 4,
                fontSize: 11,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              ⚡ Sweeps
            </button>
            <button
              onClick={() => setTradeTypeFilter("BLOCK")}
              style={{
                padding: "5px 10px",
                background: tradeTypeFilter === "BLOCK" ? theme.surface3 : theme.surface2,
                color: "#94a3b8",
                border: `1px solid ${theme.border}`,
                borderRadius: 4,
                fontSize: 11,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              🏢 Blocks
            </button>
          </div>
        </div>

        <div style={{ fontSize: 12, color: theme.textSecondary }}>
          Showing <strong>{filteredTrades.length}</strong> trades ({formatNotional(totalFilteredNotional)} total notional)
        </div>
      </div>

      {/* Main Order Flow Table Feed */}
      {flowQuery.loading && !trades.length ? (
        <div style={{ padding: 32, textAlign: "center", color: theme.textSecondary }}>
          Streaming live options orders & detecting sweeps...
        </div>
      ) : filteredTrades.length === 0 ? (
        <div style={{ padding: 32, textAlign: "center", color: theme.textSecondary }}>
          No unusual option activity matches the selected criteria.
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                <th style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600 }}>Time / Symbol</th>
                <th style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600 }}>Contract Details</th>
                <th style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600 }}>Trade Type / Side</th>
                <th style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600, textAlign: "right" }}>
                  Volume / OI
                </th>
                <th style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600, textAlign: "right" }}>
                  V/OI Multiplier
                </th>
                <th style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600, textAlign: "right" }}>
                  Fill Price
                </th>
                <th style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600, textAlign: "right" }}>
                  Total Notional
                </th>
                <th style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600, textAlign: "center" }}>
                  Sentiment Tag
                </th>
              </tr>
            </thead>
            <tbody>
              {filteredTrades.map((t) => {
                const isBullish = t.sentiment === "BULLISH";
                const isSweep = t.trade_type === "SWEEP";

                return (
                  <tr
                    key={t.id}
                    onClick={() => {
                      setSelectedTickerForSentiment(t.symbol);
                      if (onSelectTicker) onSelectTicker(t.symbol);
                    }}
                    style={{
                      borderBottom: `1px solid ${theme.border}`,
                      cursor: "pointer",
                      transition: "background 0.15s",
                    }}
                  >
                    <td style={{ padding: "12px 12px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span style={{ fontWeight: 700, fontSize: 14, color: theme.textPrimary }}>
                          {t.symbol}
                        </span>
                        {t.spot_price != null && (
                          <span style={{ fontSize: 11, color: theme.textSecondary }}>
                            ${t.spot_price.toFixed(2)}
                          </span>
                        )}
                      </div>
                      <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>
                        {t.timestamp}
                      </div>
                    </td>

                    <td style={{ padding: "12px 12px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span
                          style={{
                            fontWeight: 700,
                            padding: "2px 6px",
                            borderRadius: 3,
                            fontSize: 11,
                            background: t.option_type === "CALL" ? "rgba(16, 185, 129, 0.15)" : "rgba(239, 68, 68, 0.15)",
                            color: t.option_type === "CALL" ? theme.growth : theme.decline,
                          }}
                        >
                          ${t.strike.toFixed(1)} {t.option_type}
                        </span>
                        <span style={{ fontSize: 12, fontWeight: 500 }}>
                          {t.expiration} ({t.dte}d)
                        </span>
                      </div>
                      {t.iv != null && (
                        <div style={{ fontSize: 10, color: theme.accent, marginTop: 2 }}>
                          {(t.iv * 100).toFixed(1)}% IV {t.iv_expansion_flag ? "⚡ IV Surge" : ""}
                        </div>
                      )}
                    </td>

                    <td style={{ padding: "12px 12px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span
                          style={{
                            fontSize: 11,
                            fontWeight: 600,
                            padding: "2px 6px",
                            borderRadius: 4,
                            background: isSweep ? "rgba(245, 158, 11, 0.15)" : "rgba(100, 116, 139, 0.15)",
                            color: isSweep ? theme.caution : "#94a3b8",
                          }}
                        >
                          {isSweep ? "⚡ SWEEP" : "🏢 BLOCK"}
                        </span>
                        <span style={{ fontSize: 11, color: theme.textSecondary }}>
                          @{t.aggressor_side}
                        </span>
                      </div>
                    </td>

                    <td style={{ padding: "12px 12px", textAlign: "right" }}>
                      <div style={{ fontWeight: 600 }}>{t.volume.toLocaleString()}</div>
                      <div style={{ fontSize: 10, color: theme.textSecondary }}>
                        OI: {t.open_interest.toLocaleString()}
                      </div>
                    </td>

                    <td style={{ padding: "12px 12px", textAlign: "right" }}>
                      <span
                        style={{
                          display: "inline-block",
                          padding: "2px 7px",
                          borderRadius: 4,
                          fontWeight: 700,
                          fontSize: 12,
                          background: t.vol_oi_ratio >= 4.0 ? "rgba(16, 185, 129, 0.18)" : "rgba(245, 158, 11, 0.15)",
                          color: t.vol_oi_ratio >= 4.0 ? theme.growth : theme.caution,
                          border: `1px solid ${t.vol_oi_ratio >= 4.0 ? "rgba(16, 185, 129, 0.3)" : "rgba(245, 158, 11, 0.3)"}`,
                        }}
                      >
                        {t.vol_oi_ratio.toFixed(2)}x
                      </span>
                    </td>

                    <td style={{ padding: "12px 12px", textAlign: "right" }}>
                      <div style={{ fontWeight: 600 }}>${t.price.toFixed(2)}</div>
                    </td>

                    <td style={{ padding: "12px 12px", textAlign: "right" }}>
                      <div style={{ fontWeight: 700, color: theme.textPrimary }}>
                        {formatNotional(t.notional)}
                      </div>
                    </td>

                    <td style={{ padding: "12px 12px", textAlign: "center" }}>
                      <span
                        style={{
                          display: "inline-block",
                          padding: "4px 10px",
                          borderRadius: 4,
                          fontWeight: 700,
                          fontSize: 11,
                          letterSpacing: 0.5,
                          background: isBullish ? "rgba(16, 185, 129, 0.18)" : "rgba(239, 68, 68, 0.18)",
                          color: isBullish ? theme.growth : theme.decline,
                          border: `1px solid ${isBullish ? "rgba(16, 185, 129, 0.4)" : "rgba(239, 68, 68, 0.4)"}`,
                        }}
                      >
                        {isBullish ? "🟢 BULLISH" : "🔴 BEARISH"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
