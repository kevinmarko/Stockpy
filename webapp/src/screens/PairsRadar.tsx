import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { PairRow, PairsRadar as PairsRadarT } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading } from "../components/ui";
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

export function PairsRadar() {
  const nav = useNavigate();
  const { data, loading, error, status, reload } = useApi<PairsRadarT>(
    () => api.getPairs(),
    []
  );
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
    </div>
  );
}
