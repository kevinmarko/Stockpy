/**
 * UniverseManager.test.tsx — add/remove any stock from the tracked universe.
 * Covers the seeded chip list, add (persists + triggers onSelect), remove, and
 * the default navigate-to-symbol-detail behavior when no onSelect is passed.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UniverseManager } from "./UniverseManager";
import { __resetMockDataUniverse } from "../api/mock";

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
});
