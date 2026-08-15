import React, { useState, useEffect, useRef, useMemo, useCallback } from "react";
import {
  Box,
  Play,
  Pause,
  RotateCcw,
  Zap,
  ShieldCheck,
  Plus,
} from "lucide-react";
import { OrderBookLevel } from "../../api/types";
import { theme } from "../../theme";
import DemoDataBadge from "../DemoDataBadge";
import { Button, Input, Select } from "../ui";
import {
  disposeThreeScene,
  disposeThreeMesh,
  disposeThreeGeometry,
  disposeThreeMaterial,
  disposeThreeTexture,
  disposeWebGLRenderer,
  disposeCanvas,
} from "./threeDisposal";

export {
  disposeThreeScene,
  disposeThreeMesh,
  disposeThreeGeometry,
  disposeThreeMaterial,
  disposeThreeTexture,
  disposeWebGLRenderer,
  disposeCanvas,
};

// ============================================================================
// Types & Interfaces
// ============================================================================

export interface CumulativeOrderBookLevel extends OrderBookLevel {
  cumulative: number;
  depthPercent: number;
}

export interface OrderFlowEvent {
  id: string;
  timestamp: number;
  side: "buy" | "sell";
  type: "market" | "limit";
  price: number;
  size: number;
  status: "placed" | "filled" | "resting";
  zProgress?: number; // 0 (incoming waterfall top) to 1 (landed on book)
  splashAge?: number; // frame age after landing
}

export interface MicrostructureMetrics {
  spread: number;
  spreadBps: number;
  bestBid: number | null;
  bestAsk: number | null;
  midPrice: number | null;
  microPrice: number | null;
  totalBidDepth: number;
  totalAskDepth: number;
  totalDepth: number;
  imbalance: number; // between -1.0 and +1.0
  imbalancePct: number; // between -100% and +100%
  bidAskRatio: number;
}

export interface QueuePriorityResult {
  targetPrice: number;
  side: "bid" | "ask";
  myOrderSize: number;
  depthAhead: number;
  totalAtLevel: number;
  queuePositionRatio: number;
  estimatedFillProbability: number;
  priorityRating: "HIGH" | "MEDIUM" | "LOW";
  estimatedTimeToFillSec: number;
}

export interface LobDepth3DProps {
  symbol?: string;
  bids?: OrderBookLevel[];
  asks?: OrderBookLevel[];
  currentPrice?: number | null;
  isSynthetic?: boolean;
  forceFallback?: boolean;
  height?: number | string;
  className?: string;
  autoPlayWaterfall?: boolean;
  initialFlowSpeed?: number;
}

// ============================================================================
// Pure Calculation Helpers (Exported for direct unit testing)
// ============================================================================

/**
 * Checks if WebGL is available in the current runtime environment.
 */
export function checkWebGLSupport(): boolean {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return false;
  }
  try {
    const canvas = document.createElement("canvas");
    if (!canvas || typeof canvas.getContext !== "function") {
      return false;
    }
    const gl =
      canvas.getContext("webgl2") ||
      canvas.getContext("webgl") ||
      canvas.getContext("experimental-webgl");
    return !!(gl && typeof (gl as any).getParameter === "function");
  } catch {
    return false;
  }
}

/**
 * Calculates cumulative depth and percentages for a side of the order book.
 */
export function calculateCumulativeDepth(
  levels: OrderBookLevel[],
  side: "bid" | "ask"
): {
  levelsWithCumulative: CumulativeOrderBookLevel[];
  totalDepth: number;
} {
  if (!levels || levels.length === 0) {
    return { levelsWithCumulative: [], totalDepth: 0 };
  }

  // Bids sort descending by price, Asks sort ascending by price
  const sorted = [...levels].sort((a, b) =>
    side === "bid" ? b.price - a.price : a.price - b.price
  );

  let runningCumulative = 0;
  const withCum = sorted.map((level) => {
    runningCumulative += level.size;
    return {
      ...level,
      cumulative: runningCumulative,
      depthPercent: 0,
    };
  });

  const totalDepth = runningCumulative;
  const levelsWithCumulative = withCum.map((lvl) => ({
    ...lvl,
    depthPercent: totalDepth > 0 ? (lvl.cumulative / totalDepth) * 100 : 0,
  }));

  return { levelsWithCumulative, totalDepth };
}

/**
 * Computes essential microstructure metrics:
 * - Spread ($ and basis points)
 * - Cumulative Bid vs Ask Depth
 * - Microstructure Imbalance (Order Book Imbalance / OBI)
 * - Microprice (Volume-weighted midprice)
 */
export function calculateMicrostructureMetrics(
  bids: OrderBookLevel[] = [],
  asks: OrderBookLevel[] = [],
  fallbackPrice: number | null = null
): MicrostructureMetrics {
  const { totalDepth: totalBidDepth } = calculateCumulativeDepth(bids, "bid");
  const { totalDepth: totalAskDepth } = calculateCumulativeDepth(asks, "ask");
  const totalDepth = totalBidDepth + totalAskDepth;

  const validBids = bids.filter((b) => b && typeof b.price === "number" && b.size > 0);
  const validAsks = asks.filter((a) => a && typeof a.price === "number" && a.size > 0);

  const bestBid = validBids.length > 0 ? Math.max(...validBids.map((b) => b.price)) : null;
  const bestAsk = validAsks.length > 0 ? Math.min(...validAsks.map((a) => a.price)) : null;

  let spread = 0;
  let spreadBps = 0;
  let midPrice: number | null = null;
  let microPrice: number | null = null;

  if (bestBid !== null && bestAsk !== null) {
    spread = Math.max(0, bestAsk - bestBid);
    midPrice = (bestBid + bestAsk) / 2;
    if (midPrice > 0) {
      spreadBps = (spread / midPrice) * 10000;
    }

    // Microprice: P_micro = (P_bid * V_ask_L1 + P_ask * V_bid_L1) / (V_bid_L1 + V_ask_L1)
    const bestBidLevel = validBids.find((b) => b.price === bestBid);
    const bestAskLevel = validAsks.find((a) => a.price === bestAsk);
    const bestBidVol = bestBidLevel ? bestBidLevel.size : 0;
    const bestAskVol = bestAskLevel ? bestAskLevel.size : 0;
    const topDepth = bestBidVol + bestAskVol;
    if (topDepth > 0) {
      microPrice = (bestBid * bestAskVol + bestAsk * bestBidVol) / topDepth;
    } else if (totalDepth > 0) {
      microPrice =
        (bestBid * totalAskDepth + bestAsk * totalBidDepth) / totalDepth;
    } else {
      microPrice = midPrice;
    }
  } else if (bestBid !== null) {
    midPrice = bestBid;
    microPrice = bestBid;
  } else if (bestAsk !== null) {
    midPrice = bestAsk;
    microPrice = bestAsk;
  } else if (fallbackPrice !== null) {
    midPrice = fallbackPrice;
    microPrice = fallbackPrice;
  }

  // Order Book Imbalance (OBI) = (BidDepth - AskDepth) / (BidDepth + AskDepth)
  let imbalance = 0;
  if (totalDepth > 0) {
    imbalance = (totalBidDepth - totalAskDepth) / totalDepth;
  }
  const imbalancePct = imbalance * 100;
  const bidAskRatio = totalAskDepth > 0 ? totalBidDepth / totalAskDepth : totalBidDepth > 0 ? 1 : 0;

  return {
    spread,
    spreadBps,
    bestBid,
    bestAsk,
    midPrice,
    microPrice,
    totalBidDepth,
    totalAskDepth,
    totalDepth,
    imbalance,
    imbalancePct,
    bidAskRatio,
  };
}

