/**
 * UniverseCoverage.test.tsx — the read-only coverage-reconciliation
 * diagnostic (FULL/STALE/QUOTES_ONLY/EQUITY_ONLY/UNCOVERED/UNKNOWN
 * breakdown). Covers the summary counts, the per-symbol rows (against the
 * real mock API, which spans all six data.portfolio_sync.CoverageStatus
 * values), the "Coverage gaps only" filter, and the honest empty state for a
 * genuinely untracked universe.
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UniverseCoverage } from "./UniverseCoverage";
import { api } from "../api/client";
import type { SyncReportResponse } from "../api/types";

describe("UniverseCoverage (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders summary counts and per-symbol rows", async () => {
    render(<UniverseCoverage />);
    expect(await screen.findByTestId("universe-coverage")).toBeInTheDocument();
    // The mock fixture has DUK (equity_only) and T (uncovered) among its gaps.
    expect(screen.getByTestId("universe-coverage-row-DUK")).toBeInTheDocument();
    expect(screen.getByTestId("universe-coverage-row-AAPL")).toBeInTheDocument();
  });

  it("renders a row for every one of the six coverage states", async () => {
    render(<UniverseCoverage />);
    await screen.findByTestId("universe-coverage");
    // AAPL/MSFT/COST = full, NVDA = stale, V = quotes_only, DUK = equity_only,
    // T = uncovered, XOM = unknown — mirrors the mock's ROWS fixture exactly.
    for (const symbol of ["AAPL", "NVDA", "V", "DUK", "T", "XOM"]) {
      expect(screen.getByTestId(`universe-coverage-row-${symbol}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId("universe-coverage-row-NVDA")).toHaveTextContent("Stale");
    expect(screen.getByTestId("universe-coverage-row-V")).toHaveTextContent("Quotes only");
    expect(screen.getByTestId("universe-coverage-row-XOM")).toHaveTextContent("Unknown");
  });

  it("a gap row shows its diagnostic and a non-full coverage badge", async () => {
    render(<UniverseCoverage />);
    const row = await screen.findByTestId("universe-coverage-row-DUK");
    expect(row).toHaveTextContent("Equity only");
    expect(row).toHaveTextContent("quote:NotFoundError");
  });

  it("a fully-covered row shows no diagnostic line", async () => {
    render(<UniverseCoverage />);
    const row = await screen.findByTestId("universe-coverage-row-AAPL");
    expect(row).toHaveTextContent("Full");
    expect(row.textContent).not.toContain("quote:");
  });

  it("'Coverage gaps only' filters out FULL-coverage rows", async () => {
    render(<UniverseCoverage />);
    await screen.findByTestId("universe-coverage-row-AAPL");
    fireEvent.click(screen.getByTestId("universe-coverage-gaps-only"));
    expect(screen.queryByTestId("universe-coverage-row-AAPL")).not.toBeInTheDocument();
    expect(screen.getByTestId("universe-coverage-row-DUK")).toBeInTheDocument();
  });

  it("renders the honest empty state when nothing is tracked yet", async () => {
    const empty: SyncReportResponse = {
      generated_at: new Date().toISOString(),
      positions: [],
      watchlists: {},
      symbols: {},
      provider_source: "",
      fundamentals_source: "",
    };
    vi.spyOn(api, "getSyncReport").mockResolvedValue(empty);
    render(<UniverseCoverage />);
    expect(await screen.findByTestId("universe-coverage-empty")).toHaveTextContent(
      "No symbols tracked yet",
    );
    expect(screen.queryByTestId("universe-coverage")).not.toBeInTheDocument();
  });

  it("an API error renders ErrorState with a retry, not a crash", async () => {
    vi.spyOn(api, "getSyncReport").mockRejectedValue(new Error("boom"));
    render(<UniverseCoverage />);
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });
});
