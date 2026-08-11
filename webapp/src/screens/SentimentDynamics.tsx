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
import { fmtDate, fmtDateTime, fmtNum } from "../format";
import { seriesColor, theme } from "../theme";

function getScoreColor(score: number | null): string {
  if (score == null) return theme.textSecondary;
  if (score > 0.2) return theme.growth;
  if (score < -0.2) return theme.decline;
  return theme.textSecondary;
}

/**
 * Earnings-proximity dampening status pill (`signals/news_catalyst.py`'s
 * `_earnings_proximity_multiplier`). Red/"Suppressed" = fully zeroed (0.0x)
 * inside the pre-earnings blackout window; amber/"Dampened" = halved (0.5x)
 * — covers BOTH the multi-day run-up before earnings AND the ~24h window
 * right after the print; green/"Clear" otherwise, including the honest
 * "no earnings date currently scheduled" case (never implied as a confirmed
 * future date). Renders nothing when `earnings_catalyst` is `null`.
 */
function EarningsCatalystBanner({ c }: { c: EarningsCatalystStatus | null }) {
  if (c == null) return null;
  const dateText = fmtDateTime(c.next_earnings_date);
  const cls =
    c.status === "suppressed"
      ? "badge badge-bad"
      : c.status === "dampened"
        ? "badge badge-warn"
        : "badge badge-good";
  const label =
    c.status === "suppressed" ? "Suppressed" : c.status === "dampened" ? "Dampened" : "Clear";

  let copy: string;
  if (c.status === "suppressed") {
    copy = `News sentiment is fully suppressed (${fmtNum(c.multiplier, 1)}x) inside the pre-earnings blackout window${c.next_earnings_date ? ` — next earnings ${dateText}` : ""}. The live score isn't reliable this close to a print.`;
  } else if (c.status === "dampened") {
    copy = `News sentiment is dampened (${fmtNum(c.multiplier, 1)}x) — this covers both the run-up in the days leading into earnings and the roughly 24-hour window right after the print, while the reaction is still settling${c.next_earnings_date ? ` (next earnings ${dateText})` : ""}.`;
  } else {
    copy = c.next_earnings_date
      ? `No earnings-driven dampening in effect right now — next earnings ${dateText}.`
      : "No earnings-driven dampening in effect — no earnings date currently scheduled.";
  }

  return (
    <div
      className="card card-pad"
      data-testid="earnings-catalyst-banner"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--s-2-5)",
        marginBottom: "var(--s-3)",
        flexWrap: "wrap",
      }}
    >
      <span className={cls} data-testid="earnings-catalyst-badge">
        {label}
      </span>
      <span style={{ color: theme.textSecondary, fontSize: "var(--t-callout)" }}>{copy}</span>
    </div>
  );
}

/** Tiny 3-segment positive/neutral/negative probability bar for one headline. */
function HeadlineProbabilityBar({ p }: { p: HeadlineSentimentItem["probabilities"] }) {
  const total = p.positive + p.neutral + p.negative;
  const safeTotal = total > 0 ? total : 1;
  return (
    <div
      title={`positive ${fmtNum(p.positive, 2)} / neutral ${fmtNum(p.neutral, 2)} / negative ${fmtNum(p.negative, 2)}`}
      style={{ display: "flex", height: 6, borderRadius: 3, overflow: "hidden", width: 84, flex: "0 0 auto" }}
    >
      <div style={{ width: `${(p.positive / safeTotal) * 100}%`, background: theme.growth }} />
      <div style={{ width: `${(p.neutral / safeTotal) * 100}%`, background: theme.textMuted }} />
      <div style={{ width: `${(p.negative / safeTotal) * 100}%`, background: theme.decline }} />
    </div>
  );
}

/**
 * The real scored-headline feed backing `sentiment_score` (FMP-primary,
 * Finnhub-fallback — `signals/news_catalyst.py`). Publisher/title/url/date
 * come straight from `headlines`; `provider_used` is surfaced as a small
 * badge when a real provider actually served this request. Honest empty
 * states: "News provider not configured" when neither is set up at all,
 * vs. "No recent headlines" when a provider is configured but genuinely
 * returned nothing this request — never a fabricated headline.
 */
