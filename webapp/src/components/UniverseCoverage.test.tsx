/**
 * UniverseCoverage.test.tsx — the read-only coverage-reconciliation
 * diagnostic (FULL/STALE/QUOTES_ONLY/EQUITY_ONLY/UNCOVERED/UNKNOWN
 * breakdown). Covers the summary counts, the per-symbol rows (against the
 * real mock API, which spans all six data.portfolio_sync.CoverageStatus
 * values), the "Coverage gaps only" filter, and the honest empty state for a
 * genuinely untracked universe.
 *
 * This file also touches Node's fs/path APIs for ONE regression test (the
 * source-scan guard at the bottom) -- see theme.test.ts /
 * AutoRefreshContext.test.tsx for the same pattern and rationale. The app's
 * tsconfig deliberately keeps Node globals out of browser code via an
 * explicit `types` allowlist, so pull the node types in for THIS FILE ONLY
 * via a reference directive.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UniverseCoverage } from "./UniverseCoverage";
import { AutoRefreshProvider } from "./AutoRefreshContext";
import { api, ApiError } from "../api/client";
import type { SyncReportResponse } from "../api/types";

/**
 * `GET /data/sync-report` can trigger a real Robinhood login when the cached
 * snapshot is stale, so `UniverseCoverage` no longer fetches unconditionally
 * on mount (see the "idle vs. live" describe block below) -- it only does so
 * once the "robinhood" auto-refresh category is on. These tests exercise the
 * SAME live-view behavior the component always had, so they render with that
 * category seeded on, matching what a real operator who has opted in sees.
 */
function renderLive() {
  return render(
    <AutoRefreshProvider>
      <UniverseCoverage />
    </AutoRefreshProvider>
  );
}

