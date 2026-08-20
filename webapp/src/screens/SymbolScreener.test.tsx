/**
 * SymbolScreener.test.tsx — free-text name/ticker search plus a
 * sector/industry/market-cap/price/beta/dividend screener, independent of
 * the tracked pipeline universe. Exercises the mock fixture (real mock API,
 * matching DataExplorer.test.tsx's convention), the honest-empty-result
 * branches, the filter presets, and the two handoff actions into Paper
 * Broker (Quick Trade / Strategy Scan) via URL query params.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SymbolScreener } from "./SymbolScreener";
import { api } from "../api/client";

const mockNavigate = vi.fn();
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useNavigate: () => mockNavigate };
});

function renderScreen() {
  return render(
    <MemoryRouter>
      <SymbolScreener />
    </MemoryRouter>
  );
}

describe("SymbolScreener screen (real mock API)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockNavigate.mockClear();
  });

  it("renders the search box and filter form", async () => {
    renderScreen();
    expect(screen.getByLabelText("Company name or ticker")).toBeInTheDocument();
    // Sector/industry dropdowns populate from the mock's honest fixture enum.
    await waitFor(() => {
      expect(screen.getByLabelText("Sector")).toBeInTheDocument();
    });
  });

  it("search: finds a matching symbol by company name", async () => {
    renderScreen();
    fireEvent.change(screen.getByLabelText("Company name or ticker"), { target: { value: "Apple" } });
    fireEvent.click(screen.getByText("Search"));

    expect(await screen.findByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("Apple Inc.")).toBeInTheDocument();
  });

  it("search: shows an honest empty state when nothing matches", async () => {
    renderScreen();
    fireEvent.change(screen.getByLabelText("Company name or ticker"), { target: { value: "ZZZZNOTREAL" } });
    fireEvent.click(screen.getByText("Search"));

    expect(await screen.findByText("No matches")).toBeInTheDocument();
    expect(screen.getByText("No matching symbols found.")).toBeInTheDocument();
  });

  it("preset: 'Large Cap Tech' pre-fills filters and runs the screener", async () => {
    renderScreen();
    fireEvent.click(screen.getByText("Large Cap Tech"));

    // AAPL/MSFT/NVDA are all Technology, market cap > $100B in the fixture.
    expect(await screen.findByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("MSFT")).toBeInTheDocument();
    expect(screen.getByText("NVDA")).toBeInTheDocument();
    // NODIVCO is Technology but well under $100B market cap -- must be excluded.
    expect(screen.queryByText("NODIVCO")).not.toBeInTheDocument();
  });

  it("filters: a combination matching nothing shows an honest reason, not a fabricated row", async () => {
    renderScreen();
    fireEvent.change(screen.getByLabelText("Price ≥ ($)"), { target: { value: "999999" } });
    fireEvent.click(screen.getByText("Apply filters"));

    expect(await screen.findByText("No symbols matched")).toBeInTheDocument();
    expect(screen.getByText("No symbols matched these filters.")).toBeInTheDocument();
  });

  it("Quick Trade button navigates to Paper Broker with ?quickTradeSymbol=", async () => {
    renderScreen();
    fireEvent.change(screen.getByLabelText("Company name or ticker"), { target: { value: "Apple" } });
    fireEvent.click(screen.getByText("Search"));
    await screen.findByText("AAPL");

    fireEvent.click(screen.getByText("Quick Trade →"));
    expect(mockNavigate).toHaveBeenCalledWith("/paper-broker?quickTradeSymbol=AAPL");
  });

  it("selecting rows and Send to Strategy Scan navigates with ?scanSymbols=", async () => {
    renderScreen();
    fireEvent.click(screen.getByText("Large Cap Tech"));
    await screen.findByText("AAPL");

    fireEvent.click(screen.getByLabelText("Select AAPL"));
    fireEvent.click(screen.getByLabelText("Select MSFT"));

    const sendBtn = screen.getByTestId("send-to-strategy-scan");
    expect(sendBtn).not.toBeDisabled();
    fireEvent.click(sendBtn);

    expect(mockNavigate).toHaveBeenCalledWith("/paper-broker?scanSymbols=AAPL%2CMSFT");
  });

  it("Send to Strategy Scan is disabled with nothing selected", async () => {
    renderScreen();
    fireEvent.click(screen.getByText("Large Cap Tech"));
    await screen.findByText("AAPL");
    expect(screen.getByTestId("send-to-strategy-scan")).toBeDisabled();
  });

  it("a null field (e.g. QQQ's sector/market-cap) renders '—', never a fabricated value", async () => {
    renderScreen();
    // Uncheck "Exclude ETFs / funds" (on by default) so the fixture's one
    // ETF row (QQQ, with a genuinely null sector/industry/market_cap) is
    // included in the results table.
    fireEvent.click(screen.getByLabelText("Exclude ETFs / funds"));
    fireEvent.click(screen.getByText("Apply filters"));

    const row = (await screen.findByText("QQQ")).closest("tr");
    expect(row).not.toBeNull();
    expect(row!.textContent).toContain("—");
  });

  it("filter-query network failure shows an honest error, not a silent empty table", async () => {
    vi.spyOn(api, "getScreenerResults").mockRejectedValueOnce(new Error("network boom"));
    renderScreen();
    fireEvent.click(screen.getByText("Apply filters"));
    expect(await screen.findByText("Screener query failed")).toBeInTheDocument();
    expect(screen.getByText("network boom")).toBeInTheDocument();
  });
});
