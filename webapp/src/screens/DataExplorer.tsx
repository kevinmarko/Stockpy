import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Bar, Fundamentals, MacroSnapshot, CurvePoint } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, Tile } from "../components/ui";
import { PerfLine } from "../components/charts";
import { SymbolInput } from "../components/SymbolInput";
import { RecommendedStocks } from "../components/RecommendedStocks";
import { fmtNum } from "../format";
import { theme } from "../theme";

const DASH = "—";

/** Prettify a raw provider/FRED key ("trailingPE" / "T10Y2Y") for display. */
function label(key: string): string {
  return key
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function fmtValue(v: number | string | null): string {
  if (v == null) return DASH;
  if (typeof v === "string") return v;
  return fmtNum(v, Math.abs(v) < 10 ? 2 : 0);
}

function BarsChart({ bars }: { bars: Bar[] }) {
  if (bars.length === 0) {
    return (
      <div className="empty" style={{ padding: 24 }}>
        No bars in the store for this symbol. Run the pipeline or check the ticker.
      </div>
    );
  }
  // A close-price line: reuse PerfLine (date/value series). Null closes are
  // dropped rather than plotted as 0 (CONSTRAINT #4).
  const curve: CurvePoint[] = bars
    .filter((b) => b.Close != null)
    .map((b) => ({ date: b.date, value: b.Close as number }));
  const last = bars[bars.length - 1];
  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(90px, 1fr))", gap: 8, marginBottom: 10 }}>
        <Tile label="Last close" value={last.Close == null ? DASH : fmtNum(last.Close, 2)} />
        <Tile label="Bars" value={String(bars.length)} />
        <Tile label="From" value={bars[0].date} />
      </div>
      {curve.length > 0 ? (
        <PerfLine data={curve} valueLabel="Close" yTickDecimals={0} />
      ) : (
        <div className="empty" style={{ padding: 16 }}>No priced closes to chart.</div>
      )}
    </>
  );
}

function FundamentalsTable({ f }: { f: Fundamentals }) {
  const entries = Object.entries(f);
  if (entries.length === 0) {
    return <div className="empty" style={{ padding: 16 }}>No fundamentals available.</div>;
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <tbody>
          {entries.map(([k, v]) => (
            <tr key={k} style={{ borderTop: "1px solid var(--border)" }}>
              <td style={{ padding: "7px 6px", color: theme.textSecondary }}>{label(k)}</td>
              <td style={{ padding: "7px 6px", textAlign: "right", fontVariantNumeric: "tabular-nums", color: v == null ? theme.textMuted : theme.textPrimary }}>
                {fmtValue(v)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MacroSection() {
  const { data, loading, error, status, reload } = useApi<MacroSnapshot>(() => api.getMacro(), []);
  const known: [string, string][] = [
    ["VIXCLS", "VIX"],
    ["T10Y2Y", "10y–2y curve"],
    ["sahm_rule", "Sahm rule"],
    ["high_yield_oas", "HY OAS"],
  ];
  return (
    <section className="card card-pad" style={{ marginTop: 16 }}>
      <h2 style={{ fontSize: 15, margin: "0 0 8px" }}>Macro snapshot</h2>
      {loading && <Loading lines={1} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: 10 }}>
          {known.map(([key, lbl]) =>
            key in data ? (
              <Tile key={key} label={lbl} value={fmtValue(data[key])} />
            ) : null
          )}
        </div>
      )}
    </section>
  );
}

export function DataExplorer() {
  const nav = useNavigate();
  const [symbol, setSymbol] = useState("AAPL");
  const bars = useApi<Bar[]>(() => api.getDataBars(symbol, 120), [symbol]);
  const fundamentals = useApi<Fundamentals>(() => api.getDataFundamentals(symbol), [symbol]);
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  return (
    <div className="screen">
      <button
        onClick={back}
        style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: theme.textSecondary, fontSize: 14, marginBottom: 8 }}
      >
        ← Back
      </button>
      <h1 className="screen-title">Data explorer</h1>
      <p className="screen-sub">
        See the platform's recommended stocks and browse the raw data layer for
        a symbol — daily bars, current fundamentals, and the macro snapshot.{" "}
        Manage which stocks are tracked in <Link to="/settings">Settings</Link>.
      </p>

      <RecommendedStocks onSelect={setSymbol} />

      <SymbolInput initial={symbol} onSubmit={setSymbol} pending={bars.loading} />

      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 15, margin: "0 0 8px" }}>Price bars · {symbol}</h2>
        {bars.loading && <Loading lines={2} />}
        {!bars.loading && bars.error && (
          <ErrorState message={bars.error} status={bars.status} onRetry={bars.reload} />
        )}
        {!bars.loading && !bars.error && bars.data && <BarsChart bars={bars.data} />}
      </section>

      <section className="card card-pad">
        <h2 style={{ fontSize: 15, margin: "0 0 8px" }}>Fundamentals · {symbol}</h2>
        {fundamentals.loading && <Loading lines={2} />}
        {!fundamentals.loading && fundamentals.error && (
          <ErrorState message={fundamentals.error} status={fundamentals.status} onRetry={fundamentals.reload} />
        )}
        {!fundamentals.loading && !fundamentals.error && fundamentals.data && (
          <FundamentalsTable f={fundamentals.data} />
        )}
      </section>

      <MacroSection />
    </div>
  );
}
