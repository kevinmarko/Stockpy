import { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { Portfolio, PilotSummary, PerfRange, CurvePoint, ObservabilitySummary } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { ErrorState, Loading, Notice, Tile } from "../components/ui";
import { Toggle } from "../components/Toggle";
import { TabGuide } from "../components/TabGuide";
import { ActivityFeed } from "../components/ActivityFeed";
import { NotebookMLExport } from "../components/NotebookMLExport";
import { PerfLine, Sparkline } from "../components/charts";
import { RangeToggle } from "../components/RangeToggle";
import { theme } from "../theme";
import { fmtUsd, fmtSignedUsd } from "../format";
import { deriveAttentionItems } from "../observabilityAttention";



export function Dashboard() {
  const navigate = useNavigate();

  const [range, setRange] = useState<PerfRange>("3M");
  const port = useApi<Portfolio>(() => api.getPortfolio(), []);
  const equity = useApi<{ range: PerfRange; curve: CurvePoint[] | null }>(
    () => api.getEquityCurve(range),
    [range]
  );
  const pilots = useApi<PilotSummary[]>(() => api.listPilots(), []);

  // Feeds the "needs attention" banner below — reuses the exact same
  // GET /observability/summary + deriveAttentionItems() Mission Control
  // itself renders (observabilityAttention.ts), so the two screens can never
  // disagree about what's notable. range/horizon here don't affect any field
  // deriveAttentionItems reads (only equity_curve/forecast_skill vary by
  // those params), so the choice is arbitrary.
  const obs = useApi<ObservabilitySummary>(() => api.getObservabilitySummary("1M", 30), []);
  const attentionItems = useMemo(
    () => (obs.data ? deriveAttentionItems(obs.data) : []),
    [obs.data]
  );

  useAutoPoll(
    () => {
      port.reload();
      equity.reload();
      pilots.reload();
      obs.reload();
    },
    "dashboard",
    { hasError: port.error != null }
  );

  const [selectedTopPilots, setSelectedTopPilots] = useState<string[]>([]);


  // Retain the last successfully-loaded portfolio so a FAILED refresh keeps the
  // stale snapshot on screen behind an "offline: using cached data" notice,
  // rather than blanking to an error (useApi clears `data` on error).
  const [lastGoodPortfolio, setLastGoodPortfolio] = useState<Portfolio | null>(null);
  useEffect(() => {
    if (port.data) setLastGoodPortfolio(port.data);
  }, [port.data]);
  const shownPortfolio = port.data ?? lastGoodPortfolio;
  // A live fetch failed but we still hold a cached snapshot to display —
  // either this session's in-memory `lastGoodPortfolio`, or (surviving a
  // fresh page reload too) client.ts's localStorage offline-cache fallback,
  // flagged via `port.stale`.
  const portfolioIsOffline =
    !port.loading &&
    (port.stale || (!port.data && !!port.error && !!lastGoodPortfolio));
  // Local for clean type-narrowing of the (nullable) equity curve in the JSX.
  const equityCurve: CurvePoint[] | null = equity.data?.curve ?? null;

  const handleToggleTopPilot = (id: string) => {
    setSelectedTopPilots(prev =>
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
    );
  };

  const handleCompareSelected = () => {
    localStorage.setItem("comparison_selected_ids", JSON.stringify(selectedTopPilots));
    navigate("/compare");
  };

  return (
    <div className="screen" data-testid="dashboard-screen">
      <div style={{ marginBottom: "var(--s-4)" }}>
        <h1 className="screen-title" data-testid="dashboard-title">Dashboard</h1>
      </div>

      <TabGuide tabKey="dashboard" />

      {/* Only rendered when something's actually notable — an all-clear
          banner on the one screen already opened every session would just be
          new noise (see the published Mission Control research: the whole
          point of this pointer is giving that screen a reason to be opened,
          not turning this one into a second copy of it). */}
      {attentionItems.length > 0 && (
        <Notice
          variant="warn"
          style={{ marginBottom: "var(--s-4)" }}
          data-testid="dashboard-attention-banner"
        >
          {attentionItems.length} item{attentionItems.length === 1 ? "" : "s"} need
          {attentionItems.length === 1 ? "s" : ""} attention —{" "}
          <button
            onClick={() => navigate("/observability")}
            style={{
              background: "none",
              border: "none",
              color: theme.accent,
              cursor: "pointer",
              textDecoration: "underline",
              padding: 0,
              font: "inherit",
            }}
          >
            view in Mission Control →
          </button>
        </Notice>
      )}

      <div className="dashboard-layout" style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
        {/* Portfolio Summary */}
        <div
          key="portfolio"
          className="card card-pad dashboard-widget-wide"
          style={{
            display: "flex",
            flexDirection: "column",
            border: `1px solid ${theme.borderStrong}`,
            height: "100%",
          }}
          data-testid={`widget-portfolio-summary`}
        >
          <div className="drag-handle" style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            borderBottom: `1px solid ${theme.border}`,
            paddingBottom: 8,
            marginBottom: "var(--s-3)",
            cursor: "grab",
          }}>
            <span style={{ fontWeight: 700, color: theme.textPrimary }}>Portfolio Summary</span>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            {port.loading && !shownPortfolio ? (
              <Loading lines={2} />
            ) : !shownPortfolio ? (
              port.status === 404 ? (
                <div data-testid="portfolio-empty-state" style={{ padding: "var(--s-2)" }}>
                  <h3>Nothing here yet</h3>
                  <p>Run the Stockpy pipeline to produce data, then pull to refresh.</p>
                </div>
              ) : (
                <ErrorState message={port.error ?? "No data"} status={port.status} onRetry={port.reload} />
              )
            ) : (
              <div>
                {portfolioIsOffline && (
                  <Notice
                    variant="warn"
                    style={{ marginBottom: "var(--s-3)", fontSize: "var(--t-caption)" }}
                    data-testid="portfolio-offline-warning"
                  >
                    Offline: using cached data. <button onClick={port.reload} style={{ background: "none", border: "none", color: theme.accent, cursor: "pointer", textDecoration: "underline", padding: 0 }}>Retry</button>
                  </Notice>
                )}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div className="num" style={{ fontSize: 24, fontWeight: 800 }}>
                    {fmtUsd(shownPortfolio.total_equity)}
                  </div>
                  <button
                    className="btn"
                    onClick={port.reload}
                    style={{ fontSize: 10, padding: "var(--s-0-5) var(--s-1-5)" }}
                    data-testid="portfolio-refresh-btn"
                  >
                    Refresh
                  </button>
                </div>
                <div className="num" style={{ color: shownPortfolio.total_unrealized_pl >= 0 ? theme.growth : theme.decline, fontSize: "var(--t-body)", marginBottom: "var(--s-3)" }}>
                  {fmtSignedUsd(shownPortfolio.total_unrealized_pl)} unrealized
                </div>
                <div className="tiles">
                  <Tile label="Buying Power" value={fmtUsd(shownPortfolio.buying_power)} />
                  <Tile label="Positions" value={shownPortfolio.position_count} />
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Account Performance */}
        <div
          key="performance"
          className="card card-pad dashboard-widget-full"
          style={{
            display: "flex",
            flexDirection: "column",
            border: `1px solid ${theme.borderStrong}`,
            height: "100%",
          }}
          data-testid={`widget-performance-curve`}
        >
          <div className="drag-handle" style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            borderBottom: `1px solid ${theme.border}`,
            paddingBottom: 8,
            marginBottom: "var(--s-3)",
            cursor: "grab",
          }}>
            <span style={{ fontWeight: 700, color: theme.textPrimary }}>Account Performance</span>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            {equity.loading ? (
              <div className="skeleton" style={{ height: 150 }} />
            ) : Array.isArray(equityCurve) && equityCurve.length > 0 ? (
              <PerfLine data={equityCurve} valueFormat="currency" />
            ) : (
              <div
                className="empty"
                data-testid="equity-empty"
                style={{ padding: "var(--s-8) var(--s-2)", background: "var(--surface-2)", borderRadius: "var(--r-md)" }}
              >
                <div style={{ fontWeight: 600, color: theme.textSecondary }}>
                  No account performance data yet
                </div>
                <div style={{ marginTop: "var(--s-1-5)", fontSize: "var(--t-body)" }}>
                  No curve data available. Run the Stockpy pipeline to accumulate an
                  account equity history.
                </div>
              </div>
            )}
            <div style={{ marginTop: "var(--s-2)" }}>
              <RangeToggle value={range} onChange={setRange} />
            </div>
          </div>
        </div>

        {/* Activity Feed */}
        <div
          key="activity"
          className="card card-pad dashboard-widget-wide"
          style={{
            display: "flex",
            flexDirection: "column",
            border: `1px solid ${theme.borderStrong}`,
            height: "100%",
          }}
          data-testid={`widget-activity-feed`}
        >
          <div className="drag-handle" style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            borderBottom: `1px solid ${theme.border}`,
            paddingBottom: 8,
            marginBottom: "var(--s-3)",
            cursor: "grab",
          }}>
            <span style={{ fontWeight: 700, color: theme.textPrimary }}>Activity Feed</span>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            <ActivityFeed limit={5} />
          </div>
        </div>

        {/* Top Pilots */}
        <div
          key="pilots"
          className="card card-pad dashboard-widget-wide"
          style={{
            display: "flex",
            flexDirection: "column",
            border: `1px solid ${theme.borderStrong}`,
            height: "100%",
          }}
          data-testid={`widget-top-pilots`}
        >
          <div className="drag-handle" style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            borderBottom: `1px solid ${theme.border}`,
            paddingBottom: 8,
            marginBottom: "var(--s-3)",
            cursor: "grab",
          }}>
            <span style={{ fontWeight: 700, color: theme.textPrimary }}>Top Pilots</span>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            {pilots.loading ? (
              <Loading lines={2} />
            ) : pilots.error || !pilots.data ? (
              <ErrorState message={pilots.error ?? "No data"} status={pilots.status} onRetry={pilots.reload} />
            ) : (
              <div>
                <div className="list" style={{ marginBottom: "var(--s-3)" }}>
                  {pilots.data.slice(0, 5).map(p => (
                    <PilotRow 
                      key={p.id} 
                      pilot={p} 
                      isChecked={selectedTopPilots.includes(p.id)}
                      onToggle={() => handleToggleTopPilot(p.id)} 
                    />
                  ))}
                </div>
                <button
                  className="btn btn-primary"
                  onClick={handleCompareSelected}
                  disabled={selectedTopPilots.length === 0}
                  data-testid="compare-selected-btn"
                  style={{ width: "100%", fontSize: "var(--t-caption)" }}
                >
                  Compare Selected
                </button>
              </div>
            )}
          </div>
        </div>

        {/* NotebookML Export */}
        <div
          key="notebook"
          className="card card-pad"
          style={{
            display: "flex",
            flexDirection: "column",
            border: `1px solid ${theme.borderStrong}`,
            height: "100%",
          }}
          data-testid={`widget-notebook-export`}
        >
          <div className="drag-handle" style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            borderBottom: `1px solid ${theme.border}`,
            paddingBottom: 8,
            marginBottom: "var(--s-3)",
            cursor: "grab",
          }}>
            <span style={{ fontWeight: 700, color: theme.textPrimary }}>NotebookML Export</span>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            <NotebookMLExport portfolio={port.data} />
          </div>
        </div>
      </div>
    </div>
  );
}

