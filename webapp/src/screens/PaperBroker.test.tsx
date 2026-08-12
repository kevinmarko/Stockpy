import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router";
import { PaperBroker } from "./PaperBroker";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    getPaperBrokerAccount: vi.fn(),
    getPaperBrokerPositions: vi.fn(),
    getPaperBrokerOrders: vi.fn(),
    resetPaperBroker: vi.fn(),
    getThresholds: vi.fn(() => Promise.resolve({ VRP: 0, MAX_KELLY: 0, VIX_HIGH: 0, OPTION_MIN_IVR: 0, REGIME_LOOKAHEAD_DAYS: 0 })),
  },
}));

describe("PaperBroker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders account, positions, and orders", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 105000,
      cash: 50000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([
      {
        symbol: "AAPL",
        qty: 100,
        avg_cost: 150,
        current_price: 155,
        market_value: 15500,
        unrealized_pl: 500,
        unrealized_pl_pct: 0.0333,
      },
    ]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([
      {
        symbol: "AAPL",
        side: "BUY",
        qty: 100,
        status: "filled",
        filled_qty: 100,
        filled_avg_price: 150,
        order_id: "123",
        price: 150,
        created_at: "2026-08-12T00:00:00Z",
      },
    ]);

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    // Summary cards
    expect(await screen.findByText("$105,000.00")).toBeInTheDocument();
    expect(screen.getByText("$50,000.00")).toBeInTheDocument();

    // Positions
    expect(screen.getAllByText("AAPL")).toHaveLength(2); // Position and order
    expect(screen.getByText("$155.00")).toBeInTheDocument(); // current price
    
    // Orders
    expect(screen.getByText("BUY")).toBeInTheDocument();
  });

  it("opens reset modal and calls reset", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 105000,
      cash: 50000,
      buying_power: 100000,
    });
    vi.mocked(api.getPaperBrokerPositions).mockResolvedValue([]);
    vi.mocked(api.getPaperBrokerOrders).mockResolvedValue([]);
    vi.mocked(api.resetPaperBroker).mockResolvedValue({ status: "reset", cash: 100000 });

    render(
      <MemoryRouter>
        <PaperBroker />
      </MemoryRouter>
    );

    expect(await screen.findByText("$105,000.00")).toBeInTheDocument();

    const resetBtn = screen.getByText("Reset Paper Account");
    fireEvent.click(resetBtn);

    expect(screen.getByText("Reset Paper Broker", { selector: "h2" })).toBeInTheDocument();

    const confirmBtn = screen.getByRole("button", { name: "Reset" });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(api.resetPaperBroker).toHaveBeenCalledWith(100000);
    });
  });
});