/**
 * Calculates Queue Priority and estimated fill probability for an order placed at targetPrice.
 */
export function calculateQueuePriority(
  levels: OrderBookLevel[] = [],
  targetPrice: number,
  myOrderSize: number,
  side: "bid" | "ask"
): QueuePriorityResult {
  const size = Math.max(1, myOrderSize || 100);
  const sorted = [...levels].sort((a, b) =>
    side === "bid" ? b.price - a.price : a.price - b.price
  );

  let depthAhead = 0;
  let totalAtLevel = 0;
  let targetIndex = -1;

  for (let i = 0; i < sorted.length; i++) {
    const lvl = sorted[i];
    if (Math.abs(lvl.price - targetPrice) < 0.001) {
      targetIndex = i;
      totalAtLevel = lvl.size;
      // Assuming our order joins at the end of the existing queue at this level
      depthAhead += lvl.size;
      break;
    } else {
      depthAhead += lvl.size;
    }
  }

  // If target price is not currently on the book, place at top/behind
  if (targetIndex === -1) {
    totalAtLevel = size;
  }

  const totalBookDepth = sorted.reduce((sum, lvl) => sum + lvl.size, 0) + size;
  const queuePositionRatio = totalBookDepth > 0 ? depthAhead / totalBookDepth : 0;

  // Fill probability estimation model:
  // Decreases non-linearly with depth ahead and distance from top of book
  const rankDistanceFactor = targetIndex >= 0 ? Math.exp(-0.25 * targetIndex) : 0.5;
  const depthPenalty = Math.max(0, 1 - depthAhead / (totalBookDepth * 1.2 || 1));
  const estimatedFillProbability = Math.min(
    99,
    Math.max(1, Math.round((rankDistanceFactor * 0.6 + depthPenalty * 0.4) * 100))
  );

  let priorityRating: "HIGH" | "MEDIUM" | "LOW" = "LOW";
  if (estimatedFillProbability >= 70) {
    priorityRating = "HIGH";
  } else if (estimatedFillProbability >= 35) {
    priorityRating = "MEDIUM";
  }

  // Estimated time to fill (heuristic: ~100 shares consumed per second at top of book)
  const estimatedTimeToFillSec = Math.max(
    1,
    Math.round((depthAhead + size) / 120)
  );

  return {
    targetPrice,
    side,
    myOrderSize: size,
    depthAhead,
    totalAtLevel,
    queuePositionRatio,
    estimatedFillProbability,
    priorityRating,
    estimatedTimeToFillSec,
  };
}

/**
 * Generates deterministic or stochastic mock LOB data for standalone viewing and testing.
 */
export function generateMockLobData(
  _symbol = "SPY",
  basePrice = 450.0,
  levelsCount = 8
): { bids: OrderBookLevel[]; asks: OrderBookLevel[]; current_price: number } {
  const tick = 0.05;
  const bids: OrderBookLevel[] = [];
  const asks: OrderBookLevel[] = [];

  for (let i = 0; i < levelsCount; i++) {
    const bidPrice = Number((basePrice - (i + 1) * tick).toFixed(2));
    const askPrice = Number((basePrice + (i + 1) * tick).toFixed(2));
    // Organic depth curve tapering or peaking near top
    const bidSize = Math.round(500 + Math.sin(i * 0.8 + 1) * 350 + (levelsCount - i) * 60);
    const askSize = Math.round(480 + Math.cos(i * 0.8 + 0.5) * 320 + (levelsCount - i) * 55);

    bids.push({ price: bidPrice, size: Math.max(100, bidSize), type: "bid" });
    asks.push({ price: askPrice, size: Math.max(100, askSize), type: "ask" });
  }

  return {
    bids,
    asks,
    current_price: basePrice,
  };
}

/**
 * Simulates a single incoming order event for the waterfall stream.
 */
