/**
 * DynamicGrid.test.tsx — covers the stale-layout reconciliation fix
 * (`reconcileLayout`, an exported pure function so it can be exercised
 * directly without rendering the real react-grid-layout under jsdom) plus a
 * couple of thin component-level smoke tests for `resetGridLayout`'s new
 * confirmation guard.
 *
 * `DynamicGrid` itself renders a plain `<div>` under Vitest (the existing
 * `isTest` bypass, left as-is) since react-grid-layout's real DOM
 * measurement doesn't work under jsdom -- so the reconciliation logic is
 * tested at the function level, which is exactly why it was extracted.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ResponsiveLayouts } from "react-grid-layout";
import { reconcileLayout, resetGridLayout, DynamicGrid } from "./DynamicGrid";
import { render, screen, fireEvent } from "@testing-library/react";

vi.mock("react-grid-layout", async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-grid-layout')>();
  return {
    ...actual,
    ResponsiveGridLayout: (props: any) => (
      <div data-testid="mock-rgl" data-drag-enabled={props.dragConfig?.enabled} data-resize-enabled={props.resizeConfig?.enabled}>
        {props.children}
      </div>
    )
  };
});

// Mock hook used by DynamicGrid
vi.mock("./hooks/useContainerWidth", () => ({
  useContainerWidth: () => ({ width: 1000, containerRef: { current: null }, mounted: true })
}));

const defaultLayouts: ResponsiveLayouts = {
  lg: [
    { i: "AAPL", x: 0, y: 0, w: 4, h: 4, minW: 3, minH: 3 },
    { i: "MSFT", x: 4, y: 0, w: 4, h: 4, minW: 3, minH: 3 },
    { i: "GOOG", x: 8, y: 0, w: 4, h: 4, minW: 3, minH: 3 },
  ],
};

describe("reconcileLayout", () => {
  it("falls back to defaultLayouts verbatim on a fresh mount with no saved layout", () => {
    expect(reconcileLayout(null, defaultLayouts, ["AAPL", "MSFT", "GOOG"])).toBe(defaultLayouts);
    expect(reconcileLayout(undefined, defaultLayouts, ["AAPL", "MSFT", "GOOG"])).toBe(defaultLayouts);
    expect(reconcileLayout("", defaultLayouts, ["AAPL", "MSFT", "GOOG"])).toBe(defaultLayouts);
  });

  it("falls back gracefully (does not throw) on a corrupt/malformed saved-layout JSON blob", () => {
    expect(() => reconcileLayout("{not valid json", defaultLayouts, ["AAPL"])).not.toThrow();
    expect(reconcileLayout("{not valid json", defaultLayouts, ["AAPL"])).toBe(defaultLayouts);

    // Valid JSON, but not the expected shape (an array / a scalar instead of
    // a per-breakpoint object) -- also must not throw and must fall back.
    expect(reconcileLayout("[1,2,3]", defaultLayouts, ["AAPL"])).toBe(defaultLayouts);
    expect(reconcileLayout("42", defaultLayouts, ["AAPL"])).toBe(defaultLayouts);
    expect(reconcileLayout("null", defaultLayouts, ["AAPL"])).toBe(defaultLayouts);
  });

  it("drops a saved entry whose key is no longer present in children (stale item)", () => {
    const saved = JSON.stringify({
      lg: [
        { i: "AAPL", x: 0, y: 0, w: 4, h: 4 },
        { i: "DELISTED", x: 4, y: 0, w: 4, h: 4 },
      ],
    });

    const result = reconcileLayout(saved, defaultLayouts, ["AAPL"]);

    expect(result.lg).toHaveLength(1);
    expect(result.lg?.map((item) => item.i)).toEqual(["AAPL"]);
    // The kept item's saved position/size is preserved untouched.
    expect(result.lg?.[0]).toMatchObject({ i: "AAPL", x: 0, y: 0, w: 4, h: 4 });
  });

  it("auto-places a new child key not present in the saved layout, non-overlapping with existing items", () => {
    const saved = JSON.stringify({
      lg: [{ i: "AAPL", x: 0, y: 0, w: 4, h: 4 }],
    });

    const result = reconcileLayout(saved, defaultLayouts, ["AAPL", "MSFT"]);

    expect(result.lg).toHaveLength(2);
    const aapl = result.lg?.find((item) => item.i === "AAPL");
    const msft = result.lg?.find((item) => item.i === "MSFT");
    expect(aapl).toMatchObject({ x: 0, y: 0, w: 4, h: 4 });
    // Placed below the existing item's bottom edge (y=0, h=4 -> y=4), and
    // picks up its w/h/minW/minH template from defaultLayouts.
    expect(msft).toMatchObject({ y: 4, w: 4, h: 4, minW: 3, minH: 3 });
    // Non-overlapping: the new item's y is at/after the max y+h of every
    // kept item.
    expect((msft?.y ?? 0)).toBeGreaterThanOrEqual((aapl?.y ?? 0) + (aapl?.h ?? 0));
  });

  it("stacks multiple new items one below another without overlapping each other", () => {
    const saved = JSON.stringify({ lg: [{ i: "AAPL", x: 0, y: 0, w: 4, h: 4 }] });

    const result = reconcileLayout(saved, defaultLayouts, ["AAPL", "MSFT", "GOOG"]);

    expect(result.lg).toHaveLength(3);
    const msft = result.lg?.find((item) => item.i === "MSFT")!;
    const goog = result.lg?.find((item) => item.i === "GOOG")!;
    expect(goog.y).toBeGreaterThanOrEqual(msft.y + msft.h);
  });

  it("falls back to a sane default size for a new item with no defaultLayouts template", () => {
    const saved = JSON.stringify({ lg: [] });
    const result = reconcileLayout(saved, { lg: [] }, ["NEWSYM"]);

    expect(result.lg).toHaveLength(1);
    expect(result.lg?.[0]).toMatchObject({ i: "NEWSYM", x: 0, y: 0 });
    expect(result.lg?.[0].w).toBeGreaterThan(0);
    expect(result.lg?.[0].h).toBeGreaterThan(0);
  });

  it("handles multiple breakpoints independently, and passes through a breakpoint with no saved data at all", () => {
    const multiDefault: ResponsiveLayouts = {
      lg: [{ i: "AAPL", x: 0, y: 0, w: 4, h: 4 }],
      sm: [{ i: "AAPL", x: 0, y: 0, w: 2, h: 4 }],
    };
    // Saved blob only ever recorded `lg` (e.g. the operator never resized
    // below the `sm` breakpoint).
    const saved = JSON.stringify({ lg: [{ i: "AAPL", x: 0, y: 0, w: 4, h: 4 }] });

    const result = reconcileLayout(saved, multiDefault, ["AAPL"]);

    expect(result.lg).toEqual([{ i: "AAPL", x: 0, y: 0, w: 4, h: 4 }]);
    // `sm` had no saved entry at all -- use the fresh default verbatim.
    expect(result.sm).toEqual(multiDefault.sm);
  });

  it("returns an empty layout for a breakpoint when every saved item is stale and no children remain", () => {
    const saved = JSON.stringify({ lg: [{ i: "DELISTED", x: 0, y: 0, w: 4, h: 4 }] });
    const result = reconcileLayout(saved, defaultLayouts, []);
    expect(result.lg).toEqual([]);
  });
});

describe("resetGridLayout", () => {
  let reloadSpy: ReturnType<typeof vi.fn>;
  let removeItemSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    reloadSpy = vi.fn();
    Object.defineProperty(window, "location", {
      value: { ...window.location, reload: reloadSpy },
      writable: true,
    });
    removeItemSpy = vi.spyOn(window.localStorage.__proto__, "removeItem");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does nothing when the user cancels the confirmation", () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);

    resetGridLayout("options-matrix");

    expect(removeItemSpy).not.toHaveBeenCalled();
    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it("clears storage and reloads once the user confirms", () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);

    resetGridLayout("options-matrix");

    expect(removeItemSpy).toHaveBeenCalledWith("grid-layout-options-matrix");
    expect(reloadSpy).toHaveBeenCalledTimes(1);
  });

  it("asks a clear confirmation question before doing anything destructive", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    resetGridLayout("strategy-matrix");

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy.mock.calls[0][0]).toMatch(/reset/i);
  });
});

describe("DynamicGrid component rendering", () => {
  beforeEach(() => {
    localStorage.clear();
    process.env.TEST_RENDER_DYNAMIC_GRID = "true";
  });

  afterEach(() => {
    delete process.env.TEST_RENDER_DYNAMIC_GRID;
  });

  it("disables drag on mobile screens", () => {
    // Mock mobile screen width
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 500 });
    window.dispatchEvent(new Event('resize'));

    render(
      <DynamicGrid layoutKey="test" defaultLayouts={defaultLayouts}>
        <div key="AAPL">Apple</div>
        <div key="MSFT">Microsoft</div>
        <div key="GOOG">Google</div>
      </DynamicGrid>
    );

    const rgl = screen.getByTestId("mock-rgl");
    // dragConfig={{ enabled: !isMobile }} means enabled should be false
    expect(rgl.getAttribute("data-drag-enabled")).toBe("false");
  });

  it("enables drag on desktop screens", () => {
    // Mock desktop screen width
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1200 });
    window.dispatchEvent(new Event('resize'));

    render(
      <DynamicGrid layoutKey="test" defaultLayouts={defaultLayouts}>
        <div key="AAPL">Apple</div>
        <div key="MSFT">Microsoft</div>
        <div key="GOOG">Google</div>
      </DynamicGrid>
    );

    const rgl = screen.getByTestId("mock-rgl");
    expect(rgl.getAttribute("data-drag-enabled")).toBe("true");
  });

  it("toggles reorder overlay and moves items via up/down buttons", () => {
    render(
      <DynamicGrid layoutKey="test" defaultLayouts={defaultLayouts}>
        <div key="AAPL">Apple</div>
        <div key="MSFT">Microsoft</div>
        <div key="GOOG">Google</div>
      </DynamicGrid>
    );

    // Click Reorder Widgets toggle
    const reorderBtn = screen.getByRole("button", { name: /Reorder Widgets/i });
    fireEvent.click(reorderBtn);

    // Overlay buttons should appear
    const moveAaplDown = screen.getByRole("button", { name: /Move widget AAPL down/i });
    expect(moveAaplDown).toBeTruthy();

    // Click down to move AAPL down (should swap with MSFT)
    fireEvent.click(moveAaplDown);

    // The layout state updates internally. The result is saved to localStorage.
    const saved = localStorage.getItem("grid-layout-test");
    expect(saved).not.toBeNull();
    const parsed = JSON.parse(saved!);
    
    const aapl = parsed.lg.find((i: any) => i.i === "AAPL");
    const msft = parsed.lg.find((i: any) => i.i === "MSFT");
    
    // MSFT was at x: 4, AAPL was at x: 0
    // After moving AAPL down (right), AAPL should be at x: 4, MSFT at x: 0
    expect(aapl.x).toBe(4);
    expect(msft.x).toBe(0);
  });
});
