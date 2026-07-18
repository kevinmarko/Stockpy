/**
 * SymbolDetail.test.tsx — renders against the real mock API. Covers the header,
 * the Stockpy reverse cross-link ("Held by Pilots" → links into a Pilot), the
 * honesty invariant (a null factor/risk leaf renders "—", never a fabricated
 * value), and the honest 404 for an unknown ticker.
 */
import { render, screen, waitFor } from "@testing-library/react";
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
    afterEach(() => {
      vi.restoreAllMocks();
      localStorage.clear();
    });

    it("shows the seeded past decisions for a symbol that already has some", async () => {
      renderSymbol("AAPL");
      await screen.findByRole("heading", { name: "AAPL" });
      expect(
        await screen.findByRole("heading", { name: "Decision journal" })
      ).toBeInTheDocument();
      expect(await screen.findByText(/Sized normally/)).toBeInTheDocument();
    });

    it("a symbol with no logged decisions renders the honest empty state, not a spinner-forever or blank space", async () => {
      renderSymbol("MSFT");
      await screen.findByRole("heading", { name: "MSFT" });
      expect(
        await screen.findByText("No decisions logged yet for MSFT.")
      ).toBeInTheDocument();
    });

    it("logging a decision appends it to the past-decisions list and clears the notes field", async () => {
      const user = userEvent.setup();
      renderSymbol("MSFT");
      await screen.findByRole("heading", { name: "MSFT" });
      await screen.findByText("No decisions logged yet for MSFT.");

      const notes = screen.getByLabelText("Notes (optional)") as HTMLTextAreaElement;
      await user.type(notes, "Skipped — position already large.");
      await user.click(screen.getByRole("button", { name: /Passed/ }));

      // React renders a controlled <textarea>'s current value as its child
      // text node, so the notes textarea itself briefly matches this exact
      // string too -- and the reload passes through an intermediate loading
      // skeleton before the row lands. Poll for the FINAL state directly
      // (the posted note inside a `.row-sub`, which can only ever be the
      // rendered past-decisions row, never the live textarea) via waitFor's
      // retry semantics instead of a single point-in-time snapshot.
      await waitFor(() => {
        expect(
          screen.getByText("Skipped — position already large.", { selector: ".row-sub" })
        ).toBeInTheDocument();
      });
      expect(screen.queryByText("No decisions logged yet for MSFT.")).not.toBeInTheDocument();
      expect(notes.value).toBe("");
    });

    it("a 'modified' decision with empty notes is still accepted (no client-side 422-style block)", async () => {
      const user = userEvent.setup();
      renderSymbol("MSFT");
      await screen.findByRole("heading", { name: "MSFT" });
      await screen.findByText("No decisions logged yet for MSFT.");

      await user.click(screen.getByRole("button", { name: /Modified/ }));

      // Both the always-present button AND the newly-logged row's action
      // label read "🔁 Modified" -- two occurrences proves the row actually
      // landed (a bare >0 count would pass on the button alone, and the
      // reload passes through an intermediate loading skeleton first).
      await waitFor(() => {
        expect(screen.getAllByText("🔁 Modified").length).toBeGreaterThanOrEqual(2);
      });
      expect(screen.queryByText("No decisions logged yet for MSFT.")).not.toBeInTheDocument();
    });
  });
});
