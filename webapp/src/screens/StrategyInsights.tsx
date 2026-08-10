import React, { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar as RBar,
  ComposedChart,
  Line,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import {
  createColumnHelper,
  flexRender,
  useTable,
  tableFeatures,
  rowSortingFeature,
  createSortedRowModel,
  type SortingState,
} from "@tanstack/react-table";
import { api } from "../api/client";
import type {
  Bar,
  DecisionEntry,
  EdgeByStrategy,
  Holding,
  PilotSummary,
  Portfolio,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, EmptyState, ErrorState, Input, Loading, Notice, Select, Table } from "../components/ui";
import { chartAxisLine, chartAxisTick, chartGridProps, chartTooltipStyle } from "../components/charts";
import { TabGuide } from "../components/TabGuide";
import { fmtNum, fmtPct, fmtUsd } from "../format";
import { seriesColor, theme } from "../theme";

/**
 * Strategy Insights — one static, always-present screen combining four
 * previously-siloed reads (edge-by-strategy, per-symbol price/decision
 * history, the Pilots catalog + holdings, and a real allocation simulation)
 * into a single operator workspace. Rebuilt after a code review found PR
 * #670 (unmerged) shipped a fabricated "What-If Simulation" panel (identical
 * hardcoded deltas for every strategy), a "Save to Dashboard" flow that
 * persisted nothing, and a dual-y-axis chart. This screen is deliberately
 * NOT a generic app builder: no nav-item creation, no save/persist flow, no
 * "create app" form.
 */

const FALLBACK_SYMBOLS = ["AAPL", "MSFT", "SPY"];

/** A label / value row inside a card (value already formatted, "—" for null). */
function StatRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="row">
      <div className="row-main">
        <span className="row-title" style={{ fontWeight: 500 }}>
          {label}
        </span>
      </div>
      <div className="row-end">
        <div className="num" style={{ fontWeight: 600 }}>
          {value}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 1. Edge per Strategy -- TWO stacked single-axis bar charts (never one
//    dual-y-axis chart -- this codebase's documented anti-dual-axis
//    principle: different scales overlaid on one plot is "the most common
//    charting mistake").
// ---------------------------------------------------------------------------
function EdgePanel() {
  const { data, loading, error, status, reload } = useApi<EdgeByStrategy>(
    () => api.getEdgeByStrategy(),
    []
  );

  if (loading) return <Loading lines={3} />;
  if (error) return <ErrorState message={error} status={status} onRetry={reload} />;
  if (!data || data.rows.length === 0) {
    return (
      <EmptyState
        title="No closed trades to score yet"
        hint={data?.reason ?? "Edge ratio by strategy populates once trades close."}
      />
    );
  }

  return (
    <div data-testid="strategy-insights-edge-chart">
      <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginBottom: "var(--s-1-5)" }}>
        Mean edge ratio by strategy
      </div>
      <div style={{ height: 200 }}>
        <ResponsiveContainer>
          <BarChart data={data.rows} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
            <CartesianGrid {...chartGridProps} />
            <XAxis dataKey="strategy" tick={{ ...chartAxisTick, fontSize: "var(--t-micro)" }} {...chartAxisLine} />
            <YAxis tick={chartAxisTick} {...chartAxisLine} />
            <Tooltip
              contentStyle={chartTooltipStyle}
              formatter={(v) => [typeof v === "number" ? fmtNum(v, 2) : "—", "Mean edge ratio"]}
            />
            <RBar dataKey="mean_edge_ratio" name="Mean edge ratio" fill={seriesColor(0)} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", margin: "var(--s-3) 0 var(--s-1-5)" }}>
        Trade count by strategy
      </div>
      <div style={{ height: 200 }}>
        <ResponsiveContainer>
          <BarChart data={data.rows} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
            <CartesianGrid {...chartGridProps} />
            <XAxis dataKey="strategy" tick={{ ...chartAxisTick, fontSize: "var(--t-micro)" }} {...chartAxisLine} />
            <YAxis tick={chartAxisTick} {...chartAxisLine} allowDecimals={false} />
            <Tooltip
              contentStyle={chartTooltipStyle}
              formatter={(v) => [typeof v === "number" ? v.toFixed(0) : "—", "Trades"]}
            />
            <RBar dataKey="n_trades" name="Trades" fill={seriesColor(1)} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 2. Symbol price history + BUY/SELL decision overlay.
// ---------------------------------------------------------------------------
function PriceHistoryPanel({ symbols }: { symbols: string[] }) {
  const [symbol, setSymbol] = useState(symbols[0] ?? "AAPL");

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
          testId="strategy-insights-symbol-select"
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
        <div data-testid="strategy-insights-price-chart">
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

// ---------------------------------------------------------------------------
// 3. Strategies table -- @tanstack/react-table, expandable holdings row.
// ---------------------------------------------------------------------------
const pilotFeatures = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
});
const pilotColumnHelper = createColumnHelper<typeof pilotFeatures, PilotSummary>();

function ExpandedHoldings({ pilotId }: { pilotId: string }) {
  const { data, loading, error, status, reload } = useApi<Holding[]>(
    () => api.getHoldings(pilotId),
    [pilotId]
  );

  if (loading) return <Loading lines={2} />;
  if (error) return <ErrorState message={error} status={status} onRetry={reload} />;
  if (!data || data.length === 0) {
    return <EmptyState title="No holdings" hint="This Pilot currently holds nothing." />;
  }

  return (
    <div style={{ padding: "var(--s-2) 0" }} data-testid={`strategy-holdings-${pilotId}`}>
      <Table style={{ fontSize: "var(--t-label)" }}>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Sector</th>
            <th className="num">Weight</th>
            <th className="num">Score</th>
            <th className="num">Price</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {data.map((h) => (
            <tr key={h.symbol}>
              <td>{h.symbol}</td>
              <td>{h.sector || "—"}</td>
              <td className="num">{fmtPct(h.weight, 1, { fromFraction: true })}</td>
              <td className="num">{fmtNum(h.score, 1)}</td>
              <td className="num">{fmtUsd(h.price)}</td>
              <td>{h.action ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  );
}

function StrategiesSection({ onSimulate }: { onSimulate: (pilot: PilotSummary) => void }) {
  const { data, loading, error, status, reload } = useApi<PilotSummary[]>(() => api.listPilots(), []);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [sorting, setSorting] = useState<SortingState>([]);

  const columns = useMemo(
    () =>
      pilotColumnHelper.columns([
        pilotColumnHelper.accessor("name", {
          header: "Pilot",
          cell: (info) => {
            const p = info.row.original;
            return (
              <div>
                <div style={{ fontWeight: 700 }}>{p.name}</div>
                <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted }}>{p.id}</div>
              </div>
            );
          },
        }),
        pilotColumnHelper.accessor("category", {
          header: "Category",
          cell: (info) => <span className="chip">{info.getValue()}</span>,
        }),
        pilotColumnHelper.accessor((row) => row.headline.sharpe, {
          id: "sharpe",
          header: () => <div style={{ textAlign: "right" }}>Sharpe</div>,
          cell: (info) => (
            <div className="num" style={{ textAlign: "right" }}>
              {fmtNum(info.getValue(), 2)}
            </div>
          ),
        }),
        pilotColumnHelper.accessor((row) => row.headline.max_drawdown, {
          id: "max_dd",
          header: () => <div style={{ textAlign: "right" }}>Max DD</div>,
          cell: (info) => (
            <div className="num" style={{ textAlign: "right" }}>
              {fmtPct(info.getValue(), 0, { fromFraction: true })}
            </div>
          ),
        }),
        pilotColumnHelper.display({
          id: "actions",
          header: () => <div style={{ textAlign: "right" }}>Actions</div>,
          cell: (info) => {
            const p = info.row.original;
            const expanded = expandedId === p.id;
            return (
              <div style={{ display: "flex", gap: "var(--s-2)", justifyContent: "flex-end" }}>
                <Button
                  variant="neutral"
                  onClick={() => setExpandedId(expanded ? null : p.id)}
                  data-testid={`strategy-holdings-toggle-${p.id}`}
                >
                  {expanded ? "Hide holdings" : "Holdings"}
                </Button>
                <Button
                  variant="primary"
                  onClick={() => onSimulate(p)}
                  data-testid={`strategy-simulate-${p.id}`}
                >
                  Simulate
                </Button>
              </div>
            );
          },
        }),
      ]),
    [expandedId, onSimulate]
  );

  const table = useTable({
    features: pilotFeatures,
    data: data ?? [],
    columns,
    state: { sorting },
    onSortingChange: setSorting,
  });

  if (loading) return <Loading lines={3} />;
  if (error) return <ErrorState message={error} status={status} onRetry={reload} />;
  if (!data || data.length === 0) {
    return <EmptyState title="No Pilots yet" hint="Pilots populate once the marketplace catalog has entries." />;
  }

  return (
    <div style={{ overflowX: "auto" }} data-testid="strategy-insights-table">
      <Table>
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <th
                  key={header.id}
                  onClick={header.column.getToggleSortingHandler()}
                  style={{ cursor: header.column.getCanSort() ? "pointer" : "default" }}
                >
                  {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                  {{ asc: " ▲", desc: " ▼" }[header.column.getIsSorted() as string] ?? null}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <React.Fragment key={row.id}>
              <tr data-testid={`strategy-row-${row.original.id}`}>
                {row.getAllCells().map((cell) => (
                  <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                ))}
              </tr>
              {expandedId === row.original.id && (
                <tr>
                  <td colSpan={columns.length}>
                    <ExpandedHoldings pilotId={row.original.id} />
                  </td>
                </tr>
              )}
            </React.Fragment>
          ))}
        </tbody>
      </Table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 4. Simulate panel -- real deltas only, never a client-side constant offset.
// ---------------------------------------------------------------------------
function delta(current: number | null, projected: number | null): string {
  if (current == null || projected == null) return "";
  const d = projected - current;
  const sign = d >= 0 ? "+" : "";
  return ` (${sign}${d.toFixed(3)})`;
}

function SimulatePanel({ pilot }: { pilot: PilotSummary | null }) {
  const [amount, setAmount] = useState("10000");
  const simulate = useMutation((pilotId: string, amt: number) =>
    api.simulatePilotAllocation(pilotId, { allocation_amount: amt })
  );

  if (!pilot) {
    return (
      <section className="card card-pad" data-testid="simulate-panel">
        <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-2)" }}>Simulate allocation</h2>
        <p style={{ color: theme.textMuted, fontSize: "var(--t-body)" }}>
          Pick a Pilot from the table above and click "Simulate" to project a hypothetical
          allocation's impact on Sharpe ratio and max drawdown.
        </p>
      </section>
    );
  }

  const amountNum = Number(amount);
  const validAmount = amount.trim() !== "" && Number.isFinite(amountNum) && amountNum > 0;
  const result = simulate.result;
  const hasNullField =
    result != null &&
    (result.current.sharpe_ratio == null ||
      result.current.max_drawdown == null ||
      result.projected.sharpe_ratio == null ||
      result.projected.max_drawdown == null);

  return (
    <section className="card card-pad" data-testid="simulate-panel">
      <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-2)" }}>
        Simulate allocation — {pilot.name}
      </h2>
      <div style={{ display: "flex", gap: "var(--s-3)", alignItems: "flex-end", flexWrap: "wrap" }}>
        <div style={{ maxWidth: 220 }}>
          <Input
            label="Allocation amount ($)"
            type="number"
            min={0}
            step={100}
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </div>
        <Button
          variant="primary"
          pending={simulate.pending}
          disabled={!validAmount}
          onClick={() => simulate.run(pilot.id, amountNum)}
          data-testid="simulate-run"
        >
          Simulate
        </Button>
      </div>

      {simulate.error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-3)" }}>
          <span>{simulate.error}</span>
        </Notice>
      )}

      {result && (
        <div style={{ marginTop: "var(--s-3)" }} data-testid="simulate-result">
          {result.reason && hasNullField && (
            <Notice variant="info" style={{ marginBottom: "var(--s-3)" }}>
              <span>{result.reason}</span>
            </Notice>
          )}
          <div className="list">
            <StatRow
              label="Sharpe ratio"
              value={
                <span>
                  {fmtNum(result.current.sharpe_ratio, 2)} → {fmtNum(result.projected.sharpe_ratio, 2)}
                  <span style={{ color: theme.textMuted, fontWeight: 400 }}>
                    {delta(result.current.sharpe_ratio, result.projected.sharpe_ratio)}
                  </span>
                </span>
              }
            />
            <StatRow
              label="Max drawdown"
              value={
                <span>
                  {fmtPct(result.current.max_drawdown, 1, { fromFraction: true })} →{" "}
                  {fmtPct(result.projected.max_drawdown, 1, { fromFraction: true })}
                </span>
              }
            />
            <StatRow label="Portfolio heat (current)" value={fmtPct(result.heat_pct_current, 1, { fromFraction: true })} />
            <StatRow
              label="Portfolio heat (projected)"
              value={<span style={{ color: theme.textMuted }}>Not available for hypothetical positions</span>}
            />
          </div>
          <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-2-5)" }}>
            Coverage: {result.coverage.symbols_covered} / {result.coverage.symbols_total} symbols
          </p>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------
export function StrategyInsights() {
  const portfolio = useApi<Portfolio>(() => api.getPortfolio(), []);
  const symbols = useMemo(() => {
    const held = Array.from(
      new Set((portfolio.data?.positions ?? []).map((p) => p.symbol).filter(Boolean))
    );
    return held.length > 0 ? held : FALLBACK_SYMBOLS;
  }, [portfolio.data]);

  const [selectedPilot, setSelectedPilot] = useState<PilotSummary | null>(null);

  return (
    <div className="screen">
      <h1 className="screen-title">Strategy Insights</h1>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-body)", marginTop: "var(--s-1)" }}>
        Edge-by-strategy, price/decision history, the Pilots catalog, and a real allocation
        simulation, in one workspace.
      </p>

      <TabGuide tabKey="strategy-insights" />

      <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)", marginTop: "var(--s-4)" }}>
        <section className="card card-pad">
          <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>Edge per strategy</h2>
          <EdgePanel />
        </section>

        <section className="card card-pad">
          <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>
            Price history &amp; signal overlay
          </h2>
          <PriceHistoryPanel symbols={symbols} />
        </section>

        <section className="card card-pad">
          <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>Strategies</h2>
          <StrategiesSection onSimulate={setSelectedPilot} />
        </section>

        <SimulatePanel pilot={selectedPilot} />
      </div>
    </div>
  );
}
