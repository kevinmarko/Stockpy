/**
 * ActiveTraderLadder.test.tsx — the interactive order-preparation panel
 * bolted onto the ladder: clicking a ladder price stages a limit order,
 * Buy/Sell compose a paste-in-Claude-Code agent command via
 * CopyCommandBlock, and Reset/symbol-change clear staged state. Covers the
 * input-validation gate (no command generated from an empty/zero quantity
 * or a missing limit price) and, most importantly, that the generated
 * command never claims to invoke the queue-only `robinhood-execution`
 * skill (see buildOrderCommand's docstring in the component) — pasting a
 * "run robinhood-execution" command for an order that was never written
 * into output/execution_queue.json would send a future agent session down
 * a workflow that structurally cannot fulfill it.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ActiveTraderLadder from "./ActiveTraderLadder";
import { api } from "../api/client";
import type { OrderBookLadderResponse } from "../api/types";

const originalClipboard = navigator.clipboard;

function mockClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    writable: true,
    configurable: true,
  });
  return writeText;
}

// A single bid/ask level each -- keeps `[...asks].reverse().map((_, i) => ...)`
// index 0 unambiguous (with >1 level, reversing the display order means
// index 0 is the FARTHEST ask, not the best one) so testids stay simple to
// reason about here.
function ladderFixture(symbol: string, currentPrice: number): OrderBookLadderResponse {
  return {
    symbol,
    current_price: currentPrice,
    bids: [{ price: currentPrice - 0.05, size: 1200, type: "bid" }],
    asks: [{ price: currentPrice + 0.05, size: 900, type: "ask" }],
    is_synthetic: true,
  };
}

async function renderLadder(symbol = "SPY", currentPrice = 450) {
  vi.spyOn(api, "getOrderBookLadder").mockResolvedValue(ladderFixture(symbol, currentPrice));
  const view = render(<ActiveTraderLadder symbol={symbol} />);
  await screen.findByText(`$${currentPrice.toFixed(2)}`);
  return view;
}

describe("ActiveTraderLadder — trade panel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(navigator, "clipboard", {
      value: originalClipboard,
      writable: true,
      configurable: true,
    });
  });

  it("clicking a ladder ask price switches to LIMIT and populates the limit price field", async () => {
    await renderLadder();
    fireEvent.click(screen.getByTestId("ladder-ask-price-0"));

    expect(screen.getByLabelText("Order Type")).toHaveValue("LIMIT");
    expect(screen.getByLabelText("Limit Price")).toHaveValue(450.05);
  });

  it("clicking a ladder bid price switches to LIMIT and populates the limit price field", async () => {
    await renderLadder();
    fireEvent.click(screen.getByTestId("ladder-bid-price-0"));

    expect(screen.getByLabelText("Order Type")).toHaveValue("LIMIT");
    expect(screen.getByLabelText("Limit Price")).toHaveValue(449.95);
  });

  it("Buy with the MARKET default composes a market-order command, never claiming to run the queue-only skill", async () => {
    await renderLadder();
    fireEvent.click(screen.getByTestId("ladder-buy-button"));

    const composed = await screen.findByTestId("ladder-order-command-composed");
    expect(composed).toHaveTextContent("buy market order for 100 shares of SPY");
    expect(composed.textContent).not.toMatch(/run robinhood-execution/i);
    expect(composed.textContent).toMatch(/robinhood-trading MCP/);
    expect(composed.textContent).toMatch(/Agentic account/);
  });

  it("Sell with a ladder-picked limit price composes a limit-order command at that price", async () => {
    await renderLadder();
    fireEvent.click(screen.getByTestId("ladder-ask-price-0")); // stages LIMIT @ 450.05
    fireEvent.click(screen.getByTestId("ladder-sell-button"));

    const composed = await screen.findByTestId("ladder-order-command-composed");
    expect(composed).toHaveTextContent("sell limit order at $450.05 for 100 shares of SPY");
  });

  it("copying the generated command writes the exact composed text to the clipboard", async () => {
    const writeText = mockClipboard();
    await renderLadder();
    fireEvent.click(screen.getByTestId("ladder-buy-button"));

    const copyBtn = await screen.findByTestId("ladder-order-command-copy");
    fireEvent.click(copyBtn);
    expect(writeText).toHaveBeenCalledWith(
      expect.stringContaining("buy market order for 100 shares of SPY")
    );
  });

  it("a zero/empty quantity disables Buy and Sell instead of generating a broken command", async () => {
    const user = userEvent.setup();
    await renderLadder();

    const qtyInput = screen.getByLabelText("Quantity");
    await user.clear(qtyInput);
    await user.type(qtyInput, "0");

    expect(screen.getByTestId("ladder-buy-button")).toBeDisabled();
    expect(screen.getByTestId("ladder-sell-button")).toBeDisabled();
  });

  it("switching to LIMIT with no price staged blocks the command and shows a validation message, not an empty CopyCommandBlock", async () => {
    await renderLadder();

    fireEvent.change(screen.getByLabelText("Order Type"), { target: { value: "LIMIT" } });
    fireEvent.click(screen.getByTestId("ladder-buy-button"));

    expect(screen.queryByTestId("ladder-order-command-composed")).not.toBeInTheDocument();
    expect(screen.getByText(/Enter a valid limit price/)).toBeInTheDocument();
  });

  it("invalidating the quantity after Buy is already staged blames the quantity, not the limit price", async () => {
    await renderLadder();
    fireEvent.click(screen.getByTestId("ladder-buy-button"));
    await screen.findByTestId("ladder-order-command-composed");

    // The Buy/Sell buttons disable on an invalid quantity, but a
    // *previously* staged tradeAction survives (Reset is the only way to
    // clear it) -- the validation message must track which field is
    // actually broken, not always default to blaming the limit price.
    fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "0" } });

    expect(screen.queryByTestId("ladder-order-command-composed")).not.toBeInTheDocument();
    expect(screen.getByText(/Enter a valid quantity/)).toBeInTheDocument();
    expect(screen.queryByText(/Enter a valid limit price/)).not.toBeInTheDocument();
  });

  it("Reset clears the staged action, order type, limit price, and quantity", async () => {
    await renderLadder();

    fireEvent.click(screen.getByTestId("ladder-ask-price-0"));
    fireEvent.click(screen.getByTestId("ladder-sell-button"));
    expect(await screen.findByTestId("ladder-order-command-composed")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("ladder-reset-button"));

    expect(screen.queryByTestId("ladder-order-command-composed")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Order Type")).toHaveValue("MARKET");
    expect(screen.getByLabelText("Quantity")).toHaveValue(100);
  });

  it("switching symbols drops the staged action/type/limit price but keeps the quantity", async () => {
    const { rerender } = await renderLadder("SPY", 450);

    fireEvent.click(screen.getByTestId("ladder-ask-price-0")); // LIMIT @ 450.05
    fireEvent.click(screen.getByTestId("ladder-buy-button"));
    await screen.findByTestId("ladder-order-command-composed");

    fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "25" } });

    vi.spyOn(api, "getOrderBookLadder").mockResolvedValue(ladderFixture("AAPL", 150));
    rerender(<ActiveTraderLadder symbol="AAPL" />);
    await waitFor(() => expect(screen.getByLabelText("Order Type")).toHaveValue("MARKET"));

    expect(screen.queryByTestId("ladder-order-command-composed")).not.toBeInTheDocument();
    // MARKET is the reset order type, so the limit price field is hidden again.
    expect(screen.queryByLabelText("Limit Price")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Quantity")).toHaveValue(25);
  });
});
