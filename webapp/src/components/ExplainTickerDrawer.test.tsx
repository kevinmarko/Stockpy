import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, afterEach } from "vitest";
import { ExplainTickerDrawer } from "./ExplainTickerDrawer";
import { ExplainTickerButton } from "./ExplainTickerButton";
import { ExplainTickerProvider, useExplainTicker } from "../context/ExplainTickerContext";

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
    expect(screen.getByText("64.20")).toBeInTheDocument(); // RSI

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
