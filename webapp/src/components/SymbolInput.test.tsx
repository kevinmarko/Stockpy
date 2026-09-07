/**
 * SymbolInput.test.tsx — the symbol autocomplete combobox: it suggests tracked
 * symbols from GET /universe, supports keyboard + click selection, and still
 * submits arbitrary free-text tickers (so no valid lookup is ever blocked).
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SymbolInput } from "./SymbolInput";
import { __resetUniverseCache } from "./universeCache";
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
  // Default: no FMP matches -- individual tests override this to exercise
  // the "not yet tracked" section.
  vi.spyOn(api, "getSymbolSearch").mockResolvedValue({ query: "", results: [], reason: null });
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

  it("shows FMP results not in the tracked universe under a 'Not yet tracked' section", async () => {
    vi.spyOn(api, "getSymbolSearch").mockResolvedValue({
      query: "XO",
      results: [{ symbol: "XOM", name: "Exxon Mobil", currency: "USD", exchange: "NYSE", exchange_full_name: null }],
      reason: null,
    });
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} />);

    await user.type(screen.getByTestId("symbol-input"), "XO");
    const list = await screen.findByTestId("symbol-suggestions");
    expect(within(list).getByText("XOM")).toBeInTheDocument();
    expect(within(list).getByText("Not yet tracked")).toBeInTheDocument();

    await user.click(within(list).getByText("XOM"));
    expect(onSubmit).toHaveBeenCalledWith("XOM");
  });

  it("does not duplicate a symbol that is both tracked and returned by FMP search", async () => {
    vi.spyOn(api, "getSymbolSearch").mockResolvedValue({
      query: "AAPL",
      results: [{ symbol: "AAPL", name: "Apple", currency: "USD", exchange: "NASDAQ", exchange_full_name: null }],
      reason: null,
    });
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={vi.fn()} />);

    await user.type(screen.getByTestId("symbol-input"), "AAP");
    const list = await screen.findByTestId("symbol-suggestions");
    expect(within(list).getAllByText("AAPL")).toHaveLength(1);
    expect(within(list).queryByText("Not yet tracked")).not.toBeInTheDocument();
  });

  it("suppresses the FMP section entirely when enableFmpSuggestions is false", async () => {
    vi.spyOn(api, "getSymbolSearch").mockResolvedValue({
      query: "XO",
      results: [{ symbol: "XOM", name: "Exxon Mobil", currency: "USD", exchange: "NYSE", exchange_full_name: null }],
      reason: null,
    });
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={vi.fn()} enableFmpSuggestions={false} />);

    await user.type(screen.getByTestId("symbol-input"), "XO");
    // No tracked matches for "XO" and FMP is suppressed -- no dropdown at all.
    await new Promise((r) => setTimeout(r, 250)); // outlast the 200ms debounce
    expect(screen.queryByTestId("symbol-suggestions")).not.toBeInTheDocument();
    expect(api.getSymbolSearch).not.toHaveBeenCalled();
  });

  it("trackedSymbols overrides the shared universe cache entirely", async () => {
    const getUniverseSpy = vi.spyOn(api, "getUniverse");
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} trackedSymbols={["ZZZZ"]} />);

    await user.type(screen.getByTestId("symbol-input"), "ZZ");
    const list = await screen.findByTestId("symbol-suggestions");
    expect(within(list).getByText("ZZZZ")).toBeInTheDocument();
    // The shared GET /universe cache is never consulted -- this instance
    // has its own tracked list (mirrors Universe Manager's own fix).
    expect(getUniverseSpy).not.toHaveBeenCalled();
  });
});

describe("SymbolInput requireExactMatch (opt-in strict mode)", () => {
  it("disables Load and shows a warning for a ticker that matches no suggestion", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} requireExactMatch />);

    const input = screen.getByTestId("symbol-input");
    await user.type(input, "gbpdzd");

    const loadBtn = await screen.findByRole("button", { name: /load/i });
    expect(loadBtn).toBeDisabled();
    expect(screen.getByText(/Not a recognized ticker yet/i)).toBeInTheDocument();

    // Clicking a disabled button, and pressing Enter, are both no-ops.
    await user.click(loadBtn);
    await user.keyboard("{Enter}");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("enables Load once the typed value exactly matches a tracked symbol", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} requireExactMatch />);

    const input = screen.getByTestId("symbol-input");
    await user.type(input, "AAPL");

    const loadBtn = await screen.findByRole("button", { name: /load/i });
    await vi.waitFor(() => expect(loadBtn).not.toBeDisabled());
    expect(screen.queryByText(/Not a recognized ticker yet/i)).not.toBeInTheDocument();

    await user.click(loadBtn);
    expect(onSubmit).toHaveBeenCalledWith("AAPL");
  });

  it("enables Load once the typed value exactly matches a live FMP symbol-search result", async () => {
    vi.spyOn(api, "getSymbolSearch").mockImplementation((q) =>
      Promise.resolve(
        q.toUpperCase() === "XOM"
          ? { query: q, results: [{ symbol: "XOM", name: "Exxon Mobil", currency: "USD", exchange: "NYSE", exchange_full_name: null }], reason: null }
          : { query: q, results: [], reason: null }
      )
    );
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} requireExactMatch />);

    const input = screen.getByTestId("symbol-input");
    await user.type(input, "XOM");

    const loadBtn = await screen.findByRole("button", { name: /load/i });
    await vi.waitFor(() => expect(loadBtn).not.toBeDisabled());

    await user.click(loadBtn);
    expect(onSubmit).toHaveBeenCalledWith("XOM");
  });

  it("still allows clicking an actual suggestion row even before the exact-match debounce settles", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} requireExactMatch />);

    await user.type(screen.getByTestId("symbol-input"), "AAP");
    const list = await screen.findByTestId("symbol-suggestions");
    await user.click(within(list).getByText("AAPL"));

    // Selecting a real suggestion always works -- it's known-valid by construction.
    expect(onSubmit).toHaveBeenCalledWith("AAPL");
  });

  it("does not affect the default (requireExactMatch unset) behavior -- free text still submits", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} />);

    const input = screen.getByTestId("symbol-input");
    await user.type(input, "gbpdzd");
    await user.keyboard("{Enter}");

    expect(onSubmit).toHaveBeenCalledWith("GBPDZD");
  });
});

describe("SymbolInput autoSubmitOnExactMatch (opt-in, requires requireExactMatch)", () => {
  it("submits automatically once the typed value resolves to a known tracked symbol -- no click or Enter needed", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} requireExactMatch autoSubmitOnExactMatch />);

    const input = screen.getByTestId("symbol-input");
    await user.type(input, "AAPL");

    await vi.waitFor(() => expect(onSubmit).toHaveBeenCalledWith("AAPL"));
  });

  it("submits automatically once the typed value resolves to a live FMP symbol-search result", async () => {
    vi.spyOn(api, "getSymbolSearch").mockImplementation((q) =>
      Promise.resolve(
        q.toUpperCase() === "XOM"
          ? { query: q, results: [{ symbol: "XOM", name: "Exxon Mobil", currency: "USD", exchange: "NYSE", exchange_full_name: null }], reason: null }
          : { query: q, results: [], reason: null }
      )
    );
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} requireExactMatch autoSubmitOnExactMatch />);

    await user.type(screen.getByTestId("symbol-input"), "XOM");

    await vi.waitFor(() => expect(onSubmit).toHaveBeenCalledWith("XOM"));
  });

  it("never auto-submits an unrecognized ticker", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} requireExactMatch autoSubmitOnExactMatch />);

    await user.type(screen.getByTestId("symbol-input"), "gbpdzd");

    // Give the debounce + FMP lookup a beat to settle, then confirm it never fired.
    await new Promise((r) => setTimeout(r, 300));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("does not re-fire for the same resolved symbol on an unrelated re-render", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(<SymbolInput onSubmit={onSubmit} requireExactMatch autoSubmitOnExactMatch />);

    await user.type(screen.getByTestId("symbol-input"), "AAPL");
    await vi.waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));

    rerender(<SymbolInput onSubmit={onSubmit} requireExactMatch autoSubmitOnExactMatch hint="re-rendered" />);
    await new Promise((r) => setTimeout(r, 300));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("is a no-op without requireExactMatch -- free text is never auto-submitted mid-typing", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={onSubmit} autoSubmitOnExactMatch />);

    await user.type(screen.getByTestId("symbol-input"), "AAPL");
    await new Promise((r) => setTimeout(r, 300));
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
