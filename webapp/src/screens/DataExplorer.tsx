import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import type { Bar, Fundamentals, MacroSnapshot, CurvePoint } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { ErrorState, Loading, Table, Tile } from "../components/ui";
import { PerfLine } from "../components/charts";
import { SymbolInput } from "../components/SymbolInput";
import { RecommendedStocks } from "../components/RecommendedStocks";
import { MarketDataHealth } from "../components/MarketDataHealth";
import { TabGuide } from "../components/TabGuide";
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
      <div className="empty" style={{ padding: "var(--s-6)" }}>
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
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(90px, 1fr))", gap: "var(--s-2)", marginBottom: "var(--s-2-5)" }}>
        <Tile label="Last close" value={last.Close == null ? DASH : fmtNum(last.Close, 2)} />
        <Tile label="Bars" value={String(bars.length)} />
        <Tile label="From" value={bars[0].date} />
      </div>
      {curve.length > 0 ? (
        <PerfLine data={curve} valueLabel="Close" yTickDecimals={0} />
      ) : (
        <div className="empty" style={{ padding: "var(--s-4)" }}>No priced closes to chart.</div>
      )}
    </>
  );
}

function FundamentalsTable({ f }: { f: Fundamentals }) {
  const entries = Object.entries(f);
  if (entries.length === 0) {
    return <div className="empty" style={{ padding: "var(--s-4)" }}>No fundamentals available.</div>;
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <Table>
        <tbody>
          {entries.map(([k, v]) => (
            <tr key={k}>
              <td style={{ color: theme.textSecondary }}>{label(k)}</td>
              <td className="num" style={{ color: v == null ? theme.textMuted : theme.textPrimary }}>
                {fmtValue(v)}
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
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
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
      <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)` }}>
        <h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>Macro snapshot</h2>
      </div>
      <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
      {loading && <Loading lines={1} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: "var(--s-2-5)" }}>
          {known.map(([key, lbl]) =>
            key in data ? (
              <Tile key={key} label={lbl} value={fmtValue(data[key])} />
            ) : null
          )}
        </div>
      )}
      </div>
    </section>
  );
}

export function DataExplorer() {
  const nav = useNavigate();
  const [symbol, setSymbol] = useState("AAPL");
  const bars = useApi<Bar[]>(() => api.getDataBars(symbol, 120), [symbol]);
  const fundamentals = useApi<Fundamentals>(() => api.getDataFundamentals(symbol), [symbol]);
  useAutoPoll(
    () => {
      bars.reload();
      fundamentals.reload();
    },
    "options",
    { hasError: bars.error != null }
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
          <h1 className="screen-title" style={{ marginTop: "var(--s-2)" }}>Data explorer</h1>
          <p className="screen-sub">
            See the platform's recommended stocks and browse the raw data layer for
            a symbol — daily bars, current fundamentals, and the macro snapshot.{" "}
            Manage which stocks are tracked in <Link to="/settings">Settings</Link>.
          </p>
        </div>
      </div>

      <TabGuide tabKey="data-explorer" />

      <SymbolInput initial={symbol} onSubmit={setSymbol} pending={bars.loading} />

      <div style={{ flex: 1, minHeight: 0 }}>
        <div className="dashboard-layout" style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
          <div key="recommended">
            <RecommendedStocks onSelect={setSymbol} />
          </div>

          <div key="bars">
            <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
              <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)` }}>
                <h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>Price bars · {symbol}</h2>
              </div>
              <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
                {bars.loading && <Loading lines={2} />}
                {!bars.loading && bars.error && (
                  <ErrorState message={bars.error} status={bars.status} onRetry={bars.reload} />
                )}
                {!bars.loading && !bars.error && bars.data && <BarsChart bars={bars.data} />}
              </div>
            </section>
          </div>

          <div key="fundamentals">
            <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
              <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)` }}>
                <h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>Fundamentals · {symbol}</h2>
              </div>
              <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
                {fundamentals.loading && <Loading lines={2} />}
                {!fundamentals.loading && fundamentals.error && (
                  <ErrorState message={fundamentals.error} status={fundamentals.status} onRetry={fundamentals.reload} />
                )}
                {!fundamentals.loading && !fundamentals.error && fundamentals.data && (
                  <FundamentalsTable f={fundamentals.data} />
                )}
              </div>
            </section>
          </div>

          <div key="macro">
            <MacroSection />
          </div>

          <div key="health">
            <MarketDataHealth />
          </div>
        </div>
      </div>
    </div>
  );
}
