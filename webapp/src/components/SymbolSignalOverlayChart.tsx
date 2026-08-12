import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import { api } from "../api/client";
import type { Bar, DecisionEntry, Portfolio } from "../api/types";
import { useApi } from "../hooks/useApi";
import { EmptyState, ErrorState, Loading, Select } from "./ui";
import { chartAxisLine, chartAxisTick, chartGridProps, chartTooltipStyle } from "./charts";
import { fmtUsd } from "../format";
import { theme } from "../theme";

const FALLBACK_SYMBOLS = ["AAPL", "MSFT", "SPY"];

/**
 * Symbol price history + BUY/SELL decision overlay widget. Self-contained
 * (fetches its own held-or-fallback symbol list via `api.getPortfolio()`, no
 * required props) so it can be dropped into any screen -- currently
 * `StrategyInsights.tsx` and the Create Data App `/app/:slug` renderer, kept
 * as the ONE implementation rather than two independently-drifting copies.
 */
export function SymbolSignalOverlayChart({ defaultTicker }: { defaultTicker?: string }) {
  const portfolio = useApi<Portfolio>(() => api.getPortfolio(), []);
  const symbols = useMemo(() => {
    const held = Array.from(
      new Set((portfolio.data?.positions ?? []).map((p) => p.symbol).filter(Boolean))
    );
    let list = held.length > 0 ? held : FALLBACK_SYMBOLS;
    if (defaultTicker && !list.includes(defaultTicker)) {
      list = [defaultTicker, ...list];
    }
    return list;
  }, [portfolio.data, defaultTicker]);

  const [symbol, setSymbol] = useState(defaultTicker || (symbols[0] ?? "AAPL"));

  // Keep the selection valid as `symbols` resolves from the fallback list to
  // the operator's real holdings (portfolio fetch lands after mount).
  useEffect(() => {
    if (symbols.length > 0 && !symbols.includes(symbol)) setSymbol(symbols[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbols]);

  const bars = useApi<Bar[]>(() => api.getDataBars(symbol, 252), [symbol]);
  const decisions = useApi<DecisionEntry[]>(
    () => api.getDecisions({ symbol, limit: 100 }),
    [symbol]
  );

  const merged = useMemo(() => {
    if (!bars.data) return [];
    const markerByDate = new Map<string, { buy: boolean; sell: boolean }>();
    for (const d of decisions.data ?? []) {
      const date = (d.signal_ts ?? "").slice(0, 10);
      if (!date) continue;
      const action = (d.signal_action ?? "").toUpperCase();
      const prev = markerByDate.get(date) ?? { buy: false, sell: false };
      if (action.includes("SELL")) prev.sell = true;
      else if (action.includes("BUY")) prev.buy = true;
      markerByDate.set(date, prev);
    }
    return bars.data.map((b) => {
      const m = markerByDate.get(b.date);
      return {
        date: b.date,
        close: b.Close,
        buyMarker: m?.buy && b.Close != null ? b.Close : null,
        sellMarker: m?.sell && b.Close != null ? b.Close : null,
      };
    });
  }, [bars.data, decisions.data]);

  const buyPoints = merged
    .filter((m) => m.buyMarker != null)
    .map((m) => ({ date: m.date, value: m.buyMarker as number }));
  const sellPoints = merged
    .filter((m) => m.sellMarker != null)
    .map((m) => ({ date: m.date, value: m.sellMarker as number }));

  const loading = bars.loading || decisions.loading;
  const priced = merged.filter((m) => m.close != null);

  return (
    <div>
      <div style={{ maxWidth: 220, marginBottom: "var(--s-3)" }}>
        <Select
          label="Symbol"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          options={symbols.map((s) => ({ value: s, label: s }))}
          testId="symbol-signal-overlay-symbol-select"
        />
      </div>

      {loading && <Loading lines={3} />}
      {!loading && bars.error && (
        <ErrorState message={bars.error} status={bars.status} onRetry={bars.reload} />
      )}
      {!loading && !bars.error && priced.length === 0 && (
        <EmptyState
          title="No price history yet"
          hint={`No cached bars for ${symbol} yet.`}
        />
      )}
      {!loading && !bars.error && priced.length > 0 && (
        <div data-testid="symbol-signal-overlay-chart">
          <div style={{ height: 260 }}>
            <ResponsiveContainer>
              <ComposedChart data={merged} margin={{ top: 8, right: 6, left: 6, bottom: 0 }}>
                <CartesianGrid {...chartGridProps} />
                <XAxis dataKey="date" tick={chartAxisTick} {...chartAxisLine} minTickGap={44} />
                <YAxis tick={chartAxisTick} {...chartAxisLine} width={50} tickFormatter={(v: number) => fmtUsd(v)} />
                <Tooltip
                  contentStyle={chartTooltipStyle}
                  formatter={(v, name) => [typeof v === "number" ? fmtUsd(v) : "—", name]}
                />
                <Line
                  type="monotone"
                  dataKey="close"
                  name="Close"
                  stroke={theme.accent}
                  strokeWidth={2}
                  dot={false}
                  connectNulls
                  isAnimationActive={false}
                />
                <Scatter data={buyPoints} dataKey="value" name="BUY" shape="triangle" fill={theme.growth} isAnimationActive={false} />
                <Scatter data={sellPoints} dataKey="value" name="SELL" shape="cross" fill={theme.decline} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-2)" }}>
            <span style={{ color: theme.growth }}>▲</span> BUY &nbsp;
            <span style={{ color: theme.decline }}>✕</span> SELL — from the decision log for {symbol}.
          </p>
        </div>
      )}
    </div>
  );
}
