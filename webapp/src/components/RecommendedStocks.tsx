import { useState, useMemo } from "react";
import { Link, useNavigate } from "react-router";
import {
  createColumnHelper,
  flexRender,
  useTable,
  tableFeatures,
  columnFilteringFeature,
  rowSortingFeature,
  rowPaginationFeature,
  createFilteredRowModel,
  createSortedRowModel,
  createPaginatedRowModel,
  globalFilteringFeature,
  columnVisibilityFeature,
} from "@tanstack/react-table";
import { api } from "../api/client";
import type { ExecutionQueue, Recommendation, RecommendationsResponse } from "../api/types";
import { useApi } from "../hooks/useApi";
import { EmptyState, ErrorState, Loading, Input, Select, Table } from "./ui";
import { fmtNum, fmtPct, timeAgo } from "../format";
import { theme } from "../theme";

const features = tableFeatures({
  columnVisibilityFeature,
  globalFilteringFeature,
  columnFilteringFeature,
  rowSortingFeature,
  rowPaginationFeature,
  filteredRowModel: createFilteredRowModel(),
  sortedRowModel: createSortedRowModel(),
  paginatedRowModel: createPaginatedRowModel(),
});

type RowData = Recommendation & { queued: boolean };
const columnHelper = createColumnHelper<typeof features, RowData>();

