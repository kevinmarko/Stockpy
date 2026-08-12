import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
} from "recharts";
import { api } from "../api/client";
import type { Portfolio, SentimentHistory } from "../api/types";
import { useApi } from "../hooks/useApi";
import { EmptyState, ErrorState, Loading, Select } from "./ui";
import { chartAxisLine, chartAxisTick, chartGridProps, chartTooltipStyle } from "./charts";
import { fmtDate } from "../format";
import { seriesColor, theme } from "../theme";

const FALLBACK_SYMBOLS = ["AAPL", "MSFT", "SPY"];
const LOOKBACK_DAYS = 180;

/**
 * Compact single-symbol sentiment-score-over-time widget. Self-contained
 * (fetches its own held-or-fallback symbol list via `api.getPortfolio()`, no
 * required props) so it can be dropped into any screen -- same held/fallback
 * symbol-picker pattern as `SymbolSignalOverlayChart`, kept as the ONE
 * implementation rather than a drifting copy.
 */
export function SentimentMiniChart() {
  const portfolio = useApi<Portfolio>(() => api.getPortfolio(), []);
  const symbols = useMemo(() => {
    const held = Array.from(
      new Set((portfolio.data?.positions ?? []).map((p) => p.symbol).filter(Boolean))
    );
    return held.length > 0 ? held : FALLBACK_SYMBOLS;
  }, [portfolio.data]);

  const [symbol, setSymbol] = useState(symbols[0] ?? "AAPL");

  // Keep the selection valid as `symbols` resolves from the fallback list to
  // the operator's real holdings (portfolio fetch lands after mount).
  useEffect(() => {
    if (symbols.length > 0 && !symbols.includes(symbol)) setSymbol(symbols[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbols]);

  const { data, loading, error, status, reload } = useApi<SentimentHistory>(
    () => api.getSentimentHistory(symbol, LOOKBACK_DAYS),
    [symbol]
  );

  const points = (data?.points ?? []).filter((p) => p.score != null);

  return (
    <div>
      <div style={{ maxWidth: 220, marginBottom: "var(--s-3)" }}>
        <Select
          label="Symbol"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          options={symbols.map((s) => ({ value: s, label: s }))}
          testId="sentiment-mini-symbol-select"
        />
      </div>

      {loading && <Loading lines={3} />}
      {!loading && error && (
        <ErrorState message={error} status={status} onRetry={reload} />
      )}
      {!loading && !error && points.length === 0 && (
        <EmptyState
          title={`No sentiment history yet for ${symbol}`}
          hint={data?.reason ?? undefined}
        />
      )}
      {!loading && !error && points.length > 0 && (
        <div data-testid="sentimentMini-widget">
          <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginBottom: "var(--s-1-5)" }}>
            Sentiment score over time — {symbol}
          </div>
          <div style={{ height: 200 }}>
            <ResponsiveContainer>
              <LineChart data={data?.points ?? []} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
                <CartesianGrid {...chartGridProps} />
                <XAxis
                  dataKey="date"
                  tick={chartAxisTick}
                  {...chartAxisLine}
                  minTickGap={44}
                  tickFormatter={(v: string) => fmtDate(v)}
                />
                <YAxis domain={[-1, 1]} tick={chartAxisTick} {...chartAxisLine} width={36} />
                <ReferenceLine y={0} stroke={theme.chartGrid} strokeDasharray="3 3" />
                <Tooltip
                  contentStyle={chartTooltipStyle}
                  labelFormatter={(v) => fmtDate(typeof v === "string" ? v : undefined)}
                  formatter={(v) => [typeof v === "number" ? v.toFixed(3) : "—", "Sentiment"]}
                />
                <Line
                  type="monotone"
                  dataKey="score"
                  name="Sentiment"
                  stroke={seriesColor(0)}
                  strokeWidth={2}
                  dot={false}
                  connectNulls
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}
