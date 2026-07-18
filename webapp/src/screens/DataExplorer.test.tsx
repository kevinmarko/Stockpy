/**
 * DataExplorer.test.tsx — raw bars + fundamentals + macro for a symbol. Covers
 * the happy path, a null fundamental rendering "—", and the empty-bars state.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DataExplorer } from "./DataExplorer";
import { api } from "../api/client";

function renderScreen() {
  return render(
    <MemoryRouter>
      <DataExplorer />
    </MemoryRouter>
  );
}

describe("DataExplorer screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

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
});