describe("UniverseCoverage (real mock API)", () => {
  beforeEach(() => {
    localStorage.setItem("stockpy.auto_refresh.robinhood_enabled", "1");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("renders summary counts and per-symbol rows", async () => {
    renderLive();
    // universe-coverage (the outer wrapper) mounts immediately -- Sync Now
    // stays visible through the initial load -- so wait for actual data
    // (a real row), not just the wrapper, before asserting on content.
    expect(await screen.findByTestId("universe-coverage-row-AAPL")).toBeInTheDocument();
    // The mock fixture has DUK (equity_only) and T (uncovered) among its gaps.
    expect(screen.getByTestId("universe-coverage-row-DUK")).toBeInTheDocument();
  });

  it("renders a row for every one of the six coverage states", async () => {
    renderLive();
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
    renderLive();
    const row = await screen.findByTestId("universe-coverage-row-DUK");
    expect(row).toHaveTextContent("Equity only");
    expect(row).toHaveTextContent("quote:NotFoundError");
  });

  it("a fully-covered row shows no diagnostic line", async () => {
    renderLive();
    const row = await screen.findByTestId("universe-coverage-row-AAPL");
    expect(row).toHaveTextContent("Full");
    expect(row.textContent).not.toContain("quote:");
  });

  it("'Coverage gaps only' filters out FULL-coverage rows", async () => {
    renderLive();
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
    renderLive();
    expect(await screen.findByTestId("universe-coverage-empty")).toHaveTextContent(
      "No symbols tracked yet",
    );
    // Sync Now stays available even with nothing tracked yet -- that's
    // exactly the moment an operator would want to click it.
    expect(screen.getByTestId("universe-sync-now")).toBeInTheDocument();
  });

  it("an API error renders ErrorState with a retry, not a crash", async () => {
    vi.spyOn(api, "getSyncReport").mockRejectedValue(new Error("boom"));
    renderLive();
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });

  it("clicking a row expands the extended per-symbol detail (Held/Qty/Avg cost/Δ/Stale/Source/Forecast/Fundamentals/Lists)", async () => {
    renderLive();
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
    renderLive();
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
    renderLive();
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
    renderLive();
    await screen.findByTestId("universe-coverage-row-AAPL");
    await userEvent.click(screen.getByTestId("universe-sync-now"));
    expect(await screen.findByTestId("universe-sync-message")).toHaveTextContent(/disabled/);
  });
});

describe("UniverseCoverage — idle by default (Robinhood category gate)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("does not call GET /data/sync-report on mount when the robinhood category is off (default)", async () => {
    const spy = vi.spyOn(api, "getSyncReport");
    render(
      <AutoRefreshProvider>
        <UniverseCoverage />
      </AutoRefreshProvider>
    );
    expect(await screen.findByTestId("universe-coverage-idle")).toHaveTextContent(
      "Coverage report not loaded"
    );
    expect(screen.getByTestId("universe-coverage-load")).toBeInTheDocument();
    // Sync Now stays reachable from the idle view too.
    expect(screen.getByTestId("universe-sync-now")).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it("clicking 'Load coverage report' fetches exactly once and renders the live view", async () => {
    const spy = vi.spyOn(api, "getSyncReport");
    render(
      <AutoRefreshProvider>
        <UniverseCoverage />
      </AutoRefreshProvider>
    );
    await screen.findByTestId("universe-coverage-idle");
    expect(spy).not.toHaveBeenCalled();

    await userEvent.click(screen.getByTestId("universe-coverage-load"));

    expect(await screen.findByTestId("universe-coverage-row-AAPL")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe("UniverseCoverage / AgenticTrading / Settings — auto-poll safety source guard", () => {
  /**
   * `POST /brokerage/refresh` (refreshBrokerage) always performs a real,
   * live Robinhood login; `POST /data/sync` (postDataSync) is manual-only
   * and mutates `DEFAULT_TICKERS`. Neither must ever be auto-polled — this
   * is a plain source scan, not a rendering test, so it also catches a
   * regression introduced in a file this test doesn't otherwise render.
   */
  function readSource(relativePath: string): string {
    return readFileSync(resolve(process.cwd(), relativePath), "utf-8");
  }

  /** Every `useAutoPoll(...)` call's own argument text, balanced-paren-matched
   *  (a naive regex would stop at the first inner `)`, e.g. inside an arrow
   *  function body). */
  function useAutoPollCallBodies(src: string): string[] {
    const bodies: string[] = [];
    const marker = "useAutoPoll(";
    let searchFrom = 0;
    for (;;) {
      const start = src.indexOf(marker, searchFrom);
      if (start === -1) break;
      let depth = 1;
      let i = start + marker.length;
      for (; i < src.length && depth > 0; i++) {
        if (src[i] === "(") depth++;
        else if (src[i] === ")") depth--;
      }
      bodies.push(src.slice(start + marker.length, i - 1));
      searchFrom = i;
    }
    return bodies;
  }

  it("never passes refreshBrokerage or postDataSync into a useAutoPoll(...) call", () => {
    const files = [
      // Settings.tsx was split into SettingsLayout + these sub-screens --
      // scan every one of them, since any could grow a useAutoPoll(...) call.
      "src/screens/SettingsGeneral.tsx",
      "src/screens/SettingsData.tsx",
      "src/screens/SettingsUniverse.tsx",
      "src/screens/SettingsBrokers.tsx",
      "src/screens/SettingsModules.tsx",
      "src/screens/AgenticTrading.tsx",
      "src/components/UniverseCoverage.tsx",
    ];
    for (const file of files) {
      const src = readSource(file);
      const bodies = useAutoPollCallBodies(src);
      for (const body of bodies) {
        expect(body, `${file}: a useAutoPoll(...) call body must never reference refreshBrokerage`).not.toContain(
          "refreshBrokerage"
        );
        expect(body, `${file}: a useAutoPoll(...) call body must never reference postDataSync`).not.toContain(
          "postDataSync"
        );
      }
    }
  });
});