export function simulateIncomingOrder(
  bids: OrderBookLevel[],
  asks: OrderBookLevel[],
  currentPrice = 450
): OrderFlowEvent {
  const isBuy = Math.random() > 0.48;
  const isMarket = Math.random() > 0.35;
  const side: "buy" | "sell" = isBuy ? "buy" : "sell";
  const type: "market" | "limit" = isMarket ? "market" : "limit";

  let price = currentPrice;
  if (type === "market") {
    if (side === "buy" && asks.length > 0) {
      price = asks[0].price;
    } else if (side === "sell" && bids.length > 0) {
      price = bids[0].price;
    }
  } else {
    // Limit order placed near or within book
    const offset = (Math.floor(Math.random() * 5) + 1) * 0.05;
    price = side === "buy" ? Number((currentPrice - offset).toFixed(2)) : Number((currentPrice + offset).toFixed(2));
  }

  const sizes = [50, 100, 200, 350, 500, 1000];
  const size = sizes[Math.floor(Math.random() * sizes.length)];

  return {
    id: `ord-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
    timestamp: Date.now(),
    side,
    type,
    price,
    size,
    status: type === "market" ? "filled" : "placed",
    zProgress: 0,
    splashAge: 0,
  };
}

// ============================================================================
// Main Component
// ============================================================================

export const LobDepth3D: React.FC<LobDepth3DProps> = ({
  symbol = "SPY",
  bids: initialBids,
  asks: initialAsks,
  currentPrice: propCurrentPrice = null,
  isSynthetic = true,
  forceFallback = false,
  height = 520,
  className = "",
  autoPlayWaterfall = true,
  initialFlowSpeed = 1,
}) => {
  // LOB State
  const defaultMock = useMemo(() => generateMockLobData(symbol, propCurrentPrice || 450), [symbol, propCurrentPrice]);
  const bids = initialBids && initialBids.length > 0 ? initialBids : defaultMock.bids;
  const asks = initialAsks && initialAsks.length > 0 ? initialAsks : defaultMock.asks;
  const effectivePrice = propCurrentPrice ?? defaultMock.current_price;

  // WebGL & Fallback state detection
  const [hasWebGL, setHasWebGL] = useState<boolean>(true);
  useEffect(() => {
    setHasWebGL(checkWebGLSupport());
  }, []);

  const isFallbackActive = forceFallback || !hasWebGL;

  // Cumulative depths & metrics
  const metrics = useMemo(
    () => calculateMicrostructureMetrics(bids, asks, effectivePrice),
    [bids, asks, effectivePrice]
  );

  // 3D View Angle & Orbit State
  const [yaw, setYaw] = useState<number>(35); // degrees
  const [pitch, setPitch] = useState<number>(28); // degrees
  const [zoom] = useState<number>(1.0);
  const [activePreset, setActivePreset] = useState<"iso" | "front" | "top" | "side">("iso");

  // Order Flow Waterfall State
  const [isPlaying, setIsPlaying] = useState<boolean>(autoPlayWaterfall);
  const [flowSpeed, setFlowSpeed] = useState<number>(initialFlowSpeed);
  const [orderFilter, setOrderFilter] = useState<"all" | "market" | "limit">("all");
  const [, setWaterfallEvents] = useState<OrderFlowEvent[]>([]);
  const [recentTape, setRecentTape] = useState<OrderFlowEvent[]>([]);

  // Queue Priority Inspector State
  const [myOrderSize, setMyOrderSize] = useState<number>(100);
  const [selectedPrice, setSelectedPrice] = useState<number>(
    metrics.bestBid ?? bids[0]?.price ?? 449.95
  );
  const [selectedSide, setSelectedSide] = useState<"bid" | "ask">("bid");

  // Custom Order Injector Form State
  const [injectSide, setInjectSide] = useState<"buy" | "sell">("buy");
  const [injectType, setInjectType] = useState<"market" | "limit">("market");
  const [injectPrice, setInjectPrice] = useState<string>(
    metrics.bestAsk ? metrics.bestAsk.toFixed(2) : "450.05"
  );
  const [injectSize, setInjectSize] = useState<string>("100");

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animFrameIdRef = useRef<number | null>(null);
  const lastTimeRef = useRef<number>(Date.now());
  const spawnTimerRef = useRef<number>(0);
  const waterfallEventsRef = useRef<OrderFlowEvent[]>([]);

  // State ref to decouple 60fps render loop from React state re-renders
  const lobStateRef = useRef({
    yaw,
    pitch,
    zoom,
    isPlaying,
    flowSpeed,
    orderFilter,
    selectedPrice,
    selectedSide,
    metrics,
    symbol,
    bids,
    asks,
    effectivePrice,
  });

  lobStateRef.current = {
    yaw,
    pitch,
    zoom,
    isPlaying,
    flowSpeed,
    orderFilter,
    selectedPrice,
    selectedSide,
    metrics,
    symbol,
    bids,
    asks,
    effectivePrice,
  };

  // Calculate Queue Priority for inspector
  const queueResult = useMemo(() => {
    const relevantLevels = selectedSide === "bid" ? bids : asks;
    return calculateQueuePriority(relevantLevels, selectedPrice, myOrderSize, selectedSide);
  }, [bids, asks, selectedPrice, myOrderSize, selectedSide]);

  // Handle Preset Angle Switching
  const handlePresetSelect = (preset: "iso" | "front" | "top" | "side") => {
    setActivePreset(preset);
    if (preset === "iso") {
      setYaw(35);
      setPitch(28);
      lobStateRef.current.yaw = 35;
      lobStateRef.current.pitch = 28;
    } else if (preset === "front") {
      setYaw(0);
      setPitch(10);
      lobStateRef.current.yaw = 0;
      lobStateRef.current.pitch = 10;
    } else if (preset === "top") {
      setYaw(0);
      setPitch(85);
      lobStateRef.current.yaw = 0;
      lobStateRef.current.pitch = 85;
    } else if (preset === "side") {
      setYaw(90);
      setPitch(15);
      lobStateRef.current.yaw = 90;
      lobStateRef.current.pitch = 15;
    }
  };

  // Inject Order Helper
  const injectOrder = useCallback(
    (side: "buy" | "sell", type: "market" | "limit", price: number, size: number) => {
      const newEvent: OrderFlowEvent = {
        id: `inject-${Date.now()}-${Math.random().toString(36).substring(2, 6)}`,
        timestamp: Date.now(),
        side,
        type,
        price,
        size,
        status: type === "market" ? "filled" : "placed",
        zProgress: 0,
        splashAge: 0,
      };

      waterfallEventsRef.current = [newEvent, ...waterfallEventsRef.current.slice(0, 39)];
      setWaterfallEvents((prev) => [newEvent, ...prev.slice(0, 39)]);
      setRecentTape((prev) => [newEvent, ...prev.slice(0, 19)]);
    },
    []
  );

  // Handle Manual Order Inject Form Submit
  const handleInjectForm = (e: React.FormEvent) => {
    e.preventDefault();
    const priceNum = parseFloat(injectPrice) || effectivePrice;
    const sizeNum = parseInt(injectSize, 10) || 100;
    injectOrder(injectSide, injectType, priceNum, sizeNum);
  };

  // Reset Simulation
  const handleResetSimulation = () => {
    waterfallEventsRef.current = [];
    setWaterfallEvents([]);
    setRecentTape([]);
    setSelectedPrice(metrics.bestBid ?? bids[0]?.price ?? 449.95);
  };

  // ==========================================================================
  // Canvas 3D & Isometric Rendering Loop
  // ==========================================================================
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let isSubscribed = true;

    const renderLoop = () => {
      if (!isSubscribed) return;

      const now = Date.now();
      const dt = (now - lastTimeRef.current) / 1000;
      lastTimeRef.current = now;

      const {
        yaw: curYaw,
        pitch: curPitch,
        zoom: curZoom,
        isPlaying: curIsPlaying,
        flowSpeed: curFlowSpeed,
        orderFilter: curOrderFilter,
        selectedPrice: curSelectedPrice,
        selectedSide: curSelectedSide,
        metrics: curMetrics,
        symbol: curSymbol,
        bids: curBids,
        asks: curAsks,
        effectivePrice: curEffPrice,
      } = lobStateRef.current;

      // Update waterfall event positions mutably
      if (curIsPlaying) {
        spawnTimerRef.current += dt * curFlowSpeed;
        if (spawnTimerRef.current >= 0.9) {
          spawnTimerRef.current = 0;
          const simulated = simulateIncomingOrder(curBids, curAsks, curEffPrice);
          if (
            curOrderFilter === "all" ||
            (curOrderFilter === "market" && simulated.type === "market") ||
            (curOrderFilter === "limit" && simulated.type === "limit")
          ) {
            waterfallEventsRef.current = [simulated, ...waterfallEventsRef.current.slice(0, 34)];
            setRecentTape((prev) => [simulated, ...prev.slice(0, 19)]);
          }
        }

        // Mutate in place without causing React state re-renders
        waterfallEventsRef.current = waterfallEventsRef.current
          .map((ev) => {
            const currentZ = ev.zProgress ?? 0;
            const nextZ = currentZ + dt * 0.8 * curFlowSpeed;
            const splashAge = nextZ >= 1 ? (ev.splashAge ?? 0) + 1 : 0;
            return {
              ...ev,
              zProgress: Math.min(1.0, nextZ),
              splashAge,
            };
          })
          .filter((ev) => (ev.splashAge ?? 0) < 25);
      }

      // Drawing canvas
      const width = canvas.width;
      const height = canvas.height;

      // Background clear
      ctx.fillStyle = theme.base;
      ctx.fillRect(0, 0, width, height);

      // Grid origin center
      const centerX = width / 2;
      const centerY = height * 0.62;

      // 3D Isometric projection math
      const radYaw = (curYaw * Math.PI) / 180;
      const radPitch = (curPitch * Math.PI) / 180;
      const cosYaw = Math.cos(radYaw);
      const sinYaw = Math.sin(radYaw);
      const cosPitch = Math.cos(radPitch);
      const sinPitch = Math.sin(radPitch);

      const project3D = (x: number, y: number, z: number) => {
        // Rotate around Y axis (yaw)
        const rx = x * cosYaw - z * sinYaw;
        const rz = x * sinYaw + z * cosYaw;
        // Tilt around X axis (pitch)
        const ry = y * cosPitch - rz * sinPitch;
        const finalZ = y * sinPitch + rz * cosPitch;

        const scale = (curZoom * width) / 450;
        const sx = centerX + rx * scale;
        const sy = centerY - ry * scale;
        return { x: sx, y: sy, depth: finalZ };
      };

      // Draw Floor Grid
      ctx.strokeStyle = "rgba(255, 255, 255, 0.07)";
      ctx.lineWidth = 1;

      const gridSize = 160;
      const step = 20;

      for (let gx = -gridSize; gx <= gridSize; gx += step) {
        const p1 = project3D(gx, 0, -gridSize);
        const p2 = project3D(gx, 0, gridSize);
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.stroke();
      }

      for (let gz = -gridSize; gz <= gridSize; gz += step) {
        const p1 = project3D(-gridSize, 0, gz);
        const p2 = project3D(gridSize, 0, gz);
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.stroke();
      }

      // Draw Mid-Market dividing line (glowing cyan)
      const mid1 = project3D(0, 0, -gridSize);
      const mid2 = project3D(0, 0, gridSize);
      ctx.strokeStyle = "rgba(56, 189, 248, 0.6)";
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(mid1.x, mid1.y);
      ctx.lineTo(mid2.x, mid2.y);
      ctx.stroke();
      ctx.setLineDash([]);

      // Maximum depth for tower normalization
      const maxBidSize = Math.max(1, ...bids.map((b) => b.size));
      const maxAskSize = Math.max(1, ...asks.map((a) => a.size));
      const maxDepth = Math.max(maxBidSize, maxAskSize, 1000);
      const towerWidth = 14;
      const towerDepth = 18;

      // Draw 3D Tower Helper
      const draw3DTower = (
        xCenter: number,
        zCenter: number,
        heightVal: number,
        colorBase: string,
        colorTop: string,
        colorSide: string,
        isSelected: boolean
      ) => {
        const h = Math.max(4, (heightVal / maxDepth) * 90);
        const halfW = towerWidth / 2;
        const halfD = towerDepth / 2;

        // Vertices of the rectangular tower
        const b_fl = project3D(xCenter - halfW, 0, zCenter + halfD); // bottom front-left
        const b_fr = project3D(xCenter + halfW, 0, zCenter + halfD); // bottom front-right
        const b_br = project3D(xCenter + halfW, 0, zCenter - halfD); // bottom back-right

        const t_fl = project3D(xCenter - halfW, h, zCenter + halfD); // top front-left
        const t_fr = project3D(xCenter + halfW, h, zCenter + halfD); // top front-right
        const t_bl = project3D(xCenter - halfW, h, zCenter - halfD); // top back-left
        const t_br = project3D(xCenter + halfW, h, zCenter - halfD); // top back-right

        // Front Face
        ctx.fillStyle = colorBase;
        ctx.beginPath();
        ctx.moveTo(b_fl.x, b_fl.y);
        ctx.lineTo(b_fr.x, b_fr.y);
        ctx.lineTo(t_fr.x, t_fr.y);
        ctx.lineTo(t_fl.x, t_fl.y);
        ctx.closePath();
        ctx.fill();
        ctx.strokeStyle = isSelected ? "#38bdf8" : "rgba(255,255,255,0.15)";
        ctx.lineWidth = isSelected ? 2 : 1;
        ctx.stroke();

        // Right/Side Face
        ctx.fillStyle = colorSide;
        ctx.beginPath();
        ctx.moveTo(b_fr.x, b_fr.y);
        ctx.lineTo(b_br.x, b_br.y);
        ctx.lineTo(t_br.x, t_br.y);
        ctx.lineTo(t_fr.x, t_fr.y);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();

        // Top Face (Illuminated)
        ctx.fillStyle = colorTop;
        ctx.beginPath();
        ctx.moveTo(t_fl.x, t_fl.y);
        ctx.lineTo(t_fr.x, t_fr.y);
        ctx.lineTo(t_br.x, t_br.y);
        ctx.lineTo(t_bl.x, t_bl.y);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();

        // Selection / Hover Indicator Pin
        if (isSelected) {
          const pinTop = project3D(xCenter, h + 15, zCenter);
          const pinBase = project3D(xCenter, h, zCenter);
          ctx.strokeStyle = "#38bdf8";
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(pinBase.x, pinBase.y);
          ctx.lineTo(pinTop.x, pinTop.y);
          ctx.stroke();

          ctx.fillStyle = "#38bdf8";
          ctx.beginPath();
          ctx.arc(pinTop.x, pinTop.y, 4, 0, Math.PI * 2);
          ctx.fill();
        }
      };

      // Draw Bid Towers (Left of Mid-Market: negative X)
      curBids.forEach((bid, idx) => {
        const xPos = -(idx + 1) * (towerWidth + 5) - 4;
        const isSelected = curSelectedSide === "bid" && Math.abs(bid.price - curSelectedPrice) < 0.001;

        draw3DTower(
          xPos,
          0,
          bid.size,
          "rgba(16, 185, 129, 0.75)", // Base green
          "rgba(52, 211, 153, 0.95)", // Top bright green
          "rgba(5, 150, 105, 0.6)", // Side dark green
          isSelected
        );

        // Price label below tower
        const basePt = project3D(xPos, 0, towerDepth / 2 + 8);
        ctx.fillStyle = isSelected ? "#38bdf8" : theme.textSecondary;
        ctx.font = "10px monospace";
        ctx.textAlign = "center";
        ctx.fillText(`$${bid.price.toFixed(2)}`, basePt.x, basePt.y);
      });

      // Draw Ask Towers (Right of Mid-Market: positive X)
      curAsks.forEach((ask, idx) => {
        const xPos = (idx + 1) * (towerWidth + 5) + 4;
        const isSelected = curSelectedSide === "ask" && Math.abs(ask.price - curSelectedPrice) < 0.001;

        draw3DTower(
          xPos,
          0,
          ask.size,
          "rgba(239, 68, 68, 0.75)", // Base red
          "rgba(248, 113, 113, 0.95)", // Top bright red
          "rgba(185, 28, 28, 0.6)", // Side dark red
          isSelected
        );

        // Price label below tower
        const basePt = project3D(xPos, 0, towerDepth / 2 + 8);
        ctx.fillStyle = isSelected ? "#38bdf8" : theme.textSecondary;
        ctx.font = "10px monospace";
        ctx.textAlign = "center";
        ctx.fillText(`$${ask.price.toFixed(2)}`, basePt.x, basePt.y);
      });

      // Draw Waterfall Streaming Order Flow Particles
      waterfallEventsRef.current.forEach((ev) => {
        const progress = ev.zProgress ?? 0;
        const isBuy = ev.side === "buy";
        const isMarket = ev.type === "market";

        // Determine matching tower X location
        let targetX = 0;
        if (isBuy) {
          const idx = curBids.findIndex((b) => Math.abs(b.price - ev.price) < 0.001);
          targetX = idx >= 0 ? -(idx + 1) * (towerWidth + 5) - 4 : -25;
        } else {
          const idx = curAsks.findIndex((a) => Math.abs(a.price - ev.price) < 0.001);
          targetX = idx >= 0 ? (idx + 1) * (towerWidth + 5) + 4 : 25;
        }

        // Waterfall trajectory: cascades from (Z = -120, Y = 100) down to (Z = 0, Y = 0)
        const currZ = -120 * (1 - progress);
        const currY = 90 * (1 - progress) * (1 - progress);

        const screenPt = project3D(targetX, currY, currZ);

        if (progress < 1.0) {
          // In-flight particle / box
          const particleColor = isBuy ? "rgba(16, 185, 129, 0.9)" : "rgba(239, 68, 68, 0.9)";
          ctx.fillStyle = particleColor;
          ctx.shadowColor = particleColor;
          ctx.shadowBlur = 8;

          ctx.beginPath();
          if (isMarket) {
            // Market orders drawn as energetic diamonds
            ctx.moveTo(screenPt.x, screenPt.y - 5);
            ctx.lineTo(screenPt.x + 5, screenPt.y);
            ctx.lineTo(screenPt.x, screenPt.y + 5);
            ctx.lineTo(screenPt.x - 5, screenPt.y);
          } else {
            // Limit orders drawn as neat blocks
            ctx.rect(screenPt.x - 4, screenPt.y - 4, 8, 8);
          }
          ctx.closePath();
          ctx.fill();
          ctx.shadowBlur = 0;

          // Trail line behind particle
          const trailPt = project3D(targetX, currY + 15, currZ - 20);
          ctx.strokeStyle = isBuy ? "rgba(16, 185, 129, 0.3)" : "rgba(239, 68, 68, 0.3)";
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(screenPt.x, screenPt.y);
          ctx.lineTo(trailPt.x, trailPt.y);
          ctx.stroke();
        } else {
          // Landing splash / impact ripple
          const splashAge = ev.splashAge ?? 0;
          const radius = Math.min(25, splashAge * 2.5);
          const alpha = Math.max(0, 1 - splashAge / 25);

          ctx.strokeStyle = isBuy
            ? `rgba(52, 211, 153, ${alpha})`
            : `rgba(248, 113, 113, ${alpha})`;
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(screenPt.x, screenPt.y, radius, 0, Math.PI * 2);
          ctx.stroke();
        }
      });

      // Canvas Header HUD Overlay
      ctx.fillStyle = "rgba(18, 22, 28, 0.85)";
      ctx.strokeStyle = theme.border;
      ctx.lineWidth = 1;
      ctx.fillRect(12, 12, 210, 48);
      ctx.strokeRect(12, 12, 210, 48);

      ctx.fillStyle = theme.textPrimary;
      ctx.font = "bold 12px sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(`${curSymbol} LOB 3D DEPTH`, 22, 30);

      ctx.fillStyle = theme.textMuted;
      ctx.font = "10px monospace";
      ctx.fillText(
        `SPREAD: $${curMetrics.spread.toFixed(2)} (${curMetrics.spreadBps.toFixed(1)} bps)`,
        22,
        48
      );

      animFrameIdRef.current = requestAnimationFrame(renderLoop);
    };

    animFrameIdRef.current = requestAnimationFrame(renderLoop);

    // Global pointer up and window resize listeners
    const handleGlobalPointerUp = () => {
      // Global pointer cleanup if needed
    };
    const handleResize = () => {
      // Handle canvas resize
    };

    window.addEventListener("mouseup", handleGlobalPointerUp);
    window.addEventListener("touchend", handleGlobalPointerUp);
    window.addEventListener("resize", handleResize);

    return () => {
      isSubscribed = false;
      if (animFrameIdRef.current) {
        cancelAnimationFrame(animFrameIdRef.current);
        animFrameIdRef.current = null;
      }
      window.removeEventListener("mouseup", handleGlobalPointerUp);
      window.removeEventListener("touchend", handleGlobalPointerUp);
      window.removeEventListener("resize", handleResize);
      disposeCanvas(canvas);
    };
  }, [bids, asks, effectivePrice]);

  // Click on canvas to select price level
  const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const midX = rect.width / 2;

    if (clickX < midX) {
      // Pick best or nearest bid
      if (bids.length > 0) {
        const pickIdx = Math.min(bids.length - 1, Math.floor(((midX - clickX) / midX) * bids.length));
        setSelectedSide("bid");
        setSelectedPrice(bids[pickIdx].price);
      }
    } else {
      // Pick best or nearest ask
      if (asks.length > 0) {
        const pickIdx = Math.min(asks.length - 1, Math.floor(((clickX - midX) / midX) * asks.length));
        setSelectedSide("ask");
        setSelectedPrice(asks[pickIdx].price);
      }
    }
  };

  return (
    <div
      className={`card ${className}`}
      data-testid="lob-depth-3d-container"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--s-4)",
        padding: "var(--s-4)",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--r-md)",
      }}
    >
      {/* Header Toolbar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "var(--s-2)",
          paddingBottom: "var(--s-3)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <Box style={{ width: 22, height: 22, color: "var(--accent)" }} />
          <div>
            <h3
              style={{
                margin: 0,
                fontSize: "var(--t-callout)",
                fontWeight: 700,
                color: "var(--text-primary)",
                display: "flex",
                alignItems: "center",
                gap: "var(--s-2)",
              }}
            >
              3D Limit Order Book & Order Flow Waterfall
              <span
                style={{
                  fontSize: "var(--t-micro)",
                  fontWeight: 600,
                  padding: "2px 8px",
                  borderRadius: "var(--r-pill)",
                  background: "var(--surface-3)",
                  color: "var(--text-secondary)",
                }}
              >
                {symbol}
              </span>
            </h3>
            <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)" }}>
              Real-time LOB depth towers with simulated order flow waterfall & microstructure metrics
            </div>
          </div>
          {isSynthetic && <DemoDataBadge />}
        </div>

        {/* Mode Badge & Status Indicator */}
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <div
            data-testid="lob-render-mode"
            style={{
              fontSize: "var(--t-micro)",
              fontWeight: 600,
              padding: "4px 10px",
              borderRadius: "var(--r-sm)",
              display: "flex",
              alignItems: "center",
              gap: 6,
              background: isFallbackActive ? "rgba(245, 158, 11, 0.15)" : "rgba(16, 185, 129, 0.15)",
              color: isFallbackActive ? "var(--caution)" : "var(--growth)",
              border: `1px solid ${isFallbackActive ? "rgba(245, 158, 11, 0.3)" : "rgba(16, 185, 129, 0.3)"}`,
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: isFallbackActive ? "var(--caution)" : "var(--growth)",
              }}
            />
            {isFallbackActive ? "Canvas 2.5D Fallback Mode" : "WebGL 3D Active"}
          </div>

          <Button
            variant="neutral"
            onClick={handleResetSimulation}
            title="Reset simulation and events"
            data-testid="lob-reset-btn"
          >
            <RotateCcw style={{ width: 14, height: 14 }} />
          </Button>
        </div>
      </div>

      {/* Top Metrics Row */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
          gap: "var(--s-3)",
        }}
      >
        {/* Spread Metric */}
        <div
          data-testid="metric-spread"
          style={{
            padding: "var(--s-3)",
            background: "var(--surface-2)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
          }}
        >
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginBottom: 2 }}>
            SPREAD (L1)
          </div>
          <div style={{ fontSize: "var(--t-callout)", fontWeight: 700, color: "var(--text-primary)" }}>
            ${metrics.spread.toFixed(2)}
            <span style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginLeft: 6 }}>
              ({metrics.spreadBps.toFixed(1)} bps)
            </span>
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-secondary)", marginTop: 2 }}>
            Best: ${metrics.bestBid?.toFixed(2) ?? "--"} / ${metrics.bestAsk?.toFixed(2) ?? "--"}
          </div>
        </div>

        {/* Cumulative Bid Depth */}
        <div
          data-testid="metric-bid-depth"
          style={{
            padding: "var(--s-3)",
            background: "var(--surface-2)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
            borderLeft: "3px solid var(--growth)",
          }}
        >
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginBottom: 2 }}>
            CUMULATIVE BID DEPTH
          </div>
          <div style={{ fontSize: "var(--t-callout)", fontWeight: 700, color: "var(--growth)" }}>
            {metrics.totalBidDepth.toLocaleString()}
            <span style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginLeft: 6 }}>
              shares
            </span>
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-secondary)", marginTop: 2 }}>
            {bids.length} price levels
          </div>
        </div>

        {/* Cumulative Ask Depth */}
        <div
          data-testid="metric-ask-depth"
          style={{
            padding: "var(--s-3)",
            background: "var(--surface-2)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
            borderLeft: "3px solid var(--decline)",
          }}
        >
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginBottom: 2 }}>
            CUMULATIVE ASK DEPTH
          </div>
          <div style={{ fontSize: "var(--t-callout)", fontWeight: 700, color: "var(--decline)" }}>
            {metrics.totalAskDepth.toLocaleString()}
            <span style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginLeft: 6 }}>
              shares
            </span>
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-secondary)", marginTop: 2 }}>
            {asks.length} price levels
          </div>
        </div>

        {/* Microstructure Imbalance (OBI) */}
        <div
          data-testid="metric-imbalance"
          style={{
            padding: "var(--s-3)",
            background: "var(--surface-2)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 2 }}>
            <span style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)" }}>
              MICROSTRUCTURE IMBALANCE (OBI)
            </span>
            <span
              style={{
                fontSize: "var(--t-micro)",
                fontWeight: 700,
                color: metrics.imbalance >= 0 ? "var(--growth)" : "var(--decline)",
              }}
            >
              {metrics.imbalance >= 0 ? `+${metrics.imbalancePct.toFixed(1)}%` : `${metrics.imbalancePct.toFixed(1)}%`}
            </span>
          </div>
          {/* Visual Balance Bar */}
          <div
            style={{
              height: 6,
              background: "var(--surface-3)",
              borderRadius: "var(--r-pill)",
              overflow: "hidden",
              display: "flex",
              margin: "6px 0",
            }}
          >
            <div
              style={{
                width: `${metrics.totalDepth > 0 ? (metrics.totalBidDepth / metrics.totalDepth) * 100 : 50}%`,
                background: "var(--growth)",
              }}
            />
            <div
              style={{
                width: `${metrics.totalDepth > 0 ? (metrics.totalAskDepth / metrics.totalDepth) * 100 : 50}%`,
                background: "var(--decline)",
              }}
            />
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-secondary)" }}>
            Microprice: ${metrics.microPrice ? metrics.microPrice.toFixed(2) : effectivePrice.toFixed(2)}
          </div>
        </div>
      </div>

      {/* 3D Visualizer & Fallback Viewport */}
      <div
        style={{
          position: "relative",
          width: "100%",
          height: typeof height === "number" ? `${height}px` : height,
          background: "var(--base)",
          borderRadius: "var(--r-md)",
          border: "1px solid var(--border)",
          overflow: "hidden",
        }}
      >
        <canvas
          ref={canvasRef}
          width={800}
          height={520}
          onClick={handleCanvasClick}
          style={{
            width: "100%",
            height: "100%",
            display: "block",
            cursor: "crosshair",
          }}
          data-testid="lob-depth-canvas"
        />

        {/* Fallback Static DOM Overlay (for environments with minimal canvas support) */}
        {isFallbackActive && (
          <div
            data-testid="lob-fallback-view"
            style={{
              position: "absolute",
              bottom: 12,
              left: 12,
              background: "rgba(18, 22, 28, 0.9)",
              padding: "6px 12px",
              borderRadius: "var(--r-sm)",
              border: "1px solid var(--border)",
              fontSize: "var(--t-micro)",
              color: "var(--text-muted)",
              pointerEvents: "none",
            }}
          >
            <span>Rendering 2.5D Isometric Towers & Order Stream</span>
          </div>
        )}

        {/* Camera Angle & Preset Controls Overlay */}
        <div
          style={{
            position: "absolute",
            top: 12,
            right: 12,
            background: "rgba(18, 22, 28, 0.85)",
            backdropFilter: "blur(4px)",
            padding: "var(--s-2)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--s-2)",
          }}
        >
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", fontWeight: 600 }}>
            CAMERA PRESET
          </div>
          <div style={{ display: "flex", gap: "var(--s-1)" }}>
            <Button
              variant={activePreset === "iso" ? "primary" : "neutral"}
              onClick={() => handlePresetSelect("iso")}
              data-testid="preset-iso-btn"
            >
              Isometric 3D
            </Button>
            <Button
              variant={activePreset === "front" ? "primary" : "neutral"}
              onClick={() => handlePresetSelect("front")}
              data-testid="preset-front-btn"
            >
              Front 3D
            </Button>
            <Button
              variant={activePreset === "top" ? "primary" : "neutral"}
              onClick={() => handlePresetSelect("top")}
              data-testid="preset-top-btn"
            >
              Top-Down
            </Button>
            <Button
              variant={activePreset === "side" ? "primary" : "neutral"}
              onClick={() => handlePresetSelect("side")}
              data-testid="preset-side-btn"
            >
              Side Angle
            </Button>
          </div>

          {/* Interactive Yaw & Pitch Sliders */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--s-2)", marginTop: 2 }}>
            <div>
              <label style={{ fontSize: "10px", color: "var(--text-muted)", display: "block" }}>
                Yaw ({yaw}°)
              </label>
              <input
                type="range"
                min="-90"
                max="90"
                value={yaw}
                onChange={(e) => {
                  setYaw(parseInt(e.target.value, 10));
                  setActivePreset("iso");
                }}
                data-testid="slider-yaw"
                style={{ width: "100%", accentColor: "var(--accent)" }}
              />
            </div>
            <div>
              <label style={{ fontSize: "10px", color: "var(--text-muted)", display: "block" }}>
                Pitch ({pitch}°)
              </label>
              <input
                type="range"
                min="0"
                max="89"
                value={pitch}
                onChange={(e) => {
                  setPitch(parseInt(e.target.value, 10));
                  setActivePreset("iso");
                }}
                data-testid="slider-pitch"
                style={{ width: "100%", accentColor: "var(--accent)" }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Interactive Controls & Order Flow Waterfall Ribbon */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
          gap: "var(--s-4)",
        }}
      >
        {/* Waterfall Controls & Manual Order Injector */}
        <div
          style={{
            background: "var(--surface-2)",
            padding: "var(--s-4)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--s-3)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
              <Zap style={{ width: 18, height: 18, color: "var(--accent)" }} />
              <h4 style={{ margin: 0, fontSize: "var(--t-body)", fontWeight: 600, color: "var(--text-primary)" }}>
                Order Flow Waterfall Stream
              </h4>
            </div>

            {/* Play/Pause & Speed Controls */}
            <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1)" }}>
              <Button
                variant={isPlaying ? "neutral" : "primary"}
                onClick={() => setIsPlaying(!isPlaying)}
                data-testid="waterfall-play-pause-btn"
              >
                {isPlaying ? (
                  <>
                    <Pause style={{ width: 14, height: 14, marginRight: 4 }} /> Pause
                  </>
                ) : (
                  <>
                    <Play style={{ width: 14, height: 14, marginRight: 4 }} /> Play
                  </>
                )}
              </Button>

              {[0.5, 1, 2, 5].map((spd) => (
                <button
                  key={spd}
                  type="button"
                  onClick={() => setFlowSpeed(spd)}
                  data-testid={`speed-btn-${spd}`}
                  style={{
                    background: flowSpeed === spd ? "var(--accent)" : "var(--surface-3)",
                    color: flowSpeed === spd ? "#000" : "var(--text-secondary)",
                    border: "none",
                    borderRadius: "var(--r-2xs)",
                    padding: "3px 7px",
                    fontSize: "var(--t-micro)",
                    fontWeight: 700,
                    cursor: "pointer",
                  }}
                >
                  {spd}x
                </button>
              ))}
            </div>
          </div>

          {/* Filter Pills */}
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
            <span style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)" }}>FILTER:</span>
            {(["all", "market", "limit"] as const).map((filter) => (
              <button
                key={filter}
                type="button"
                onClick={() => setOrderFilter(filter)}
                data-testid={`filter-btn-${filter}`}
                style={{
                  background: orderFilter === filter ? "var(--surface-3)" : "transparent",
                  color: orderFilter === filter ? "var(--text-primary)" : "var(--text-muted)",
                  border: `1px solid ${orderFilter === filter ? "var(--border-strong)" : "transparent"}`,
                  borderRadius: "var(--r-sm)",
                  padding: "2px 8px",
                  fontSize: "var(--t-micro)",
                  cursor: "pointer",
                  textTransform: "capitalize",
                }}
              >
                {filter === "all" ? "All Orders" : `${filter} Only`}
              </button>
            ))}
          </div>

          {/* Quick Manual Injection Buttons */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "var(--s-2)" }}>
            <Button
              variant="neutral"
              onClick={() => injectOrder("buy", "market", metrics.bestAsk ?? 450.05, 200)}
              data-testid="inject-market-buy-btn"
              style={{ color: "var(--growth)", borderColor: "rgba(16, 185, 129, 0.4)" }}
            >
              + Mkt Buy
            </Button>
            <Button
              variant="neutral"
              onClick={() => injectOrder("sell", "market", metrics.bestBid ?? 449.95, 200)}
              data-testid="inject-market-sell-btn"
              style={{ color: "var(--decline)", borderColor: "rgba(239, 68, 68, 0.4)" }}
            >
              + Mkt Sell
            </Button>
            <Button
              variant="neutral"
              onClick={() => injectOrder("buy", "limit", Number((effectivePrice - 0.1).toFixed(2)), 300)}
              data-testid="inject-limit-bid-btn"
            >
              + Lmt Bid
            </Button>
            <Button
              variant="neutral"
              onClick={() => injectOrder("sell", "limit", Number((effectivePrice + 0.1).toFixed(2)), 300)}
              data-testid="inject-limit-ask-btn"
            >
              + Lmt Ask
            </Button>
          </div>

          {/* Custom Order Injector Form */}
          <form
            onSubmit={handleInjectForm}
            style={{
              background: "var(--surface)",
              padding: "var(--s-3)",
              borderRadius: "var(--r-sm)",
              border: "1px solid var(--border)",
              display: "flex",
              flexDirection: "column",
              gap: "var(--s-2)",
            }}
          >
            <div style={{ fontSize: "var(--t-micro)", fontWeight: 600, color: "var(--text-muted)" }}>
              CUSTOM ORDER INJECTION
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: "var(--s-2)" }}>
              <Select
                id="inject-side"
                label="Side"
                value={injectSide}
                onChange={(e) => setInjectSide(e.target.value as "buy" | "sell")}
                options={[
                  { value: "buy", label: "Buy" },
                  { value: "sell", label: "Sell" },
                ]}
              />
              <Select
                id="inject-type"
                label="Type"
                value={injectType}
                onChange={(e) => setInjectType(e.target.value as "market" | "limit")}
                options={[
                  { value: "market", label: "Market" },
                  { value: "limit", label: "Limit" },
                ]}
              />
              <Input
                id="inject-price"
                label="Price"
                type="number"
                step={0.01}
                value={injectPrice}
                onChange={(e) => setInjectPrice(e.target.value)}
              />
              <Input
                id="inject-size"
                label="Size"
                type="number"
                step={50}
                value={injectSize}
                onChange={(e) => setInjectSize(e.target.value)}
              />
            </div>
            <Button type="submit" variant="primary" data-testid="inject-custom-btn">
              <Plus style={{ width: 14, height: 14, marginRight: 4 }} /> Inject Order
            </Button>
          </form>
        </div>

        {/* Queue Priority Indicator & Microstructure Inspector */}
        <div
          style={{
            background: "var(--surface-2)",
            padding: "var(--s-4)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--s-3)",
          }}
          data-testid="queue-priority-panel"
        >
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
            <ShieldCheck style={{ width: 18, height: 18, color: "var(--accent)" }} />
            <h4 style={{ margin: 0, fontSize: "var(--t-body)", fontWeight: 600, color: "var(--text-primary)" }}>
              Queue Priority & Fill Probability
            </h4>
          </div>

          {/* Queue Parameters Input */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "var(--s-2)" }}>
            <Select
              id="queue-side"
              label="Order Side"
              value={selectedSide}
              onChange={(e) => setSelectedSide(e.target.value as "bid" | "ask")}
              options={[
                { value: "bid", label: "Bid (Buy)" },
                { value: "ask", label: "Ask (Sell)" },
              ]}
            />
            <Select
              id="queue-price"
              label="Price Level"
              value={selectedPrice.toString()}
              onChange={(e) => setSelectedPrice(parseFloat(e.target.value))}
              options={(selectedSide === "bid" ? bids : asks).map((lvl) => ({
                value: lvl.price.toString(),
                label: `$${lvl.price.toFixed(2)} (${lvl.size} sz)`,
              }))}
            />
            <Input
              id="queue-size"
              label="My Order Size"
              type="number"
              min={1}
              value={myOrderSize.toString()}
              onChange={(e) => setMyOrderSize(parseInt(e.target.value, 10) || 100)}
            />
          </div>

          {/* Queue Metrics Output Box */}
          <div
            style={{
              background: "var(--surface)",
              padding: "var(--s-3)",
              borderRadius: "var(--r-sm)",
              border: "1px solid var(--border)",
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "var(--s-3)",
            }}
          >
            <div>
              <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)" }}>
                DEPTH AHEAD IN QUEUE
              </div>
              <div
                style={{ fontSize: "var(--t-callout)", fontWeight: 700, color: "var(--text-primary)" }}
                data-testid="queue-depth-ahead"
              >
                {queueResult.depthAhead.toLocaleString()}
                <span style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginLeft: 4 }}>
                  shares
                </span>
              </div>
              <div style={{ fontSize: "var(--t-micro)", color: "var(--text-secondary)", marginTop: 2 }}>
                Est. Time to Fill: ~{queueResult.estimatedTimeToFillSec}s
              </div>
            </div>

            <div>
              <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)" }}>
                QUEUE PRIORITY RATING
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 2 }}>
                <span
                  data-testid="queue-priority-rating"
                  style={{
                    fontSize: "var(--t-micro)",
                    fontWeight: 700,
                    padding: "3px 8px",
                    borderRadius: "var(--r-pill)",
                    background:
                      queueResult.priorityRating === "HIGH"
                        ? "rgba(16, 185, 129, 0.2)"
                        : queueResult.priorityRating === "MEDIUM"
                        ? "rgba(245, 158, 11, 0.2)"
                        : "rgba(239, 68, 68, 0.2)",
                    color:
                      queueResult.priorityRating === "HIGH"
                        ? "var(--growth)"
                        : queueResult.priorityRating === "MEDIUM"
                        ? "var(--caution)"
                        : "var(--decline)",
                  }}
                >
                  {queueResult.priorityRating} PRIORITY
                </span>
                <span
                  style={{ fontSize: "var(--t-micro)", fontWeight: 600, color: "var(--text-primary)" }}
                  data-testid="queue-fill-prob"
                >
                  {queueResult.estimatedFillProbability}% Fill Prob
                </span>
              </div>
              {/* Fill Probability Progress Bar */}
              <div
                style={{
                  height: 4,
                  background: "var(--surface-3)",
                  borderRadius: "var(--r-pill)",
                  overflow: "hidden",
                  marginTop: 6,
                }}
              >
                <div
                  style={{
                    width: `${queueResult.estimatedFillProbability}%`,
                    height: "100%",
                    background:
                      queueResult.priorityRating === "HIGH"
                        ? "var(--growth)"
                        : queueResult.priorityRating === "MEDIUM"
                        ? "var(--caution)"
                        : "var(--decline)",
                  }}
                />
              </div>
            </div>
          </div>

          {/* Real-time Simulated Tape Feed */}
          <div>
            <div style={{ fontSize: "var(--t-micro)", fontWeight: 600, color: "var(--text-muted)", marginBottom: 4 }}>
              RECENT ARRIVALS TAPE (SIMULATED)
            </div>
            <div
              style={{
                maxHeight: 110,
                overflowY: "auto",
                background: "var(--surface)",
                borderRadius: "var(--r-xs)",
                border: "1px solid var(--border)",
                padding: "4px 8px",
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
              data-testid="lob-tape-feed"
            >
              {recentTape.length === 0 ? (
                <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", textAlign: "center", padding: 8 }}>
                  Awaiting order arrivals...
                </div>
              ) : (
                recentTape.slice(0, 5).map((ev) => (
                  <div
                    key={ev.id}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      fontSize: "var(--t-micro)",
                      fontFamily: "monospace",
                      padding: "2px 0",
                      borderBottom: "1px solid rgba(255, 255, 255, 0.04)",
                    }}
                  >
                    <span
                      style={{
                        color: ev.side === "buy" ? "var(--growth)" : "var(--decline)",
                        fontWeight: 700,
                      }}
                    >
                      {ev.side.toUpperCase()} {ev.type.toUpperCase()}
                    </span>
                    <span style={{ color: "var(--text-primary)" }}>
                      {ev.size} @ ${ev.price.toFixed(2)}
                    </span>
                    <span style={{ color: "var(--text-muted)" }}>
                      {new Date(ev.timestamp).toLocaleTimeString().split(" ")[0]}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default LobDepth3D;
