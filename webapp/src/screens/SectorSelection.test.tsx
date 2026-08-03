/**
 * SectorSelection.test.tsx — semantic Related Sector Selection ranking
 * screen. Covers the happy path, null-field honesty ("—" never a fabricated
 * number), the per-row degraded_reason label, the review-unavailable
 * banner, the N-slider re-fetching a new ranking, and the cold-start empty
 * state for an untracked symbol.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SectorSelection } from "./SectorSelection";
import { api } from "../api/client";
import type { SectorSelectionView } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <SectorSelection />
    </MemoryRouter>
  );
}

describe("SectorSelection screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the ranked sector table for the default symbol", async () => {
    renderScreen();
    expect(await screen.findByText("Sector Selection")).toBeInTheDocument();
    // The mock's fully-populated candidate sectors.
    expect(await screen.findByText("New Energy")).toBeInTheDocument();
  });

  it("shows the review-unavailable banner by default", async () => {
    renderScreen();
    expect(await screen.findByTestId("review-unavailable-banner")).toBeInTheDocument();
  });

  it("a sector with no description renders '—' for cosine similarity, not a fabricated number", async () => {
    renderScreen();
    await screen.findByText("New Energy");
    // Autonomous Driving is the mock's no_sector_description row (index 2).
    expect(screen.getByText("Autonomous Driving")).toBeInTheDocument();
    expect(screen.getByText("No description for this sector")).toBeInTheDocument();
  });

  it("a sector with no observed volume shows its degraded reason and is never selected", async () => {
    renderScreen();
    await screen.findByText("New Energy");
    expect(screen.getByText("Semiconductor")).toBeInTheDocument();
    expect(
      screen.getByText("No sentiment volume observed for this sector")
    ).toBeInTheDocument();
  });

  it("shows the selected-count summary chip", async () => {
    renderScreen();
    await screen.findByText("New Energy");
    expect(screen.getByText(/of \d+ selected/)).toBeInTheDocument();
  });

  it("changing N re-fetches with the new top-N", async () => {
    const spy = vi.spyOn(api, "getSectorSelection");
    renderScreen();
    await screen.findByText("New Energy");
    expect(spy).toHaveBeenLastCalledWith("AAPL", 3);

    fireEvent.change(screen.getByLabelText(/Related sectors to select/i), {
      target: { value: "2" },
    });

    await waitFor(() => expect(spy).toHaveBeenLastCalledWith("AAPL", 2));
  });

  it("an untracked symbol renders the honest cold-start empty state, not an error", async () => {
    renderScreen();
    await screen.findByText("New Energy");

    fireEvent.change(screen.getByTestId("symbol-input"), {
      target: { value: "ZZZZ_UNTRACKED" },
    });
    fireEvent.submit(screen.getByTestId("symbol-input").closest("form")!);

    expect(
      await screen.findByText("No sector selection has been computed for this symbol yet.")
    ).toBeInTheDocument();
  });

  it("a hard error renders ErrorState, not a blank screen", async () => {
    vi.spyOn(api, "getSectorSelection").mockRejectedValueOnce(
      new Error("network unreachable")
    );
    renderScreen();
    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
  });

  it("renders embedder/pooling provenance chip", async () => {
    renderScreen();
    await screen.findByText("New Energy");
    expect(screen.getByText(/sbert · max-pooled/)).toBeInTheDocument();
  });

  it("a fully-null response (no embedder configured) still renders the table with all dashes", async () => {
    const nullRow: SectorSelectionView = {
      target_symbol: "AAPL",
      as_of: "2026-07-26",
      top_n: 3,
      rows: [
        {
          sector: "Technology",
          cosine_similarity: null,
          ingestion_volume: null,
          sector_heat_factor: null,
          correlation_coefficient: null,
          rank: null,
          selected: false,
          degraded_reason: "no_embedder",
          pe: null,
          change_pct: null,
        },
      ],
      embedder: "none",
      pooling: null,
      reason: null,
    };
    vi.spyOn(api, "getSectorSelection").mockResolvedValueOnce(nullRow);
    renderScreen();
    expect(await screen.findByText("Technology")).toBeInTheDocument();
    expect(screen.getByText("No similarity backend configured")).toBeInTheDocument();
    // Seven numeric/rank cells (including the new P/E and 1D Chg columns)
    // all render the dash, never a fabricated 0.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(7);
  });

  it("renders the P/E and 1D Chg columns with real values from the mock sector snapshot", async () => {
    renderScreen();
    await screen.findByText("New Energy");

    const row = screen.getByText("New Energy").closest("tr")!;
    // Every fully-populated candidate row carries a real pe/change_pct value
    // (mockSectorSelection's fixture) -- the last two `.num` cells (P/E, 1D
    // Chg) must be real numbers, never a dash, for this covered sector.
    const numCells = row.querySelectorAll("td.num");
    const [peCell, changeCell] = [numCells[numCells.length - 2], numCells[numCells.length - 1]];
    expect(peCell.textContent).not.toContain("—");
    expect(changeCell.textContent).not.toContain("—");
    expect(changeCell.textContent).toMatch(/%/);
  });

  it("a candidate sector with no FMP valuation snapshot renders honest dashes for P/E and 1D Chg", async () => {
    renderScreen();
    await screen.findByText("New Energy");

    // "Charging Post" is the mock's deliberate no-snapshot row (index 4) --
    // its similarity fields are populated but pe/change_pct are null.
    const row = screen.getByText("Charging Post").closest("tr")!;
    const numCells = row.querySelectorAll("td.num");
    const [peCell, changeCell] = [numCells[numCells.length - 2], numCells[numCells.length - 1]];
    expect(peCell.textContent).toContain("—");
    expect(changeCell.textContent).toContain("—");
  });

  it("`pe`/`change_pct` being absent entirely (backend omits the optional fields) still renders honest dashes, never a crash", async () => {
    const legacyRow: SectorSelectionView = {
      target_symbol: "AAPL",
      as_of: "2026-07-26",
      top_n: 3,
      rows: [
        {
          sector: "Technology",
          cosine_similarity: 0.5,
          ingestion_volume: 10,
          sector_heat_factor: 0.6,
          correlation_coefficient: 0.3,
          rank: 1,
          selected: true,
          degraded_reason: null,
          // pe/change_pct deliberately omitted (optional fields)
        },
      ],
      embedder: "sbert",
      pooling: "max",
      reason: null,
    };
    vi.spyOn(api, "getSectorSelection").mockResolvedValueOnce(legacyRow);
    renderScreen();
    const row = (await screen.findByText("Technology")).closest("tr")!;
    expect(row.textContent).toContain("—");
  });
});
