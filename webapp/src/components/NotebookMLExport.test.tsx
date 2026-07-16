import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NotebookMLExport } from "./NotebookMLExport";
import { api } from "../api/client";
import type { Portfolio } from "../api/types";

const originalCreateObjectURL = globalThis.URL.createObjectURL;
const originalRevokeObjectURL = globalThis.URL.revokeObjectURL;
const originalClipboard = navigator.clipboard;

// A valid, fully-resolved portfolio so a test can render the widget in its
// "ready" state synchronously (no fetch, buttons enabled immediately).
function makePortfolio(overrides: Partial<Portfolio> = {}): Portfolio {
  return {
    total_equity: 5000,
    buying_power: 1000,
    total_unrealized_pl: 0,
    total_dividends: 0,
    position_count: 0,
    source: "mock",
    fetched_at: new Date().toISOString(),
    positions: [],
    ...overrides,
  };
}

describe("NotebookMLExport component (R4)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    // Reset clipboard
    Object.defineProperty(navigator, "clipboard", {
      value: originalClipboard,
      writable: true,
      configurable: true,
    });
    // Reset URL properties in-place
    Object.defineProperty(globalThis.URL, "createObjectURL", {
      value: originalCreateObjectURL,
      writable: true,
      configurable: true,
    });
    Object.defineProperty(globalThis.URL, "revokeObjectURL", {
      value: originalRevokeObjectURL,
      writable: true,
      configurable: true,
    });
  });

  // T1.1: Payload Preview Structure
  it("renders export preview containing valid structured JSON payload", async () => {
    render(<NotebookMLExport portfolio={makePortfolio()} />);
    const preview = await screen.findByTestId("export-preview");
    expect(preview).toBeInTheDocument();

    const parsed = JSON.parse(preview.textContent || "");
    expect(parsed).toHaveProperty("timestamp");
    expect(parsed).toHaveProperty("portfolio");
    expect(parsed.portfolio).toHaveProperty("total_equity");
    expect(parsed).toHaveProperty("followed_pilots");
  });

  // T1.2: Clipboard Write Action
  it("copies payload JSON to clipboard and displays success label", async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: writeTextMock },
      writable: true,
      configurable: true,
    });

    render(<NotebookMLExport portfolio={makePortfolio()} />);
    const copyBtn = await screen.findByTestId("copy-export-btn");
    expect(copyBtn).toBeEnabled();
    fireEvent.click(copyBtn);

    expect(writeTextMock).toHaveBeenCalled();
    expect(await screen.findByText("Copied! ✓")).toBeInTheDocument();
  });

  // T1.3: Download Triggering
  it("creates blob download link and clicks it when download is triggered", async () => {
    const createObjectURLMock = vi.fn().mockReturnValue("blob:mock-url");
    const revokeObjectURLMock = vi.fn();
    Object.defineProperty(globalThis.URL, "createObjectURL", { value: createObjectURLMock, writable: true });
    Object.defineProperty(globalThis.URL, "revokeObjectURL", { value: revokeObjectURLMock, writable: true });

    render(<NotebookMLExport portfolio={makePortfolio()} />);

    const dummyAnchor = document.createElement("a");
    const clickSpy = vi.spyOn(dummyAnchor, "click");
    vi.spyOn(document, "createElement").mockImplementation((tagName) => {
      if (tagName === "a") return dummyAnchor;
      return document.createElement(tagName);
    });

    const downloadBtn = await screen.findByTestId("download-export-btn");
    expect(downloadBtn).toBeEnabled();
    fireEvent.click(downloadBtn);

    expect(createObjectURLMock).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalled();
  });

  // T1.4: Export Copy Confirmation State (revert after 2s — scoped fake timers)
  it("reverts the copy success button state after 2 seconds", async () => {
    vi.useFakeTimers();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      writable: true,
      configurable: true,
    });

    render(<NotebookMLExport portfolio={makePortfolio()} />);
    const copyBtn = screen.getByTestId("copy-export-btn"); // prop-driven → ready sync
    fireEvent.click(copyBtn);
    // Flush the clipboard writeText microtask so setCopySuccess(true) applies.
    await act(async () => {});
    expect(screen.getByText("Copied! ✓")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(2000);
    });

    expect(screen.queryByText("Copied! ✓")).not.toBeInTheDocument();
    expect(screen.getByText("Copy JSON")).toBeInTheDocument();
  });

  // T1.5: Dynamic Portfolio Changes — preview reflects the current portfolio prop.
  it("automatically updates export preview when the underlying portfolio changes", async () => {
    const { rerender } = render(
      <NotebookMLExport portfolio={makePortfolio({ total_equity: 5000 })} />
    );
    let preview = await screen.findByTestId("export-preview");
    let parsed = JSON.parse(preview.textContent || "");
    expect(parsed.portfolio.total_equity).toBe(5000);

    rerender(<NotebookMLExport portfolio={makePortfolio({ total_equity: 12000 })} />);
    preview = await screen.findByTestId("export-preview");
    parsed = JSON.parse(preview.textContent || "");
    expect(parsed.portfolio.total_equity).toBe(12000);
  });

  // T2.1: Export Empty Portfolio Status
  it("produces valid JSON containing empty arrays when portfolio has no positions", async () => {
    render(<NotebookMLExport portfolio={makePortfolio({ total_equity: 0, positions: [] })} />);
    const preview = await screen.findByTestId("export-preview");
    const parsed = JSON.parse(preview.textContent || "");
    expect(parsed.portfolio.positions).toEqual([]);
    // A genuine $0 balance is a REAL value and is preserved (not turned to null).
    expect(parsed.portfolio.total_equity).toBe(0);
  });

  // T2.2: Secure Context Clipboard Fallback
  it("displays fallback message if navigator.clipboard is absent", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      writable: true,
      configurable: true,
    });

    render(<NotebookMLExport portfolio={makePortfolio()} />);
    const copyBtn = await screen.findByTestId("copy-export-btn");
    fireEvent.click(copyBtn);

    expect(await screen.findByTestId("clipboard-fallback-warning")).toBeInTheDocument();
  });

  // T2.3: Escaped Export Payloads — the REAL `name` field is JSON-escaped safely.
  it("escapes quotes and special characters in a position name", async () => {
    render(
      <NotebookMLExport
        portfolio={makePortfolio({
          total_equity: 100,
          position_count: 1,
          positions: [
            {
              symbol: "AAPL",
              name: 'Apple "Special" Inc.',
              qty: 1,
              avg_cost: 150,
              current_price: 155,
              market_value: 155,
              unrealized_pl: 5,
              unrealized_pl_pct: 3.33,
            },
          ],
        })}
      />
    );
    const preview = await screen.findByTestId("export-preview");
    expect(preview.textContent).toContain('Apple \\"Special\\" Inc.');
    // The dropped `description` field is never emitted.
    const parsed = JSON.parse(preview.textContent || "");
    expect(parsed.portfolio.positions[0]).not.toHaveProperty("description");
    expect(parsed.portfolio.positions[0].name).toBe('Apple "Special" Inc.');
  });

  // T2.4: Scale Verification
  it("handles exporting portfolios with large positions (>100 items) instantly", async () => {
    const largePositions = Array.from({ length: 150 }).map((_, i) => ({
      symbol: `SYM${i}`,
      name: `Company ${i}`,
      qty: 10,
      avg_cost: 100,
      current_price: 105,
      market_value: 1050,
      unrealized_pl: 50,
      unrealized_pl_pct: 5,
    }));

    const start = performance.now();
    render(
      <NotebookMLExport
        portfolio={makePortfolio({ total_equity: 150000, position_count: 150, positions: largePositions })}
      />
    );
    const preview = await screen.findByTestId("export-preview");
    expect(preview).toBeInTheDocument();
    const parsed = JSON.parse(preview.textContent || "");
    expect(parsed.portfolio.positions.length).toBe(150);
    const end = performance.now();
    expect(end - start).toBeLessThan(500);
  });

  // T2.5: Stub URL Blob Support
  it("falls back to data URI format if URL.createObjectURL is missing", async () => {
    Object.defineProperty(globalThis.URL, "createObjectURL", { value: undefined, writable: true });

    render(<NotebookMLExport portfolio={makePortfolio()} />);

    const dummyAnchor = document.createElement("a");
    const clickSpy = vi.spyOn(dummyAnchor, "click");
    vi.spyOn(document, "createElement").mockImplementation((tagName) => {
      if (tagName === "a") return dummyAnchor;
      return document.createElement(tagName);
    });

    const downloadBtn = await screen.findByTestId("download-export-btn");
    fireEvent.click(downloadBtn);

    expect(clickSpy).toHaveBeenCalled();
    expect(dummyAnchor.href).toContain("data:application/json;charset=utf-8,");
  });

  // --- HONESTY (D2, CONSTRAINT #4): null money fields must serialize as JSON
  // null, NEVER a fabricated 0 that an LLM would read as a real balance. ---

  it("serializes null money fields as JSON null, never a fabricated 0", async () => {
    const portfolio = {
      total_equity: null,
      buying_power: null,
      total_unrealized_pl: 0,
      total_dividends: 0,
      position_count: 1,
      source: "unavailable",
      fetched_at: null,
      positions: [
        {
          symbol: "AAPL",
          name: "Apple",
          qty: 10,
          avg_cost: 150,
          current_price: null,
          market_value: null,
          unrealized_pl: null,
          unrealized_pl_pct: null,
        },
      ],
    } as unknown as Portfolio;

    render(<NotebookMLExport portfolio={portfolio} />);
    const preview = await screen.findByTestId("export-preview");
    const parsed = JSON.parse(preview.textContent || "");

    // Per-position money field: null, NOT 0.
    expect(parsed.portfolio.positions[0].market_value).toBe(null);
    expect(parsed.portfolio.positions[0].market_value).not.toBe(0);
    // Top-level money field: null, NOT 0.
    expect(parsed.portfolio.total_equity).toBe(null);
    expect(parsed.portfolio.total_equity).not.toBe(0);
    expect(parsed.portfolio.buying_power).toBe(null);
    expect(parsed.portfolio.buying_power).not.toBe(0);
  });

  it("disables export buttons and emits a null timestamp until the portfolio resolves", async () => {
    // A parent still loading passes portfolio={null}; the widget is prop-driven
    // and must NOT build a real-timestamped, exportable payload yet.
    render(<NotebookMLExport portfolio={null} />);
    const copyBtn = await screen.findByTestId("copy-export-btn");
    const downloadBtn = screen.getByTestId("download-export-btn");
    expect(copyBtn).toBeDisabled();
    expect(downloadBtn).toBeDisabled();

    const preview = screen.getByTestId("export-preview");
    const parsed = JSON.parse(preview.textContent || "");
    expect(parsed.timestamp).toBe(null);
    expect(parsed.portfolio.total_equity).toBe(null);
  });

  it("does not fabricate a real timestamp mid-fetch; clicking a disabled export is a no-op", async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: writeTextMock },
      writable: true,
      configurable: true,
    });

    render(<NotebookMLExport portfolio={null} />);
    const copyBtn = await screen.findByTestId("copy-export-btn");
    fireEvent.click(copyBtn); // disabled — handler must not run
    expect(writeTextMock).not.toHaveBeenCalled();
  });

  it("becomes ready and stamps a real timestamp once a portfolio resolves via fetch", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValue(makePortfolio({ total_equity: 7777 }));

    render(<NotebookMLExport />);
    const copyBtn = await screen.findByTestId("copy-export-btn");
    await waitFor(() => expect(copyBtn).toBeEnabled());

    const parsed = JSON.parse(screen.getByTestId("export-preview").textContent || "");
    expect(parsed.portfolio.total_equity).toBe(7777);
    expect(typeof parsed.timestamp).toBe("string");
  });
});
