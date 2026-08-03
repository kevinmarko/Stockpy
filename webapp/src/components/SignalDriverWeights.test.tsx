/**
 * SignalDriverWeights.test.tsx — smoke tests for the universe-wide signal
 * driver-weight bar chart.
 *
 * `<ResponsiveContainer>` has zero layout in jsdom, so the actual Recharts
 * SVG bars never mount here (a well-known Recharts + jsdom limitation --
 * see ValidationTrend.test.tsx for this codebase's established precedent of
 * asserting on the chart section's `data-testid` wrapper instead). Likewise
 * Recharts only ever mounts a `<Tooltip>`'s content on a real mouse hover,
 * which jsdom's zero-layout container never produces -- so the tooltip is
 * exercised directly via the exported `SignalDriverTooltip` component
 * (hoisted to module scope specifically for this reason) rather than by
 * simulating a chart hover.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import SignalDriverWeights, { SignalDriverTooltip, type SHAPFeature } from "./SignalDriverWeights";

describe("SignalDriverWeights", () => {
  it("renders the chart section for a fixed data prop, bypassing the live fetch", () => {
    const data: SHAPFeature[] = [
      { name: "timeseries_momentum", value: 5.2, normalizedContribution: 0.62, configWeight: 0.12 },
      { name: "multifactor", value: 3.1, normalizedContribution: 0.38, configWeight: 0.15 },
    ];
    render(<SignalDriverWeights data={data} />);
    expect(screen.getByTestId("signal-driver-weights-chart")).toBeInTheDocument();
    expect(screen.getByText("Signal Driver Weights")).toBeInTheDocument();
  });

  it("renders the honest empty state for an empty data array, never crashing", () => {
    render(<SignalDriverWeights data={[]} />);
    expect(screen.getByTestId("signal-driver-weights-chart")).toBeInTheDocument();
    expect(
      screen.getByText("No tracked symbols yet — run the pipeline, then reload.")
    ).toBeInTheDocument();
  });

  it("tooltip shows both Normalized Contribution and Absolute Config Weight when config_weight is present", () => {
    const point: SHAPFeature = {
      name: "timeseries_momentum",
      value: 5.2,
      normalizedContribution: 0.62,
      configWeight: 0.12,
    };
    render(<SignalDriverTooltip active payload={[{ payload: point }]} />);
    expect(screen.getByText("timeseries_momentum")).toBeInTheDocument();
    expect(screen.getByText(/Mean \|Contribution\|: 520\.0%/)).toBeInTheDocument();
    expect(screen.getByText(/Normalized Contribution: 62\.0%/)).toBeInTheDocument();
    expect(screen.getByText(/Absolute Config Weight: 0\.120/)).toBeInTheDocument();
  });

  it("tooltip omits the normalized/config-weight lines when neither is present, never fabricating a value", () => {
    const point: SHAPFeature = { name: "macd_momentum", value: 2.0 };
    render(<SignalDriverTooltip active payload={[{ payload: point }]} />);
    expect(screen.getByText("macd_momentum")).toBeInTheDocument();
    expect(screen.queryByText(/Normalized Contribution/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Absolute Config Weight/)).not.toBeInTheDocument();
  });

  it("tooltip renders nothing when inactive", () => {
    const { container } = render(
      <SignalDriverTooltip active={false} payload={[{ payload: { name: "x", value: 1 } }]} />
    );
    expect(container).toBeEmptyDOMElement();
  });
});
