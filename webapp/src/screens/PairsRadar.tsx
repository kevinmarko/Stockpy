import { useState } from "react";
import { useNavigate } from "react-router-dom";
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
import { useMutation } from "../hooks/useMutation";
import { Button, ErrorState, Input, Loading } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
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
    <section className="card card-pad" style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ fontWeight: 700, fontSize: 16 }}>
          {p.ticker1} <span style={{ color: theme.textMuted }}>/</span> {p.ticker2}
        </div>
        <span
          className="badge"
          style={{ background: "transparent", color: signalColor(p.signal), fontWeight: 700 }}
        >
          {p.signal}
        </span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, marginTop: 12 }}>
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
      <div style={{ fontSize: 11, color: theme.textMuted }}>{label}</div>
      <div className="num" style={{ fontSize: 15, fontWeight: 700 }}>{value}</div>
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
  const mutation = useMutation((y: string, x: string) =>
    api.analyzePairs({ symbol_y: y, symbol_x: x })
  );
  const result: PairsAnalyzeResult | null = mutation.result ?? null;

  const canSubmit =
    symY.trim().length > 0 &&
    symX.trim().length > 0 &&
    symY.trim().toUpperCase() !== symX.trim().toUpperCase();

  return (
    <section className="card card-pad" style={{ marginBottom: 16 }}>
      <h2 style={{ fontSize: 15, margin: "0 0 4px" }}>Analyze a pair</h2>
      <p style={{ color: theme.textMuted, fontSize: 12.5, marginTop: 0, marginBottom: 12 }}>
        Cointegration test + current spread state for two tickers you pick — computed
        live, not from the pipeline's last run. Advisory only.
      </p>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 120px" }}>
          <Input
            label="Symbol Y (dependent)"
            value={symY}
            onChange={(e) => setSymY(e.target.value.toUpperCase())}
          />
        </div>
        <div style={{ flex: "1 1 120px" }}>
          <Input
            label="Symbol X (hedge)"
            value={symX}
            onChange={(e) => setSymX(e.target.value.toUpperCase())}
          />
        </div>
      </div>
      <div style={{ marginTop: 12 }}>
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
        <div className="notice notice-warn" style={{ marginTop: 12 }}>
          <span>{mutation.error}</span>
        </div>
      )}

      {result && !result.found && (
        <div className="empty" style={{ padding: 18, marginTop: 12 }}>
          {result.reason ?? "No result for this pair."}
        </div>
      )}

      {result && result.found && (
        <div style={{ marginTop: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <div style={{ fontWeight: 700, fontSize: 16 }}>
              {result.ticker1} <span style={{ color: theme.textMuted }}>/</span> {result.ticker2}
            </div>
            <span
              className="badge"
              style={{ background: "transparent", color: signalColor(result.signal), fontWeight: 700 }}
            >
              {result.signal}
            </span>
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: 16, marginTop: 12 }}>
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
            <div className="notice notice-warn" style={{ marginTop: 12 }}>
              <span>
                Half-life is outside the tradeable 5–60 day band — treat this pair as not
                currently actionable even though a signal is shown above.
              </span>
            </div>
          )}

          {result.z_score_series.length > 1 && (
            <div style={{ marginTop: 16, height: 200 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={result.z_score_series}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="date" hide />
                  <YAxis stroke="rgba(255,255,255,0.4)" style={{ fontSize: 10 }} />
                  <ChartTooltip
                    formatter={(value: number) => [fmtNum(value, 2), "z-score"]}
                    labelFormatter={(label: string) => label}
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
              <p style={{ fontSize: 11, color: theme.textMuted, marginTop: 4 }}>
                Spread z-score over time. Dashed lines mark the ±2 entry band.
              </p>
            </div>
          )}

          <p style={{ fontSize: 11.5, color: theme.textMuted, marginTop: 12 }}>
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
  const mutation = useMutation((symbols: string[]) => api.scanPairs({ symbols }));
  const result = mutation.result ?? null;

  const parsed = symbolsText
    .split(/[,\s]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
  const uniqueCount = new Set(parsed).size;
  const canSubmit = uniqueCount >= 2 && uniqueCount <= 15;

  return (
    <section className="card card-pad" style={{ marginBottom: 16 }}>
      <h2 style={{ fontSize: 15, margin: "0 0 4px" }}>Scan for pairs</h2>
      <p style={{ color: theme.textMuted, fontSize: 12.5, marginTop: 0, marginBottom: 12 }}>
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
      <div style={{ marginTop: 12 }}>
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
        <div className="notice notice-warn" style={{ marginTop: 12 }}>
          <span>{mutation.error}</span>
        </div>
      )}

      {result && (
        <div style={{ marginTop: 16 }}>
          {result.missing.length > 0 && (
            <p style={{ fontSize: 12, color: theme.textMuted, marginBottom: 10 }}>
              No data for: {result.missing.join(", ")} (skipped).
            </p>
          )}
          {result.pairs.length === 0 ? (
            <div className="empty" style={{ padding: 18 }}>
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
  const [showRecompute, setShowRecompute] = useState(false);
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  return (
    <div className="screen">
      <button
        onClick={back}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          color: theme.textSecondary,
          fontSize: 14,
          marginBottom: 8,
        }}
      >
        ← Pilots
      </button>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h1 className="screen-title">Pairs radar</h1>
        {data?.as_of && (
          <span style={{ fontSize: 12, color: theme.textMuted }}>{timeAgo(data.as_of)}</span>
        )}
      </div>
      <p className="screen-sub">
        Cointegrated stat-arb candidates and their current spread state. Advisory
        only — no orders are placed.
      </p>

      <TabGuide tabKey="pairs" />

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        data.pairs.length === 0 ? (
          <div className="empty" style={{ padding: 30 }}>
            {data.reason ?? "No cointegrated pairs found yet."}
          </div>
        ) : (
          <div style={{ marginTop: 12 }}>
            {data.pairs.map((p) => (
              <PairCard key={`${p.ticker1}-${p.ticker2}`} p={p} />
            ))}
          </div>
        )
      )}
      <p
        style={{
          color: theme.textMuted,
          fontSize: 11.5,
          marginTop: 20,
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
        style={{ marginTop: 20, width: "100%" }}
      >
        {showRecompute ? "▲ Hide" : "▼"} Recompute with custom symbols
      </button>

      {showRecompute && (
        <div style={{ marginTop: 16 }}>
          <PairAnalyzeSection />
          <PairScanSection />
        </div>
      )}
    </div>
  );
}
