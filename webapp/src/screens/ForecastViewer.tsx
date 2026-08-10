import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import type { Bar, ForecastResult } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, Tile } from "../components/ui";
import { AttentionHeatmapStrip, ForecastCandleChart } from "../components/charts";
import { SymbolInput } from "../components/SymbolInput";
import { TabGuide } from "../components/TabGuide";
import { DynamicGrid, resetGridLayout } from "../components/DynamicGrid";
import { Button } from "../components/ui";
import toast from "react-hot-toast";
import { fmtNum } from "../format";
import { theme } from "../theme";

const DASH = "—";
const HORIZONS: { key: keyof ForecastResult; days: number }[] = [
  { key: "Forecast_10", days: 10 },
  { key: "Forecast_30", days: 30 },
  { key: "Forecast_60", days: 60 },
  { key: "Forecast_90", days: 90 },
];

const LOOKBACK_RANGES: { label: string; days: number }[] = [
  { label: "1M", days: 21 },
  { label: "3M", days: 63 },
  { label: "6M", days: 126 },
  { label: "1Y", days: 252 },
];
const DEFAULT_LOOKBACK_DAYS = 63; // 3M

function LookbackToggle({
  value,
  onChange,
}: {
  value: number;
  onChange: (days: number) => void;
}) {
  return (
    <div className="segmented" role="tablist" aria-label="Price history range">
      {LOOKBACK_RANGES.map((r) => (
        <button
          key={r.label}
          role="tab"
          aria-selected={r.days === value}
          className={r.days === value ? "on" : ""}
          onClick={() => onChange(r.days)}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

function ForecastView({
  symbol,
  d,
  bars,
  lookbackDays,
  onLookbackChange,
}: {
  symbol: string;
  d: ForecastResult;
  bars: Bar[];
  lookbackDays: number;
  onLookbackChange: (days: number) => void;
}) {
  const [selectedHorizon, setSelectedHorizon] = useState<number | null>(30);

  // Export handlers -- both built from the real forecast result `d`, never
  // fabricated placeholder numbers (CONSTRAINT #4). A horizon that didn't
  // converge this run is exported as an empty cell, matching the DASH the
  // tiles above already show for it.
  const handleExportCSV = () => {
    const rows = HORIZONS.map((h) => {
      const mid = d[h.key] as number | null;
      const lower = d[`Forecast_${h.days}_Lower`] as number | null;
      const upper = d[`Forecast_${h.days}_Upper`] as number | null;
      return [`${h.days}d`, mid ?? "", lower ?? "", upper ?? ""].join(",");
    });
    const csvContent = "data:text/csv;charset=utf-8," + ["Horizon,Forecast,Lower,Upper", ...rows].join("\n");
    const link = document.createElement("a");
    link.setAttribute("href", encodeURI(csvContent));
    link.setAttribute("download", `${symbol}_forecast_data.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    toast.success(
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Exported CSV</span>
        <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
          Saved {symbol}_forecast_data.csv to downloads.
        </span>
      </div>
    );
  };

  const handleExportJSON = () => {
    const jsonContent =
      "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(d, null, 2));
    const link = document.createElement("a");
    link.setAttribute("href", jsonContent);
    link.setAttribute("download", `${symbol}_forecast_data.json`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    toast.success(
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Exported JSON</span>
        <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
          Saved {symbol}_forecast_data.json to downloads.
        </span>
      </div>
    );
  };

  // Map the 8 named band fields onto ForecastCandleChart's forecast prop. A
  // null horizon is skipped entirely, never plotted as 0 (CONSTRAINT #4); a
  // populated horizon with a null band still draws its projection point, just
  // without a cone at that horizon (ForecastCandleChart's own contract).
  const forecast = HORIZONS.filter((h) => d[h.key] != null).map((h) => ({
    day: h.days,
    mid: d[h.key] as number,
    lower: d[`Forecast_${h.days}_Lower`] as number | null,
    upper: d[`Forecast_${h.days}_Upper`] as number | null,
  }));

  const hasBand = d.MC_Lower != null && d.MC_Upper != null;
  const noHistory = bars.length === 0 && forecast.length > 0;
  const chartEmpty = bars.length === 0 && forecast.length === 0;
  const convergedCount = forecast.length;

  // Key derived insights -- `null` (never a fabricated anchor price) whenever
  // there's no real last close to derive from.
  const currentPrice = bars.length > 0 && bars[bars.length - 1].Close != null ? (bars[bars.length - 1].Close as number) : null;
  const expectedReturnPct =
    currentPrice != null && d.Forecast_30 != null ? ((Number(d.Forecast_30) - currentPrice) / currentPrice) * 100 : null;
  const volatileRangePct =
    currentPrice != null && hasBand ? ((Number(d.MC_Upper) - Number(d.MC_Lower)) / currentPrice) * 100 : null;

  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(90px, 1fr))", gap: "var(--s-2)", marginBottom: "var(--s-3-5)" }}>
        {HORIZONS.map((h) => (
          <Tile
            key={h.days}
            label={`${h.days}d`}
            value={d[h.key] == null ? DASH : fmtNum(d[h.key] as number, 2)}
          />
        ))}
      </div>

      <div style={{ flex: 1, minHeight: 0 }}>
        <DynamicGrid
          layoutKey="forecast-viewer"
          defaultLayouts={{
            lg: [
              { i: "summary", x: 0, y: 0, w: 12, h: 4, minW: 6, minH: 3 },
              { i: "price", x: 0, y: 4, w: 8, h: 6, minW: 4, minH: 4 },
              { i: "model", x: 8, y: 4, w: 4, h: 6, minW: 3, minH: 4 },
            ],
          }}
        >
          <div key="summary">
            <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0, background: "var(--surface-2)" }}>
              <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "var(--s-2)" }}>
                  <h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>Horizon & Expected Return Summary</h2>
                  <Link
                    to={`/symbol/${symbol}`}
                    className="btn"
                    style={{ fontSize: "var(--t-caption)", background: "var(--surface-3)", color: "var(--accent)" }}
                    onMouseDown={(e) => e.stopPropagation()}
                    onTouchStart={(e) => e.stopPropagation()}
                  >
                    📊 View Model Skill & Historical Accuracy
                  </Link>
                </div>
              </div>
              
              <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: "var(--s-2-5)", marginBottom: "var(--s-3)" }}>
          {HORIZONS.map((h) => {
            const isSelected = selectedHorizon === h.days;
            return (
              <div
                key={h.days}
                onClick={() => setSelectedHorizon(h.days)}
                style={{
                  padding: "var(--s-2-5) var(--s-3)",
                  borderRadius: "var(--r-sm)",
                  background: isSelected ? "var(--surface-3)" : "var(--surface)",
                  border: `1px solid ${isSelected ? "var(--accent)" : "var(--border)"}`,
                  cursor: "pointer",
                  transition: "all 0.15s ease",
                }}
              >
                <div style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>{h.days}d Horizon</div>
                <div style={{ fontSize: "var(--t-title)", fontWeight: 700, color: "var(--text-primary)", marginTop: "2px" }}>
                  {d[h.key] == null ? DASH : fmtNum(d[h.key] as number, 2)}
                </div>
              </div>
            );
          })}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "var(--s-2-5)" }}>
          <Tile
            label="Expected Return (30d)"
            value={expectedReturnPct == null ? DASH : `${expectedReturnPct >= 0 ? "+" : ""}${fmtNum(expectedReturnPct, 2)}%`}
            tone={expectedReturnPct == null ? undefined : expectedReturnPct >= 0 ? "pos" : "neg"}
          />
          <Tile label="Monte Carlo Range Band" value={volatileRangePct == null ? DASH : `±${fmtNum(volatileRangePct / 2, 1)}%`} />
          <Tile
            label="Forecast Trend Direction"
            value={
              expectedReturnPct == null
                ? DASH
                : expectedReturnPct > 1.5
                ? "BULLISH ↗"
                : expectedReturnPct < -1.5
                ? "BEARISH ↘"
                : "NEUTRAL ➔"
            }
          />
        </div>
              </div>
            </section>
          </div>

          <div key="price">
            {/* 1. Data Visualization & Chart Enhancements */}
            <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
              <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "var(--s-2)" }}>
                  <h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>Price & forecast</h2>
                  <div onMouseDown={(e) => e.stopPropagation()} onTouchStart={(e) => e.stopPropagation()}>
                    <LookbackToggle value={lookbackDays} onChange={onLookbackChange} />
                  </div>
                </div>
              </div>

              <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>

        {chartEmpty ? (
          <div className="empty" style={{ padding: "var(--s-5)" }}>
            Not enough forecast or price data to draw a chart.
          </div>
        ) : (
          <>
            {noHistory && (
              <div className="empty" style={{ padding: "var(--s-3)", marginBottom: "var(--s-2-5)", fontSize: "var(--t-label)" }}>
                No price history in the store for this symbol — showing the forecast projection only.
              </div>
            )}
            <ForecastCandleChart bars={bars} forecast={forecast} />
            <AttentionHeatmapStrip attention={d.attention} />
            {d.attention && (
              <p style={{ color: theme.textMuted, fontSize: "var(--t-micro)", marginTop: "var(--s-2)", lineHeight: 1.5 }}>
                Darker cells show which days the BERT-LLA model weighted most
                heavily when forming this forecast — not a stronger buy/sell
                signal.
              </p>
                )}
              </>
            )}
              </div>
            </section>
          </div>

          <div key="model">
            {/* 2. Model Breakdown & Insights */}
            <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
              <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
                <h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>Model detail</h2>
              </div>
              <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: "var(--s-2-5)", marginBottom: "var(--s-3)" }}>
          <Tile label="ARIMA" value={d.ARIMA == null ? DASH : fmtNum(d.ARIMA, 2)} />
          <Tile
            label="MC band"
            value={
              hasBand
                ? `${fmtNum(d.MC_Lower as number, 0)} – ${fmtNum(d.MC_Upper as number, 0)}`
                : DASH
            }
          />
        </div>

        {/* Drivers summary */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "var(--s-3)" }}>
          <div style={{ background: "var(--surface-2)", padding: "var(--s-3)", borderRadius: "var(--r-xs)" }}>
            <div style={{ fontWeight: 600, color: "var(--text-primary)", fontSize: "var(--t-callout)" }}>Volatility Drivers</div>
            <div style={{ fontSize: "var(--t-caption)", color: "var(--text-secondary)", marginTop: "4px" }}>
              Monte Carlo daily sigma is GARCH annualized volatility scaled by
              /sqrt(252). {hasBand ? "" : "No Monte Carlo band converged this run."}
            </div>
          </div>

          <div style={{ background: "var(--surface-2)", padding: "var(--s-3)", borderRadius: "var(--r-xs)" }}>
            <div style={{ fontWeight: 600, color: "var(--text-primary)", fontSize: "var(--t-callout)" }}>Convergence Status</div>
            <div
              style={{
                fontSize: "var(--t-caption)",
                color: convergedCount === HORIZONS.length ? "var(--growth)" : "var(--caution)",
                marginTop: "4px",
                fontWeight: 600,
              }}
            >
              {convergedCount === HORIZONS.length
                ? `✓ All ${HORIZONS.length} horizons converged this run.`
                : `⚠ ${convergedCount} of ${HORIZONS.length} horizons converged this run.`}
            </div>
          </div>
        </div>
              </div>
            </section>
          </div>
        </DynamicGrid>
      </div>

      {/* 3. User Interaction & Export Capabilities */}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--s-2)" }}>
        <button className="btn" onClick={handleExportCSV} style={{ fontSize: "var(--t-caption)" }}>
          📥 Export CSV
        </button>
        <button className="btn" onClick={handleExportJSON} style={{ fontSize: "var(--t-caption)" }}>
          📥 Export JSON
        </button>
      </div>
    </>
  );
}

export function ForecastViewer() {
  const nav = useNavigate();
  const [symbol, setSymbol] = useState("AAPL");
  const [lookbackDays, setLookbackDays] = useState(DEFAULT_LOOKBACK_DAYS);
  const { data, loading, error, status, reload } = useApi<ForecastResult>(
    () => api.getForecastResult(symbol),
    [symbol]
  );
  const { data: barsData } = useApi<Bar[]>(
    () => api.getDataBars(symbol, lookbackDays),
    [symbol, lookbackDays]
  );
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  return (
    <div className="screen">
      <button
        onClick={back}
        style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: theme.textSecondary, fontSize: "var(--t-callout)", marginBottom: "var(--s-2)" }}
      >
        ← Back
      </button>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="screen-title" style={{ marginTop: "var(--s-2)" }}>Forecast viewer</h1>
          <p className="screen-sub">
            Multi-horizon price forecast for a symbol — 10/30/60/90-day blended levels, model weighting, drivers, and Monte-Carlo confidence bands.
          </p>
        </div>
        <Button variant="neutral" onClick={() => resetGridLayout("forecast-viewer")}>Reset Layout</Button>
      </div>

      <TabGuide tabKey="forecast" />

      <SymbolInput initial={symbol} onSubmit={setSymbol} pending={loading} />

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        <ForecastView
          symbol={symbol}
          d={data}
          bars={barsData ?? []}
          lookbackDays={lookbackDays}
          onLookbackChange={setLookbackDays}
        />
      )}
    </div>
  );
}
