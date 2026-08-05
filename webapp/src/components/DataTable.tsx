import React, { useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  columnFilteringFeature,
  globalFilteringFeature,
  createFilteredRowModel,
  createSortedRowModel,
  rowSortingFeature,
  tableFeatures,
  useTable,
  createGroupedRowModel,
  columnGroupingFeature,
  rowExpandingFeature,
  createExpandedRowModel,
  flexRender,
  SortingState,

  ColumnDef,
  filterFn_includesString,
  columnVisibilityFeature,
  columnPinningFeature,
  columnSizingFeature,
} from "@tanstack/react-table";
import { useDensity } from "./DensityContext";

export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => React.ReactNode;
  sortable?: boolean;
  /**
   * Opt-in: renders a small pin/unpin affordance in this column's header so
   * the operator can stick it to the left edge while the rest of the table
   * scrolls horizontally underneath. Undefined/false (the default) renders
   * no pinning UI for the column — pinning is never forced on a column.
   */
  pinnable?: boolean;
}

interface DataTableProps<T> {
  data: T[];
  columns: Column<T>[];
  groupByKey?: keyof T;
  onRowClick?: (row: T) => void;
  copyableJson?: boolean;
}

const features = tableFeatures({
  columnFilteringFeature,
  globalFilteringFeature,
  columnVisibilityFeature,
  rowSortingFeature,
  columnGroupingFeature,
  rowExpandingFeature,
  // columnSizingFeature is registered alongside columnPinningFeature purely
  // so `column.getStart("start")` is available to compute the pixel offset
  // for a sticky pinned column below — this table never reads `getSize()`
  // to drive a column's rendered width, so registering it does not change
  // any existing column's layout (auto-layout, driven by content, is
  // untouched; only the new pinning affordance depends on this feature).
  columnPinningFeature,
  columnSizingFeature,
  filteredRowModel: createFilteredRowModel(),
  sortedRowModel: createSortedRowModel(),
  groupedRowModel: createGroupedRowModel(),
  expandedRowModel: createExpandedRowModel(),
  filterFns: { includesString: filterFn_includesString }
});

