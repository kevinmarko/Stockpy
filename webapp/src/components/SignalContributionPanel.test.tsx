/**
 * SignalContributionPanel.test.tsx
 *
 * Covers the happy-path render (bar chart of a symbol's per-module signal
 * contribution, via the EXISTING SignalBreakdown/SignalModuleScore types --
 * this panel deliberately does not define its own duplicate types), the
 * loading state, the error state, and the real empty/cold-start state (never
 * a fabricated bar in place of missing signal data -- CONSTRAINT #4). `api`
 * is already the mock (VITE_USE_MOCK default-true); we spy on individual api
 * methods only for the fixtures each test needs, mirroring
 * SignalBreakdownMiniWidget.test.tsx's convention.
 *
 * Recharts renders its SVG bars/axis ticks off a real measured pixel size
 * (via ResponsiveContainer), which jsdom never provides -- so, matching this
 * codebase's own chart-testing convention, assertions here target the
 * component's own plain-DOM output (heading text, empty/error copy, the
 * `.recharts-responsive-container` mount point itself) rather than
 * chart-internal tick/bar text that never renders in this environment.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SignalContributionPanel } from "./SignalContributionPanel";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import type { SignalBreakdown } from "../api/types";

function makeBreakdown(overrides: Partial<SignalBreakdown> = {}): SignalBreakdown {
  return {
    symbol: "AAPL",
    action: "BUY",
    conviction: 0.58,
    final_score: 20,
    modules: [
      { name: "timeseries_momentum", score: 0.62, weight: 20, contribution: 12.4 },
      { name: "multifactor", score: -0.18, weight: 15, contribution: -2.7 },
      // Honest null: a module that didn't run this cycle -- must be filtered
      // out of the chart, never rendered as a fabricated zero-height bar.
      { name: "rsi2_mean_reversion", score: null, weight: 10, contribution: null },
    ],
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SignalContributionPanel", () => {
  it("renders a real loading state before data arrives, then mounts the chart once data lands", async () => {
    let resolveFn: (v: SignalBreakdown) => void = () => {};
    vi.spyOn(api, "getSignalBreakdown").mockImplementation(
      () => new Promise((res) => { resolveFn = res; })
    );

    const { container } = render(<SignalContributionPanel symbol="AAPL" />);
    expect(container.querySelector(".skeleton")).not.toBeNull();
    expect(container.querySelector(".recharts-responsive-container")).toBeNull();

    resolveFn(makeBreakdown());
    expect(
      await screen.findByText("Signal Contribution Breakdown")
    ).toBeInTheDocument();
    expect(container.querySelector(".skeleton")).toBeNull();
    expect(container.querySelector(".recharts-responsive-container")).not.toBeNull();
  });

  it("fetches the given symbol's real breakdown (never a duplicate local type)", async () => {
    const spy = vi.spyOn(api, "getSignalBreakdown").mockResolvedValue(makeBreakdown());

    render(<SignalContributionPanel symbol="AAPL" />);

    await screen.findByText("Signal Contribution Breakdown");
    expect(spy).toHaveBeenCalledWith("AAPL");
  });

  it("shows the compact heading-less layout when compact is set", async () => {
    vi.spyOn(api, "getSignalBreakdown").mockResolvedValue(makeBreakdown());

    const { container } = render(<SignalContributionPanel symbol="AAPL" compact />);
    await vi.waitFor(() =>
      expect(container.querySelector(".recharts-responsive-container")).not.toBeNull()
    );
    expect(screen.queryByText("Signal Contribution Breakdown")).not.toBeInTheDocument();
  });

  it("shows the full heading when compact is not set", async () => {
    vi.spyOn(api, "getSignalBreakdown").mockResolvedValue(makeBreakdown());

    render(<SignalContributionPanel symbol="AAPL" />);

    expect(await screen.findByText("Signal Contribution Breakdown")).toBeInTheDocument();
  });

  it("shows a real error state with Retry on a hard error, and recovers on retry", async () => {
    const spy = vi
      .spyOn(api, "getSignalBreakdown")
      .mockRejectedValueOnce(new ApiError("boom", 500));

    const { container } = render(<SignalContributionPanel symbol="AAPL" />);

    expect(await screen.findByText("boom")).toBeInTheDocument();

    spy.mockResolvedValueOnce(makeBreakdown());
    screen.getByRole("button", { name: "Retry" }).click();
    await screen.findByText("Signal Contribution Breakdown");
    expect(container.querySelector(".recharts-responsive-container")).not.toBeNull();
  });

  it("shows the real empty/cold-start state (never a fabricated bar) when modules is empty", async () => {
    vi.spyOn(api, "getSignalBreakdown").mockResolvedValue(
      makeBreakdown({ action: null, conviction: null, final_score: null, modules: [] })
    );

    const { container } = render(<SignalContributionPanel symbol="ZZZZ" />);

    expect(await screen.findByText("No Signal Data")).toBeInTheDocument();
    expect(container.querySelector(".recharts-responsive-container")).toBeNull();
  });

  it("treats all-null module contributions as the empty state, not a blank chart", async () => {
    vi.spyOn(api, "getSignalBreakdown").mockResolvedValue(
      makeBreakdown({
        modules: [
          { name: "timeseries_momentum", score: null, weight: 20, contribution: null },
          { name: "multifactor", score: null, weight: 15, contribution: null },
        ],
      })
    );

    const { container } = render(<SignalContributionPanel symbol="AAPL" />);

    expect(await screen.findByText("No Signal Data")).toBeInTheDocument();
    expect(container.querySelector(".recharts-responsive-container")).toBeNull();
  });
});
