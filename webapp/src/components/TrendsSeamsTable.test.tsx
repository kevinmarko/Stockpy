import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TrendsSeamsTable } from "./TrendsSeamsTable";
import type { TrendsSeam } from "../api/types";

const OK: TrendsSeam = {
  index: 1, a_window: 0, b_window: 1, overlap_start: "2025-12-10", overlap_end: "2026-01-08",
  overlap_days: 30, sum_a: 1234.56, sum_b: 987.66, applied_factor: 1.2345678, pair_factor: 1.2345678,
  degenerate_guard: false,
};
const DEGEN: TrendsSeam = {
  ...OK, index: 2, a_window: 1, b_window: 2, sum_a: 0, sum_b: null, applied_factor: 1,
  pair_factor: null, degenerate_guard: true,
};

describe("TrendsSeamsTable", () => {
  it("shows an empty state for no seams", () => {
    render(<TrendsSeamsTable seams={[]} />);
    expect(screen.getByText("No seams to show")).toBeInTheDocument();
  });

  it("formats factors to 4 decimals and sums to 1 decimal, windows 1-based", () => {
    render(<TrendsSeamsTable seams={[OK]} />);
    const row = screen.getByTestId("trends-seam-row-1");
    expect(row).toHaveTextContent("1.2346");
    expect(row).toHaveTextContent("1234.6");
    expect(row).toHaveTextContent("987.7");
    expect(row).toHaveTextContent("1 → 2");
    expect(row).toHaveTextContent("2025-12-10 → 2026-01-08");
    expect(row).not.toHaveTextContent("guard");
  });

  it("renders a degenerate row with em dashes and the guard badge", () => {
    render(<TrendsSeamsTable seams={[DEGEN]} />);
    const row = screen.getByTestId("trends-seam-row-2");
    expect(within(row).getByText("guard: f=1")).toBeInTheDocument();
    const cells = within(row).getAllByRole("cell");
    expect(cells[5]).toHaveTextContent("—"); // sum_b null
    expect(cells[7]).toHaveTextContent("—"); // pair_factor null
    expect(row).not.toHaveTextContent("NaN");
  });

  it("wraps the table for horizontal scroll", () => {
    render(<TrendsSeamsTable seams={[OK]} />);
    expect(screen.getByTestId("trends-seams-table").style.overflowX).toBe("auto");
  });
});
