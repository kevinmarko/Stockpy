/**
 * Marketplace.test.tsx — renders the Marketplace screen against the REAL mock
 * API (no vi.mock — `api` resolves to `mockApi` by default under
 * VITE_USE_MOCK's default-true behavior, exercising the same data flow the
 * app actually runs offline). Verifies the rails render from real catalog
 * data and that loading/error/retry states surface correctly.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
});
