/**
 * Attribution.test.tsx — factor exposure + correlation cluster attribution
 * screen. Verifies the real mock renders both sections, and that every
 * honesty branch (no holdings, no matched factor data, null factor value,
 * unmatched symbols, empty clusters, heavy-concentration warning) degrades to
 * an explicit honest message rather than a fabricated 0 or blank chart.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Attribution } from "./Attribution";
import { api } from "../api/client";
import type { PortfolioAttribution } from "../api/types";

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
