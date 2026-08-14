import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { OptionsOrderTicket } from "./OptionsOrderTicket";
import { api } from "../../api/client";
import { OptionContract } from "../../api/types";

vi.mock("../../api/client", () => ({
  api: {
    getPaperBrokerAccount: vi.fn(),
    postOptionsOrder: vi.fn(),
    watchCandidate: vi.fn(),
  },
}));

const mockContract: OptionContract = {
  contractSymbol: "AGNC260814P00010500",
  strike: 10.5,
  bid: 0.10,
  ask: 0.15,
  lastPrice: 0.12,
  impliedVolatility: 0.25,
  volume: 50,
  openInterest: 120,
  inTheMoney: false,
  greeks: {
    delta: -0.30,
    gamma: 0.05,
    theta: -0.01,
    vega: 0.02,
    rho: -0.001,
    chanceOfProfit: 0.65,
  }
};

describe("OptionsOrderTicket", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 100000,
      cash: 100000,
      buying_power: 100000,
    });
  });

  it("renders option ticket with dollar amount sizing and contract calculation", async () => {
    const onClear = vi.fn();
    render(
      <OptionsOrderTicket
        symbol="AGNC"
        expiration="2026-08-14"
        legs={[{ contract: mockContract, type: 'put', action: 'Buy' }]}
        spotPrice={10.96}
        onClear={onClear}
      />
    );

    // Check title
    expect(screen.getByText(/Buy AGNC \$10.50 Put 2026-08-14/i)).toBeInTheDocument();

    // Check sizing mode buttons
    expect(screen.getByText("By Dollar ($)")).toBeInTheDocument();
    expect(screen.getByText("By Contracts")).toBeInTheDocument();

    // Check preset dollar chips
    expect(screen.getByText("$100")).toBeInTheDocument();
    expect(screen.getByText("$500")).toBeInTheDocument();

    // Available cash & 75% Cash chip
    expect(await screen.findByText("$100,000.00")).toBeInTheDocument();
    expect(await screen.findByText("75% Cash")).toBeInTheDocument();
  });

  it("switches to quantity sizing mode and updates stepper", async () => {
    const onClear = vi.fn();
    render(
      <OptionsOrderTicket
        symbol="AGNC"
        expiration="2026-08-14"
        legs={[{ contract: mockContract, type: 'put', action: 'Buy' }]}
        spotPrice={10.96}
        onClear={onClear}
      />
    );

    // Switch to By Contracts
    fireEvent.click(screen.getByText("By Contracts"));

    // Check unit reminder
    expect(screen.getByText(/1 Contract = 100 Shares/i)).toBeInTheDocument();

    // Click increment stepper
    const plusBtn = screen.getByText("+");
    fireEvent.click(plusBtn);

    // Total should update
    expect(screen.getByText(/2 contracts/i)).toBeInTheDocument();
  });

  it("supports stock trading mode with dollar amount and shares", async () => {
    const onClear = vi.fn();
    render(
      <OptionsOrderTicket
        symbol="AGNC"
        assetType="stock"
        spotPrice={10.0}
        onClear={onClear}
      />
    );

    // Check title for stock
    expect(screen.getByText(/Buy AGNC Stock/i)).toBeInTheDocument();
    expect(screen.getByText("By Shares")).toBeInTheDocument();

    // Sizing calculation: $500 / $10 = 50 shares
    expect(screen.getAllByText(/50 shares/i).length).toBeGreaterThanOrEqual(1);
  });

  it("submits paper order successfully", async () => {
    vi.mocked(api.postOptionsOrder).mockResolvedValue({
      ok: true,
      order_id: "order_123",
      message: "Order filled",
    });

    const onClear = vi.fn();
    render(
      <OptionsOrderTicket
        symbol="AGNC"
        expiration="2026-08-14"
        legs={[{ contract: mockContract, type: 'put', action: 'Buy' }]}
        spotPrice={10.96}
        onClear={onClear}
      />
    );

    // Submit button
    const submitBtn = screen.getByRole("button", { name: /Paper Buy/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(api.postOptionsOrder).toHaveBeenCalledWith(expect.objectContaining({
        symbol: "AGNC",
        asset_type: "option",
        isLive: false,
      }));
    });
  });

  it("adds symbol to watchlist on button click", async () => {
    vi.mocked(api.watchCandidate).mockResolvedValue({
      symbol: "AGNC",
      added: ["AGNC"],
      already_present: [],
      watchlist_file: "watchlist.txt",
      applies: "next_pipeline_run",
      note: "Added to watchlist",
    });

    const onClear = vi.fn();
    render(
      <OptionsOrderTicket
        symbol="AGNC"
        expiration="2026-08-14"
        legs={[{ contract: mockContract, type: 'put', action: 'Buy' }]}
        spotPrice={10.96}
        onClear={onClear}
      />
    );

    const watchBtn = screen.getByText("+ Add to Watchlist");
    fireEvent.click(watchBtn);

    await waitFor(() => {
      expect(api.watchCandidate).toHaveBeenCalledWith("AGNC");
      expect(screen.getByText("✓ Added to Watchlist")).toBeInTheDocument();
    });
  });
});
