import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  TrendsRawWindowsChart,
  formatUtcDate,
  isoToUtcMs,
  lastNonNullPoint,
  mergeSeries,
  windowLabel,
} from "./TrendsRawWindowsChart";
import type { TrendsRawWindow } from "../api/types";

const D = (iso: string) => isoToUtcMs(iso);
const W: TrendsRawWindow[] = [
  { index: 0, start: "2025-10-10", end: "2026-01-08", peak_date: "2025-12-01", peak_value: 100,
    points: [[D("2025-10-10"), 40], [D("2025-10-11"), 100], [D("2025-10-12"), null]] },
  { index: 1, start: "2025-12-10", end: "2026-03-10", peak_date: "2026-02-01", peak_value: 100,
    points: [[D("2025-10-11"), 55], [D("2025-10-12"), 20]] },
];

describe("TrendsRawWindowsChart", () => {
  it("shows an honest empty state for no windows", () => {
    render(<TrendsRawWindowsChart windows={[]} />);
    expect(screen.getByText("No raw windows to show")).toBeInTheDocument();
  });

  it("shows the empty state for null input and all-null windows", () => {
    // @ts-expect-error defensive branch
    const { unmount } = render(<TrendsRawWindowsChart windows={null} />);
    expect(screen.getByTestId("trends-raw-empty")).toBeInTheDocument();
    unmount();
    render(<TrendsRawWindowsChart windows={[{ ...W[0], points: [[D("2025-10-10"), null]] }]} />);
    expect(screen.getByTestId("trends-raw-empty")).toBeInTheDocument();
  });

  it("renders a chart with the normalization note without crashing", () => {
    render(<TrendsRawWindowsChart windows={W} />);
    expect(screen.getByTestId("trends-raw-chart")).toBeInTheDocument();
    expect(screen.getByText(/own peak of 100/)).toBeInTheDocument();
  });

  it("formats dates in UTC (no off-by-one for US viewers)", () => {
    expect(formatUtcDate(Date.UTC(2026, 0, 1))).toBe("Jan 1, 2026");
    expect(formatUtcDate(undefined)).toBe("");
  });

  it("labels windows with 1-based number and range", () => {
    expect(windowLabel(W[0])).toBe("Window 1 (2025-10-10 → 2026-01-08)");
  });

  it("mergeSeries leaves nulls absent (never 0)", () => {
    const rows = mergeSeries([{ key: "a", points: W[0].points }, { key: "b", points: W[1].points }]);
    const r = rows.find((x) => x.t === D("2025-10-12"))!;
    expect("a" in r).toBe(false);
    expect(r.b).toBe(20);
    expect(lastNonNullPoint([[1, 5], [2, null]])).toEqual([1, 5]);
    expect(lastNonNullPoint([[1, null]])).toBeNull();
  });
});
