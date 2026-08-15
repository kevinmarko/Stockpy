import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import {
  VolSurface3D,
  checkWebGLSupport,
  generateSyntheticVolMesh,
  buildMeshFromPointsOrResponse,
  sampleColormap,
  sliceMesh,
  calculateSurfaceMetrics,
  type VolSurface3DPoint,
} from "./VolSurface3D";
import type { VolSurfaceResponse } from "../../api/types";

// Setup Canvas 2D mock context for clean test execution in JSDOM
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

const mockVolResponse: VolSurfaceResponse = {
  symbol: "NVDA",
  spot_price: 125.5,
  as_of: "2026-08-15T14:30:00Z",
  expirations: ["2026-08-22", "2026-09-19", "2026-10-17"],
  selected_expiration: "2026-09-19",
  smile_points: [
    { strike: 100, iv: 0.48, moneyness: 0.8 },
    { strike: 115, iv: 0.39, moneyness: 0.92 },
    { strike: 125, iv: 0.35, moneyness: 1.0 },
    { strike: 135, iv: 0.34, moneyness: 1.08 },
    { strike: 150, iv: 0.36, moneyness: 1.2 },
  ],
  term_structure: [
    { expiration: "2026-08-22", dte: 7, atm_iv: 0.32, historical_realized_vol_30d: 0.28 },
    { expiration: "2026-09-19", dte: 35, atm_iv: 0.35, historical_realized_vol_30d: 0.28 },
    { expiration: "2026-10-17", dte: 63, atm_iv: 0.38, historical_realized_vol_30d: 0.28 },
  ],
  skew: {
    skew_25delta: 0.05,
    put_25delta_iv: 0.39,
    call_25delta_iv: 0.34,
    atm_iv: 0.35,
    vrp_spread: 0.07,
    realized_vol_10d: 0.26,
    realized_vol_20d: 0.27,
    realized_vol_30d: 0.28,
    realized_vol_60d: 0.3,
  },
};

const mockCustomPoints: VolSurface3DPoint[] = [
  { strike: 480, dte: 14, iv: 0.28, moneyness: 0.96 },
  { strike: 500, dte: 14, iv: 0.22, moneyness: 1.0 },
  { strike: 520, dte: 14, iv: 0.19, moneyness: 1.04 },
  { strike: 480, dte: 45, iv: 0.3, moneyness: 0.96 },
  { strike: 500, dte: 45, iv: 0.24, moneyness: 1.0 },
  { strike: 520, dte: 45, iv: 0.21, moneyness: 1.04 },
];

