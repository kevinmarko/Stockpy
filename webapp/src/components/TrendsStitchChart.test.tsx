import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TrendsStitchChart } from "./TrendsStitchChart";
import { isoToUtcMs } from "./TrendsRawWindowsChart";
import type { TrendsRescaledWindow, TrendsSeam, TrendsStitched } from "../api/types";

const D = isoToUtcMs;
const RESC: TrendsRescaledWindow[] = [
  { index: 0, points: [[D("2025-10-10"), 40], [D("2025-10-11"), 130]] },
  { index: 1, points: [[D("2025-10-11"), 125], [D("2025-10-12"), null]] },
];
const STITCHED: TrendsStitched = {
  points: [[D("2025-10-10"), 40], [D("2025-10-11"), 128]],
  first_date: "2025-10-10", last_date: "2025-10-11", days: 2, persisted_at: null, from_latest_batch: true,
};
const SEAM: TrendsSeam = {
  index: 1, a_window: 0, b_window: 1, overlap_start: "2025-10-11", overlap_end: "2025-10-12",
  overlap_days: 2, sum_a: 10, sum_b: 9, applied_factor: 1.1, pair_factor: 1.1, degenerate_guard: false,
};

describe("TrendsStitchChart", () => {
  it("shows an empty state with no data", () => {
    render(<TrendsStitchChart rescaled={[]} stitched={null} seams={[]} />);
    expect(screen.getByText("No stitched series to show")).toBeInTheDocument();
  });

  it("renders with values above 100 and a seam overlap without crashing", () => {
    expect(() =>
      render(<TrendsStitchChart rescaled={RESC} stitched={STITCHED} seams={[SEAM]} />),
    ).not.toThrow();
    expect(screen.getByTestId("trends-stitch-chart")).toBeInTheDocument();
  });

  it("tolerates unparseable seam dates and stitched-only input", () => {
    render(
      <TrendsStitchChart rescaled={[]} stitched={STITCHED} seams={[{ ...SEAM, overlap_start: "bad" }]} />,
    );
    expect(screen.getByTestId("trends-stitch-chart")).toBeInTheDocument();
  });
});
