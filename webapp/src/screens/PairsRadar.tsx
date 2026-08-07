import { useState } from "react";
import { useNavigate } from "react-router";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import type {
  PairRow,
  PairsAnalyzeResult,
  PairsRadar as PairsRadarT,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { useMutation } from "../hooks/useMutation";
import { Button, ErrorState, Input, Loading, Notice } from "../components/ui";
import { SymbolInput } from "../components/SymbolInput";
import { TabGuide } from "../components/TabGuide";
import { DynamicGrid, resetGridLayout } from "../components/DynamicGrid";
import { chartAxisLine, chartAxisTick, chartGridProps } from "../components/charts";
import { fmtNum, timeAgo } from "../format";
import { theme } from "../theme";

/** Color a signal label: entry green/red, stop amber, flat/none muted. */
function signalColor(signal: string): string {
  if (signal.startsWith("STOP")) return theme.caution;
  if (signal.startsWith("ENTER LONG") || signal.startsWith("Hold LONG")) return theme.growth;
  if (signal.startsWith("ENTER SHORT") || signal.startsWith("Hold SHORT")) return theme.decline;
  return theme.textMuted;
}

function PairCard({ p }: { p: PairRow }) {
  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
      <div className="drag-handle" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "grab", borderBottom: `1px solid ${theme.border}`, padding: "var(--s-3)" }}>
        <div style={{ fontWeight: 700, fontSize: "var(--t-input)" }}>
          {p.ticker1} <span style={{ color: theme.textMuted }}>/</span> {p.ticker2}
        </div>
        <span
          className="badge"
          style={{ background: "transparent", color: signalColor(p.signal), fontWeight: 700 }}
        >
          {p.signal}
        </span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-4)", padding: "var(--s-3)", flex: 1, overflow: "auto", alignContent: "flex-start" }}>
        <Metric label="z-score" value={fmtNum(p.z_score, 2)} />
        <Metric label="Half-life" value={p.half_life == null ? "—" : `${fmtNum(p.half_life, 0)}d`} />
        <Metric label="p-value" value={fmtNum(p.p_value, 4)} />
        <Metric label="Hedge β" value={fmtNum(p.beta, 3)} />
        <Metric label="ADF p" value={fmtNum(p.rolling_p, 3)} />
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted }}>{label}</div>
      <div className="num" style={{ fontSize: "var(--t-subhead)", fontWeight: 700 }}>{value}</div>
    </div>
  );
}

/**
 * "Analyze a pair" — the on-demand wedge for backlog item 8a. Ports
 * gui/panels/pairs.py's "Analyze a pair" mode. The persisted GET /pairs view
 * above stays the default; this is an explicit, operator-triggered action for
 * a pair the operator names (not necessarily one the pipeline already
 * ranked).
 */