function HeadlineFeed({
  headlines,
  providerUsed,
}: {
  headlines: HeadlineSentimentItem[];
  providerUsed: SentimentDynamicsData["provider_used"];
}) {
  return (
    <section
      className="card card-pad"
      style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}
      data-testid="headline-feed"
    >
      <div
        className="drag-handle"
        style={{
          padding: "var(--s-3)",
          borderBottom: `1px solid rgba(255, 255, 255, 0.08)`,
          cursor: "grab",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "var(--s-2)",
        }}
      >
        <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Headlines</h2>
        {providerUsed !== "none" && (
          <span className="chip" data-testid="headline-feed-provider">
            {providerUsed}
          </span>
        )}
      </div>
      <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
        {headlines.length === 0 && (
          <div className="empty" data-testid="headline-feed-empty">
            {providerUsed === "none" ? "News provider not configured" : "No recent headlines"}
          </div>
        )}
        {headlines.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
            {headlines.map((h, i) => (
              <div
                key={i}
                data-testid="headline-item"
                style={{
                  borderBottom: i < headlines.length - 1 ? `1px solid ${theme.border}` : "none",
                  paddingBottom: i < headlines.length - 1 ? "var(--s-2-5)" : 0,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    gap: "var(--s-2)",
                    marginBottom: "var(--s-1)",
                  }}
                >
                  <span style={{ fontWeight: 600, color: theme.textSecondary, fontSize: "var(--t-caption)" }}>
                    {h.publisher}
                  </span>
                  <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
                    {fmtDateTime(h.published_at)}
                  </span>
                </div>
                {h.url ? (
                  <a
                    href={h.url}
                    target="_blank"
                    rel="noreferrer"
                    style={{ color: theme.textPrimary, fontSize: "var(--t-callout)", textDecoration: "none" }}
                  >
                    {h.title}
                  </a>
                ) : (
                  <span style={{ color: theme.textPrimary, fontSize: "var(--t-callout)" }}>{h.title}</span>
                )}
                <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", marginTop: "var(--s-1-5)" }}>
                  <span className="num" style={{ color: getScoreColor(h.score), fontSize: "var(--t-caption)" }}>
                    {fmtNum(h.score, 2)}
                  </span>
                  <HeadlineProbabilityBar p={h.probabilities} />
                </div>
              </div>
            ))}
          </div>
        )}
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

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: "var(--s-2-5)", marginBottom: "var(--s-4)" }}>
        <Tile
          label="Sentiment Score"
          value={<span style={{ color: getScoreColor(d.sentiment_score) }}>{fmtNum(d.sentiment_score, 2)}</span>}
        />
        <Tile label="Sentiment Intensity" value={fmtNum(d.sentiment_intensity, 2)} />
        <Tile label="Credibility Score" value={fmtNum(d.credibility_score, 2)} />
        <Tile label="Vol Persistence" value={fmtNum(d.volatility_persistence, 2)} />
      </div>

      <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
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

// Minimum ALIGNED days (both a real VIX value AND a real, non-null sentiment
// score on the same date) before this chart will show the trend without a
// prominent "not enough history yet" banner. This is a display threshold,
// NOT settings.SENTIMENT_PIT_MIN_MONTHS (the much larger, ~6-month bar
// validation/backtest deployability claims must clear) — this chart makes
// no lead-lag or causal claim at all, at any depth; the banner exists
// purely so a two-week-old archive doesn't get mistaken for a real trend.
const _MIN_ALIGNED_DAYS = 14;

const _SHARED_CHART_MARGIN = { top: 6, right: 10, left: -10, bottom: 0 } as const;

type AlignedPoint = { date: string; vix: number | null; sentiment: number | null };

/**
 * SentimentVixChart — rolling news sentiment vs. VIX, TWO stacked charts
 * sharing one date-aligned x-axis. Deliberately never a single dual-axis
 * chart: sentiment (~[-1,1]) and VIX (~10-80) are different scales, and
 * overlaying two y-axes on one plot is the most common charting mistake —
 * two panels with a shared x-domain reads the lead-lag relationship (if
 * any) more honestly than a forced overlay would.
 *
 * No correlation/lead-lag NUMBER is ever computed or shown here — the
 * sentiment archive (news_history) only started 2026-07, far too young for
 * that claim. See the coverage banner below _MIN_ALIGNED_DAYS.
 */
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
          <button
            onClick={() => nav("/settings/sentiment")}
            style={{
              padding: "6px 12px",
              borderRadius: "var(--r-sm)",
              background: "transparent",
              border: `1px solid ${theme.border}`,
              color: theme.textSecondary,
              fontSize: "var(--t-caption)",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Configure ingestion →
          </button>
        </div>
      </div>

      <TabGuide tabKey="sentiment" />

      <SymbolInput initial={symbol} onSubmit={setSymbol} pending={loading} />

      {!loading && !error && data && <EarningsCatalystBanner c={data.earnings_catalyst} />}

      <div style={{ marginTop: "var(--s-4)" }}>
        <div className="dashboard-layout" style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
          <div key="breakdown">
            {loading && <Loading lines={3} />}
            {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
            {!loading && !error && data && <Breakdown d={data} />}
          </div>
          <div key="chart">
            <SentimentVixChart symbol={symbol} />
          </div>
          <div key="headlines">
            {loading && <Loading lines={3} />}
            {!loading && !error && data && (
              <HeadlineFeed headlines={data.headlines} providerUsed={data.provider_used} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
