import { useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type {
  SignalBreakdown as SignalBreakdownData,
  SignalImportance,
  SignalModuleScore,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, Table, Tile } from "../components/ui";
import { SymbolInput } from "../components/SymbolInput";
import { TabGuide } from "../components/TabGuide";
import { fmtNum } from "../format";
import { pnlColor, theme } from "../theme";

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
          background: pnlColor(contribution),
          borderRadius: 5,
        }}
      />
    </div>
  );
}

function ModuleRow({ m, max }: { m: SignalModuleScore; max: number }) {
  const contribTone = m.contribution == null ? undefined : pnlColor(m.contribution);
  return (
    <tr>
      <td style={{ fontFamily: "monospace", fontSize: 13 }}>{m.name}</td>
      <td className="num">
        {m.score == null ? DASH : fmtNum(m.score, 2)}
      </td>
      <td className="num" style={{ color: theme.textMuted }}>
        {fmtNum(m.weight, 0)}
      </td>
      <td className="num" style={{ color: contribTone, fontWeight: 600 }}>
        {m.contribution == null ? DASH : fmtNum(m.contribution, 2)}
      </td>
      <td style={{ width: "28%", minWidth: 90 }}>
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
            <Table>
              <thead>
                <tr>
                  <th>Module</th>
                  <th className="num">Score</th>
                  <th className="num">Weight</th>
                  <th className="num">Contribution</th>
                  <th aria-label="magnitude" />
                </tr>
              </thead>
              <tbody>
                {modules.map((m) => (
                  <ModuleRow key={m.name} m={m} max={max} />
                ))}
              </tbody>
            </Table>
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

/**
 * GlobalImportancePanel — "Signal driver weights (universe-wide)": mean
 * |contribution| per module across the tracked universe.
 *
 * Deliberately NOT labeled SHAP or "feature importance" anywhere in this
 * component. It's a linear, configured-weight decomposition (see the
 * "signal driver weight" glossary entry) — the same honesty distinction
 * api/metrics_api.py::_signal_importance's docstring makes server-side.
 * A module with n_symbols_scored === 0 renders "—", never a 0 bar (a 0 bar
 * would misread as "measured and found unimportant" rather than "no data
 * this batch").
 */
function GlobalImportancePanel() {
  const { data, loading, error, status, reload } = useApi<SignalImportance>(async () => {
    const universe = await api.getUniverse();
    const symbols = universe.symbols.map((s) => s.symbol);
    if (symbols.length === 0) {
      return { rows: [], n_symbols_requested: 0, n_symbols_scored: 0 };
    }
    return api.getSignalImportance(symbols);
  }, []);

  const maxAbs = Math.max(0, ...(data?.rows.map((r) => r.mean_abs_contribution ?? 0) ?? []));

  return (
    <section className="card card-pad" style={{ marginTop: 16 }} data-testid="global-importance-panel">
      <h2 style={{ fontSize: 15, margin: "0 0 4px" }}>Signal driver weights (universe-wide)</h2>
      <p style={{ color: theme.textMuted, fontSize: 12, margin: "0 0 10px", lineHeight: 1.5 }}>
        Mean absolute contribution per module, averaged across every symbol currently
        tracked. This is a configured-weight breakdown (score × weight), not a
        feature-importance or SHAP measure — it shows no interaction effects between
        modules.
      </p>
      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && data.rows.length === 0 && (
        <div className="empty" style={{ padding: 20 }}>
          No tracked symbols yet — run the pipeline, then reload.
        </div>
      )}
      {!loading && !error && data && data.rows.length > 0 && (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {data.rows.map((r) => (
              <div key={r.name} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span
                  style={{
                    width: 150,
                    flex: "0 0 auto",
                    fontFamily: "monospace",
                    fontSize: 12,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {r.name}
                </span>
                <div
                  style={{ flex: 1, position: "relative", height: 10, background: "var(--surface-2)", borderRadius: 5 }}
                  aria-hidden
                >
                  {r.mean_abs_contribution != null && maxAbs > 0 && (
                    <div
                      style={{
                        position: "absolute",
                        left: 0,
                        top: 0,
                        height: "100%",
                        width: `${Math.min(100, (r.mean_abs_contribution / maxAbs) * 100)}%`,
                        background: theme.accent,
                        borderRadius: 5,
                      }}
                    />
                  )}
                </div>
                <span
                  className="num"
                  style={{ width: 60, flex: "0 0 auto", textAlign: "right", fontSize: 12, fontWeight: 600 }}
                >
                  {r.mean_abs_contribution == null ? DASH : fmtNum(r.mean_abs_contribution, 2)}
                </span>
              </div>
            ))}
          </div>
          <p style={{ color: theme.textMuted, fontSize: 11.5, marginTop: 12, lineHeight: 1.5 }}>
            Based on {data.n_symbols_scored} of {data.n_symbols_requested} tracked symbols with a
            score this cycle. A module with no data this batch shows {DASH}, never a fabricated 0.
          </p>
        </>
      )}
    </section>
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

      <TabGuide tabKey="signals" />

      <SymbolInput initial={symbol} onSubmit={setSymbol} pending={loading} />

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && <Breakdown d={data} />}

      <GlobalImportancePanel />
    </div>
  );
}
