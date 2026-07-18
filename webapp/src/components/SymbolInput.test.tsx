/**
 * SymbolInput.test.tsx — the symbol autocomplete combobox: it suggests tracked
 * symbols from GET /universe, supports keyboard + click selection, and still
 * submits arbitrary free-text tickers (so no valid lookup is ever blocked).
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SymbolInput, __resetUniverseCache } from "./SymbolInput";
import { api } from "../api/client";

const UNIVERSE = {
  symbols: [
    { symbol: "AAPL", action: "BUY" },
    { symbol: "AMD", action: null },
    { symbol: "MSFT", action: "HOLD" },
  ],
};

beforeEach(() => {
  __resetUniverseCache();
  vi.spyOn(api, "getUniverse").mockResolvedValue(UNIVERSE);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SymbolInput autocomplete", () => {
  it("suggests tracked symbols matching the typed prefix", async () => {
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={vi.fn()} />);

    await user.type(screen.getByTestId("symbol-input"), "A");
    const list = await screen.findByTestId("symbol-suggestions");
    // Both A-prefixed symbols appear; MSFT does not.
    expect(within(list).getByText("AAPL")).toBeInTheDocument();
    expect(within(list).getByText("AMD")).toBeInTheDocument();
    expect(within(list).queryByText("MSFT")).not.toBeInTheDocument();
  });

  it("selects a highlighted suggestion with the keyboard and submits it", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} />);

    const input = screen.getByTestId("symbol-input");
    await user.type(input, "AM");
    await screen.findByTestId("symbol-suggestions");
    await user.keyboard("{ArrowDown}{Enter}");

    expect(onSubmit).toHaveBeenCalledWith("AMD");
  });

  it("clears the highlighted suggestion on blur, so refocusing and pressing Enter submits the typed text (not a stale highlight)", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(
      <div>
        <SymbolInput onSubmit={onSubmit} />
        <button>elsewhere</button>
      </div>
    );

    const input = screen.getByTestId("symbol-input");
    await user.type(input, "AM");
    await screen.findByTestId("symbol-suggestions");
    await user.keyboard("{ArrowDown}"); // highlight AMD, but don't accept it

    await user.click(screen.getByText("elsewhere")); // blur
    await user.click(input); // refocus without retyping
    await user.keyboard("{Enter}");

    // Must submit the typed "AM" (uppercased), not silently commit the
    // previously-highlighted "AMD" suggestion.
    expect(onSubmit).toHaveBeenCalledWith("AM");
  });

  it("selects a suggestion on click", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} />);

    await user.type(screen.getByTestId("symbol-input"), "AAP");
    const list = await screen.findByTestId("symbol-suggestions");
    await user.click(within(list).getByText("AAPL"));

    expect(onSubmit).toHaveBeenCalledWith("AAPL");
  });

  it("still submits a free-text ticker that is not in the universe", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} />);

    const input = screen.getByTestId("symbol-input");
    await user.type(input, "tsla");
    // No dropdown for an unknown ticker; Enter falls through to a plain submit.
    expect(screen.queryByTestId("symbol-suggestions")).not.toBeInTheDocument();
    await user.keyboard("{Enter}");

    expect(onSubmit).toHaveBeenCalledWith("TSLA");
  });

  it("degrades to a plain field (still submittable) on a genuinely empty universe", async () => {
    // Cold-start backend state (GET /universe → {symbols: []}), distinct from a
    // fetch failure — the combobox must degrade the same honest way either way.
    vi.spyOn(api, "getUniverse").mockResolvedValue({ symbols: [] });
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} />);

    const input = screen.getByTestId("symbol-input");
    await user.type(input, "aapl");
    expect(screen.queryByTestId("symbol-suggestions")).not.toBeInTheDocument();
    await user.keyboard("{Enter}");

    expect(onSubmit).toHaveBeenCalledWith("AAPL");
  });

  it("degrades to a plain field (still submittable) when the universe fetch fails", async () => {
    vi.spyOn(api, "getUniverse").mockRejectedValue(new Error("offline"));
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} />);

    const input = screen.getByTestId("symbol-input");
    await user.type(input, "aapl");
    expect(screen.queryByTestId("symbol-suggestions")).not.toBeInTheDocument();
    await user.keyboard("{Enter}");

    expect(onSubmit).toHaveBeenCalledWith("AAPL");
  });
});
