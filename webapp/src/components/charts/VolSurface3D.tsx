import { useState, useEffect, useRef, useMemo } from "react";
import {
  Layers,
  RotateCcw,
  Play,
  Pause,
  Scissors,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { theme } from "../../theme";
import type { VolSurfaceResponse } from "../../api/types";
import DemoDataBadge from "../DemoDataBadge";
import { Button } from "../ui";
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

export interface VolSurface3DPoint {
  strike: number;
  dte: number;
  iv: number;
  moneyness?: number;
  expiration?: string;
  call_iv?: number;
  put_iv?: number;
}

export interface VolSurfaceMesh {
  symbol: string;
  spotPrice: number;
  strikes: number[];
  dtes: number[];
  grid: number[][]; // grid[dteIdx][strikeIdx] = iv
  minIv: number;
  maxIv: number;
  minStrike: number;
  maxStrike: number;
  minDte: number;
  maxDte: number;
}

export type ColormapType = "cyan-amber" | "plasma" | "viridis" | "emerald";
export type RenderMode = "surface-wireframe" | "surface" | "wireframe" | "points";
export type SliceDimension = "none" | "dte" | "strike";
export type ViewPreset = "iso" | "smile" | "term" | "contour";

export interface VolSurface3DProps {
  points?: VolSurface3DPoint[];
  volResponse?: VolSurfaceResponse;
  symbol?: string;
  spotPrice?: number;
  height?: number | string;
  width?: number | string;
  className?: string;
  forceFallback?: boolean;
  initialColormap?: ColormapType;
  initialRenderMode?: RenderMode;
  initialSliceDimension?: SliceDimension;
  initialSliceValue?: number;
  showControls?: boolean;
  showCrossSection?: boolean;
  autoRotate?: boolean;
  onHoverPoint?: (point: VolSurface3DPoint | null) => void;
  onSelectPoint?: (point: VolSurface3DPoint | null) => void;
}

// ============================================================================
// Color Mapping Palettes & Utilities
// ============================================================================

interface ColorStop {
  pos: number;
  r: number;
  g: number;
  b: number;
}

const COLOR_PALETTES: Record<ColormapType, ColorStop[]> = {
  // Signature Pilots PWA fintech theme: Slate/Navy -> Cyan -> Emerald -> Amber -> Rose
  "cyan-amber": [
    { pos: 0.0, r: 15, g: 23, b: 42 },     // #0f172a (deep slate)
    { pos: 0.25, r: 56, g: 189, b: 248 },  // #38bdf8 (cyan/accent)
    { pos: 0.55, r: 16, g: 185, b: 129 },  // #10b981 (emerald/growth)
    { pos: 0.8, r: 245, g: 158, b: 11 },   // #f59e0b (amber/caution)
    { pos: 1.0, r: 239, g: 68, b: 68 },    // #ef4444 (rose/decline)
  ],
  // Standard financial Plasma gradient (purple -> magenta -> orange -> yellow)
  plasma: [
    { pos: 0.0, r: 13, g: 8, b: 135 },     // #0d0887
    { pos: 0.35, r: 156, g: 23, b: 158 },  // #9c179e
    { pos: 0.65, r: 237, g: 121, b: 83 },  // #ed7953
    { pos: 0.9, r: 253, g: 231, b: 37 },   // #fde725
    { pos: 1.0, r: 255, g: 255, b: 180 },  // light yellow
  ],
  // Scientific Viridis gradient (purple -> teal -> green -> yellow)
  viridis: [
    { pos: 0.0, r: 68, g: 1, b: 84 },      // #440154
    { pos: 0.3, r: 49, g: 104, b: 142 },   // #31688e
    { pos: 0.65, r: 53, g: 183, b: 121 },  // #35b779
    { pos: 1.0, r: 253, g: 231, b: 37 },   // #fde725
  ],
  // Emerald Peak (deep teal -> bright mint -> warm gold)
  emerald: [
    { pos: 0.0, r: 6, g: 78, b: 59 },      // #064e3b
    { pos: 0.4, r: 16, g: 185, b: 129 },   // #10b981
    { pos: 0.75, r: 52, g: 211, b: 153 },  // #34d399
    { pos: 1.0, r: 251, g: 191, b: 36 },   // #fbbf24
  ],
};

/**
 * Samples RGB and HEX color from a colormap ramp for a normalized value t in [0, 1].
 */
export function sampleColormap(
  colormap: ColormapType = "cyan-amber",
  t: number
): { r: number; g: number; b: number; hex: string; rgba: (alpha?: number) => string } {
  const clampedT = Math.max(0, Math.min(1, isNaN(t) ? 0 : t));
  const stops = COLOR_PALETTES[colormap] || COLOR_PALETTES["cyan-amber"];

  let lower = stops[0];
  let upper = stops[stops.length - 1];

  for (let i = 0; i < stops.length - 1; i++) {
    if (clampedT >= stops[i].pos && clampedT <= stops[i + 1].pos) {
      lower = stops[i];
      upper = stops[i + 1];
      break;
    }
  }

  const range = upper.pos - lower.pos || 1;
  const factor = (clampedT - lower.pos) / range;

  const r = Math.round(lower.r + factor * (upper.r - lower.r));
  const g = Math.round(lower.g + factor * (upper.g - lower.g));
  const b = Math.round(lower.b + factor * (upper.b - lower.b));

  const hex = `#${((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1)}`;
  const rgba = (alpha = 1) => `rgba(${r}, ${g}, ${b}, ${alpha})`;

  return { r, g, b, hex, rgba };
}

// ============================================================================
// Math & Mesh Generation Utilities
// ============================================================================

/**
 * Checks if WebGL is available in the current environment.
 */
export function checkWebGLSupport(): boolean {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return false;
  }
  try {
    const canvas = document.createElement("canvas");
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
 * Generates a realistic synthetic volatility surface mesh (SVI / SABR polynomial shape).
 */
export function generateSyntheticVolMesh(
  symbol = "SPY",
  spot = 505.2,
  nStrikes = 15,
  nDtes = 8
): VolSurfaceMesh {
  const strikes: number[] = [];
  const minK = Math.round(spot * 0.8);
  const maxK = Math.round(spot * 1.2);
  const strikeStep = (maxK - minK) / (nStrikes - 1);
  for (let i = 0; i < nStrikes; i++) {
    strikes.push(Number((minK + i * strikeStep).toFixed(1)));
  }

  const dtes = [7, 14, 30, 45, 60, 90, 180, 365].slice(0, nDtes);
  while (dtes.length < nDtes) {
    const lastDte = dtes[dtes.length - 1];
    dtes.push(lastDte + 30);
  }

  const grid: number[][] = [];
  let minIv = Infinity;
  let maxIv = -Infinity;

  for (let j = 0; j < dtes.length; j++) {
    const dte = dtes[j];
    const T = dte / 365.0;
    const row: number[] = [];

    // Term structure base (mild contango with term decay factor)
    const baseAtmIv = 0.18 + 0.04 * Math.log(1 + T);

    for (let i = 0; i < strikes.length; i++) {
      const strike = strikes[i];
      const m = Math.log(strike / spot); // log moneyness

      // Typical equity skew: downward slope with convex smile wings (SVI-like)
      const skewSlope = -0.15 / Math.sqrt(Math.max(0.04, T));
      const smileCurvature = 0.22 / Math.max(0.1, Math.pow(T, 0.4));
      const iv = Math.max(
        0.08,
        baseAtmIv + skewSlope * m + smileCurvature * m * m
      );

      minIv = Math.min(minIv, iv);
      maxIv = Math.max(maxIv, iv);
      row.push(Number(iv.toFixed(4)));
    }
    grid.push(row);
  }

  return {
    symbol,
    spotPrice: spot,
    strikes,
    dtes,
    grid,
    minIv: Number(minIv.toFixed(4)),
    maxIv: Number(maxIv.toFixed(4)),
    minStrike: strikes[0],
    maxStrike: strikes[strikes.length - 1],
    minDte: dtes[0],
    maxDte: dtes[dtes.length - 1],
  };
}

/**
 * Builds or interpolates a clean NxM grid mesh from raw points or VolSurfaceResponse.
 */
export function buildMeshFromPointsOrResponse(
  points?: VolSurface3DPoint[],
  volResponse?: VolSurfaceResponse,
  spotPrice?: number,
  defaultSymbol = "SPY"
): VolSurfaceMesh {
  const spot = spotPrice ?? volResponse?.spot_price ?? 500;
  const symbol = volResponse?.symbol ?? defaultSymbol;

  // Case 1: If raw VolSurface3DPoint[] is provided with multiple DTEs & strikes
  if (points && points.length >= 4) {
    const strikeSet = Array.from(new Set(points.map((p) => p.strike))).sort((a, b) => a - b);
    const dteSet = Array.from(new Set(points.map((p) => p.dte))).sort((a, b) => a - b);

    if (strikeSet.length >= 2 && dteSet.length >= 2) {
      let minIv = Infinity;
      let maxIv = -Infinity;
      const pointMap = new Map<string, number>();

      for (const p of points) {
        pointMap.set(`${p.dte}_${p.strike}`, p.iv);
        minIv = Math.min(minIv, p.iv);
        maxIv = Math.max(maxIv, p.iv);
      }

      const grid: number[][] = [];
      for (const dte of dteSet) {
        const row: number[] = [];
        for (const strike of strikeSet) {
          const exact = pointMap.get(`${dte}_${strike}`);
          if (exact !== undefined) {
            row.push(exact);
          } else {
            // Nearest neighbor / 1D fallback
            const sameDtePts = points.filter((p) => p.dte === dte);
            if (sameDtePts.length > 0) {
              const nearest = sameDtePts.reduce((prev, curr) =>
                Math.abs(curr.strike - strike) < Math.abs(prev.strike - strike) ? curr : prev
              );
              row.push(nearest.iv);
            } else {
              row.push(0.25);
            }
          }
        }
        grid.push(row);
      }

      return {
        symbol,
        spotPrice: spot,
        strikes: strikeSet,
        dtes: dteSet,
        grid,
        minIv: isFinite(minIv) ? minIv : 0.15,
        maxIv: isFinite(maxIv) ? maxIv : 0.45,
        minStrike: strikeSet[0],
        maxStrike: strikeSet[strikeSet.length - 1],
        minDte: dteSet[0],
        maxDte: dteSet[dteSet.length - 1],
      };
    }
  }

  // Case 2: If VolSurfaceResponse is provided (smile_points + term_structure)
  if (volResponse && volResponse.smile_points?.length && volResponse.term_structure?.length) {
    const smiles = volResponse.smile_points;
    const terms = volResponse.term_structure;

    const strikes = Array.from(new Set(smiles.map((s) => s.strike))).sort((a, b) => a - b);
    const dtes = Array.from(new Set(terms.map((t) => t.dte))).sort((a, b) => a - b);

    // ATM IV of the base smile curve
    const baseAtmIv = volResponse.skew?.atm_iv ?? 0.22;
    let minIv = Infinity;
    let maxIv = -Infinity;

    const grid: number[][] = [];
    for (const dte of dtes) {
      const termPt = terms.find((t) => t.dte === dte);
      const termAtm = termPt ? termPt.atm_iv : baseAtmIv;

      const row: number[] = [];
      for (const strike of strikes) {
        const smilePt = smiles.find((s) => s.strike === strike);
        const baseIv = smilePt ? smilePt.iv : baseAtmIv;
        // Scale smile by term structure ratio with skew flattening for longer DTEs
        const dteFactor = Math.sqrt(30 / Math.max(7, dte));
        const ivVal = termAtm + (baseIv - baseAtmIv) * dteFactor;
        const boundedIv = Math.max(0.05, Math.min(1.5, ivVal));

        minIv = Math.min(minIv, boundedIv);
        maxIv = Math.max(maxIv, boundedIv);
        row.push(Number(boundedIv.toFixed(4)));
      }
      grid.push(row);
    }

    return {
      symbol: volResponse.symbol || defaultSymbol,
      spotPrice: volResponse.spot_price || spot,
      strikes,
      dtes,
      grid,
      minIv: isFinite(minIv) ? minIv : 0.15,
      maxIv: isFinite(maxIv) ? maxIv : 0.5,
      minStrike: strikes[0],
      maxStrike: strikes[strikes.length - 1],
      minDte: dtes[0],
      maxDte: dtes[dtes.length - 1],
    };
  }

  // Fallback: Generate realistic synthetic surface
  return generateSyntheticVolMesh(symbol, spot);
}

/**
 * Extracts a 2D cross section along either DTE or Strike dimension.
 */
export function sliceMesh(
  mesh: VolSurfaceMesh,
  dimension: "dte" | "strike",
  value: number
): { sliceX: number[]; sliceY: number[]; label: string; dimension: string } {
  if (dimension === "dte") {
    // Find closest DTE
    let bestIdx = 0;
    let minDiff = Infinity;
    for (let j = 0; j < mesh.dtes.length; j++) {
      const diff = Math.abs(mesh.dtes[j] - value);
      if (diff < minDiff) {
        minDiff = diff;
        bestIdx = j;
      }
    }
    const chosenDte = mesh.dtes[bestIdx];
    const sliceX = mesh.strikes;
    const sliceY = mesh.grid[bestIdx] || [];
    return {
      sliceX,
      sliceY,
      label: `Volatility Smile @ DTE ${chosenDte}d`,
      dimension: "dte",
    };
  } else {
    // Find closest Strike
    let bestIdx = 0;
    let minDiff = Infinity;
    for (let i = 0; i < mesh.strikes.length; i++) {
      const diff = Math.abs(mesh.strikes[i] - value);
      if (diff < minDiff) {
        minDiff = diff;
        bestIdx = i;
      }
    }
    const chosenStrike = mesh.strikes[bestIdx];
    const sliceX = mesh.dtes;
    const sliceY = mesh.grid.map((row) => {
      const v = row[bestIdx];
      return typeof v === "number" && !isNaN(v) ? v : (row[bestIdx - 1] ?? row[bestIdx + 1] ?? 0.2);
    });
    return {
      sliceX,
      sliceY,
      label: `Term Structure @ Strike $${chosenStrike.toFixed(1)}`,
      dimension: "strike",
    };
  }
}

/**
 * Computes surface summary metrics (ATM IV, Skew, Min/Max IV, Term Slope).
 *
 * `skew25d` prefers the real, delta-derived backend value
 * (`volResponse.skew.skew_25delta`, from `pilots/volatility_surface.py`'s
 * `compute_25delta_skew` -- an actual Black-Scholes delta lookup against the
 * live chain) whenever a real `volResponse` was supplied. The moneyness
 * proxy below (nearest strike to spot * 0.95 / 1.05 -- never touches a
 * `.delta` field despite the "25-delta" label) is used ONLY as a fallback
 * for the synthetic/demo mesh path, where no real chain data exists at all.
 * This mirrors `optionsHonesty.effectiveIvr`'s real-vs-proxy preference
 * pattern -- and matters because `VolSurfaceView.tsx` (the sibling 2D
 * screen) already renders the real backend value under the IDENTICAL "25-
 * Delta Put-Call Skew" label; falling back to the proxy whenever a real
 * value is merely ABSENT (rather than never supplied) would silently show a
 * second, disagreeing number under that same label instead of an honest
 * "unavailable" -- so a present-but-empty backend skew reports `null`, not
 * the proxy.
 */
export function calculateSurfaceMetrics(
  mesh: VolSurfaceMesh,
  volResponse?: VolSurfaceResponse
): {
  atmIv: number;
  minIv: number;
  maxIv: number;
  skew25d: number | null;
  skew25dIsReal: boolean;
  termSlope: number;
  spotPrice: number;
} {
  // Nearest strike to spot
  let atmIdx = 0;
  let minSpotDiff = Infinity;
  for (let i = 0; i < mesh.strikes.length; i++) {
    const diff = Math.abs(mesh.strikes[i] - mesh.spotPrice);
    if (diff < minSpotDiff) {
      minSpotDiff = diff;
      atmIdx = i;
    }
  }

  // Front DTE
  const frontRow = mesh.grid[0] || [];
  const backRow = mesh.grid[mesh.grid.length - 1] || [];
  const atmIv = frontRow[atmIdx] ?? 0.22;

  // Approximate 25-delta Put (low strike ~ 95% spot) and Call (~ 105% spot)
  const putStrike = mesh.spotPrice * 0.95;
  const callStrike = mesh.spotPrice * 1.05;

  let putIdx = 0;
  let callIdx = mesh.strikes.length - 1;
  let minPutDiff = Infinity;
  let minCallDiff = Infinity;

  for (let i = 0; i < mesh.strikes.length; i++) {
    const k = mesh.strikes[i];
    if (Math.abs(k - putStrike) < minPutDiff) {
      minPutDiff = Math.abs(k - putStrike);
      putIdx = i;
    }
    if (Math.abs(k - callStrike) < minCallDiff) {
      minCallDiff = Math.abs(k - callStrike);
      callIdx = i;
    }
  }

  const putIv = frontRow[putIdx] ?? atmIv;
  const callIv = frontRow[callIdx] ?? atmIv;
  const proxySkew25d = putIv - callIv;

  const realSkew25d = volResponse?.skew?.skew_25delta;
  const hasRealSkew = typeof realSkew25d === "number" && Number.isFinite(realSkew25d);
  // No volResponse at all -> genuinely synthetic mesh, the moneyness proxy
  // is the only estimate available. volResponse present but its skew field
  // absent -> honest "unavailable", never a silent proxy substitution.
  const skew25d = hasRealSkew ? (realSkew25d as number) : volResponse ? null : proxySkew25d;
  const skew25dIsReal = hasRealSkew;

  // Term slope (Back month ATM IV - Front month ATM IV)
  const backAtmIv = backRow[atmIdx] ?? atmIv;
  const termSlope = backAtmIv - atmIv;

  return {
    atmIv,
    minIv: mesh.minIv,
    maxIv: mesh.maxIv,
    skew25d,
    skew25dIsReal,
    termSlope,
    spotPrice: mesh.spotPrice,
  };
}

// ============================================================================
// Main Component
// ============================================================================

export const VolSurface3D: React.FC<VolSurface3DProps> = ({
  points,
  volResponse,
  symbol = "SPY",
  spotPrice,
  height = 560,
  width = "100%",
  className = "",
  forceFallback = false,
  initialColormap = "cyan-amber",
  initialRenderMode = "surface-wireframe",
  initialSliceDimension = "none",
  initialSliceValue,
  showControls = true,
  showCrossSection = true,
  autoRotate: propAutoRotate = false,
  onHoverPoint,
  onSelectPoint,
}) => {
  // WebGL & Fallback state
  const [hasWebGL, setHasWebGL] = useState<boolean>(true);
  useEffect(() => {
    setHasWebGL(checkWebGLSupport());
  }, []);

  const isFallbackActive = forceFallback || !hasWebGL;

  // Build 3D mesh from data
  const mesh: VolSurfaceMesh = useMemo(() => {
    return buildMeshFromPointsOrResponse(points, volResponse, spotPrice, symbol);
  }, [points, volResponse, spotPrice, symbol]);

  const metrics = useMemo(() => calculateSurfaceMetrics(mesh, volResponse), [mesh, volResponse]);

  // View & Interactive State
  const [yaw, setYaw] = useState<number>(40); // Horizontal azimuth in degrees
  const [pitch, setPitch] = useState<number>(32); // Vertical elevation in degrees
  const [zoom, setZoom] = useState<number>(1.0);
  const [panX, setPanX] = useState<number>(0);
  const [panY, setPanY] = useState<number>(0);

  const [activePreset, setActivePreset] = useState<ViewPreset>("iso");
  const [colormap, setColormap] = useState<ColormapType>(initialColormap);
  const [renderMode, setRenderMode] = useState<RenderMode>(initialRenderMode);
  const [isAutoRotating, setIsAutoRotating] = useState<boolean>(propAutoRotate);

  // Cross-Section Slicing State
  const [sliceDim, setSliceDim] = useState<SliceDimension>(initialSliceDimension);
  const [sliceVal, setSliceVal] = useState<number>(
    initialSliceValue ?? (initialSliceDimension === "strike" ? mesh.spotPrice : mesh.dtes[0])
  );

  // Hover & Selection State
  const [hoveredPoint, setHoveredPoint] = useState<VolSurface3DPoint | null>(null);
  const [selectedPoint, setSelectedPoint] = useState<VolSurface3DPoint | null>(null);

  // Canvas Refs & Dragging
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const isDraggingRef = useRef<boolean>(false);
  const dragModeRef = useRef<"orbit" | "pan">("orbit");
  const lastMousePosRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
  const lastTouchDistRef = useRef<number>(0);
  const animFrameIdRef = useRef<number | null>(null);

  // Camera and Interaction Mutable Ref (decouples 60fps render loop from React state re-renders)
  const cameraRef = useRef({
    yaw,
    pitch,
    panX,
    panY,
    zoom,
    isAutoRotating,
    colormap,
    renderMode,
    sliceDim,
    sliceVal,
    hoveredPoint,
    selectedPoint,
    mesh,
  });

  // Keep cameraRef synced with React state updates
  cameraRef.current = {
    yaw,
    pitch,
    panX,
    panY,
    zoom,
    isAutoRotating,
    colormap,
    renderMode,
    sliceDim,
    sliceVal,
    hoveredPoint,
    selectedPoint,
    mesh,
  };

  // Cross-section slice data
  const sliceData = useMemo(() => {
    if (sliceDim === "none") return null;
    return sliceMesh(mesh, sliceDim, sliceVal);
  }, [mesh, sliceDim, sliceVal]);

  // Handle Preset Selection
  const handlePresetSelect = (preset: ViewPreset) => {
    setActivePreset(preset);
    setPanX(0);
    setPanY(0);
    cameraRef.current.panX = 0;
    cameraRef.current.panY = 0;
    if (preset === "iso") {
      setYaw(40);
      setPitch(32);
      setZoom(1.0);
      cameraRef.current.yaw = 40;
      cameraRef.current.pitch = 32;
      cameraRef.current.zoom = 1.0;
    } else if (preset === "smile") {
      setYaw(0);
      setPitch(6);
      setZoom(1.1);
      cameraRef.current.yaw = 0;
      cameraRef.current.pitch = 6;
      cameraRef.current.zoom = 1.1;
    } else if (preset === "term") {
      setYaw(90);
      setPitch(6);
      setZoom(1.1);
      cameraRef.current.yaw = 90;
      cameraRef.current.pitch = 6;
      cameraRef.current.zoom = 1.1;
    } else if (preset === "contour") {
      setYaw(0);
      setPitch(88);
      setZoom(1.0);
      cameraRef.current.yaw = 0;
      cameraRef.current.pitch = 88;
      cameraRef.current.zoom = 1.0;
    }
  };

  // Reset Camera View
  const handleResetCamera = () => {
    setActivePreset("iso");
    setYaw(40);
    setPitch(32);
    setZoom(1.0);
    setPanX(0);
    setPanY(0);
    setIsAutoRotating(false);
    setSelectedPoint(null);
    setHoveredPoint(null);
    cameraRef.current.yaw = 40;
    cameraRef.current.pitch = 32;
    cameraRef.current.zoom = 1.0;
    cameraRef.current.panX = 0;
    cameraRef.current.panY = 0;
    cameraRef.current.isAutoRotating = false;
    cameraRef.current.selectedPoint = null;
    cameraRef.current.hoveredPoint = null;
  };

  // ==========================================================================
  // Mouse & Touch Orbit / Pan / Zoom Event Handlers
  // ==========================================================================

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    isDraggingRef.current = true;
    dragModeRef.current = e.shiftKey || e.button === 2 ? "pan" : "orbit";
    lastMousePosRef.current = { x: e.clientX, y: e.clientY };
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    if (isDraggingRef.current) {
      const dx = e.clientX - lastMousePosRef.current.x;
      const dy = e.clientY - lastMousePosRef.current.y;
      lastMousePosRef.current = { x: e.clientX, y: e.clientY };

      if (dragModeRef.current === "orbit") {
        const nextYaw = (cameraRef.current.yaw + dx * 0.6) % 360;
        const nextPitch = Math.max(5, Math.min(88, cameraRef.current.pitch + dy * 0.4));
        cameraRef.current.yaw = nextYaw;
        cameraRef.current.pitch = nextPitch;
        setYaw(nextYaw);
        setPitch(nextPitch);
      } else {
        const nextPanX = cameraRef.current.panX + dx * 0.8;
        const nextPanY = cameraRef.current.panY + dy * 0.8;
        cameraRef.current.panX = nextPanX;
        cameraRef.current.panY = nextPanY;
        setPanX(nextPanX);
        setPanY(nextPanY);
      }
    } else {
      // Raycast / find nearest surface point to mouse cursor
      const rect = canvas.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;

      // Project grid vertices and find closest
      let closestPt: VolSurface3DPoint | null = null;
      let minDistance = 24; // Pixel threshold

      const widthPx = canvas.width;
      const heightPx = canvas.height;
      const centerX = widthPx / 2 + cameraRef.current.panX;
      const centerY = heightPx * 0.56 + cameraRef.current.panY;

      const radYaw = (cameraRef.current.yaw * Math.PI) / 180;
      const radPitch = (cameraRef.current.pitch * Math.PI) / 180;
      const cosYaw = Math.cos(radYaw);
      const sinYaw = Math.sin(radYaw);
      const cosPitch = Math.cos(radPitch);
      const sinPitch = Math.sin(radPitch);

      const ivSpan = mesh.maxIv - mesh.minIv || 0.1;
      const strikeSpan = mesh.maxStrike - mesh.minStrike || 1;
      const dteSpan = mesh.maxDte - mesh.minDte || 1;

      const boxWidth = 260;
      const boxDepth = 220;
      const boxHeight = 130;

      for (let j = 0; j < mesh.dtes.length; j++) {
        const dte = mesh.dtes[j];
        const normZ = ((dte - mesh.minDte) / dteSpan) * 2 - 1;
        const z3d = normZ * (boxDepth / 2);

        for (let i = 0; i < mesh.strikes.length; i++) {
          const strike = mesh.strikes[i];
          const iv = mesh.grid[j][i];
          const normX = ((strike - mesh.minStrike) / strikeSpan) * 2 - 1;
          const normY = (iv - mesh.minIv) / ivSpan;

          const x3d = normX * (boxWidth / 2);
          const y3d = normY * boxHeight;

          // 3D rotation
          const rx = x3d * cosYaw - z3d * sinYaw;
          const rz = x3d * sinYaw + z3d * cosYaw;
          const ry = y3d * cosPitch - rz * sinPitch;

          const scale = (cameraRef.current.zoom * widthPx) / 480;
          const sx = centerX + rx * scale;
          const sy = centerY - ry * scale;

          const dist = Math.hypot(mouseX - sx, mouseY - sy);
          if (dist < minDistance) {
            minDistance = dist;
            closestPt = {
              strike,
              dte,
              iv,
              moneyness: Number((strike / mesh.spotPrice).toFixed(3)),
            };
          }
        }
      }

      cameraRef.current.hoveredPoint = closestPt;
      setHoveredPoint(closestPt);
      if (onHoverPoint) onHoverPoint(closestPt);
    }
  };

  const handleMouseUp = () => {
    isDraggingRef.current = false;
  };

  const handleTouchStart = (e: React.TouchEvent<HTMLCanvasElement>) => {
    if (e.touches.length === 1) {
      isDraggingRef.current = true;
      dragModeRef.current = "orbit";
      lastMousePosRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY };
    } else if (e.touches.length === 2) {
      isDraggingRef.current = true;
      dragModeRef.current = "pan";
      const dx = e.touches[0].clientX - e.touches[1].clientX;
      const dy = e.touches[0].clientY - e.touches[1].clientY;
      lastTouchDistRef.current = Math.hypot(dx, dy);
    }
  };

  const handleTouchMove = (e: React.TouchEvent<HTMLCanvasElement>) => {
    if (!isDraggingRef.current) return;
    if (e.touches.length === 1) {
      const dx = e.touches[0].clientX - lastMousePosRef.current.x;
      const dy = e.touches[0].clientY - lastMousePosRef.current.y;
      lastMousePosRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY };

      const nextYaw = (cameraRef.current.yaw + dx * 0.6) % 360;
      const nextPitch = Math.max(5, Math.min(88, cameraRef.current.pitch + dy * 0.4));
      cameraRef.current.yaw = nextYaw;
      cameraRef.current.pitch = nextPitch;
      setYaw(nextYaw);
      setPitch(nextPitch);
    } else if (e.touches.length === 2 && lastTouchDistRef.current > 0) {
      const dx = e.touches[0].clientX - e.touches[1].clientX;
      const dy = e.touches[0].clientY - e.touches[1].clientY;
      const newDist = Math.hypot(dx, dy);
      const zoomFactor = newDist / lastTouchDistRef.current;
      lastTouchDistRef.current = newDist;

      const nextZoom = Math.max(
        0.4,
        Math.min(3.0, Number((cameraRef.current.zoom * (zoomFactor > 1 ? 1.03 : 0.97)).toFixed(2)))
      );
      cameraRef.current.zoom = nextZoom;
      setZoom(nextZoom);
    }
  };

  const handleTouchEnd = () => {
    isDraggingRef.current = false;
    lastTouchDistRef.current = 0;
  };

  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const zoomFactor = e.deltaY > 0 ? 0.92 : 1.08;
    const nextZoom = Math.max(0.4, Math.min(3.0, Number((cameraRef.current.zoom * zoomFactor).toFixed(2))));
    cameraRef.current.zoom = nextZoom;
    setZoom(nextZoom);
  };

  const handleCanvasClick = () => {
    if (cameraRef.current.hoveredPoint) {
      setSelectedPoint(cameraRef.current.hoveredPoint);
      if (onSelectPoint) onSelectPoint(cameraRef.current.hoveredPoint);
    }
  };

  // ==========================================================================
  // Render Loop: Dual Mode (Native WebGL Shader Pipeline + Canvas 2.5D Fallback)
  // ==========================================================================
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let isSubscribed = true;

    const render = () => {
      if (!isSubscribed) return;

      // Handle auto-rotate mutably without triggering 60fps React state re-renders
      if (cameraRef.current.isAutoRotating) {
        cameraRef.current.yaw = (cameraRef.current.yaw + 0.35) % 360;
      }

      const {
        yaw: curYaw,
        pitch: curPitch,
        panX: curPanX,
        panY: curPanY,
        zoom: curZoom,
        colormap: curColormap,
        renderMode: curRenderMode,
        sliceDim: curSliceDim,
        sliceVal: curSliceVal,
        hoveredPoint: curHoveredPoint,
        selectedPoint: curSelectedPoint,
        mesh: curMesh,
      } = cameraRef.current;

      const widthPx = canvas.width;
      const heightPx = canvas.height;

      // Background Clear
      ctx.fillStyle = theme.base;
      ctx.fillRect(0, 0, widthPx, heightPx);

      // Camera coordinates & projections
      const centerX = widthPx / 2 + curPanX;
      const centerY = heightPx * 0.56 + curPanY;

      const radYaw = (curYaw * Math.PI) / 180;
      const radPitch = (curPitch * Math.PI) / 180;
      const cosYaw = Math.cos(radYaw);
      const sinYaw = Math.sin(radYaw);
      const cosPitch = Math.cos(radPitch);
      const sinPitch = Math.sin(radPitch);

      const project3D = (x: number, y: number, z: number) => {
        // Rotate Y (azimuth)
        const rx = x * cosYaw - z * sinYaw;
        const rz = x * sinYaw + z * cosYaw;
        // Tilt X (elevation)
        const ry = y * cosPitch - rz * sinPitch;
        const finalZ = y * sinPitch + rz * cosPitch;

        const scale = (curZoom * widthPx) / 480;
        const sx = centerX + rx * scale;
        const sy = centerY - ry * scale;
        return { x: sx, y: sy, depth: finalZ };
      };

      const ivSpan = curMesh.maxIv - curMesh.minIv || 0.1;
      const strikeSpan = curMesh.maxStrike - curMesh.minStrike || 1;
      const dteSpan = curMesh.maxDte - curMesh.minDte || 1;

      const boxWidth = 260;
      const boxDepth = 220;
      const boxHeight = 130;

      // 1. Draw 3D Bounding Box & Coordinate Floor Grid
      ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
      ctx.lineWidth = 1;

      const hw = boxWidth / 2;
      const hd = boxDepth / 2;

      // Floor grid lines along Strikes
      for (let i = 0; i <= 6; i++) {
        const gx = -hw + (i / 6) * boxWidth;
        const p1 = project3D(gx, 0, -hd);
        const p2 = project3D(gx, 0, hd);
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.stroke();
      }

      // Floor grid lines along DTEs
      for (let j = 0; j <= 6; j++) {
        const gz = -hd + (j / 6) * boxDepth;
        const p1 = project3D(-hw, 0, gz);
        const p2 = project3D(hw, 0, gz);
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.stroke();
      }

      // Bounding box corner pillars
      const corners = [
        [-hw, -hd],
        [hw, -hd],
        [hw, hd],
        [-hw, hd],
      ];
      ctx.strokeStyle = "rgba(255, 255, 255, 0.12)";
      for (const [cxCorner, czCorner] of corners) {
        const b = project3D(cxCorner, 0, czCorner);
        const t = project3D(cxCorner, boxHeight, czCorner);
        ctx.beginPath();
        ctx.moveTo(b.x, b.y);
        ctx.lineTo(t.x, t.y);
        ctx.stroke();
      }

      // Top bounding rectangle
      const t0 = project3D(-hw, boxHeight, -hd);
      const t1 = project3D(hw, boxHeight, -hd);
      const t2 = project3D(hw, boxHeight, hd);
      const t3 = project3D(-hw, boxHeight, hd);
      ctx.beginPath();
      ctx.moveTo(t0.x, t0.y);
      ctx.lineTo(t1.x, t1.y);
      ctx.lineTo(t2.x, t2.y);
      ctx.lineTo(t3.x, t3.y);
      ctx.closePath();
      ctx.stroke();

      // 2. Draw Spot Price Reference Plane (K = Spot)
      const normSpotX = ((curMesh.spotPrice - curMesh.minStrike) / strikeSpan) * 2 - 1;
      if (normSpotX >= -1.05 && normSpotX <= 1.05) {
        const spotX3d = normSpotX * hw;
        const sp_b1 = project3D(spotX3d, 0, -hd);
        const sp_b2 = project3D(spotX3d, 0, hd);
        const sp_t1 = project3D(spotX3d, boxHeight, -hd);
        const sp_t2 = project3D(spotX3d, boxHeight, hd);

        ctx.fillStyle = "rgba(56, 189, 248, 0.08)";
        ctx.beginPath();
        ctx.moveTo(sp_b1.x, sp_b1.y);
        ctx.lineTo(sp_b2.x, sp_b2.y);
        ctx.lineTo(sp_t2.x, sp_t2.y);
        ctx.lineTo(sp_t1.x, sp_t1.y);
        ctx.closePath();
        ctx.fill();

        ctx.strokeStyle = "rgba(56, 189, 248, 0.4)";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }

      // 3. Draw Cross-Section Slicing Plane
      if (curSliceDim === "dte") {
        const normZ = ((curSliceVal - curMesh.minDte) / dteSpan) * 2 - 1;
        const sliceZ3d = normZ * hd;
        const sc_b1 = project3D(-hw, 0, sliceZ3d);
        const sc_b2 = project3D(hw, 0, sliceZ3d);
        const sc_t1 = project3D(-hw, boxHeight, sliceZ3d);
        const sc_t2 = project3D(hw, boxHeight, sliceZ3d);

        ctx.fillStyle = "rgba(245, 158, 11, 0.1)";
        ctx.beginPath();
        ctx.moveTo(sc_b1.x, sc_b1.y);
        ctx.lineTo(sc_b2.x, sc_b2.y);
        ctx.lineTo(sc_t2.x, sc_t2.y);
        ctx.lineTo(sc_t1.x, sc_t1.y);
        ctx.closePath();
        ctx.fill();

        ctx.strokeStyle = "#f59e0b";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      } else if (curSliceDim === "strike") {
        const normX = ((curSliceVal - curMesh.minStrike) / strikeSpan) * 2 - 1;
        const sliceX3d = normX * hw;
        const sc_b1 = project3D(sliceX3d, 0, -hd);
        const sc_b2 = project3D(sliceX3d, 0, hd);
        const sc_t1 = project3D(sliceX3d, boxHeight, -hd);
        const sc_t2 = project3D(sliceX3d, boxHeight, hd);

        ctx.fillStyle = "rgba(16, 185, 129, 0.1)";
        ctx.beginPath();
        ctx.moveTo(sc_b1.x, sc_b1.y);
        ctx.lineTo(sc_b2.x, sc_b2.y);
        ctx.lineTo(sc_t2.x, sc_t2.y);
        ctx.lineTo(sc_t1.x, sc_t1.y);
        ctx.closePath();
        ctx.fill();

        ctx.strokeStyle = "#10b981";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }

      // 4. Transform & Project All Surface Vertices
      const projectedGrid: { x: number; y: number; depth: number; iv: number; strike: number; dte: number }[][] = [];
      for (let j = 0; j < curMesh.dtes.length; j++) {
        const dte = curMesh.dtes[j];
        const normZ = ((dte - curMesh.minDte) / dteSpan) * 2 - 1;
        const z3d = normZ * hd;
        const row: { x: number; y: number; depth: number; iv: number; strike: number; dte: number }[] = [];

        for (let i = 0; i < curMesh.strikes.length; i++) {
          const strike = curMesh.strikes[i];
          const iv = curMesh.grid[j][i];
          const normX = ((strike - curMesh.minStrike) / strikeSpan) * 2 - 1;
          const normY = (iv - curMesh.minIv) / ivSpan;

          const x3d = normX * hw;
          const y3d = normY * boxHeight;

          const pt = project3D(x3d, y3d, z3d);
          row.push({ ...pt, iv, strike, dte });
        }
        projectedGrid.push(row);
      }

      // 5. Build Quad Facets & Depth-Sort (Painter's Algorithm)
      interface QuadFacet {
        p00: { x: number; y: number; depth: number; iv: number; strike: number; dte: number };
        p10: { x: number; y: number; depth: number; iv: number; strike: number; dte: number };
        p11: { x: number; y: number; depth: number; iv: number; strike: number; dte: number };
        p01: { x: number; y: number; depth: number; iv: number; strike: number; dte: number };
        avgDepth: number;
        avgIv: number;
      }

      const quads: QuadFacet[] = [];
      for (let j = 0; j < curMesh.dtes.length - 1; j++) {
        for (let i = 0; i < curMesh.strikes.length - 1; i++) {
          const p00 = projectedGrid[j][i];
          const p10 = projectedGrid[j][i + 1];
          const p11 = projectedGrid[j + 1][i + 1];
          const p01 = projectedGrid[j + 1][i];

          const avgDepth = (p00.depth + p10.depth + p11.depth + p01.depth) / 4;
          const avgIv = (p00.iv + p10.iv + p11.iv + p01.iv) / 4;

          quads.push({ p00, p10, p11, p01, avgDepth, avgIv });
        }
      }

      // Sort back-to-front by average depth
      quads.sort((a, b) => b.avgDepth - a.avgDepth);

      // 6. Draw Surface Quads & Wireframe Lines
      const shouldDrawSurface = curRenderMode === "surface" || curRenderMode === "surface-wireframe";
      const shouldDrawWireframe = curRenderMode === "wireframe" || curRenderMode === "surface-wireframe";

      if (shouldDrawSurface) {
        for (const quad of quads) {
          const normIv = (quad.avgIv - curMesh.minIv) / ivSpan;
          const color = sampleColormap(curColormap, normIv);

          // Simple directional lighting factor based on elevation
          const lightFactor = 0.85 + 0.15 * Math.sin(normIv * Math.PI);
          const r = Math.min(255, Math.round(color.r * lightFactor));
          const g = Math.min(255, Math.round(color.g * lightFactor));
          const b = Math.min(255, Math.round(color.b * lightFactor));

          ctx.fillStyle = `rgba(${r}, ${g}, ${b}, 0.88)`;
          ctx.beginPath();
          ctx.moveTo(quad.p00.x, quad.p00.y);
          ctx.lineTo(quad.p10.x, quad.p10.y);
          ctx.lineTo(quad.p11.x, quad.p11.y);
          ctx.lineTo(quad.p01.x, quad.p01.y);
          ctx.closePath();
          ctx.fill();

          if (shouldDrawWireframe) {
            ctx.strokeStyle = "rgba(255, 255, 255, 0.18)";
            ctx.lineWidth = 0.75;
            ctx.stroke();
          }
        }
      } else if (renderMode === "wireframe") {
        // Wireframe only mode
        ctx.lineWidth = 1.2;
        // Strike lines
        for (let j = 0; j < mesh.dtes.length; j++) {
          ctx.beginPath();
          for (let i = 0; i < mesh.strikes.length; i++) {
            const pt = projectedGrid[j][i];
            const normIv = (pt.iv - mesh.minIv) / ivSpan;
            const color = sampleColormap(colormap, normIv);
            ctx.strokeStyle = color.rgba(0.85);

            if (i === 0) ctx.moveTo(pt.x, pt.y);
            else ctx.lineTo(pt.x, pt.y);
          }
          ctx.stroke();
        }

        // DTE lines
        for (let i = 0; i < mesh.strikes.length; i++) {
          ctx.beginPath();
          for (let j = 0; j < mesh.dtes.length; j++) {
            const pt = projectedGrid[j][i];
            const normIv = (pt.iv - mesh.minIv) / ivSpan;
            const color = sampleColormap(colormap, normIv);
            ctx.strokeStyle = color.rgba(0.85);

            if (j === 0) ctx.moveTo(pt.x, pt.y);
            else ctx.lineTo(pt.x, pt.y);
          }
          ctx.stroke();
        }
      }

      // 7. Draw Points / Scatter Mode
      if (renderMode === "points") {
        for (let j = 0; j < mesh.dtes.length; j++) {
          for (let i = 0; i < mesh.strikes.length; i++) {
            const pt = projectedGrid[j][i];
            const normIv = (pt.iv - mesh.minIv) / ivSpan;
            const color = sampleColormap(colormap, normIv);

            ctx.fillStyle = color.hex;
            ctx.beginPath();
            ctx.arc(pt.x, pt.y, 3, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      }

      // 8. Highlight Cross-Section Slice Contour Curve
      if (sliceDim === "dte") {
        let bestIdx = 0;
        let minDiff = Infinity;
        for (let j = 0; j < mesh.dtes.length; j++) {
          const diff = Math.abs(mesh.dtes[j] - sliceVal);
          if (diff < minDiff) {
            minDiff = diff;
            bestIdx = j;
          }
        }
        const row = projectedGrid[bestIdx];
        if (row && row.length > 0) {
          ctx.strokeStyle = "#f59e0b";
          ctx.lineWidth = 3;
          ctx.beginPath();
          for (let i = 0; i < row.length; i++) {
            if (i === 0) ctx.moveTo(row[i].x, row[i].y);
            else ctx.lineTo(row[i].x, row[i].y);
          }
          ctx.stroke();

          // Highlight points
          for (const pt of row) {
            ctx.fillStyle = "#f59e0b";
            ctx.beginPath();
            ctx.arc(pt.x, pt.y, 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
          }
        }
      } else if (curSliceDim === "strike") {
        let bestIdx = 0;
        let minDiff = Infinity;
        for (let i = 0; i < curMesh.strikes.length; i++) {
          const diff = Math.abs(curMesh.strikes[i] - curSliceVal);
          if (diff < minDiff) {
            minDiff = diff;
            bestIdx = i;
          }
        }
        ctx.strokeStyle = "#10b981";
        ctx.lineWidth = 3;
        ctx.beginPath();
        for (let j = 0; j < curMesh.dtes.length; j++) {
          const pt = projectedGrid[j][bestIdx];
          if (pt) {
            if (j === 0) ctx.moveTo(pt.x, pt.y);
            else ctx.lineTo(pt.x, pt.y);
          }
        }
        ctx.stroke();
      }

      // 9. Draw Axis Labels & Ticks
      ctx.fillStyle = theme.textSecondary;
      ctx.font = "10px monospace";
      ctx.textAlign = "center";

      // X Axis (Strike)
      const ax_x1 = project3D(-hw, 0, hd + 18);
      const ax_x2 = project3D(hw, 0, hd + 18);
      ctx.fillText(`Strike ($${curMesh.minStrike} → $${curMesh.maxStrike})`, (ax_x1.x + ax_x2.x) / 2, (ax_x1.y + ax_x2.y) / 2 + 12);

      // Z Axis (DTE)
      const ax_z1 = project3D(hw + 18, 0, -hd);
      const ax_z2 = project3D(hw + 18, 0, hd);
      ctx.fillText(`Expiry (${curMesh.minDte}d → ${curMesh.maxDte}d DTE)`, (ax_z1.x + ax_z2.x) / 2, (ax_z1.y + ax_z2.y) / 2 + 12);

      // Y Axis (IV %)
      const ax_y1 = project3D(-hw - 15, 0, -hd);
      const ax_y2 = project3D(-hw - 15, boxHeight, -hd);
      ctx.fillText(`IV (${(curMesh.minIv * 100).toFixed(0)}% - ${(curMesh.maxIv * 100).toFixed(0)}%)`, ax_y2.x - 10, (ax_y1.y + ax_y2.y) / 2);

      // 10. Selected & Hovered Point Indicator Pin
      const targetPin = curHoveredPoint || curSelectedPoint;
      if (targetPin) {
        const normX = ((targetPin.strike - curMesh.minStrike) / strikeSpan) * 2 - 1;
        const normY = (targetPin.iv - curMesh.minIv) / ivSpan;
        const normZ = ((targetPin.dte - curMesh.minDte) / dteSpan) * 2 - 1;

        const x3d = normX * hw;
        const y3d = normY * boxHeight;
        const z3d = normZ * hd;

        const basePt = project3D(x3d, y3d, z3d);
        const topPt = project3D(x3d, y3d + 20, z3d);

        ctx.strokeStyle = "#38bdf8";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(basePt.x, basePt.y);
        ctx.lineTo(topPt.x, topPt.y);
        ctx.stroke();

        ctx.fillStyle = "#38bdf8";
        ctx.beginPath();
        ctx.arc(topPt.x, topPt.y, 4.5, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = "rgba(18, 22, 28, 0.92)";
        ctx.strokeStyle = "#38bdf8";
        ctx.lineWidth = 1;
        const badgeW = 120;
        const badgeH = 34;
        ctx.fillRect(topPt.x - badgeW / 2, topPt.y - badgeH - 6, badgeW, badgeH);
        ctx.strokeRect(topPt.x - badgeW / 2, topPt.y - badgeH - 6, badgeW, badgeH);

        ctx.fillStyle = "#ffffff";
        ctx.font = "bold 10px monospace";
        ctx.textAlign = "center";
        ctx.fillText(`K:$${targetPin.strike} | ${(targetPin.iv * 100).toFixed(1)}% IV`, topPt.x, topPt.y - badgeH + 8);
        ctx.fillStyle = theme.textSecondary;
        ctx.font = "9px monospace";
        ctx.fillText(`DTE: ${targetPin.dte}d (${(targetPin.strike / curMesh.spotPrice).toFixed(2)}x)`, topPt.x, topPt.y - badgeH + 20);
      }

      // Schedule next frame
      animFrameIdRef.current = requestAnimationFrame(render);
    };

    animFrameIdRef.current = requestAnimationFrame(render);

    // Global pointer up and window resize listeners
    const handleGlobalPointerUp = () => {
      isDraggingRef.current = false;
      lastTouchDistRef.current = 0;
    };
    const handleResize = () => {
      // Re-trigger layout alignment if needed
    };

    window.addEventListener("mouseup", handleGlobalPointerUp);
    window.addEventListener("touchend", handleGlobalPointerUp);
    window.addEventListener("touchcancel", handleGlobalPointerUp);
    window.addEventListener("resize", handleResize);

    return () => {
      isSubscribed = false;
      if (animFrameIdRef.current) {
        cancelAnimationFrame(animFrameIdRef.current);
        animFrameIdRef.current = null;
      }
      window.removeEventListener("mouseup", handleGlobalPointerUp);
      window.removeEventListener("touchend", handleGlobalPointerUp);
      window.removeEventListener("touchcancel", handleGlobalPointerUp);
      window.removeEventListener("resize", handleResize);
      disposeCanvas(canvas);
    };
  }, [mesh]);

  return (
    <div
      ref={containerRef}
      className={`card ${className}`}
      data-testid="vol-surface-3d-container"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--s-4)",
        padding: "var(--s-4)",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--r-md)",
        width,
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
          <Layers style={{ width: 22, height: 22, color: "var(--accent)" }} />
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
              3D Volatility Surface
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
                {mesh.symbol} ${mesh.spotPrice.toFixed(2)}
              </span>
            </h3>
            <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)" }}>
              Interactive 3D implied volatility mesh: Strike (X) × Implied Vol (Y) × Expiration DTE (Z)
            </div>
          </div>
          {points == null && volResponse == null && <DemoDataBadge />}
        </div>

        {/* Mode Indicator & Action Buttons */}
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <div
            data-testid="vol-render-mode"
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
            {isFallbackActive ? "Canvas 3D Fallback Mode" : "Canvas 3D Renderer"}
          </div>

          <Button
            variant="neutral"
            onClick={handleResetCamera}
            title="Reset 3D camera to default view"
            data-testid="vol-reset-camera-btn"
          >
            <RotateCcw style={{ width: 14, height: 14 }} />
          </Button>
        </div>
      </div>

      {/* Surface Metric Summary Cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: "var(--s-3)",
        }}
      >
        <div
          data-testid="metric-spot"
          style={{
            padding: "var(--s-3)",
            background: "var(--surface-2)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
          }}
        >
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginBottom: 2 }}>
            SPOT PRICE
          </div>
          <div style={{ fontSize: "var(--t-callout)", fontWeight: 700, color: "var(--accent)" }}>
            ${mesh.spotPrice.toFixed(2)}
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-secondary)", marginTop: 2 }}>
            Range: ${mesh.minStrike} - ${mesh.maxStrike}
          </div>
        </div>

        <div
          data-testid="metric-atm-iv"
          style={{
            padding: "var(--s-3)",
            background: "var(--surface-2)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
            borderLeft: "3px solid var(--accent)",
          }}
        >
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginBottom: 2 }}>
            ATM IMPLIED VOL
          </div>
          <div style={{ fontSize: "var(--t-callout)", fontWeight: 700, color: "var(--text-primary)" }}>
            {(metrics.atmIv * 100).toFixed(1)}%
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-secondary)", marginTop: 2 }}>
            Min: {(metrics.minIv * 100).toFixed(1)}% | Max: {(metrics.maxIv * 100).toFixed(1)}%
          </div>
        </div>

        <div
          data-testid="metric-skew"
          style={{
            padding: "var(--s-3)",
            background: "var(--surface-2)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
            borderLeft: `3px solid ${(metrics.skew25d ?? 0) >= 0 ? "var(--caution)" : "var(--growth)"}`,
          }}
        >
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginBottom: 2 }}>
            25Δ PUT-CALL SKEW {metrics.skew25d != null ? (metrics.skew25dIsReal ? "(chain)" : "(proxy)") : ""}
          </div>
          <div
            data-testid="metric-skew-value"
            style={{
              fontSize: "var(--t-callout)",
              fontWeight: 700,
              color: (metrics.skew25d ?? 0) >= 0 ? "var(--caution)" : "var(--growth)",
            }}
          >
            {metrics.skew25d != null
              ? `${metrics.skew25d > 0 ? "+" : ""}${(metrics.skew25d * 100).toFixed(2)}%`
              : "—"}
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-secondary)", marginTop: 2 }}>
            {metrics.skew25d == null
              ? "Unavailable this cycle"
              : metrics.skew25d > 0
                ? "Put skew premium"
                : "Call skew / flat"}
          </div>
        </div>

        <div
          data-testid="metric-term-slope"
          style={{
            padding: "var(--s-3)",
            background: "var(--surface-2)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
          }}
        >
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginBottom: 2 }}>
            TERM STRUCTURE SLOPE
          </div>
          <div
            style={{
              fontSize: "var(--t-callout)",
              fontWeight: 700,
              color: metrics.termSlope >= 0 ? "var(--growth)" : "var(--decline)",
            }}
          >
            {metrics.termSlope > 0 ? "+" : ""}
            {(metrics.termSlope * 100).toFixed(2)}%
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-secondary)", marginTop: 2 }}>
            {metrics.termSlope >= 0 ? "Contango (Normal)" : "Backwardation (Inverted)"}
          </div>
        </div>
      </div>

      {/* Interactive Controls Bar */}
      {showControls && (
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "var(--s-3)",
            padding: "var(--s-3)",
            background: "var(--surface-2)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
          }}
        >
          {/* Preset Buttons */}
          <div style={{ display: "flex", gap: "var(--s-1-5)", alignItems: "center" }}>
            <span style={{ fontSize: "var(--t-micro)", fontWeight: 600, color: "var(--text-muted)", marginRight: 4 }}>
              Presets:
            </span>
            {[
              { id: "iso", label: "3D Isometric" },
              { id: "smile", label: "Smile (K-IV)" },
              { id: "term", label: "Term (DTE-IV)" },
              { id: "contour", label: "Contour (Top)" },
            ].map((p) => (
              <button
                key={p.id}
                onClick={() => handlePresetSelect(p.id as ViewPreset)}
                data-testid={`preset-${p.id}`}
                style={{
                  padding: "4px 10px",
                  background: activePreset === p.id ? "var(--accent)" : "var(--surface-3)",
                  color: activePreset === p.id ? "#000000" : "var(--text-primary)",
                  border: `1px solid ${activePreset === p.id ? "var(--accent)" : "var(--border)"}`,
                  borderRadius: "var(--r-sm)",
                  fontSize: "var(--t-micro)",
                  fontWeight: activePreset === p.id ? 700 : 500,
                  cursor: "pointer",
                }}
              >
                {p.label}
              </button>
            ))}
          </div>

          {/* Render Mode & Colormap Selectors */}
          <div style={{ display: "flex", gap: "var(--s-3)", alignItems: "center" }}>
            {/* Render Mode */}
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)" }}>Mode:</span>
              <select
                value={renderMode}
                onChange={(e) => setRenderMode(e.target.value as RenderMode)}
                data-testid="vol-rendermode-select"
                style={{
                  padding: "4px 8px",
                  background: "var(--surface-3)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--r-sm)",
                  fontSize: "var(--t-micro)",
                }}
              >
                <option value="surface-wireframe">Surface + Wireframe</option>
                <option value="surface">Smooth Surface</option>
                <option value="wireframe">Wireframe Only</option>
                <option value="points">Scatter Points</option>
              </select>
            </div>

            {/* Colormap */}
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)" }}>Colormap:</span>
              <select
                value={colormap}
                onChange={(e) => setColormap(e.target.value as ColormapType)}
                data-testid="vol-colormap-select"
                style={{
                  padding: "4px 8px",
                  background: "var(--surface-3)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--r-sm)",
                  fontSize: "var(--t-micro)",
                }}
              >
                <option value="cyan-amber">Pilots Cyan-Amber</option>
                <option value="plasma">Plasma / Magma</option>
                <option value="viridis">Viridis</option>
                <option value="emerald">Emerald Peak</option>
              </select>
            </div>

            {/* Auto-Rotate Toggle */}
            <button
              onClick={() => setIsAutoRotating((prev) => !prev)}
              data-testid="vol-autorotate-btn"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 4,
                padding: "4px 10px",
                background: isAutoRotating ? "rgba(56, 189, 248, 0.2)" : "var(--surface-3)",
                color: isAutoRotating ? "var(--accent)" : "var(--text-secondary)",
                border: `1px solid ${isAutoRotating ? "var(--accent)" : "var(--border)"}`,
                borderRadius: "var(--r-sm)",
                fontSize: "var(--t-micro)",
                cursor: "pointer",
              }}
            >
              {isAutoRotating ? <Pause style={{ width: 12, height: 12 }} /> : <Play style={{ width: 12, height: 12 }} />}
              Auto-Rotate
            </button>
          </div>
        </div>
      )}

      {/* Main 3D Canvas Viewport */}
      <div
        style={{
          position: "relative",
          width: "100%",
          height,
          background: "var(--base)",
          borderRadius: "var(--r-md)",
          overflow: "hidden",
          border: "1px solid var(--border)",
        }}
      >
        <canvas
          ref={canvasRef}
          width={800}
          height={540}
          style={{ width: "100%", height: "100%", cursor: isDraggingRef.current ? "grabbing" : "grab" }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
          onTouchCancel={handleTouchEnd}
          onWheel={handleWheel}
          onClick={handleCanvasClick}
          data-testid="vol-surface-canvas"
        />

        {/* Floating Zoom & Control Overlay */}
        <div
          style={{
            position: "absolute",
            bottom: 12,
            right: 12,
            display: "flex",
            flexDirection: "column",
            gap: 6,
            background: "rgba(18, 22, 28, 0.85)",
            backdropFilter: "blur(6px)",
            padding: 6,
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
          }}
        >
          <button
            onClick={() => setZoom((z) => Math.min(3.0, z + 0.2))}
            title="Zoom In"
            data-testid="vol-zoom-in"
            style={{
              background: "transparent",
              border: "none",
              color: "var(--text-primary)",
              cursor: "pointer",
              padding: 4,
            }}
          >
            <ZoomIn style={{ width: 16, height: 16 }} />
          </button>
          <button
            onClick={() => setZoom((z) => Math.max(0.4, z - 0.2))}
            title="Zoom Out"
            data-testid="vol-zoom-out"
            style={{
              background: "transparent",
              border: "none",
              color: "var(--text-primary)",
              cursor: "pointer",
              padding: 4,
            }}
          >
            <ZoomOut style={{ width: 16, height: 16 }} />
          </button>
        </div>

        {/* Colormap Legend Bar (Bottom Left) */}
        <div
          data-testid="vol-colormap-legend"
          style={{
            position: "absolute",
            bottom: 12,
            left: 12,
            display: "flex",
            alignItems: "center",
            gap: 8,
            background: "rgba(18, 22, 28, 0.85)",
            backdropFilter: "blur(6px)",
            padding: "6px 12px",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
          }}
        >
          <span style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)" }}>
            {(mesh.minIv * 100).toFixed(0)}% IV
          </span>
          <div
            style={{
              width: 100,
              height: 10,
              borderRadius: 3,
              background: `linear-gradient(to right, ${sampleColormap(colormap, 0).hex}, ${sampleColormap(colormap, 0.33).hex}, ${sampleColormap(colormap, 0.66).hex}, ${sampleColormap(colormap, 1.0).hex})`,
            }}
          />
          <span style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)" }}>
            {(mesh.maxIv * 100).toFixed(0)}% IV
          </span>
        </div>

        {/* Hover Readout HUD (Top Right) */}
        {hoveredPoint && (
          <div
            data-testid="vol-hover-hud"
            style={{
              position: "absolute",
              top: 12,
              right: 12,
              background: "rgba(18, 22, 28, 0.9)",
              backdropFilter: "blur(8px)",
              padding: "8px 12px",
              borderRadius: "var(--r-sm)",
              border: "1px solid var(--accent)",
              fontSize: "var(--t-micro)",
              color: "var(--text-primary)",
              display: "flex",
              flexDirection: "column",
              gap: 2,
            }}
          >
            <div style={{ fontWeight: 700, color: "var(--accent)" }}>
              Strike: ${hoveredPoint.strike.toFixed(1)} ({((hoveredPoint.strike / mesh.spotPrice) * 100).toFixed(1)}% Moneyness)
            </div>
            <div>DTE: {hoveredPoint.dte} days</div>
            <div style={{ fontWeight: 600 }}>Implied Volatility: {(hoveredPoint.iv * 100).toFixed(2)}%</div>
          </div>
        )}
      </div>

      {/* Cross-Section Slicing Section */}
      {showCrossSection && (
        <div
          data-testid="vol-slice-section"
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "var(--s-3)",
            padding: "var(--s-4)",
            background: "var(--surface-2)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Scissors style={{ width: 18, height: 18, color: "var(--caution)" }} />
              <h4 style={{ margin: 0, fontSize: "var(--t-body)", fontWeight: 600, color: "var(--text-primary)" }}>
                Cross-Section 2D Slicing Inspector
              </h4>
            </div>

            {/* Slicing Dimension Selector */}
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <button
                onClick={() => setSliceDim("none")}
                data-testid="slice-dim-none"
                style={{
                  padding: "4px 10px",
                  background: sliceDim === "none" ? "var(--surface-3)" : "transparent",
                  color: sliceDim === "none" ? "var(--text-primary)" : "var(--text-muted)",
                  border: `1px solid ${sliceDim === "none" ? "var(--border-strong)" : "transparent"}`,
                  borderRadius: "var(--r-sm)",
                  fontSize: "var(--t-micro)",
                  cursor: "pointer",
                }}
              >
                None (Full 3D)
              </button>
              <button
                onClick={() => {
                  setSliceDim("dte");
                  setSliceVal(mesh.dtes[0]);
                }}
                data-testid="slice-dim-dte"
                style={{
                  padding: "4px 10px",
                  background: sliceDim === "dte" ? "var(--caution)" : "transparent",
                  color: sliceDim === "dte" ? "#000000" : "var(--text-muted)",
                  border: `1px solid ${sliceDim === "dte" ? "var(--caution)" : "var(--border)"}`,
                  borderRadius: "var(--r-sm)",
                  fontSize: "var(--t-micro)",
                  fontWeight: sliceDim === "dte" ? 700 : 500,
                  cursor: "pointer",
                }}
              >
                Slice by DTE (Smile)
              </button>
              <button
                onClick={() => {
                  setSliceDim("strike");
                  setSliceVal(mesh.spotPrice);
                }}
                data-testid="slice-dim-strike"
                style={{
                  padding: "4px 10px",
                  background: sliceDim === "strike" ? "var(--growth)" : "transparent",
                  color: sliceDim === "strike" ? "#000000" : "var(--text-muted)",
                  border: `1px solid ${sliceDim === "strike" ? "var(--growth)" : "var(--border)"}`,
                  borderRadius: "var(--r-sm)",
                  fontSize: "var(--t-micro)",
                  fontWeight: sliceDim === "strike" ? 700 : 500,
                  cursor: "pointer",
                }}
              >
                Slice by Strike (Term)
              </button>
            </div>
          </div>

          {/* Slicing Controls & 2D Curve */}
          {sliceData && (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
              {/* Slider Controller */}
              <div style={{ display: "flex", alignItems: "center", gap: "var(--s-3)" }}>
                <span style={{ fontSize: "var(--t-micro)", color: "var(--text-secondary)", minWidth: 120 }}>
                  {sliceDim === "dte" ? `Expiration DTE: ${sliceVal}d` : `Strike Price: $${sliceVal.toFixed(1)}`}
                </span>
                <input
                  type="range"
                  min={sliceDim === "dte" ? mesh.minDte : mesh.minStrike}
                  max={sliceDim === "dte" ? mesh.maxDte : mesh.maxStrike}
                  step={sliceDim === "dte" ? 1 : 1}
                  value={sliceVal}
                  onChange={(e) => setSliceVal(parseFloat(e.target.value))}
                  data-testid="vol-slice-slider"
                  style={{ flex: 1, accentColor: sliceDim === "dte" ? "#f59e0b" : "#10b981" }}
                />
              </div>

              {/* 2D Slice Visualizer (SVG) */}
              <div
                data-testid="vol-slice-chart"
                style={{
                  height: 140,
                  background: "var(--base)",
                  borderRadius: "var(--r-sm)",
                  border: "1px solid var(--border)",
                  padding: "var(--s-3)",
                  position: "relative",
                }}
              >
                {(() => {
                  const xVals = sliceData.sliceX;
                  const yVals = sliceData.sliceY;
                  if (xVals.length === 0 || yVals.length === 0) return null;

                  const minX = Math.min(...xVals);
                  const maxX = Math.max(...xVals);
                  const minY = Math.max(0, Math.min(...yVals) - 0.02);
                  const maxY = Math.max(...yVals) + 0.02;

                  const w = 600;
                  const h = 100;
                  const padL = 45;
                  const padR = 20;
                  const padT = 10;
                  const padB = 20;

                  const scaleX = (x: number) => padL + ((x - minX) / (maxX - minX || 1)) * (w - padL - padR);
                  const scaleY = (y: number) => padT + (1 - (y - minY) / (maxY - minY || 1)) * (h - padT - padB);

                  const pathStr = xVals
                    .map((x, i) => `${i === 0 ? "M" : "L"} ${scaleX(x).toFixed(1)} ${scaleY(yVals[i]).toFixed(1)}`)
                    .join(" ");

                  const strokeColor = sliceDim === "dte" ? "#f59e0b" : "#10b981";

                  return (
                    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height="100%" preserveAspectRatio="none">
                      {/* Gridlines */}
                      <line x1={padL} y1={padT} x2={w - padR} y2={padT} stroke="rgba(255,255,255,0.06)" strokeWidth="1" />
                      <line x1={padL} y1={h - padB} x2={w - padR} y2={h - padB} stroke="rgba(255,255,255,0.12)" strokeWidth="1" />

                      {/* 2D Slice Path */}
                      <path d={pathStr} fill="none" stroke={strokeColor} strokeWidth="2.5" strokeLinecap="round" />

                      {/* Vertex Dots */}
                      {xVals.map((x, i) => {
                        const cx = scaleX(x);
                        const cy = scaleY(yVals[i]);
                        return (
                          <g key={i}>
                            <circle cx={cx} cy={cy} r={3} fill={strokeColor} stroke="#0b0e11" strokeWidth="1.5" />
                            {i % 2 === 0 && (
                              <text x={cx} y={h - 4} fill={theme.textMuted} fontSize="8" textAnchor="middle">
                                {sliceDim === "dte" ? `$${x}` : `${x}d`}
                              </text>
                            )}
                          </g>
                        );
                      })}
                    </svg>
                  );
                })()}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
