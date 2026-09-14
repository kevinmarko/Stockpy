import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { theme, alpha } from "../theme";
import type {
  BatchRetrospectiveInsightsResponse,
  BridgeReliabilityResponse,
  PaperBrokerClosedTrade,
  RetrospectiveTradeRecord,
} from "../api/types";
import { RetrospectiveDetailModal } from "../components/RetrospectiveDetailModal";
import { TabGuide } from "../components/TabGuide";
import { Tile, Loading, EmptyState, ErrorState, Select, Notice } from "../components/ui";
import {
  BrainCircuit,
  FileText,
  HelpCircle,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Compass,
  ArrowRight,
  TrendingUp,
  Layers,
  RefreshCw,
  Sliders,
} from "lucide-react";

export function RetrospectiveJournal() {
  const [tab, setTab] = useState<"journal" | "insights">("journal");
  const [selectedTradeId, setSelectedTradeId] = useState<number | null>(null);

  // Filter state for Journal tab
  const [symbolFilter, setSymbolFilter] = useState<string>("");
  const [strategyFilter, setStrategyFilter] = useState<string>("all");
  const [provenanceFilter, setProvenanceFilter] = useState<string>("all");

  // Filter state for Insights tab
  const [insightStrategyFilter, setInsightStrategyFilter] = useState<string>("all");

  // Queries
  const bridge = useApi<BridgeReliabilityResponse>(() => api.getBridgeReliability());
  const closedTrades = useApi<PaperBrokerClosedTrade[]>(() => api.getPaperBrokerClosedTrades(100));
  const insights = useApi<BatchRetrospectiveInsightsResponse>(
    () =>
      api.getRetrospectiveInsights({
        strategy_id: insightStrategyFilter === "all" ? undefined : insightStrategyFilter,
      }),
    [insightStrategyFilter]
  );

  // Cache of individual retrospective records to enrich the closed trades table
  const [retroMap, setRetroMap] = useState<Record<number, RetrospectiveTradeRecord>>({});
  const [fetchingRetros, setFetchingRetros] = useState<boolean>(false);

  // When closed trades are loaded, proactively fetch retrospective records for visible trades
  useEffect(() => {
    if (!closedTrades.data || closedTrades.data.length === 0) return;
    const tradesToFetch = closedTrades.data.slice(0, 50).filter((t) => !retroMap[t.trade_id]);
    if (tradesToFetch.length === 0) return;

    setFetchingRetros(true);
    Promise.allSettled(
      tradesToFetch.map((t) =>
        api.getRetrospectiveTrade(t.trade_id).then((rec) => ({ id: t.trade_id, rec }))
      )
    ).then((results) => {
      setRetroMap((prev) => {
        const next = { ...prev };
        for (const res of results) {
          if (res.status === "fulfilled") {
            next[res.value.id] = res.value.rec;
          }
        }
        return next;
      });
      setFetchingRetros(false);
    });
  }, [closedTrades.data]);

  // Unique strategies for filter dropdown
  const strategyOptions = useMemo(() => {
    const set = new Set<string>();
    if (closedTrades.data) {
      for (const t of closedTrades.data) {
        if (t.strategy_id) set.add(t.strategy_id);
      }
    }
    const opts = [{ value: "all", label: "All Strategies" }];
    for (const s of Array.from(set).sort()) {
      opts.push({ value: s, label: s });
    }
    return opts;
  }, [closedTrades.data]);

  // Filtered trades
  const filteredTrades = useMemo(() => {
    if (!closedTrades.data) return [];
    return closedTrades.data.filter((t) => {
      // Symbol filter
      if (symbolFilter.trim() && !t.symbol.toUpperCase().includes(symbolFilter.trim().toUpperCase())) {
        return false;
      }
      // Strategy filter
      if (strategyFilter !== "all" && t.strategy_id !== strategyFilter) {
        return false;
      }
      // Provenance filter
      if (provenanceFilter !== "all") {
        const retro = retroMap[t.trade_id];
        const prov = retro ? retro.provenance : "unknown"; // Never infer provenance from strategy_id presence (the same anti-fabrication rule pilots/retrospective_composer.py itself enforces) -- until the composer's own read has resolved, the honest state is "unknown", not a guess.
        if (prov !== provenanceFilter) return false;
      }
      return true;
    });
  }, [closedTrades.data, symbolFilter, strategyFilter, provenanceFilter, retroMap]);

  return (
    <div className="screen" style={{ paddingBottom: "var(--s-8)" }}>
      {/* Header */}
      <div style={{ marginBottom: "var(--s-4)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Compass size={28} color="#818cf8" />
          <h1 className="screen-title" style={{ margin: 0 }}>
            Retrospective Journal
          </h1>
        </div>
        <p className="screen-sub" style={{ marginTop: "var(--s-1)" }}>
          Post-trade causal autopsy, hold-period excursion metrics (MAE/MFE), and cohort-isolated pattern insights.
        </p>
      </div>

      <TabGuide tabKey="retrospective" />

      {/* Bridge Health Banner */}
      <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-3)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Layers size={18} color={theme.textSecondary} />
            <span style={{ fontSize: 13, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.8, color: theme.textSecondary }}>
              Bridge Reliability &amp; Synchronization
            </span>
          </div>
          {bridge.data && (
            <span
              style={{
                fontSize: 12,
                fontWeight: 600,
                padding: "2px 8px",
                borderRadius: 4,
                // completeness_pct == null means genuinely unmeasured
                // (CONSTRAINT #4 -- the bridge store never fabricates a
                // percentage for a state it couldn't measure), not a bad
                // reading -- a prior version's `? pos : neg` fallback
                // painted "unknown" the same alarming red as a real
                // "degraded" measurement.
                backgroundColor:
                  bridge.data.completeness_pct == null
                    ? alpha(theme.textMuted, "20")
                    : bridge.data.completeness_pct >= 95
                    ? alpha(theme.growth, "20")
                    : alpha(theme.decline, "20"),
                color:
                  bridge.data.completeness_pct == null
                    ? theme.textMuted
                    : bridge.data.completeness_pct >= 95
                    ? theme.growth
                    : theme.decline,
              }}
            >
              {bridge.data.status === "healthy"
                ? "Bridge Synchronized"
                : bridge.data.status === "degraded"
                ? "Bridge Degraded"
                : `Status: ${bridge.data.status}`}
            </span>
          )}
        </div>

        <div className="tiles">
          <Tile
            label="Completeness"
            value={
              bridge.data && bridge.data.completeness_pct != null
                ? `${bridge.data.completeness_pct.toFixed(1)}%`
                : bridge.loading
                ? "…"
                : "—"
            }
            tone={
              !bridge.data || bridge.data.completeness_pct == null
                ? undefined
                : bridge.data.completeness_pct >= 95
                ? "pos"
                : "neg"
            }
          />
          <Tile
            label="Synced Trades"
            value={bridge.data ? bridge.data.bridged_count : bridge.loading ? "…" : "—"}
          />
          <Tile
            label="Total Closed"
            value={bridge.data ? bridge.data.total_closed_trades : bridge.loading ? "…" : "—"}
          />
          <Tile
            label="Bridge Failures"
            value={bridge.data ? bridge.data.failed_count : bridge.loading ? "…" : "—"}
            tone={bridge.data && bridge.data.failed_count > 0 ? "neg" : undefined}
          />
        </div>
      </section>

      {/* Tabs */}
      <div
        style={{
          display: "flex",
          gap: "var(--s-2)",
          marginBottom: "var(--s-4)",
          borderBottom: `1px solid ${theme.border}`,
          paddingBottom: "var(--s-2)",
        }}
      >
        <button
          type="button"
          className={`btn ${tab === "journal" ? "btn-primary" : "btn-neutral"}`}
          onClick={() => setTab("journal")}
          style={{ display: "flex", alignItems: "center", gap: 6 }}
        >
          <FileText size={16} />
          Trade Journal
        </button>
        <button
          type="button"
          className={`btn ${tab === "insights" ? "btn-primary" : "btn-neutral"}`}
          onClick={() => setTab("insights")}
          style={{ display: "flex", alignItems: "center", gap: 6 }}
        >
          <BrainCircuit size={16} />
          Pattern Insights
        </button>
      </div>

      {/* TAB 1: TRADE JOURNAL */}
      {tab === "journal" && (
        <div>
          {/* Filter Bar */}
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--s-3)",
              alignItems: "flex-end",
              marginBottom: "var(--s-4)",
              background: theme.surface,
              padding: "var(--s-3)",
              borderRadius: 8,
              border: `1px solid ${theme.border}`,
            }}
          >
            {/* Symbol Search */}
            <div style={{ minWidth: 140, flex: "1 1 140px" }}>
              <label
                htmlFor="retro-symbol-search"
                className="tile-label"
                style={{ display: "block", marginBottom: 6 }}
              >
                Filter Symbol
              </label>
              <input
                id="retro-symbol-search"
                type="text"
                placeholder="e.g. SPY"
                className="input"
                value={symbolFilter}
                onChange={(e) => setSymbolFilter(e.target.value)}
                style={{ width: "100%", textTransform: "uppercase" }}
              />
            </div>

            {/* Strategy Filter */}
            <div style={{ minWidth: 180, flex: "1 1 180px" }}>
              <Select
                label="Strategy"
                value={strategyFilter}
                onChange={(e) => setStrategyFilter(e.target.value)}
                options={strategyOptions}
              />
            </div>

            {/* Provenance Filter */}
            <div style={{ minWidth: 180, flex: "1 1 180px" }}>
              <Select
                label="Provenance Cohort"
                value={provenanceFilter}
                onChange={(e) => setProvenanceFilter(e.target.value)}
                options={[
                  { value: "all", label: "All Provenances" },
                  { value: "signal_driven", label: "Signal-Driven Strategy" },
                  { value: "manual", label: "Manual Entry" },
                  { value: "unknown", label: "Unknown" },
                ]}
              />
            </div>

            {/* Clear / Reset Filters */}
            {(symbolFilter || strategyFilter !== "all" || provenanceFilter !== "all") && (
              <button
                type="button"
                className="btn btn-neutral"
                onClick={() => {
                  setSymbolFilter("");
                  setStrategyFilter("all");
                  setProvenanceFilter("all");
                }}
                style={{ height: 38 }}
              >
                Reset Filters
              </button>
            )}

            {fetchingRetros && (
              <div style={{ fontSize: 12, color: theme.textMuted, marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
                <RefreshCw size={13} className="spinner" />
                Enriching excursion autopsies...
              </div>
            )}
          </div>

          {/* Table / Results */}
          {closedTrades.loading && !closedTrades.data ? (
            <Loading lines={6} />
          ) : closedTrades.error && !closedTrades.data ? (
            <ErrorState message={closedTrades.error} status={closedTrades.status} onRetry={closedTrades.reload} />
          ) : !closedTrades.data || closedTrades.data.length === 0 ? (
            <EmptyState
              title="No closed paper trades"
              hint="Execute and close positions in the Paper Broker to generate retrospective autopsies."
            />
          ) : filteredTrades.length === 0 ? (
            <EmptyState
              title="No matching trades"
              hint="No closed trades match the currently selected filters. Adjust or reset your filters."
            />
          ) : (
            <div
              style={{
                background: theme.surface,
                borderRadius: 8,
                border: `1px solid ${theme.border}`,
                overflowX: "auto",
              }}
            >
              <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${theme.border}`, background: theme.surface2 }}>
                    <th style={{ padding: "10px 14px", color: theme.textSecondary, fontWeight: 600 }}>Exit Date</th>
                    <th style={{ padding: "10px 14px", color: theme.textSecondary, fontWeight: 600 }}>Symbol</th>
                    <th style={{ padding: "10px 14px", color: theme.textSecondary, fontWeight: 600 }}>Side</th>
                    <th style={{ padding: "10px 14px", color: theme.textSecondary, fontWeight: 600 }}>Provenance</th>
                    <th style={{ padding: "10px 14px", color: theme.textSecondary, fontWeight: 600, textAlign: "right" }}>
                      Realized P&amp;L
                    </th>
                    <th style={{ padding: "10px 14px", color: theme.textSecondary, fontWeight: 600, textAlign: "right" }}>
                      P&amp;L %
                    </th>
                    <th style={{ padding: "10px 14px", color: theme.textSecondary, fontWeight: 600, textAlign: "right" }}>
                      MAE (Adverse)
                    </th>
                    <th style={{ padding: "10px 14px", color: theme.textSecondary, fontWeight: 600, textAlign: "right" }}>
                      MFE (Favorable)
                    </th>
                    <th style={{ padding: "10px 14px", color: theme.textSecondary, fontWeight: 600, textAlign: "right" }}>
                      Edge Ratio
                    </th>
                    <th style={{ padding: "10px 14px", color: theme.textSecondary, fontWeight: 600, textAlign: "center" }}>
                      Snapshot
                    </th>
                    <th style={{ padding: "10px 14px", color: theme.textSecondary, fontWeight: 600, textAlign: "center" }}>
                      Action
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {filteredTrades.map((t) => {
                    const retro = retroMap[t.trade_id];
                    const prov = retro ? retro.provenance : "unknown"; // Never infer provenance from strategy_id presence (the same anti-fabrication rule pilots/retrospective_composer.py itself enforces) -- until the composer's own read has resolved, the honest state is "unknown", not a guess.
                    const isWin = t.realized_pnl >= 0;

                    return (
                      <tr
                        key={t.trade_id}
                        onClick={() => setSelectedTradeId(t.trade_id)}
                        style={{
                          borderBottom: `1px solid ${theme.border}`,
                          cursor: "pointer",
                          transition: "background-color 0.15s",
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.backgroundColor = alpha(theme.surface2, "80");
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.backgroundColor = "transparent";
                        }}
                      >
                        {/* Exit Date */}
                        <td style={{ padding: "10px 14px", whiteSpace: "nowrap", color: theme.textSecondary }}>
                          {new Date(t.exit_ts).toLocaleDateString()}{" "}
                          <span style={{ fontSize: 11, color: theme.textMuted }}>
                            {new Date(t.exit_ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                          </span>
                        </td>

                        {/* Symbol */}
                        <td style={{ padding: "10px 14px", fontWeight: 700, color: theme.textPrimary }}>
                          {t.symbol}
                        </td>

                        {/* Side */}
                        <td style={{ padding: "10px 14px" }}>
                          <span
                            style={{
                              padding: "2px 6px",
                              borderRadius: 4,
                              fontSize: 11,
                              fontWeight: 700,
                              backgroundColor: t.side === "BUY" ? alpha(theme.growth, "15") : alpha(theme.decline, "15"),
                              color: t.side === "BUY" ? theme.growth : theme.decline,
                            }}
                          >
                            {t.side}
                          </span>
                        </td>

                        {/* Provenance Badge */}
                        <td style={{ padding: "10px 14px" }}>
                          <span
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 4,
                              padding: "2px 8px",
                              borderRadius: 4,
                              fontSize: 11,
                              fontWeight: 600,
                              backgroundColor:
                                prov === "signal_driven"
                                  ? "rgba(99, 102, 241, 0.15)"
                                  : prov === "manual"
                                  ? alpha(theme.caution, "15")
                                  : alpha(theme.textMuted, "15"),
                              color:
                                prov === "signal_driven"
                                  ? "#818cf8"
                                  : prov === "manual"
                                  ? theme.caution
                                  : theme.textMuted,
                              border: `1px solid ${
                                prov === "signal_driven"
                                  ? "rgba(99, 102, 241, 0.3)"
                                  : prov === "manual"
                                  ? alpha(theme.caution, "30")
                                  : theme.border
                              }`,
                            }}
                          >
                            {prov === "signal_driven" && <BrainCircuit size={12} />}
                            {prov === "manual" && <FileText size={12} />}
                            {prov === "unknown" && <HelpCircle size={12} />}
                            {prov === "signal_driven"
                              ? t.strategy_id || "Strategy"
                              : prov === "manual"
                              ? "Manual"
                              : "Unknown"}
                          </span>
                        </td>

                        {/* Realized PnL */}
                        <td
                          style={{
                            padding: "10px 14px",
                            textAlign: "right",
                            fontWeight: 600,
                            color: isWin ? theme.growth : theme.decline,
                          }}
                        >
                          {isWin ? "+" : ""}${t.realized_pnl.toFixed(2)}
                        </td>

                        {/* Realized PnL % */}
                        <td
                          style={{
                            padding: "10px 14px",
                            textAlign: "right",
                            // A missing pct is neither a gain nor a loss --
                            // `?? 0` previously painted it green (as if it
                            // were a real non-negative return), a CONSTRAINT
                            // #4-adjacent visual fabrication even though the
                            // adjacent text already correctly showed "—".
                            color: t.realized_pnl_pct == null ? theme.textMuted : t.realized_pnl_pct >= 0 ? theme.growth : theme.decline,
                          }}
                        >
                          {t.realized_pnl_pct != null ? `${(t.realized_pnl_pct * 100).toFixed(2)}%` : "—"}
                        </td>

                        {/* MAE -- a fraction of entry price (evaluation_engine.py's
                            contract), never a dollar amount */}
                        <td style={{ padding: "10px 14px", textAlign: "right", color: theme.decline }}>
                          {retro ? (
                            retro.excursion.mae != null ? (
                              `-${(retro.excursion.mae * 100).toFixed(1)}%`
                            ) : (
                              <span style={{ fontSize: 11, color: theme.textMuted }}>
                                {retro.bridge_status === "failed" ? "Bridge error" : "Unavailable"}
                              </span>
                            )
                          ) : (
                            <span style={{ color: theme.textMuted }}>…</span>
                          )}
                        </td>

                        {/* MFE -- likewise a fraction, never a dollar amount */}
                        <td style={{ padding: "10px 14px", textAlign: "right", color: theme.growth }}>
                          {retro ? (
                            retro.excursion.mfe != null ? (
                              `+${(retro.excursion.mfe * 100).toFixed(1)}%`
                            ) : (
                              <span style={{ fontSize: 11, color: theme.textMuted }}>
                                {retro.bridge_status === "failed" ? "Bridge error" : "Unavailable"}
                              </span>
                            )
                          ) : (
                            <span style={{ color: theme.textMuted }}>…</span>
                          )}
                        </td>

                        {/* Edge Ratio */}
                        <td style={{ padding: "10px 14px", textAlign: "right", fontWeight: 600 }}>
                          {retro ? (
                            retro.excursion.edge_ratio != null ? (
                              `${retro.excursion.edge_ratio.toFixed(2)}x`
                            ) : (
                              <span style={{ color: theme.textMuted }}>—</span>
                            )
                          ) : (
                            <span style={{ color: theme.textMuted }}>…</span>
                          )}
                        </td>

                        {/* Snapshot Status */}
                        <td style={{ padding: "10px 14px", textAlign: "center" }}>
                          {retro ? (
                            retro.snapshot?.captured ? (
                              <span
                                style={{
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: 2,
                                  color: theme.growth,
                                  fontSize: 12,
                                }}
                              >
                                <CheckCircle2 size={14} />
                              </span>
                            ) : (
                              <span
                                style={{
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: 2,
                                  color: theme.textMuted,
                                  fontSize: 12,
                                }}
                              >
                                <XCircle size={14} />
                              </span>
                            )
                          ) : (
                            <span style={{ color: theme.textMuted }}>—</span>
                          )}
                        </td>

                        {/* Action Button */}
                        <td style={{ padding: "10px 14px", textAlign: "center" }}>
                          <button
                            type="button"
                            className="btn btn-neutral"
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelectedTradeId(t.trade_id);
                            }}
                            style={{
                              padding: "4px 8px",
                              fontSize: 11,
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 4,
                            }}
                          >
                            Autopsy <ArrowRight size={12} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* TAB 2: PATTERN INSIGHTS */}
      {tab === "insights" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
          {/* Cohort Separation Notice */}
          <Notice variant="info">
            <strong>Cohort Isolation Principle:</strong> Signal-driven algorithmic trades and manual discretionary trades
            are analyzed in strictly isolated cohorts. Win rates, edge ratios, and calibration metrics are never blended or conflated.
          </Notice>

          {/* Filters for Insights */}
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--s-3)",
              alignItems: "flex-end",
              background: theme.surface,
              padding: "var(--s-3)",
              borderRadius: 8,
              border: `1px solid ${theme.border}`,
            }}
          >
            <div style={{ minWidth: 180, flex: "1 1 180px" }}>
              <Select
                label="Filter by Strategy"
                value={insightStrategyFilter}
                onChange={(e) => setInsightStrategyFilter(e.target.value)}
                options={strategyOptions}
              />
            </div>
            <button
              type="button"
              className="btn btn-neutral"
              onClick={() => insights.reload()}
              style={{ height: 38, display: "flex", alignItems: "center", gap: 6 }}
            >
              <RefreshCw size={14} />
              Refresh Insights
            </button>
          </div>

          {insights.loading && !insights.data ? (
            <Loading lines={6} />
          ) : insights.error && !insights.data ? (
            <ErrorState message={insights.error} status={insights.status} onRetry={insights.reload} />
          ) : !insights.data ? (
            <EmptyState title="No insight data available" />
          ) : (
            <>
              {/* Automated vs Manual Cohort Cards */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
                  gap: "var(--s-4)",
                }}
              >
                {/* Automated Cohort Card */}
                <div className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, borderBottom: `1px solid ${theme.border}`, paddingBottom: "var(--s-2)" }}>
                    <BrainCircuit size={20} color="#818cf8" />
                    <div>
                      <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: theme.textPrimary }}>
                        Automated (Signal-Driven) Cohort
                      </h2>
                      <div style={{ fontSize: 12, color: theme.textSecondary, marginTop: 2 }}>
                        Algorithmic strategy executions with forward decision context capture
                      </div>
                    </div>
                  </div>

                  <div className="tiles">
                    <Tile
                      label="Total Trades"
                      value={insights.data.automated_cohort.total_trades}
                    />
                    <Tile
                      label="Win Rate"
                      value={
                        insights.data.automated_cohort.win_rate != null
                          ? `${(insights.data.automated_cohort.win_rate * 100).toFixed(1)}%`
                          : "—"
                      }
                      tone={
                        insights.data.automated_cohort.win_rate != null
                          ? insights.data.automated_cohort.win_rate >= 0.5
                            ? "pos"
                            : "neg"
                          : undefined
                      }
                    />
                    <Tile
                      label="Profit Factor"
                      value={
                        insights.data.automated_cohort.profit_factor != null
                          ? insights.data.automated_cohort.profit_factor.toFixed(2)
                          : "—"
                      }
                    />
                    <Tile
                      label="Avg Edge Ratio"
                      value={
                        insights.data.automated_cohort.mean_edge_ratio != null
                          ? `${insights.data.automated_cohort.mean_edge_ratio.toFixed(2)}x`
                          : "—"
                      }
                      tone={
                        insights.data.automated_cohort.mean_edge_ratio != null &&
                        insights.data.automated_cohort.mean_edge_ratio >= 1.0
                          ? "pos"
                          : undefined
                      }
                    />
                  </div>

                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr 1fr",
                      gap: "var(--s-2)",
                      background: theme.surface2,
                      padding: "var(--s-2)",
                      borderRadius: 6,
                    }}
                  >
                    <div>
                      <div style={{ fontSize: 11, color: theme.textMuted }}>Mean MAE (Adverse)</div>
                      <div style={{ fontSize: 14, fontWeight: 600, color: theme.decline, marginTop: 2 }}>
                        {insights.data.automated_cohort.mean_mae != null
                          ? `-${(insights.data.automated_cohort.mean_mae * 100).toFixed(1)}%`
                          : "Unavailable"}
                      </div>
                    </div>
                    <div>
                      <div style={{ fontSize: 11, color: theme.textMuted }}>Mean MFE (Favorable)</div>
                      <div style={{ fontSize: 14, fontWeight: 600, color: theme.growth, marginTop: 2 }}>
                        {insights.data.automated_cohort.mean_mfe != null
                          ? `+${(insights.data.automated_cohort.mean_mfe * 100).toFixed(1)}%`
                          : "Unavailable"}
                      </div>
                    </div>
                  </div>

                  {/* Calibration / Brier Metric */}
                  <div
                    style={{
                      padding: "var(--s-2)",
                      borderRadius: 6,
                      border: `1px solid ${theme.border}`,
                      backgroundColor: theme.surface,
                    }}
                  >
                    <div style={{ fontSize: 11, color: theme.textMuted, display: "flex", alignItems: "center", gap: 4 }}>
                      <Sliders size={12} />
                      Brier Score Calibration Quality
                    </div>
                    <div style={{ fontSize: 15, fontWeight: 700, color: theme.textPrimary, marginTop: 4 }}>
                      {insights.data.automated_cohort.calibration_brier_score != null
                        ? insights.data.automated_cohort.calibration_brier_score.toFixed(3)
                        : "Uncalibrated"}
                    </div>
                    <div style={{ fontSize: 12, color: theme.textSecondary, marginTop: 2 }}>
                      {insights.data.automated_cohort.calibration_status ||
                        insights.data.automated_cohort.note ||
                        "Requires >= 5 calibrated trades for statistical validity"}
                    </div>
                  </div>

                  {/* Strategy Breakdown Table */}
                  {insights.data.automated_cohort.strategies &&
                    Object.keys(insights.data.automated_cohort.strategies).length > 0 && (
                      <div style={{ marginTop: "var(--s-2)" }}>
                        <div style={{ fontSize: 12, fontWeight: 700, textTransform: "uppercase", color: theme.textSecondary, marginBottom: 6 }}>
                          Strategy Performance Breakdown
                        </div>
                        <div style={{ overflowX: "auto" }}>
                          <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse", textAlign: "left" }}>
                            <thead>
                              <tr style={{ borderBottom: `1px solid ${theme.border}`, color: theme.textMuted }}>
                                <th style={{ padding: "6px 8px" }}>Strategy</th>
                                <th style={{ padding: "6px 8px", textAlign: "right" }}>Trades</th>
                                <th style={{ padding: "6px 8px", textAlign: "right" }}>Win Rate</th>
                                <th style={{ padding: "6px 8px", textAlign: "right" }}>Realized P&amp;L</th>
                                <th style={{ padding: "6px 8px", textAlign: "right" }}>Edge Ratio</th>
                              </tr>
                            </thead>
                            <tbody>
                              {Object.entries(insights.data.automated_cohort.strategies).map(([strategyId, s]) => (
                                <tr key={strategyId} style={{ borderBottom: `1px solid ${theme.border}` }}>
                                  <td style={{ padding: "6px 8px", fontWeight: 600, color: theme.textPrimary }}>
                                    {strategyId}
                                  </td>
                                  <td style={{ padding: "6px 8px", textAlign: "right" }}>{s.total_trades}</td>
                                  <td style={{ padding: "6px 8px", textAlign: "right", color: s.win_rate == null ? theme.textMuted : s.win_rate >= 0.5 ? theme.growth : theme.decline }}>
                                    {s.win_rate != null ? `${(s.win_rate * 100).toFixed(1)}%` : "—"}
                                  </td>
                                  <td style={{ padding: "6px 8px", textAlign: "right", color: s.total_realized_pnl >= 0 ? theme.growth : theme.decline }}>
                                    ${s.total_realized_pnl.toFixed(2)}
                                  </td>
                                  <td style={{ padding: "6px 8px", textAlign: "right" }}>
                                    {s.mean_edge_ratio != null ? `${s.mean_edge_ratio.toFixed(2)}x` : "—"}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                </div>

                {/* Manual Cohort Card */}
                <div className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, borderBottom: `1px solid ${theme.border}`, paddingBottom: "var(--s-2)" }}>
                    <FileText size={20} color={theme.caution} />
                    <div>
                      <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: theme.textPrimary }}>
                        Manual (Discretionary) Cohort
                      </h2>
                      <div style={{ fontSize: 12, color: theme.textSecondary, marginTop: 2 }}>
                        Operator-entered trades without algorithmic model scoring
                      </div>
                    </div>
                  </div>

                  <div className="tiles">
                    <Tile
                      label="Total Trades"
                      value={insights.data.manual_cohort.total_trades}
                    />
                    <Tile
                      label="Win Rate"
                      value={
                        insights.data.manual_cohort.win_rate != null
                          ? `${(insights.data.manual_cohort.win_rate * 100).toFixed(1)}%`
                          : "—"
                      }
                      tone={
                        insights.data.manual_cohort.win_rate != null
                          ? insights.data.manual_cohort.win_rate >= 0.5
                            ? "pos"
                            : "neg"
                          : undefined
                      }
                    />
                    <Tile
                      label="Profit Factor"
                      value={
                        insights.data.manual_cohort.profit_factor != null
                          ? insights.data.manual_cohort.profit_factor.toFixed(2)
                          : "—"
                      }
                    />
                    <Tile
                      label="Avg Edge Ratio"
                      value={
                        insights.data.manual_cohort.mean_edge_ratio != null
                          ? `${insights.data.manual_cohort.mean_edge_ratio.toFixed(2)}x`
                          : "—"
                      }
                    />
                  </div>

                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr 1fr",
                      gap: "var(--s-2)",
                      background: theme.surface2,
                      padding: "var(--s-2)",
                      borderRadius: 6,
                    }}
                  >
                    <div>
                      <div style={{ fontSize: 11, color: theme.textMuted }}>Mean MAE (Adverse)</div>
                      <div style={{ fontSize: 14, fontWeight: 600, color: theme.decline, marginTop: 2 }}>
                        {insights.data.manual_cohort.mean_mae != null
                          ? `-${(insights.data.manual_cohort.mean_mae * 100).toFixed(1)}%`
                          : "Unavailable"}
                      </div>
                    </div>
                    <div>
                      <div style={{ fontSize: 11, color: theme.textMuted }}>Mean MFE (Favorable)</div>
                      <div style={{ fontSize: 14, fontWeight: 600, color: theme.growth, marginTop: 2 }}>
                        {insights.data.manual_cohort.mean_mfe != null
                          ? `+${(insights.data.manual_cohort.mean_mfe * 100).toFixed(1)}%`
                          : "Unavailable"}
                      </div>
                    </div>
                  </div>

                  <div
                    style={{
                      padding: "var(--s-3)",
                      borderRadius: 6,
                      backgroundColor: theme.surface2,
                      border: `1px solid ${theme.border}`,
                      color: theme.textMuted,
                      fontSize: 12,
                      lineHeight: 1.5,
                    }}
                  >
                    <div style={{ fontWeight: 600, color: theme.textSecondary, marginBottom: 4 }}>
                      Calibration Non-Applicability Notice
                    </div>
                    Brier Score and model conviction calibration are not applicable for manual discretionary trades
                    because no quantitative signal model evaluated probability or conviction at the time of entry.
                  </div>
                </div>
              </div>

              {/* Comparative Observations Panel */}
              <div className="card card-pad">
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: "var(--s-3)" }}>
                  <TrendingUp size={18} color="#818cf8" />
                  <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: theme.textPrimary }}>
                    Cohort Comparative Observations
                  </h3>
                </div>

                {insights.data.contrastive_insights && insights.data.contrastive_insights.length > 0 ? (
                  <ul style={{ margin: 0, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 8 }}>
                    {insights.data.contrastive_insights.map((item: string, idx: number) => (
                      <li key={idx} style={{ fontSize: 13, color: theme.textPrimary, lineHeight: 1.5 }}>
                        {item}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div style={{ fontSize: 13, color: theme.textMuted }}>
                    No contrastive patterns identified yet. Accumulate more closed trades in both cohorts.
                  </div>
                )}
              </div>

              {/* Unrecorded Cohort Notice */}
              {insights.data.unrecorded_cohort &&
                insights.data.unrecorded_cohort.total_trades > 0 && (
                  <div
                    style={{
                      padding: "var(--s-3)",
                      borderRadius: 8,
                      backgroundColor: theme.surface2,
                      border: `1px solid ${theme.border}`,
                      display: "flex",
                      alignItems: "flex-start",
                      gap: 10,
                    }}
                  >
                    <AlertTriangle size={18} color={theme.caution} style={{ flexShrink: 0, marginTop: 2 }} />
                    <div style={{ fontSize: 13, color: theme.textSecondary, lineHeight: 1.5 }}>
                      <strong>Historical / Pre-Feature Notice:</strong>{" "}
                      {insights.data.unrecorded_cohort.note ||
                        `${insights.data.unrecorded_cohort.total_trades} historical trades predate retrospective decision capture and are excluded from causal attribution.`}
                    </div>
                  </div>
                )}
            </>
          )}
        </div>
      )}

      {/* Retrospective Detail Modal */}
      <RetrospectiveDetailModal
        isOpen={selectedTradeId !== null}
        tradeId={selectedTradeId}
        initialTrade={selectedTradeId ? retroMap[selectedTradeId] : null}
        onClose={() => setSelectedTradeId(null)}
      />
    </div>
  );
}
