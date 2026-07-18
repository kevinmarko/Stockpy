import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ForecastResult, CurvePoint } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, Tile } from "../components/ui";
import { PerfLine } from "../components/charts";
import { SymbolInput } from "../components/SymbolInput";
import { fmtNum } from "../format";
import { theme } from "../theme";

const DASH = "—";
const HORIZONS: { key: keyof ForecastResult; days: number }[] = [
  { key: "Forecast_10", days: 10 },
  { key: "Forecast_30", days: 30 },
  { key: "Forecast_60", days: 60 },
  { key: "Forecast_90", days: 90 },
];

function ForecastView({ d }: { d: ForecastResult }) {
  // Build the horizon curve from whatever horizons actually returned a value;
  // a null horizon is skipped, never plotted as 0 (CONSTRAINT #4). We anchor
  // day 0 at the tightest available reference (ARIMA ~ near-term) only if present.
  const points: CurvePoint[] = HORIZONS.filter((h) => d[h.key] != null).map((h) => ({
    date: `+${h.days}d`,
    value: d[h.key] as number,
  }));

  const hasBand = d.MC_Lower != null && d.MC_Upper != null;

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

      {points.length >= 2 ? (
        <section className="card card-pad" style={{ marginBottom: 14 }}>
          <h2 style={{ fontSize: 15, margin: "0 0 8px" }}>Forecast path</h2>
          <PerfLine data={points} valueLabel="Forecast" yTickDecimals={0} />
        </section>
      ) : (
        <div className="empty" style={{ padding: 20, marginBottom: 14 }}>
          Not enough populated horizons to draw a path.
        </div>
      )}

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
          CNN-LSTM) with the Monte-Carlo confidence band. A horizon that didn't
          converge this run shows {DASH}, never a fabricated level.
        </p>
      </section>
    </>
  );
}

export function ForecastViewer() {
  const nav = useNavigate();
  const [symbol, setSymbol] = useState("AAPL");
  const { data, loading, error, status, reload } = useApi<ForecastResult>(
    () => api.getForecastResult(symbol),
    [symbol]
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

      <SymbolInput initial={symbol} onSubmit={setSymbol} pending={loading} />

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && <ForecastView d={data} />}
    </div>
  );
}
