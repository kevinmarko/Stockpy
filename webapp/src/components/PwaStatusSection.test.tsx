/**
 * PwaStatusSection.test.tsx — the content formerly inside PwaStatusDrawer's
 * bottom sheet (see git history / CLAUDE.md for the fold-in rationale), now
 * a plain always-visible card embedded in the Settings screen. Mocks
 * vite-plugin-pwa's `virtual:pwa-register/react` hook directly since the real
 * module is an inert dev-mode stub under vitest (no `command === "build"`) —
 * see usePwaStatus.ts's docstring.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PwaStatusSection } from "./PwaStatusSection";

const updateServiceWorker = vi.fn().mockResolvedValue(undefined);
let needRefresh = false;
let offlineReady = false;

vi.mock("virtual:pwa-register/react", () => ({
  useRegisterSW: (opts?: { onRegisteredSW?: () => void; onRegisterError?: () => void }) => {
    useEffect(() => {
      opts?.onRegisteredSW?.();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return {
      needRefresh: [needRefresh, vi.fn()],
      offlineReady: [offlineReady, vi.fn()],
      updateServiceWorker,
    };
  },
}));

describe("PwaStatusSection", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "serviceWorker", {
      value: {},
      configurable: true,
    });
  });

  afterEach(() => {
    needRefresh = false;
    offlineReady = false;
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
    vi.clearAllMocks();
  });

  it("reports 'Not supported' when the browser has no Service Worker API at all", () => {
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
    render(<PwaStatusSection />);
    expect(screen.getByText("Not supported")).toBeInTheDocument();
  });

  it("renders directly (no trigger, no sheet) and reports Active/registered + not-yet-cached", () => {
    render(<PwaStatusSection />);
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Not cached yet")).toBeInTheDocument();
    expect(screen.getByText("Up to date")).toBeInTheDocument();
    expect(screen.queryByTestId("pwa-update-btn")).not.toBeInTheDocument();
  });

  it("reports offline-ready once precaching finishes", () => {
    offlineReady = true;
    render(<PwaStatusSection />);
    expect(screen.getByText("Ready for offline use")).toBeInTheDocument();
  });

  it("shows a reload action when a new SW version is waiting, and it triggers the update", async () => {
    needRefresh = true;
    const user = userEvent.setup();
    render(<PwaStatusSection />);

    expect(screen.getByText("Update available")).toBeInTheDocument();
    expect(screen.getByText(/new version has been downloaded/i)).toBeInTheDocument();

    await user.click(screen.getByTestId("pwa-update-btn"));
    expect(updateServiceWorker).toHaveBeenCalledWith(true);
  });
});
