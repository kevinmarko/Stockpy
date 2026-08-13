import React, { useState, useMemo, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useDensity } from "./DensityContext";
import { useDebounce } from "../hooks/useDebounce";

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
  debounceMs?: number;
}

type ListItem<T> =
  | { type: "group"; groupName: string; count: number }
  | { type: "row"; row: T; index: number; bg: string };

export function DataTable<T extends Record<string, any>>({
  data,
  columns,
  groupByKey,
  onRowClick,
  copyableJson = true,
  debounceMs = 0,
}: DataTableProps<T>) {
  const { density } = useDensity();
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, debounceMs);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});

  const parentRef = useRef<HTMLDivElement>(null);

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const filteredData = useMemo(() => {
    if (!debouncedSearch.trim()) return data;
    const q = debouncedSearch.toLowerCase();
    return data.filter((row) =>
      Object.values(row).some((val) => String(val).toLowerCase().includes(q))
    );
  }, [data, debouncedSearch]);

  const sortedData = useMemo(() => {
    if (!sortKey) return filteredData;
    return [...filteredData].sort((a, b) => {
      const valA = a[sortKey];
      const valB = b[sortKey];
      if (valA < valB) return sortDir === "asc" ? -1 : 1;
      if (valA > valB) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
  }, [filteredData, sortKey, sortDir]);

  const groups = useMemo(() => {
    if (!groupByKey) return null;
    const map = new Map<string, T[]>();
    sortedData.forEach((row) => {
      const keyVal = String(row[groupByKey] || "Other");
      if (!map.has(keyVal)) map.set(keyVal, []);
      map.get(keyVal)!.push(row);
    });
    return map;
  }, [sortedData, groupByKey]);

  const items = useMemo<ListItem<T>[]>(() => {
    if (groups) {
      const list: ListItem<T>[] = [];
      Array.from(groups.entries()).forEach(([groupName, groupRows]) => {
        list.push({ type: "group", groupName, count: groupRows.length });
        if (expandedGroups[groupName] ?? true) {
          groupRows.forEach((row, idx) => {
            list.push({ type: "row", row, index: idx, bg: "var(--surface)" });
          });
        }
      });
      return list;
    } else {
      return sortedData.map((row, idx) => ({
        type: "row",
        row,
        index: idx,
        bg: idx % 2 === 0 ? "var(--surface)" : "var(--surface-2)",
      }));
    }
  }, [groups, sortedData, expandedGroups]);

  const rowVirtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => (density === "compact" ? 32 : 48),
    overscan: 10,
  });

  const toggleGroup = (key: string) => {
    setExpandedGroups((prev) => ({ ...prev, [key]: !(prev[key] ?? true) }));
  };

  const copyRowJson = (row: T, e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(JSON.stringify(row, null, 2));
    alert("Copied event context as JSON to clipboard!");
  };

  const cellPadding = density === "compact" ? "var(--s-1-5) var(--s-2-5)" : "var(--s-3) var(--s-4)";
  const virtualItems = rowVirtualizer.getVirtualItems();

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
      {/* Search Filter input */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <input
          type="text"
          placeholder="Filter data..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
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
          Showing {sortedData.length} records
        </span>
      </div>

      <div
        ref={parentRef}
        style={{
          maxHeight: "600px",
          overflow: "auto",
          border: "1px solid var(--border)",
          borderRadius: "var(--r-sm)",
        }}
      >
        <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "var(--t-body)" }}>
          <thead style={{ position: "sticky", top: 0, zIndex: 1 }}>
            <tr style={{ background: "var(--surface-2)", borderBottom: "1px solid var(--border)" }}>
              {columns.map((col) => (
                <th
                  key={col.key}
                  onClick={() => col.sortable !== false && handleSort(col.key)}
                  style={{
                    padding: cellPadding,
                    fontWeight: 700,
                    color: "var(--text-secondary)",
                    fontSize: "var(--t-caption)",
                    cursor: col.sortable !== false ? "pointer" : "default",
                    userSelect: "none",
                  }}
                >
                  {col.header} {sortKey === col.key ? (sortDir === "asc" ? "▲" : "▼") : ""}
                </th>
              ))}
              {copyableJson && <th style={{ padding: cellPadding, width: "60px" }}>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {virtualItems.length > 0 && (
              <tr>
                <td style={{ height: `${virtualItems[0].start}px`, padding: 0 }} colSpan={columns.length + (copyableJson ? 1 : 0)} />
              </tr>
            )}
            
            {items.length === 0 && (
              <tr>
                <td colSpan={columns.length + (copyableJson ? 1 : 0)} style={{ padding: "var(--s-4)", textAlign: "center", color: "var(--text-muted)" }}>
                  No matching records found.
                </td>
              </tr>
            )}

            {virtualItems.map((virtualRow) => {
              const item = items[virtualRow.index];
              
              if (item.type === "group") {
                const isExpanded = expandedGroups[item.groupName] ?? true;
                return (
                  <tr
                    key={virtualRow.key}
                    data-index={virtualRow.index}
                    ref={rowVirtualizer.measureElement}
                    onClick={() => toggleGroup(item.groupName)}
                    style={{ background: "var(--surface-3)", cursor: "pointer", borderBottom: "1px solid var(--border)" }}
                  >
                    <td colSpan={columns.length + (copyableJson ? 1 : 0)} style={{ padding: cellPadding, fontWeight: 700 }}>
                      {isExpanded ? "▼" : "▶"} {item.groupName} ({item.count} events)
                    </td>
                  </tr>
                );
              }

              // Row item
              return (
                <tr
                  key={virtualRow.key}
                  data-index={virtualRow.index}
                  ref={rowVirtualizer.measureElement}
                  onClick={() => onRowClick && onRowClick(item.row)}
                  style={{
                    borderBottom: "1px solid var(--border)",
                    background: item.bg,
                    cursor: onRowClick ? "pointer" : "default",
                  }}
                >
                  {columns.map((col) => (
                    <td key={col.key} style={{ padding: cellPadding }}>
                      {col.render ? col.render(item.row) : item.row[col.key]}
                    </td>
                  ))}
                  {copyableJson && (
                    <td style={{ padding: cellPadding }}>
                      <button
                        onClick={(e) => copyRowJson(item.row, e)}
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
                    </td>
                  )}
                </tr>
              );
            })}
            
            {virtualItems.length > 0 && (
              <tr>
                <td
                  style={{ height: `${rowVirtualizer.getTotalSize() - virtualItems[virtualItems.length - 1].end}px`, padding: 0 }}
                  colSpan={columns.length + (copyableJson ? 1 : 0)}
                />
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
