import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { SentimentDynamics as SentimentDynamicsData } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, Tile } from "../components/ui";
import { SymbolInput } from "../components/SymbolInput";
import { fmtNum } from "../format";
import { theme } from "../theme";

function getScoreColor(score: number | null): string {
  if (score == null) return theme.textSecondary;
  if (score > 0.2) return theme.growth;
  if (score < -0.2) return theme.decline;
  return theme.textSecondary;
}

function Breakdown({ d }: { d: SentimentDynamicsData }) {
  return (
    <>
      {d.source === "unavailable" && (
        <div className="notice notice-info" style={{ marginBottom: 16 }}>
          <span>
            🔌 <strong>Antigravity agent unavailable for this request</strong> — the
            agent isn't configured (SDK/API key) or the live call failed. Sentiment
            Score / Intensity / Credibility below are honestly blank ("—") rather
            than guessed. Vol Persistence is unaffected — it's computed
            independently from price history via a real GJR-GARCH fit, not the
            agent.
          </span>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10, marginBottom: 16 }}>
        <Tile
          label="Sentiment Score"
          value={<span style={{ color: getScoreColor(d.sentiment_score) }}>{fmtNum(d.sentiment_score, 2)}</span>}
        />
        <Tile label="Sentiment Intensity" value={fmtNum(d.sentiment_intensity, 2)} />
        <Tile label="Credibility Score" value={fmtNum(d.credibility_score, 2)} />
        <Tile label="Vol Persistence" value={fmtNum(d.volatility_persistence, 2)} />
      </div>

      <section className="card card-pad">
        <h2 style={{ fontSize: 15, margin: "0 0 8px" }}>Interpretation</h2>
        <p style={{ color: theme.textSecondary, fontSize: 14, lineHeight: 1.5 }}>
          <strong>Score (-1 to 1):</strong> Positive means bullish news sentiment, negative means bearish.
          <br/>
          <strong>Intensity (0.1 to 1):</strong> High values mean extreme emotional language or high news volume.
          <br/>
          <strong>Credibility (0.1 to 1):</strong> Filter for 'rumor mill' spikes; low credibility means the sentiment is likely noise.
          <br/>
          <strong>Persistence:</strong> GJR-GARCH measure of how long volatility shocks endure.
        </p>
      </section>
    </>
  );
}

export function SentimentDynamics() {
  const nav = useNavigate();
  const [symbol, setSymbol] = useState("AAPL");
  const { data, loading, error, status, reload } = useApi<SentimentDynamicsData>(
    () => api.getSentimentDynamics(symbol),
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
      <h1 className="screen-title">Sentiment Dynamics</h1>
      <p className="screen-sub">
        Live sentiment analysis from financial news and social media activity,
        driven by the Antigravity Agent and GJR-GARCH asymmetric volatility metrics.
      </p>

      {/* TabGuide key doesn't really matter unless we define it, we can omit it or use an existing one, omitting is fine or just pass "sentiment" */}
      <SymbolInput initial={symbol} onSubmit={setSymbol} pending={loading} />

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && <Breakdown d={data} />}
    </div>
  );
}
