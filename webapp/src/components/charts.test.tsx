/**
 * charts.test.tsx — covers the pieces of the shared Recharts chrome that are
 * plain, non-visual logic: `CustomTooltip`'s active/payload branch, the
 * shared prop-shaped chrome consts (`chartCursorProps` in particular, added
 * alongside the crosshair-consistency pass across all 5 <Tooltip> usages —
 * see the module docstring in charts.tsx), and a smoke test that a full
 * chart component mounts without crashing given a minimal fixture.
 *
 * jsdom has no real layout engine, so `<ResponsiveContainer>` reports 0
 * width/height (test-setup.ts stubs `ResizeObserver` just enough for it to
 * mount without throwing) -- these tests assert on rendered TEXT content
 * (tooltip values, dates) and on "did this throw", not on pixel geometry or
 * SVG path data, which is the same convention other component tests in this
 * app (DataTable.test.tsx, Modal.test.tsx) already follow.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  CustomTooltip,
  PerfLine,
  chartAxisLine,
  chartAxisTick,
  chartCursorProps,
  chartGridProps,
  chartTooltipStyle,
} from "./charts";
import { theme } from "../theme";
import type { CurvePoint } from "../api/types";

describe("CustomTooltip", () => {
  it("renders the formatted date label and value(s) when active+payload are present", () => {
    render(
      <CustomTooltip
        active
        payload={[{ dataKey: "value", value: 123.456, color: theme.growth }]}
        label="2026-07-01"
        valueLabel="Pilot"
        yTickDecimals={0}
      />
    );
    // fmtDateTime renders a date-only ISO string as "Jul 1, 2026".
    expect(screen.getByText("Jul 1, 2026")).toBeInTheDocument();
    expect(screen.getByText("Pilot")).toBeInTheDocument();
    // default valueFormat="number" -> v.toFixed(yTickDecimals) = "123".
    expect(screen.getByText("123")).toBeInTheDocument();
  });

  it("formats a currency series via fmtUsd when valueFormat='currency'", () => {
    render(
      <CustomTooltip
        active
        payload={[{ dataKey: "value", value: 1234.5, color: theme.growth }]}
        label="2026-07-01"
        valueFormat="currency"
      />
    );
    expect(screen.getByText("$1,234.50")).toBeInTheDocument();
  });

  it("labels a 'macro' series with the given macroLabel", () => {
    render(
      <CustomTooltip
        active
        payload={[{ dataKey: "macro", value: 100, color: theme.accent }]}
        label="2026-07-01"
        macroLabel="S&P 500"
      />
    );
    expect(screen.getByText("S&P 500")).toBeInTheDocument();
  });

  it("renders nothing (null) when active is false", () => {
    const { container } = render(
      <CustomTooltip
        active={false}
        payload={[{ dataKey: "value", value: 1, color: theme.growth }]}
        label="2026-07-01"
      />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing (null) when payload is empty, even if active", () => {
    const { container } = render(<CustomTooltip active payload={[]} label="2026-07-01" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing (null) when payload is undefined", () => {
    const { container } = render(<CustomTooltip active label="2026-07-01" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("shared chart chrome exports", () => {
  it("chartCursorProps has the expected crosshair shape", () => {
    expect(chartCursorProps).toEqual({
      stroke: theme.borderStrong,
      strokeWidth: 1,
      strokeDasharray: "4 4",
    });
  });

  it("chartAxisTick/chartAxisLine/chartGridProps still have their documented shapes", () => {
    expect(chartAxisTick).toEqual({ fill: theme.textMuted, fontSize: 10 });
    expect(chartAxisLine).toEqual({ axisLine: false, tickLine: false });
    expect(chartGridProps).toEqual({
      vertical: false,
      stroke: theme.chartGrid,
      strokeDasharray: "0",
    });
  });

  it("chartTooltipStyle stays on the opaque surface3 (pinned by chartChrome.test.ts's 'one tooltip surface, not two' invariant against index.css's .recharts-default-tooltip fallback)", () => {
    expect(chartTooltipStyle.background).toBe(theme.surface3);
  });
});

describe("PerfLine", () => {
  const DATA: CurvePoint[] = [
    { date: "2026-06-01", value: 100 },
    { date: "2026-06-02", value: 101.5 },
    { date: "2026-06-03", value: 99.8 },
  ];

  it("renders without crashing given a minimal CurvePoint[] fixture", () => {
    expect(() => render(<PerfLine data={DATA} />)).not.toThrow();
  });

  it("renders nothing for an empty data array (no fabricated empty chart)", () => {
    const { container } = render(<PerfLine data={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders without crashing with a benchmark and a secondary-axis macro overlay", () => {
    const benchmark: CurvePoint[] = [
      { date: "2026-06-01", value: 100 },
      { date: "2026-06-02", value: 100.2 },
      { date: "2026-06-03", value: 99.9 },
    ];
    const macroBenchmark: CurvePoint[] = [
      { date: "2026-06-01", value: 5000 },
      { date: "2026-06-02", value: 5010 },
      { date: "2026-06-03", value: 4990 },
    ];
    expect(() =>
      render(
        <PerfLine
          data={DATA}
          benchmark={benchmark}
          macroBenchmark={macroBenchmark}
          macroSecondaryAxis
          valueFormat="currency"
        />
      )
    ).not.toThrow();
  });
});
