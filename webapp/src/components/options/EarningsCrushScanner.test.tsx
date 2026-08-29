import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { EarningsCrushScanner } from "./EarningsCrushScanner";
import { api } from "../../api/client";
import type { EarningsCrushCandidatesResponse } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    getEarningsCrushCandidates: vi.fn(),
    executeEarningsCrushTrade: vi.fn(),
  },
}));

const mockCandidatesResponse: EarningsCrushCandidatesResponse = {
  candidates: [
    {
      symbol: "NVDA",
      company_name: "NVIDIA Corporation",
      report_date: "2026-08-20",
      report_timing: "AMC",
      spot_price: 128.50,
      atm_iv: 0.68,
      dte: 3,
      expected_move_dollar: 11.20,
      expected_move_pct: 0.087,
      median_realized_move_pct: 0.054,
      crush_edge_ratio: 1.61,
      suggested_strategy: "Iron Condor",
      short_put_strike: 118,
      put_wing_strike: 112,
      short_call_strike: 139,
      call_wing_strike: 145,
      expiration: "2026-08-21",
      estimated_credit: 2.35,
      edge_passed: true,
      historical_moves: [4.2, 5.8, 7.1, 3.9, 5.4, 6.2, 4.8, 5.1],
    },
    {
      symbol: "AAPL",
      company_name: "Apple Inc.",
      report_date: "2026-08-27",
      report_timing: "AMC",
      spot_price: 224.50,
      atm_iv: 0.32,
      dte: 10,
      expected_move_dollar: 8.10,
      expected_move_pct: 0.036,
      median_realized_move_pct: 0.034,
      crush_edge_ratio: 1.06,
      suggested_strategy: "Iron Condor",
      short_put_strike: 215,
      put_wing_strike: 210,
      short_call_strike: 235,
      call_wing_strike: 240,
      expiration: "2026-08-28",
      estimated_credit: 1.15,
      edge_passed: false,
      historical_moves: [2.8, 3.5, 3.4, 4.1, 2.9, 3.1, 3.8, 3.2],
    },
  ],
  count: 2,
  as_of: "2026-08-14T14:00:00Z",
};

