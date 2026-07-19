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

  it("a symbol already in the execution queue shows an 'In queue' badge; others don't", async () => {
    // The mock execution queue seeds an AAPL intent (see MOCK_EXECUTION_QUEUE) --
    // the same recommendation, cross-referenced, so the operator can see the
    // recommendation <-> queue link the backend already draws internally.
    render(
      <MemoryRouter>
        <RecommendedStocks />
      </MemoryRouter>
    );
    expect(await screen.findByTestId("rec-queued-AAPL")).toBeInTheDocument();
    await screen.findByTestId("rec-row-NVDA");
    expect(screen.queryByTestId("rec-queued-NVDA")).not.toBeInTheDocument();
  });

  it("the 'In queue' badge links to Agentic Trading", async () => {
    render(
      <MemoryRouter initialEntries={["/compare"]}>
        <Routes>
          <Route path="/compare" element={<RecommendedStocks />} />
          <Route path="/agentic" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    );
    fireEvent.click(await screen.findByTestId("rec-queued-AAPL"));
    await waitFor(() =>
      expect(screen.getByTestId("loc")).toHaveTextContent("/agentic")
    );
  });

  it("the Detail link always navigates to the symbol page, even when onSelect diverts the row click", async () => {
    // Data Explorer passes onSelect to load a pick inline instead of navigating
    // -- the Detail link must still provide a guaranteed path to the actionable
    // SymbolDetail page (Held-by-Pilots -> Follow, Decision journal) regardless.
    const onSelect = vi.fn();
    render(
      <MemoryRouter initialEntries={["/data-explorer"]}>
        <Routes>
          <Route path="/data-explorer" element={<RecommendedStocks onSelect={onSelect} />} />
          <Route path="/symbol/:ticker" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    );
    fireEvent.click(await screen.findByTestId("rec-detail-AAPL"));
    await waitFor(() =>
      expect(screen.getByTestId("loc")).toHaveTextContent("/symbol/AAPL")
    );
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("renders the recommendations' as_of freshness in the header, never fabricated when null", async () => {
    render(
      <MemoryRouter>
        <RecommendedStocks />
      </MemoryRouter>
    );
    // The mock's as_of is a fixed past date -> renders a real "Nd ago" age.
    expect(await screen.findByText(/\(\d+d ago\)/)).toBeInTheDocument();

    vi.restoreAllMocks();
    vi.spyOn(api, "getRecommendations").mockResolvedValueOnce({
      recommendations: [
        { symbol: "AAPL", action: "BUY", conviction: 0.5, score: 50, buy_range: null, sector: null, price: null },
      ],
      count: 1,
      as_of: null,
      reason: null,
    });
    render(
      <MemoryRouter>
        <RecommendedStocks />
      </MemoryRouter>
    );
    expect(await screen.findByText(/\(unknown\)/)).toBeInTheDocument();
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
