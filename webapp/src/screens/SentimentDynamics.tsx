import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import type {
  EarningsCatalystStatus,
  HeadlineSentimentItem,
  MacroHistorySeries,
  SentimentDynamics as SentimentDynamicsData,
  SentimentHistory,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { ErrorState, Loading, Notice, Tile } from "../components/ui";
import { chartAxisLine, chartAxisTick, chartGridProps, chartTooltipStyle } from "../components/charts";
import { SymbolInput } from "../components/SymbolInput";
import { TabGuide } from "../components/TabGuide";
import { DynamicGrid, resetGridLayout } from "../components/DynamicGrid";
import { fmtDate, fmtNum } from "../format";
import { seriesColor, theme } from "../theme";

function getScoreColor(score: number | null): string {
  if (score == null) return theme.textSecondary;
  if (score > 0.2) return theme.growth;
  if (score < -0.2) return theme.decline;
  return theme.textSecondary;
}

function EarningsCatalystBanner({ catalyst }: { catalyst?: EarningsCatalystStatus | null }) {
  if (!catalyst) return null;

  const { status, multiplier, next_earnings_date, hours_to_earnings } = catalyst;

  let bannerVariant: "info" | "warn" = "info";
  let title = "✅ Earnings Catalyst Clear";
  let description = `Full news signal multiplier (1.0x). Next earnings date: ${
    next_earnings_date ? fmtDate(next_earnings_date) : "no earnings date currently scheduled"
  }.`;

  if (status === "suppressed") {
    bannerVariant = "warn";
    title = "🚨 Earnings Proximity Suppressed (Within 48h)";
    description = `News catalyst signal is forced to 0.0x multiplier due to extreme event uncertainty before earnings on ${
      next_earnings_date ? fmtDate(next_earnings_date) : "upcoming date"
    } (${hours_to_earnings != null ? `${hours_to_earnings.toFixed(1)}h away` : "imminent"}).`;
  } else if (status === "dampened") {
    bannerVariant = "warn";
    title = `⚠️ Earnings Proximity Dampened (7-Day Pre or 24h Post Earnings)`;
    description = `News catalyst signal multiplier reduced to ${multiplier}x (${(multiplier * 100).toFixed(
      0
    )}%) due to elevated carry risk near earnings (${
      next_earnings_date ? fmtDate(next_earnings_date) : "event window"
    }).`;
  }

  return (
    <Notice variant={bannerVariant} style={{ marginBottom: "var(--s-4)" }} data-testid="earnings-catalyst-banner">
      <div>
        <strong>{title}</strong> — {description}
      </div>
    </Notice>
  );
}

function HeadlineFeed({
  headlines,
  providerUsed,
}: {
  headlines?: HeadlineSentimentItem[];
  providerUsed?: "fmp" | "finnhub" | "none";
}) {
  if (!headlines || headlines.length === 0) {
    if (providerUsed === "none") {
      return (
        <Notice variant="info" style={{ marginTop: "var(--s-4)" }}>
          <span>News provider not configured — see Settings.</span>
        </Notice>
      );
    }
    return (
      <Notice variant="info" style={{ marginTop: "var(--s-4)" }}>
        <span>No recent headlines available.</span>
      </Notice>
    );
  }

  return (
    <section className="card card-pad" style={{ marginTop: "var(--s-4)", padding: 0 }} data-testid="headline-feed-section">
      <div
        className="drag-handle"
        style={{
          padding: "var(--s-3)",
          borderBottom: `1px solid rgba(255, 255, 255, 0.08)`,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "var(--s-2)",
        }}
      >
        <div>
          <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Scored News Headlines & FinBERT Breakdown</h2>
          <p style={{ color: theme.textSecondary, fontSize: "var(--t-micro)", margin: "4px 0 0 0" }}>
            FinBERT 3-class softmax probability distribution & signed score per headline
          </p>
        </div>

        {providerUsed && providerUsed !== "none" && (
          <div style={{ display: "flex", gap: "var(--s-1-5)", alignItems: "center", flexWrap: "wrap" }}>
              <span
                style={{
                  fontSize: "var(--t-micro)",
                  padding: "2px 8px",
                  borderRadius: "var(--r-pill)",
                  background: "rgba(255, 255, 255, 0.06)",
                  border: `1px solid ${theme.border}`,
                  color: theme.textSecondary,
                  textTransform: "uppercase",
                  fontWeight: 600,
                }}
              >
                via {providerUsed}
              </span>
          </div>
        )}
      </div>

      <div style={{ padding: "var(--s-3)", display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
        {headlines.map((item, idx) => {
          const probs = item.probabilities;
          const posPct = probs ? Math.round(probs.positive * 100) : 0;
          const neuPct = probs ? Math.round(probs.neutral * 100) : 0;
          const negPct = probs ? Math.round(probs.negative * 100) : 0;

          return (
            <div
              key={idx}
              style={{
                padding: "var(--s-3)",
                borderRadius: "var(--r-sm)",
                background: "rgba(255, 255, 255, 0.03)",
                border: `1px solid rgba(255, 255, 255, 0.08)`,
                display: "flex",
                flexDirection: "column",
                gap: "var(--s-2)",
              }}
              data-testid={`headline-item-${idx}`}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--s-2)" }}>
                <div style={{ flex: 1 }}>
                  <span
                    style={{
                      fontSize: "var(--t-micro)",
                      padding: "2px 6px",
                      borderRadius: "4px",
                      background: "rgba(255, 255, 255, 0.08)",
                      color: theme.accent,
                      fontWeight: 700,
                      marginRight: "var(--s-2)",
                      textTransform: "uppercase",
                    }}
                  >
                    {item.publisher}
                  </span>
                  {item.url ? (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: theme.textPrimary, textDecoration: "none", fontWeight: 500, fontSize: "var(--t-body)" }}
                    >
                      {item.title} ↗
                    </a>
                  ) : (
                    <span style={{ color: theme.textPrimary, fontWeight: 500, fontSize: "var(--t-body)" }}>{item.title}</span>
                  )}
                </div>
                <span
                  style={{
                    fontSize: "var(--t-caption)",
                    fontWeight: 700,
                    color: getScoreColor(item.score),
                    whiteSpace: "nowrap",
                  }}
                >
                  {item.score > 0 ? `+${fmtNum(item.score, 2)}` : fmtNum(item.score, 2)}
                </span>
              </div>

              {probs && (
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--t-micro)", color: theme.textMuted, marginBottom: "4px" }}>
                    <span>FinBERT Classification:</span>
                    <span>
                      <strong style={{ color: theme.growth }}>+{posPct}% Pos</strong> · {neuPct}% Neu ·{" "}
                      <strong style={{ color: theme.decline }}>-{negPct}% Neg</strong>
                    </span>
                  </div>
                  <div style={{ height: "6px", width: "100%", background: "rgba(255, 255, 255, 0.1)", borderRadius: "3px", overflow: "hidden", display: "flex" }}>
                    <div style={{ width: `${posPct}%`, background: theme.growth, height: "100%" }} />
                    <div style={{ width: `${neuPct}%`, background: "rgba(255, 255, 255, 0.3)", height: "100%" }} />
                    <div style={{ width: `${negPct}%`, background: theme.decline, height: "100%" }} />
                  </div>
                </div>
              )}

              {item.published_at && (
                <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted }}>
                  Published: {fmtDate(item.published_at)}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Breakdown({ d }: { d: SentimentDynamicsData }) {
  return (
    <>
      {d.source === "unavailable" && (
        <Notice variant="info" style={{ marginBottom: "var(--s-4)" }}>
          <span>
            🔌 <strong>Antigravity agent unavailable for this request</strong> — the
            agent isn't configured (SDK/API key) or the live call failed. Sentiment
            Score / Intensity / Credibility below are honestly blank ("—") rather
            than guessed. Vol Persistence is unaffected — it's computed
            independently from price history via a real GJR-GARCH fit, not the
            agent.
          </span>
        </Notice>
      )}

      <EarningsCatalystBanner catalyst={d.earnings_catalyst} />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: "var(--s-2-5)", marginBottom: "var(--s-4)" }}>
        <Tile
          label="Sentiment Score"
          value={<span style={{ color: getScoreColor(d.sentiment_score) }}>{fmtNum(d.sentiment_score, 2)}</span>}
        />
        <Tile label="Sentiment Intensity" value={fmtNum(d.sentiment_intensity, 2)} />
        <Tile label="Credibility Score" value={fmtNum(d.credibility_score, 2)} />
        <Tile label="Vol Persistence" value={fmtNum(d.volatility_persistence, 2)} />
      </div>

      <HeadlineFeed headlines={d.headlines} providerUsed={d.provider_used} />

      <section className="card card-pad" style={{ marginTop: "var(--s-4)", display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
        <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
          <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Interpretation</h2>
        </div>
        <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
          <p style={{ color: theme.textSecondary, fontSize: "var(--t-callout)", lineHeight: 1.5 }}>
            <strong>Score (-1 to 1):</strong> Positive means bullish news sentiment, negative means bearish.
            <br/>
            <strong>Intensity (0.1 to 1):</strong> High values mean extreme emotional language or high news volume.
            <br/>
            <strong>Credibility (0.1 to 1):</strong> Filter for 'rumor mill' spikes; low credibility means the sentiment is likely noise.
            <br/>
            <strong>Persistence:</strong> GJR-GARCH measure of how long volatility shocks endure.
          </p>
        </div>
      </section>
    </>
  );
}

const _MIN_ALIGNED_DAYS = 14;
const _SHARED_CHART_MARGIN = { top: 6, right: 10, left: -10, bottom: 0 } as const;
type AlignedPoint = { date: string; vix: number | null; sentiment: number | null };

function SentimentVixChart({ symbol }: { symbol: string }) {
  const vix = useApi<MacroHistorySeries>(() => api.getMacroHistory("VIXCLS", 180), []);
  const sentimentHist = useApi<SentimentHistory>(
    () => api.getSentimentHistory(symbol, 180),
    [symbol]
  );

  const { chartData, alignedDays } = useMemo(() => {
    if (!vix.data || !sentimentHist.data) return { chartData: [] as AlignedPoint[], alignedDays: 0 };
    const vixByDate = new Map(vix.data.points.map((p) => [p.date, p.value]));
    const sentByDate = new Map(sentimentHist.data.points.map((p) => [p.date, p.score]));
    const dates = Array.from(new Set([...vixByDate.keys(), ...sentByDate.keys()])).sort();
    let aligned = 0;
    const rows: AlignedPoint[] = dates.map((date) => {
      const v = vixByDate.get(date) ?? null;
      const s = sentByDate.get(date) ?? null;
      if (v != null && s != null) aligned++;
      return { date, vix: v, sentiment: s };
    });
    return { chartData: rows, alignedDays: aligned };
  }, [vix.data, sentimentHist.data]);

  const loading = vix.loading || sentimentHist.loading;
  const error = vix.error || sentimentHist.error;

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }} data-testid="sentiment-vix-chart">
      <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
        <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Sentiment vs. VIX</h2>
        <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-1)" }}>
          Archived daily news sentiment for {symbol} alongside the CBOE Volatility
          Index, on a shared date axis. No lead-lag relationship is computed or
          implied here — the sentiment archive is new, and this is a raw trend view,
          not a backtest.
        </p>
      </div>
      <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto", display: "flex", flexDirection: "column" }}>
      {loading && <Loading lines={4} />}
      {!loading && error && (
        <ErrorState
          message={error}
          status={vix.status ?? sentimentHist.status}
          onRetry={() => {
            vix.reload();
            sentimentHist.reload();
          }}
        />
      )}
      {!loading && !error && chartData.length === 0 && (
        <div className="empty" style={{ padding: "var(--s-5)" }} data-testid="sentiment-vix-empty">
          No VIX or sentiment history archived yet.
        </div>
      )}
      {!loading && !error && chartData.length > 0 && (
        <>
          {alignedDays < _MIN_ALIGNED_DAYS && (
            <Notice
              variant="info"
              style={{ marginBottom: "var(--s-3)" }}
              data-testid="sentiment-vix-coverage-notice"
            >
              <span>
                📊 Only {alignedDays} aligned day{alignedDays === 1 ? "" : "s"} of sentiment +
                VIX history so far (the sentiment archive started 2026-07) — not enough for a
                trend read yet, minimum {_MIN_ALIGNED_DAYS}. Showing what history exists below.
              </span>
            </Notice>
          )}

          <div style={{ height: 110 }} data-testid="sentiment-vix-vix-panel">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={_SHARED_CHART_MARGIN}>
                <CartesianGrid {...chartGridProps} />
                <XAxis dataKey="date" hide />
                <YAxis tick={chartAxisTick} {...chartAxisLine} width={30} />
                <Tooltip
                  contentStyle={chartTooltipStyle}
                  labelFormatter={(l) => fmtDate(String(l))}
                  formatter={(val: unknown) =>
                    typeof val === "number" ? [val.toFixed(1), "VIX"] : ["—", "VIX"]
                  }
                />
                <Line
                  type="monotone"
                  dataKey="vix"
                  stroke={theme.accent}
                  strokeWidth={2}
                  dot={false}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div style={{ color: theme.textMuted, fontSize: "var(--t-micro)", marginBottom: "var(--s-2)" }}>
            VIX (CBOE Volatility Index)
          </div>

          <div style={{ height: 110 }} data-testid="sentiment-vix-sentiment-panel">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={_SHARED_CHART_MARGIN}>
                <CartesianGrid {...chartGridProps} />
                <XAxis
                  dataKey="date"
                  tickFormatter={fmtDate}
                  tick={chartAxisTick}
                  {...chartAxisLine}
                  minTickGap={44}
                />
                <YAxis domain={[-1, 1]} tick={chartAxisTick} {...chartAxisLine} width={30} />
                <ReferenceLine y={0} stroke={theme.border} />
                <Tooltip
                  contentStyle={chartTooltipStyle}
                  labelFormatter={(l) => fmtDate(String(l))}
                  formatter={(val: unknown) =>
                    typeof val === "number" ? [val.toFixed(2), "Sentiment"] : ["—", "Sentiment"]
                  }
                />
                <Line
                  type="monotone"
                  dataKey="sentiment"
                  stroke={seriesColor(0)}
                  strokeWidth={2}
                  dot={false}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div style={{ color: theme.textMuted, fontSize: "var(--t-micro)", marginTop: "var(--s-2)" }}>
            Archived news sentiment (FinBERT/lexicon, daily) — gaps are honest: a real
            fetch failure or a day with zero headlines, never plotted as neutral 0.
          </div>
        </>
      )}
      </div>
    </section>
  );
}

export function SentimentDynamics() {
  const nav = useNavigate();
  const [symbol, setSymbol] = useState("AAPL");
  const { data, loading, error, status, reload } = useApi<SentimentDynamicsData>(
    () => api.getSentimentDynamics(symbol),
    [symbol]
  );
  useAutoPoll(reload, "options", { hasError: error != null });
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  return (
    <div className="screen">
      <button
        onClick={back}
        style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: theme.textSecondary, fontSize: "var(--t-callout)", marginBottom: "var(--s-2)" }}
      >
        ← Back
      </button>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "var(--s-2)" }}>
        <div>
          <h1 className="screen-title">Sentiment Dynamics</h1>
          <p className="screen-sub">
            Live sentiment analysis from financial news and social media activity,
            driven by the Antigravity Agent and GJR-GARCH asymmetric volatility metrics.
          </p>
        </div>
        <div style={{ display: "flex", gap: "var(--s-2)" }}>
          <button className="reset-layout-btn" onClick={() => resetGridLayout("sentiment-layout")} title="Reset grid layout">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>
          </button>
          <button
            onClick={() => nav("/settings/sentiment")}
            style={{
              padding: "6px 12px",
              borderRadius: "var(--r-sm)",
              background: "var(--surface)",
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              cursor: "pointer",
            }}
          >
            ⚙️ Settings
          </button>
          <button
            onClick={() => reload()}
            style={{
              padding: "6px 12px",
              borderRadius: "var(--r-sm)",
              background: theme.accent,
              border: "none",
              color: "#fff",
              cursor: loading ? "not-allowed" : "pointer",
              fontWeight: 500,
              opacity: loading ? 0.7 : 1,
            }}
            disabled={loading}
          >
            {loading ? "Refreshing..." : "🔄 Refresh News"}
          </button>
        </div>
      </div>

      <TabGuide tabKey="sentiment" />

      <SymbolInput initial={symbol} onSubmit={setSymbol} pending={loading} />

      <div style={{ marginTop: "var(--s-4)" }}>
        <DynamicGrid layoutKey="sentiment-layout" defaultLayouts={{}}>
          <div key="breakdown">
            {loading && <Loading lines={3} />}
            {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
            {!loading && !error && data && <Breakdown d={data} />}
          </div>
          <div key="chart">
            <SentimentVixChart symbol={symbol} />
          </div>
        </DynamicGrid>
      </div>
    </div>
  );
}
