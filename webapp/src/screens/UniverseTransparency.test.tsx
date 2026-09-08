import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { UniverseTransparency } from "./UniverseTransparency";
import { ExplainTickerProvider } from "../context/ExplainTickerContext";
import { ExplainTickerDrawer } from "../components/ExplainTickerDrawer";
import { AutoRefreshProvider } from "../components/AutoRefreshContext";
import { api } from "../api/client";

/**
 * `GET /data/sync-report` can trigger a real Robinhood login when the cached
 * snapshot is stale (see CLAUDE.md's ROBINHOOD_AUTO_REFRESH_ENABLED bullet),
 * so `UniverseTransparency` -- like its sibling `UniverseCoverage.tsx` --
 * does not fetch unconditionally on mount; it only does so once the
 * "robinhood" auto-refresh category is on. These tests exercise the live
 * view's existing behavior, so they render with that category seeded on,
 * matching what an operator who has opted in sees (mirrors
 * `UniverseCoverage.test.tsx`'s identical `renderLive()` convention).
 */
function renderUniverseTransparency() {
  return render(
    <AutoRefreshProvider>
      <MemoryRouter>
        <ExplainTickerProvider>
          <UniverseTransparency />
          <ExplainTickerDrawer />
        </ExplainTickerProvider>
      </MemoryRouter>
    </AutoRefreshProvider>
  );
}

