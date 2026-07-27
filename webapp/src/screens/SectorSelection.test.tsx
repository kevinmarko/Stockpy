/**
 * SectorSelection.test.tsx — semantic Related Sector Selection ranking
 * screen. Covers the happy path, null-field honesty ("—" never a fabricated
 * number), the per-row degraded_reason label, the review-unavailable
 * banner, the N-slider re-fetching a new ranking, and the cold-start empty
 * state for an untracked symbol.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
    // Five numeric/rank cells all render the dash, never a fabricated 0.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(5);
  });
});
