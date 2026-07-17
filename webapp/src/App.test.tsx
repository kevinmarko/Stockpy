/**
 * App.test.tsx — nav/gear wiring for the Data & Automation fold-in: the
 * gear button navigates to /settings (instead of opening a local sheet), it
 * still carries the needRefresh "update available" dot from any screen, the
 * /settings route resolves to the real Settings screen, and BottomNav still
 * shows exactly 3 items (Settings is NAV_ITEMS' 8th entry -- desktop Sidebar
 * only, per App.tsx's own comment on NAV_ITEMS).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { api } from "./api/client";
import type { LlmProviderName, LlmStatus } from "./api/types";
import { writeOnboarding } from "./onboarding";

const _noCall = (provider: LlmProviderName) => ({
  provider,
  ok: null,
  error_kind: null,
  exception_type: null,
  http_status: null,
  checked_at: null,
  age_seconds: null,
  source: "none" as const,
});

/** A minimal LlmStatus with one auth-rejected capability (attention: true). */
const LLM_ATTENTION: LlmStatus = {
  capabilities: [
    {
      key: "claude_commentary",
      label: "Analyst rationale commentary",
      trigger: "on_demand",
      toggle_key: "LLM_COMMENTARY_ENABLED",
      provider_keys: ["ANTHROPIC_API_KEY"],
      active_provider: "claude",
      invalid_provider: "claude",
      enabled: true,
      key_present: true,
      built: true,
      status: "invalid_key",
    },
  ],
  capabilities_source: "test",
  providers: {
    claude: {
      provider: "claude",
      ok: false,
      error_kind: "auth",
      exception_type: "AuthenticationError",
      http_status: 401,
      checked_at: new Date().toISOString(),
      age_seconds: 10,
      source: "last_call",
    },
    gemini: _noCall("gemini"),
    openai: _noCall("openai"),
  },
  providers_source: "test",
  telemetry_note: "note",
  attention: true,
  attention_reason: "invalid_key",
};

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

  it("shows the LLM-config dot when an enabled capability needs attention", async () => {
    vi.spyOn(api, "getLlmStatus").mockResolvedValue(LLM_ATTENTION);
    renderApp("/marketplace");
    expect(await screen.findByTestId("llm-config-dot")).toBeInTheDocument();
  });

  it("no LLM-config dot in the honest default (attention: false)", async () => {
    const spy = vi.spyOn(api, "getLlmStatus").mockResolvedValue({
      ...LLM_ATTENTION,
      attention: false,
      attention_reason: null,
    });
    renderApp("/marketplace");
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(screen.queryByTestId("llm-config-dot")).not.toBeInTheDocument();
  });

  it("no LLM-config dot when the fetch fails (absence is not a false alarm)", async () => {
    const spy = vi.spyOn(api, "getLlmStatus").mockRejectedValue(new Error("network down"));
    renderApp("/marketplace");
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(screen.queryByTestId("llm-config-dot")).not.toBeInTheDocument();
    // PWA dot mechanism stays independent and unaffected.
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
