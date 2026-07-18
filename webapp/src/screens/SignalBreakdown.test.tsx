/**
 * SignalBreakdown.test.tsx — per-module contribution breakdown. Covers the
 * happy path, a null-score module rendering "—" (never a fabricated 0), and the
 * cold-start (no bars) all-null / empty-modules honest state.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SignalBreakdown } from "./SignalBreakdown";
import { api } from "../api/client";

function renderScreen() {
  return render(
    <MemoryRouter>
      <SignalBreakdown />
    </MemoryRouter>
  );
}

describe("SignalBreakdown screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the module rows and the BUY action for the default symbol", async () => {
    renderScreen();
    expect(await screen.findByText("timeseries_momentum")).toBeInTheDocument();
    expect(screen.getByText("BUY")).toBeInTheDocument();
  });

  it("a module with a null score renders '—', never a fabricated 0", async () => {
    renderScreen();
    // rsi2_mean_reversion carries score:null / contribution:null in the fixture.
    await screen.findByText("rsi2_mean_reversion");
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("cold-start (no bars) renders the honest empty-modules state", async () => {
    vi.spyOn(api, "getSignalBreakdown").mockResolvedValueOnce({
      symbol: "ZZZZ",
      action: null,
      conviction: null,
      final_score: null,
      modules: [],
    });
    renderScreen();
    expect(
      await screen.findByText(/No signal modules ran for ZZZZ/)
    ).toBeInTheDocument();
  });
});
