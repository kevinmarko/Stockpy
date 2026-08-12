import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router";
import { LiveTradeApprovals } from "./LiveTradeApprovals";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    getPendingLiveTrades: vi.fn(),
    approveLiveTrade: vi.fn(),
    rejectLiveTrade: vi.fn(),
    getThresholds: vi.fn(() => Promise.resolve({ VRP: 0, MAX_KELLY: 0, VIX_HIGH: 0, OPTION_MIN_IVR: 0, REGIME_LOOKAHEAD_DAYS: 0 })),
  },
}));

const PROPOSAL = {
  token: "ltp_test1",
  symbol: "AAPL",
  side: "BUY",
  qty: 25,
  order_type: "limit",
  limit_price: 228.5,
  strategy_id: "momentum_12_1",
  proposed_at: "2026-08-12T00:00:00Z",
  expires_at: new Date(Date.now() + 20 * 60_000).toISOString(),
  status: "pending_approval" as const,
  approved_at: null,
  approved_by: null,
  broker_order_id: null,
  error_message: null,
};

describe("LiveTradeApprovals", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the pending proposals list", async () => {
    vi.mocked(api.getPendingLiveTrades).mockResolvedValue({ proposals: [PROPOSAL] });

    render(
      <MemoryRouter>
        <LiveTradeApprovals />
      </MemoryRouter>
    );

    expect(await screen.findByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("BUY")).toBeInTheDocument();
    expect(screen.getByText("momentum_12_1")).toBeInTheDocument();
    expect(screen.getByText("$228.50")).toBeInTheDocument();
  });

  it("renders an honest empty state when the queue is quiet", async () => {
    vi.mocked(api.getPendingLiveTrades).mockResolvedValue({ proposals: [] });

    render(
      <MemoryRouter>
        <LiveTradeApprovals />
      </MemoryRouter>
    );

    expect(await screen.findByText("No pending live-trade proposals")).toBeInTheDocument();
  });

  it("shows a null limit price as an em dash for a market order", async () => {
    vi.mocked(api.getPendingLiveTrades).mockResolvedValue({
      proposals: [{ ...PROPOSAL, token: "ltp_test2", order_type: "market", limit_price: null }],
    });

    render(
      <MemoryRouter>
        <LiveTradeApprovals />
      </MemoryRouter>
    );

    expect(await screen.findByText("market")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("approves a proposal through the confirm modal and reloads the list", async () => {
    vi.mocked(api.getPendingLiveTrades)
      .mockResolvedValueOnce({ proposals: [PROPOSAL] })
      .mockResolvedValueOnce({ proposals: [] });
    vi.mocked(api.approveLiveTrade).mockResolvedValue({
      ...PROPOSAL,
      status: "approved",
      approved_at: "2026-08-12T00:05:00Z",
      approved_by: "operator",
    });

    render(
      <MemoryRouter>
        <LiveTradeApprovals />
      </MemoryRouter>
    );

    expect(await screen.findByText("AAPL")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Approve AAPL BUY", { selector: "h2" })).toBeInTheDocument();

    const confirmButtons = screen.getAllByRole("button", { name: "Approve" });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => {
      expect(api.approveLiveTrade).toHaveBeenCalledWith("ltp_test1");
    });
    await waitFor(() => {
      expect(api.getPendingLiveTrades).toHaveBeenCalledTimes(2);
    });
  });

  it("rejects a proposal through the confirm modal", async () => {
    vi.mocked(api.getPendingLiveTrades)
      .mockResolvedValueOnce({ proposals: [PROPOSAL] })
      .mockResolvedValueOnce({ proposals: [] });
    vi.mocked(api.rejectLiveTrade).mockResolvedValue({
      ...PROPOSAL,
      status: "rejected",
      approved_at: "2026-08-12T00:05:00Z",
      approved_by: "operator",
    });

    render(
      <MemoryRouter>
        <LiveTradeApprovals />
      </MemoryRouter>
    );

    expect(await screen.findByText("AAPL")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    expect(await screen.findByText("Reject AAPL BUY", { selector: "h2" })).toBeInTheDocument();

    const confirmButtons = screen.getAllByRole("button", { name: "Reject" });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => {
      expect(api.rejectLiveTrade).toHaveBeenCalledWith("ltp_test1");
    });
  });
});