function PilotRow({
  pilot,
  isChecked,
  onToggle,
}: {
  pilot: PilotSummary;
  isChecked: boolean;
  onToggle: () => void;
}) {
  const perf = useApi(() => api.getPerformance(pilot.id, "3M"), [pilot.id]);
  const curve = perf.data?.curve;
  const isUp = curve && curve.length > 0 && curve[curve.length - 1].value >= curve[0].value;

  return (
    <div className="row" style={{ padding: "var(--s-2) 0", display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
      <div style={{ flexShrink: 0 }}>
        <Toggle
          label=""
          checked={isChecked}
          onChange={() => onToggle()}
          dataTestId={`top-pilot-checkbox-${pilot.id}`}
        />
      </div>
      <div className="row-main" style={{ flex: 1, minWidth: 100 }}>
        <span className="row-title">{pilot.name}</span>
        <span className="row-sub" style={{ fontSize: "var(--t-micro)", color: theme.textSecondary }}>{pilot.category}</span>
      </div>
      <div style={{ flex: "0 0 60px", height: 32 }}>
        {curve && curve.length > 0 ? (
          <Sparkline data={curve} positive={!!isUp} />
        ) : perf.loading ? (
          <div className="skeleton" style={{ width: "100%", height: "100%" }} />
        ) : null}
      </div>
      <div className="row-end" style={{ flexShrink: 0, width: 70, textAlign: "right" }}>
        <div className="num" style={{ fontWeight: 700 }}>
          {pilot.headline.sharpe ? `SR: ${pilot.headline.sharpe.toFixed(2)}` : "SR: —"}
        </div>
      </div>
    </div>
  );
}
