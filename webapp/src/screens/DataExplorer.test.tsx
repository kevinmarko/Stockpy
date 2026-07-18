/**
 * DataExplorer.test.tsx — raw bars + fundamentals + macro for a symbol, plus the
 * recommended-stocks list. Covers the happy path, a null fundamental rendering
 * "—", the empty-bars state, and the recommendations list. Universe add/remove
 * lives in Settings now (see UniverseManager.test.tsx) — this screen only
 * points there.
 */
import { render, screen, within } from "@testing-library/react";
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

  it("shows the recommended-stocks list", async () => {
    renderScreen();
    const recs = await screen.findByTestId("recommended-stocks");
    expect(await within(recs).findByTestId("rec-row-NVDA")).toBeInTheDocument();
  });

  it("points to Settings for tracked-universe management (no inline control here)", async () => {
    renderScreen();
    const link = await screen.findByRole("link", { name: "Settings" });
    expect(link).toHaveAttribute("href", "/settings");
    expect(screen.queryByTestId("universe-manager")).not.toBeInTheDocument();
  });
});
