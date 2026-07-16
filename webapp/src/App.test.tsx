/**
 * App.test.tsx — nav/gear wiring for the Data & Automation fold-in: the
 * gear button navigates to /settings (instead of opening a local sheet), it
 * still carries the needRefresh "update available" dot from any screen, the
 * /settings route resolves to the real Settings screen, and BottomNav still
 * shows exactly 3 items (Settings is NAV_ITEMS' 8th entry -- desktop Sidebar
 * only, per App.tsx's own comment on NAV_ITEMS).
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { writeOnboarding } from "./onboarding";

let needRefresh = false;

vi.mock("virtual:pwa-register/react", () => ({
  useRegisterSW: (opts?: { onRegisteredSW?: () => void }) => {
    useEffect(() => {
      opts?.onRegisteredSW?.();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return {
      needRefresh: [needRefresh, vi.fn()],
      offlineReady: [false, vi.fn()],
      updateServiceWorker: vi.fn(),
    };
  },
}));

function renderApp(initialPath = "/") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <App />
    </MemoryRouter>
  );
}

describe("App — Settings gear + nav", () => {
  beforeEach(() => {
    // Bypass the onboarding gate so App renders the real shell, not the
    // Onboarding wizard (App.tsx reads this once into useState on mount).
    writeOnboarding({ completed: true });
    Object.defineProperty(navigator, "serviceWorker", {
      value: {},
      configurable: true,
    });
  });

  afterEach(() => {
    needRefresh = false;
    localStorage.clear();
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
    vi.clearAllMocks();
  });

  it("the gear navigates to /settings instead of opening a local sheet", async () => {
    const user = userEvent.setup();
    renderApp("/");

    await user.click(screen.getByTestId("settings-button"));

    expect(await screen.findByText("Data & Automation")).toBeInTheDocument();
    // No sheet/backdrop scaffold left behind by the old drawer pattern.
    expect(screen.queryByTestId("pwa-status-sheet")).not.toBeInTheDocument();
  });

  it("shows the needRefresh update dot on the gear from any screen", () => {
    needRefresh = true;
    renderApp("/marketplace");
    expect(screen.getByTestId("pwa-update-dot")).toBeInTheDocument();
  });

  it("no update dot when the app is up to date", () => {
    renderApp("/");
    expect(screen.queryByTestId("pwa-update-dot")).not.toBeInTheDocument();
  });

  it("the /settings route resolves directly (deep link)", async () => {
    renderApp("/settings");
    expect(await screen.findByText("Data & Automation")).toBeInTheDocument();
  });

  it("the Settings screen's App-status section renders (PwaStatusSection fold-in)", async () => {
    renderApp("/settings");
    expect(await screen.findByText("App status")).toBeInTheDocument();
  });

  it("BottomNav still shows exactly 3 items (Settings does not evict Activity)", () => {
    const { container } = renderApp("/");
    const bottomNav = container.querySelector(".bottom-nav");
    expect(bottomNav).not.toBeNull();
    const items = bottomNav!.querySelectorAll(".nav-item");
    expect(items).toHaveLength(3);
    const labels = Array.from(items).map((el) => el.textContent);
    expect(labels.some((l) => l?.includes("Settings"))).toBe(false);
  });
});
