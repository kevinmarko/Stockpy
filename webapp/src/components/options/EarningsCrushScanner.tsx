import React, { useState } from "react";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { useMutation } from "../../hooks/useMutation";
import { theme } from "../../theme";
import type {
  EarningsCrushCandidate,
  EarningsCrushCandidatesResponse,
  EarningsCrushExecutionResult,
} from "../../api/types";

interface EarningsCrushScannerProps {
  initialSymbols?: string[];
  onTradeExecuted?: (result: EarningsCrushExecutionResult) => void;
  onSelectTicker?: (symbol: string) => void;
  onClose?: () => void;
}

export const EarningsCrushScanner: React.FC<EarningsCrushScannerProps> = ({
  initialSymbols,
  onTradeExecuted,
  onSelectTicker,
  onClose,
}) => {
  const [filterEdgeOnly, setFilterEdgeOnly] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [sortField, setSortField] = useState<"edge" | "date" | "move" | "credit">("edge");
  const [sortAsc, setSortAsc] = useState(false);
  const [expandedSymbol, setExpandedSymbol] = useState<string | null>(null);
  const [executingSymbol, setExecutingSymbol] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<{ text: string; type: "success" | "error" } | null>(null);
  // earnings_crush is an UNGATEABLE_DATA_GAP (see CLAUDE.md's "Options desk ML/safety
  // gates and findings" bullet) -- the backend blocks every request by default and only
  // proceeds when override_deployability_gate: true is set explicitly. Tracks which
  // candidate's first (unblocked) attempt just came back blocked, so its row can offer
  // a distinct, deliberate "override & execute anyway" action instead of silently
  // failing forever.
  const [blockedSymbol, setBlockedSymbol] = useState<string | null>(null);

  const query = useApi<EarningsCrushCandidatesResponse>(
    () => api.getEarningsCrushCandidates(initialSymbols),
    [initialSymbols]
  );

  const executeMutation = useMutation((candidate: EarningsCrushCandidate, override: boolean) =>
    api.executeEarningsCrushTrade(candidate, override)
  );

  const candidates: EarningsCrushCandidate[] = query.data?.candidates || [];

  const handleExecuteTrade = async (c: EarningsCrushCandidate, e: React.MouseEvent, override = false) => {
    e.stopPropagation();
    setExecutingSymbol(c.symbol);
    setStatusMessage(null);
    try {
      const res = await executeMutation.run(c, override);
      if (res && res.ok) {
        setBlockedSymbol(null);
        setStatusMessage({
          text: res.message || `Successfully executed ${res.strategy} on ${res.symbol} (Credit: $${res.net_credit?.toFixed(2) ?? "—"})`,
          type: "success",
        });
        if (onTradeExecuted) {
          onTradeExecuted(res);
        }
      } else if (res && res.blocked) {
        setBlockedSymbol(c.symbol);
        setStatusMessage({ text: res.message, type: "error" });
      } else {
        setBlockedSymbol(null);
        setStatusMessage({
          text: executeMutation.error || `Failed to execute trade on ${c.symbol}.`,
          type: "error",
        });
      }
    } finally {
      setExecutingSymbol(null);
    }
  };

  const handleConfirmOverride = (c: EarningsCrushCandidate, e: React.MouseEvent) => {
    e.stopPropagation();
    const reason = statusMessage?.text || "This strategy is blocked by a deployability gate.";
    const confirmed = window.confirm(
      `⚠️ Deployability gate override\n\n${reason}\n\nThis places a PAPER (simulated) trade on ${c.symbol} only -- no real capital is at risk. Override and execute anyway?`
    );
    if (confirmed) {
      handleExecuteTrade(c, e, true);
    }
  };

  const handleSort = (field: "edge" | "date" | "move" | "credit") => {
    if (sortField === field) {
      setSortAsc(!sortAsc);
    } else {
      setSortField(field);
      setSortAsc(false);
    }
  };

  let filtered = candidates.filter((c) => {
    if (filterEdgeOnly && c.crush_edge_ratio < 1.25) return false;
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toUpperCase();
      const symMatch = c.symbol.toUpperCase().includes(q);
      const nameMatch = c.company_name?.toUpperCase().includes(q);
      if (!symMatch && !nameMatch) return false;
    }
    return true;
  });

  filtered = [...filtered].sort((a, b) => {
    let diff = 0;
    if (sortField === "edge") {
      diff = a.crush_edge_ratio - b.crush_edge_ratio;
    } else if (sortField === "date") {
      diff = new Date(a.report_date).getTime() - new Date(b.report_date).getTime();
    } else if (sortField === "move") {
      diff = a.expected_move_pct - b.expected_move_pct;
    } else if (sortField === "credit") {
      diff = (a.estimated_credit || 0) - (b.estimated_credit || 0);
    }
    return sortAsc ? diff : -diff;
  });

  const favorableCount = candidates.filter((c) => c.crush_edge_ratio >= 1.25).length;
  const avgEdge = candidates.length > 0
    ? (candidates.reduce((sum, c) => sum + c.crush_edge_ratio, 0) / candidates.length).toFixed(2)
    : "—";

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
              <span>⚡ Earnings Volatility Crush Scanner</span>
            </h2>
            <span
              style={{
                fontSize: 11,
                fontWeight: 600,
                padding: "2px 8px",
                borderRadius: 4,
                background: "rgba(16, 185, 129, 0.15)",
                color: theme.growth,
              }}
            >
              Phase 10
            </span>
          </div>
          <div style={{ fontSize: 13, color: theme.textSecondary, marginTop: 4 }}>
            Quantitative overpricing scanner: Identifies IV crush mispricing where{" "}
            <strong>Expected Move / Median Realized Move &ge; 1.25x</strong>. Auto-enters 15m pre-close, exits 9:35 AM post-earnings.
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            onClick={() => query.reload()}
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
            {query.loading ? "Scanning..." : "🔄 Scan"}
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

      {/* Summary KPI Badges */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
        <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
          <div style={{ fontSize: 11, color: theme.textSecondary }}>Upcoming Events</div>
          <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4 }}>{candidates.length}</div>
        </div>
        <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
          <div style={{ fontSize: 11, color: theme.textSecondary }}>Edge &ge; 1.25x (Tradeable)</div>
          <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4, color: theme.growth }}>
            {favorableCount}
          </div>
        </div>
        <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
          <div style={{ fontSize: 11, color: theme.textSecondary }}>Mean Crush Edge</div>
          <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4, color: theme.accent }}>
            {avgEdge}x
          </div>
        </div>
        <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
          <div style={{ fontSize: 11, color: theme.textSecondary }}>Automated Wing Target</div>
          <div style={{ fontSize: 14, fontWeight: 600, marginTop: 6, color: theme.textSecondary }}>
            1.20x Implied Move
          </div>
        </div>
      </div>

      {/* Filter & Search Bar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            onClick={() => setFilterEdgeOnly(false)}
            style={{
              padding: "6px 12px",
              background: !filterEdgeOnly ? theme.accent : theme.surface2,
              color: !filterEdgeOnly ? "#000" : theme.textPrimary,
              border: `1px solid ${!filterEdgeOnly ? theme.accent : theme.border}`,
              borderRadius: 4,
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            All Upcoming ({candidates.length})
          </button>
          <button
            onClick={() => setFilterEdgeOnly(true)}
            style={{
              padding: "6px 12px",
              background: filterEdgeOnly ? theme.growth : theme.surface2,
              color: filterEdgeOnly ? "#000" : theme.textPrimary,
              border: `1px solid ${filterEdgeOnly ? theme.growth : theme.border}`,
              borderRadius: 4,
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Edge &ge; 1.25x Only ({favorableCount})
          </button>
        </div>

        <input
          type="text"
          placeholder="Filter by ticker or name..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{
            padding: "6px 12px",
            background: theme.base,
            border: `1px solid ${theme.border}`,
            color: theme.textPrimary,
            borderRadius: 4,
            fontSize: 12,
            minWidth: 200,
          }}
        />
      </div>

      {/* Execution Feedback Notification */}
      {statusMessage && (
        <div
          style={{
            padding: "10px 14px",
            background: statusMessage.type === "success" ? "rgba(16, 185, 129, 0.15)" : "rgba(239, 68, 68, 0.15)",
            border: `1px solid ${statusMessage.type === "success" ? theme.growth : theme.decline}`,
            color: statusMessage.type === "success" ? theme.growth : theme.decline,
            borderRadius: 6,
            fontSize: 13,
            fontWeight: 500,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>{statusMessage.text}</span>
          <button
            onClick={() => {
              setStatusMessage(null);
              // Dismissing the disclosed reason retires the row's override affordance
              // too -- re-clicking "Trade Crush Spread" re-surfaces the honest reason
              // before offering to override again, so the override is never silent.
              setBlockedSymbol(null);
            }}
            style={{ background: "transparent", border: "none", color: "inherit", cursor: "pointer", fontSize: 14 }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Main Table */}
      {query.loading && !candidates.length ? (
        <div style={{ padding: 32, textAlign: "center", color: theme.textSecondary }}>
          Scanning earnings calendar & volatility mispricing...
        </div>
      ) : filtered.length === 0 ? (
        <div style={{ padding: 32, textAlign: "center", color: theme.textSecondary }}>
          No earnings crush candidates match the selected filters.
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                <th
                  style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600, cursor: "pointer" }}
                  onClick={() => handleSort("date")}
                >
                  Ticker / Date {sortField === "date" ? (sortAsc ? "▲" : "▼") : ""}
                </th>
                <th style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600, textAlign: "right" }}>
                  Spot / ATM IV
                </th>
                <th
                  style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600, textAlign: "right", cursor: "pointer" }}
                  onClick={() => handleSort("move")}
                >
                  Expected Move {sortField === "move" ? (sortAsc ? "▲" : "▼") : ""}
                </th>
                <th style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600, textAlign: "right" }}>
                  8Q Med Realized
                </th>
                <th
                  style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600, textAlign: "right", cursor: "pointer" }}
                  onClick={() => handleSort("edge")}
                >
                  Crush Edge Ratio {sortField === "edge" ? (sortAsc ? "▲" : "▼") : ""}
                </th>
                <th style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600 }}>
                  Recommended Spreads &amp; Wings
                </th>
                <th
                  style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600, textAlign: "right", cursor: "pointer" }}
                  onClick={() => handleSort("credit")}
                >
                  Est. Credit {sortField === "credit" ? (sortAsc ? "▲" : "▼") : ""}
                </th>
                <th style={{ padding: "10px 12px", color: theme.textSecondary, fontWeight: 600, textAlign: "center" }}>
                  Action
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => {
                const isEdgeFavorable = c.crush_edge_ratio >= 1.25;
                const isExpanded = expandedSymbol === c.symbol;
                const isExecuting = executingSymbol === c.symbol;

                return (
                  <React.Fragment key={c.symbol}>
                    <tr
                      onClick={() => setExpandedSymbol(isExpanded ? null : c.symbol)}
                      style={{
                        borderBottom: `1px solid ${theme.border}`,
                        background: isExpanded ? "rgba(255, 255, 255, 0.02)" : "transparent",
                        cursor: "pointer",
                      }}
                    >
                      <td style={{ padding: "12px 12px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span
                            onClick={(e) => {
                              if (onSelectTicker) {
                                e.stopPropagation();
                                onSelectTicker(c.symbol);
                              }
                            }}
                            style={{
                              fontWeight: 700,
                              fontSize: 14,
                              color: theme.textPrimary,
                              cursor: onSelectTicker ? "pointer" : "default",
                              textDecoration: onSelectTicker ? "underline" : "none",
                            }}
                          >
                            {c.symbol}
                          </span>
                          {c.report_timing && (
                            <span
                              style={{
                                fontSize: 10,
                                fontWeight: 700,
                                padding: "2px 5px",
                                borderRadius: 3,
                                background: c.report_timing === "AMC" ? "rgba(139, 92, 246, 0.15)" : "rgba(245, 158, 11, 0.15)",
                                color: c.report_timing === "AMC" ? "#a78bfa" : "#fbbf24",
                              }}
                            >
                              {c.report_timing}
                            </span>
                          )}
                          <span style={{ fontSize: 11, color: theme.textSecondary }}>{c.dte}d DTE</span>
                        </div>
                        <div style={{ fontSize: 11, color: theme.textSecondary, marginTop: 2 }}>
                          {c.report_date} {c.company_name ? `• ${c.company_name}` : ""}
                        </div>
                      </td>

                      <td style={{ padding: "12px 12px", textAlign: "right" }}>
                        <div style={{ fontWeight: 600 }}>${c.spot_price.toFixed(2)}</div>
                        <div style={{ fontSize: 11, color: theme.accent }}>{(c.atm_iv * 100).toFixed(1)}% IV</div>
                      </td>

                      <td style={{ padding: "12px 12px", textAlign: "right" }}>
                        <div style={{ fontWeight: 600, color: theme.caution }}>
                          &plusmn;{(c.expected_move_pct * 100).toFixed(1)}%
                        </div>
                        <div style={{ fontSize: 11, color: theme.textSecondary }}>
                          &plusmn;${c.expected_move_dollar.toFixed(2)}
                        </div>
                      </td>

                      <td style={{ padding: "12px 12px", textAlign: "right" }}>
                        <div style={{ fontWeight: 500 }}>{(c.median_realized_move_pct * 100).toFixed(1)}%</div>
                        <div style={{ fontSize: 11, color: theme.textSecondary }}>8-Qtr Median</div>
                      </td>

                      <td style={{ padding: "12px 12px", textAlign: "right" }}>
                        <span
                          style={{
                            display: "inline-block",
                            padding: "3px 8px",
                            borderRadius: 4,
                            fontWeight: 700,
                            fontSize: 12,
                            background: isEdgeFavorable ? "rgba(16, 185, 129, 0.18)" : "rgba(148, 163, 184, 0.12)",
                            color: isEdgeFavorable ? theme.growth : theme.textSecondary,
                            border: `1px solid ${isEdgeFavorable ? "rgba(16, 185, 129, 0.4)" : "rgba(148, 163, 184, 0.3)"}`,
                          }}
                        >
                          {c.crush_edge_ratio.toFixed(2)}x
                        </span>
                      </td>

                      <td style={{ padding: "12px 12px" }}>
                        <div style={{ fontWeight: 600, fontSize: 12 }}>
                          {c.suggested_strategy || "Iron Condor"}
                        </div>
                        {c.short_put_strike != null && c.short_call_strike != null ? (
                          <div style={{ fontSize: 11, color: theme.textSecondary, marginTop: 2 }}>
                            {c.put_wing_strike}P / {c.short_put_strike}P &mdash; {c.short_call_strike}C / {c.call_wing_strike}C
                          </div>
                        ) : (
                          <div style={{ fontSize: 11, color: theme.textSecondary, marginTop: 2 }}>
                            Exp: {c.expiration || "Front-Week"}
                          </div>
                        )}
                      </td>

                      <td style={{ padding: "12px 12px", textAlign: "right" }}>
                        <div style={{ fontWeight: 600, color: theme.growth }}>
                          {c.estimated_credit != null ? `$${c.estimated_credit.toFixed(2)}` : "—"}
                        </div>
                        <div style={{ fontSize: 10, color: theme.textSecondary }}>per spread</div>
                      </td>

                      <td style={{ padding: "12px 12px", textAlign: "center" }}>
                        {blockedSymbol === c.symbol ? (
                          <button
                            onClick={(e) => handleConfirmOverride(c, e)}
                            disabled={isExecuting || executeMutation.pending}
                            title="This strategy has an unmeasurable deployability gap. Overriding places a paper (simulated) trade only."
                            style={{
                              padding: "6px 12px",
                              background: theme.caution,
                              border: "none",
                              color: "#000",
                              borderRadius: 4,
                              cursor: isExecuting || executeMutation.pending ? "not-allowed" : "pointer",
                              fontWeight: 600,
                              fontSize: 12,
                              whiteSpace: "nowrap",
                              opacity: isExecuting ? 0.6 : 1,
                            }}
                          >
                            {isExecuting ? "Executing..." : "⚠️ Override & Execute"}
                          </button>
                        ) : (
                          <button
                            onClick={(e) => handleExecuteTrade(c, e)}
                            disabled={isExecuting || executeMutation.pending}
                            style={{
                              padding: "6px 12px",
                              background: isEdgeFavorable ? theme.accent : theme.surface2,
                              border: "none",
                              color: isEdgeFavorable ? "#000" : theme.textPrimary,
                              borderRadius: 4,
                              cursor: isExecuting || executeMutation.pending ? "not-allowed" : "pointer",
                              fontWeight: 600,
                              fontSize: 12,
                              whiteSpace: "nowrap",
                              opacity: isExecuting ? 0.6 : 1,
                            }}
                          >
                            {isExecuting ? "Executing..." : "⚡ Trade Crush Spread"}
                          </button>
                        )}
                      </td>
                    </tr>

                    {/* Expanded 8-Quarter Historical Move Breakdown */}
                    {isExpanded && (
                      <tr style={{ background: "rgba(0, 0, 0, 0.2)", borderBottom: `1px solid ${theme.border}` }}>
                        <td colSpan={8} style={{ padding: "14px 20px" }}>
                          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                              <span style={{ fontSize: 12, fontWeight: 600, color: theme.textPrimary }}>
                                📊 {c.symbol} Prior 8 Quarters Realized Post-Earnings Move vs Implied Expected Move ({(c.expected_move_pct * 100).toFixed(1)}%)
                              </span>
                              <span style={{ fontSize: 11, color: theme.textSecondary }}>
                                Historical Crush Edge: <strong>{c.crush_edge_ratio.toFixed(2)}x</strong> ({isEdgeFavorable ? "Overpriced Implied Move" : "Fair / Underpriced"})
                              </span>
                            </div>

                            {c.historical_moves && c.historical_moves.length > 0 ? (
                              <div style={{ display: "flex", gap: 8, alignItems: "flex-end", height: 60, marginTop: 4 }}>
                                {c.historical_moves.map((move, idx) => {
                                  const impliedPct = c.expected_move_pct * 100;
                                  const isBelowImplied = move <= impliedPct;
                                  const maxScale = Math.max(impliedPct * 1.3, ...c.historical_moves!, 10);
                                  const barHeight = Math.max(8, (move / maxScale) * 50);

                                  return (
                                    <div
                                      key={idx}
                                      style={{
                                        flex: 1,
                                        display: "flex",
                                        flexDirection: "column",
                                        alignItems: "center",
                                        gap: 4,
                                      }}
                                    >
                                      <span style={{ fontSize: 10, color: theme.textSecondary }}>{move.toFixed(1)}%</span>
                                      <div
                                        style={{
                                          width: "100%",
                                          height: `${barHeight}px`,
                                          background: isBelowImplied ? theme.growth : theme.decline,
                                          borderRadius: "3px 3px 0 0",
                                          opacity: 0.85,
                                        }}
                                        title={`Q-${8 - idx}: Realized Move ${move.toFixed(1)}% (${isBelowImplied ? "Captured by Wing" : "Breached Wing"})`}
                                      />
                                      <span style={{ fontSize: 9, color: theme.textMuted }}>Q-{8 - idx}</span>
                                    </div>
                                  );
                                })}
                              </div>
                            ) : (
                              <div style={{ fontSize: 12, color: theme.textSecondary, fontStyle: "italic" }}>
                                8-quarter history being ingested from HistoricalStore.
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
