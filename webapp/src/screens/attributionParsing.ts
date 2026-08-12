import type { BrinsonFachlerRow } from "../api/types";

// ---------------------------------------------------------------------------
// Paste-from-spreadsheet -- a TS port of the legacy Streamlit Command
// Center's `parse_pasted_sector_matrix` (gui/report_viewer_helpers.py). Same
// contract, deliberately: delimiter auto-detected from the FIRST line only
// (tab if present, else comma); the header row is OPTIONAL, detected by
// sniffing whether the first line's 4 numeric-column cells all parse as
// numbers (if so, the line is data, not a header); an exact-match header
// (case-insensitive) reorders columns, any non-exact-match header falls back
// to positional order -- there is no fuzzy/partial alias matching, mirroring
// the Python "all 5 canonical names present, or none of them count" rule.
// Sector names are free text, NOT validated against the fixed GICS-11 list
// (the Python version doesn't either); a non-numeric cell coerces to 0
// rather than rejecting the whole paste (CONSTRAINT #4/#6 shape: a bad CELL
// degrades, a bad SHAPE errors). Blank-sector rows are dropped silently. Row
// count is not constrained to 11 -- fewer or more are both accepted.
//
// Split out of Attribution.tsx (a pure helper, no React) so that file only
// exports the `Attribution` component -- keeps Vite's React Fast Refresh
// working there instead of invalidating on every edit.
const BF_CANONICAL_HEADERS: Record<string, keyof BrinsonFachlerRow> = {
  sector: "sector",
  "portfolio weight (%)": "portfolio_weight_pct",
  "portfolio return (%)": "portfolio_return_pct",
  "benchmark weight (%)": "benchmark_weight_pct",
  "benchmark return (%)": "benchmark_return_pct",
};
const BF_POSITIONAL_ORDER: (keyof BrinsonFachlerRow)[] = [
  "sector",
  "portfolio_weight_pct",
  "portfolio_return_pct",
  "benchmark_weight_pct",
  "benchmark_return_pct",
];

function parseNumericPasteCell(raw: string): number {
  const n = Number(raw.trim().replace(/%/g, ""));
  return Number.isFinite(n) ? n : 0;
}

/** Splits on the SAME delimiter throughout (sniffed once from the first
 * line), so a file with tabs on line 1 but commas in later data rows is not
 * specially handled -- matches the Python original's behavior exactly. */
function splitPasteLine(line: string, delimiter: string): string[] {
  return line.split(delimiter).map((c) => c.trim());
}

export function parsePastedSectorMatrix(text: string): BrinsonFachlerRow[] {
  if (!text || !text.trim()) {
    throw new Error("Pasted text is empty.");
  }
  const lines = text
    .trim()
    .split(/\r\n|\r|\n/)
    .filter((l) => l.trim().length > 0);
  if (lines.length === 0) {
    throw new Error("Pasted text is empty.");
  }

  const delimiter = lines[0].includes("\t") ? "\t" : ",";
  const firstCells = splitPasteLine(lines[0], delimiter);

  // Header-vs-data sniff: if the first line's 4 numeric-column cells (index
  // 1-4) ALL parse as numbers, treat the first line as data, not a header.
  let hasHeader = true;
  if (firstCells.length >= 5) {
    const numericCells = firstCells.slice(1, 5).map((c) => c.replace(/%/g, ""));
    hasHeader = !numericCells.every((c) => c !== "" && Number.isFinite(Number(c)));
  }

  const expectedColumnCount = hasHeader ? firstCells.length : 5;
  if (expectedColumnCount !== 5) {
    throw new Error(
      `Expected 5 columns (Sector, P-Weight, P-Return, B-Weight, B-Return); got ${expectedColumnCount}.`
    );
  }

  // Column order: an EXACT match (case-insensitive) on all 5 canonical
  // header names reorders columns; anything else (including a partial
  // match) falls back to positional order -- the header line is consumed
  // either way once hasHeader was decided above, never re-treated as data.
  let columnOrder = BF_POSITIONAL_ORDER;
  if (hasHeader) {
    const mapped = firstCells.map((c) => BF_CANONICAL_HEADERS[c.toLowerCase().trim()]);
    if (mapped.every((m) => m !== undefined)) {
      columnOrder = mapped as (keyof BrinsonFachlerRow)[];
    }
  }

  const dataLines = hasHeader ? lines.slice(1) : lines;
  const rows: BrinsonFachlerRow[] = [];
  for (const line of dataLines) {
    const cells = splitPasteLine(line, delimiter);
    if (cells.length !== 5) {
      throw new Error(
        `Expected 5 columns (Sector, P-Weight, P-Return, B-Weight, B-Return); got ${cells.length}.`
      );
    }
    const row: Partial<Record<keyof BrinsonFachlerRow, string | number>> = {};
    columnOrder.forEach((field, i) => {
      row[field] = field === "sector" ? cells[i].trim() : parseNumericPasteCell(cells[i]);
    });
    if (!row.sector) continue; // blank-sector rows dropped silently
    rows.push(row as unknown as BrinsonFachlerRow);
  }

  if (rows.length === 0) {
    throw new Error("No data rows found after parsing.");
  }
  return rows;
}
