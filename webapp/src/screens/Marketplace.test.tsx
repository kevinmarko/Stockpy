/**
 * Marketplace.test.tsx — renders the Marketplace screen against the REAL mock
 * API (no vi.mock — `api` resolves to `mockApi` by default under
 * VITE_USE_MOCK's default-true behavior, exercising the same data flow the
 * app actually runs offline). Verifies the rails render from real catalog
 * data and that loading/error/retry states surface correctly.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Marketplace } from "./Marketplace";
import { api } from "../api/client";
import { ApiError } from "../api/types";

function renderMarketplace() {
  return render(
    <MemoryRouter>
      <Marketplace />
    </MemoryRouter>
  );
}

describe("Marketplace screen (real mock API)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders Top Performers, Most Popular, and category rails from the real mock catalog", async () => {
    renderMarketplace();

    expect(await screen.findByText("Top Performers")).toBeInTheDocument();
    expect(screen.getByText("Most Popular")).toBeInTheDocument();
    expect(screen.getByText("Browse by category")).toBeInTheDocument();

    // A known pilot from pilots/catalog.py's mock mirror should render as a card.
    expect(screen.getAllByText(/Trend Follower/i).length).toBeGreaterThan(0);
  });

  it("a non-deployable pilot (momentum-burst) still surfaces its badge honestly, never hidden", async () => {
    renderMarketplace();
    await screen.findByText("Top Performers");

    // momentum-burst is deliberately non-deployable (docs/AUTOPILOT_PLAN.md) —
    // it must still appear somewhere in the marketplace (its own category rail),
    // never silently dropped from the listing.
    expect(screen.getAllByText(/not deployable/i).length).toBeGreaterThan(0);
  });

  it("cards render a top-holding chip and a DSR honesty chip, not just a plain symbol list", async () => {
    renderMarketplace();
    await screen.findByText("Top Performers");

    // trend-following's top holding (NVDA) renders as a chip on its card.
    expect(screen.getAllByText("NVDA").length).toBeGreaterThan(0);
    // Every rail card's footer now also surfaces a DSR honesty badge.
    expect(screen.getAllByText(/DSR/).length).toBeGreaterThan(0);
  });

  it("shows a loading skeleton before data resolves", () => {
    renderMarketplace();
    // Loading renders `.skeleton` placeholder divs; the rails aren't present yet.
    expect(screen.queryByText("Top Performers")).not.toBeInTheDocument();
  });

  it("shows ErrorState with a Retry button on a hard API failure, and retry reloads", async () => {
    const spy = vi
      .spyOn(api, "listPilots")
      .mockRejectedValueOnce(new ApiError("backend unreachable", 500));

    renderMarketplace();

    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
    expect(screen.getByText("backend unreachable")).toBeInTheDocument();

    spy.mockResolvedValueOnce([]);
    const retryBtn = screen.getByRole("button", { name: "Retry" });
    retryBtn.click();

    await waitFor(() => {
      expect(screen.queryByText("Couldn't load")).not.toBeInTheDocument();
    });
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("a cold-start 404 renders the honest 'nothing here yet' message, no Retry button", async () => {
    vi.spyOn(api, "listPilots").mockRejectedValueOnce(
      new ApiError("not found", 404)
    );

    renderMarketplace();

    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("offline with a cached catalog (client.ts's localStorage fallback) renders the rails from cached data behind a stale-data notice, not a blank/error screen", async () => {
    const err = new ApiError("Network error reaching Pilots API", 0);
    err.cachedData = [
      {
        id: "trend-following",
        name: "Trend Follower",
        category: "Momentum",
        description: "cached description",
        headline: { sharpe: 1.1, dsr: 0.97, pbo: 0.3, max_drawdown: 0.2, deployable: true },
        holdings_count: 5,
        aum_proxy: 100,
        followers_proxy: 10,
        long_only: false,
      },
    ];
    err.cachedAt = new Date(Date.now() - 5 * 60_000).toISOString();
    vi.spyOn(api, "listPilots").mockRejectedValueOnce(err);

    renderMarketplace();

    const notice = await screen.findByTestId("stale-data-notice");
    expect(notice).toHaveTextContent(/offline: showing cached data/i);
    // The cached catalog still rendered as real rails, not an error/blank screen.
    expect(screen.getAllByText(/Trend Follower/i).length).toBeGreaterThan(0);
    expect(screen.queryByText("Couldn't load")).not.toBeInTheDocument();
  });

  it("the category pill bar filters to a single-category grid, and 'All' restores the rails", async () => {
    const user = userEvent.setup();
    renderMarketplace();
    await screen.findByText("Top Performers");

    const momentumPill = screen.getByRole("button", { name: "Momentum" });
    await user.click(momentumPill);

    // Rails are gone; only Momentum-category pilots render, as cards in a grid.
    expect(screen.queryByText("Top Performers")).not.toBeInTheDocument();
    expect(screen.queryByText("Most Popular")).not.toBeInTheDocument();
    expect(screen.queryByText("Browse by category")).not.toBeInTheDocument();
    expect(screen.getAllByText(/Trend Follower/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Momentum Burst/i).length).toBeGreaterThan(0);
    // A Mean Reversion-only pilot must not leak into the Momentum filter.
    expect(screen.queryByText(/Dip Buyer/i)).not.toBeInTheDocument();

    const allPill = screen.getByRole("button", { name: "All" });
    await user.click(allPill);

    expect(screen.getByText("Top Performers")).toBeInTheDocument();
    expect(screen.getByText("Most Popular")).toBeInTheDocument();
    expect(screen.getByText("Browse by category")).toBeInTheDocument();
  });
});
