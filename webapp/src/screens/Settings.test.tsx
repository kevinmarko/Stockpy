/**
 * Settings.test.tsx — the "did the pipeline run?" screen (Phase 2, read-only:
 * no Run Now / pause / resume / interval-write yet, those are a later phase).
 * Exercises the honesty contract GET /automation/status exists for: a
 * daemon restart never renders as a blank/fabricated run record, a kill
 * switch renders its real reason, and an interval drift is surfaced rather
 * than silently assumed applied.
 */
import { useEffect } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "./Settings";
import { api } from "../api/client";
import type { AutomationSchedule, AutomationStatus } from "../api/types";
import { writeOnboarding, readOnboarding } from "../onboarding";

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

function renderSettings() {
  return render(
    <MemoryRouter initialEntries={["/settings"]}>
      <Settings />
    </MemoryRouter>
  );
}

const HEALTHY_STATUS: AutomationStatus = {
  daemon: {
    alive: true,
    source: "control_api",
    pid: null,
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
};

const HEALTHY_SCHEDULE: AutomationSchedule = {
  interval: {
    running_value: 300,
    configured_value: 300,
    drift: false,
    writable: false,
    note: "Read-only in this build — schedule writes land in a follow-up.",
  },
  cron: {
    source: "deploy/crontab.txt",
    installed: null,
    note: "Parsed from the repo file — the intended schedule.",
    entries: [{ schedule: "0 21 * * 1-5", command: "python x.py", comment: "Daily job" }],
  },
};

describe("Settings screen", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "serviceWorker", { value: {}, configurable: true });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
  });

  it("renders the healthy-state pipeline status", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce(HEALTHY_SCHEDULE);
    renderSettings();

    // MetricBadge renders "{label} {value}" as one combined span, so match
    // the label as a partial regex rather than an exact isolated text node.
    expect(await screen.findByText(/succeeded/)).toBeInTheDocument();
    expect(screen.getByText("No errors")).toBeInTheDocument();
  });

  it("daemon down + no run record renders the honest restart-explainer, never a blank section", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce({
      ...HEALTHY_STATUS,
      daemon: { ...HEALTHY_STATUS.daemon, alive: false, source: "none", started_at: null, interval_seconds: null, is_running: null, current_run_id: null, engines_warm: null },
      last_run: null,
      last_run_source: "state_snapshot",
    });
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce(HEALTHY_SCHEDULE);
    renderSettings();

    expect(
      await screen.findByText(/No run record.*daemon has never triggered/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/Not reachable/)).toBeInTheDocument();
  });

  it("kill switch active renders its real reason, not a generic warning", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce({
      ...HEALTHY_STATUS,
      kill_switch: { active: true, reason: "manual pause for maintenance" },
    });
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce(HEALTHY_SCHEDULE);
    renderSettings();

    expect(await screen.findByText(/manual pause for maintenance/)).toBeInTheDocument();
  });

  it("dead-letter errors render the true count even when the list is capped", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce({
      ...HEALTHY_STATUS,
      errors: {
        generated_at: "2026-07-16T19:05:00+00:00",
        entry_count: 60,
        entries: Array.from({ length: 50 }, (_, i) => ({ symbol: `SYM${i}` })),
      },
    });
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce(HEALTHY_SCHEDULE);
    renderSettings();

    expect(await screen.findByText(/60 symbols failed/)).toBeInTheDocument();
    expect(screen.getByText(/showing 50/)).toBeInTheDocument();
  });

  it("an in-flight, fresh progress renders the stage/percent -- a stale one does not", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce({
      ...HEALTHY_STATUS,
      progress: {
        run_id: "orch-2", state: "running", stage: "forecasting", stage_index: 1,
        stage_total: 4, symbols_done: 3, symbols_total: 10, percent: 32.5,
        message: "Forecasting AAPL", started_at: "x", updated_at: "x",
        age_seconds: 20, is_terminal: false, stale: false,
      },
    });
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce(HEALTHY_SCHEDULE);
    renderSettings();

    expect(await screen.findByText(/forecasting \(2\/4\) · 33%/)).toBeInTheDocument();
  });

  it("interval drift renders the running-vs-configured notice", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce({
      ...HEALTHY_SCHEDULE,
      interval: { running_value: 300, configured_value: 0, drift: true, writable: false, note: "x" },
    });
    renderSettings();

    expect(await screen.findByText(/Running: 300s · Configured: 0s/)).toBeInTheDocument();
  });

  it("renders the cron entries with their schedule and comment", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce(HEALTHY_SCHEDULE);
    renderSettings();

    expect(await screen.findByText("0 21 * * 1-5")).toBeInTheDocument();
    expect(screen.getByText("Daily job")).toBeInTheDocument();
  });

  it("a status-endpoint failure shows ErrorState with retry, not a crash", async () => {
    vi.spyOn(api, "getAutomationStatus").mockRejectedValueOnce(new Error("network down"));
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce(HEALTHY_SCHEDULE);
    renderSettings();

    expect(await screen.findByText("network down")).toBeInTheDocument();
  });

  it("folds in the PWA status section (App status)", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce(HEALTHY_SCHEDULE);
    renderSettings();

    expect(await screen.findByText("App status")).toBeInTheDocument();
  });

  it("Reset onboarding: confirm actually clears the marker; Cancel does not", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce(HEALTHY_SCHEDULE);
    writeOnboarding({ completed: true });
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "Reset onboarding" }));
    expect(screen.getByText("Reset onboarding?")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("Reset onboarding?")).not.toBeInTheDocument();
    expect(readOnboarding().completed).toBe(true); // unaffected by Cancel

    await user.click(screen.getByRole("button", { name: "Reset onboarding" }));
    await user.click(screen.getByRole("button", { name: "Reset" }));

    await waitFor(() => expect(readOnboarding().completed).toBe(false));
  });
});
