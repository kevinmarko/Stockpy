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
  MacroHistorySeries,
  SentimentDynamics as SentimentDynamicsData,
  SentimentHistory,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, Notice, Tile } from "../components/ui";
import { chartAxisLine, chartAxisTick, chartGridProps, chartTooltipStyle } from "../components/charts";
import { SymbolInput } from "../components/SymbolInput";
import { fmtDate, fmtNum } from "../format";
import { seriesColor, theme } from "../theme";

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
        <Notice variant="info" style={{ marginBottom: 16 }}>
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
    <section className="card card-pad" style={{ marginTop: 16 }} data-testid="sentiment-vix-chart">
      <h2 style={{ fontSize: 15, margin: "0 0 4px" }}>Sentiment vs. VIX</h2>
      <p style={{ color: theme.textMuted, fontSize: 12, margin: "0 0 10px", lineHeight: 1.5 }}>
        Archived daily news sentiment for {symbol} alongside the CBOE Volatility
        Index, on a shared date axis. No lead-lag relationship is computed or
        implied here — the sentiment archive is new, and this is a raw trend view,
        not a backtest.
      </p>

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
        <div className="empty" style={{ padding: 20 }} data-testid="sentiment-vix-empty">
          No VIX or sentiment history archived yet.
        </div>
      )}
      {!loading && !error && chartData.length > 0 && (
        <>
          {alignedDays < _MIN_ALIGNED_DAYS && (
            <Notice
              variant="info"
              style={{ marginBottom: 12 }}
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
          <div style={{ color: theme.textMuted, fontSize: 11, marginBottom: 8 }}>
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
          <div style={{ color: theme.textMuted, fontSize: 11 }}>
            Archived news sentiment (FinBERT/lexicon, daily) — gaps are honest: a real
            fetch failure or a day with zero headlines, never plotted as neutral 0.
          </div>
        </>
      )}
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

      <SentimentVixChart symbol={symbol} />
    </div>
  );
}
