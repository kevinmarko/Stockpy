import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { ZeroDteDesk } from "./ZeroDteDesk";
import { api } from "../../api/client";
import type { ZeroDteSignalResponse } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    getZeroDteSignals: vi.fn(),
    executeZeroDteTrade: vi.fn(),
  },
}));

const mockZeroDteResponse: ZeroDteSignalResponse = {
  signals: [
    {
      symbol: "SPY",
      spot_price: 546.50,
      timestamp: "10:14:32",
      opening_range_high: 545.80,
      opening_range_low: 544.10,
      opening_range_width_pct: 0.0031,
      ttm_squeeze_active: false,
      ttm_squeeze_bars: 8,
      momentum_direction: "BULLISH_BREAKOUT",
      momentum_score: 0.86,
      relative_volume_15m: 2.15,
      suggested_action: "BUY_CALL",
      recommended_contract: {
        option_type: "CALL",
        strike: 547.0,
        expiration: "2026-08-14",
        dte: 0,
        delta: 0.51,
        gamma: 0.082,
        theta: -1.45,
        vega: 0.12,
        bid: 1.85,
        ask: 1.90,
        mid: 1.88,
        implied_vol: 0.18,
        target_price: 3.29,
        stop_loss_price: 1.32,
        hard_exit_time: "15:45 ET",
      },
      trigger_reason: "15-min ORB breakout above $545.80 on 2.15x volume acceleration with TTM Squeeze release.",
    },
    {
      symbol: "TSLA",
      spot_price: 214.30,
      timestamp: "10:13:50",
      opening_range_high: 221.50,
      opening_range_low: 216.00,
      opening_range_width_pct: 0.025,
      ttm_squeeze_active: false,
      ttm_squeeze_bars: 5,
      momentum_direction: "BEARISH_BREAKDOWN",
      momentum_score: -0.82,
      relative_volume_15m: 2.40,
      suggested_action: "BUY_PUT",
      recommended_contract: {
        option_type: "PUT",
        strike: 215.0,
        expiration: "2026-08-14",
        dte: 0,
        delta: -0.48,
        gamma: 0.058,
        theta: -2.10,
        vega: 0.18,
        bid: 2.40,
        ask: 2.48,
        mid: 2.44,
        implied_vol: 0.62,
        target_price: 4.27,
        stop_loss_price: 1.71,
        hard_exit_time: "15:45 ET",
      },
      trigger_reason: "15-min ORB breakdown below $216.00 with heavy 2.40x selling momentum.",
    },
  ],
  as_of: "2026-08-14T14:00:00Z",
};

