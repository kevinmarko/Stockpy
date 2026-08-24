import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import {
  VolSurface3D,
  checkWebGLSupport,
  generateSyntheticVolMesh,
  buildMeshFromPointsOrResponse,
  sampleColormap,
  sliceMesh,
  calculateSurfaceMetrics,
  disposeThreeScene,
  disposeThreeMesh,
  disposeThreeGeometry,
  disposeThreeMaterial,
  disposeThreeTexture,
  disposeWebGLRenderer,
  disposeCanvas,
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
    cleanup();
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

    it("calculateSurfaceMetrics prefers the real backend skew_25delta over the " +
      "moneyness-proxy recompute when a real volResponse is supplied -- the " +
      "sibling 2D screen (VolSurfaceView.tsx) renders the backend field under " +
      "the identical '25-Delta Put-Call Skew' label, so the two must agree", () => {
        const mesh = buildMeshFromPointsOrResponse(undefined, mockVolResponse);

        // Give the backend a real skew value distinct from whatever the
        // moneyness proxy recomputes from this same mesh, to prove
        // preference rather than coincidental agreement.
        const withoutResponse = calculateSurfaceMetrics(mesh); // no real volResponse -> proxy
        const proxyValue = withoutResponse.skew25d as number;
        const divergentResponse: VolSurfaceResponse = {
          ...mockVolResponse,
          skew: { ...mockVolResponse.skew, skew_25delta: proxyValue + 0.10 },
        };
        const withRealSkew = calculateSurfaceMetrics(mesh, divergentResponse);

        expect(withoutResponse.skew25dIsReal).toBe(false);

        expect(withRealSkew.skew25dIsReal).toBe(true);
        expect(withRealSkew.skew25d).toBeCloseTo(proxyValue + 0.10, 6); // the real backend value, not the proxy
        expect(withRealSkew.skew25d).not.toBeCloseTo(proxyValue, 4);
      });

    it("calculateSurfaceMetrics reports an honest null (never the proxy) when a " +
      "real volResponse is supplied but its skew_25delta is absent -- a present- " +
      "but-empty backend field must not silently fall back to a disagreeing proxy", () => {
        const mesh = buildMeshFromPointsOrResponse(undefined, mockVolResponse);
        const noSkewResponse: VolSurfaceResponse = {
          ...mockVolResponse,
          skew: { ...mockVolResponse.skew, skew_25delta: undefined },
        };

        const metrics = calculateSurfaceMetrics(mesh, noSkewResponse);
        expect(metrics.skew25d).toBeNull();
        expect(metrics.skew25dIsReal).toBe(false);
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

    it("renders Canvas 3D Renderer badge when WebGL is available", () => {
      vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation((type: string) => {
        if (type === "webgl" || type === "webgl2" || type === "experimental-webgl") {
          return { getParameter: vi.fn(() => "WebGL 2.0") } as any;
        }
        return mockCtx;
      });

      render(<VolSurface3D symbol="AAPL" forceFallback={false} />);

      const renderMode = screen.getByTestId("vol-render-mode");
      expect(renderMode).toHaveTextContent("Canvas 3D Renderer");
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

      // Touch events (Single finger orbit + two finger pinch/pan)
      fireEvent.touchStart(canvas, {
        touches: [{ clientX: 100, clientY: 100 }],
      });
      fireEvent.touchMove(canvas, {
        touches: [{ clientX: 130, clientY: 110 }],
      });
      fireEvent.touchEnd(canvas);

      fireEvent.touchStart(canvas, {
        touches: [
          { clientX: 100, clientY: 100 },
          { clientX: 200, clientY: 200 },
        ],
      });
      fireEvent.touchMove(canvas, {
        touches: [
          { clientX: 80, clientY: 80 },
          { clientX: 220, clientY: 220 },
        ],
      });
      fireEvent.touchCancel(canvas);
    });

    it("verifies unmount cancels animation frame and cleans up window event listeners", () => {
      const cancelAnimSpy = vi.spyOn(window, "cancelAnimationFrame");
      const removeEventListenerSpy = vi.spyOn(window, "removeEventListener");

      const { unmount } = render(
        <VolSurface3D volResponse={mockVolResponse} forceFallback={true} />
      );

      // Verify render is active
      const canvas = screen.getByTestId("vol-surface-canvas") as HTMLCanvasElement;
      expect(canvas).toBeInTheDocument();

      // Trigger unmount
      unmount();

      // Verify cancelAnimationFrame was called
      expect(cancelAnimSpy).toHaveBeenCalled();

      // Verify window event listeners were removed
      expect(removeEventListenerSpy).toHaveBeenCalledWith("mouseup", expect.any(Function));
      expect(removeEventListenerSpy).toHaveBeenCalledWith("touchend", expect.any(Function));
      expect(removeEventListenerSpy).toHaveBeenCalledWith("touchcancel", expect.any(Function));
      expect(removeEventListenerSpy).toHaveBeenCalledWith("resize", expect.any(Function));

      // Verify canvas backbuffers were zeroed out
      expect(canvas.width).toBe(0);
      expect(canvas.height).toBe(0);
    });
  });

  describe("Three.js & WebGL Explicit Disposal Routines", () => {
    it("disposeThreeTexture disposes texture, image, source, and mipmaps cleanly", () => {
      const mockDispose = vi.fn();
      const mockSourceDispose = vi.fn();
      const mockImageBitmap = {
        close: vi.fn(),
      };

      const texture = {
        dispose: mockDispose,
        image: mockImageBitmap,
        source: { dispose: mockSourceDispose },
        mipmaps: [{}, {}],
      };

      disposeThreeTexture(texture);

      expect(mockDispose).toHaveBeenCalledTimes(1);
      expect(mockSourceDispose).toHaveBeenCalledTimes(1);
      expect(texture.image).toBeNull();
      expect(texture.source).toBeNull();
      expect(texture.mipmaps.length).toBe(0);

      // Should handle null or undefined safely without throwing
      expect(() => disposeThreeTexture(null)).not.toThrow();
      expect(() => disposeThreeTexture(undefined)).not.toThrow();
    });

    it("disposeThreeMaterial disposes material and all attached texture maps and shader uniforms", () => {
      const mockMatDispose = vi.fn();
      const mockTexDispose1 = vi.fn();
      const mockTexDispose2 = vi.fn();
      const mockUniformTexDispose = vi.fn();

      const material = {
        dispose: mockMatDispose,
        map: { dispose: mockTexDispose1 },
        normalMap: { dispose: mockTexDispose2 },
        roughnessMap: null,
        uniforms: {
          uTexture: { value: { dispose: mockUniformTexDispose } },
          uScalar: { value: 1.5 },
        },
      };

      disposeThreeMaterial(material);

      expect(mockTexDispose1).toHaveBeenCalledTimes(1);
      expect(mockTexDispose2).toHaveBeenCalledTimes(1);
      expect(mockUniformTexDispose).toHaveBeenCalledTimes(1);
      expect(mockMatDispose).toHaveBeenCalledTimes(1);
      expect(material.map).toBeNull();
      expect(material.normalMap).toBeNull();

      // Handles array of materials
      const multiMatDispose1 = vi.fn();
      const multiMatDispose2 = vi.fn();
      disposeThreeMaterial([
        { dispose: multiMatDispose1 },
        { dispose: multiMatDispose2 },
      ]);
      expect(multiMatDispose1).toHaveBeenCalledTimes(1);
      expect(multiMatDispose2).toHaveBeenCalledTimes(1);

      // Handles null safely
      expect(() => disposeThreeMaterial(null)).not.toThrow();
    });

    it("disposeThreeGeometry disposes geometry, buffer attributes, and indices", () => {
      const mockGeomDispose = vi.fn();
      const mockPosAttrDispose = vi.fn();
      const mockNormAttrDispose = vi.fn();
      const mockIndexDispose = vi.fn();

      const geometry = {
        dispose: mockGeomDispose,
        attributes: {
          position: { dispose: mockPosAttrDispose },
          normal: { dispose: mockNormAttrDispose },
        },
        index: { dispose: mockIndexDispose },
      };

      disposeThreeGeometry(geometry);

      expect(mockPosAttrDispose).toHaveBeenCalledTimes(1);
      expect(mockNormAttrDispose).toHaveBeenCalledTimes(1);
      expect(mockIndexDispose).toHaveBeenCalledTimes(1);
      expect(mockGeomDispose).toHaveBeenCalledTimes(1);

      expect(() => disposeThreeGeometry(null)).not.toThrow();
    });

    it("disposeThreeMesh disposes geometry and material attached to mesh", () => {
      const mockMeshDispose = vi.fn();
      const mockGeomDispose = vi.fn();
      const mockMatDispose = vi.fn();

      const mesh = {
        dispose: mockMeshDispose,
        geometry: { dispose: mockGeomDispose },
        material: { dispose: mockMatDispose },
      };

      disposeThreeMesh(mesh);

      expect(mockGeomDispose).toHaveBeenCalledTimes(1);
      expect(mockMatDispose).toHaveBeenCalledTimes(1);
      expect(mockMeshDispose).toHaveBeenCalledTimes(1);
      expect(mesh.geometry).toBeNull();
      expect(mesh.material).toBeNull();

      expect(() => disposeThreeMesh(null)).not.toThrow();
    });

    it("disposeThreeScene traverses scene hierarchy, disposes all children, and clears hierarchy", () => {
      const mockGeomDispose1 = vi.fn();
      const mockMatDispose1 = vi.fn();
      const mockGeomDispose2 = vi.fn();
      const mockMatDispose2 = vi.fn();
      const mockSceneDispose = vi.fn();
      const mockSceneClear = vi.fn();

      const child1 = {
        geometry: { dispose: mockGeomDispose1 },
        material: { dispose: mockMatDispose1 },
      };
      const child2 = {
        geometry: { dispose: mockGeomDispose2 },
        material: { dispose: mockMatDispose2 },
      };

      const scene: any = {
        dispose: mockSceneDispose,
        clear: mockSceneClear,
        children: [child1, child2],
        traverse: (callback: (obj: any) => void) => {
          callback(scene);
          callback(child1);
          callback(child2);
        },
      };

      disposeThreeScene(scene);

      expect(mockGeomDispose1).toHaveBeenCalledTimes(1);
      expect(mockMatDispose1).toHaveBeenCalledTimes(1);
      expect(mockGeomDispose2).toHaveBeenCalledTimes(1);
      expect(mockMatDispose2).toHaveBeenCalledTimes(1);
      expect(mockSceneClear).toHaveBeenCalledTimes(1);
      expect(mockSceneDispose).toHaveBeenCalledTimes(1);

      expect(() => disposeThreeScene(null)).not.toThrow();
    });

    it("disposeWebGLRenderer disposes renderer, forces context loss, and removes dom element", () => {
      const mockRendererDispose = vi.fn();
      const mockForceContextLoss = vi.fn();
      const mockLoseContext = vi.fn();

      const mockDomElement = document.createElement("canvas");
      document.body.appendChild(mockDomElement);

      const mockGl = {
        getExtension: vi.fn((ext: string) => {
          if (ext === "WEBGL_lose_context") {
            return { loseContext: mockLoseContext };
          }
          return null;
        }),
      };

      const renderer = {
        dispose: mockRendererDispose,
        forceContextLoss: mockForceContextLoss,
        getContext: () => mockGl,
        domElement: mockDomElement,
      };

      disposeWebGLRenderer(renderer);

      expect(mockRendererDispose).toHaveBeenCalledTimes(1);
      expect(mockForceContextLoss).toHaveBeenCalledTimes(1);
      expect(mockLoseContext).toHaveBeenCalledTimes(1);
      expect(renderer.domElement).toBeNull();
      expect(document.body.contains(mockDomElement)).toBe(false);

      expect(() => disposeWebGLRenderer(null)).not.toThrow();
    });

    it("disposeCanvas calls WEBGL_lose_context, clears 2D context, and zeroes out dimensions", () => {
      const mockLoseContext = vi.fn();
      const canvas = document.createElement("canvas");
      canvas.width = 800;
      canvas.height = 600;

      vi.spyOn(canvas, "getContext").mockImplementation((type: string) => {
        if (type === "webgl" || type === "webgl2" || type === "experimental-webgl") {
          return {
            getExtension: (name: string) => {
              if (name === "WEBGL_lose_context") {
                return { loseContext: mockLoseContext };
              }
              return null;
            },
          } as any;
        }
        return mockCtx;
      });

      disposeCanvas(canvas);

      expect(mockLoseContext).toHaveBeenCalledTimes(1);
      expect(canvas.width).toBe(0);
      expect(canvas.height).toBe(0);

      expect(() => disposeCanvas(null)).not.toThrow();
    });
  });
});