export function RecommendedStocks({
  onSelect,
  limit = 100,
}: {
  onSelect?: (symbol: string) => void;
  limit?: number;
}) {
  const nav = useNavigate();
  const { data, loading, error, status, reload } = useApi<RecommendationsResponse>(
    () => api.getRecommendations(limit),
    [limit]
  );
  const queue = useApi<ExecutionQueue>(() => api.getExecutionQueue(), []);
  const queuedSymbols = new Set(
    (queue.data?.intents ?? []).map((i) => i.symbol.toUpperCase())
  );

  const select = (symbol: string) => {
    if (onSelect) onSelect(symbol);
    else nav(`/symbol/${encodeURIComponent(symbol)}`);
  };

  const [globalFilter, setGlobalFilter] = useState("");
  const [sectorFilter, setSectorFilter] = useState<string>("All");
  const [minConviction, setMinConviction] = useState<string>("0");

  const sectors = useMemo(() => {
    if (!data?.recommendations) return [];
    const unique = new Set(data.recommendations.map((r) => r.sector).filter(Boolean));
    return ["All", ...Array.from(unique)].map((s) => ({ value: s as string, label: s as string }));
  }, [data]);

  const tableData = useMemo(() => {
    if (!data?.recommendations) return [];
    
    return data.recommendations
      .map((r) => ({
        ...r,
        queued: queuedSymbols.has(r.symbol.toUpperCase()),
      }))
      .filter((r) => {
        if (sectorFilter !== "All" && r.sector !== sectorFilter) return false;
        
        const minC = parseFloat(minConviction);
        if (!isNaN(minC) && minC > 0 && (r.conviction == null || r.conviction < minC / 100)) return false;

        return true;
      });
  }, [data, queuedSymbols, sectorFilter, minConviction]);

  const columns = useMemo(
    () => columnHelper.columns([
      columnHelper.accessor("symbol", {
        header: "Symbol",
        cell: (info) => {
          const r = info.row.original;
          return (
            <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
              <button
                type="button"
                onClick={() => select(r.symbol)}
                data-testid={`rec-btn-${r.symbol}`}
                style={{
                  fontWeight: 700,
                  color: theme.textPrimary,
                  background: "none",
                  border: "none",
                  padding: 0,
                  cursor: "pointer",
                  textDecoration: "underline",
                }}
              >
                {r.symbol}
              </button>
              {r.action && (
                <span
                  style={{
                    fontSize: "var(--t-micro)",
                    fontWeight: 700,
                    color: theme.growth,
                    background: "rgba(16,185,129,0.12)",
                    padding: "1px 6px",
                    borderRadius: "var(--r-2xs)",
                    whiteSpace: "nowrap",
                  }}
                >
                  {r.action}
                </span>
              )}
            </div>
          );
        },
      }),
      columnHelper.display({
        id: "details",
        header: "Details",
        cell: (info: any) => {
          const r = info.row.original;
          return (
            <div style={{ fontSize: "var(--t-caption)", color: theme.textMuted }}>
              {[r.sector, r.buy_range].filter(Boolean).join(" · ") || "—"}
            </div>
          );
        },
      }),
      columnHelper.accessor("conviction", {
        header: () => <div style={{ textAlign: "right" }}>Conviction</div>,
        cell: (info) => (
          <div style={{ fontWeight: 700, color: theme.accent, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
            {fmtPct(info.getValue(), 0, { fromFraction: true })}
          </div>
        ),
      }),
      columnHelper.accessor("score", {
        header: () => <div style={{ textAlign: "right" }}>Score</div>,
        cell: (info) => (
          <div style={{ fontSize: "var(--t-caption)", color: theme.textMuted, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
            {fmtNum(info.getValue(), 1)}
          </div>
        ),
      }),
      columnHelper.display({
        id: "actions",
        header: () => <div style={{ textAlign: "right" }}>Actions</div>,
        cell: (info: any) => {
          const r = info.row.original;
          return (
            <div
              style={{
                display: "flex",
                flexDirection: "row",
                alignItems: "center",
                justifyContent: "flex-end",
                gap: "var(--s-2)",
              }}
            >
              {r.queued && (
                <Link
                  to="/agentic"
                  data-testid={`rec-queued-${r.symbol}`}
                  style={{
                    fontSize: "var(--t-micro)",
                    fontWeight: 700,
                    color: theme.accent,
                    background: "rgba(56,189,248,0.12)",
                    padding: "1px 6px",
                    borderRadius: "var(--r-2xs)",
                    whiteSpace: "nowrap",
                    textDecoration: "none",
                  }}
                >
                  In queue
                </Link>
              )}
              <Link
                to={`/symbol/${encodeURIComponent(r.symbol)}`}
                data-testid={`rec-detail-${r.symbol}`}
                style={{
                  fontSize: "var(--t-caption)",
                  color: theme.textMuted,
                  whiteSpace: "nowrap",
                  textDecoration: "none",
                }}
              >
                Detail →
              </Link>
            </div>
          );
        },
      }),
    ]),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const table = useTable({
    features,
    data: tableData,
    columns,
    state: {
      globalFilter,
    },
    onGlobalFilterChange: setGlobalFilter,
    initialState: {
      pagination: {
        pageIndex: 0,
        pageSize: 10,
      },
    },
  });

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }} data-testid="recommended-stocks">
      <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
        <h2 style={{ fontSize: "var(--t-subhead)", margin: "0 0 var(--s-1)" }}>Recommended stocks</h2>
        <p style={{ margin: "0", fontSize: "var(--t-body)", color: theme.textMuted }}>
          The platform's current BUY picks, ranked by conviction. From the latest pipeline run
          {data && ` (${timeAgo(data.as_of)})`}.
        </p>
      </div>
      
      <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && data.recommendations.length === 0 && (
        <EmptyState
          title="No recommendations yet"
          hint={data.reason ?? "Run the pipeline to generate BUY signals."}
        />
      )}
      {!loading && !error && data && data.recommendations.length > 0 && (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-3)", marginBottom: "var(--s-3)", alignItems: "flex-end" }}>
            <div style={{ flex: "1 1 200px" }}>
              <Input
                label="Search Symbol"
                placeholder="e.g. AAPL"
                value={globalFilter}
                onChange={(e) => setGlobalFilter(e.target.value)}
              />
            </div>
            <div style={{ flex: "1 1 150px" }}>
              <Select
                label="Sector"
                value={sectorFilter}
                onChange={(e) => setSectorFilter(e.target.value)}
                options={sectors}
              />
            </div>
            <div style={{ flex: "1 1 150px" }}>
              <Input
                label="Min Conviction (%)"
                type="number"
                min={0}
                max={100}
                step={5}
                value={minConviction}
                onChange={(e) => setMinConviction(e.target.value)}
              />
            </div>
          </div>

          <div style={{ overflowX: "auto" }}>
            <Table>
              <thead>
                {table.getHeaderGroups().map((headerGroup: any) => (
                  <tr key={headerGroup.id}>
                    {headerGroup.headers.map((header: any) => (
                      <th
                        key={header.id}
                        onClick={header.column.getToggleSortingHandler()}
                        style={{ cursor: header.column.getCanSort() ? "pointer" : "default" }}
                      >
                        {header.isPlaceholder
                          ? null
                          : flexRender(
                              header.column.columnDef.header,
                              header.getContext()
                            )}
                        {{
                          asc: " 🔼",
                          desc: " 🔽",
                        }[header.column.getIsSorted() as string] ?? null}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((row: any) => (
                  <tr key={row.id} data-testid={`rec-row-${row.original.symbol}`}>
                    {row.getAllCells().map((cell: any) => (
                      <td key={cell.id}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "var(--s-3)" }}>
            <div style={{ fontSize: "var(--t-caption)", color: theme.textMuted }}>
              Showing {table.getRowModel().rows.length} of {tableData.length} entries
            </div>
            <div style={{ display: "flex", gap: "var(--s-2)" }}>
              <button
                className="btn btn-neutral"
                onClick={() => table.previousPage()}
                disabled={!table.getCanPreviousPage()}
                style={{ padding: "var(--s-1) var(--s-2)", fontSize: "var(--t-caption)" }}
              >
                Previous
              </button>
              <button
                className="btn btn-neutral"
                onClick={() => table.nextPage()}
                disabled={!table.getCanNextPage()}
                style={{ padding: "var(--s-1) var(--s-2)", fontSize: "var(--t-caption)" }}
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
      </div>
    </section>
  );
}
