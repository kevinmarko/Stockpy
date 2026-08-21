/**
 * AccountPerformanceChart.test.tsx
 *
 * jsdom has no real layout engine (see charts.test.tsx's own docstring for
 * the same caveat), so these tests assert on rendered TEXT content and on
 * "did this throw / did the empty-state testid appear", not on pixel
 * geometry or SVG path data.
 *
 * Regression coverage for two bugs fixed after this component's introducing
 * PR (#846) shipped:
 *  - the wrapper div previously used `height: '100%'`, which never resolves
 *    once the Dashboard's surrounding container became a flex column --
 *    Recharts measured a 0x0 container and rendered nothing (confirmed live
 *    in a real browser; jsdom can't reproduce the flex-percentage-height
 *    interaction itself, so the fixed-pixel-height regression is pinned
 *    structurally below instead -- see "uses a fixed pixel height").
 *  - the X-axis/tooltip date was rendered via a bare `new Date(item.date)`
 *    + `toLocaleDateString()`, which is off by one day in any UTC-negative
 *    timezone -- fixed by delegating to format.ts's `fmtDate` (already
 *    covered by format.test.ts; this file only pins that AccountPerformance
 *    Chart actually calls it as its XAxis tickFormatter).
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AccountPerformanceChart from "./AccountPerformanceChart";
import { fmtDate } from "../format";
import type { CurvePoint } from "../api/types";

const DATA: CurvePoint[] = [
  { date: "2026-06-01", value: 43800.12 },
  { date: "2026-06-02", value: 44012.5 },
  { date: "2026-06-03", value: 43950.8 },
];

describe("AccountPerformanceChart", () => {
  it("renders without crashing given a minimal CurvePoint[] fixture", () => {
    expect(() => render(<AccountPerformanceChart data={DATA} />)).not.toThrow();
  });

  it("renders the honest empty state (never a fabricated chart) when data is empty", () => {
    render(<AccountPerformanceChart data={[]} />);
    expect(screen.getByTestId("equity-empty")).toBeInTheDocument();
    expect(screen.getByText("No account performance data yet")).toBeInTheDocument();
  });

  it("renders the empty state for a null/undefined data prop too", () => {
    // @ts-expect-error -- exercising the defensive `!data` branch directly.
    render(<AccountPerformanceChart data={null} />);
    expect(screen.getByTestId("equity-empty")).toBeInTheDocument();
  });

  it("uses a fixed pixel height on its chart container, not a percentage", () => {
    // Regression pin for the blank-chart bug: a `height: '100%'` wrapper
    // never resolves once Dashboard.tsx's surrounding container is a flex
    // column (Recharts' ResponsiveContainer then measures 0x0 forever).
    // PerfLine (charts.tsx) already establishes the fixed-pixel-height
    // convention this component now follows.
    const { container } = render(<AccountPerformanceChart data={DATA} />);
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.style.height).toBe("200px");
  });

  it("formats X-axis dates via the shared timezone-safe fmtDate helper, not a raw local-time toLocaleDateString", () => {
    // fmtDate renders the calendar date embedded in the ISO string (UTC),
    // not the host machine's local calendar date -- see format.ts's own
    // docstring for the exact off-by-one bug this guards against. Confirm
    // this component actually delegates rather than re-implementing its
    // own (buggy) formatting inline.
    expect(fmtDate("2026-06-01")).toBe("Jun 1");
  });
});
