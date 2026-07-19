/**
 * App.test.tsx — nav/gear wiring for the Data & Automation fold-in, plus the
 * 2026-07 navigation rework: the gear button navigates to /settings (instead
 * of opening a local sheet), it still carries the needRefresh "update
 * available" dot from any screen, the /settings route resolves to the real
 * Settings screen, and BottomNav shows a primary 4 (Dashboard/Portfolio/
 * Activity per the usage-frequency audit, plus Agent per a later
 * `/user-research` pass) plus a "More" button whose sheet groups
 * every other screen into labeled sections (Research / Trading Tools /
 * Operations / Settings) -- Settings now has two entry points, the same as
 * every other screen (the sheet) plus the always-on gear shortcut.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
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
      provider_selector_setting: "LLM_COMMENTARY_RATIONALE_PROVIDER",
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
  writable: false,
  writable_note: "AI-capability writes are disabled (LLM_WRITES_ENABLED=false).",
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

  it("BottomNav shows the primary 4 plus a More button", () => {
    const { container } = renderApp("/");
    const bottomNav = container.querySelector(".bottom-nav");
    expect(bottomNav).not.toBeNull();
    const items = bottomNav!.querySelectorAll(".nav-item");
    // Dashboard, Portfolio, Activity (2026-07 usage-frequency audit), Agent
    // (a later `/user-research` pass -- an "active operational surface" the
    // operator drives from mobile, not an occasional deep-dive) + More.
    expect(items).toHaveLength(5);
    const nav = within(bottomNav as HTMLElement);
    expect(nav.getByText("Dashboard")).toBeInTheDocument();
    expect(nav.getByText("Portfolio")).toBeInTheDocument();
    expect(nav.getByText("Activity")).toBeInTheDocument();
    expect(nav.getByText("Agent")).toBeInTheDocument();
    expect(nav.getByText("More")).toBeInTheDocument();
    // Pilots was checked less often than Portfolio in the audit -- it moved
    // out of the primary group into the More sheet's Research section.
    expect(nav.queryByText("Pilots")).not.toBeInTheDocument();
  });

  it("the More sheet groups every secondary screen into labeled sections, including Settings", async () => {
    const user = userEvent.setup();
    renderApp("/");

    await user.click(screen.getByTestId("more-nav-button"));

    const dialog = await screen.findByRole("dialog", { name: "More sections" });
    // Section headers (h3s -- distinct from the "Settings" nav-item button
    // text below, which shares the same string).
    for (const section of ["Research", "Trading Tools", "Operations", "Settings"]) {
      expect(
        within(dialog).getByRole("heading", { name: section, level: 3 })
      ).toBeInTheDocument();
    }
    // Pilots moved out of the primary 3 into Research, alongside the rest of
    // the research/vetting screens.
    for (const label of [
      "Pilots",
      "Compare",
      "Models",
      "Strategy Health",
      "Pairs radar",
      "Options",
      "Signal Breakdown",
      "Forecast Viewer",
      "Data Explorer",
    ]) {
      expect(within(dialog).getByText(label)).toBeInTheDocument();
    }
    for (const label of ["Attribution", "Calibration", "Commands"]) {
      expect(within(dialog).getByText(label)).toBeInTheDocument();
    }
    for (const label of ["Mission Control", "Pipeline"]) {
      expect(within(dialog).getByText(label)).toBeInTheDocument();
    }
    // Settings is now listed like any other screen, not gear-only.
    expect(within(dialog).getByRole("button", { name: "Settings" })).toBeInTheDocument();
    // Portfolio and Agent are primary now -- neither should also appear
    // inside the sheet (Agent moved out of Trading Tools into the bottom bar).
    expect(within(dialog).queryByText("Portfolio")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Agent")).not.toBeInTheDocument();
  });

  it("Pilots (moved out of primary) is reachable on mobile via More -> Research", async () => {
    const user = userEvent.setup();
    renderApp("/");

    await user.click(screen.getByTestId("more-nav-button"));
    const dialog = await screen.findByRole("dialog", { name: "More sections" });
    await user.click(within(dialog).getByText("Pilots"));

    // Landed on the Marketplace/Pilots screen and the sheet is gone.
    expect(
      await screen.findByRole("heading", { name: "Pilots" })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("dialog", { name: "More sections" })
    ).not.toBeInTheDocument();
  });

  it("Settings is reachable from the More sheet, in addition to the gear shortcut", async () => {
    const user = userEvent.setup();
    renderApp("/");

    await user.click(screen.getByTestId("more-nav-button"));
    const dialog = await screen.findByRole("dialog", { name: "More sections" });
    await user.click(within(dialog).getByRole("button", { name: "Settings" }));

    expect(await screen.findByText("Data & Automation")).toBeInTheDocument();
    expect(
      screen.queryByRole("dialog", { name: "More sections" })
    ).not.toBeInTheDocument();
  });

  it.each([
    ["Research", "Research"],
    ["Trading Tools", "Trading Tools"],
    ["Operations", "Operations"],
  ])(
    "clicking the %s section header in the More sheet navigates to its hub and closes the sheet",
    async (sectionLabel, hubHeading) => {
      const user = userEvent.setup();
      renderApp("/");

      await user.click(screen.getByTestId("more-nav-button"));
      const dialog = await screen.findByRole("dialog", { name: "More sections" });
      await user.click(
        within(dialog).getByRole("heading", { name: sectionLabel, level: 3 })
      );

      expect(
        await screen.findByRole("heading", { name: hubHeading })
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("dialog", { name: "More sections" })
      ).not.toBeInTheDocument();
    }
  );

  it("clicking the Settings section header in the More sheet has no navigation side effect (no hub screen)", async () => {
    const user = userEvent.setup();
    renderApp("/");

    await user.click(screen.getByTestId("more-nav-button"));
    const dialog = await screen.findByRole("dialog", { name: "More sections" });
    const settingsHeading = within(dialog).getByRole("heading", {
      name: "Settings",
      level: 3,
    });
    await user.click(settingsHeading);

    // Still on Dashboard, sheet still open -- the header did nothing.
    expect(
      await screen.findByRole("dialog", { name: "More sections" })
    ).toBeInTheDocument();
    expect(screen.queryByText("Data & Automation")).not.toBeInTheDocument();
  });

  it.each([
    ["Research", "Research"],
    ["Trading Tools", "Trading Tools"],
    ["Operations", "Operations"],
  ])(
    "desktop Sidebar's %s section header is clickable and navigates to its hub",
    async (sectionLabel, hubHeading) => {
      const user = userEvent.setup();
      const { container } = renderApp("/");

      const sidebar = container.querySelector(".sidebar");
      expect(sidebar).not.toBeNull();
      // getAllByText(...)[0]: the section header div renders before its item
      // buttons in the DOM. Only "Settings" collides with an item label
      // (the lone Settings nav item shares its section's name) -- [0] always
      // resolves to the header itself, which is what should be clicked.
      const header = within(sidebar as HTMLElement).getAllByText(sectionLabel)[0];
      await user.click(header);

      expect(
        await screen.findByRole("heading", { name: hubHeading })
      ).toBeInTheDocument();
    }
  );

  it("desktop Sidebar's Settings section header is not clickable (no hub screen)", async () => {
    const user = userEvent.setup();
    const { container } = renderApp("/");

    const sidebar = container.querySelector(".sidebar");
    expect(sidebar).not.toBeNull();
    const header = within(sidebar as HTMLElement).getAllByText("Settings")[0];
    await user.click(header);

    // Still on Dashboard -- clicking the plain-text header did nothing.
    expect(screen.queryByText("Data & Automation")).not.toBeInTheDocument();
  });
});
