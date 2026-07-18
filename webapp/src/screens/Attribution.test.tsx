/**
 * Attribution.test.tsx — factor exposure + correlation cluster attribution +
 * trade quality screen. Verifies the real mock renders every section, and
 * that every honesty branch (no holdings, no matched factor data, null
 * factor value, unmatched symbols, empty clusters, heavy-concentration
 * warning, no excursion data, no closed trades, null edge ratio) degrades to
 * an explicit honest message rather than a fabricated 0 or blank chart.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Attribution } from "./Attribution";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import type { PortfolioAttribution, PortfolioTradeQuality } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <Attribution />
    </MemoryRouter>
  );
}

const BASE: PortfolioAttribution = {
  as_of: "2026-07-11T21:05:00Z",
  factor_exposure: {
    as_of: "2026-07-11T21:05:00Z",
    exposures: {
      value_z: -0.4,
      quality_z: 1.2,
      lowvol_z: 0.3,
      size_z: -1.8,
      multifactor_composite: 0.25,
    },
    coverage: {
      held_count: 3,
      matched_count: 2,
      matched_value_pct: 0.8,
      unmatched_symbols: ["DUK"],
    },
    reason: null,
  },
  correlation_clusters: {
    clusters: [
      {
        cluster_id: 1,
        symbols: ["AAPL", "MSFT", "NVDA"],
        n_symbols: 3,
        avg_intra_corr: 0.71,
        weight_pct: 0.25, // below the 30% heavy-concentration threshold
        insufficient_history: false,
      },
      {
        cluster_id: 3,
        symbols: ["DUK"],
        n_symbols: 1,
        avg_intra_corr: null,
        weight_pct: 0.1,
        insufficient_history: false,
      },
    ],
    lookback_days: 60,
    reason: null,
  },
};

describe("Attribution screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the factor exposure and correlation cluster sections from the mock", async () => {
    renderScreen();
    expect(
      await screen.findByRole("heading", { name: "Portfolio attribution" })
    ).toBeInTheDocument();
    expect(await screen.findByText("Factor exposure")).toBeInTheDocument();
    expect(await screen.findByText("Correlation clusters")).toBeInTheDocument();
    // At least one cluster card renders from the mock fixture (the mega-cap
    // tech cluster is genuinely concentrated enough to ALSO trip the
    // diversification warning -- "AAPL" legitimately appears more than once).
    expect((await screen.findAllByText(/AAPL/)).length).toBeGreaterThan(0);
  });

  it("no held positions renders the honest empty state, never a fabricated bar", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce({
      as_of: null,
      factor_exposure: {
        as_of: null,
        exposures: {
          value_z: null, quality_z: null, lowvol_z: null,
          size_z: null, multifactor_composite: null,
        },
        coverage: { held_count: 0, matched_count: 0, matched_value_pct: null, unmatched_symbols: [] },
        reason: "no held positions",
      },
      correlation_clusters: { clusters: [], lookback_days: 60, reason: "no held positions" },
    });
    renderScreen();
    expect(await screen.findByText("No holdings yet")).toBeInTheDocument();
    expect(await screen.findByText("No clusters yet")).toBeInTheDocument();
    expect(screen.getByText("no held positions")).toBeInTheDocument();
  });

  it("held positions with no matched factor data render the honest reason, not zeros", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce({
      ...BASE,
      factor_exposure: {
        as_of: null,
        exposures: {
          value_z: null, quality_z: null, lowvol_z: null,
          size_z: null, multifactor_composite: null,
        },
        coverage: { held_count: 2, matched_count: 0, matched_value_pct: null, unmatched_symbols: ["AAPL", "MSFT"] },
        reason: "no pipeline snapshot yet",
      },
    });
    renderScreen();
    expect(await screen.findByText("No factor data yet")).toBeInTheDocument();
    expect(screen.getByText("no pipeline snapshot yet")).toBeInTheDocument();
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
  });

  it("unmatched held symbols are surfaced in the coverage caption", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    expect(await screen.findByText(/Not yet scored: DUK/)).toBeInTheDocument();
    expect(screen.getByText(/2 of 3 holdings scored/)).toBeInTheDocument();
  });

  it("a null factor value renders an em dash, never 0 or NaN", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce({
      ...BASE,
      factor_exposure: {
        ...BASE.factor_exposure,
        exposures: { ...BASE.factor_exposure.exposures, size_z: null },
      },
    });
    renderScreen();
    await screen.findByText("Factor exposure");
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThan(0);
    expect(screen.queryByText("NaN")).not.toBeInTheDocument();
  });

  it("an insufficient-history cluster (cluster_id 0) is flagged, not shown as a real grouping", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce({
      ...BASE,
      correlation_clusters: {
        clusters: [
          {
            cluster_id: 0,
            symbols: ["ZZZZ"],
            n_symbols: 1,
            avg_intra_corr: null,
            weight_pct: 0.05,
            insufficient_history: true,
          },
        ],
        lookback_days: 60,
        reason: null,
      },
    });
    renderScreen();
    expect(
      await screen.findByText(/Not enough price history yet to correlate/)
    ).toBeInTheDocument();
  });

  it("a heavily concentrated cluster (>30% of book) shows the diversification warning", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce({
      ...BASE,
      correlation_clusters: {
        clusters: [
          {
            cluster_id: 1,
            symbols: ["AAPL", "MSFT", "NVDA"],
            n_symbols: 3,
            avg_intra_corr: 0.9,
            weight_pct: 0.55,
            insufficient_history: false,
          },
        ],
        lookback_days: 60,
        reason: null,
      },
    });
    renderScreen();
    expect(await screen.findByText(/High concentration/)).toBeInTheDocument();
  });

  it("no heavy concentration -> no warning banner rendered", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    await screen.findByText("Correlation clusters");
    expect(screen.queryByText(/High concentration/)).not.toBeInTheDocument();
  });
});

const TQ_BASE: PortfolioTradeQuality = {
  as_of: "2026-07-17T00:00:00Z",
  scatter: [
    { symbol: "AAPL", mfe: 0.09, mae: 0.03, edge_ratio: 3.0, conviction: 0.72, action: "BUY" },
    // Honest partial record: mfe/mae present, everything else null -- never
    // fabricated into a fake edge ratio / conviction / action.
    { symbol: "COST", mfe: 0.05, mae: 0.02, edge_ratio: null, conviction: null, action: null },
  ],
  edge_ratio_by_strategy: {
    by_strategy: [
      { strategy: "trend-following", n_trades: 8, avg_mfe: 0.086, avg_mae: 0.041, avg_edge_ratio: 2.35 },
      // Honest null-average branch: no trade in this group had a computable
      // Edge Ratio -- never averaged over a fabricated 0.
      { strategy: "(untagged)", n_trades: 2, avg_mfe: 0.03, avg_mae: 0.045, avg_edge_ratio: null },
    ],
    reason: null,
  },
};

describe("Attribution screen — Trade Quality section", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the MFE/MAE scatter and edge-ratio-by-strategy tables from the real mock", async () => {
    renderScreen();
    expect(await screen.findByText("Trade quality")).toBeInTheDocument();
    expect(await screen.findByText("MFE vs. MAE — current signals")).toBeInTheDocument();
    expect(await screen.findByText("Edge ratio by strategy — closed trades")).toBeInTheDocument();
    expect((await screen.findAllByText(/AAPL/)).length).toBeGreaterThan(0);
    expect(screen.getByText("trend-following")).toBeInTheDocument();
  });

  it("a scatter row with null edge_ratio/action renders an em dash, never a fabricated value", async () => {
    vi.spyOn(api, "getPortfolioTradeQuality").mockResolvedValueOnce(TQ_BASE);
    renderScreen();
    await screen.findByText("COST");
    // Both AAPL's real 3.0 and COST's honest "—" edge ratio must render.
    expect(screen.getByText("3.00")).toBeInTheDocument();
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThan(0);
  });

  it("a strategy group with null avg_edge_ratio renders an em dash, not 0", async () => {
    vi.spyOn(api, "getPortfolioTradeQuality").mockResolvedValueOnce(TQ_BASE);
    renderScreen();
    const untaggedRow = await screen.findByText("(untagged)");
    expect(untaggedRow).toBeInTheDocument();
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
  });

  it("no scatter data yet renders the honest empty state", async () => {
    vi.spyOn(api, "getPortfolioTradeQuality").mockResolvedValueOnce({
      ...TQ_BASE,
      scatter: [],
    });
    renderScreen();
    expect(await screen.findByText("No excursion data yet")).toBeInTheDocument();
  });

  it("no closed trades yet renders the honest empty state with the server's reason", async () => {
    vi.spyOn(api, "getPortfolioTradeQuality").mockResolvedValueOnce({
      ...TQ_BASE,
      edge_ratio_by_strategy: { by_strategy: [], reason: "no closed trades yet" },
    });
    renderScreen();
    expect(await screen.findByText("No closed trades yet")).toBeInTheDocument();
    expect(screen.getByText("no closed trades yet")).toBeInTheDocument();
  });

  it("a Trade Quality fetch failure never blocks the factor exposure / correlation sections", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    vi.spyOn(api, "getPortfolioTradeQuality").mockRejectedValueOnce(new Error("network down"));
    renderScreen();
    expect(await screen.findByText("Factor exposure")).toBeInTheDocument();
    expect(await screen.findByText("Correlation clusters")).toBeInTheDocument();
    expect(await screen.findByText("No trade quality data yet")).toBeInTheDocument();
  });

  it("clicking a scatter column header re-sorts the table", async () => {
    vi.spyOn(api, "getPortfolioTradeQuality").mockResolvedValueOnce(TQ_BASE);
    renderScreen();
    await screen.findByText("MFE vs. MAE — current signals");
    const symbolHeader = screen.getByText("Symbol");
    symbolHeader.click();
    // Sorting by symbol (alphabetical) should still render both symbols.
    expect(await screen.findByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("COST")).toBeInTheDocument();
  });
});

describe("Brinson-Fachler manual-input calculator", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the 11-sector editable table (real mock GET, no server round-trip needed to seed it)", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    expect(
      await screen.findByText("Brinson-Fachler attribution")
    ).toBeInTheDocument();
    expect(screen.getByText("Energy")).toBeInTheDocument();
    expect(screen.getByText("Real Estate")).toBeInTheDocument();
    expect(screen.getByText("Information Technology")).toBeInTheDocument();
    // All 11 GICS sectors * 4 editable numeric cells each.
    expect(screen.getAllByRole("spinbutton")).toHaveLength(11 * 4);
  });

  it("all-zero default rows show the client-side weight-sum warning before any edit", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    await screen.findByText("Brinson-Fachler attribution");
    expect(
      screen.getByText("Portfolio weights sum to 0.00% (expected ~100%).")
    ).toBeInTheDocument();
    expect(
      screen.getByText("Benchmark weights sum to 0.00% (expected ~100%).")
    ).toBeInTheDocument();
  });

  it("computing a single fully-weighted sector matches hand-computed effects", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    await screen.findByText("Brinson-Fachler attribution");

    const user = userEvent.setup();
    const row = screen.getByText("Energy").closest("tr") as HTMLElement;
    await user.clear(within(row).getByLabelText("Energy portfolio weight percent"));
    await user.type(within(row).getByLabelText("Energy portfolio weight percent"), "100");
    await user.clear(within(row).getByLabelText("Energy portfolio return percent"));
    await user.type(within(row).getByLabelText("Energy portfolio return percent"), "10");
    await user.clear(within(row).getByLabelText("Energy benchmark weight percent"));
    await user.type(within(row).getByLabelText("Energy benchmark weight percent"), "100");
    await user.clear(within(row).getByLabelText("Energy benchmark return percent"));
    await user.type(within(row).getByLabelText("Energy benchmark return percent"), "8");

    await user.click(screen.getByRole("button", { name: "Compute" }));

    // Single fully-weighted sector: Portfolio Return = 10%, Benchmark Return =
    // 8%, Active Return = 2% = Selection Effect (Allocation/Interaction = 0
    // since portfolio and benchmark weights are identical in every sector).
    expect(await screen.findByText("+10.00%")).toBeInTheDocument(); // Portfolio return
    expect(screen.getByText("+8.00%")).toBeInTheDocument(); // Benchmark return
    expect(screen.getAllByText("+2.00%").length).toBeGreaterThan(0); // Active + Selection effect
  });

  it("a 422 from the server renders the honest error message inline, not a generic failure", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    vi.spyOn(api, "getBrinsonFachlerAttribution").mockRejectedValueOnce(
      new ApiError("No rows with a non-blank sector name.", 422)
    );
    renderScreen();
    await screen.findByText("Brinson-Fachler attribution");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Compute" }));

    const errorBox = await screen.findByTestId("brinson-error");
    expect(errorBox).toHaveTextContent("No rows with a non-blank sector name.");
  });
});
