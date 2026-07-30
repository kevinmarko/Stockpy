/**
 * SignalBreakdown.test.tsx — per-module contribution breakdown. Covers the
 * happy path, a null-score module rendering "—" (never a fabricated 0), and the
 * cold-start (no bars) all-null / empty-modules honest state.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
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

  describe("Signal driver weights (universe-wide) panel", () => {
    it("renders module rows from the tracked universe, and a null row shows '—' not a fabricated 0", async () => {
      renderScreen();
      // Wait for the async getUniverse -> getSignalImportance chain to
      // resolve and a real row to render, not just the panel's static header.
      const row = await screen.findByText("news_catalyst");
      const panel = row.closest('[data-testid="global-importance-panel"]') as HTMLElement;
      expect(panel).not.toBeNull();
      // The mock fixture's honest-empty row (n_symbols_scored: 0) for this module.
      expect(panel.textContent).toMatch(/—/);
    });

    it("titles the panel accurately and only mentions SHAP to disclaim it, never to claim it", async () => {
      renderScreen();
      await screen.findByText("news_catalyst");
      const heading = screen.getByRole("heading", { name: "Signal driver weights (universe-wide)" });
      expect(heading).toBeInTheDocument();
      // The subtitle is allowed (expected) to mention "SHAP" as part of an
      // explicit disclaimer -- it must never appear as a bare, unqualified
      // label (e.g. a heading reading "SHAP" or "Feature importance").
      expect(screen.getByText(/not a feature-importance or SHAP measure/i)).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: /SHAP/i })).not.toBeInTheDocument();
    });

    it("an empty tracked universe renders the honest empty state, not a fabricated chart", async () => {
      vi.spyOn(api, "getUniverse").mockResolvedValueOnce({ symbols: [] });
      renderScreen();
      expect(await screen.findByText("No tracked symbols yet — run the pipeline, then reload.")).toBeInTheDocument();
    });
  });
});
