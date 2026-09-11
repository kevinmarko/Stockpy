/**
 * SymbolInput.test.tsx — the symbol autocomplete combobox: it suggests tracked
 * symbols from GET /universe, supports keyboard + click selection, and still
 * submits arbitrary free-text tickers (so no valid lookup is ever blocked).
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import { SymbolInput } from "./SymbolInput";
import { __resetUniverseCache } from "./universeCache";
import { api } from "../api/client";
import { SearchDefaultProvider } from "../context/SearchDefaultContext";

/** Renders in explicit tracked-first (rollback) mode, bypassing the
 * component's own new universe-first default -- for tests exercising the
 * pre-2026-09 ordering specifically. Plain `render(<SymbolInput .../>)`
 * elsewhere in this file gets the real default (universeFirst=true) via
 * `useSearchDefault()`'s safe-fallback-outside-provider value. */
function renderTrackedFirst(ui: ReactElement) {
  return render(
    <SearchDefaultProvider initialUniverseFirst={false}>{ui}</SearchDefaultProvider>
  );
}

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

  it("in universe-first mode (the real default), shows FMP results unlabeled with no tracked matches at all -- no 'Not yet tracked' header when untracked is the only section", async () => {
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
    // Untracked is the PRIMARY (unlabeled) section in universe-first mode --
    // with zero tracked matches for "XO" there is no secondary section to
    // label at all, so neither header appears.
    expect(within(list).queryByText("Not yet tracked")).not.toBeInTheDocument();
    expect(within(list).queryByText("Saved")).not.toBeInTheDocument();

    await user.click(within(list).getByText("XOM"));
    expect(onSubmit).toHaveBeenCalledWith("XOM");
  });

  it("in legacy tracked-first (rollback) mode, shows the same FMP results under a 'Not yet tracked' section -- reproduces the pre-2026-09 default exactly", async () => {
    vi.spyOn(api, "getSymbolSearch").mockResolvedValue({
      query: "XO",
      results: [{ symbol: "XOM", name: "Exxon Mobil", currency: "USD", exchange: "NYSE", exchange_full_name: null }],
      reason: null,
    });
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    renderTrackedFirst(<SymbolInput onSubmit={onSubmit} />);

    await user.type(screen.getByTestId("symbol-input"), "XO");
    const list = await screen.findByTestId("symbol-suggestions");
    expect(within(list).getByText("XOM")).toBeInTheDocument();
    expect(within(list).getByText("Not yet tracked")).toBeInTheDocument();

    await user.click(within(list).getByText("XOM"));
    expect(onSubmit).toHaveBeenCalledWith("XOM");
  });

  it("in universe-first mode, a MIXED list of tracked and untracked matches puts untracked first (unlabeled) and tracked second under 'Saved'", async () => {
    vi.spyOn(api, "getSymbolSearch").mockResolvedValue({
      query: "A",
      results: [{ symbol: "AA", name: "Alcoa", currency: "USD", exchange: "NYSE", exchange_full_name: null }],
      reason: null,
    });
    const user = userEvent.setup();
    render(<SymbolInput onSubmit={vi.fn()} />);

    await user.type(screen.getByTestId("symbol-input"), "A");
    const list = await screen.findByTestId("symbol-suggestions");
    // Tracked matches (AAPL/AMD) can render before the debounced FMP fetch
    // for "AA" resolves -- wait for "AA" itself so this doesn't race a slow
    // fetch and read a stale, tracked-only intermediate DOM state.
    await within(list).findByText("AA");
    const options = within(list).getAllByRole("option").map((el) => el.textContent);
    // AA (untracked) leads; AAPL/AMD (tracked) follow -- both start with "A".
    expect(options[0]).toContain("AA");
    expect(options.slice(1).join(",")).toContain("AAPL");
    expect(within(list).getByText("Saved")).toBeInTheDocument();
    expect(within(list).queryByText("Not yet tracked")).not.toBeInTheDocument();

    // The "Saved" header renders between the untracked and tracked rows,
    // not before the very first (untracked) row.
    const rows = within(list).getAllByRole("option");
    expect(rows[0]).toHaveTextContent("AA");
  });

  it("the rollback toggle genuinely reverses the order -- same mocks, opposite mode, opposite result", async () => {
    vi.spyOn(api, "getSymbolSearch").mockResolvedValue({
      query: "A",
      results: [{ symbol: "AA", name: "Alcoa", currency: "USD", exchange: "NYSE", exchange_full_name: null }],
      reason: null,
    });
    const user1 = userEvent.setup();
    const { unmount } = render(<SymbolInput onSubmit={vi.fn()} testId="si-a" />);
    await user1.type(screen.getByTestId("si-a"), "A");
    const listA = await screen.findByTestId("symbol-suggestions");
    // Tracked matches (AAPL/AMD) can render before the debounced FMP fetch
    // for "AA" resolves -- wait for "AA" itself, not just any dropdown, so
    // this doesn't race a slow fetch under heavy full-suite load and read a
    // stale, tracked-only intermediate DOM state.
    await within(listA).findByText("AA");
    const firstRowUniverseFirst = within(listA).getAllByRole("option")[0].textContent;
    unmount();

    const user2 = userEvent.setup();
    renderTrackedFirst(<SymbolInput onSubmit={vi.fn()} testId="si-b" />);
    await user2.type(screen.getByTestId("si-b"), "A");
    const listB = await screen.findByTestId("symbol-suggestions");
    await within(listB).findByText("AA");
    const firstRowTrackedFirst = within(listB).getAllByRole("option")[0].textContent;

    // Universe-first: the untracked Alcoa ("AA") result leads.
    expect(firstRowUniverseFirst).toBe("AA");
    // Tracked-first (rollback): a tracked match leads instead -- never the
    // untracked "AA" result.
    expect(firstRowTrackedFirst).not.toBe("AA");
    expect(["AAPLBUY", "AMD"]).toContain(firstRowTrackedFirst);
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

  it("never renders a section header when enableFmpSuggestions is false, even with real tracked matches (Sector Selection's exact case)", async () => {
    // Regression test: a live browser check found that with the
    // universe-first default active, an enableFmpSuggestions={false} call
    // site (Sector Selection is the one production example) spuriously
    // rendered a "Saved" header over its entirely-tracked suggestion list --
    // pre-2026-09 this call site's suggestions were never labeled at all,
    // since its `!s.tracked` header condition could never be true when no
    // untracked section can ever exist. The previous test above only
    // covered the ZERO-tracked-matches case (no dropdown renders at all),
    // which never exercised the buggy header logic.
    const user = userEvent.setup();
    render(
      <SymbolInput
        onSubmit={vi.fn()}
        enableFmpSuggestions={false}
        trackedSymbols={["XOM", "COST"]}
      />
    );

    await user.type(screen.getByTestId("symbol-input"), "O");
    const list = await screen.findByTestId("symbol-suggestions");
    expect(within(list).getByText("XOM")).toBeInTheDocument();
    expect(within(list).getByText("COST")).toBeInTheDocument();
    expect(within(list).queryByText("Saved")).not.toBeInTheDocument();
    expect(within(list).queryByText("Not yet tracked")).not.toBeInTheDocument();
    // FMP is never even consulted at this call site, in either mode.
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
