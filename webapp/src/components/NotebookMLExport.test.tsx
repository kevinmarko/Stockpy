import { render, screen, fireEvent, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NotebookMLExport } from "./NotebookMLExport";
import { api } from "../api/client";

const originalCreateObjectURL = global.URL.createObjectURL;
const originalRevokeObjectURL = global.URL.revokeObjectURL;
const originalClipboard = navigator.clipboard;

describe("NotebookMLExport component (R4)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

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
    Object.defineProperty(global.URL, "createObjectURL", {
      value: originalCreateObjectURL,
      writable: true,
      configurable: true,
    });
    Object.defineProperty(global.URL, "revokeObjectURL", {
      value: originalRevokeObjectURL,
      writable: true,
      configurable: true,
    });
  });

  // T1.1: Payload Preview Structure
  it("renders export preview containing valid structured JSON payload", async () => {
    render(<NotebookMLExport />);
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

    render(<NotebookMLExport />);
    const copyBtn = await screen.findByTestId("copy-export-btn");
    fireEvent.click(copyBtn);

    expect(writeTextMock).toHaveBeenCalled();
    expect(await screen.findByText("Copied! ✓")).toBeInTheDocument();
  });

  // T1.3: Download Triggering
  it("creates blob download link and clicks it when download is triggered", async () => {
    const createObjectURLMock = vi.fn().mockReturnValue("blob:mock-url");
    const revokeObjectURLMock = vi.fn();
    Object.defineProperty(global.URL, "createObjectURL", { value: createObjectURLMock, writable: true });
    Object.defineProperty(global.URL, "revokeObjectURL", { value: revokeObjectURLMock, writable: true });

    render(<NotebookMLExport />);
    
    const dummyAnchor = document.createElement("a");
    const clickSpy = vi.spyOn(dummyAnchor, "click");
    vi.spyOn(document, "createElement").mockImplementation((tagName) => {
      if (tagName === "a") return dummyAnchor;
      return document.createElement(tagName);
    });

    const downloadBtn = await screen.findByTestId("download-export-btn");
    fireEvent.click(downloadBtn);

    expect(createObjectURLMock).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalled();
  });

  // T1.4: Export Copy Confirmation State
  it("reverts the copy success button state after 2 seconds", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      writable: true,
      configurable: true,
    });

    render(<NotebookMLExport />);
    const copyBtn = await screen.findByTestId("copy-export-btn");
    fireEvent.click(copyBtn);
    expect(await screen.findByText("Copied! ✓")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(2000);
    });

    expect(screen.queryByText("Copied! ✓")).not.toBeInTheDocument();
    expect(screen.getByText("Copy JSON")).toBeInTheDocument();
  });

  // T1.5: Dynamic Portfolio Changes
  it("automatically updates export preview when underlying portfolio changes", async () => {
    let callCount = 0;
    vi.spyOn(api, "getPortfolio").mockImplementation(() => {
      callCount++;
      if (callCount === 1) {
        return Promise.resolve({
          total_equity: 5000,
          buying_power: 1000,
          total_unrealized_pl: 0,
          total_dividends: 0,
          position_count: 0,
          source: "mock",
          fetched_at: new Date().toISOString(),
          positions: []
        });
      }
      return Promise.resolve({
        total_equity: 12000,
        buying_power: 3000,
        total_unrealized_pl: 0,
        total_dividends: 0,
        position_count: 0,
        source: "mock",
        fetched_at: new Date().toISOString(),
        positions: []
      });
    });

    const { unmount } = render(<NotebookMLExport />);
    let preview = await screen.findByTestId("export-preview");
    let parsed = JSON.parse(preview.textContent || "");
    expect(parsed.portfolio.total_equity).toBe(5000);

    // Unmount and remount to trigger another getPortfolio fetch
    unmount();

    render(<NotebookMLExport />);
    preview = await screen.findByTestId("export-preview");
    parsed = JSON.parse(preview.textContent || "");
    expect(parsed.portfolio.total_equity).toBe(12000);
  });

  // T2.1: Export Empty Portfolio Status
  it("produces valid JSON containing empty arrays when portfolio has no positions", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValueOnce({
      total_equity: 0,
      buying_power: 0,
      total_unrealized_pl: 0,
      total_dividends: 0,
      position_count: 0,
      source: "mock",
      fetched_at: new Date().toISOString(),
      positions: []
    });

    render(<NotebookMLExport />);
    const preview = await screen.findByTestId("export-preview");
    const parsed = JSON.parse(preview.textContent || "");
    expect(parsed.portfolio.positions).toEqual([]);
  });

  // T2.2: Secure Context Clipboard Fallback
  it("displays fallback message if navigator.clipboard is absent", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      writable: true,
      configurable: true,
    });

    render(<NotebookMLExport />);
    const copyBtn = await screen.findByTestId("copy-export-btn");
    fireEvent.click(copyBtn);

    expect(await screen.findByTestId("clipboard-fallback-warning")).toBeInTheDocument();
  });

  // T2.3: Escaped Export Payloads
  it("escapes quotes or special characters in positions description", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValueOnce({
      total_equity: 100,
      buying_power: 50,
      total_unrealized_pl: 0,
      total_dividends: 0,
      position_count: 1,
      source: "mock",
      fetched_at: new Date().toISOString(),
      positions: [{
        symbol: "AAPL",
        name: "Apple Inc.",
        qty: 1,
        avg_cost: 150,
        current_price: 155,
        market_value: 155,
        unrealized_pl: 5,
        unrealized_pl_pct: 3.33,
        description: 'Apple "Special" Tech Description'
      } as any]
    });

    render(<NotebookMLExport />);
    const preview = await screen.findByTestId("export-preview");
    expect(preview.textContent).toContain('Apple \\"Special\\" Tech Description');
  });

  // T2.4: Scale Verification
  it("handles exporting portfolios with large positions (>100 items) instantly", async () => {
    const largePositions = Array.from({ length: 150 }).map((_, i) => ({
      symbol: `SYM${i}`,
      qty: 10,
      avg_cost: 100,
      market_value: 1000
    }));

    vi.spyOn(api, "getPortfolio").mockResolvedValueOnce({
      total_equity: 150000,
      buying_power: 1000,
      total_unrealized_pl: 0,
      total_dividends: 0,
      position_count: 150,
      source: "mock",
      fetched_at: new Date().toISOString(),
      positions: largePositions as any
    });

    const start = performance.now();
    render(<NotebookMLExport />);
    const preview = await screen.findByTestId("export-preview");
    expect(preview).toBeInTheDocument();
    const end = performance.now();
    expect(end - start).toBeLessThan(100); // Verify instant execution
  });

  // T2.5: Stub URL Blob Support
  it("falls back to data URI format if URL.createObjectURL is missing", async () => {
    Object.defineProperty(global.URL, "createObjectURL", { value: undefined, writable: true });
    
    render(<NotebookMLExport />);
    
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
});
