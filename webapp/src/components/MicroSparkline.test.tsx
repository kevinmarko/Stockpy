/**
 * MicroSparkline.test.tsx — covers the bare-SVG sparkline primitive
 * (renamed from the ambiguous `Sparkline` to avoid colliding with
 * `charts.tsx`'s Recharts-based `Sparkline`, see the doc comment on
 * `MicroSparkline` itself for why the two are deliberately separate).
 */
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MicroSparkline } from "./MicroSparkline";
import { theme } from "../theme";

describe("MicroSparkline", () => {
  it("renders nothing when data is an empty array", () => {
    const { container } = render(<MicroSparkline data={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders an svg polyline with exactly as many coordinate pairs as data points", () => {
    const data = [1, 5, 3, 8, 2, 6];
    const { container } = render(<MicroSparkline data={data} />);

    const svg = container.querySelector("svg");
    expect(svg).toBeInTheDocument();

    const polyline = container.querySelector("polyline");
    expect(polyline).toBeInTheDocument();

    const pointsAttr = polyline!.getAttribute("points") ?? "";
    const pairs = pointsAttr.trim().split(/\s+/).filter(Boolean);
    expect(pairs).toHaveLength(data.length);
  });

  it("uses the growth color when the series ends at or above where it started", () => {
    const { container } = render(<MicroSparkline data={[1, 2, 3, 4]} />);
    const polyline = container.querySelector("polyline");
    expect(polyline).toHaveAttribute("stroke", theme.growth);
  });

  it("uses the growth color when the series is flat (last === first)", () => {
    const { container } = render(<MicroSparkline data={[5, 1, 9, 5]} />);
    const polyline = container.querySelector("polyline");
    expect(polyline).toHaveAttribute("stroke", theme.growth);
  });

  it("uses the decline color when the series ends below where it started", () => {
    const { container } = render(<MicroSparkline data={[4, 3, 2, 1]} />);
    const polyline = container.querySelector("polyline");
    expect(polyline).toHaveAttribute("stroke", theme.decline);
  });

  it("an explicit color prop overrides the trend-based default", () => {
    // This series would default to the decline color (last < first) --
    // the explicit override must win regardless.
    const { container } = render(
      <MicroSparkline data={[9, 5, 1]} color="#123456" />
    );
    const polyline = container.querySelector("polyline");
    expect(polyline).toHaveAttribute("stroke", "#123456");
  });
});