export function DataTable<T extends Record<string, any>>({
  data,
  columns,
  groupByKey,
  onRowClick,
  copyableJson = true,
}: DataTableProps<T>) {
  const { density } = useDensity();
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");

  // Columns opted into the pin affordance, keyed by their TanStack column id
  // (which is `col.key` — see the `id: col.key` mapping below). The `_actions`
  // column is never in this set since it isn't part of the caller's `columns`.
  const pinnableColumnKeys = useMemo(
    () => new Set(columns.filter((col) => col.pinnable).map((col) => col.key)),
    [columns]
  );

  // Convert custom columns to TanStack Table ColumnDefs
  const tableColumns = useMemo<ColumnDef<typeof features, T>[]>(() => {
    const cols: ColumnDef<typeof features, T>[] = columns.map((col) => ({
      id: col.key,
      accessorFn: (row) => row[col.key],
      header: col.header,
      enableSorting: col.sortable !== false,
      cell: (info) =>
        col.render ? col.render(info.row.original) : info.getValue() as React.ReactNode,
    }));

    if (copyableJson) {
      cols.push({
        id: "_actions",
        header: "Actions",
        enableSorting: false,
        cell: (info) => (
          <button
            onClick={(e) => {
              e.stopPropagation();
              navigator.clipboard.writeText(JSON.stringify(info.row.original, null, 2));
              alert("Copied event context as JSON to clipboard!");
            }}
            style={{
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              borderRadius: "var(--r-xs)",
              color: "var(--text-muted)",
              fontSize: "var(--t-micro)",
              padding: "2px 6px",
              cursor: "pointer",
            }}
          >
            JSON
          </button>
        ),
      });
    }
    return cols;
  }, [columns, copyableJson]);

  const table = useTable({
    features,
    data,
    columns: tableColumns,
    state: {
      sorting,
      globalFilter,
      grouping: groupByKey ? [String(groupByKey)] : [],
    },
    initialState: {
      expanded: true,
    },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    globalFilterFn: "includesString",
  });

  const { rows } = table.getRowModel();
  // The displayed row model (above) is grouping+expansion-aware, so with
  // `groupByKey` set it includes both group-header rows AND leaf rows —
  // using its length for the "Showing N records" count over-reports by the
  // number of distinct groups. `getFilteredRowModel()` is resolved earlier
  // in the pipeline (core -> filtered -> grouped -> sorted -> expanded), so
  // it always reflects the filtered LEAF rows only, independent of whether
  // grouping/expansion is in play.
  const filteredRowCount = table.getFilteredRowModel().rows.length;
  const parentRef = useRef<HTMLDivElement>(null);

  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => (density === "compact" ? 36 : 48),
    overscan: 10,
  });

  const isTest = typeof process !== "undefined" && process.env?.NODE_ENV === "test";
  const virtualRows = isTest
    ? rows.map((_, index) => ({ index, start: 0, size: 0, end: 0, key: index, measureElement: () => {} }))
    : rowVirtualizer.getVirtualItems();

  const paddingTop = !isTest && virtualRows.length > 0 ? virtualRows[0].start : 0;
  const totalSize = rowVirtualizer.getTotalSize();
  const paddingBottom = !isTest && virtualRows.length > 0 ? totalSize - virtualRows[virtualRows.length - 1].end : 0;

  const cellPadding = density === "compact" ? "var(--s-1-5) var(--s-2-5)" : "var(--s-3) var(--s-4)";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <input
          type="text"
          placeholder="Filter data..."
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
          style={{
            background: "var(--surface)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: "var(--r-xs)",
            padding: "var(--s-1-5) var(--s-3)",
            fontSize: "var(--t-caption)",
            maxWidth: "280px",
          }}
        />
        <span style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)" }}>
          Showing {filteredRowCount} records
        </span>
      </div>

      <div
        ref={parentRef}
        style={{
          overflowX: "auto",
          overflowY: "auto",
          maxHeight: "500px",
          border: "1px solid var(--border)",
          borderRadius: "var(--r-sm)",
          position: "relative",
        }}
      >
        <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "var(--t-body)" }}>
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id} style={{ background: "var(--surface-2)", borderBottom: "1px solid var(--border)", position: "sticky", top: 0, zIndex: 1 }}>
                {headerGroup.headers.map((header) => {
                  const isPinned = header.column.getIsPinned();
                  const isPinnable = pinnableColumnKeys.has(header.column.id);
                  const isActionsColumn = header.column.id === "_actions";
                  return (
                    <th
                      key={header.id}
                      onClick={header.column.getToggleSortingHandler()}
                      style={{
                        padding: cellPadding,
                        fontWeight: 700,
                        color: "var(--text-secondary)",
                        fontSize: "var(--t-caption)",
                        cursor: header.column.getCanSort() ? "pointer" : "default",
                        userSelect: "none",
                        background: "var(--surface-2)",
                        ...(isActionsColumn ? { width: "60px" } : {}),
                        ...(isPinned
                          ? {
                              position: "sticky" as const,
                              left: `${header.column.getStart("start")}px`,
                              // zIndex 2 (vs. the header row's own zIndex: 1)
                              // keeps a pinned header cell visible above its
                              // unpinned sibling cells as they scroll
                              // horizontally underneath it, without escaping
                              // above the sticky header row itself (both live
                              // inside the same local stacking context the
                              // row's own `position: sticky` + `zIndex`
                              // establishes).
                              zIndex: 2,
                            }
                          : {}),
                      }}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {{
                        asc: " ▲",
                        desc: " ▼",
                      }[header.column.getIsSorted() as string] ?? null}
                      {isPinnable && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            header.column.pin(isPinned ? false : "start");
                          }}
                          aria-label={isPinned ? "Unpin column" : "Pin column"}
                          title={isPinned ? "Unpin column" : "Pin column"}
                          style={{
                            marginLeft: "var(--s-1)",
                            background: isPinned ? "var(--surface-3)" : "var(--surface-2)",
                            border: "1px solid var(--border)",
                            borderRadius: "var(--r-xs)",
                            color: "var(--text-muted)",
                            fontSize: "var(--t-micro)",
                            padding: "1px 4px",
                            cursor: "pointer",
                            lineHeight: 1,
                          }}
                        >
                          📌
                        </button>
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={tableColumns.length} style={{ padding: "var(--s-4)", textAlign: "center", color: "var(--text-muted)" }}>
                  No matching records found.
                </td>
              </tr>
            ) : (
              <>
                {paddingTop > 0 && (
                  <tr>
                    <td colSpan={tableColumns.length} style={{ height: `${paddingTop}px` }} />
                  </tr>
                )}
                {virtualRows.map((virtualRow) => {
                  const row = rows[virtualRow.index];
                  const idx = virtualRow.index;

                  if (row.getIsGrouped()) {
                    return (
                      <tr
                        key={row.id}
                        onClick={() => row.toggleExpanded()}
                        style={{ background: "var(--surface-3)", cursor: "pointer", borderBottom: "1px solid var(--border)" }}
                      >
                        <td colSpan={tableColumns.length} style={{ padding: cellPadding, fontWeight: 700 }}>
                          {row.getIsExpanded() ? "▼" : "▶"} {String(row.getValue(row.groupingColumnId!))} ({row.subRows.length} events)
                        </td>
                      </tr>
                    );
                  }

                  const rowBackground = idx % 2 === 0 ? "var(--surface)" : "var(--surface-2)";

                  return (
                    <tr
                      key={row.id}
                      onClick={() => onRowClick && onRowClick(row.original)}
                      style={{
                        borderBottom: "1px solid var(--border)",
                        background: rowBackground,
                        cursor: onRowClick ? "pointer" : "default",
                      }}
                    >
                      {row.getVisibleCells().map((cell) => {
                        if (cell.getIsGrouped() || cell.getIsPlaceholder()) {
                          return null;
                        }
                        const isPinned = cell.column.getIsPinned();
                        const isActionsColumn = cell.column.id === "_actions";
                        return (
                          <td
                            key={cell.id}
                            style={{
                              padding: cellPadding,
                              ...(isActionsColumn ? { width: "60px" } : {}),
                              ...(isPinned
                                ? {
                                    position: "sticky" as const,
                                    left: `${cell.column.getStart("start")}px`,
                                    // Match the row's own stripe so scrolled-
                                    // under content doesn't show through.
                                    background: rowBackground,
                                    // Stays above other (unpinned) cells in
                                    // this row as they scroll horizontally
                                    // underneath, but never exceeds the
                                    // sticky header row's own zIndex of 1.
                                    zIndex: 1,
                                  }
                                : {}),
                            }}
                          >
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
                {paddingBottom > 0 && (
                  <tr>
                    <td colSpan={tableColumns.length} style={{ height: `${paddingBottom}px` }} />
                  </tr>
                )}
              </>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
