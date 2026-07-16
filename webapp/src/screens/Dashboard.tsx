import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Portfolio, PilotSummary, PerfRange, CurvePoint } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, Tile } from "../components/ui";
import { ActivityFeed } from "../components/ActivityFeed";
import { NotebookMLExport } from "../components/NotebookMLExport";
import { PerfLine } from "../components/charts";
import { RangeToggle } from "../components/RangeToggle";
import { theme } from "../theme";
import { fmtUsd, fmtSignedUsd } from "../format";

interface WidgetLayout {
  id: string;
  title: string;
  size: "S" | "M" | "L";
}

const DEFAULT_LAYOUT: WidgetLayout[] = [
  { id: "portfolio-summary", title: "Portfolio Summary", size: "M" },
  { id: "performance-curve", title: "Account Performance", size: "L" },
  { id: "activity-feed", title: "Activity Feed", size: "M" },
  { id: "top-pilots", title: "Top Pilots", size: "M" },
  { id: "notebook-export", title: "NotebookML Export", size: "S" },
];

export function Dashboard() {
  const navigate = useNavigate();

  const [layout, setLayout] = useState<WidgetLayout[]>(() => {
    try {
      const saved = localStorage.getItem("dashboard_layout");
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0) {
          const defaultIds = new Set(DEFAULT_LAYOUT.map(w => w.id));
          const seen = new Set<string>();
          const validParsed: WidgetLayout[] = [];

          parsed.forEach((w) => {
            if (
              w &&
              typeof w.id === "string" &&
              defaultIds.has(w.id) &&
              !seen.has(w.id) &&
              typeof w.title === "string" &&
              (w.size === "S" || w.size === "M" || w.size === "L")
            ) {
              seen.add(w.id);
              validParsed.push(w);
            }
          });

          if (validParsed.length > 0) {
            const savedIds = new Set(validParsed.map(w => w.id));
            const missing = DEFAULT_LAYOUT.filter(w => !savedIds.has(w.id));
            return [...validParsed, ...missing];
          }
        }
      }
      return DEFAULT_LAYOUT;
    } catch {
      return DEFAULT_LAYOUT;
    }
  });

  const [range, setRange] = useState<PerfRange>("3M");
  const port = useApi<Portfolio>(() => api.getPortfolio(), []);
  const equity = useApi<{ range: PerfRange; curve: CurvePoint[] | null }>(
    () => api.getEquityCurve(range),
    [range]
  );
  const pilots = useApi<PilotSummary[]>(() => api.listPilots(), []);

  const [selectedTopPilots, setSelectedTopPilots] = useState<string[]>([]);
  const [isMobile, setIsMobile] = useState(() => typeof window !== "undefined" && window.innerWidth < 768);

  // Retain the last successfully-loaded portfolio so a FAILED refresh keeps the
  // stale snapshot on screen behind an "offline: using cached data" notice,
  // rather than blanking to an error (useApi clears `data` on error).
  const [lastGoodPortfolio, setLastGoodPortfolio] = useState<Portfolio | null>(null);
  useEffect(() => {
    if (port.data) setLastGoodPortfolio(port.data);
  }, [port.data]);
  const shownPortfolio = port.data ?? lastGoodPortfolio;
  // A live fetch failed but we still hold a cached snapshot to display.
  const portfolioIsOffline = !port.loading && !port.data && !!port.error && !!lastGoodPortfolio;
  // Local for clean type-narrowing of the (nullable) equity curve in the JSX.
  const equityCurve: CurvePoint[] | null = equity.data?.curve ?? null;

  useEffect(() => {
    localStorage.setItem("dashboard_layout", JSON.stringify(layout));
  }, [layout]);

  useEffect(() => {
    const handleWindowResize = () => {
      setIsMobile(window.innerWidth < 768);
    };
    handleWindowResize();
    window.addEventListener("resize", handleWindowResize);
    return () => window.removeEventListener("resize", handleWindowResize);
  }, []);

  const handleDragStart = (e: React.DragEvent, index: number) => {
    e.dataTransfer.setData("text/plain", index.toString());
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
  };

  const handleDrop = (e: React.DragEvent, targetIndex: number) => {
    e.preventDefault();
    const sourceIndexStr = e.dataTransfer.getData("text/plain");
    if (!sourceIndexStr) return;
    const sourceIndex = parseInt(sourceIndexStr, 10);
    if (isNaN(sourceIndex) || sourceIndex === targetIndex) return;
    if (sourceIndex < 0 || sourceIndex >= layout.length) return;

    const nextLayout = [...layout];
    const [draggedWidget] = nextLayout.splice(sourceIndex, 1);
    nextLayout.splice(targetIndex, 0, draggedWidget);
    setLayout(nextLayout);
  };

  const moveWidget = (id: string, direction: -1 | 1) => {
    setLayout(prev => {
      const currentIndex = prev.findIndex(w => w.id === id);
      if (currentIndex === -1) return prev;
      const targetIndex = currentIndex + direction;
      if (targetIndex < 0 || targetIndex >= prev.length) return prev;
      const nextLayout = [...prev];
      const [moved] = nextLayout.splice(currentIndex, 1);
      nextLayout.splice(targetIndex, 0, moved);
      return nextLayout;
    });
  };

  const handleResize = (id: string, newSize: "S" | "M" | "L") => {
    setLayout(prev =>
      prev.map(w => (w.id === id ? { ...w, size: newSize } : w))
    );
  };

  const getWidgetStyle = (size: "S" | "M" | "L") => {
    if (isMobile) {
      return { gridColumn: "span 3", minHeight: 180 };
    }
    switch (size) {
      case "S": return { gridColumn: "span 1", minHeight: 180 };
      case "M": return { gridColumn: "span 2", minHeight: 280 };
      case "L": return { gridColumn: "span 3", minHeight: 380 };
      default: return { gridColumn: "span 2" };
    }
  };

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
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h1 className="screen-title" data-testid="dashboard-title">Dashboard</h1>
        <button 
          className="btn" 
          onClick={() => setLayout(DEFAULT_LAYOUT)}
          style={{ fontSize: 12, padding: "4px 8px" }}
        >
          Reset Layout
        </button>
      </div>

      <div 
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 16,
          alignItems: "start",
        }}
        onDragOver={handleDragOver}
      >
        {layout.map((w, index) => (
          <div
            key={w.id}
            draggable
            onDragStart={(e) => handleDragStart(e, index)}
            onDragOver={handleDragOver}
            onDrop={(e) => handleDrop(e, index)}
            className="card card-pad"
            style={{
              ...getWidgetStyle(w.size),
              display: "flex",
              flexDirection: "column",
              border: `1px solid ${theme.borderStrong}`,
              position: "relative",
              cursor: "grab",
            }}
            data-testid={`widget-${w.id}`}
          >
            {/* Widget Header */}
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              borderBottom: `1px solid ${theme.border}`,
              paddingBottom: 8,
              marginBottom: 12,
              cursor: "move",
            }}>
              <span style={{ fontWeight: 700, color: theme.textPrimary }}>{w.title}</span>
              <div style={{ display: "flex", gap: 4 }}>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    moveWidget(w.id, -1);
                  }}
                  disabled={index === 0}
                  aria-label="Move widget up"
                  style={{
                    fontSize: 10,
                    width: 24,
                    height: 24,
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: theme.surface2,
                    color: theme.textPrimary,
                    border: "none",
                    borderRadius: 3,
                    cursor: index === 0 ? "not-allowed" : "pointer",
                    opacity: index === 0 ? 0.5 : 1,
                    outline: "revert",
                  }}
                  data-testid={`move-up-${w.id}`}
                >
                  ↑
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    moveWidget(w.id, 1);
                  }}
                  disabled={index === layout.length - 1}
                  aria-label="Move widget down"
                  style={{
                    fontSize: 10,
                    width: 24,
                    height: 24,
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: theme.surface2,
                    color: theme.textPrimary,
                    border: "none",
                    borderRadius: 3,
                    cursor: index === layout.length - 1 ? "not-allowed" : "pointer",
                    opacity: index === layout.length - 1 ? 0.5 : 1,
                    outline: "revert",
                  }}
                  data-testid={`move-down-${w.id}`}
                >
                  ↓
                </button>
                {(["S", "M", "L"] as const).map(sz => (
                  <button
                    key={sz}
                    onClick={(e) => {
                      e.stopPropagation();
                      handleResize(w.id, sz);
                    }}
                    style={{
                      fontSize: 10,
                      padding: "2px 4px",
                      background: w.size === sz ? theme.accent : theme.surface2,
                      color: w.size === sz ? theme.base : theme.textPrimary,
                      border: "none",
                      borderRadius: 3,
                      cursor: "pointer",
                    }}
                    data-testid={`resize-${w.id}-${sz}`}
                  >
                    {sz}
                  </button>
                ))}
              </div>
            </div>

            {/* Widget Content */}
            <div style={{ flex: 1, overflow: "auto" }} onClick={(e) => e.stopPropagation()} onDragStart={(e) => e.stopPropagation()}>
              {w.id === "portfolio-summary" && (
                <div>
                  {port.loading && !shownPortfolio ? (
                    <Loading lines={2} />
                  ) : !shownPortfolio ? (
                    port.status === 404 ? (
                      <div data-testid="portfolio-empty-state" style={{ padding: 8 }}>
                        <h3>Nothing here yet</h3>
                        <p>Run the Stockpy pipeline to produce data, then pull to refresh.</p>
                      </div>
                    ) : (
                      <ErrorState message={port.error ?? "No data"} status={port.status} onRetry={port.reload} />
                    )
                  ) : (
                    <div>
                      {portfolioIsOffline && (
                        <div
                          className="notice notice-warn"
                          style={{ marginBottom: 12, fontSize: 12 }}
                          data-testid="portfolio-offline-warning"
                        >
                          Offline: using cached data. <button onClick={port.reload} style={{ background: "none", border: "none", color: theme.accent, cursor: "pointer", textDecoration: "underline", padding: 0 }}>Retry</button>
                        </div>
                      )}
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <div className="num" style={{ fontSize: 24, fontWeight: 800 }}>
                          {fmtUsd(shownPortfolio.total_equity)}
                        </div>
                        <button
                          className="btn"
                          onClick={port.reload}
                          style={{ fontSize: 10, padding: "2px 6px" }}
                          data-testid="portfolio-refresh-btn"
                        >
                          Refresh
                        </button>
                      </div>
                      <div className="num" style={{ color: shownPortfolio.total_unrealized_pl >= 0 ? theme.growth : theme.decline, fontSize: 13, marginBottom: 12 }}>
                        {fmtSignedUsd(shownPortfolio.total_unrealized_pl)} unrealized
                      </div>
                      <div className="tiles">
                        <Tile label="Buying Power" value={fmtUsd(shownPortfolio.buying_power)} />
                        <Tile label="Positions" value={shownPortfolio.position_count} />
                      </div>
                    </div>
                  )}
                </div>
              )}

              {w.id === "performance-curve" && (
                <div>
                  {equity.loading ? (
                    <div className="skeleton" style={{ height: 150 }} />
                  ) : Array.isArray(equityCurve) && equityCurve.length > 0 ? (
                    <PerfLine data={equityCurve} />
                  ) : (
                    // Honest empty panel (mirrors PilotDetail) — never a blank
                    // chart. PerfLine returns null on an empty series, so the
                    // caller owns the empty state.
                    <div
                      className="empty"
                      data-testid="equity-empty"
                      style={{ padding: "32px 8px", background: "var(--surface-2)", borderRadius: 12 }}
                    >
                      <div style={{ fontWeight: 600, color: theme.textSecondary }}>
                        No account performance data yet
                      </div>
                      <div style={{ marginTop: 6, fontSize: 13 }}>
                        No curve data available. Run the Stockpy pipeline to accumulate an
                        account equity history.
                      </div>
                    </div>
                  )}
                  <div style={{ marginTop: 8 }}>
                    <RangeToggle value={range} onChange={setRange} />
                  </div>
                </div>
              )}

              {w.id === "activity-feed" && (
                <ActivityFeed limit={5} />
              )}

              {w.id === "top-pilots" && (
                <div>
                  {pilots.loading ? (
                    <Loading lines={2} />
                  ) : pilots.error || !pilots.data ? (
                    <ErrorState message={pilots.error ?? "No data"} status={pilots.status} onRetry={pilots.reload} />
                  ) : (
                    <div>
                      <div className="list" style={{ marginBottom: 12 }}>
                        {pilots.data.slice(0, 5).map(p => {
                          const isChecked = selectedTopPilots.includes(p.id);
                          return (
                            <div key={p.id} className="row" style={{ padding: "8px 0", display: "flex", alignItems: "center", gap: 8 }}>
                              <input
                                type="checkbox"
                                checked={isChecked}
                                onChange={() => handleToggleTopPilot(p.id)}
                                data-testid={`top-pilot-checkbox-${p.id}`}
                                style={{ cursor: "pointer" }}
                              />
                              <div className="row-main" style={{ flex: 1 }}>
                                <span className="row-title">{p.name}</span>
                                <span className="row-sub" style={{ fontSize: 11, color: theme.textSecondary }}>{p.category}</span>
                              </div>
                              <div className="row-end">
                                <div className="num" style={{ fontWeight: 700 }}>
                                  {p.headline.sharpe ? `SR: ${p.headline.sharpe.toFixed(2)}` : "SR: —"}
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                      <button
                        className="btn btn-primary"
                        onClick={handleCompareSelected}
                        disabled={selectedTopPilots.length === 0}
                        data-testid="compare-selected-btn"
                        style={{ width: "100%", fontSize: 12 }}
                      >
                        Compare Selected
                      </button>
                    </div>
                  )}
                </div>
              )}

              {w.id === "notebook-export" && (
                <NotebookMLExport portfolio={port.data} />
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
