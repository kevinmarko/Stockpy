import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { EquityOrderTicket } from "./EquityOrderTicket";
import { api } from "../api/client";
import { __resetUniverseCache } from "./universeCache";

vi.mock("../api/client", () => ({
  api: {
    getPaperBrokerAccount: vi.fn(),
    postPaperEquityOrder: vi.fn(),
    watchCandidate: vi.fn(),
    triggerSymbolBackfill: vi.fn(),
    // universeCache.ts (imported by EquityOrderTicket for the "not tracked
    // yet" fill-time prompt) calls api.getUniverse() through this same
    // mocked module.
    getUniverse: vi.fn(),
  },
}));

describe("EquityOrderTicket", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetUniverseCache();
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 100000,
      cash: 100000,
      buying_power: 100000,
    });
    // Default: nothing tracked -- individual tests override this to exercise
    // the "already tracked, no prompt" branch.
    vi.mocked(api.getUniverse).mockResolvedValue({ symbols: [] });
    vi.mocked(api.triggerSymbolBackfill).mockResolvedValue({
      symbol: "AGNC",
      rows_persisted: 504,
      last_bar_date: "2026-08-21",
      status: "ok",
    });
  });

  it("renders with dollar-amount sizing and a commission preview", async () => {
    const onClear = vi.fn();
    render(<EquityOrderTicket symbol="AGNC" spotPrice={10.0} onClear={onClear} />);

    expect(screen.getByText(/Buy AGNC Stock/i)).toBeInTheDocument();
    expect(screen.getByText("By Dollar ($)")).toBeInTheDocument();
    expect(screen.getByText("By Shares")).toBeInTheDocument();

    // $500 default / $10.00 spot = 50 shares; commission = max(1, 50*0.005) = 1.00;
    // total = 500 + 1.00 = 501.00.
    expect(screen.getAllByText(/50 shares/i).length).toBeGreaterThanOrEqual(1);
    expect(await screen.findByText("$100,000.00")).toBeInTheDocument();
  });

  it("switches to By Shares sizing and updates the stepper", async () => {
    const onClear = vi.fn();
    render(<EquityOrderTicket symbol="AGNC" spotPrice={10.0} onClear={onClear} />);

    fireEvent.click(screen.getByText("By Shares"));
    expect(screen.getByText(/Unit: 1 Share/i)).toBeInTheDocument();

    fireEvent.click(screen.getByText("+"));
    // The submit button label reflects the updated share count.
    expect(screen.getByRole("button", { name: /Paper Buy .* \(2 shares\)/i })).toBeInTheDocument();
  });

  it("submits a paper BUY order sized by dollars with the right payload", async () => {
    vi.mocked(api.postPaperEquityOrder).mockResolvedValue({
      ok: true,
      order_id: "eq_ord_123",
      message: "Paper stock order filled: BUY 50.00 shares of AGNC at $10.00 (Total: $501.00).",
    });

    const onClear = vi.fn();
    render(<EquityOrderTicket symbol="AGNC" spotPrice={10.0} onClear={onClear} />);

    const submitBtn = screen.getByRole("button", { name: /Paper Buy/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(api.postPaperEquityOrder).toHaveBeenCalledWith({
        symbol: "AGNC",
        side: "buy",
        quantity: undefined,
        dollar_amount: 500,
        order_type: "market",
        limit_price: undefined,
        isLive: false,
      });
    });
  });

  it("submits a paper LIMIT SELL order sized by shares with the right payload", async () => {
    vi.mocked(api.postPaperEquityOrder).mockResolvedValue({
      ok: true,
      order_id: "eq_ord_124",
      message: "Paper stock order filled: SELL 3.00 shares of AGNC at $12.50 (Total: $36.35).",
    });

    const onClear = vi.fn();
    render(<EquityOrderTicket symbol="AGNC" spotPrice={10.0} onClear={onClear} />);

    // Sell side
    fireEvent.click(screen.getByText("Sell AGNC"));
    // By Shares sizing
    fireEvent.click(screen.getByText("By Shares"));
    fireEvent.click(screen.getByText("+"));
    fireEvent.click(screen.getByText("+")); // quantity: 1 -> 2 -> 3
    // Limit order
    fireEvent.click(screen.getByText("Limit"));
    const limitInput = screen.getByDisplayValue("10");
    fireEvent.change(limitInput, { target: { value: "12.5" } });

    const submitBtn = screen.getByRole("button", { name: /Paper Sell/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(api.postPaperEquityOrder).toHaveBeenCalledWith({
        symbol: "AGNC",
        side: "sell",
        quantity: 3,
        dollar_amount: undefined,
        order_type: "limit",
        limit_price: 12.5,
        isLive: false,
      });
    });
  });

  it("shows the server's failure message and does not fabricate a fill", async () => {
    vi.mocked(api.postPaperEquityOrder).mockResolvedValue({
      ok: false,
      order_id: null,
      message: "Order rejected: Insufficient funds or inventory for SELL 3 AGNC.",
    });

    const onClear = vi.fn();
    render(<EquityOrderTicket symbol="AGNC" spotPrice={10.0} onClear={onClear} />);

    fireEvent.click(screen.getByRole("button", { name: /Paper Buy/i }));

    expect(await screen.findByText(/Order failed:/i)).toBeInTheDocument();
    expect(screen.getByText(/Insufficient funds or inventory for SELL 3 AGNC/i)).toBeInTheDocument();
    expect(screen.queryByText(/Order Executed/i)).not.toBeInTheDocument();
  });

  it("warns about insufficient paper cash before allowing submission", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 100,
      cash: 100,
      buying_power: 100,
    });

    const onClear = vi.fn();
    render(<EquityOrderTicket symbol="AGNC" spotPrice={10.0} onClear={onClear} />);

    // Default $500 order vs. $100 cash -> insufficient.
    expect(await screen.findByText(/Insufficient paper cash balance/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Paper Buy/i })).toBeDisabled();
  });

  it("does not block a SELL on low cash, and shows proceeds net of commission", async () => {
    vi.mocked(api.getPaperBrokerAccount).mockResolvedValue({
      equity: 100,
      cash: 5,
      buying_power: 5,
    });
    render(<EquityOrderTicket symbol="AGNC" spotPrice={10.0} onClear={vi.fn()} />);
    fireEvent.click(screen.getByText("Sell AGNC"));
    await screen.findByText("$5.00");

    // $500 / $10 = 50 shares; proceeds = 500 - max(1, 0.25) = 499.
    expect(screen.getByText("Estimated Proceeds:")).toBeInTheDocument();
    expect(screen.getAllByText("$499.00").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/Insufficient paper cash balance/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Paper Sell/i })).not.toBeDisabled();
  });

  it("allows a fractional share quantity so a dollar-bought position can be sold outright", async () => {
    vi.mocked(api.postPaperEquityOrder).mockResolvedValue({
      ok: true,
      order_id: "eq_ord_125",
      message: "Paper stock order filled: SELL 2.27 shares of AAPL at $220.32 (Total: $499.13).",
    });
    render(<EquityOrderTicket symbol="AAPL" spotPrice={220.32} onClear={vi.fn()} />);
    fireEvent.click(screen.getByText("Sell AAPL"));
    fireEvent.click(screen.getByText("By Shares"));
    fireEvent.change(screen.getByDisplayValue("1"), { target: { value: "2.2694" } });
    fireEvent.click(screen.getByRole("button", { name: /Paper Sell .*\(2\.2694 shares\)/i }));

    await waitFor(() => {
      expect(api.postPaperEquityOrder).toHaveBeenCalledWith(
        expect.objectContaining({ symbol: "AAPL", side: "sell", quantity: 2.2694, dollar_amount: undefined }),
      );
    });
  });

  it("shows an inline 'not tracked yet' prompt after a fill on an untracked symbol, and Add wires to watchCandidate + triggerSymbolBackfill", async () => {
    vi.mocked(api.postPaperEquityOrder).mockResolvedValue({
      ok: true,
      order_id: "eq_ord_125",
      message: "Paper stock order filled: BUY 50.00 shares of AGNC at $10.00 (Total: $501.00).",
    });
    vi.mocked(api.getUniverse).mockResolvedValue({ symbols: [] }); // AGNC not tracked
    vi.mocked(api.watchCandidate).mockResolvedValue({
      symbol: "AGNC",
      added: ["AGNC"],
      already_present: [],
      watchlist_file: "watchlist.txt",
      applies: "next_pipeline_run",
      note: "Added to watchlist",
    });

    const onClear = vi.fn();
    render(<EquityOrderTicket symbol="AGNC" spotPrice={10.0} onClear={onClear} />);

    fireEvent.click(screen.getByRole("button", { name: /Paper Buy/i }));

    const prompt = await screen.findByTestId("not-tracked-prompt");
    expect(prompt).toHaveTextContent(/AGNC isn't in your tracked universe/);

    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(api.watchCandidate).toHaveBeenCalledWith("AGNC"));
    await waitFor(() => expect(api.triggerSymbolBackfill).toHaveBeenCalledWith("AGNC"));
    expect(await screen.findByText(/Backfilled 504 bars of price history/)).toBeInTheDocument();
    await waitFor(() => expect(onClear).toHaveBeenCalled());
  });

  it("does not show the 'not tracked' prompt when the symbol is already tracked", async () => {
    vi.mocked(api.postPaperEquityOrder).mockResolvedValue({
      ok: true,
      order_id: "eq_ord_126",
      message: "Paper stock order filled: BUY 50.00 shares of AGNC at $10.00 (Total: $501.00).",
    });
    vi.mocked(api.getUniverse).mockResolvedValue({
      symbols: [{ symbol: "AGNC", action: "BUY" }],
    });

    const onClear = vi.fn();
    render(<EquityOrderTicket symbol="AGNC" spotPrice={10.0} onClear={onClear} />);

    fireEvent.click(screen.getByRole("button", { name: /Paper Buy/i }));

    await waitFor(() => expect(api.getUniverse).toHaveBeenCalled());
    expect(screen.queryByTestId("not-tracked-prompt")).not.toBeInTheDocument();
  });
});
