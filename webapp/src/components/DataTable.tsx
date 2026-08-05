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
  ExpandedState,
  ColumnDef,
  filterFn_includesString,
  columnVisibilityFeature,
} from "@tanstack/react-table";
import { useDensity } from "./DensityContext";

export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => React.ReactNode;
  sortable?: boolean;
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
          Showing {rows.length} records
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
                {headerGroup.headers.map((header) => (
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
                    }}
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {{
                      asc: " ▲",
                      desc: " ▼",
                    }[header.column.getIsSorted() as string] ?? null}
                  </th>
                ))}
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
                  
                  return (
                    <tr
                      key={row.id}
                      onClick={() => onRowClick && onRowClick(row.original)}
                      style={{
                        borderBottom: "1px solid var(--border)",
                        background: idx % 2 === 0 ? "var(--surface)" : "var(--surface-2)",
                        cursor: onRowClick ? "pointer" : "default",
                      }}
                    >
                      {row.getVisibleCells().map((cell) => {
                        if (cell.getIsGrouped() || cell.getIsPlaceholder()) {
                          return null;
                        }
                        return (
                          <td key={cell.id} style={{ padding: cellPadding }}>
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
