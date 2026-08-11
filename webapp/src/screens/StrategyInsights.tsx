import React, { useMemo, useState } from "react";
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
import type { Holding, PilotSummary } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, EmptyState, ErrorState, Input, Loading, Notice, Table } from "../components/ui";
import { EdgeByStrategyChart } from "../components/EdgeByStrategyChart";
import { SymbolSignalOverlayChart } from "../components/SymbolSignalOverlayChart";
import { TabGuide } from "../components/TabGuide";
import { fmtNum, fmtPct, fmtUsd } from "../format";
import { theme } from "../theme";

/**
 * Strategy Insights — one static, always-present screen combining three
 * previously-siloed reads (edge-by-strategy + price/decision history --
 * both now shared widgets, see components/EdgeByStrategyChart.tsx and
 * components/SymbolSignalOverlayChart.tsx -- the Pilots catalog + holdings,
 * and a real allocation simulation) into a single operator workspace.
 * Rebuilt after a code review found PR #670 (unmerged) shipped a fabricated
 * "What-If Simulation" panel (identical hardcoded deltas for every
 * strategy), a "Save to Dashboard" flow that persisted nothing, and a
 * dual-y-axis chart. This screen is deliberately NOT a generic app builder:
 * no nav-item creation, no save/persist flow, no "create app" form -- that
 * capability now lives in screens/CreateDataApp.tsx + screens/CustomView.tsx,
 * built with real persistence, reusing this screen's own chart widgets
 * rather than a second implementation of them.
 */

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
// Strategies table -- @tanstack/react-table, expandable holdings row.
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
// Simulate panel -- real deltas only, never a client-side constant offset.
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
          <EdgeByStrategyChart />
        </section>

        <section className="card card-pad">
          <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>
            Price history &amp; signal overlay
          </h2>
          <SymbolSignalOverlayChart />
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
