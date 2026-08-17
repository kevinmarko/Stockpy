import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  LobDepth3D,
  calculateCumulativeDepth,
  calculateMicrostructureMetrics,
  calculateQueuePriority,
  generateMockLobData,
  simulateIncomingOrder,
  checkWebGLSupport,
  disposeThreeScene,
  disposeThreeMesh,
  disposeThreeGeometry,
  disposeThreeMaterial,
  disposeThreeTexture,
  disposeWebGLRenderer,
  disposeCanvas,
} from "./LobDepth3D";
import { OrderBookLevel } from "../../api/types";

// Setup Canvas 2D mock context for clean test execution
function createMockCanvasContext() {
  return {
    fillRect: vi.fn(),
    clearRect: vi.fn(),
    getImageData: vi.fn(() => ({ data: [] })),
    putImageData: vi.fn(),
    createImageData: vi.fn(() => []),
    setTransform: vi.fn(),
    drawImage: vi.fn(),
    save: vi.fn(),
    fillText: vi.fn(),
    strokeText: vi.fn(),
    restore: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    closePath: vi.fn(),
    stroke: vi.fn(),
    strokeRect: vi.fn(),
    arc: vi.fn(),
    fill: vi.fn(),
    rect: vi.fn(),
    setLineDash: vi.fn(),
    measureText: vi.fn(() => ({ width: 50 })),
    transform: vi.fn(),
    resetTransform: vi.fn(),
  };
}

