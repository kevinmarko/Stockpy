/**
 * SymbolDetail.test.tsx — renders against the real mock API. Covers the header,
 * the Stockpy reverse cross-link ("Held by Pilots" → links into a Pilot), the
 * honesty invariant (a null factor/risk leaf renders "—", never a fabricated
 * value), and the honest 404 for an unknown ticker.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SymbolDetail } from "./SymbolDetail";
import { api } from "../api/client";

function renderSymbol(ticker: string) {
  return render(
    <MemoryRouter initialEntries={[`/symbol/${ticker}`]}>
      <Routes>
        <Route path="/symbol/:ticker" element={<SymbolDetail />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("SymbolDetail screen (real mock API)", () => {
  it("renders the header for a known symbol", async () => {
    renderSymbol("NVDA");
    expect(await screen.findByRole("heading", { name: "NVDA" })).toBeInTheDocument();
  });

  it("the 'Held by Pilots' section links into at least one Pilot detail page", async () => {
    renderSymbol("NVDA");
    await screen.findByRole("heading", { name: "NVDA" });
    const links = document.querySelectorAll('a[href^="/pilots/"]');
    expect(links.length).toBeGreaterThan(0);
  });

  it("a null factor/risk leaf renders '—', never a fabricated value", async () => {
    renderSymbol("NVDA");
    await screen.findByRole("heading", { name: "NVDA" });
    // value_z/quality_z (factors) + several risk fields are honestly null in the
    // mock, so at least one em-dash placeholder is rendered.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("an unknown ticker renders the honest 404 'Nothing here yet' state", async () => {
    renderSymbol("ZZZZ");
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
  });

  it("renders the Forecast skill and Options premium sections", async () => {
    renderSymbol("AAPL");
    await screen.findByRole("heading", { name: "AAPL" });
    expect(await screen.findByRole("heading", { name: "Forecast skill" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Options premium" })).toBeInTheDocument();
  });

  it("a debit-spread symbol's options directive shows '—' for Realizable Daily Theta, never a fabricated $0.00", async () => {
    // NVDA's mock directive is a Call Debit Spread carrying a raw
    // Realizable_Daily_Theta of 0.0 (the pre-fix engine default) specifically
    // to prove the shared optionsHonesty gate — not the raw field — drives
    // this row.
    renderSymbol("NVDA");
    await screen.findByRole("heading", { name: "NVDA" });
    await screen.findByText("Call Debit Spread");
    const row = screen.getByText("Realizable θ/day").closest(".row") as HTMLElement;
    const value = row.querySelector(".num") as HTMLElement;
    expect(value.textContent).toBe("—");
  });

  describe("Rolling beta vs SPY section", () => {
    afterEach(() => vi.restoreAllMocks());

    it("renders a real, non-empty rolling beta series for a known symbol", async () => {
      renderSymbol("AAPL");
      await screen.findByRole("heading", { name: "AAPL" });
      expect(
        await screen.findByRole("heading", { name: "Rolling beta vs SPY" })
      ).toBeInTheDocument();
      // The mock fixture returns a real series for a known symbol -- the
      // "latest" caption (not the honest-empty placeholder) must render.
      expect(await screen.findByText(/60-day rolling beta — latest:/)).toBeInTheDocument();
    });

    it("insufficient cached history renders the honest reason, never a fabricated chart", async () => {
      vi.spyOn(api, "getRollingBeta").mockResolvedValueOnce({
        symbol: "AAPL",
        window: 60,
        series: [],
        reason: "Not enough overlapping history to compute a 60-day rolling beta for AAPL yet.",
      });
      renderSymbol("AAPL");
      await screen.findByRole("heading", { name: "AAPL" });
      expect(
        await screen.findByText(
          "Not enough overlapping history to compute a 60-day rolling beta for AAPL yet."
        )
      ).toBeInTheDocument();
      // The "latest" caption belongs to the real-series branch only.
      expect(screen.queryByText(/60-day rolling beta — latest:/)).not.toBeInTheDocument();
    });
  });

  describe("Decision journal section", () => {
    afterEach(() => vi.restoreAllMocks());

    it("renders the seeded decision for AAPL (the mock's pre-populated symbol)", async () => {
      renderSymbol("AAPL");
      await screen.findByRole("heading", { name: "AAPL" });
      expect(await screen.findByRole("heading", { name: "Decision journal" })).toBeInTheDocument();
      expect(await screen.findByText("✅ Acted")).toBeInTheDocument();
    });

    it("a symbol with no logged decisions renders the honest empty state", async () => {
      renderSymbol("NVDA");
      await screen.findByRole("heading", { name: "NVDA" });
      expect(
        await screen.findByText("No decisions logged yet for NVDA.")
      ).toBeInTheDocument();
    });

    it("clicking 'Log decision' opens the modal, and a successful submit refreshes the list", async () => {
      const user = userEvent.setup();
      renderSymbol("NVDA");
      await screen.findByRole("heading", { name: "NVDA" });
      await screen.findByText("No decisions logged yet for NVDA.");

      await user.click(screen.getByRole("button", { name: "Log decision" }));
      expect(await screen.findByText("Log decision — NVDA")).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "⏭ Passed" }));
      expect(await screen.findByTestId("decision-result")).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Done" }));
      // The list refetches on close -- the empty-state message is gone and
      // the newly-logged entry renders.
      await screen.findByText("⏭ Passed");
      expect(screen.queryByText("No decisions logged yet for NVDA.")).not.toBeInTheDocument();
    });
  });
});