function PairAnalyzeSection() {
  const [symY, setSymY] = useState("");
  const [symX, setSymX] = useState("");
  const mutation = useMutation(
    (y: string, x: string) => api.analyzePairs({ symbol_y: y, symbol_x: x }),
    { successMessage: "Pair analysis complete" }
  );
  const result: PairsAnalyzeResult | null = mutation.result ?? null;

  const canSubmit =
    symY.trim().length > 0 &&
    symX.trim().length > 0 &&
    symY.trim().toUpperCase() !== symX.trim().toUpperCase();

  return (
    <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }}>
      <h2 style={{ fontSize: "var(--t-subhead)", margin: "0 0 var(--s-1)" }}>Analyze a pair</h2>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-label)", marginTop: 0, marginBottom: "var(--s-3)" }}>
        Cointegration test + current spread state for two tickers you pick — computed
        live, not from the pipeline's last run. Advisory only.
      </p>
      <div style={{ display: "flex", gap: "var(--s-2-5)", flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 120px" }}>
          <SymbolInput
            label="Symbol Y (dependent)"
            initial={symY}
            onChange={(sym) => setSymY(sym.toUpperCase())}
            onSubmit={(sym) => setSymY(sym.toUpperCase())}
            hideButton
            testId="symbol-input-y"
          />
        </div>
        <div style={{ flex: "1 1 120px" }}>
          <SymbolInput
            label="Symbol X (hedge)"
            initial={symX}
            onChange={(sym) => setSymX(sym.toUpperCase())}
            onSubmit={(sym) => setSymX(sym.toUpperCase())}
            hideButton
            testId="symbol-input-x"
          />
        </div>
      </div>
      <div style={{ marginTop: "var(--s-3)" }}>
        <Button
          variant="primary"
          pending={mutation.pending}
          disabled={!canSubmit}
          onClick={() => mutation.run(symY.trim(), symX.trim())}
        >
          Analyze
        </Button>
      </div>

      {mutation.error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-3)" }}>
          <span>{mutation.error}</span>
        </Notice>
      )}

      {result && !result.found && (
        <div className="empty" style={{ padding: "var(--s-4-5)", marginTop: "var(--s-3)" }}>
          {result.reason ?? "No result for this pair."}
        </div>
      )}

      {result && result.found && (
        <div style={{ marginTop: "var(--s-4)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <div style={{ fontWeight: 700, fontSize: "var(--t-input)" }}>
              {result.ticker1} <span style={{ color: theme.textMuted }}>/</span> {result.ticker2}
            </div>
            <span
              className="badge"
              style={{ background: "transparent", color: signalColor(result.signal), fontWeight: 700 }}
            >
              {result.signal}
            </span>
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-4)", marginTop: "var(--s-3)" }}>
            <Metric label="z-score" value={fmtNum(result.z_score, 2)} />
            <Metric
              label="Half-life"
              value={result.half_life == null ? "—" : `${fmtNum(result.half_life, 0)}d`}
            />
            <Metric label="p-value" value={fmtNum(result.p_value, 4)} />
            <Metric label="Hedge β" value={fmtNum(result.beta, 3)} />
            <Metric label="ADF p" value={fmtNum(result.rolling_p, 3)} />
          </div>

          {result.half_life_tradeable === false && (
            <Notice variant="warn" style={{ marginTop: "var(--s-3)" }}>
              <span>
                Half-life is outside the tradeable 5–60 day band — treat this pair as not
                currently actionable even though a signal is shown above.
              </span>
            </Notice>
          )}

          {result.z_score_series.length > 1 && (
            <div style={{ marginTop: "var(--s-4)", height: 200 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={result.z_score_series}>
                  <CartesianGrid {...chartGridProps} />
                  <XAxis dataKey="date" hide />
                  <YAxis tick={chartAxisTick} {...chartAxisLine} />
                  <ChartTooltip
                    formatter={(value) => [fmtNum(Number(value), 2), "z-score"]}
                    labelFormatter={(label) => label}
                  />
                  <ReferenceLine y={2} stroke={theme.caution} strokeDasharray="3 3" />
                  <ReferenceLine y={-2} stroke={theme.caution} strokeDasharray="3 3" />
                  <ReferenceLine y={0} stroke="rgba(255,255,255,0.3)" />
                  <Line
                    type="monotone"
                    dataKey="z_score"
                    stroke={theme.accent}
                    strokeWidth={2}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
              <p style={{ fontSize: "var(--t-micro)", color: theme.textMuted, marginTop: "var(--s-1)" }}>
                Spread z-score over time. Dashed lines mark the ±2 entry band.
              </p>
            </div>
          )}

          <p style={{ fontSize: "var(--t-footnote)", color: theme.textMuted, marginTop: "var(--s-3)" }}>
            This is a displayed signal, not an order — the platform never trades pairs
            automatically.
          </p>
        </div>
      )}
    </section>
  );
}

/**
 * "Scan for pairs" — backlog item 8a's follow-on (full scan mode). Ports
 * gui/panels/pairs.py's "Scan for pairs" mode over an operator-chosen symbol
 * list (2-15 tickers; the server 422s outside that range).
 */
function PairScanSection() {
  const [symbolsText, setSymbolsText] = useState("");
  const mutation = useMutation(
    (symbols: string[]) => api.scanPairs({ symbols }),
    { successMessage: "Pairs scan complete" }
  );
  const result = mutation.result ?? null;

  const parsed = symbolsText
    .split(/[,\s]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
  const uniqueCount = new Set(parsed).size;
  const canSubmit = uniqueCount >= 2 && uniqueCount <= 15;

  return (
    <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }}>
      <h2 style={{ fontSize: "var(--t-subhead)", margin: "0 0 var(--s-1)" }}>Scan for pairs</h2>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-label)", marginTop: 0, marginBottom: "var(--s-3)" }}>
        Cointegration scan over a symbol list you pick (2–15 tickers) — computed live.
        Advisory only.
      </p>
      <Input
        label="Symbols (comma or space separated)"
        value={symbolsText}
        onChange={(e) => setSymbolsText(e.target.value)}
        hint={`${uniqueCount} distinct symbol${uniqueCount === 1 ? "" : "s"} entered (need 2–15).`}
        invalid={uniqueCount > 0 && !canSubmit}
      />
      <div style={{ marginTop: "var(--s-3)" }}>
        <Button
          variant="primary"
          pending={mutation.pending}
          disabled={!canSubmit}
          onClick={() => mutation.run(parsed)}
        >
          Scan
        </Button>
      </div>

      {mutation.error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-3)" }}>
          <span>{mutation.error}</span>
        </Notice>
      )}

      {result && (
        <div style={{ marginTop: "var(--s-4)" }}>
          {result.missing.length > 0 && (
            <p style={{ fontSize: "var(--t-caption)", color: theme.textMuted, marginBottom: "var(--s-2-5)" }}>
              No data for: {result.missing.join(", ")} (skipped).
            </p>
          )}
          {result.pairs.length === 0 ? (
            <div className="empty" style={{ padding: "var(--s-4-5)" }}>
              {result.reason ?? "No cointegrated pairs found."}
            </div>
          ) : (
            result.pairs.map((p) => <PairCard key={`${p.ticker1}-${p.ticker2}`} p={p} />)
          )}
        </div>
      )}
    </section>
  );
}

