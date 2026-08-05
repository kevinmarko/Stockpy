/**
 * SettingsGeneral.test.tsx — the "General & Execution Mode" /settings index
 * screen: signal-generation pause/resume, PWA status, reset onboarding, and
 * the 1-Click Go Live / Execution Mode toggle.
 *
 * Coverage here is scoped to ExecutionModeSection's typed-confirmation gate
 * (added alongside `_require_dangerous_confirmation` in api/pilots_api.py):
 * PUT /settings/tunables already required a typed field-name confirmation
 * for `settings_keysets.DANGEROUS_KEYS` fields (ADVISORY_ONLY, DRY_RUN) via
 * GenericSettingsEditor.tsx's DangerousConfirmDialog; PUT
 * /automation/execution-mode used to write the very same fields directly
 * with zero confirmation of any kind. These tests pin that the SAME
 * confirmation rigor -- type each dangerous field's own name, exactly --
 * now applies here too.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsGeneral } from "./SettingsGeneral";
import { api } from "../api/client";
import type { AutomationStatus } from "../api/types";

vi.mock("virtual:pwa-register/react", () => ({
  useRegisterSW: (opts?: { onRegisteredSW?: () => void }) => {
    useEffect(() => {
      opts?.onRegisteredSW?.();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return {
      needRefresh: [false, vi.fn()],
      offlineReady: [false, vi.fn()],
      updateServiceWorker: vi.fn(),
    };
  },
}));

const HEALTHY_STATUS: AutomationStatus = {
  daemon: {
    alive: true,
    source: "control_api",
    pid: null,
    pid_alive: null,
    port: 8601,
    started_at: "2026-07-16T10:00:00+00:00",
    interval_seconds: 300,
    is_running: false,
    current_run_id: null,
    engines_warm: true,
  },
  last_run: {
    run_id: "orch-1",
    state: "succeeded",
    started_at: "2026-07-16T19:00:00+00:00",
    finished_at: "2026-07-16T19:05:00+00:00",
    duration_seconds: 300,
    error: null,
    reason: "interval",
    progress: null,
  },
  last_run_source: "daemon_memory",
  pipeline: {
    snapshot_age_seconds: 300,
    snapshot_age_source: "timestamp",
    heartbeat_age_seconds: null,
    heartbeat_note: "heartbeat.txt is written only by main_orchestrator.py; null here does not mean the engine is down.",
  },
  progress: null,
  kill_switch: { active: false, reason: null },
  errors: { generated_at: "2026-07-16T19:05:00+00:00", entry_count: 0, entries: [] },
  advisory_only: true,
  dry_run: false,
  alpaca_paper: false,
};

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/settings"]}>
      <SettingsGeneral />
    </MemoryRouter>
  );
}

describe("SettingsGeneral screen — Execution mode (typed confirmation)", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "serviceWorker", { value: {}, configurable: true });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
  });

  it("ADVISORY_ONLY cannot be flipped without typing the confirmation -- Confirm stays disabled and setExecutionMode is never called", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS); // advisory_only: true
    const modeSpy = vi.spyOn(api, "setExecutionMode");
    renderScreen();

    await user.click(await screen.findByRole("button", { name: "🔴 Live Production" }));
    expect(await screen.findByRole("dialog", { name: "Confirm Mode Change" })).toBeInTheDocument();

    // mode="live" touches two dangerous keys: ADVISORY_ONLY and DRY_RUN.
    expect(screen.getByLabelText('Type "ADVISORY_ONLY" to confirm')).toBeInTheDocument();
    expect(screen.getByLabelText('Type "DRY_RUN" to confirm')).toBeInTheDocument();

    const confirmBtn = screen.getByTestId("execution-mode-confirm");
    expect(confirmBtn).toBeDisabled();
    await user.click(confirmBtn);
    expect(modeSpy).not.toHaveBeenCalled();
  });

  it("typing only ONE of two required dangerous fields still leaves Confirm disabled", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    renderScreen();

    await user.click(await screen.findByRole("button", { name: "🔴 Live Production" }));
    await user.type(screen.getByLabelText('Type "ADVISORY_ONLY" to confirm'), "ADVISORY_ONLY");

    expect(screen.getByTestId("execution-mode-confirm")).toBeDisabled();
  });

  it("ADVISORY_ONLY CAN be flipped once every dangerous field is typed correctly -- and each is echoed by its own name in `confirm`", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    const modeSpy = vi.spyOn(api, "setExecutionMode").mockResolvedValueOnce({
      written: ["ADVISORY_ONLY", "DRY_RUN", "ALPACA_PAPER"],
      advisory_only: false,
      mode: "live",
      applies: "next_daemon_restart",
      note: "Execution mode updated.",
    });
    renderScreen();

    await user.click(await screen.findByRole("button", { name: "🔴 Live Production" }));
    const confirmBtn = screen.getByTestId("execution-mode-confirm");
    expect(confirmBtn).toBeDisabled();

    await user.type(screen.getByLabelText('Type "ADVISORY_ONLY" to confirm'), "ADVISORY_ONLY");
    await user.type(screen.getByLabelText('Type "DRY_RUN" to confirm'), "DRY_RUN");
    expect(confirmBtn).toBeEnabled();
    await user.click(confirmBtn);

    expect(modeSpy).toHaveBeenCalledWith({
      mode: "live",
      advisory_only: false,
      confirm: { ADVISORY_ONLY: "ADVISORY_ONLY", DRY_RUN: "DRY_RUN" },
    });
    expect(screen.queryByRole("dialog", { name: "Confirm Mode Change" })).not.toBeInTheDocument();
  });

  it("choosing target mode 'advisory' only needs (and only sends) ADVISORY_ONLY confirmed -- a single input, no DRY_RUN prompt", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue({
      ...HEALTHY_STATUS,
      advisory_only: false,
      dry_run: false,
      alpaca_paper: false, // currentMode: live, so "Advisory Only" is clickable
    });
    const modeSpy = vi.spyOn(api, "setExecutionMode").mockResolvedValueOnce({
      written: ["ADVISORY_ONLY"],
      advisory_only: true,
      mode: "advisory",
      applies: "next_daemon_restart",
      note: "Execution mode updated.",
    });
    renderScreen();

    await user.click(await screen.findByRole("button", { name: "🛑 Advisory Only" }));
    expect(screen.queryByLabelText('Type "DRY_RUN" to confirm')).not.toBeInTheDocument();

    await user.type(screen.getByLabelText('Type "ADVISORY_ONLY" to confirm'), "ADVISORY_ONLY");
    await user.click(screen.getByTestId("execution-mode-confirm"));

    expect(modeSpy).toHaveBeenCalledWith({
      mode: "advisory",
      advisory_only: true,
      confirm: { ADVISORY_ONLY: "ADVISORY_ONLY" },
    });
  });

  it("a mistyped confirmation value (not an exact field-name match) keeps Confirm disabled", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    renderScreen();

    await user.click(await screen.findByRole("button", { name: "📝 Paper Trading" }));
    const confirmBtn = screen.getByTestId("execution-mode-confirm");

    await user.type(screen.getByLabelText('Type "ADVISORY_ONLY" to confirm'), "advisory only please");
    await user.type(screen.getByLabelText('Type "DRY_RUN" to confirm'), "DRY_RUN");
    expect(confirmBtn).toBeDisabled();
  });

  it("cancel closes the dialog without calling setExecutionMode, and re-opening starts from a clean slate", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    const modeSpy = vi.spyOn(api, "setExecutionMode");
    renderScreen();

    await user.click(await screen.findByRole("button", { name: "🧪 Simulation" }));
    await user.type(screen.getByLabelText('Type "ADVISORY_ONLY" to confirm'), "ADVISORY_ONLY");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog", { name: "Confirm Mode Change" })).not.toBeInTheDocument();
    expect(modeSpy).not.toHaveBeenCalled();

    await user.click(await screen.findByRole("button", { name: "🧪 Simulation" }));
    expect(screen.getByLabelText('Type "ADVISORY_ONLY" to confirm')).toHaveValue("");
    expect(screen.getByTestId("execution-mode-confirm")).toBeDisabled();
  });

  it("a rejected write (e.g. a server-side confirmation mismatch) surfaces the real error and leaves the dialog open, never silently closes", async () => {
    // Pins the fix to the pre-existing bug where confirmChange() called
    // setSelectedMode(null) unconditionally, discarding any error the
    // mutation had just set -- a failed write used to close the dialog as if
    // it had succeeded, with no visible error at all.
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "setExecutionMode").mockRejectedValueOnce(
      new Error("confirmation_required: this change touches safety-critical setting(s).")
    );
    renderScreen();

    await user.click(await screen.findByRole("button", { name: "🔴 Live Production" }));
    await user.type(screen.getByLabelText('Type "ADVISORY_ONLY" to confirm'), "ADVISORY_ONLY");
    await user.type(screen.getByLabelText('Type "DRY_RUN" to confirm'), "DRY_RUN");
    await user.click(screen.getByTestId("execution-mode-confirm"));

    expect(
      await screen.findByText("confirmation_required: this change touches safety-critical setting(s).")
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Confirm Mode Change" })).toBeInTheDocument();
  });
});
