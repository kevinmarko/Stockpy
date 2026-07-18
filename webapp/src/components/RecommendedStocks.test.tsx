/**
 * RecommendedStocks.test.tsx — the shared ranked BUY-picks list. Covers the
 * ranked render, the null-field honesty branch ("—", never 0), click →
 * onSelect, the default navigation path, and the honest empty state.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RecommendedStocks } from "./RecommendedStocks";
import { api } from "../api/client";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname}</div>;
}

describe("RecommendedStocks (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders BUY picks ranked by conviction, top pick first", async () => {
    render(
      <MemoryRouter>
        <RecommendedStocks />
      </MemoryRouter>
    );
    // NVDA is the highest-conviction fixture pick → its row exists.
    expect(await screen.findByTestId("rec-row-NVDA")).toBeInTheDocument();
    expect(screen.getByText("88%")).toBeInTheDocument();
  });

  it("a null conviction/score row renders '—', never a fabricated 0", async () => {
    render(
      <MemoryRouter>
        <RecommendedStocks />
      </MemoryRouter>
    );
    // The "ZZ" fixture row carries all-null numerics.
    const row = await screen.findByTestId("rec-row-ZZ");
    expect(row.textContent).toContain("—");
    expect(row.textContent).not.toMatch(/\b0%|score 0\b/);
  });

  it("clicking a pick calls onSelect with the symbol", async () => {
    const onSelect = vi.fn();
    render(
      <MemoryRouter>
        <RecommendedStocks onSelect={onSelect} />
      </MemoryRouter>
    );
    fireEvent.click(await screen.findByTestId("rec-row-AAPL"));
    expect(onSelect).toHaveBeenCalledWith("AAPL");
  });

  it("without onSelect, clicking navigates to the symbol detail page", async () => {
    render(
      <MemoryRouter initialEntries={["/compare"]}>
        <Routes>
          <Route path="/compare" element={<RecommendedStocks />} />
          <Route path="/symbol/:ticker" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    );
    fireEvent.click(await screen.findByTestId("rec-row-AAPL"));
    await waitFor(() =>
      expect(screen.getByTestId("loc")).toHaveTextContent("/symbol/AAPL")
    );
  });

  it("renders the honest empty state (with reason) when there are no picks", async () => {
    vi.spyOn(api, "getRecommendations").mockResolvedValueOnce({
      recommendations: [],
      count: 0,
      as_of: null,
      reason: "No BUY-rated recommendations in the latest snapshot yet.",
    });
    render(
      <MemoryRouter>
        <RecommendedStocks />
      </MemoryRouter>
    );
    expect(await screen.findByText("No recommendations yet")).toBeInTheDocument();
    expect(
      screen.getByText(/No BUY-rated recommendations/)
    ).toBeInTheDocument();
  });
});
