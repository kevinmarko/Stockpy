/**
 * OptionsMatrix.test.tsx — the options-premium screen must render the persisted
 * matrix honestly: null legs as "—" (never 0), a debit spread's default 0.0
 * theta as "not computed" (not a measurement), IVR labeled as REALIZED-vol rank
 * (never implied), an Iron Condor's full 4-leg structure, and — for the ATM
 * Greeks roll-up — the held set from /portfolio only, never a sum over the whole
 * universe when there is no account snapshot.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OptionsMatrix } from "./OptionsMatrix";
import { api } from "../api/client";
import type { OptionsMatrix as OptionsMatrixT, Portfolio } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <OptionsMatrix />
    </MemoryRouter>,
  );
}

describe("OptionsMatrix screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the matrix header and directive cards from the mock", async () => {
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Options premium" })).toBeInTheDocument();
    // Every mock symbol appears as a card.
    expect(await screen.findByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("MSFT")).toBeInTheDocument();
    expect(screen.getByText("XOM")).toBeInTheDocument();
  });

  it("labels IVR as a realized-vol rank, never as implied vol", async () => {
    renderScreen();
    // Persistent banner states the honest caveat.
    expect(await screen.findByText(/realized-volatility rank/i)).toBeInTheDocument();
    // The per-directive IVR row is labeled "realized-vol rank", not "implied".
    await userEvent.click(await screen.findByText("AAPL"));
    const sheet = await screen.findByRole("dialog", { name: /AAPL options directive/ });
    expect(within(sheet).getByText(/IVR \(realized-vol rank\)/i)).toBeInTheDocument();
    expect(within(sheet).queryByText(/implied volatility rank/i)).not.toBeInTheDocument();
  });

  it("an empty matrix renders the honest reason, never a fabricated row", async () => {
    vi.spyOn(api, "getOptions").mockResolvedValueOnce({
      as_of: null,
      target_dte: null,
      vix: null,
      market_regime: null,
      directives: [],
      reason: "Options matrix not generated yet — enable OPTIONS_MATRIX_ENABLED.",
    } satisfies OptionsMatrixT);
    renderScreen();
    expect(
      await screen.findByText(/Options matrix not generated yet/),
    ).toBeInTheDocument();
  });

  it("shows a debit spread's 0.0 theta as 'not computed', but a credit spread's theta as a number", async () => {
    renderScreen();
    // NVDA = Call Debit Spread: Realizable_Daily_Theta 0.0 is a DEFAULT.
    await userEvent.click(await screen.findByText("NVDA"));
    const nvdaSheet = await screen.findByRole("dialog", { name: /NVDA options directive/ });
    expect(within(nvdaSheet).getByText(/default, not a measurement/i)).toBeInTheDocument();
    // Close, open AAPL = Put Credit Spread: theta IS a real measurement.
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByText("AAPL"));
    const aaplSheet = await screen.findByRole("dialog", { name: /AAPL options directive/ });
    expect(within(aaplSheet).queryByText(/default, not a measurement/i)).not.toBeInTheDocument();
    // The realizable-theta value (0.031) renders as a real number (split from " /day").
    expect(within(aaplSheet).getByText(/0\.031/)).toBeInTheDocument();
  });

  it("renders all four Iron Condor legs (Short_Strike alone would show only two)", async () => {
    renderScreen();
    await userEvent.click(await screen.findByText("MSFT"));
    const sheet = await screen.findByRole("dialog", { name: /MSFT options directive/ });
    // 4 legs -> a Legs table with 4 body rows (header + 4).
    const rows = within(sheet).getAllByRole("row");
    expect(rows.length).toBe(5); // 1 header + 4 legs
    // Iron Condor legs omit Delta -> Δ column shows "—", never 0.00.
    expect(within(sheet).getAllByText("—").length).toBeGreaterThan(0);
  });

  it("filter chips narrow the visible cards", async () => {
    renderScreen();
    await screen.findByText("AAPL");
    // "Flagged" filter -> only KO (Integrity_OK false) + ZZZ (error stub).
    await userEvent.click(screen.getByRole("button", { name: /^Flagged/ }));
    await waitFor(() => {
      expect(screen.getByText("KO")).toBeInTheDocument();
      expect(screen.queryByText("AAPL")).not.toBeInTheDocument();
    });
  });

  it("ATM Greeks roll-up: with no account snapshot (404), renders the honest empty state — never a whole-universe sum", async () => {
    vi.spyOn(api, "getPortfolio").mockRejectedValue(
      Object.assign(new Error("no snapshot"), { status: 404 }),
    );
    renderScreen();
    await screen.findByText("AAPL");
    await userEvent.click(screen.getByRole("button", { name: /ATM Greeks roll-up/ }));
    expect(await screen.findByText(/No account snapshot/i)).toBeInTheDocument();
    // No summed-greeks label leaks through.
    expect(screen.queryByText(/Σ Δ delta/)).not.toBeInTheDocument();
  });

  it("ATM Greeks roll-up: sums only held ∩ actionable, excluding non-held and Cash", async () => {
    const held: Portfolio = {
      total_equity: 1000,
      buying_power: 100,
      total_unrealized_pl: 0,
      total_dividends: 0,
      position_count: 2,
      source: "cache",
      fetched_at: new Date().toISOString(),
      positions: [
        { symbol: "AAPL", qty: 1, avg_cost: 1, current_price: 1, market_value: 1, unrealized_pl: 0, unrealized_pl_pct: 0 },
        { symbol: "XOM", qty: 1, avg_cost: 1, current_price: 1, market_value: 1, unrealized_pl: 0, unrealized_pl_pct: 0 },
      ],
    };
    vi.spyOn(api, "getPortfolio").mockResolvedValue(held);
    renderScreen();
    await screen.findByText("AAPL");
    await userEvent.click(screen.getByRole("button", { name: /ATM Greeks roll-up/ }));
    // AAPL is held+actionable (included); XOM is held but Cash (excluded) ->
    // exactly 1 symbol contributes.
    expect(await screen.findByText(/across 1 held symbol/)).toBeInTheDocument();
    expect(screen.getByText(/Σ Δ delta/)).toBeInTheDocument();
  });
});
