import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TrendsAsviChart } from "./TrendsAsviChart";
import { isoToUtcMs } from "./TrendsRawWindowsChart";
import type { TrendsAsvi } from "../api/types";

const D = isoToUtcMs;
const ASVI: TrendsAsvi = {
  points: [[D("2026-01-01"), null], [D("2026-01-02"), -0.5], [D("2026-01-03"), 1.23456]],
  latest: 1.23456, latest_date: "2026-01-03", lookback_days: 30,
};

describe("TrendsAsviChart", () => {
  it("shows an empty state for null asvi", () => {
    render(<TrendsAsviChart asvi={null} />);
    expect(screen.getByText("No ASVI series to show")).toBeInTheDocument();
  });

  it("shows an empty state when every point is null", () => {
    render(<TrendsAsviChart asvi={{ ...ASVI, points: [[D("2026-01-01"), null]], latest: null }} />);
    expect(screen.getByTestId("trends-asvi-empty")).toBeInTheDocument();
  });

  it("captions the latest value to 2 decimals", () => {
    render(<TrendsAsviChart asvi={ASVI} />);
    expect(screen.getByTestId("trends-asvi-caption")).toHaveTextContent("1.23");
  });

  it("shows an em dash when latest is null", () => {
    render(<TrendsAsviChart asvi={{ ...ASVI, latest: null, latest_date: null }} />);
    const cap = screen.getByTestId("trends-asvi-caption");
    expect(cap).toHaveTextContent("—");
    expect(cap).not.toHaveTextContent("NaN");
    expect(cap.textContent).not.toMatch(/0\.00/);
  });
});