describe("EarningsCrushScanner", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getEarningsCrushCandidates).mockResolvedValue(mockCandidatesResponse);
    vi.mocked(api.executeEarningsCrushTrade).mockResolvedValue({
      ok: true,
      order_id: "ord_crush_123",
      symbol: "NVDA",
      strategy: "Iron Condor",
      net_credit: 2.35,
      message: "Successfully executed Earnings Crush Iron Condor on NVDA for $2.35 net credit.",
    });
  });

  it("renders scanner title, summary badges, and candidate table", async () => {
    render(<EarningsCrushScanner />);

    expect(await screen.findByText(/Earnings Volatility Crush Scanner/i)).toBeInTheDocument();
    expect(screen.getByText("NVDA")).toBeInTheDocument();
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("1.61x")).toBeInTheDocument();
    expect(screen.getByText("1.06x")).toBeInTheDocument();
    expect(screen.getByText("±8.7%")).toBeInTheDocument();
  });

  it("filters candidates by edge ratio threshold (≥1.25x)", async () => {
    render(<EarningsCrushScanner />);

    await screen.findByText("NVDA");
    const edgeBtn = screen.getByRole("button", { name: /Edge ≥ 1.25x Only/i });
    fireEvent.click(edgeBtn);

    expect(screen.getByText("NVDA")).toBeInTheDocument();
    expect(screen.queryByText("AAPL")).not.toBeInTheDocument();
  });

  it("filters candidates by search input", async () => {
    render(<EarningsCrushScanner />);

    await screen.findByText("NVDA");
    const searchInput = screen.getByPlaceholderText(/Filter by ticker or name/i);
    fireEvent.change(searchInput, { target: { value: "Apple" } });

    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.queryByText("NVDA")).not.toBeInTheDocument();
  });

  it("expands 8-quarter historical move distribution when row clicked", async () => {
    render(<EarningsCrushScanner />);

    const rowHeader = await screen.findByText("NVDA");
    fireEvent.click(rowHeader);

    expect(await screen.findByText(/Prior 8 Quarters Realized Post-Earnings Move/i)).toBeInTheDocument();
    expect(screen.getByText("Q-8")).toBeInTheDocument();
  });

  it("executes earnings crush spread and triggers callbacks", async () => {
    const onTradeMock = vi.fn();
    render(<EarningsCrushScanner onTradeExecuted={onTradeMock} />);

    const tradeButtons = await screen.findAllByRole("button", { name: /⚡ Trade Crush Spread/i });
    fireEvent.click(tradeButtons[0]);

    await waitFor(() => {
      expect(api.executeEarningsCrushTrade).toHaveBeenCalledWith(mockCandidatesResponse.candidates[0], false);
      expect(onTradeMock).toHaveBeenCalledWith(
        expect.objectContaining({
          ok: true,
          symbol: "NVDA",
        })
      );
    });

    expect(await screen.findByText(/Successfully executed Earnings Crush Iron Condor on NVDA/i)).toBeInTheDocument();
  });

  it("shows an override affordance when blocked by the deployability gate, and executes on confirmed override", async () => {
    const onTradeMock = vi.fn();
    vi.mocked(api.executeEarningsCrushTrade).mockResolvedValueOnce({
      ok: false,
      blocked: true,
      message:
        "Strategy has an UNGATEABLE_DATA_GAP and is blocked by default. Pass override_deployability_gate=True to execute.",
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<EarningsCrushScanner onTradeExecuted={onTradeMock} />);

    const tradeButtons = await screen.findAllByRole("button", { name: /⚡ Trade Crush Spread/i });
    fireEvent.click(tradeButtons[0]);

    expect(await screen.findByText(/UNGATEABLE_DATA_GAP/i)).toBeInTheDocument();
    expect(api.executeEarningsCrushTrade).toHaveBeenNthCalledWith(1, mockCandidatesResponse.candidates[0], false);

    const overrideBtn = await screen.findByRole("button", { name: /⚠️ Override & Execute/i });
    fireEvent.click(overrideBtn);
    expect(confirmSpy).toHaveBeenCalled();

    await waitFor(() => {
      expect(api.executeEarningsCrushTrade).toHaveBeenNthCalledWith(2, mockCandidatesResponse.candidates[0], true);
      expect(onTradeMock).toHaveBeenCalledWith(expect.objectContaining({ ok: true, symbol: "NVDA" }));
    });

    confirmSpy.mockRestore();
  });

  it("does not execute when the override confirmation dialog is declined", async () => {
    vi.mocked(api.executeEarningsCrushTrade).mockResolvedValueOnce({
      ok: false,
      blocked: true,
      message:
        "Strategy has an UNGATEABLE_DATA_GAP and is blocked by default. Pass override_deployability_gate=True to execute.",
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<EarningsCrushScanner />);

    const tradeButtons = await screen.findAllByRole("button", { name: /⚡ Trade Crush Spread/i });
    fireEvent.click(tradeButtons[0]);

    const overrideBtn = await screen.findByRole("button", { name: /⚠️ Override & Execute/i });
    fireEvent.click(overrideBtn);

    expect(confirmSpy).toHaveBeenCalled();
    expect(api.executeEarningsCrushTrade).toHaveBeenCalledTimes(1);

    confirmSpy.mockRestore();
  });

  it("calls onClose when close button clicked", async () => {
    const handleClose = vi.fn();
    render(<EarningsCrushScanner onClose={handleClose} />);

    const closeBtn = await screen.findByText("✕ Close");
    fireEvent.click(closeBtn);

    expect(handleClose).toHaveBeenCalled();
  });
});