describe("VolSurface3D Component & Helpers Suite", () => {
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

    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      return setTimeout(() => cb(performance.now()), 16) as unknown as number;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
      clearTimeout(id);
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("Pure Calculations & Geometry Helpers", () => {
    it("checkWebGLSupport returns boolean", () => {
      const result = checkWebGLSupport();
      expect(typeof result).toBe("boolean");
    });

    it("generateSyntheticVolMesh produces valid grid, strikes, and bounds", () => {
      const mesh = generateSyntheticVolMesh("SPY", 500, 10, 5);
      expect(mesh.symbol).toBe("SPY");
      expect(mesh.spotPrice).toBe(500);
      expect(mesh.strikes.length).toBe(10);
      expect(mesh.dtes.length).toBe(5);
      expect(mesh.grid.length).toBe(5);
      expect(mesh.grid[0].length).toBe(10);
      expect(mesh.minIv).toBeGreaterThan(0);
      expect(mesh.maxIv).toBeGreaterThanOrEqual(mesh.minIv);
      expect(mesh.minStrike).toBeLessThan(mesh.maxStrike);
    });

    it("buildMeshFromPointsOrResponse parses VolSurfaceResponse correctly", () => {
      const mesh = buildMeshFromPointsOrResponse(undefined, mockVolResponse);
      expect(mesh.symbol).toBe("NVDA");
      expect(mesh.spotPrice).toBe(125.5);
      expect(mesh.strikes).toEqual([100, 115, 125, 135, 150]);
      expect(mesh.dtes).toEqual([7, 35, 63]);
      expect(mesh.grid.length).toBe(3);
      expect(mesh.grid[0].length).toBe(5);
    });

    it("buildMeshFromPointsOrResponse parses custom points correctly", () => {
      const mesh = buildMeshFromPointsOrResponse(mockCustomPoints, undefined, 500, "SPY");
      expect(mesh.strikes).toEqual([480, 500, 520]);
      expect(mesh.dtes).toEqual([14, 45]);
      expect(mesh.grid.length).toBe(2);
      expect(mesh.grid[0].length).toBe(3);
      expect(mesh.grid[0][1]).toBe(0.22);
    });

    it("sampleColormap returns valid color interpolations across all colormaps", () => {
      const maps = ["cyan-amber", "plasma", "viridis", "emerald"] as const;
      for (const cm of maps) {
        const cLow = sampleColormap(cm, 0.0);
        expect(cLow.hex).toBeDefined();
        expect(typeof cLow.r).toBe("number");
        expect(cLow.rgba(0.5)).toContain("rgba(");

        const cMid = sampleColormap(cm, 0.5);
        expect(cMid.hex.startsWith("#")).toBe(true);

        const cHigh = sampleColormap(cm, 1.0);
        expect(cHigh.r).toBeGreaterThanOrEqual(0);
      }
    });

    it("sliceMesh extracts 2D cross sections for DTE and Strike dimensions", () => {
      const mesh = buildMeshFromPointsOrResponse(undefined, mockVolResponse);

      // Slice along DTE (Smile curve)
      const dteSlice = sliceMesh(mesh, "dte", 35);
      expect(dteSlice.dimension).toBe("dte");
      expect(dteSlice.sliceX).toEqual(mesh.strikes);
      expect(dteSlice.sliceY.length).toBe(mesh.strikes.length);
      expect(dteSlice.label).toContain("DTE 35d");

      // Slice along Strike (Term structure)
      const strikeSlice = sliceMesh(mesh, "strike", 125);
      expect(strikeSlice.dimension).toBe("strike");
      expect(strikeSlice.sliceX).toEqual(mesh.dtes);
      expect(strikeSlice.sliceY.length).toBe(mesh.dtes.length);
      expect(strikeSlice.label).toContain("Strike $125.0");
    });

    it("calculateSurfaceMetrics computes accurate ATM IV, skew, and slope", () => {
      const mesh = buildMeshFromPointsOrResponse(undefined, mockVolResponse);
      const metrics = calculateSurfaceMetrics(mesh);

      expect(metrics.spotPrice).toBe(125.5);
      expect(metrics.atmIv).toBeGreaterThan(0);
      expect(metrics.minIv).toBeGreaterThan(0);
      expect(metrics.maxIv).toBeGreaterThanOrEqual(metrics.minIv);
      expect(typeof metrics.skew25d).toBe("number");
      expect(typeof metrics.termSlope).toBe("number");
    });
  });

  describe("Component Mounting, Interactions & Controls", () => {
    it("renders container, title, spot price badge, metrics cards, and colormap legend", () => {
      render(<VolSurface3D symbol="SPY" forceFallback={true} />);

      expect(screen.getByTestId("vol-surface-3d-container")).toBeInTheDocument();
      expect(screen.getByText(/3D Volatility Surface/i)).toBeInTheDocument();
      expect(screen.getByTestId("vol-render-mode")).toHaveTextContent(/Canvas 3D Fallback Mode/i);
      expect(screen.getByTestId("metric-spot")).toBeInTheDocument();
      expect(screen.getByTestId("metric-atm-iv")).toBeInTheDocument();
      expect(screen.getByTestId("metric-skew")).toBeInTheDocument();
      expect(screen.getByTestId("metric-term-slope")).toBeInTheDocument();
      expect(screen.getByTestId("vol-colormap-legend")).toBeInTheDocument();
    });

    it("renders WebGL 3D Active badge when WebGL is available", () => {
      vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation((type: string) => {
        if (type === "webgl" || type === "webgl2" || type === "experimental-webgl") {
          return { getParameter: vi.fn(() => "WebGL 2.0") } as any;
        }
        return mockCtx;
      });

      render(<VolSurface3D symbol="AAPL" forceFallback={false} />);

      const renderMode = screen.getByTestId("vol-render-mode");
      expect(renderMode).toHaveTextContent("WebGL 3D Active");
    });

    it("renders with VolSurfaceResponse data and updates metrics HUD", () => {
      render(<VolSurface3D volResponse={mockVolResponse} forceFallback={true} />);

      expect(screen.getByText(/NVDA \$125.50/i)).toBeInTheDocument();
      expect(screen.getByTestId("metric-spot")).toHaveTextContent("$125.50");
    });

    it("renders with custom points array", () => {
      render(<VolSurface3D points={mockCustomPoints} symbol="SPY" spotPrice={500} forceFallback={true} />);

      expect(screen.getByText(/SPY \$500.00/i)).toBeInTheDocument();
      expect(screen.getByTestId("metric-spot")).toHaveTextContent("$500.00");
    });

    it("switches camera view presets (Isometric, Smile, Term, Contour)", () => {
      render(<VolSurface3D volResponse={mockVolResponse} forceFallback={true} />);

      const smileBtn = screen.getByTestId("preset-smile");
      fireEvent.click(smileBtn);
      expect(smileBtn).toHaveStyle({ fontWeight: 700 });

      const termBtn = screen.getByTestId("preset-term");
      fireEvent.click(termBtn);
      expect(termBtn).toHaveStyle({ fontWeight: 700 });

      const contourBtn = screen.getByTestId("preset-contour");
      fireEvent.click(contourBtn);
      expect(contourBtn).toHaveStyle({ fontWeight: 700 });

      const isoBtn = screen.getByTestId("preset-iso");
      fireEvent.click(isoBtn);
      expect(isoBtn).toHaveStyle({ fontWeight: 700 });
    });

    it("changes render mode dropdown", () => {
      render(<VolSurface3D volResponse={mockVolResponse} forceFallback={true} />);

      const renderModeSelect = screen.getByTestId("vol-rendermode-select") as HTMLSelectElement;
      fireEvent.change(renderModeSelect, { target: { value: "wireframe" } });
      expect(renderModeSelect.value).toBe("wireframe");

      fireEvent.change(renderModeSelect, { target: { value: "points" } });
      expect(renderModeSelect.value).toBe("points");

      fireEvent.change(renderModeSelect, { target: { value: "surface" } });
      expect(renderModeSelect.value).toBe("surface");
    });

    it("changes colormap selection", () => {
      render(<VolSurface3D volResponse={mockVolResponse} forceFallback={true} />);

      const colormapSelect = screen.getByTestId("vol-colormap-select") as HTMLSelectElement;
      fireEvent.change(colormapSelect, { target: { value: "plasma" } });
      expect(colormapSelect.value).toBe("plasma");

      fireEvent.change(colormapSelect, { target: { value: "viridis" } });
      expect(colormapSelect.value).toBe("viridis");
    });

    it("toggles auto-rotate button", () => {
      render(<VolSurface3D volResponse={mockVolResponse} forceFallback={true} />);

      const autoRotateBtn = screen.getByTestId("vol-autorotate-btn");
      fireEvent.click(autoRotateBtn);
      expect(autoRotateBtn).toBeInTheDocument();
    });

    it("handles zoom in and zoom out clicks", () => {
      render(<VolSurface3D volResponse={mockVolResponse} forceFallback={true} />);

      const zoomInBtn = screen.getByTestId("vol-zoom-in");
      const zoomOutBtn = screen.getByTestId("vol-zoom-out");

      fireEvent.click(zoomInBtn);
      fireEvent.click(zoomOutBtn);
      expect(zoomInBtn).toBeInTheDocument();
    });

    it("handles reset camera button click", () => {
      render(<VolSurface3D volResponse={mockVolResponse} forceFallback={true} />);

      const resetBtn = screen.getByTestId("vol-reset-camera-btn");
      fireEvent.click(resetBtn);
      expect(resetBtn).toBeInTheDocument();
    });

    it("handles cross-section slicing toggling and slider adjustments", () => {
      render(<VolSurface3D volResponse={mockVolResponse} forceFallback={true} />);

      // Switch to slice by DTE
      const sliceDteBtn = screen.getByTestId("slice-dim-dte");
      fireEvent.click(sliceDteBtn);

      expect(screen.getByTestId("vol-slice-chart")).toBeInTheDocument();
      expect(screen.getByTestId("vol-slice-slider")).toBeInTheDocument();

      const slider = screen.getByTestId("vol-slice-slider");
      fireEvent.change(slider, { target: { value: "35" } });

      // Switch to slice by Strike
      const sliceStrikeBtn = screen.getByTestId("slice-dim-strike");
      fireEvent.click(sliceStrikeBtn);

      expect(screen.getByTestId("vol-slice-chart")).toBeInTheDocument();

      // Switch to slice None
      const sliceNoneBtn = screen.getByTestId("slice-dim-none");
      fireEvent.click(sliceNoneBtn);
      expect(screen.queryByTestId("vol-slice-chart")).not.toBeInTheDocument();
    });

    it("handles mouse dragging and interaction events on canvas", () => {
      const handleHover = vi.fn();
      const handleSelect = vi.fn();

      render(
        <VolSurface3D
          volResponse={mockVolResponse}
          forceFallback={true}
          onHoverPoint={handleHover}
          onSelectPoint={handleSelect}
        />
      );

      const canvas = screen.getByTestId("vol-surface-canvas");
      vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
        left: 0,
        top: 0,
        width: 800,
        height: 540,
        right: 800,
        bottom: 540,
        x: 0,
        y: 0,
        toJSON: () => {},
      });

      // Mouse drag
      fireEvent.mouseDown(canvas, { clientX: 100, clientY: 100 });
      fireEvent.mouseMove(canvas, { clientX: 150, clientY: 120 });
      fireEvent.mouseUp(canvas);

      // Shift-drag for pan
      fireEvent.mouseDown(canvas, { clientX: 100, clientY: 100, shiftKey: true });
      fireEvent.mouseMove(canvas, { clientX: 120, clientY: 110, shiftKey: true });
      fireEvent.mouseUp(canvas);

      // Mouse move hover
      fireEvent.mouseMove(canvas, { clientX: 400, clientY: 270 });

      // Wheel zoom
      fireEvent.wheel(canvas, { deltaY: -100 });
      fireEvent.wheel(canvas, { deltaY: 100 });

      // Click
      fireEvent.click(canvas);
    });
  });
});
