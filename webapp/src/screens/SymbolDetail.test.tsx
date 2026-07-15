/**
 * SymbolDetail.test.tsx — renders against the real mock API. Covers the header,
 * the Stockpy reverse cross-link ("Held by Pilots" → links into a Pilot), the
 * honesty invariant (a null factor/risk leaf renders "—", never a fabricated
 * value), and the honest 404 for an unknown ticker.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { SymbolDetail } from "./SymbolDetail";

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
});
