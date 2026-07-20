/**
 * SymbolDetail.test.tsx — renders against the real mock API. Covers the header,
 * the Stockpy reverse cross-link ("Held by Pilots" → links into a Pilot), the
 * honesty invariant (a null factor/risk leaf renders "—", never a fabricated
 * value), and the honest 404 for an unknown ticker.
 */
import { render, screen, within } from "@testing-library/react";
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

  describe("On-demand AI generation cards", () => {
    afterEach(() => vi.restoreAllMocks());

    it("Claude analyst note: Generate renders headline / why_now / key risks / invalidation for a known symbol", async () => {
      const user = userEvent.setup();
      renderSymbol("AAPL");
      await screen.findByRole("heading", { name: "AAPL" });
      const section = screen
        .getByRole("heading", { name: "Claude analyst note" })
        .closest("section") as HTMLElement;

      await user.click(within(section).getByRole("button", { name: "Generate" }));

      expect(await within(section).findByText(/Mean-reversion entry/)).toBeInTheDocument();
      expect(within(section).getByText(/shallow, orderly dip/)).toBeInTheDocument();
      expect(within(section).getByText(/Key risks/)).toBeInTheDocument();
      expect(within(section).getByText(/Invalidation:/)).toBeInTheDocument();
    });

    it("Claude analyst note: an honest missing_key reason renders the specific operator-facing message, never a generic error", async () => {
      const user = userEvent.setup();
      renderSymbol("NVDA");
      await screen.findByRole("heading", { name: "NVDA" });
      const section = screen
        .getByRole("heading", { name: "Claude analyst note" })
        .closest("section") as HTMLElement;

      await user.click(within(section).getByRole("button", { name: "Generate" }));

      expect(
        await within(section).findByText(
          "Claude commentary is enabled, but ANTHROPIC_API_KEY is not configured."
        )
      ).toBeInTheDocument();
    });

    it("Gemini chart read: Generate renders the chart image plus pattern / support / resistance / narrative for a known symbol", async () => {
      const user = userEvent.setup();
      renderSymbol("AAPL");
      await screen.findByRole("heading", { name: "AAPL" });
      const section = screen
        .getByRole("heading", { name: "Gemini chart read" })
        .closest("section") as HTMLElement;

      await user.click(within(section).getByRole("button", { name: "Generate" }));

      expect(await within(section).findByRole("img", { name: "AAPL price chart" })).toBeInTheDocument();
      expect(within(section).getByText("ascending triangle")).toBeInTheDocument();
      expect(within(section).getByText(/consolidating in a tightening range/)).toBeInTheDocument();
    });

    it("Gemini chart read: the chart image renders even when available is false (generation_failed) -- the chart itself still worked", async () => {
      const user = userEvent.setup();
      renderSymbol("NVDA");
      await screen.findByRole("heading", { name: "NVDA" });
      const section = screen
        .getByRole("heading", { name: "Gemini chart read" })
        .closest("section") as HTMLElement;

      await user.click(within(section).getByRole("button", { name: "Generate" }));

      expect(await within(section).findByRole("img", { name: "NVDA price chart" })).toBeInTheDocument();
      expect(
        within(section).getByText(
          "The chart rendered, but Gemini couldn't generate a pattern read for it right now — try again."
        )
      ).toBeInTheDocument();
      // The honest-failure branch never renders pattern/support/resistance fields.
      expect(within(section).queryByText("ascending triangle")).not.toBeInTheDocument();
    });

    it("Opal research brief: Generate renders thesis context / catalysts / risk factors / recent developments / sources for a known symbol", async () => {
      const user = userEvent.setup();
      renderSymbol("AAPL");
      await screen.findByRole("heading", { name: "AAPL" });
      const section = screen
        .getByRole("heading", { name: "Opal research brief" })
        .closest("section") as HTMLElement;

      await user.click(within(section).getByRole("button", { name: "Generate" }));

      expect(await within(section).findByText(/Q3 earnings call/)).toBeInTheDocument();
      expect(within(section).getByText("Catalysts")).toBeInTheDocument();
      expect(within(section).getByText("Risk factors")).toBeInTheDocument();
      expect(within(section).getByText("Recent developments")).toBeInTheDocument();
      expect(within(section).getByText(/Finnhub headlines/)).toBeInTheDocument();
    });

    it("Opal research brief: an honest disabled reason renders the specific operator-facing message, never a generic error", async () => {
      const user = userEvent.setup();
      renderSymbol("NVDA");
      await screen.findByRole("heading", { name: "NVDA" });
      const section = screen
        .getByRole("heading", { name: "Opal research brief" })
        .closest("section") as HTMLElement;

      await user.click(within(section).getByRole("button", { name: "Generate" }));

      expect(
        await within(section).findByText(
          "Opal research briefs are off. An operator can enable it via OPAL_RESEARCH_ENABLED in .env."
        )
      ).toBeInTheDocument();
    });

    it("clicking each card's Generate button calls the matching client method with the current symbol", async () => {
      const user = userEvent.setup();
      const commentarySpy = vi.spyOn(api, "generateCommentary");
      const chartSpy = vi.spyOn(api, "generateChart");
      const researchSpy = vi.spyOn(api, "generateResearch");

      renderSymbol("AAPL");
      await screen.findByRole("heading", { name: "AAPL" });

      const commentarySection = screen
        .getByRole("heading", { name: "Claude analyst note" })
        .closest("section") as HTMLElement;
      await user.click(within(commentarySection).getByRole("button", { name: "Generate" }));
      expect(commentarySpy).toHaveBeenCalledWith("AAPL");

      const chartSection = screen
        .getByRole("heading", { name: "Gemini chart read" })
        .closest("section") as HTMLElement;
      await user.click(within(chartSection).getByRole("button", { name: "Generate" }));
      expect(chartSpy).toHaveBeenCalledWith("AAPL");

      const researchSection = screen
        .getByRole("heading", { name: "Opal research brief" })
        .closest("section") as HTMLElement;
      await user.click(within(researchSection).getByRole("button", { name: "Generate" }));
      expect(researchSpy).toHaveBeenCalledWith("AAPL");
    });

    it("a request still in flight disables the Generate button (spinner state)", async () => {
      const user = userEvent.setup();
      renderSymbol("AAPL");
      await screen.findByRole("heading", { name: "AAPL" });
      const section = screen
        .getByRole("heading", { name: "Claude analyst note" })
        .closest("section") as HTMLElement;
      const button = within(section).getByRole("button", { name: "Generate" });

      await user.click(button);
      // useMutation sets pending synchronously before the mock's delay()
      // resolves -- the button must be disabled and aria-busy for that window.
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute("aria-busy", "true");

      await within(section).findByText(/Invalidation:/);
      expect(button).not.toBeDisabled();
    });

    it("one card failing/being disabled never blocks the other two -- NVDA's missing_key commentary still lets the chart and research cards render their own results", async () => {
      const user = userEvent.setup();
      renderSymbol("NVDA");
      await screen.findByRole("heading", { name: "NVDA" });

      const commentarySection = screen
        .getByRole("heading", { name: "Claude analyst note" })
        .closest("section") as HTMLElement;
      await user.click(within(commentarySection).getByRole("button", { name: "Generate" }));
      await within(commentarySection).findByText(/ANTHROPIC_API_KEY is not configured/);

      const researchSection = screen
        .getByRole("heading", { name: "Opal research brief" })
        .closest("section") as HTMLElement;
      await user.click(within(researchSection).getByRole("button", { name: "Generate" }));
      // NVDA's research fixture is the honest disabled branch too -- confirms
      // it renders its OWN reason independent of the commentary card's state.
      await within(researchSection).findByText(/OPAL_RESEARCH_ENABLED/);
    });
  });

  describe("Regime sizing impact card", () => {
    it("a symbol with a full sizing decomposition renders pre/post Kelly, the pp delta, and the multiplier", async () => {
      renderSymbol("AAPL");
      const card = (
        await screen.findByRole("heading", { name: "Regime sizing impact" })
      ).closest("section") as HTMLElement;

      expect(within(card).getByText("Kelly Target (pre-regime)")).toBeInTheDocument();
      expect(within(card).getByText("Kelly Target (post-regime)")).toBeInTheDocument();
      expect(within(card).getByText("HMM regime multiplier")).toBeInTheDocument();
      // pp delta is rendered alongside the post-regime value, signed.
      expect(within(card).getByText(/\(.*pp\)/)).toBeInTheDocument();
      expect(within(card).getByTestId("regime-sizing-chart")).toBeInTheDocument();
      expect(within(card).getByTestId("regime-sizing-meta-label")).toHaveTextContent(
        "Meta-label composite currently 1.000",
      );
      // Never a raw NaN anywhere in the card (the direct regression test for
      // the legacy panel's asymmetric pre/post NaN-check bug).
      expect(card.textContent).not.toMatch(/NaN/i);
    });

    it("a symbol missing the sizing decomposition (DUK) renders the honest unavailable message, never a fabricated or NaN value", async () => {
      renderSymbol("DUK");
      const card = (
        await screen.findByRole("heading", { name: "Regime sizing impact" })
      ).closest("section") as HTMLElement;

      expect(card.textContent).toContain(
        "Pre/post-regime Kelly Target breakdown is not available for DUK",
      );
      expect(within(card).queryByTestId("regime-sizing-chart")).not.toBeInTheDocument();
      expect(within(card).queryByText("Kelly Target (pre-regime)")).not.toBeInTheDocument();
      expect(card.textContent).not.toMatch(/NaN/i);
    });
  });
});
