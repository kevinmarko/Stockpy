import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";
import { OptionsChain } from "./OptionsChain";
import { api } from "../api/client";
import { DensityProvider } from "../components/DensityContext";

vi.mock("../api/client", () => ({
  api: {
    getOptionsChain: vi.fn(),
    getPaperBrokerAccount: vi.fn(),
    postOptionsOrder: vi.fn(),
    watchCandidate: vi.fn(),
    getThresholds: vi.fn(() => Promise.resolve({ VRP: 0, MAX_KELLY: 0, VIX_HIGH: 0, OPTION_MIN_IVR: 0, REGIME_LOOKAHEAD_DAYS: 0 })),
  },
}));

const mockChain = {
  symbol: "AGNC",
  spot_price: 10.96,
  expirations: ["2026-08-14", "2026-08-21"],
  calls: [
    {
      contractSymbol: "AGNC260814C00011000",
      strike: 11.0,
      bid: 0.15,
      ask: 0.20,
      lastPrice: 0.18,
      impliedVolatility: 0.22,
      volume: 100,
      openInterest: 500,
      inTheMoney: false,
      greeks: { delta: 0.45, gamma: 0.08, theta: -0.02, vega: 0.03, rho: 0.001, chanceOfProfit: 0.55 },
    },
  ],
  puts: [
    {
      contractSymbol: "AGNC260814P00010500",
      strike: 10.5,
      bid: 0.10,
      ask: 0.15,
      lastPrice: 0.12,
      impliedVolatility: 0.25,
      volume: 50,
      openInterest: 120,
      inTheMoney: false,
      greeks: { delta: -0.30, gamma: 0.05, theta: -0.01, vega: 0.02, rho: -0.001, chanceOfProfit: 0.65 },
    },
  ],
};

describe("OptionsChain screen", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getOptionsChain).mockResolvedValue(mockChain);
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 100000,
      cash: 100000,
      buying_power: 100000,
    });
  });

  it("renders options chain screen with spot price and expirations", async () => {
    render(
      <DensityProvider>
        <MemoryRouter initialEntries={["/symbol/AGNC/options"]}>
          <Routes>
            <Route path="/symbol/:ticker/options" element={<OptionsChain />} />
          </Routes>
        </MemoryRouter>
      </DensityProvider>
    );

    expect(await screen.findByText("AGNC")).toBeInTheDocument();
    expect(screen.getByText("$10.96")).toBeInTheDocument();
    expect(screen.getByText("📈 Trade AGNC Stock")).toBeInTheDocument();
  });

  it("opens stock order ticket when Trade Stock is clicked", async () => {
    render(
      <DensityProvider>
        <MemoryRouter initialEntries={["/symbol/AGNC/options"]}>
          <Routes>
            <Route path="/symbol/:ticker/options" element={<OptionsChain />} />
          </Routes>
        </MemoryRouter>
      </DensityProvider>
    );

    const tradeStockBtn = await screen.findByText("📈 Trade AGNC Stock");
    fireEvent.click(tradeStockBtn);

    // Should render Stock Order Ticket
    expect(await screen.findByText("Buy AGNC Stock")).toBeInTheDocument();
    expect(screen.getByText("By Dollar ($)")).toBeInTheDocument();
    expect(screen.getByText("By Shares")).toBeInTheDocument();
  });
});
