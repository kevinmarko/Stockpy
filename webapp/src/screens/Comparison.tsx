import { useState, useEffect, useMemo } from "react";
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from "recharts";
import { api } from "../api/client";
import type { PilotSummary, CurvePoint } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, Notice } from "../components/ui";
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

  return (
    <div className="screen" data-testid="comparison-screen">
      <h1 className="screen-title" data-testid="comparison-title">Pilot Strategy Comparison</h1>

      <TabGuide tabKey="compare" />

      {/* Pilot Checklist */}
      <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-3)" }}>
          <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Select Pilots (max 5)</h2>
          {selectedIds.length > 0 && (
            <button className="btn btn-neutral" onClick={clearAll} style={{ fontSize: "var(--t-caption)", padding: "var(--s-1) var(--s-2)" }}>
              Clear All
            </button>
          )}
        </div>

        {pilotsList.loading ? (
          <Loading lines={1} />
        ) : pilotsList.error || !pilotsList.data ? (
          <ErrorState message={pilotsList.error ?? "No data"} status={pilotsList.status} onRetry={pilotsList.reload} />
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2-5)" }}>
            {pilotsList.data.map(p => {
              const checked = selectedIds.includes(p.id);
              const disabled = !checked && selectedIds.length >= 5;
              return (
                <div
                  key={p.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    background: checked ? theme.surface3 : theme.surface2,
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
        )}
      </section>

      {/* Recommended stocks — the platform's current BUY picks (click → detail). */}
      <RecommendedStocks />

      {/* Symbol-vs-symbol comparison — a separate entity from Pilot-vs-Pilot
          above (tickers, not strategies), so it's its own always-visible card
          rather than nested inside the Pilot-selection-dependent block below. */}
      <SymbolComparison />

      {/* Row Error Banner for fetch failures */}
      {Object.keys(fetchErrors).length > 0 && (
        <Notice variant="warn" style={{ marginBottom: "var(--s-4)" }} data-testid="row-error-banner">
          <strong>Notice:</strong> Failed to load performance curve data for some strategies.
        </Notice>
      )}

      {/* Comparison Grid */}
      {selectedIds.length === 0 ? (
        <div className="empty" style={{ padding: 40 }}>
          Select at least one pilot strategy above to start comparing metrics and performance curves.
        </div>
      ) : (
        <>
          {/* Overlaid Performance Chart */}
          <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }}>
            <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>Overlaid Performance</h2>
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
                        // Up to 5 Pilots can be selected but only 3 hues are
                        // CVD-validated (see theme.ts) — a 4th/5th folds to
                        // theme.textMuted rather than an unvalidated color;
                        // the Legend's `name` above still identifies every line.
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

            {/* Honest "no backtest series" note — never a fabricated line for
                Pilots whose validation report has no persisted return curve. */}
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
          </section>

          {/* Comparison Grid */}
          <section className="card card-pad" style={{ overflowX: "auto", padding: 0 }}>
            <div style={{ padding: "var(--s-3)" }}>
              <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Key Metrics Comparison</h2>
            </div>
            
            {(() => {
              const sharpes = selectedPilots.map(p => p.headline.sharpe).filter((v): v is number => v != null);
              const maxSharpe = sharpes.length > 0 ? Math.max(...sharpes) : -Infinity;
              
              const pbos = selectedPilots.map(p => p.headline.pbo).filter((v): v is number => v != null);
              const minPbo = pbos.length > 0 ? Math.min(...pbos) : Infinity;
              
              const dsrs = selectedPilots.map(p => p.headline.dsr).filter((v): v is number => v != null);
              const maxDsr = dsrs.length > 0 ? Math.max(...dsrs) : -Infinity;

              const dds = selectedPilots.map(p => p.headline.max_drawdown).filter((v): v is number => v != null);
              // Max drawdown is usually a negative fraction (-0.2 for -20%), so highest (closest to 0) is best. If positive, lowest is best.
              // Assuming it's negative fraction as usual in this app based on fmtPct defaults.
              const bestDd = dds.length > 0 ? Math.max(...dds) : -Infinity; 

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
          </section>

          {/* Recent pilot alerts */}
          <section className="card card-pad" style={{ marginTop: "var(--s-4)" }} data-testid="comparison-activity-feed">
            <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>Recent pilot alerts</h2>
            <ActivityFeed limit={5} pilotIds={selectedIds} />
          </section>
        </>
      )}
      {followPilot && (
        <FollowModal
          pilot={followPilot}
          onClose={() => setFollowPilot(null)}
        />
      )}
    </div>
  );
}
