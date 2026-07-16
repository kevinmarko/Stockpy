/**
 * PwaStatusDrawer.test.tsx — operator-visible SW telemetry (Web App
 * Resilience gap: "no operator UI feedback indicating whether service
 * workers are active, caching successfully, or running on the latest
 * updated version"). Mocks vite-plugin-pwa's `virtual:pwa-register/react`
 * hook directly since the real module is an inert dev-mode stub under
 * vitest (no `command === "build"`) — see usePwaStatus.ts's docstring.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PwaStatusDrawer } from "./PwaStatusDrawer";

const updateServiceWorker = vi.fn().mockResolvedValue(undefined);
let needRefresh = false;
let offlineReady = false;

// Mirrors the real hook's fidelity: onRegisteredSW/onRegisterError fire once
// as a mount side effect (not synchronously during render, which would be a
// render-phase state update on every single re-render of the consumer).
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

describe("PwaStatusDrawer", () => {
  beforeEach(() => {
    // jsdom has no real Service Worker API; stub its presence so `supported`
    // reflects the mocked registration state instead of always "Not supported".
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

  it("reports 'Not supported' when the browser has no Service Worker API at all", async () => {
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
    const user = userEvent.setup();
    render(<PwaStatusDrawer />);
    await user.click(screen.getByTestId("pwa-status-trigger"));
    expect(screen.getByText("Not supported")).toBeInTheDocument();
  });

  it("the trigger button is present on every screen and shows no update dot by default", () => {
    render(<PwaStatusDrawer />);
    expect(screen.getByTestId("pwa-status-trigger")).toBeInTheDocument();
    expect(screen.queryByTestId("pwa-update-dot")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pwa-status-sheet")).not.toBeInTheDocument();
  });

  it("opens the status sheet on click and reports Active/registered + not-yet-cached", async () => {
    const user = userEvent.setup();
    render(<PwaStatusDrawer />);

    await user.click(screen.getByTestId("pwa-status-trigger"));

    expect(screen.getByTestId("pwa-status-sheet")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Not cached yet")).toBeInTheDocument();
    expect(screen.getByText("Up to date")).toBeInTheDocument();
    expect(screen.queryByTestId("pwa-update-btn")).not.toBeInTheDocument();
  });

  it("reports offline-ready once precaching finishes", async () => {
    offlineReady = true;
    const user = userEvent.setup();
    render(<PwaStatusDrawer />);
    await user.click(screen.getByTestId("pwa-status-trigger"));
    expect(screen.getByText("Ready for offline use")).toBeInTheDocument();
  });

  it("shows an update-available dot on the trigger and a reload action in the sheet when a new SW is waiting", async () => {
    needRefresh = true;
    const user = userEvent.setup();
    render(<PwaStatusDrawer />);

    expect(screen.getByTestId("pwa-update-dot")).toBeInTheDocument();

    await user.click(screen.getByTestId("pwa-status-trigger"));
    expect(screen.getByText("Update available")).toBeInTheDocument();
    expect(
      screen.getByText(/new version has been downloaded/i)
    ).toBeInTheDocument();

    await user.click(screen.getByTestId("pwa-update-btn"));
    expect(updateServiceWorker).toHaveBeenCalledWith(true);
  });

  it("closes the sheet on backdrop click and on Close", async () => {
    const user = userEvent.setup();
    render(<PwaStatusDrawer />);
    await user.click(screen.getByTestId("pwa-status-trigger"));
    expect(screen.getByTestId("pwa-status-sheet")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByTestId("pwa-status-sheet")).not.toBeInTheDocument();
  });
});
