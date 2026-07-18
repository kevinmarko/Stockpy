import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { SignalBreakdown as SignalBreakdownData, SignalModuleScore } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, Tile } from "../components/ui";
import { SymbolInput } from "../components/SymbolInput";
import { fmtNum } from "../format";
import { theme } from "../theme";

const DASH = "—";

/** Signed magnitude bar for a module's contribution, centered on zero. */
function ContributionBar({ contribution, max }: { contribution: number | null; max: number }) {
  if (contribution == null || max <= 0) {
    return <span style={{ color: theme.textMuted }}>{DASH}</span>;
  }
  const pct = Math.min(100, (Math.abs(contribution) / max) * 100);
  const pos = contribution >= 0;
  return (
    <div
      style={{ position: "relative", height: 10, background: "var(--surface-2)", borderRadius: 5 }}
      aria-hidden
    >
      <div
        style={{
          position: "absolute",
          left: "50%",
          transform: pos ? "none" : "translateX(-100%)",
          width: `${pct / 2}%`,
          height: "100%",
          background: pos ? theme.growth : theme.decline,
          borderRadius: 5,
        }}
      />
    </div>
  );
}

function ModuleRow({ m, max }: { m: SignalModuleScore; max: number }) {
  const contribTone =
    m.contribution == null ? undefined : m.contribution >= 0 ? theme.growth : theme.decline;
  return (
    <tr style={{ borderTop: "1px solid var(--border)" }}>
      <td style={{ padding: "8px 6px", fontFamily: "monospace", fontSize: 13 }}>{m.name}</td>
      <td style={{ padding: "8px 6px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
        {m.score == null ? DASH : fmtNum(m.score, 2)}
      </td>
      <td style={{ padding: "8px 6px", textAlign: "right", fontVariantNumeric: "tabular-nums", color: theme.textMuted }}>
        {fmtNum(m.weight, 0)}
      </td>
      <td style={{ padding: "8px 6px", textAlign: "right", fontVariantNumeric: "tabular-nums", color: contribTone, fontWeight: 600 }}>
        {m.contribution == null ? DASH : fmtNum(m.contribution, 2)}
      </td>
      <td style={{ padding: "8px 6px", width: "28%", minWidth: 90 }}>
        <ContributionBar contribution={m.contribution} max={max} />
      </td>
    </tr>
  );
}

function actionColor(action: string | null): string {
  if (action === "BUY") return theme.growth;
  if (action === "SELL") return theme.decline;
  return theme.textSecondary;
}

function Breakdown({ d }: { d: SignalBreakdownData }) {
  // Sort by contribution magnitude desc so the biggest drivers read first
  // (visual hierarchy); null contributions sink to the bottom, never fabricated.
  const modules = [...d.modules].sort(
    (a, b) => Math.abs(b.contribution ?? 0) - Math.abs(a.contribution ?? 0)
  );
  const max = Math.max(0, ...modules.map((m) => Math.abs(m.contribution ?? 0)));

  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 10, marginBottom: 16 }}>
        <Tile
          label="Action"
          value={<span style={{ color: actionColor(d.action) }}>{d.action ?? DASH}</span>}
        />
        <Tile label="Conviction" value={d.conviction == null ? DASH : fmtNum(d.conviction, 2)} />
        <Tile label="Blended score" value={d.final_score == null ? DASH : fmtNum(d.final_score, 0)} />
      </div>

      {modules.length === 0 ? (
        <div className="empty" style={{ padding: 24 }}>
          No signal modules ran for {d.symbol} yet — this symbol has no bars in the
          store. Run the pipeline, then reload.
        </div>
      ) : (
        <section className="card card-pad">
          <h2 style={{ fontSize: 15, margin: "0 0 8px" }}>Module contributions</h2>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ color: theme.textMuted, textAlign: "left" }}>
                  <th style={{ padding: "0 6px 6px" }}>Module</th>
                  <th style={{ padding: "0 6px 6px", textAlign: "right" }}>Score</th>
                  <th style={{ padding: "0 6px 6px", textAlign: "right" }}>Weight</th>
                  <th style={{ padding: "0 6px 6px", textAlign: "right" }}>Contribution</th>
                  <th style={{ padding: "0 6px 6px" }} aria-label="magnitude" />
                </tr>
              </thead>
              <tbody>
                {modules.map((m) => (
                  <ModuleRow key={m.name} m={m} max={max} />
                ))}
              </tbody>
            </table>
          </div>
          <p style={{ color: theme.textMuted, fontSize: 11.5, marginTop: 12, lineHeight: 1.5 }}>
            Contribution = score × weight. A module with no score this cycle shows {DASH},
            never a fabricated 0.
          </p>
        </section>
      )}
    </>
  );
}

export function SignalBreakdown() {
  const nav = useNavigate();
  const [symbol, setSymbol] = useState("AAPL");
  const { data, loading, error, status, reload } = useApi<SignalBreakdownData>(
    () => api.getSignalBreakdown(symbol),
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
      <h1 className="screen-title">Signal breakdown</h1>
      <p className="screen-sub">
        Per-module contributions to a symbol's blended signal — which signals are
        driving the call, and by how much. The action and conviction come from the
        advisory engine; the module split from the signal aggregator.
      </p>

      <SymbolInput initial={symbol} onSubmit={setSymbol} pending={loading} />

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && <Breakdown d={data} />}
    </div>
  );
}
