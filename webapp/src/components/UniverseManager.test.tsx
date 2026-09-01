/**
 * UniverseManager.test.tsx — add/remove any stock from the tracked universe.
 * Covers the seeded chip list, add (persists + triggers onSelect), remove, and
 * the default navigate-to-symbol-detail behavior when no onSelect is passed.
 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UniverseManager } from "./UniverseManager";
import { api } from "../api/client";
import { __resetMockDataUniverse } from "../api/mock";
import { __resetUniverseCache } from "./universeCache";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname}</div>;
}

describe("UniverseManager (real mock API)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    __resetMockDataUniverse();
  });

  it("renders the seeded tracked-universe chips", async () => {
    render(
      <MemoryRouter>
        <UniverseManager />
      </MemoryRouter>
    );
    expect(await screen.findByTestId("universe-chip-AAPL")).toBeInTheDocument();
    expect(screen.getByTestId("universe-chip-MSFT")).toBeInTheDocument();
  });

  it("adding a stock persists it and shows a new chip", async () => {
    render(
      <MemoryRouter>
        <UniverseManager />
      </MemoryRouter>
    );
    await screen.findByTestId("universe-chip-AAPL");
    expect(screen.queryByTestId("universe-chip-TSLA")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Add a stock"), {
      target: { value: "tsla" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() =>
      expect(screen.getByTestId("universe-chip-TSLA")).toBeInTheDocument()
    );
  });

  it("clears the input after a successful add instead of leaving the added ticker behind", async () => {
    // Regression test: SymbolInput only reads its `initial` prop on mount
    // (it manages `value` internally), so passing `initial={draft}` alone
    // and resetting `draft` to "" after a successful add did NOT visibly
    // clear the field -- the just-added ticker silently lingered in the box.
    render(
      <MemoryRouter>
        <UniverseManager />
      </MemoryRouter>
    );
    await screen.findByTestId("universe-chip-AAPL");

    const input = screen.getByLabelText("Add a stock") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "nvda" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() =>
      expect(screen.getByTestId("universe-chip-NVDA")).toBeInTheDocument()
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Add a stock")).toHaveValue("")
    );
  });

  it("removing a stock drops its chip", async () => {
    render(
      <MemoryRouter>
        <UniverseManager />
      </MemoryRouter>
    );
    await screen.findByTestId("universe-chip-MSFT");

    fireEvent.click(screen.getByTestId("universe-remove-MSFT"));

    await waitFor(() =>
      expect(screen.queryByTestId("universe-chip-MSFT")).not.toBeInTheDocument()
    );
  });

  it("without onSelect, clicking a chip's symbol navigates to its detail page", async () => {
    render(
      <MemoryRouter initialEntries={["/settings"]}>
        <Routes>
          <Route path="/settings" element={<UniverseManager />} />
          <Route path="/symbol/:ticker" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    );
    fireEvent.click(await screen.findByText("AAPL"));
    await waitFor(() =>
      expect(screen.getByTestId("loc")).toHaveTextContent("/symbol/AAPL")
    );
  });

  it("clicking a chip's symbol calls onSelect when provided", async () => {
    const onSelect = vi.fn();
    render(
      <MemoryRouter>
        <UniverseManager onSelect={onSelect} />
      </MemoryRouter>
    );
    fireEvent.click(await screen.findByText("AAPL"));
    expect(onSelect).toHaveBeenCalledWith("AAPL");
  });

  it("adding a stock also triggers a spot data backfill for it", async () => {
    const backfillSpy = vi.spyOn(api, "triggerSymbolBackfill");
    render(
      <MemoryRouter>
        <UniverseManager />
      </MemoryRouter>
    );
    await screen.findByTestId("universe-chip-AAPL");

    fireEvent.change(screen.getByLabelText("Add a stock"), { target: { value: "tsla" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(backfillSpy).toHaveBeenCalledWith("TSLA"));
  });

  it("the 'Add a stock' field suggests from its OWN tracked list (DEFAULT_TICKERS), not the shared pipeline-snapshot universe", async () => {
    // The shared GET /universe cache (universeCache.ts) is a DIFFERENT list
    // from what this screen manages -- this is the fix for the bug where
    // SymbolInput here suggested from the wrong universe entirely.
    __resetUniverseCache();
    const getUniverseSpy = vi.spyOn(api, "getUniverse");
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <UniverseManager />
      </MemoryRouter>
    );
    await screen.findByTestId("universe-chip-AAPL"); // seeded DEFAULT_TICKERS includes AAPL

    await user.type(screen.getByLabelText("Add a stock"), "AAP");
    const list = await screen.findByTestId("symbol-suggestions");
    expect(within(list).getByText("AAPL")).toBeInTheDocument();
    expect(getUniverseSpy).not.toHaveBeenCalled();
  });

  // Regression test for the DEFAULT_TICKERS reporting-mismatch fix -- see
  // docs/known_issues/universe_count_reporting_mismatch.md. The mock's
  // MOCK_ACTIVE_WATCHLIST is deliberately narrower than MOCK_DATA_UNIVERSE
  // so GET /data/universe honestly reports `default_tickers_is_fallback:
  // false`, and this screen must surface that as a visible warning rather
  // than silently implying DEFAULT_TICKERS is what the pipeline evaluates.
  it("shows a warning notice when DEFAULT_TICKERS is configured but NOT the effective per-cycle universe", async () => {
    render(
      <MemoryRouter>
        <UniverseManager />
      </MemoryRouter>
    );
    await screen.findByTestId("universe-chip-AAPL");

    const notice = await screen.findByTestId("universe-not-effective-notice");
    expect(notice).toHaveTextContent("is NOT the effective per-cycle universe");
  });
});
