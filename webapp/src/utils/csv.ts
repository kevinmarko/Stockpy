/**
 * csv.ts — client-side CSV export. No backend endpoint: every call site
 * already has the rows in hand (fetched JSON already rendered on screen), so
 * this just serializes them to a CSV string and triggers a browser download
 * via a Blob + a synthetic `<a download>` click. Mirrors Streamlit's
 * `st.download_button(..., data=pd.DataFrame(...).to_csv(index=False))`
 * pattern for "Export latest signals (CSV)" / "Export decision log (CSV)".
 */

/** RFC 4180-ish escaping: wrap in quotes and double any embedded quote
 * whenever the value contains a comma, quote, or newline. `null`/`undefined`
 * become an empty cell (never the literal string "null"/"undefined"). */
function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const s = typeof value === "string" ? value : String(value);
  if (/[",\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

/**
 * Serialize an array of flat objects to a CSV string. `columns` fixes the
 * column order and header labels explicitly — deliberately not inferred from
 * `Object.keys(rows[0])`, so column order stays stable even when a row's
 * fields are sparse (e.g. a `null` value omitted by `JSON.stringify` upstream
 * would otherwise silently shift columns for that row alone).
 */
export function toCsv<T>(
  rows: T[],
  columns: { key: keyof T; header: string }[]
): string {
  const header = columns.map((c) => csvCell(c.header)).join(",");
  const body = rows.map((row) =>
    columns.map((c) => csvCell(row[c.key])).join(",")
  );
  return [header, ...body].join("\r\n");
}

/** Trigger a browser download of `content` as `filename`. No network call —
 * a Blob URL is created, clicked via a synthetic, off-DOM `<a>`, then
 * revoked. Safe to call from any click handler (no async gap that could
 * trip a popup blocker). Exported (not just used by `exportCsv` below) so
 * other already-fetched-content downloads — e.g. ReportLibrary.tsx's report
 * files — share the same primitive instead of re-implementing it. */
export function downloadBlob(content: string, filename: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/** Build a CSV from `rows`/`columns` and download it as `filename` (a
 * `.csv` suffix is appended if the caller didn't already include one). */
export function exportCsv<T>(
  rows: T[],
  columns: { key: keyof T; header: string }[],
  filename: string
): void {
  const csv = toCsv(rows, columns);
  const name = filename.toLowerCase().endsWith(".csv") ? filename : `${filename}.csv`;
  downloadBlob(csv, name, "text/csv;charset=utf-8;");
}
