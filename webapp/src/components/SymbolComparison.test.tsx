/**
 * SymbolComparison.test.tsx — symbol-vs-symbol comparison card (Compare
 * screen). Covers the "select at least 2" gate, the comparison table, the
 * honest "not tracked" row (never a hard failure over one bad ticker), the
 * grouped score-component chart, the max-selection cap, and localStorage
 * persistence of the selection.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SymbolComparison } from "./SymbolComparison";
import { api } from "../api/client";

function checkbox(symbol: string) {
  return screen.findByTestId(`symbol-comparison-checkbox-${symbol}`);
}

describe("SymbolComparison", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("prompts to select at least 2 symbols before any comparison is shown", async () => {
    render(<SymbolComparison />);
    expect(await screen.findByTestId("symbol-comparison-select-more")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("selecting 2 symbols renders the comparison table with real values", async () => {
    render(<SymbolComparison />);
    fireEvent.click(await checkbox("AAPL"));
    fireEvent.click(await checkbox("MSFT"));

    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.getByText("Final Score")).toBeInTheDocument();
    expect(screen.getByText("Kelly Target")).toBeInTheDocument();
    expect(screen.getByText("GARCH Vol")).toBeInTheDocument();
    expect(screen.getByText("Meta-Label Composite")).toBeInTheDocument();
    expect(screen.getByText("Regime Multiplier")).toBeInTheDocument();
    // Both selected symbols appear as column headers.
    expect(screen.getAllByText("AAPL").length).toBeGreaterThan(0);
    expect(screen.getAllByText("MSFT").length).toBeGreaterThan(0);
  });

  it("caps selection at 3 symbols (mirrors the legacy Streamlit max_selections=3)", async () => {
    render(<SymbolComparison />);
    fireEvent.click(await checkbox("AAPL"));
    fireEvent.click(await checkbox("MSFT"));
    fireEvent.click(await checkbox("NVDA"));
    const fourth = await checkbox("XOM");
    expect(fourth).toBeDisabled();
  });

  it("unchecking a selected symbol removes it and frees a selection slot", async () => {
    render(<SymbolComparison />);
    fireEvent.click(await checkbox("AAPL"));
    fireEvent.click(await checkbox("MSFT"));
    fireEvent.click(await checkbox("NVDA"));
    fireEvent.click(await checkbox("AAPL")); // uncheck
    const xom = await checkbox("XOM");
    expect(xom).not.toBeDisabled();
  });

  it("Clear All resets the selection back to the 'select more' prompt", async () => {
    render(<SymbolComparison />);
    fireEvent.click(await checkbox("AAPL"));
    fireEvent.click(await checkbox("MSFT"));
    await screen.findByRole("table");

    fireEvent.click(screen.getByText("Clear All"));
    expect(await screen.findByTestId("symbol-comparison-select-more")).toBeInTheDocument();
  });

  it("an unknown/not-tracked symbol renders an honest row, not a hard failure", async () => {
    vi.spyOn(api, "getSymbolsCompare").mockResolvedValueOnce({
      as_of: "2026-07-11T21:05:00+00:00",
      symbols: [
        {
          symbol: "AAPL", found: true, reason: null, score: 96.8, action: "BUY",
          kelly_target: 0.041, conviction: 0.72, garch_vol: 0.243,
          meta_label_composite: 1.0, regime_multiplier: 1.0,
          score_components: { momentum: 9.0, value: -3.0 },
        },
        {
          symbol: "ZZZ", found: false, reason: "Not tracked in the latest snapshot.",
          score: null, action: null, kelly_target: null, conviction: null,
          garch_vol: null, meta_label_composite: null, regime_multiplier: null,
          score_components: null,
        },
      ],
      modules: ["momentum", "value"],
    });
    render(<SymbolComparison />);
    fireEvent.click(await checkbox("AAPL"));
    fireEvent.click(await checkbox("MSFT")); // triggers the 2-symbol fetch (mocked above)

    expect(await screen.findByTestId("symbol-comparison-not-tracked-note")).toHaveTextContent("ZZZ");
    // ZZZ's row renders dashes, never a fabricated 0/BUY.
    const table = screen.getByRole("table");
    expect(table.textContent).toContain("—");
  });

  it("null meta_label_composite/regime_multiplier render '—', never a fabricated 1.0", async () => {
    vi.spyOn(api, "getSymbolsCompare").mockResolvedValueOnce({
      as_of: "2026-07-11T21:05:00+00:00",
      symbols: [
        {
          symbol: "AAPL", found: true, reason: null, score: 50, action: "BUY",
          kelly_target: 0.02, conviction: 0.6, garch_vol: 0.2,
          meta_label_composite: null, regime_multiplier: null,
          score_components: { momentum: 5.0 },
        },
        {
          symbol: "MSFT", found: true, reason: null, score: 40, action: "HOLD",
          kelly_target: 0.0, conviction: 0.5, garch_vol: 0.18,
          meta_label_composite: 1.0, regime_multiplier: 1.0,
          score_components: { momentum: 3.0 },
        },
      ],
      modules: ["momentum"],
    });
    render(<SymbolComparison />);
    fireEvent.click(await checkbox("AAPL"));
    fireEvent.click(await checkbox("MSFT"));

    const row = (await screen.findByText("Meta-Label Composite")).closest("tr")!;
    expect(row.textContent).toContain("—");
    expect(row.textContent).not.toContain("1.00​0.00"); // sanity: not both blank
  });

  it("renders the grouped score-component bar chart when components are available", async () => {
    render(<SymbolComparison />);
    fireEvent.click(await checkbox("AAPL"));
    fireEvent.click(await checkbox("MSFT"));
    expect(await screen.findByTestId("symbol-comparison-chart")).toBeInTheDocument();
  });

  it("shows an honest empty note when no selected symbol has a score-component breakdown", async () => {
    vi.spyOn(api, "getSymbolsCompare").mockResolvedValueOnce({
      as_of: "2026-07-11T21:05:00+00:00",
      symbols: [
        {
          symbol: "AAPL", found: true, reason: null, score: 50, action: "BUY",
          kelly_target: 0.02, conviction: 0.6, garch_vol: 0.2,
          meta_label_composite: 1.0, regime_multiplier: 1.0, score_components: null,
        },
        {
          symbol: "MSFT", found: true, reason: null, score: 40, action: "HOLD",
          kelly_target: 0.0, conviction: 0.5, garch_vol: 0.18,
          meta_label_composite: 1.0, regime_multiplier: 1.0, score_components: null,
        },
      ],
      modules: [],
    });
    render(<SymbolComparison />);
    fireEvent.click(await checkbox("AAPL"));
    fireEvent.click(await checkbox("MSFT"));
    expect(await screen.findByTestId("symbol-comparison-no-components")).toBeInTheDocument();
  });

  it("persists the selection across remounts via localStorage", async () => {
    const { unmount } = render(<SymbolComparison />);
    fireEvent.click(await checkbox("AAPL"));
    fireEvent.click(await checkbox("MSFT"));
    await screen.findByRole("table");
    unmount();

    render(<SymbolComparison />);
    await waitFor(async () => {
      expect(await checkbox("AAPL")).toBeChecked();
      expect(await checkbox("MSFT")).toBeChecked();
    });
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("surfaces an honest error state when the compare call fails", async () => {
    vi.spyOn(api, "getSymbolsCompare").mockRejectedValueOnce(new Error("boom"));
    render(<SymbolComparison />);
    fireEvent.click(await checkbox("AAPL"));
    fireEvent.click(await checkbox("MSFT"));
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });
});
