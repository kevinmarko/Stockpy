import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, afterEach } from "vitest";
import { ExplainTickerDrawer, buildGapAwareSeries } from "./ExplainTickerDrawer";
import { ExplainTickerButton } from "./ExplainTickerButton";
import { ExplainTickerProvider, useExplainTicker } from "../context/ExplainTickerContext";
import type { Bar } from "../api/types";

describe("ExplainTickerDrawer", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not render when isOpen is false", () => {
    render(
      <ExplainTickerProvider>
        <ExplainTickerDrawer symbol="AAPL" isOpen={false} onClose={vi.fn()} />
      </ExplainTickerProvider>
    );

    expect(screen.queryByTestId("explain-ticker-drawer")).not.toBeInTheDocument();
  });

  it("renders all 4 honest sections for a fully populated symbol (AAPL)", async () => {
    render(
      <ExplainTickerProvider>
        <ExplainTickerDrawer symbol="AAPL" isOpen={true} onClose={vi.fn()} />
      </ExplainTickerProvider>
    );

    // Header & Profile Data Loaded
    expect(await screen.findByText("Apple Inc.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "AAPL" })).toBeInTheDocument();

    // Section 1: Company Profile
    expect(screen.getByTestId("company-profile-section")).toBeInTheDocument();
    expect(screen.getByText(/smartphones, personal computers/i)).toBeInTheDocument();
    expect(screen.getByText("Consumer Electronics")).toBeInTheDocument();
    expect(screen.getByText("Tim Cook")).toBeInTheDocument();

    // Section 2: Why It's Tracked
    expect(screen.getByTestId("why-tracked-section")).toBeInTheDocument();
    expect(screen.getByText("Held in Portfolio")).toBeInTheDocument();
    expect(screen.getByText(/Held in portfolio \(40 shares\)/i)).toBeInTheDocument();
    expect(screen.getByText("tech_megacap")).toBeInTheDocument();

    // Section 3: Factor Breakdown
    expect(screen.getByTestId("factor-breakdown-section")).toBeInTheDocument();
    expect(screen.getByText("Multifactor Components")).toBeInTheDocument();
    expect(screen.getByText("Momentum & Technicals")).toBeInTheDocument();
    expect(screen.getByText("0.85")).toBeInTheDocument(); // xsec_momentum_rank

    // Section 4: Price History & Recent Price Action
    expect(screen.getByTestId("price-history-section")).toBeInTheDocument();
    expect(screen.getByText(/252 bars/i)).toBeInTheDocument();
    expect(await screen.findByTestId("price-chart")).toBeInTheDocument();
  });

  it("honestly displays company profile unavailable notice when disabled (NVDA)", async () => {
    render(
      <ExplainTickerProvider>
        <ExplainTickerDrawer symbol="NVDA" isOpen={true} onClose={vi.fn()} />
      </ExplainTickerProvider>
    );

    expect(await screen.findByRole("heading", { name: "NVDA" })).toBeInTheDocument();

    // Notice that company profile is disabled/unavailable without hallucinating
    const notice = await screen.findByTestId("profile-unavailable-notice");
    expect(notice).toHaveTextContent(/Company description unavailable/i);
    expect(notice).toHaveTextContent(/FMP_PROFILE_ENABLED=False/i);

    // Tracking and factor breakdown are still honestly shown
    expect(screen.getByTestId("why-tracked-section")).toBeInTheDocument();
    expect(screen.getByTestId("factor-breakdown-section")).toBeInTheDocument();
  });

  it("honestly displays untracked notice and backfill option for untracked symbol (XYZ)", async () => {
    render(
      <ExplainTickerProvider>
        <ExplainTickerDrawer symbol="XYZ" isOpen={true} onClose={vi.fn()} />
      </ExplainTickerProvider>
    );

    expect(await screen.findByRole("heading", { name: "XYZ" })).toBeInTheDocument();

    // Section 2: Untracked notice
    const untrackedNotice = await screen.findByTestId("untracked-notice");
    expect(untrackedNotice).toHaveTextContent(/Not currently tracked in portfolio or watchlists/i);
    expect(screen.getByText("Add to Universe / Backfill")).toBeInTheDocument();

    // Section 3: No signals computed notice
    const noSignalsNotice = await screen.findByTestId("no-signals-notice");
    expect(noSignalsNotice).toHaveTextContent(/No daily signals computed for this cycle/i);
    expect(noSignalsNotice).toHaveTextContent(/not tracked in the quantitative pipeline/i);

    // Section 4: No historical bars notice (no fake chart)
    const noBarsNotice = await screen.findByTestId("no-price-bars-notice");
    expect(noBarsNotice).toHaveTextContent(/No historical price bars available in local store/i);
    expect(screen.queryByTestId("price-chart")).not.toBeInTheDocument();
  });

  it("honestly handles missing signals without fabricating fake scores (COST)", async () => {
    render(
      <ExplainTickerProvider>
        <ExplainTickerDrawer symbol="COST" isOpen={true} onClose={vi.fn()} />
      </ExplainTickerProvider>
    );

    expect(await screen.findByRole("heading", { name: "COST" })).toBeInTheDocument();

    // Section 3: Honest missing signal notice
    const noSignals = await screen.findByTestId("no-signals-notice");
    expect(noSignals).toHaveTextContent(/No daily signals computed for this cycle/i);
    expect(screen.queryByText("Multifactor Components")).not.toBeInTheDocument();
  });

  it("honestly handles missing price bars without fabricating fake lines (XOM)", async () => {
    render(
      <ExplainTickerProvider>
        <ExplainTickerDrawer symbol="XOM" isOpen={true} onClose={vi.fn()} />
      </ExplainTickerProvider>
    );

    expect(await screen.findByRole("heading", { name: "XOM" })).toBeInTheDocument();

    // Section 4: Honest missing bars notice
    const noBars = await screen.findByTestId("no-price-bars-notice");
    expect(noBars).toHaveTextContent(/No historical price bars available in local store/i);
    expect(noBars).toHaveTextContent(/Historical price bars unavailable in local store/i);
    expect(screen.getByText("Trigger Bar Backfill")).toBeInTheDocument();
    expect(screen.queryByTestId("price-chart")).not.toBeInTheDocument();
  });

  it("renders a genuinely distinct 'tracked but not held (watchlist only)' state, not a copy of held or untracked (XOM)", async () => {
    render(
      <ExplainTickerProvider>
        <ExplainTickerDrawer symbol="XOM" isOpen={true} onClose={vi.fn()} />
      </ExplainTickerProvider>
    );

    const trackedSection = await screen.findByTestId("why-tracked-section");
    // Distinct from the held state (AAPL renders the exact string "Held in
    // Portfolio" -- note "Not Held in Portfolio" contains that as a
    // substring, so this checks for the exact held-state heading node,
    // not a substring match).
    expect(trackedSection).toHaveTextContent("Not Held in Portfolio");
    expect(screen.queryByText("Held in Portfolio", { exact: true })).not.toBeInTheDocument();
    // Distinct from the fully-untracked state (never implies "not tracked").
    expect(screen.queryByTestId("untracked-notice")).not.toBeInTheDocument();
    expect(trackedSection).not.toHaveTextContent(/not currently tracked/i);
    // Honestly surfaces which watchlists it IS on, since it's not held.
    expect(trackedSection).toHaveTextContent("energy");
    expect(trackedSection).toHaveTextContent("dividend");
    expect(trackedSection).toHaveTextContent(/In watchlists: energy, dividend/i);
  });

  it("three tracking states (held, watchlist-only, untracked) each render mutually exclusive, honest text", async () => {
    const { unmount: unmountHeld } = render(
      <ExplainTickerProvider>
        <ExplainTickerDrawer symbol="AAPL" isOpen={true} onClose={vi.fn()} />
      </ExplainTickerProvider>
    );
    const heldSection = await screen.findByTestId("why-tracked-section");
    const heldText = heldSection.textContent || "";
    unmountHeld();

    const { unmount: unmountWatchlistOnly } = render(
      <ExplainTickerProvider>
        <ExplainTickerDrawer symbol="XOM" isOpen={true} onClose={vi.fn()} />
      </ExplainTickerProvider>
    );
    const watchlistOnlySection = await screen.findByTestId("why-tracked-section");
    const watchlistOnlyText = watchlistOnlySection.textContent || "";
    unmountWatchlistOnly();

    const { unmount: unmountUntracked } = render(
      <ExplainTickerProvider>
        <ExplainTickerDrawer symbol="XYZ" isOpen={true} onClose={vi.fn()} />
      </ExplainTickerProvider>
    );
    const untrackedSection = await screen.findByTestId("why-tracked-section");
    const untrackedText = untrackedSection.textContent || "";
    unmountUntracked();

    // All three states must render genuinely different copy -- never two of
    // the three collapsing onto the same rendered text.
    expect(heldText).not.toBe(watchlistOnlyText);
    expect(heldText).not.toBe(untrackedText);
    expect(watchlistOnlyText).not.toBe(untrackedText);

    expect(heldText).toMatch(/Held in Portfolio/i);
    expect(watchlistOnlyText).toMatch(/Not Held in Portfolio/i);
    expect(watchlistOnlyText).not.toMatch(/not currently tracked/i);
    expect(untrackedText).toMatch(/Not currently tracked in portfolio or watchlists/i);
    expect(untrackedText).not.toMatch(/Held in Portfolio|Not Held in Portfolio/i);
  });

  it("calls onClose when the close button is clicked", async () => {
    const onClose = vi.fn();
    render(
      <ExplainTickerProvider>
        <ExplainTickerDrawer symbol="AAPL" isOpen={true} onClose={onClose} />
      </ExplainTickerProvider>
    );

    const closeBtn = await screen.findByTestId("explain-drawer-close");
    await userEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when Escape key is pressed", async () => {
    const onClose = vi.fn();
    render(
      <ExplainTickerProvider>
        <ExplainTickerDrawer symbol="AAPL" isOpen={true} onClose={onClose} />
      </ExplainTickerProvider>
    );

    await screen.findByTestId("explain-ticker-drawer");
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("ExplainTickerButton & Context Integration", () => {
  it("clicking ExplainTickerButton stops propagation and opens drawer via context", async () => {
    const outerClick = vi.fn();

    const Consumer = () => {
      const { symbol, isOpen } = useExplainTicker();
      return (
        <div onClick={outerClick}>
          <ExplainTickerButton symbol="MSFT" />
          <div data-testid="context-status">{isOpen ? `Open for ${symbol}` : "Closed"}</div>
        </div>
      );
    };

    render(
      <ExplainTickerProvider>
        <Consumer />
      </ExplainTickerProvider>
    );

    expect(screen.getByTestId("context-status")).toHaveTextContent("Closed");

    const btn = screen.getByTestId("explain-ticker-btn-MSFT");
    expect(btn).toBeInTheDocument();

    await userEvent.click(btn);

    expect(outerClick).not.toHaveBeenCalled(); // stopPropagation verified!
    expect(screen.getByTestId("context-status")).toHaveTextContent("Open for MSFT");
  });
});

describe("buildGapAwareSeries (bars-gap honesty, pure function)", () => {
  const bar = (date: string, close: number | null = 100): Bar => ({
    date,
    Open: close,
    High: close,
    Low: close,
    Close: close,
    Volume: 1_000_000,
  });

  it("inserts no gap marker for a normal consecutive daily series", () => {
    const bars = [bar("2026-01-01"), bar("2026-01-02"), bar("2026-01-03")];
    const { series, gapCount } = buildGapAwareSeries(bars);
    expect(gapCount).toBe(0);
    expect(series).toHaveLength(3);
    expect(series.every((p) => p.Close != null)).toBe(true);
  });

  it("tolerates an ordinary long-weekend/holiday gap (<= 5 calendar days) without flagging it", () => {
    // Thu -> Tue across a 3-day weekend plus one holiday = 5 calendar days.
    const bars = [bar("2026-01-01"), bar("2026-01-06")];
    const { gapCount } = buildGapAwareSeries(bars);
    expect(gapCount).toBe(0);
  });

  it("never draws a fabricated straight line across a genuine multi-week bars gap -- inserts a null marker instead", () => {
    const bars = [bar("2026-01-01", 100), bar("2026-01-02", 101), bar("2026-02-01", 150)];
    const { series, gapCount } = buildGapAwareSeries(bars);

    expect(gapCount).toBe(1);
    // Real bars are untouched, in order, values preserved.
    expect(series[0]).toMatchObject({ date: "2026-01-01", Close: 100 });
    expect(series[1]).toMatchObject({ date: "2026-01-02", Close: 101 });
    // A synthetic null marker is inserted BETWEEN the two real, far-apart
    // points -- this, combined with connectNulls={false} on the chart, is
    // what breaks the line instead of interpolating across the gap.
    const marker = series[2];
    expect(marker.Close).toBeNull();
    expect(marker.isGapMarker).toBe(true);
    expect(series[3]).toMatchObject({ date: "2026-02-01", Close: 150 });
    expect(series).toHaveLength(4);
  });

  it("flags every gap when a series has more than one", () => {
    const bars = [bar("2026-01-01"), bar("2026-02-01"), bar("2026-03-15")];
    const { gapCount, series } = buildGapAwareSeries(bars);
    expect(gapCount).toBe(2);
    // 3 real bars + 2 synthetic gap markers.
    expect(series).toHaveLength(5);
    expect(series.filter((p) => p.isGapMarker)).toHaveLength(2);
  });

  it("sorts out-of-order bars before detecting gaps", () => {
    const bars = [bar("2026-02-01", 150), bar("2026-01-01", 100), bar("2026-01-02", 101)];
    const { series, gapCount } = buildGapAwareSeries(bars);
    expect(gapCount).toBe(1);
    expect(series[0].date).toBe("2026-01-01");
    expect(series[1].date).toBe("2026-01-02");
    expect(series[series.length - 1].date).toBe("2026-02-01");
  });

  it("degrades honestly (no crash, no fabricated markers) on empty, null, or malformed input", () => {
    expect(buildGapAwareSeries([])).toEqual({ series: [], gapCount: 0 });
    expect(buildGapAwareSeries(null)).toEqual({ series: [], gapCount: 0 });
    expect(buildGapAwareSeries(undefined)).toEqual({ series: [], gapCount: 0 });

    const malformed = [bar(""), bar("not-a-date"), bar("2026-01-01")];
    const { series, gapCount } = buildGapAwareSeries(malformed);
    // Entries with no date are dropped rather than crashing or being
    // silently plotted at a fabricated position; an unparsable date is kept
    // (it's a real bar) but never treated as a computed gap boundary.
    expect(series.some((p) => p.date === "")).toBe(false);
    expect(gapCount).toBe(0);
    expect(series.map((p) => p.date)).toContain("2026-01-01");
  });
});
