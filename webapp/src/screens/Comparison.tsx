import { useState, useEffect, useMemo } from "react";
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from "recharts";
import { api } from "../api/client";
import type { PilotSummary, CurvePoint } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, Notice, DeployableBadge } from "../components/ui";
import { Toggle } from "../components/Toggle";
import { ActivityFeed } from "../components/ActivityFeed";
import { RecommendedStocks } from "../components/RecommendedStocks";
import { SymbolComparison } from "../components/SymbolComparison";
import { TabGuide } from "../components/TabGuide";
import { chartAxisLine, chartAxisTick, chartGridProps, chartTooltipStyle } from "../components/charts";
import { FollowModal } from "./FollowModal";
import { seriesColor, theme } from "../theme";
import { fmtNum, fmtPct, fmtUsd } from "../format";

export function Comparison() {
  const [selectedIds, setSelectedIds] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem("comparison_selected_ids");
      const parsed = saved ? JSON.parse(saved) : [];
      return Array.isArray(parsed) ? parsed.filter(id => typeof id === "string") : [];
    } catch {
      return [];
    }
  });

  const [curves, setCurves] = useState<Record<string, CurvePoint[]>>({});
  // Pilots whose performance fetch SUCCEEDED but returned `curve: null` (no
  // persisted backtest series). These are NOT errors — they stay in the metrics
  // table but must never get a fabricated chart line. Tracked with the honest
  // `reason` the API returns so the UI can explain the absence.
  const [nullCurves, setNullCurves] = useState<Record<string, string>>({});
  const [fetchErrors, setFetchErrors] = useState<Record<string, string>>({});
  const [loadingCurves, setLoadingCurves] = useState(false);
  const [followPilot, setFollowPilot] = useState<PilotSummary | null>(null);
  const pilotsList = useApi<PilotSummary[]>(() => api.listPilots(), []);

  useEffect(() => {
    localStorage.setItem("comparison_selected_ids", JSON.stringify(selectedIds));
  }, [selectedIds]);

  useEffect(() => {
    if (selectedIds.length === 0) {
      setCurves({});
      setNullCurves({});
      setFetchErrors({});
      setLoadingCurves(false);
      return;
    }

    let active = true;
    setLoadingCurves(true);
    setFetchErrors({});

    Promise.all(
      selectedIds.map(id =>
        api.getPerformance(id, "3M")
          .then(res => ({
            id,
            curve: res.curve,
            reason: res.reason ?? null,
            error: null as string | null,
          }))
          .catch(err => ({
            id,
            curve: null as CurvePoint[] | null,
            reason: null as string | null,
            error: (err?.message as string) || "Failed to load performance",
          }))
      )
    )
      .then(results => {
        if (!active) return;
        const nextCurves: Record<string, CurvePoint[]> = {};
        const nextNull: Record<string, string> = {};
        const nextErrors: Record<string, string> = {};

        results.forEach(r => {
          if (r.error) {
            nextErrors[r.id] = r.error;
          } else if (Array.isArray(r.curve) && r.curve.length > 0) {
            nextCurves[r.id] = r.curve;
          } else {
            // Success, but no persisted backtest series — honest, not an error.
            nextNull[r.id] =
              r.reason ?? "This Pilot's validation report has no persisted return curve.";
          }
        });

        setCurves(nextCurves);
        setNullCurves(nextNull);
        setFetchErrors(nextErrors);
        setLoadingCurves(false);
      })
      .catch(() => {
        // Individual promises already catch; this is a defensive backstop so a
        // rejection can never leave the spinner stuck.
        if (!active) return;
        setFetchErrors({ _batch: "Failed to load performance curves." });
        setLoadingCurves(false);
      });

    return () => {
      active = false;
    };
  }, [selectedIds]);

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      if (prev.includes(id)) {
        return prev.filter(x => x !== id);
      }
      if (prev.length >= 5) {
        return prev;
      }
      return [...prev, id];
    });
  };

  const clearAll = () => {
    setSelectedIds([]);
  };

  // Metrics-table columns: every selected pilot that didn't hard-error, INCLUDING
  // null-curve pilots (they keep their honest metrics row).
  const selectedPilots = pilotsList.data?.filter(p => selectedIds.includes(p.id) && !fetchErrors[p.id]) ?? [];
  // Chart series: only pilots with a REAL curve — a null-curve pilot is never
  // drawn (no phantom line, no phantom legend entry).
  const chartPilots = selectedPilots.filter(p => Array.isArray(curves[p.id]) && curves[p.id].length > 0);
  // Pilots to name in the honest "no backtest series" note.
  const nullCurvePilots = selectedPilots.filter(p => nullCurves[p.id]);

  const chartData = useMemo(() => {
    const validCurves: Record<string, CurvePoint[]> = {};
    if (curves && typeof curves === "object") {
      Object.keys(curves).forEach((key) => {
        if (Array.isArray(curves[key])) {
          validCurves[key] = curves[key];
        }
      });
    }

    const lookup: Record<string, Record<string, number>> = {};
    Object.keys(validCurves).forEach((id) => {
      lookup[id] = {};
      validCurves[id].forEach((pt) => {
        if (pt && pt.date) {
          lookup[id][pt.date] = pt.value;
        }
      });
    });

    const dates = Array.from(
      new Set(
        Object.values(validCurves)
          .flat()
          .map((p) => p?.date)
          .filter(Boolean)
      )
    ).sort();

    return dates.map((date) => {
      const row: Record<string, any> = { date };
      Object.keys(validCurves).forEach((id) => {
        const val = lookup[id]?.[date];
        if (val !== undefined) {
          row[id] = val;
        }
      });
      return row;
    });
  }, [curves]);

  // Group pilots by category for the accordion UI
  const pilotsByCategory = useMemo(() => {
    if (!pilotsList.data) return {};
    const grouped: Record<string, PilotSummary[]> = {};
    pilotsList.data.forEach(p => {
      if (!grouped[p.category]) grouped[p.category] = [];
      grouped[p.category].push(p);
    });
    return grouped;
  }, [pilotsList.data]);

  return (
    <div className="screen" data-testid="comparison-screen">
      <div style={{ marginBottom: "var(--s-4)" }}>
        <h1 className="screen-title" data-testid="comparison-title" style={{ margin: 0 }}>Pilot Strategy Comparison</h1>
      </div>

      <TabGuide tabKey="compare" />

      {/* Responsive Split Pane Layout */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-4)", alignItems: "flex-start" }}>
        
        {/* Left Pane (Controls) */}
        <div style={{ flex: "1 1 350px", minWidth: 0, display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
          
          {/* Pilot Checklist Accordions */}
          <section className="card card-pad" style={{ padding: 0 }}>
            {/* Sticky Header inside the card */}
            <div style={{ 
              position: "sticky", 
              top: 0, 
              zIndex: 10, 
              background: "var(--surface)", 
              padding: "var(--s-3)", 
              borderBottom: `1px solid ${theme.borderStrong}`,
              display: "flex", 
              justifyContent: "space-between", 
              alignItems: "center" 
            }}>
              <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Select Pilots (max 5)</h2>
              {selectedIds.length > 0 && (
                <button className="btn btn-neutral" onClick={clearAll} style={{ fontSize: "var(--t-caption)", padding: "var(--s-1) var(--s-2)" }}>
                  Clear All
                </button>
              )}
            </div>
            
            <div style={{ padding: "var(--s-3)", borderBottom: `1px solid ${theme.borderStrong}` }}>
              <select
                className="input"
                value=""
                onChange={(e) => { if(e.target.value) toggleSelect(e.target.value); }}
                disabled={selectedIds.length >= 5 || pilotsList.loading}
                style={{ width: "100%", cursor: "pointer", fontSize: "var(--t-caption)" }}
                aria-label="Quick add pilot"
                data-testid="comparison-quick-add"
              >
                <option value="" disabled>Quick add pilot...</option>
                {pilotsList.data?.map(p => (
                   <option key={p.id} value={p.id} disabled={selectedIds.includes(p.id)}>
                     {p.name}
                   </option>
                ))}
              </select>
            </div>

            <div style={{ padding: "var(--s-3)" }}>
              {pilotsList.loading ? (
                <Loading lines={1} />
              ) : pilotsList.error || !pilotsList.data ? (
                <ErrorState message={pilotsList.error ?? "No data"} status={pilotsList.status} onRetry={pilotsList.reload} />
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
                  {Object.entries(pilotsByCategory).map(([cat, pilots]) => {
                    const selectedCount = pilots.filter(p => selectedIds.includes(p.id)).length;
                    return (
                      <details key={cat} open style={{ marginBottom: "var(--s-1)" }}>
                        <summary style={{ cursor: "pointer", padding: "var(--s-2) var(--s-3)", background: theme.surface2, borderRadius: "var(--r-md)", fontWeight: 600, userSelect: "none", outline: "none" }}>
                          {cat} <span style={{ color: theme.textMuted, fontWeight: 400, marginLeft: "var(--s-2)" }}>({selectedCount}/{pilots.length})</span>
                        </summary>
                        <div style={{ padding: "var(--s-2) 0 var(--s-1)", display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
                          {pilots.map(p => {
                            const checked = selectedIds.includes(p.id);
                            const disabled = !checked && selectedIds.length >= 5;
                            return (
                              <div
                                key={p.id}
                                style={{
                                  display: "flex",
                                  alignItems: "center",
                                  background: checked ? theme.surface3 : "transparent",
                                  padding: "var(--s-1-5) var(--s-3)",
                                  borderRadius: 20,
                                  border: `1px solid ${checked ? theme.accent : theme.border}`,
                                  opacity: disabled ? 0.5 : 1,
                                }}
                              >
                                <Toggle
                                  label={p.name}
                                  checked={checked}
                                  onChange={() => toggleSelect(p.id)}
                                  disabled={disabled}
                                  dataTestId={`comparison-checkbox-${p.id}`}
                                />
                              </div>
                            );
                          })}
                        </div>
                      </details>
                    );
                  })}
                </div>
              )}
            </div>
          </section>

          {/* Symbol-vs-symbol comparison */}
          <SymbolComparison />
        </div>

        {/* Right Pane (Visualizations & Data) */}
        <div style={{ flex: "2 1 600px", minWidth: 0, position: "sticky", top: "var(--s-4)" }}>
          {/* Row Error Banner for fetch failures */}
          {Object.keys(fetchErrors).length > 0 && (
            <Notice variant="warn" data-testid="row-error-banner" style={{ marginBottom: "var(--s-4)" }}>
              <strong>Notice:</strong> Failed to load performance curve data for some strategies.
            </Notice>
          )}

          <div className="dashboard-layout" style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
            {/* Recommended stocks table */}
            <RecommendedStocks key="recommended" />

          {selectedIds.length === 0 ? (
            <div key="empty" className="card card-pad empty" style={{ padding: 40 }}>
              Select at least one pilot strategy in the left pane to start comparing metrics and performance curves.
            </div>
          ) : (
            [
              /* Overlaid Performance Chart */
              <div key="performance" className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
                <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid ${theme.borderStrong}` }}>
                  <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Overlaid Performance</h2>
                </div>
                <div style={{ flex: 1, padding: "var(--s-3)", overflow: "auto" }}>
                {loadingCurves ? (
                  <div className="skeleton" style={{ height: 200 }} />
                ) : chartData.length === 0 ? (
                  <div className="empty" style={{ height: 200, padding: 40 }}>
                    No performance curve data available for selected pilots.
                  </div>
                ) : (
                  <div style={{ height: 200 }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={chartData} margin={{ top: 5, right: 10, left: -20, bottom: 5 }}>
                        <CartesianGrid {...chartGridProps} />
                        <XAxis dataKey="date" tick={chartAxisTick} {...chartAxisLine} />
                        <YAxis tick={chartAxisTick} {...chartAxisLine} domain={["auto", "auto"]} />
                        <Tooltip
                          contentStyle={chartTooltipStyle}
                          labelStyle={{ color: theme.textSecondary, fontSize: "var(--t-micro)" }}
                          itemStyle={{ fontSize: "var(--t-micro)" }}
                        />
                        <Legend wrapperStyle={{ fontSize: "var(--t-micro)", paddingTop: 10 }} />
                        {chartPilots.map((p, index) => (
                          <Line
                            key={p.id}
                            type="monotone"
                            dataKey={p.id}
                            name={p.name}
                            stroke={seriesColor(index)}
                            dot={false}
                            strokeWidth={2}
                            isAnimationActive={false}
                            activeDot={{ r: 4 }}
                          />
                        ))}
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                )}

                {nullCurvePilots.length > 0 && (
                  <div
                    data-testid="no-series-note"
                    className="empty"
                    style={{ marginTop: "var(--s-3)", padding: "var(--s-4) var(--s-3)", background: "var(--surface-2)", borderRadius: "var(--r-md)" }}
                  >
                    <div style={{ fontWeight: 600, color: theme.textSecondary }}>
                      No backtest series for: {nullCurvePilots.map(p => p.name).join(", ")}
                    </div>
                    <div style={{ marginTop: "var(--s-1-5)", fontSize: "var(--t-body)", color: theme.textMuted }}>
                      These Pilots have no persisted return curve yet, so no line is drawn for them.
                      Their metrics below are shown honestly.
                    </div>
                  </div>
                )}
                </div>
              </div>,

              /* Comparison Grid */
              <div key="metrics" className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
                <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid ${theme.borderStrong}` }}>
                  <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Key Metrics Comparison</h2>
                </div>
                <div style={{ flex: 1, overflowX: "auto" }}>
                
                {(() => {
                  const sharpes = selectedPilots.map(p => p.headline.sharpe).filter((v): v is number => v != null);
                  const maxSharpe = sharpes.length > 0 ? Math.max(...sharpes) : -Infinity;
                  
                  const pbos = selectedPilots.map(p => p.headline.pbo).filter((v): v is number => v != null);
                  const minPbo = pbos.length > 0 ? Math.min(...pbos) : Infinity;
                  
                  const dsrs = selectedPilots.map(p => p.headline.dsr).filter((v): v is number => v != null);
                  const maxDsr = dsrs.length > 0 ? Math.max(...dsrs) : -Infinity;

                  // max_drawdown is a positive-fraction magnitude (0.18 = 18% drawdown,
                  // see Headline's doc comment) -- smaller is better, so the "best" value
                  // to highlight is the MIN, not the max. (Using Math.max here would
                  // highlight the worst drawdown as if it were the best.)
                  const dds = selectedPilots.map(p => p.headline.max_drawdown).filter((v): v is number => v != null);
                  const bestDd = dds.length > 0 ? Math.min(...dds) : Infinity;

                  const getHeatmap = (val: number | null | undefined, best: number) => {
                    if (val == null || best === Infinity || best === -Infinity) return {};
                    if (val === best) return { backgroundColor: "rgba(16, 185, 129, 0.1)", color: theme.growth, fontWeight: 700 };
                    return {};
                  };

                  const cellStyle = { padding: "var(--s-2) var(--s-3)", borderBottom: `1px solid ${theme.borderStrong}`, display: "flex", alignItems: "center" };
                  const headerStyle = { ...cellStyle, fontWeight: 700, backgroundColor: theme.surface2, color: theme.textSecondary, fontSize: "var(--t-caption)", whiteSpace: "normal" as const, wordBreak: "break-word" as const };
                  const stickyColStyle = { ...headerStyle, position: "sticky" as const, left: 0, zIndex: 10, borderRight: `1px solid ${theme.borderStrong}` };

                  return (
                    <div style={{ minWidth: 600, display: "grid", gridTemplateColumns: `140px repeat(${selectedPilots.length}, minmax(120px, 1fr))` }} role="table" aria-label="Key Metrics Comparison">
                      {/* Headers */}
                      <div style={{ display: "contents" }} role="row">
                        <div role="columnheader" style={{ ...stickyColStyle, borderBottom: `1px solid ${theme.borderStrong}` }}>Metric</div>
                        {selectedPilots.map(p => (
                          <div role="columnheader" key={`head-${p.id}`} style={{ ...headerStyle, color: theme.accent, borderBottom: `1px solid ${theme.borderStrong}` }}>{p.name}</div>
                        ))}
                      </div>

                      {/* Category */}
                      <div style={{ display: "contents" }} role="row">
                        <div role="cell" style={stickyColStyle}>Category</div>
                        {selectedPilots.map(p => (
                          <div role="cell" key={`cat-${p.id}`} style={cellStyle}>{p.category}</div>
                        ))}
                      </div>

                      {/* Deployable — the PBO/DSR/Sharpe/MaxDD gate verdict, always paired
                          with the numbers below it so a failing strategy's equity curve
                          and metrics are never shown with the same visual treatment as a
                          passing one (see PilotCard/Marketplace/StrategyHealth/PilotDetail,
                          which all pair this badge with performance numbers the same way). */}
                      <div style={{ display: "contents" }} role="row">
                        <div role="cell" style={stickyColStyle}>Deployable</div>
                        {selectedPilots.map(p => (
                          <div role="cell" key={`dep-${p.id}`} style={cellStyle}>
                            <DeployableBadge deployable={p.headline.deployable} />
                          </div>
                        ))}
                      </div>

                      {/* Sharpe */}
                      <div style={{ display: "contents" }} role="row">
                        <div role="cell" style={stickyColStyle}>Sharpe Ratio</div>
                        {selectedPilots.map(p => (
                          <div role="cell" key={`shr-${p.id}`} className="num" style={{ ...cellStyle, ...getHeatmap(p.headline.sharpe, maxSharpe) }}>
                            {p.headline.sharpe == null ? "—" : fmtNum(p.headline.sharpe, 2)}
                          </div>
                        ))}
                      </div>

                      {/* PBO */}
                      <div style={{ display: "contents" }} role="row">
                        <div role="cell" style={stickyColStyle}>PBO</div>
                        {selectedPilots.map(p => (
                          <div role="cell" key={`pbo-${p.id}`} className="num" style={{ ...cellStyle, ...getHeatmap(p.headline.pbo, minPbo) }}>
                            {p.headline.pbo == null ? "—" : fmtNum(p.headline.pbo, 2)}
                          </div>
                        ))}
                      </div>

                      {/* Max Drawdown */}
                      <div style={{ display: "contents" }} role="row">
                        <div role="cell" style={stickyColStyle}>Max Drawdown</div>
                        {selectedPilots.map(p => (
                          <div role="cell" key={`mdd-${p.id}`} className="num" style={{ ...cellStyle, ...getHeatmap(p.headline.max_drawdown, bestDd) }}>
                            {p.headline.max_drawdown == null ? "—" : fmtPct(p.headline.max_drawdown, 0, { fromFraction: true })}
                          </div>
                        ))}
                      </div>

                      {/* DSR */}
                      <div style={{ display: "contents" }} role="row">
                        <div role="cell" style={stickyColStyle}>DSR</div>
                        {selectedPilots.map(p => (
                          <div role="cell" key={`dsr-${p.id}`} className="num" style={{ ...cellStyle, ...getHeatmap(p.headline.dsr, maxDsr) }}>
                            {p.headline.dsr == null ? "—" : fmtNum(p.headline.dsr, 3)}
                          </div>
                        ))}
                      </div>

                      {/* AUM Proxy */}
                      <div style={{ display: "contents" }} role="row">
                        <div role="cell" style={stickyColStyle}>AUM Proxy</div>
                        {selectedPilots.map(p => (
                          <div role="cell" key={`aum-${p.id}`} className="num" style={cellStyle}>
                            {p.aum_proxy == null ? "—" : fmtUsd(p.aum_proxy)}
                          </div>
                        ))}
                      </div>

                      {/* Followers */}
                      <div style={{ display: "contents" }} role="row">
                        <div role="cell" style={{ ...stickyColStyle, borderBottom: "none" }}>Followers</div>
                        {selectedPilots.map(p => (
                          <div role="cell" key={`fol-${p.id}`} className="num" style={{ ...cellStyle, borderBottom: "none" }}>
                            {p.followers_proxy == null ? "—" : p.followers_proxy}
                          </div>
                        ))}
                      </div>
                      
                      {/* Actions */}
                      <div style={{ display: "contents" }} role="row">
                        <div role="cell" style={{ ...stickyColStyle, borderBottom: "none", borderTop: `1px solid ${theme.borderStrong}` }}>Actions</div>
                        {selectedPilots.map(p => (
                          <div role="cell" key={`act-${p.id}`} style={{ ...cellStyle, borderBottom: "none", borderTop: `1px solid ${theme.borderStrong}` }}>
                            <button
                              className="btn btn-primary"
                              onClick={() => setFollowPilot(p)}
                              style={{ fontSize: "var(--t-caption)", padding: "var(--s-1) var(--s-2)" }}
                              data-testid={`follow-pilot-btn-${p.id}`}
                            >
                              Follow
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })()}
                </div>
              </div>,

              /* Recent pilot alerts */
              <div key="alerts" className="card card-pad" data-testid="comparison-activity-feed" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
                <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid ${theme.borderStrong}` }}>
                  <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Recent pilot alerts</h2>
                </div>
                <div style={{ flex: 1, padding: "var(--s-3)", overflow: "auto" }}>
                  <ActivityFeed limit={5} pilotIds={selectedIds} />
                </div>
              </div>
            ]
          )}
          </div>
        </div>
      </div>

      {followPilot && (
        <FollowModal
          pilot={followPilot}
          onClose={() => setFollowPilot(null)}
        />
      )}
    </div>
  );
}
