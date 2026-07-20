import { useState, useEffect, useMemo } from "react";
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from "recharts";
import { api } from "../api/client";
import type { PilotSummary, CurvePoint } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading } from "../components/ui";
import { ActivityFeed } from "../components/ActivityFeed";
import { RecommendedStocks } from "../components/RecommendedStocks";
import { SymbolComparison } from "../components/SymbolComparison";
import { TabGuide } from "../components/TabGuide";
import { FollowModal } from "./FollowModal";
import { theme } from "../theme";
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

  const colors = ["#38bdf8", "#10b981", "#f59e0b", "#a855f7", "#ec4899"];

  return (
    <div className="screen" data-testid="comparison-screen">
      <h1 className="screen-title" data-testid="comparison-title">Pilot Strategy Comparison</h1>

      <TabGuide tabKey="compare" />

      {/* Pilot Checklist */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h2 style={{ fontSize: 16, margin: 0 }}>Select Pilots (max 5)</h2>
          {selectedIds.length > 0 && (
            <button className="btn btn-neutral" onClick={clearAll} style={{ fontSize: 12, padding: "4px 8px" }}>
              Clear All
            </button>
          )}
        </div>

        {pilotsList.loading ? (
          <Loading lines={1} />
        ) : pilotsList.error || !pilotsList.data ? (
          <ErrorState message={pilotsList.error ?? "No data"} status={pilotsList.status} onRetry={pilotsList.reload} />
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
            {pilotsList.data.map(p => {
              const checked = selectedIds.includes(p.id);
              const disabled = !checked && selectedIds.length >= 5;
              return (
                <label
                  key={p.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    background: checked ? theme.surface3 : theme.surface2,
                    padding: "6px 12px",
                    borderRadius: 20,
                    border: `1px solid ${checked ? theme.accent : theme.border}`,
                    cursor: disabled ? "not-allowed" : "pointer",
                    opacity: disabled ? 0.5 : 1,
                    fontSize: 13,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled}
                    onChange={() => toggleSelect(p.id)}
                    style={{ cursor: "pointer" }}
                    data-testid={`comparison-checkbox-${p.id}`}
                  />
                  {p.name}
                </label>
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
        <div data-testid="row-error-banner" style={{ background: theme.decline, color: theme.base, padding: "10px 16px", borderRadius: 6, marginBottom: 16, fontSize: 13 }}>
          <strong>Notice:</strong> Failed to load performance curve data for some strategies.
        </div>
      )}

      {/* Comparison Grid */}
      {selectedIds.length === 0 ? (
        <div className="empty" style={{ padding: 40 }}>
          Select at least one pilot strategy above to start comparing metrics and performance curves.
        </div>
      ) : (
        <>
          {/* Overlaid Performance Chart */}
          <section className="card card-pad" style={{ marginBottom: 16 }}>
            <h2 style={{ fontSize: 16, margin: "0 0 12px" }}>Overlaid Performance</h2>
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
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255, 255, 255, 0.05)" />
                    <XAxis dataKey="date" stroke={theme.textMuted} fontSize={10} tickLine={false} />
                    <YAxis stroke={theme.textMuted} fontSize={10} tickLine={false} domain={["auto", "auto"]} />
                    <Tooltip
                      contentStyle={{ background: theme.surface2, border: `1px solid ${theme.border}`, borderRadius: 4 }}
                      labelStyle={{ color: theme.textSecondary, fontSize: 11 }}
                      itemStyle={{ fontSize: 11 }}
                    />
                    <Legend wrapperStyle={{ fontSize: 11, paddingTop: 10 }} />
                    {chartPilots.map((p, index) => (
                      <Line
                        key={p.id}
                        type="monotone"
                        dataKey={p.id}
                        name={p.name}
                        stroke={colors[index % colors.length]}
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
                style={{ marginTop: 12, padding: "16px 12px", background: "var(--surface-2)", borderRadius: 12 }}
              >
                <div style={{ fontWeight: 600, color: theme.textSecondary }}>
                  No backtest series for: {nullCurvePilots.map(p => p.name).join(", ")}
                </div>
                <div style={{ marginTop: 6, fontSize: 13, color: theme.textMuted }}>
                  These Pilots have no persisted return curve yet, so no line is drawn for them.
                  Their metrics below are shown honestly.
                </div>
              </div>
            )}
          </section>

          {/* Comparison Table */}
          <section className="card card-pad" style={{ overflowX: "auto" }}>
            <h2 style={{ fontSize: 16, margin: "0 0 12px" }}>Key Metrics Comparison</h2>
            <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.borderStrong}` }}>
                  <th style={{ padding: 8 }}>Metric</th>
                  {selectedPilots.map(p => (
                    <th
                      key={p.id}
                      style={{
                        padding: 8,
                        color: theme.accent,
                        whiteSpace: "normal",
                        wordBreak: "break-word",
                        maxWidth: 120
                      }}
                    >
                      {p.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>Category</td>
                  {selectedPilots.map(p => (
                    <td key={p.id} style={{ padding: 8 }}>{p.category}</td>
                  ))}
                </tr>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>Sharpe Ratio</td>
                  {selectedPilots.map(p => (
                    <td key={p.id} style={{ padding: 8 }} className="num">
                      {p.headline.sharpe == null ? "—" : fmtNum(p.headline.sharpe, 2)}
                    </td>
                  ))}
                </tr>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>PBO</td>
                  {selectedPilots.map(p => (
                    <td key={p.id} style={{ padding: 8 }} className="num">
                      {p.headline.pbo == null ? "—" : fmtNum(p.headline.pbo, 2)}
                    </td>
                  ))}
                </tr>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>Max Drawdown</td>
                  {selectedPilots.map(p => (
                    <td key={p.id} style={{ padding: 8 }} className="num">
                      {p.headline.max_drawdown == null ? "—" : fmtPct(p.headline.max_drawdown, 0, { fromFraction: true })}
                    </td>
                  ))}
                </tr>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>DSR</td>
                  {selectedPilots.map(p => (
                    <td key={p.id} style={{ padding: 8 }} className="num">
                      {p.headline.dsr == null ? "—" : fmtNum(p.headline.dsr, 3)}
                    </td>
                  ))}
                </tr>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>AUM Proxy</td>
                  {selectedPilots.map(p => (
                    <td key={p.id} style={{ padding: 8 }} className="num">
                      {p.aum_proxy == null ? "—" : fmtUsd(p.aum_proxy)}
                    </td>
                  ))}
                </tr>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>Followers</td>
                  {selectedPilots.map(p => (
                    <td key={p.id} style={{ padding: 8 }} className="num">
                      {p.followers_proxy == null ? "—" : p.followers_proxy}
                    </td>
                  ))}
                </tr>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>Actions</td>
                  {selectedPilots.map(p => (
                    <td key={p.id} style={{ padding: 8 }}>
                      <button
                        className="btn btn-primary"
                        onClick={() => setFollowPilot(p)}
                        style={{ fontSize: 12, padding: "4px 8px" }}
                        data-testid={`follow-pilot-btn-${p.id}`}
                      >
                        Follow
                      </button>
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </section>

          {/* Recent pilot alerts */}
          <section className="card card-pad" style={{ marginTop: 16 }} data-testid="comparison-activity-feed">
            <h2 style={{ fontSize: 16, margin: "0 0 12px" }}>Recent pilot alerts</h2>
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