describe("UniverseTransparency screen", () => {
  beforeEach(() => {
    localStorage.setItem("stockpy.auto_refresh.robinhood_enabled", "1");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("renders header, provenance data, and 6 KPI summary cards", async () => {
    renderUniverseTransparency();

    // Title & provenance (awaiting async data resolution)
    expect(await screen.findByText(/Quotes Source:/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Universe Transparency" })).toBeInTheDocument();
    expect(screen.getByText(/Fundamentals Source:/i)).toBeInTheDocument();

    // 6 KPI summary cards
    expect(screen.getByTestId("kpi-total")).toBeInTheDocument();
    expect(screen.getByTestId("kpi-full")).toBeInTheDocument();
    expect(screen.getByTestId("kpi-stale")).toBeInTheDocument();
    expect(screen.getByTestId("kpi-quotes_only")).toBeInTheDocument();
    expect(screen.getByTestId("kpi-equity_only")).toBeInTheDocument();
    expect(screen.getByTestId("kpi-uncovered")).toBeInTheDocument();

    // Table rows rendered
    expect(screen.getByTestId("universe-row-AAPL")).toBeInTheDocument();
    expect(screen.getByTestId("universe-row-NVDA")).toBeInTheDocument();
  });

  it("filters rows when filter tabs are clicked", async () => {
    renderUniverseTransparency();

    await screen.findByTestId("universe-row-AAPL");

    // Click "Issues & Gaps" tab
    const gapsTab = screen.getByTestId("filter-tab-gaps");
    await userEvent.click(gapsTab);

    // AAPL is 'full' coverage, so it should be filtered out
    expect(screen.queryByTestId("universe-row-AAPL")).not.toBeInTheDocument();

    // NVDA is 'stale' coverage, so it should still be in the gaps list
    expect(screen.getByTestId("universe-row-NVDA")).toBeInTheDocument();

    // Click "Held" tab
    const heldTab = screen.getByTestId("filter-tab-held");
    await userEvent.click(heldTab);

    // Both AAPL and NVDA are held
    expect(screen.getByTestId("universe-row-AAPL")).toBeInTheDocument();
    expect(screen.getByTestId("universe-row-NVDA")).toBeInTheDocument();

    // Click "All" tab to reset
    const allTab = screen.getByTestId("filter-tab-all");
    await userEvent.click(allTab);
    expect(screen.getByTestId("universe-row-AAPL")).toBeInTheDocument();
  });

  it("filters rows when typing in the search input", async () => {
    renderUniverseTransparency();

    await screen.findByTestId("universe-row-AAPL");

    const searchInput = screen.getByTestId("search-input");
    await userEvent.type(searchInput, "AAPL");

    expect(screen.getByTestId("universe-row-AAPL")).toBeInTheDocument();
    expect(screen.queryByTestId("universe-row-NVDA")).not.toBeInTheDocument();

    // Clear search and type a non-existent ticker
    await userEvent.clear(searchInput);
    await userEvent.type(searchInput, "NONEXISTENT");

    // A non-empty universe filtered down to zero matches is a DIFFERENT,
    // distinct honest state from a genuinely empty universe (see the
    // dedicated cold-start test below) -- must render the filter-specific
    // empty state, not the "nothing tracked yet" one.
    expect(screen.getByTestId("empty-filtered-state")).toBeInTheDocument();
    expect(screen.queryByTestId("universe-transparency-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId("universe-row-AAPL")).not.toBeInTheDocument();
  });

  it("clicking (ⓘ) info button on a symbol row opens the ExplainTickerDrawer", async () => {
    renderUniverseTransparency();

    await screen.findByTestId("universe-row-AAPL");

    const explainBtn = screen.getByTestId("explain-ticker-btn-AAPL");
    expect(explainBtn).toBeInTheDocument();

    await userEvent.click(explainBtn);

    // Verify ExplainTickerDrawer is mounted and opened for AAPL
    expect(await screen.findByTestId("explain-ticker-drawer")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "AAPL" })).toBeInTheDocument();
  });

  it("triggers universe sync when Sync Universe button is clicked", async () => {
    const postSyncSpy = vi.spyOn(api, "postDataSync").mockResolvedValueOnce({
      report: {
        generated_at: new Date().toISOString(),
        positions: ["AAPL"],
        watchlists: {},
        symbols: {},
        provider_source: "alpaca",
        fundamentals_source: "yahoo",
      },
      default_tickers: ["AAPL", "MSFT", "NVDA"],
      applies: "next_daemon_restart",
      note: "Discovered 3 symbols",
    });

    renderUniverseTransparency();

    await screen.findByRole("heading", { name: "Universe Transparency" });

    const syncBtn = screen.getByTestId("sync-now-button");
    await userEvent.click(syncBtn);

    expect(postSyncSpy).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Sync completed: 3 tickers configured/i)).toBeInTheDocument();
  });

  it("handles symbol re-inclusion mutation", async () => {
    // Override getSyncReport with an excluded symbol
    vi.spyOn(api, "getSyncReport").mockResolvedValueOnce({
      generated_at: new Date().toISOString(),
      positions: [],
      watchlists: {},
      symbols: {
        EXCL: {
          symbol: "EXCL",
          coverage: "uncovered",
          held: false,
          quantity: 0,
          avg_cost: null,
          current_price: null,
          cost_basis_delta_per_share: null,
          market_value: null,
          is_stale_quote: false,
          quote_source: "none",
          has_fundamentals: false,
          forecast_available: false,
          watchlists: [],
          diagnostic: "drop_threshold_reached",
          rating_consecutive_bad_cycles: 5,
          rating_excluded: true,
        },
      },
      provider_source: "alpaca",
      fundamentals_source: "yahoo",
    });

    const reincludeSpy = vi.spyOn(api, "reincludeSymbol").mockResolvedValueOnce({
      symbol: "EXCL",
      reincluded: true,
    });

    renderUniverseTransparency();

    await screen.findByTestId("universe-row-EXCL");
    expect(screen.getByText("Excluded")).toBeInTheDocument();
    expect(screen.getByText("5 bad cycle(s)")).toBeInTheDocument();

    const reincludeBtn = screen.getByTestId("reinclude-btn-EXCL");
    await userEvent.click(reincludeBtn);

    expect(reincludeSpy).toHaveBeenCalledWith("EXCL");
  });

  it("distinguishes a real 0-bad-cycles rating from no rating history at all", async () => {
    // CONSTRAINT #4: `rating_consecutive_bad_cycles == null` (no history)
    // must never render identically to a symbol genuinely verified clean
    // for zero consecutive bad cycles -- both used to render "0 bad".
    vi.spyOn(api, "getSyncReport").mockResolvedValueOnce({
      generated_at: new Date().toISOString(),
      positions: [],
      watchlists: {},
      symbols: {
        RATED_CLEAN: {
          symbol: "RATED_CLEAN",
          coverage: "full",
          held: false,
          quantity: 0,
          avg_cost: null,
          current_price: 100,
          cost_basis_delta_per_share: null,
          market_value: null,
          is_stale_quote: false,
          quote_source: "alpaca",
          has_fundamentals: true,
          forecast_available: true,
          watchlists: ["file:watchlist.txt"],
          diagnostic: "",
          rating_consecutive_bad_cycles: 0,
          rating_excluded: false,
        },
        UNRATED: {
          symbol: "UNRATED",
          coverage: "full",
          held: false,
          quantity: 0,
          avg_cost: null,
          current_price: 100,
          cost_basis_delta_per_share: null,
          market_value: null,
          is_stale_quote: false,
          quote_source: "alpaca",
          has_fundamentals: true,
          forecast_available: true,
          watchlists: ["file:watchlist.txt"],
          diagnostic: "",
          rating_consecutive_bad_cycles: null,
          rating_excluded: false,
        },
      },
      provider_source: "alpaca",
      fundamentals_source: "yahoo",
    });

    renderUniverseTransparency();

    const ratedRow = await screen.findByTestId("universe-row-RATED_CLEAN");
    const unratedRow = await screen.findByTestId("universe-row-UNRATED");

    expect(ratedRow).toHaveTextContent("0 bad");
    // "No rating history" must render a distinct em-dash, never the same
    // "0 bad" text a genuinely-rated-clean symbol gets.
    expect(unratedRow).not.toHaveTextContent("0 bad");
    expect(unratedRow).toHaveTextContent("—");
  });

  it("renders an honest cold-start empty state for a genuinely empty universe, distinct from a filtered-to-zero result", async () => {
    vi.spyOn(api, "getSyncReport").mockResolvedValueOnce({
      generated_at: new Date().toISOString(),
      positions: [],
      watchlists: {},
      symbols: {},
      provider_source: "",
      fundamentals_source: "",
    });

    renderUniverseTransparency();

    expect(await screen.findByTestId("universe-transparency-empty")).toHaveTextContent(
      /No symbols tracked yet/i,
    );
    expect(screen.queryByTestId("empty-filtered-state")).not.toBeInTheDocument();
    // Points the operator at the wider market rather than a dead end.
    expect(screen.getByRole("link", { name: /Symbol Screener/i })).toBeInTheDocument();
  });
});

describe("UniverseTransparency — idle by default (Robinhood category gate)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("does not call GET /data/sync-report on mount when the robinhood category is off (default)", async () => {
    const spy = vi.spyOn(api, "getSyncReport");
    render(
      <AutoRefreshProvider>
        <MemoryRouter>
          <ExplainTickerProvider>
            <UniverseTransparency />
            <ExplainTickerDrawer />
          </ExplainTickerProvider>
        </MemoryRouter>
      </AutoRefreshProvider>
    );

    expect(await screen.findByTestId("universe-transparency-idle")).toHaveTextContent(
      "Coverage report not loaded",
    );
    expect(screen.getByTestId("universe-transparency-load")).toBeInTheDocument();
    // Sync Universe stays reachable from the idle view too.
    expect(screen.getByTestId("sync-now-button")).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it("clicking 'Load coverage report' fetches exactly once and renders the live view", async () => {
    const spy = vi.spyOn(api, "getSyncReport");
    render(
      <AutoRefreshProvider>
        <MemoryRouter>
          <ExplainTickerProvider>
            <UniverseTransparency />
            <ExplainTickerDrawer />
          </ExplainTickerProvider>
        </MemoryRouter>
      </AutoRefreshProvider>
    );

    await screen.findByTestId("universe-transparency-idle");
    expect(spy).not.toHaveBeenCalled();

    await userEvent.click(screen.getByTestId("universe-transparency-load"));

    expect(await screen.findByTestId("universe-row-AAPL")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
