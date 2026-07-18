/**
 * DataExplorer.test.tsx — raw bars + fundamentals + macro for a symbol, plus the
 * recommended-stocks list and add/remove-universe control. Covers the happy
 * path, a null fundamental rendering "—", the empty-bars state, the
 * recommendations list, and the universe add/remove write flows.
 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DataExplorer } from "./DataExplorer";
import { api } from "../api/client";
import { __resetMockDataUniverse } from "../api/mock";

function renderScreen() {
  return render(
    <MemoryRouter>
      <DataExplorer />
    </MemoryRouter>
  );
}

describe("DataExplorer screen (real mock API)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    __resetMockDataUniverse();
  });

  it("renders the fundamentals for the default symbol", async () => {
    renderScreen();
    // "trailingPE" is prettified to "Trailing PE" by the label() helper.
    expect(await screen.findByText("Trailing PE")).toBeInTheDocument();
  });

  it("a null fundamental renders '—', never a fabricated 0", async () => {
    renderScreen();
    // payoutRatio:null in the fixture → the value cell renders an em-dash.
    await screen.findByText("Payout Ratio");
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("empty bars render the honest empty-state (not a zeroed chart)", async () => {
    vi.spyOn(api, "getDataBars").mockResolvedValueOnce([]);
    renderScreen();
    expect(
      await screen.findByText(/No bars in the store for this symbol/)
    ).toBeInTheDocument();
  });

  it("shows the recommended-stocks list", async () => {
    renderScreen();
    const recs = await screen.findByTestId("recommended-stocks");
    expect(await within(recs).findByTestId("rec-row-NVDA")).toBeInTheDocument();
  });

  it("adding a stock persists it and shows a new universe chip", async () => {
    renderScreen();
    const mgr = await screen.findByTestId("universe-manager");
    // seeded default universe (chips arrive after the async GET resolves)
    expect(await within(mgr).findByTestId("universe-chip-AAPL")).toBeInTheDocument();
    expect(within(mgr).queryByTestId("universe-chip-TSLA")).not.toBeInTheDocument();

    fireEvent.change(within(mgr).getByLabelText("Add a stock"), {
      target: { value: "tsla" },
    });
    fireEvent.click(within(mgr).getByRole("button", { name: "Add" }));

    await waitFor(() =>
      expect(within(mgr).getByTestId("universe-chip-TSLA")).toBeInTheDocument()
    );
  });

  it("removing a stock drops its universe chip", async () => {
    renderScreen();
    const mgr = await screen.findByTestId("universe-manager");
    expect(await within(mgr).findByTestId("universe-chip-MSFT")).toBeInTheDocument();

    fireEvent.click(within(mgr).getByTestId("universe-remove-MSFT"));

    await waitFor(() =>
      expect(within(mgr).queryByTestId("universe-chip-MSFT")).not.toBeInTheDocument()
    );
  });
});
