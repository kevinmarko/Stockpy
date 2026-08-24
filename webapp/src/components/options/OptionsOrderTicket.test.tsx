import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { OptionsOrderTicket } from "./OptionsOrderTicket";
import { api } from "../../api/client";
import { OptionContract } from "../../api/types";
import { __resetUniverseCache } from "../universeCache";

vi.mock("../../api/client", () => ({
  api: {
    getPaperBrokerAccount: vi.fn(),
    postOptionsOrder: vi.fn(),
    watchCandidate: vi.fn(),
    triggerSymbolBackfill: vi.fn(),
    // universeCache.ts (imported by OptionsOrderTicket for the "not tracked
    // yet" fill-time prompt) calls api.getUniverse() through this same
    // mocked module -- both files resolve "../../api/client"/"../api/client"
    // to webapp/src/api/client.ts, so one vi.mock covers both importers.
    getUniverse: vi.fn(),
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

  it("surfaces a watchlist-add failure visibly instead of console-only", async () => {
    vi.mocked(api.watchCandidate).mockRejectedValue(
      new Error("watchlist_env_precedence: WATCHLIST env var is set.")
    );

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

    fireEvent.click(screen.getByText("+ Add to Watchlist"));

    expect(await screen.findByText(/Couldn't add AGNC to your watchlist/)).toBeInTheDocument();
    expect(screen.getByText(/watchlist_env_precedence/)).toBeInTheDocument();
    // The button stays offered -- no fabricated "✓ Added" confirmation.
    expect(screen.getByText("+ Add to Watchlist")).toBeInTheDocument();
  });

  it("triggers a spot-data backfill after a successful watchlist add", async () => {
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

    fireEvent.click(screen.getByText("+ Add to Watchlist"));

    await waitFor(() => expect(api.triggerSymbolBackfill).toHaveBeenCalledWith("AGNC"));
    expect(await screen.findByText(/Backfilled 504 bars of price history/)).toBeInTheDocument();
  });

  it("shows an inline 'not tracked yet' prompt after a fill on an untracked symbol", async () => {
    vi.mocked(api.postOptionsOrder).mockResolvedValue({
      ok: true,
      order_id: "order_123",
      message: "Order filled",
    });
    vi.mocked(api.getUniverse).mockResolvedValue({ symbols: [] }); // AGNC not tracked

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

    fireEvent.click(screen.getByRole("button", { name: /Paper Buy/i }));

    const prompt = await screen.findByTestId("not-tracked-prompt");
    expect(prompt).toHaveTextContent(/AGNC isn't in your tracked universe/);

    // "Not now" dismisses the prompt and hands control back to the caller
    // (never auto-fires the add flow) -- an explicit action, not a silent add.
    fireEvent.click(screen.getByText("Not now"));
    await waitFor(() => expect(onClear).toHaveBeenCalled());
    expect(api.watchCandidate).not.toHaveBeenCalled();
  });

  it("does not show the 'not tracked' prompt when the symbol is already tracked", async () => {
    vi.mocked(api.postOptionsOrder).mockResolvedValue({
      ok: true,
      order_id: "order_123",
      message: "Order filled",
    });
    vi.mocked(api.getUniverse).mockResolvedValue({
      symbols: [{ symbol: "AGNC", action: "BUY" }],
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

    fireEvent.click(screen.getByRole("button", { name: /Paper Buy/i }));

    await waitFor(() => expect(api.getUniverse).toHaveBeenCalled());
    expect(screen.queryByTestId("not-tracked-prompt")).not.toBeInTheDocument();
  });

  it("'Add' on the not-tracked prompt calls watchCandidate for the filled symbol", async () => {
    vi.mocked(api.postOptionsOrder).mockResolvedValue({
      ok: true,
      order_id: "order_123",
      message: "Order filled",
    });
    vi.mocked(api.getUniverse).mockResolvedValue({ symbols: [] });
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

    fireEvent.click(screen.getByRole("button", { name: /Paper Buy/i }));
    await screen.findByTestId("not-tracked-prompt");

    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(api.watchCandidate).toHaveBeenCalledWith("AGNC"));
    await waitFor(() => expect(onClear).toHaveBeenCalled());
  });
});
