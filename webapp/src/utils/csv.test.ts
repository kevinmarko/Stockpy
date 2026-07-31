/**
 * csv.test.ts — unit tests for the shared client-side CSV export utility.
 * `toCsv` is pure (no DOM); `exportCsv` additionally drives the
 * Blob/URL/`<a download>` machinery, mocked here so no real download or
 * jsdom navigation is attempted.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { toCsv, exportCsv } from "./csv";

describe("toCsv", () => {
  it("renders a header row plus one row per record, in column order", () => {
    const rows = [
      { symbol: "AAPL", score: 0.5 },
      { symbol: "NVDA", score: 0.9 },
    ];
    const csv = toCsv(rows, [
      { key: "symbol", header: "Symbol" },
      { key: "score", header: "Score" },
    ]);
    expect(csv).toBe("Symbol,Score\r\nAAPL,0.5\r\nNVDA,0.9");
  });

  it("renders null/undefined as an empty cell, never the literal string", () => {
    const rows = [{ symbol: "AAPL", note: null as string | null }];
    const csv = toCsv(rows, [
      { key: "symbol", header: "Symbol" },
      { key: "note", header: "Note" },
    ]);
    expect(csv).toBe("Symbol,Note\r\nAAPL,");
  });

  it("quotes a cell containing a comma", () => {
    const rows = [{ note: "acted, then passed" }];
    const csv = toCsv(rows, [{ key: "note", header: "Note" }]);
    expect(csv).toBe('Note\r\n"acted, then passed"');
  });

  it("quotes and doubles an embedded quote", () => {
    const rows = [{ note: 'said "hold"' }];
    const csv = toCsv(rows, [{ key: "note", header: "Note" }]);
    expect(csv).toBe('Note\r\n"said ""hold"""');
  });

  it("quotes a cell containing a newline", () => {
    const rows = [{ note: "line one\nline two" }];
    const csv = toCsv(rows, [{ key: "note", header: "Note" }]);
    expect(csv).toBe('Note\r\n"line one\nline two"');
  });

  it("empty rows produces just the header", () => {
    const csv = toCsv([], [{ key: "symbol", header: "Symbol" }]);
    expect(csv).toBe("Symbol");
  });

  it("column order is fixed by the caller, not inferred from row keys", () => {
    const rows = [{ b: "2", a: "1" }];
    const csv = toCsv(rows, [
      { key: "a", header: "A" },
      { key: "b", header: "B" },
    ]);
    expect(csv).toBe("A,B\r\n1,2");
  });
});

describe("exportCsv", () => {
  const originalCreateObjectURL = URL.createObjectURL;
  const originalRevokeObjectURL = URL.revokeObjectURL;

  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    URL.createObjectURL = originalCreateObjectURL;
    URL.revokeObjectURL = originalRevokeObjectURL;
    vi.restoreAllMocks();
  });

  it("creates a Blob URL, clicks a synthetic anchor, then revokes the URL", () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    exportCsv(
      [{ symbol: "AAPL" }],
      [{ key: "symbol", header: "Symbol" }],
      "signals"
    );
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });

  it("appends .csv when the caller's filename lacks it", () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement
    ) {
      expect(this.download).toBe("signals.csv");
    });
    exportCsv([{ symbol: "AAPL" }], [{ key: "symbol", header: "Symbol" }], "signals");
    expect(clickSpy).toHaveBeenCalledTimes(1);
  });

  it("does not double an already-present .csv suffix", () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement
    ) {
      expect(this.download).toBe("signals.csv");
    });
    exportCsv([{ symbol: "AAPL" }], [{ key: "symbol", header: "Symbol" }], "signals.csv");
    expect(clickSpy).toHaveBeenCalledTimes(1);
  });
});
