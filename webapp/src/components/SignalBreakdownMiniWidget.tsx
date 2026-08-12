import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar as RBar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
} from "recharts";
import { api } from "../api/client";
import type { Portfolio, SignalBreakdown } from "../api/types";
import { useApi } from "../hooks/useApi";
import { EmptyState, ErrorState, Loading, Select, Tile } from "./ui";
import { chartAxisLine, chartAxisTick, chartGridProps, chartTooltipStyle } from "./charts";
import { fmtNum } from "../format";
import { theme } from "../theme";

const FALLBACK_SYMBOLS = ["AAPL", "MSFT", "SPY"];
const DASH = "—";

function actionColor(action: string | null): string {
  if (action === "BUY") return theme.growth;
  if (action === "SELL") return theme.decline;
  return theme.textSecondary;
}

/**
 * Mini per-symbol signal breakdown widget -- action/conviction/blended-score
 * header plus a horizontal bar chart of each signal module's contribution.
 * Self-contained (fetches its own held-or-fallback symbol list via
 * `api.getPortfolio()` and its own breakdown via `api.getSignalBreakdown()`,
 * no required props) so it can be dropped into the Create Data App canvas --
 * mirrors `SymbolSignalOverlayChart`'s self-contained symbol-select pattern
 * and `SignalContributionPanel`'s horizontal-bar rendering, kept as its own
 * small widget rather than reusing that full-size panel directly.
 */
export function SignalBreakdownMiniWidget() {
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

  const { data, loading, error, status, reload } = useApi<SignalBreakdown>(
    () => api.getSignalBreakdown(symbol),
    [symbol]
  );

  const modules = useMemo(() => {
    if (!data) return [];
    // Only modules with a real (non-null) contribution can be plotted; a
    // module that didn't run this cycle is dropped from the chart rather
    // than rendered as a fabricated zero bar.
    return [...data.modules]
      .filter((m) => m.contribution !== null)
      .sort((a, b) => Math.abs(b.contribution!) - Math.abs(a.contribution!));
  }, [data]);

  return (
    <div>
      <div style={{ maxWidth: 220, marginBottom: "var(--s-3)" }}>
        <Select
          label="Symbol"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          options={symbols.map((s) => ({ value: s, label: s }))}
          testId="signal-breakdown-widget-symbol-select"
        />
      </div>

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && (!data || modules.length === 0) && (
        <EmptyState
          title="No signal data yet"
          hint={`No scored signal modules for ${symbol} yet -- run the pipeline, then reload.`}
        />
      )}
      {!loading && !error && data && modules.length > 0 && (
        <div data-testid="signalBreakdown-widget">
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))",
              gap: "var(--s-2)",
              marginBottom: "var(--s-3)",
            }}
          >
            <Tile
              label="Action"
              value={<span style={{ color: actionColor(data.action) }}>{data.action ?? DASH}</span>}
            />
            <Tile label="Conviction" value={data.conviction == null ? DASH : fmtNum(data.conviction, 2)} />
            <Tile label="Blended score" value={data.final_score == null ? DASH : fmtNum(data.final_score, 0)} />
          </div>

          <div style={{ height: Math.max(160, modules.length * 32) }}>
            <ResponsiveContainer>
              <BarChart data={modules} layout="vertical" margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                <CartesianGrid {...chartGridProps} />
                <XAxis type="number" tick={chartAxisTick} {...chartAxisLine} />
                <YAxis
                  dataKey="name"
                  type="category"
                  tick={{ ...chartAxisTick, fontSize: "var(--t-micro)" }}
                  width={110}
                  {...chartAxisLine}
                />
                <Tooltip
                  contentStyle={chartTooltipStyle}
                  formatter={(v) => [typeof v === "number" ? fmtNum(v, 4) : "—", "Contribution"]}
                />
                <RBar dataKey="contribution" name="Contribution" isAnimationActive={false}>
                  {modules.map((m, i) => (
                    <Cell key={`cell-${i}`} fill={m.contribution! >= 0 ? theme.growth : theme.decline} />
                  ))}
                </RBar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-2)" }}>
            Contribution = score × weight, for {symbol}.
          </p>
        </div>
      )}
    </div>
  );
}
