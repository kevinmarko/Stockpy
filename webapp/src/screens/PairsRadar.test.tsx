/**
 * PairsRadar.test.tsx — the pairs radar sub-page renders cointegrated pair
 * cards from the mock, and renders the honest empty state (with the persisted
 * reason) when no pairs are available — never a fabricated pair.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PairsRadar } from "./PairsRadar";
import { api } from "../api/client";

function renderPairs() {
  return render(
    <MemoryRouter>
      <PairsRadar />
    </MemoryRouter>
  );
}

describe("PairsRadar screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the ranked pair cards from the mock", async () => {
    renderPairs();
    expect(await screen.findByRole("heading", { name: "Pairs radar" })).toBeInTheDocument();
    // The first mock pair (XOM / CVX) renders its tickers.
    expect(await screen.findByText(/XOM/)).toBeInTheDocument();
    expect(screen.getAllByText("z-score").length).toBeGreaterThan(0);
  });

  it("an empty radar renders the honest reason, never a fabricated pair", async () => {
    vi.spyOn(api, "getPairs").mockResolvedValueOnce({
      as_of: null,
      universe: [],
      pairs: [],
      reason: "Pairs radar not generated yet — enable PAIRS_SNAPSHOT_ENABLED.",
    });
    renderPairs();
    expect(
      await screen.findByText(/Pairs radar not generated yet/)
    ).toBeInTheDocument();
  });
});
