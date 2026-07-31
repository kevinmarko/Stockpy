/**
 * UniverseCoverage.test.tsx — the read-only coverage-reconciliation
 * diagnostic (FULL/STALE/QUOTES_ONLY/EQUITY_ONLY/UNCOVERED/UNKNOWN
 * breakdown). Covers the summary counts, the per-symbol rows (against the
 * real mock API, which spans all six data.portfolio_sync.CoverageStatus
 * values), the "Coverage gaps only" filter, and the honest empty state for a
 * genuinely untracked universe.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UniverseCoverage } from "./UniverseCoverage";
import { api, ApiError } from "../api/client";
import type { SyncReportResponse } from "../api/types";

describe("UniverseCoverage (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders summary counts and per-symbol rows", async () => {
    render(<UniverseCoverage />);
    // universe-coverage (the outer wrapper) mounts immediately -- Sync Now
    // stays visible through the initial load -- so wait for actual data
    // (a real row), not just the wrapper, before asserting on content.
    expect(await screen.findByTestId("universe-coverage-row-AAPL")).toBeInTheDocument();
    // The mock fixture has DUK (equity_only) and T (uncovered) among its gaps.
    expect(screen.getByTestId("universe-coverage-row-DUK")).toBeInTheDocument();
  });

  it("renders a row for every one of the six coverage states", async () => {
    render(<UniverseCoverage />);
    await screen.findByTestId("universe-coverage-row-AAPL");
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
    // Sync Now stays available even with nothing tracked yet -- that's
    // exactly the moment an operator would want to click it.
    expect(screen.getByTestId("universe-sync-now")).toBeInTheDocument();
  });

  it("an API error renders ErrorState with a retry, not a crash", async () => {
    vi.spyOn(api, "getSyncReport").mockRejectedValue(new Error("boom"));
    render(<UniverseCoverage />);
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });

  it("clicking a row expands the extended per-symbol detail (Held/Qty/Avg cost/Δ/Stale/Source/Forecast/Fundamentals/Lists)", async () => {
    render(<UniverseCoverage />);
    await screen.findByTestId("universe-coverage-row-AAPL");
    expect(screen.queryByTestId("universe-coverage-detail-AAPL")).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId("universe-coverage-toggle-AAPL"));
    const detail = await screen.findByTestId("universe-coverage-detail-AAPL");
    expect(detail).toHaveTextContent("Held: ✓");
    expect(detail).toHaveTextContent(/Qty:/);
    expect(detail).toHaveTextContent(/Avg cost:/);
    expect(detail).toHaveTextContent(/Δ\/share:/);
    expect(detail).toHaveTextContent(/Source:/);
    expect(detail).toHaveTextContent(/Forecast:/);
    expect(detail).toHaveTextContent(/Fundamentals:/);
    expect(detail).toHaveTextContent(/Lists:/);
  });

  it("an unheld symbol's detail shows Held: ✗ and an honest Qty dash, not a fabricated 0", async () => {
    render(<UniverseCoverage />);
    await screen.findByTestId("universe-coverage-row-T");
    await userEvent.click(screen.getByTestId("universe-coverage-toggle-T"));
    const detail = await screen.findByTestId("universe-coverage-detail-T");
    expect(detail).toHaveTextContent("Held: ✗");
    expect(detail).toHaveTextContent("Qty: —");
  });

  it("Sync Now calls POST /data/sync and reloads the coverage report on success", async () => {
    const syncReportSpy = vi.spyOn(api, "getSyncReport");
    const postSyncSpy = vi.spyOn(api, "postDataSync").mockResolvedValue({
      report: await api.getSyncReport(),
      default_tickers: ["AAPL", "MSFT"],
      applies: "next_daemon_restart",
      note: "Synced 2 symbol(s). Submitted to DEFAULT_TICKERS in .env; effective on next daemon restart.",
    });
    render(<UniverseCoverage />);
    await screen.findByTestId("universe-coverage-row-AAPL");
    const callsBefore = syncReportSpy.mock.calls.length;
    await userEvent.click(screen.getByTestId("universe-sync-now"));
    await waitFor(() => expect(postSyncSpy).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId("universe-sync-message")).toHaveTextContent(/Synced 2 symbol/);
    // A reload (fresh GET /data/sync-report) followed the successful sync.
    await waitFor(() => expect(syncReportSpy.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  it("Sync Now surfaces a disabled-flag 403 as an honest inline message, not a crash", async () => {
    vi.spyOn(api, "postDataSync").mockRejectedValue(
      new ApiError("Universe sync is disabled (UNIVERSE_SYNC_ENABLED=false).", 403),
    );
    render(<UniverseCoverage />);
    await screen.findByTestId("universe-coverage-row-AAPL");
    await userEvent.click(screen.getByTestId("universe-sync-now"));
    expect(await screen.findByTestId("universe-sync-message")).toHaveTextContent(/disabled/);
  });
});
