/**
 * MarketDataHealth.test.tsx — the connection-health badge + per-symbol
 * latency table (client-side analog of the legacy Streamlit "Market Data
 * Provider" tab). Covers the loading/empty/error branches, the honest
 * "unreachable" row for a symbol omitted from the response (never rendered
 * as OK), the "stale" row, and the truncation notice for an oversized
 * universe.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MarketDataHealth } from "./MarketDataHealth";
import { api } from "../api/client";
import type { QuotesResponse, UniverseResponse } from "../api/types";

function universeOf(symbols: string[]): UniverseResponse {
  return { symbols: symbols.map((symbol) => ({ symbol, action: null })) };
}

describe("MarketDataHealth (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("exercises the REAL mock quote fixture end-to-end: OK, Stale, and Unreachable rows, never all-green", async () => {
    // Only the universe is overridden (kept small so the sequential,
    // staggered check finishes quickly) -- getDataQuotes itself is the real
    // api/mock.ts fixture, not stubbed, so this proves the fixture's own
    // deterministic rules: NVDA (even leading char) resolves real-time,
    // MSFT (odd leading char) resolves delayed/stale, and V (the fixed
    // honesty fixture) is always omitted -- a real, always-present
    // PORTFOLIO-position symbol, never a contrived one.
    vi.spyOn(api, "getUniverse").mockResolvedValueOnce(universeOf(["NVDA", "MSFT", "V"]));

    render(<MarketDataHealth />);
    fireEvent.click(await screen.findByRole("button", { name: "Check connection" }));

    const nvdaRow = await screen.findByTestId("md-row-NVDA", {}, { timeout: 5000 });
    expect(nvdaRow.textContent).toContain("OK");

    const msftRow = await screen.findByTestId("md-row-MSFT", {}, { timeout: 5000 });
    expect(msftRow.textContent).toContain("Stale");

    const vRow = await screen.findByTestId("md-row-V", {}, { timeout: 5000 });
    expect(vRow.textContent).toContain("Unreachable");
  }, 10000);

  it("renders the honest empty state when no symbols are tracked", async () => {
    vi.spyOn(api, "getUniverse").mockResolvedValueOnce(universeOf([]));
    render(<MarketDataHealth />);
    expect(await screen.findByText("No tracked symbols yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check connection" })).not.toBeInTheDocument();
  });

  it("renders the honest error state on a failed universe fetch, with a working retry", async () => {
    vi.spyOn(api, "getUniverse").mockRejectedValueOnce(new Error("offline"));
    render(<MarketDataHealth />);
    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
  });

  it("a reachable symbol renders OK with a colored latency, not the unreachable/stale buckets", async () => {
    vi.spyOn(api, "getUniverse").mockResolvedValueOnce(universeOf(["AAPL"]));
    vi.spyOn(api, "getDataQuotes").mockResolvedValueOnce({
      AAPL: {
        symbol: "AAPL",
        price: 214.9,
        bid: 214.85,
        ask: 214.95,
        timestamp: new Date().toISOString(),
        is_stale: false,
        source: "alpaca",
      },
    } satisfies QuotesResponse);

    render(<MarketDataHealth />);
    fireEvent.click(await screen.findByRole("button", { name: "Check connection" }));

    const row = await screen.findByTestId("md-row-AAPL");
    expect(row.textContent).toContain("OK");
    expect(row.textContent).toMatch(/\d+ ms/);
    expect(row.textContent).toContain("alpaca");

    // Rolling connection-health badge reflects the single successful check.
    await waitFor(() =>
      expect(screen.getByTestId("md-health-badge").textContent).toContain("Healthy (1/1 ok)")
    );
  });

  it("a symbol omitted from the response renders Unreachable, never fabricated as OK", async () => {
    vi.spyOn(api, "getUniverse").mockResolvedValueOnce(universeOf(["ZZZZ"]));
    vi.spyOn(api, "getDataQuotes").mockResolvedValueOnce({} as QuotesResponse);

    render(<MarketDataHealth />);
    fireEvent.click(await screen.findByRole("button", { name: "Check connection" }));

    const row = await screen.findByTestId("md-row-ZZZZ");
    expect(row.textContent).toContain("Unreachable");
    expect(row.textContent).not.toContain("OK");

    await waitFor(() =>
      expect(screen.getByTestId("md-health-badge").textContent).toContain("Down (0/1 ok)")
    );
  });

  it("caps the check at MAX_CHECK_SYMBOLS and shows an honest truncation notice for an oversized universe", async () => {
    const big = Array.from({ length: 40 }, (_, i) => `SYM${i}`);
    vi.spyOn(api, "getUniverse").mockResolvedValueOnce(universeOf(big));
    render(<MarketDataHealth />);
    expect(
      await screen.findByText("Showing the first 25 of 40 tracked symbols.")
    ).toBeInTheDocument();
  });
});