export function PairsRadar() {
  const nav = useNavigate();
  const { data, loading, error, status, reload } = useApi<PairsRadarT>(
    () => api.getPairs(),
    []
  );
  useAutoPoll(reload, "options", { hasError: error != null });
  const [showRecompute, setShowRecompute] = useState(false);
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  return (
    <div className="screen" style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--s-3)" }}>
        <div>
          <button
            onClick={back}
            style={{
              background: "none",
              border: "none",
              padding: 0,
              cursor: "pointer",
              color: theme.textSecondary,
              fontSize: "var(--t-callout)",
              marginBottom: "var(--s-2)",
            }}
          >
            ← Pilots
          </button>
          <div style={{ display: "flex", alignItems: "baseline", gap: "var(--s-2)" }}>
            <h1 className="screen-title" style={{ margin: 0 }}>Pairs radar</h1>
            {data?.as_of && (
              <span style={{ fontSize: "var(--t-caption)", color: theme.textMuted }}>{timeAgo(data.as_of)}</span>
            )}
          </div>
          <p className="screen-sub" style={{ marginTop: "var(--s-1)", marginBottom: 0 }}>
            Cointegrated stat-arb candidates and their current spread state. Advisory
            only — no orders are placed.
          </p>
        </div>
        <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-4)" }}>
          <Button variant="neutral" onClick={() => resetGridLayout("pairs-radar")}>Reset Layout</Button>
        </div>
      </div>

      <TabGuide tabKey="pairs" />

      {loading && !data && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      
      <div style={{ flex: 1, minHeight: 0, marginTop: "var(--s-4)", display: "flex", flexDirection: "column" }}>
        <div style={{ flex: 1, minHeight: 0 }}>
          {!loading && !error && data && (
            data.pairs.length === 0 ? (
              <div className="empty" style={{ padding: "var(--s-7-5)" }}>
                {data.reason ?? "No cointegrated pairs found yet."}
              </div>
            ) : (
              <DynamicGrid
                layoutKey="pairs-radar"
                defaultLayouts={{
                  lg: data.pairs.map((p, i) => ({
                    i: `${p.ticker1}-${p.ticker2}`,
                    x: (i % 3) * 4,
                    y: Math.floor(i / 3) * 3,
                    w: 4,
                    h: 3,
                    minW: 3,
                    minH: 3,
                  })),
                }}
              >
                {data.pairs.map((p) => (
                  <div key={`${p.ticker1}-${p.ticker2}`}>
                    <PairCard p={p} />
                  </div>
                ))}
              </DynamicGrid>
            )
          )}
        </div>

        <div style={{ flexShrink: 0, paddingBottom: "var(--s-4)" }}>
          <p
            style={{
              color: theme.textMuted,
              fontSize: "var(--t-footnote)",
              marginTop: "var(--s-5)",
              textAlign: "center",
              lineHeight: 1.5,
            }}
          >
            Entry at |z| &gt; 2, exit on a 0-cross, stop at |z| &gt; 4. Cointegration
            breaks when the rolling ADF p-value exceeds 0.10.
          </p>

          <button
            type="button"
            onClick={() => setShowRecompute((v) => !v)}
            aria-expanded={showRecompute}
            className="btn btn-neutral"
            style={{ marginTop: "var(--s-3)", width: "100%" }}
          >
            {showRecompute ? "▲ Hide" : "▼"} Recompute with custom symbols
          </button>

          {showRecompute && (
            <div style={{ marginTop: "var(--s-4)" }}>
              <PairAnalyzeSection />
              <PairScanSection />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