describe("ZeroDteDesk", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getZeroDteSignals).mockResolvedValue(mockZeroDteResponse);
    vi.mocked(api.executeZeroDteTrade).mockResolvedValue({
      ok: true,
      order_id: "ord_0dte_123",
      symbol: "SPY",
      option_type: "CALL",
      strike: 547.0,
      contracts: 5,
      fill_price: 1.88,
      profit_target_price: 3.29,
      stop_loss_price: 1.32,
      hard_exit_time: "15:45 ET",
      strategy: "0DTE Intraday Momentum Breakout",
      message: "Executed 5x SPY 547 CALL @ $1.88. Profit target set at $3.29 (+75%), Stop loss at $1.32 (-30%), Hard Time Stop at 15:45 ET.",
    });
  });

  it("renders desk title, 15-min ORB levels, and squeeze status", async () => {
    render(<ZeroDteDesk />);

    expect(await screen.findByText(/0DTE Intraday Momentum & Breakout Desk/i)).toBeInTheDocument();
    expect(screen.getByText("15-Min ORB Levels")).toBeInTheDocument();
    expect(screen.getByText("$545.80")).toBeInTheDocument(); // High
    expect(screen.getByText("$544.10")).toBeInTheDocument(); // Low
    expect(screen.getByText(/TTM Squeeze Released/i)).toBeInTheDocument();
    expect(screen.getByText("BULLISH BREAKOUT")).toBeInTheDocument();
    expect(screen.getByText("2.15x Vol Thrust")).toBeInTheDocument();
  });

  it("renders recommended 0DTE contract with profit target, stop loss, and hard time stop", async () => {
    render(<ZeroDteDesk />);

    expect(await screen.findByText(/Recommended 0DTE Contract Execution/i)).toBeInTheDocument();
    expect(screen.getByText(/SPY \$547\.0 CALL/i)).toBeInTheDocument();
    expect(screen.getByText("$3.29")).toBeInTheDocument(); // Target
    expect(screen.getByText("$1.32")).toBeInTheDocument(); // Stop
    expect(screen.getByText(/15:45 ET Auto-Close/i)).toBeInTheDocument();
  });

  it("switches symbols when symbol pill clicked", async () => {
    render(<ZeroDteDesk />);

    await screen.findByText("15-Min ORB Levels");
    const tslaPill = screen.getByRole("button", { name: /TSLA/i });
    fireEvent.click(tslaPill);

    expect(await screen.findByText(/TSLA \$215\.0 PUT/i)).toBeInTheDocument();
    expect(screen.getByText("$221.50")).toBeInTheDocument(); // TSLA High
    expect(screen.getByText("$216.00")).toBeInTheDocument(); // TSLA Low
  });

  it("adjusts contract size with position sizing buttons", async () => {
    render(<ZeroDteDesk />);

    await screen.findByText("15-Min ORB Levels");
    const btn10x = screen.getByRole("button", { name: "10x" });
    fireEvent.click(btn10x);

    expect(screen.getByText(/\$1880\.00/i)).toBeInTheDocument(); // 10 * 1.88 * 100
  });

  it("executes 0DTE breakout trade and triggers callbacks", async () => {
    const onTradeMock = vi.fn();
    render(<ZeroDteDesk onTradeExecuted={onTradeMock} />);

    const execBtn = await screen.findByRole("button", { name: /⚡ Trade 0DTE Breakout/i });
    fireEvent.click(execBtn);

    await waitFor(() => {
      expect(api.executeZeroDteTrade).toHaveBeenCalledWith(
        expect.objectContaining({
          symbol: "SPY",
          option_type: "CALL",
          strike: 547.0,
          contracts: 5,
        }),
        false
      );
      expect(onTradeMock).toHaveBeenCalledWith(
        expect.objectContaining({
          ok: true,
          symbol: "SPY",
        })
      );
    });

    expect(await screen.findByText(/Executed 5x SPY 547 CALL/i)).toBeInTheDocument();
  });

  it("shows an override affordance when blocked by the deployability gate, and executes on confirmed override", async () => {
    const onTradeMock = vi.fn();
    vi.mocked(api.executeZeroDteTrade).mockResolvedValueOnce({
      ok: false,
      blocked: true,
      message:
        "Strategy has an UNGATEABLE_DATA_GAP and is blocked by default. Pass override_deployability_gate=True to execute.",
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<ZeroDteDesk onTradeExecuted={onTradeMock} />);

    const execBtn = await screen.findByRole("button", { name: /⚡ Trade 0DTE Breakout/i });
    fireEvent.click(execBtn);

    expect(await screen.findByText(/UNGATEABLE_DATA_GAP/i)).toBeInTheDocument();
    expect(api.executeZeroDteTrade).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ symbol: "SPY" }),
      false
    );

    const overrideBtn = await screen.findByRole("button", { name: /⚠️ Override & Execute/i });
    fireEvent.click(overrideBtn);
    expect(confirmSpy).toHaveBeenCalled();

    await waitFor(() => {
      expect(api.executeZeroDteTrade).toHaveBeenNthCalledWith(
        2,
        expect.objectContaining({ symbol: "SPY" }),
        true
      );
      expect(onTradeMock).toHaveBeenCalledWith(expect.objectContaining({ ok: true, symbol: "SPY" }));
    });

    confirmSpy.mockRestore();
  });

  it("does not execute when the override confirmation dialog is declined", async () => {
    vi.mocked(api.executeZeroDteTrade).mockResolvedValueOnce({
      ok: false,
      blocked: true,
      message:
        "Strategy has an UNGATEABLE_DATA_GAP and is blocked by default. Pass override_deployability_gate=True to execute.",
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<ZeroDteDesk />);

    const execBtn = await screen.findByRole("button", { name: /⚡ Trade 0DTE Breakout/i });
    fireEvent.click(execBtn);

    const overrideBtn = await screen.findByRole("button", { name: /⚠️ Override & Execute/i });
    fireEvent.click(overrideBtn);

    expect(confirmSpy).toHaveBeenCalled();
    expect(api.executeZeroDteTrade).toHaveBeenCalledTimes(1);

    confirmSpy.mockRestore();
  });

  it("calls onClose when close button clicked", async () => {
    const handleClose = vi.fn();
    render(<ZeroDteDesk onClose={handleClose} />);

    const closeBtn = await screen.findByText("✕ Close");
    fireEvent.click(closeBtn);

    expect(handleClose).toHaveBeenCalled();
  });
});
