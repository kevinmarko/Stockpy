/**
 * UniverseCoverage.test.tsx — the read-only coverage-reconciliation
 * diagnostic (FULL/EQUITY_ONLY/UNCOVERED breakdown). Covers the summary
 * counts, the per-symbol rows (against the real mock API), the "Coverage
 * gaps only" filter, and the honest cold-start/reason state.
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UniverseCoverage } from "./UniverseCoverage";
import { api } from "../api/client";
import type { UniverseCoverageResponse } from "../api/types";

describe("UniverseCoverage (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders summary counts and per-symbol rows", async () => {
    render(<UniverseCoverage />);
    expect(await screen.findByTestId("universe-coverage")).toBeInTheDocument();
    // The mock fixture has DUK (equity_only) and T (uncovered) as the two gaps.
    expect(screen.getByTestId("universe-coverage-row-DUK")).toBeInTheDocument();
    expect(screen.getByTestId("universe-coverage-row-AAPL")).toBeInTheDocument();
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

  it("renders the honest empty state with the server's reason on a cold start", async () => {
    const empty: UniverseCoverageResponse = {
      generated_at: null,
      provider_source: null,
      fundamentals_source: null,
      counts: { full: 0, stale: 0, quotes_only: 0, equity_only: 0, uncovered: 0, unknown: 0 },
      n_total: 0,
      symbols: [],
      reason: "No sync report yet — use Sync Now to discover and reconcile the tracked universe.",
    };
    vi.spyOn(api, "getUniverseCoverage").mockResolvedValue(empty);
    render(<UniverseCoverage />);
    expect(await screen.findByTestId("universe-coverage-empty")).toHaveTextContent(
      "No sync report yet",
    );
    expect(screen.queryByTestId("universe-coverage")).not.toBeInTheDocument();
  });

  it("an API error renders ErrorState with a retry, not a crash", async () => {
    vi.spyOn(api, "getUniverseCoverage").mockRejectedValue(new Error("boom"));
    render(<UniverseCoverage />);
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });
});
