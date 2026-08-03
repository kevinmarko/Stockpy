import React, { useState, useMemo } from "react";
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
  pageSize?: number;
  debounceMs?: number;
}

export function DataTable<T extends Record<string, any>>({
  data,
  columns,
  groupByKey,
  onRowClick,
  copyableJson = true,
  pageSize = 50,
  debounceMs = 0,
}: DataTableProps<T>) {
  const { density } = useDensity();
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, debounceMs);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const [page, setPage] = useState(1);

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

  const totalPages = Math.max(1, Math.ceil(sortedData.length / pageSize));
  const paginatedData = useMemo(() => {
    if (groupByKey || !pageSize) return sortedData;
    const start = (page - 1) * pageSize;
    return sortedData.slice(start, start + pageSize);
  }, [sortedData, page, pageSize, groupByKey]);

  // Grouped logic if groupByKey is provided
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

  const toggleGroup = (key: string) => {
    setExpandedGroups((prev) => ({ ...prev, [key]: !(prev[key] ?? true) }));
  };

  const copyRowJson = (row: T, e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(JSON.stringify(row, null, 2));
    alert("Copied event context as JSON to clipboard!");
  };

  const cellPadding = density === "compact" ? "var(--s-1-5) var(--s-2-5)" : "var(--s-3) var(--s-4)";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
      {/* Search Filter input */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <input
          type="text"
          placeholder="Filter data..."
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
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
          Showing {sortedData.length} records {totalPages > 1 && `(Page ${page} of ${totalPages})`}
        </span>
      </div>

      <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: "var(--r-sm)" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "var(--t-body)" }}>
          <thead>
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
            {groups ? (
              Array.from(groups.entries()).map(([groupName, groupRows]) => {
                const isExpanded = expandedGroups[groupName] ?? true;
                return (
                  <React.Fragment key={groupName}>
                    <tr
                      onClick={() => toggleGroup(groupName)}
                      style={{ background: "var(--surface-3)", cursor: "pointer", borderBottom: "1px solid var(--border)" }}
                    >
                      <td colSpan={columns.length + (copyableJson ? 1 : 0)} style={{ padding: cellPadding, fontWeight: 700 }}>
                        {isExpanded ? "▼" : "▶"} {groupName} ({groupRows.length} events)
                      </td>
                    </tr>
                    {isExpanded &&
                      groupRows.map((row, idx) => (
                        <tr
                          key={idx}
                          onClick={() => onRowClick && onRowClick(row)}
                          style={{
                            borderBottom: "1px solid var(--border)",
                            background: "var(--surface)",
                            cursor: onRowClick ? "pointer" : "default",
                          }}
                        >
                          {columns.map((col) => (
                            <td key={col.key} style={{ padding: cellPadding }}>
                              {col.render ? col.render(row) : row[col.key]}
                            </td>
                          ))}
                          {copyableJson && (
                            <td style={{ padding: cellPadding }}>
                              <button
                                onClick={(e) => copyRowJson(row, e)}
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
                      ))}
                  </React.Fragment>
                );
              })
            ) : paginatedData.length === 0 ? (
              <tr>
                <td colSpan={columns.length + (copyableJson ? 1 : 0)} style={{ padding: "var(--s-4)", textAlign: "center", color: "var(--text-muted)" }}>
                  No matching records found.
                </td>
              </tr>
            ) : (
              paginatedData.map((row, idx) => (
                <tr
                  key={idx}
                  onClick={() => onRowClick && onRowClick(row)}
                  style={{
                    borderBottom: "1px solid var(--border)",
                    background: idx % 2 === 0 ? "var(--surface)" : "var(--surface-2)",
                    cursor: onRowClick ? "pointer" : "default",
                  }}
                >
                  {columns.map((col) => (
                    <td key={col.key} style={{ padding: cellPadding }}>
                      {col.render ? col.render(row) : row[col.key]}
                    </td>
                  ))}
                  {copyableJson && (
                    <td style={{ padding: cellPadding }}>
                      <button
                        onClick={(e) => copyRowJson(row, e)}
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
              ))
            )}
          </tbody>
        </table>
      </div>

      {!groupByKey && totalPages > 1 && (
        <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: "var(--s-2)" }}>
          <button
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            style={{
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              borderRadius: "var(--r-xs)",
              color: "var(--text-primary)",
              padding: "var(--s-1) var(--s-3)",
              cursor: page <= 1 ? "not-allowed" : "pointer",
              opacity: page <= 1 ? 0.5 : 1,
            }}
          >
            Previous
          </button>
          <span style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>
            Page {page} of {totalPages}
          </span>
          <button
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            style={{
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              borderRadius: "var(--r-xs)",
              color: "var(--text-primary)",
              padding: "var(--s-1) var(--s-3)",
              cursor: page >= totalPages ? "not-allowed" : "pointer",
              opacity: page >= totalPages ? 0.5 : 1,
            }}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
