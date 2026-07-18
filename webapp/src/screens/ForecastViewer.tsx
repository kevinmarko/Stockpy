import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Bar, ForecastResult } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, Tile } from "../components/ui";
import { ForecastCandleChart } from "../components/charts";
import { SymbolInput } from "../components/SymbolInput";
import { TabGuide } from "../components/TabGuide";
import { fmtNum } from "../format";
import { theme } from "../theme";

const DASH = "—";
const HORIZONS: { key: keyof ForecastResult; days: number }[] = [
  { key: "Forecast_10", days: 10 },
  { key: "Forecast_30", days: 30 },
  { key: "Forecast_60", days: 60 },
  { key: "Forecast_90", days: 90 },
];

// Price-history lookback presets for the chart's range toggle. Same idiom as
// components/RangeToggle.tsx (.segmented CSS class), but distinct values —
// this drives a raw day count into GET /data/bars, not a PerfRange enum.
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
  d,
  bars,
  lookbackDays,
  onLookbackChange,
}: {
  d: ForecastResult;
  bars: Bar[];
  lookbackDays: number;
  onLookbackChange: (days: number) => void;
}) {
  // Map the 8 named band fields onto ForecastCandleChart's forecast prop. A
  // null horizon is skipped entirely, never plotted as 0 (CONSTRAINT #4); a
  // populated horizon with a null band still draws its projection point, just
  // without a cone at that horizon (ForecastCandleChart's own contract).
  const forecast = HORIZONS.filter((h) => d[h.key] != null).map((h) => ({
    day: h.days,
    mid: d[h.key] as number,
    lower: d[`Forecast_${h.days}_Lower`],
    upper: d[`Forecast_${h.days}_Upper`],
  }));

  const hasBand = d.MC_Lower != null && d.MC_Upper != null;
  // Bars fetch failing/empty never blocks the forecast — this is an honest
  // inline note, not a page-level error (the forecast is the primary content).
  const noHistory = bars.length === 0 && forecast.length > 0;
  const chartEmpty = bars.length === 0 && forecast.length === 0;

  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(90px, 1fr))", gap: 8, marginBottom: 14 }}>
        {HORIZONS.map((h) => (
          <Tile
            key={h.days}
            label={`${h.days}d`}
            value={d[h.key] == null ? DASH : fmtNum(d[h.key] as number, 2)}
          />
        ))}
      </div>

      <section className="card card-pad" style={{ marginBottom: 14 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            flexWrap: "wrap",
            gap: 8,
            marginBottom: 8,
          }}
        >
          <h2 style={{ fontSize: 15, margin: 0 }}>Price & forecast</h2>
          <LookbackToggle value={lookbackDays} onChange={onLookbackChange} />
        </div>
        {chartEmpty ? (
          <div className="empty" style={{ padding: 20 }}>
            Not enough forecast or price data to draw a chart.
          </div>
        ) : (
          <>
            {noHistory && (
              <div className="empty" style={{ padding: 12, marginBottom: 10, fontSize: 12.5 }}>
                No price history in the store for this symbol — showing the
                forecast projection only.
              </div>
            )}
            <ForecastCandleChart bars={bars} forecast={forecast} />
          </>
        )}
      </section>

      <section className="card card-pad">
        <h2 style={{ fontSize: 15, margin: "0 0 8px" }}>Model detail</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: 10 }}>
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
        <p style={{ color: theme.textMuted, fontSize: 11.5, marginTop: 12, lineHeight: 1.5 }}>
          Multi-horizon blended forecast (ARIMA / Monte Carlo / Holt-Winters /
          CNN-LSTM) with the Monte-Carlo confidence band, plotted above as a
          widening cone against real price history. A horizon that didn't
          converge this run shows {DASH}, never a fabricated level.
        </p>
      </section>
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
  // Independent second fetch — real price history for the chart. Its own
  // loading/error state never gates the forecast tiles/model detail; a failed
  // or empty bars fetch degrades to an inline note inside ForecastView, not a
  // page-level error (the forecast is the primary, valid content here).
  const { data: barsData } = useApi<Bar[]>(
    () => api.getDataBars(symbol, lookbackDays),
    [symbol, lookbackDays]
  );
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  return (
    <div className="screen">
      <button
        onClick={back}
        style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: theme.textSecondary, fontSize: 14, marginBottom: 8 }}
      >
        ← Back
      </button>
      <h1 className="screen-title">Forecast viewer</h1>
      <p className="screen-sub">
        Multi-horizon price forecast for a symbol — the 10/30/60/90-day blended
        levels and the Monte-Carlo band. This is the forecast itself; the model
        skill/accuracy history lives on each symbol's detail page.
      </p>

      <TabGuide tabKey="forecast" />

      <SymbolInput initial={symbol} onSubmit={setSymbol} pending={loading} />

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        <ForecastView
          d={data}
          bars={barsData ?? []}
          lookbackDays={lookbackDays}
          onLookbackChange={setLookbackDays}
        />
      )}
    </div>
  );
}