describe("LobDepth3D Pure Calculations & Helpers", () => {
  let mockCtx: any;

  beforeEach(() => {
    mockCtx = createMockCanvasContext();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation((type: string) => {
      if (type === "2d") return mockCtx;
      if (type === "webgl" || type === "webgl2" || type === "experimental-webgl") {
        return null;
      }
      return null;
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  const sampleBids: OrderBookLevel[] = [
    { price: 449.95, size: 1000, type: "bid" },
    { price: 449.90, size: 1500, type: "bid" },
    { price: 449.85, size: 2500, type: "bid" },
  ];

  const sampleAsks: OrderBookLevel[] = [
    { price: 450.05, size: 800, type: "ask" },
    { price: 450.10, size: 1200, type: "ask" },
    { price: 450.15, size: 2000, type: "ask" },
  ];

  describe("calculateCumulativeDepth", () => {
    it("correctly computes cumulative depth and percentages for bids", () => {
      const result = calculateCumulativeDepth(sampleBids, "bid");
      expect(result.totalDepth).toBe(5000);
      expect(result.levelsWithCumulative).toHaveLength(3);

      // Best bid should be first (highest price)
      expect(result.levelsWithCumulative[0].price).toBe(449.95);
      expect(result.levelsWithCumulative[0].cumulative).toBe(1000);
      expect(result.levelsWithCumulative[0].depthPercent).toBeCloseTo(20);

      expect(result.levelsWithCumulative[1].cumulative).toBe(2500);
      expect(result.levelsWithCumulative[1].depthPercent).toBeCloseTo(50);

      expect(result.levelsWithCumulative[2].cumulative).toBe(5000);
      expect(result.levelsWithCumulative[2].depthPercent).toBe(100);
    });

    it("correctly computes cumulative depth and percentages for asks", () => {
      const result = calculateCumulativeDepth(sampleAsks, "ask");
      expect(result.totalDepth).toBe(4000);
      expect(result.levelsWithCumulative).toHaveLength(3);

      // Best ask should be first (lowest price)
      expect(result.levelsWithCumulative[0].price).toBe(450.05);
      expect(result.levelsWithCumulative[0].cumulative).toBe(800);
      expect(result.levelsWithCumulative[0].depthPercent).toBeCloseTo(20);

      expect(result.levelsWithCumulative[1].cumulative).toBe(2000);
      expect(result.levelsWithCumulative[1].depthPercent).toBeCloseTo(50);

      expect(result.levelsWithCumulative[2].cumulative).toBe(4000);
      expect(result.levelsWithCumulative[2].depthPercent).toBe(100);
    });

    it("handles empty levels gracefully", () => {
      const result = calculateCumulativeDepth([], "bid");
      expect(result.totalDepth).toBe(0);
      expect(result.levelsWithCumulative).toEqual([]);
    });
  });

  describe("calculateMicrostructureMetrics", () => {
    it("computes spread, midPrice, microPrice, and order book imbalance (OBI)", () => {
      const metrics = calculateMicrostructureMetrics(sampleBids, sampleAsks);

      expect(metrics.bestBid).toBe(449.95);
      expect(metrics.bestAsk).toBe(450.05);
      expect(metrics.spread).toBeCloseTo(0.10);
      expect(metrics.midPrice).toBeCloseTo(450.00);

      // Total bid depth = 5000, Total ask depth = 4000, Total depth = 9000
      expect(metrics.totalBidDepth).toBe(5000);
      expect(metrics.totalAskDepth).toBe(4000);
      expect(metrics.totalDepth).toBe(9000);

      // Imbalance = (5000 - 4000) / 9000 = 1000 / 9000 = +0.1111 (+11.11%)
      expect(metrics.imbalance).toBeCloseTo(0.1111, 3);
      expect(metrics.imbalancePct).toBeCloseTo(11.11, 1);

      // Microprice weighted towards ask because bid depth is heavier:
      // (449.95 * 4000 + 450.05 * 5000) / 9000 = 450.0055
      expect(metrics.microPrice).toBeCloseTo(450.0055, 3);
      expect(metrics.spreadBps).toBeCloseTo((0.10 / 450.00) * 10000, 1);
    });

    it("handles ask-heavy order book (negative imbalance)", () => {
      const askHeavyAsks: OrderBookLevel[] = [
        { price: 450.05, size: 5000, type: "ask" },
      ];
      const lightBids: OrderBookLevel[] = [
        { price: 449.95, size: 1000, type: "bid" },
      ];

      const metrics = calculateMicrostructureMetrics(lightBids, askHeavyAsks);
      expect(metrics.imbalance).toBeCloseTo(-0.6666, 3);
      expect(metrics.imbalancePct).toBeCloseTo(-66.67, 1);
    });

    it("handles single-sided and empty books safely", () => {
      const emptyMetrics = calculateMicrostructureMetrics([], []);
      expect(emptyMetrics.spread).toBe(0);
      expect(emptyMetrics.imbalance).toBe(0);
      expect(emptyMetrics.totalBidDepth).toBe(0);
      expect(emptyMetrics.totalAskDepth).toBe(0);

      const bidOnlyMetrics = calculateMicrostructureMetrics(sampleBids, []);
      expect(bidOnlyMetrics.bestBid).toBe(449.95);
      expect(bidOnlyMetrics.bestAsk).toBeNull();
      expect(bidOnlyMetrics.imbalance).toBe(1);
    });
  });

  describe("calculateQueuePriority", () => {
    it("computes HIGH priority for top-of-book levels with minimal depth ahead", () => {
      const queue = calculateQueuePriority(sampleBids, 449.95, 100, "bid");
      expect(queue.targetPrice).toBe(449.95);
      expect(queue.side).toBe("bid");
      expect(queue.myOrderSize).toBe(100);
      expect(queue.depthAhead).toBe(1000); // 1000 shares ahead at best bid
      expect(queue.priorityRating).toBe("HIGH");
      expect(queue.estimatedFillProbability).toBeGreaterThanOrEqual(70);
    });

    it("computes MEDIUM or LOW priority for deep price levels", () => {
      const queue = calculateQueuePriority(sampleBids, 449.85, 100, "bid");
      expect(queue.targetPrice).toBe(449.85);
      expect(queue.depthAhead).toBe(5000); // 1000 + 1500 + 2500 ahead
      expect(queue.estimatedFillProbability).toBeLessThan(70);
      expect(["MEDIUM", "LOW"]).toContain(queue.priorityRating);
    });

    it("calculates estimated time to fill proportionally", () => {
      const queue1 = calculateQueuePriority(sampleBids, 449.95, 100, "bid");
      const queue2 = calculateQueuePriority(sampleBids, 449.85, 100, "bid");
      expect(queue2.estimatedTimeToFillSec).toBeGreaterThan(queue1.estimatedTimeToFillSec);
    });
  });

  describe("generateMockLobData & simulateIncomingOrder", () => {
    it("generates structured mock LOB data with balanced bids and asks", () => {
      const data = generateMockLobData("AAPL", 150.0, 6);
      expect(data.bids).toHaveLength(6);
      expect(data.asks).toHaveLength(6);
      expect(data.current_price).toBe(150.0);

      // Verify bid prices are strictly below basePrice and descending
      expect(data.bids[0].price).toBe(149.95);
      expect(data.bids[5].price).toBe(149.70);

      // Verify ask prices are strictly above basePrice and ascending
      expect(data.asks[0].price).toBe(150.05);
      expect(data.asks[5].price).toBe(150.30);
    });

    it("simulates incoming order events with valid structure", () => {
      const order = simulateIncomingOrder(sampleBids, sampleAsks, 450);
      expect(order.id).toBeDefined();
      expect(order.timestamp).toBeGreaterThan(0);
      expect(["buy", "sell"]).toContain(order.side);
      expect(["market", "limit"]).toContain(order.type);
      expect(["placed", "filled"]).toContain(order.status);
      expect(order.size).toBeGreaterThan(0);
    });
  });

  describe("checkWebGLSupport", () => {
    it("returns a boolean indicating WebGL support status", () => {
      const supported = checkWebGLSupport();
      expect(typeof supported).toBe("boolean");
    });
  });
});

describe("LobDepth3D Component Rendering & Interactions", () => {
  let mockCtx: any;

  beforeEach(() => {
    mockCtx = createMockCanvasContext();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation((type: string) => {
      if (type === "2d") return mockCtx;
      if (type === "webgl" || type === "webgl2" || type === "experimental-webgl") {
        return null; // Simulate WebGL fallback mode in headless test
      }
      return null;
    });

    // Stub requestAnimationFrame / cancelAnimationFrame for vitest jsdom
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      return setTimeout(() => cb(performance.now()), 16) as unknown as number;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
      clearTimeout(id);
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders the 3D LOB container, title, symbol, and metrics cards", () => {
    render(<LobDepth3D symbol="SPY" autoPlayWaterfall={false} />);

    expect(screen.getByTestId("lob-depth-3d-container")).toBeInTheDocument();
    expect(
      screen.getByText("3D Limit Order Book & Order Flow Waterfall")
    ).toBeInTheDocument();
    expect(screen.getByText("SPY")).toBeInTheDocument();

    // Verify Metrics Cards
    expect(screen.getByTestId("metric-spread")).toBeInTheDocument();
    expect(screen.getByTestId("metric-bid-depth")).toBeInTheDocument();
    expect(screen.getByTestId("metric-ask-depth")).toBeInTheDocument();
    expect(screen.getByTestId("metric-imbalance")).toBeInTheDocument();
  });

  it("renders fallback mode gracefully in jsdom environment when WebGL is unavailable", () => {
    render(<LobDepth3D symbol="TSLA" forceFallback={true} autoPlayWaterfall={false} />);

    const renderMode = screen.getByTestId("lob-render-mode");
    expect(renderMode).toHaveTextContent("Canvas 2.5D Fallback Mode");
    expect(screen.getByTestId("lob-fallback-view")).toBeInTheDocument();
    expect(
      screen.getByText("Rendering 2.5D Isometric Towers & Order Stream")
    ).toBeInTheDocument();
  });

  it("renders Canvas 2.5D Renderer badge when WebGL is supported", () => {
    // Mock WebGL context presence
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation((type: string) => {
      if (type === "webgl" || type === "webgl2" || type === "experimental-webgl") {
        return { getParameter: vi.fn(() => "WebGL 2.0") } as any;
      }
      return mockCtx;
    });

    render(<LobDepth3D symbol="NVDA" forceFallback={false} autoPlayWaterfall={false} />);

    const renderMode = screen.getByTestId("lob-render-mode");
    expect(renderMode).toHaveTextContent("Canvas 2.5D Renderer");
  });

  it("renders with custom bid/ask levels and calculates metrics correctly in DOM", () => {
    const customBids: OrderBookLevel[] = [
      { price: 100.0, size: 2500, type: "bid" },
    ];
    const customAsks: OrderBookLevel[] = [
      { price: 100.1, size: 1500, type: "ask" },
    ];

    render(
      <LobDepth3D
        symbol="XYZ"
        bids={customBids}
        asks={customAsks}
        currentPrice={100.05}
        autoPlayWaterfall={false}
      />
    );

    const spreadCard = screen.getByTestId("metric-spread");
    expect(spreadCard).toHaveTextContent("$0.10");

    const bidDepthCard = screen.getByTestId("metric-bid-depth");
    expect(bidDepthCard).toHaveTextContent("2,500");

    const askDepthCard = screen.getByTestId("metric-ask-depth");
    expect(askDepthCard).toHaveTextContent("1,500");

    const imbalanceCard = screen.getByTestId("metric-imbalance");
    // (2500 - 1500) / 4000 = +25.0%
    expect(imbalanceCard).toHaveTextContent("+25.0%");
  });

  it("toggles Play and Pause for the order flow waterfall animation", () => {
    render(<LobDepth3D symbol="SPY" autoPlayWaterfall={false} />);

    const playPauseBtn = screen.getByTestId("waterfall-play-pause-btn");
    expect(playPauseBtn).toHaveTextContent("Play");

    fireEvent.click(playPauseBtn);
    expect(playPauseBtn).toHaveTextContent("Pause");

    fireEvent.click(playPauseBtn);
    expect(playPauseBtn).toHaveTextContent("Play");
  });

  it("changes flow speed multiplier when speed buttons are clicked", () => {
    render(<LobDepth3D symbol="SPY" initialFlowSpeed={1} autoPlayWaterfall={false} />);

    const speed2xBtn = screen.getByTestId("speed-btn-2");
    fireEvent.click(speed2xBtn);
    expect(speed2xBtn).toHaveStyle({ background: "var(--accent)" });

    const speed5xBtn = screen.getByTestId("speed-btn-5");
    fireEvent.click(speed5xBtn);
    expect(speed5xBtn).toHaveStyle({ background: "var(--accent)" });
  });

  it("switches order type filter between All, Market, and Limit", () => {
    render(<LobDepth3D symbol="SPY" autoPlayWaterfall={false} />);

    const marketFilterBtn = screen.getByTestId("filter-btn-market");
    fireEvent.click(marketFilterBtn);
    expect(marketFilterBtn).toHaveTextContent("market Only");

    const limitFilterBtn = screen.getByTestId("filter-btn-limit");
    fireEvent.click(limitFilterBtn);
    expect(limitFilterBtn).toHaveTextContent("limit Only");

    const allFilterBtn = screen.getByTestId("filter-btn-all");
    fireEvent.click(allFilterBtn);
    expect(allFilterBtn).toHaveTextContent("All Orders");
  });

  it("injects orders into the simulated tape using quick action buttons", async () => {
    render(<LobDepth3D symbol="SPY" autoPlayWaterfall={false} />);

    const injectMktBuyBtn = screen.getByTestId("inject-market-buy-btn");
    fireEvent.click(injectMktBuyBtn);

    const tapeFeed = screen.getByTestId("lob-tape-feed");
    expect(tapeFeed).toHaveTextContent("BUY MARKET");

    const injectMktSellBtn = screen.getByTestId("inject-market-sell-btn");
    fireEvent.click(injectMktSellBtn);
    expect(tapeFeed).toHaveTextContent("SELL MARKET");

    const injectLmtBidBtn = screen.getByTestId("inject-limit-bid-btn");
    fireEvent.click(injectLmtBidBtn);
    expect(tapeFeed).toHaveTextContent("BUY LIMIT");

    const injectLmtAskBtn = screen.getByTestId("inject-limit-ask-btn");
    fireEvent.click(injectLmtAskBtn);
    expect(tapeFeed).toHaveTextContent("SELL LIMIT");
  });

  it("injects custom order via custom order injection form", async () => {
    render(<LobDepth3D symbol="SPY" autoPlayWaterfall={false} />);

    const sideSelect = screen.getByLabelText("Side");
    const typeSelect = screen.getByLabelText("Type");
    const priceInput = screen.getByLabelText("Price");
    const sizeInput = screen.getByLabelText("Size");

    fireEvent.change(sideSelect, { target: { value: "buy" } });
    fireEvent.change(typeSelect, { target: { value: "limit" } });
    fireEvent.change(priceInput, { target: { value: "448.50" } });
    fireEvent.change(sizeInput, { target: { value: "500" } });

    const injectBtn = screen.getByTestId("inject-custom-btn");
    fireEvent.click(injectBtn);

    const tapeFeed = screen.getByTestId("lob-tape-feed");
    expect(tapeFeed).toHaveTextContent("BUY LIMIT");
    expect(tapeFeed).toHaveTextContent("500 @ $448.50");
  });

  it("updates Queue Priority section dynamically when inputs change", () => {
    render(<LobDepth3D symbol="SPY" autoPlayWaterfall={false} />);

    const queuePanel = screen.getByTestId("queue-priority-panel");
    expect(queuePanel).toBeInTheDocument();

    const depthAheadEl = screen.getByTestId("queue-depth-ahead");
    expect(depthAheadEl).toBeInTheDocument();

    const ratingEl = screen.getByTestId("queue-priority-rating");
    expect(ratingEl).toBeInTheDocument();

    const fillProbEl = screen.getByTestId("queue-fill-prob");
    expect(fillProbEl).toHaveTextContent("% Fill Prob");

    // Change order size
    const sizeInput = screen.getByLabelText("My Order Size");
    fireEvent.change(sizeInput, { target: { value: "500" } });
    expect(sizeInput).toHaveValue(500);

    // Switch side to Ask
    const sideSelect = screen.getByLabelText("Order Side");
    fireEvent.change(sideSelect, { target: { value: "ask" } });
    expect(sideSelect).toHaveValue("ask");
  });

  it("switches camera presets (Isometric, Front, Top-Down, Side)", () => {
    render(<LobDepth3D symbol="SPY" autoPlayWaterfall={false} />);

    const frontBtn = screen.getByTestId("preset-front-btn");
    fireEvent.click(frontBtn);

    const topBtn = screen.getByTestId("preset-top-btn");
    fireEvent.click(topBtn);

    const sideBtn = screen.getByTestId("preset-side-btn");
    fireEvent.click(sideBtn);

    const isoBtn = screen.getByTestId("preset-iso-btn");
    fireEvent.click(isoBtn);
  });

  it("adjusts Yaw and Pitch sliders", () => {
    render(<LobDepth3D symbol="SPY" autoPlayWaterfall={false} />);

    const yawSlider = screen.getByTestId("slider-yaw");
    fireEvent.change(yawSlider, { target: { value: "45" } });
    expect(yawSlider).toHaveValue("45");

    const pitchSlider = screen.getByTestId("slider-pitch");
    fireEvent.change(pitchSlider, { target: { value: "50" } });
    expect(pitchSlider).toHaveValue("50");
  });

  it("resets simulation when Reset button is clicked", () => {
    render(<LobDepth3D symbol="SPY" autoPlayWaterfall={false} />);

    // Inject an order
    const injectMktBuyBtn = screen.getByTestId("inject-market-buy-btn");
    fireEvent.click(injectMktBuyBtn);
    expect(screen.getByTestId("lob-tape-feed")).toHaveTextContent("BUY MARKET");

    // Click Reset
    const resetBtn = screen.getByTestId("lob-reset-btn");
    fireEvent.click(resetBtn);

    expect(screen.getByTestId("lob-tape-feed")).toHaveTextContent(
      "Awaiting order arrivals..."
    );
  });

  it("handles canvas click to select price level", () => {
    render(<LobDepth3D symbol="SPY" autoPlayWaterfall={false} />);

    const canvas = screen.getByTestId("lob-depth-canvas");
    // Mock getBoundingClientRect
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      left: 0,
      top: 0,
      width: 800,
      height: 520,
      right: 800,
      bottom: 520,
      x: 0,
      y: 0,
      toJSON: () => {},
    });

    // Click on bid side (left half)
    fireEvent.click(canvas, { clientX: 200, clientY: 300 });

    // Click on ask side (right half)
    fireEvent.click(canvas, { clientX: 600, clientY: 300 });

    expect(screen.getByTestId("queue-priority-panel")).toBeInTheDocument();
  });

  it("verifies unmount cancels animation frame, removes window listeners, and zeroes out canvas dimensions", () => {
    const cancelAnimSpy = vi.spyOn(window, "cancelAnimationFrame");
    const removeEventListenerSpy = vi.spyOn(window, "removeEventListener");

    const { unmount } = render(
      <LobDepth3D symbol="SPY" autoPlayWaterfall={true} />
    );

    const canvas = screen.getByTestId("lob-depth-canvas") as HTMLCanvasElement;
    expect(canvas).toBeInTheDocument();

    unmount();

    // Verify cancelAnimationFrame was called
    expect(cancelAnimSpy).toHaveBeenCalled();

    // Verify window event listeners were cleaned up
    expect(removeEventListenerSpy).toHaveBeenCalledWith("mouseup", expect.any(Function));
    expect(removeEventListenerSpy).toHaveBeenCalledWith("touchend", expect.any(Function));
    expect(removeEventListenerSpy).toHaveBeenCalledWith("resize", expect.any(Function));

    // Verify canvas backbuffers were zeroed out
    expect(canvas.width).toBe(0);
    expect(canvas.height).toBe(0);
  });

  describe("LobDepth3D Three.js & WebGL Disposal Export Suite", () => {
    it("exports valid disposal routines that execute cleanly", () => {
      expect(typeof disposeThreeScene).toBe("function");
      expect(typeof disposeThreeMesh).toBe("function");
      expect(typeof disposeThreeGeometry).toBe("function");
      expect(typeof disposeThreeMaterial).toBe("function");
      expect(typeof disposeThreeTexture).toBe("function");
      expect(typeof disposeWebGLRenderer).toBe("function");
      expect(typeof disposeCanvas).toBe("function");

      const mockMesh = {
        dispose: vi.fn(),
        geometry: { dispose: vi.fn() },
        material: { dispose: vi.fn(), map: { dispose: vi.fn() } },
      };

      disposeThreeMesh(mockMesh);
      expect(mockMesh.geometry).toBeNull();
      expect(mockMesh.material).toBeNull();

      const canvas = document.createElement("canvas");
      canvas.width = 400;
      canvas.height = 300;
      disposeCanvas(canvas);
      expect(canvas.width).toBe(0);
      expect(canvas.height).toBe(0);
    });
  });
});

