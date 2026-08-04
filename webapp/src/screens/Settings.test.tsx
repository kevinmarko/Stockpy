/**
 * Settings.test.tsx — the "did the pipeline run?" screen, plus its four
 * writes (Run Now, pause/resume, interval, per-pilot re-plan). Exercises
 * the honesty contract GET /automation/status exists for (a daemon restart
 * never renders as a blank/fabricated run record, a kill switch renders its
 * real reason, an interval drift is surfaced rather than silently assumed
 * applied), plus the write UI's own contract: every mutation renders
 * whatever the server actually returned, never assumes success client-side.
 */
import { useEffect } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "./Settings";
import { AutoRefreshProvider } from "../components/AutoRefreshContext";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import type { AutomationSchedule, AutomationStatus, BrokerageLoginJob, BrokerageStatus, Follow, FollowResult, LlmProviderName, LlmStatus, TriggerRunResult } from "../api/types";
import { writeOnboarding, readOnboarding } from "../onboarding";
import { __resetMockDataUniverse } from "../api/mock";

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
      <AutoRefreshProvider>
        <Settings />
      </AutoRefreshProvider>
    </MemoryRouter>
  );
}

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

  it("daemon_json fallback with a confirmed-dead pid renders 'stopped', not the ambiguous 'last known state'", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce({
      ...HEALTHY_STATUS,
      daemon: { ...HEALTHY_STATUS.daemon, alive: false, source: "daemon_json", pid: 99999, pid_alive: false, is_running: null },
    });
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce(HEALTHY_SCHEDULE);
    renderSettings();

    expect(await screen.findByText(/stopped — process not running/)).toBeInTheDocument();
  });

  it("daemon_json fallback with a live-but-unresponsive pid renders that distinction, not 'stopped'", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce({
      ...HEALTHY_STATUS,
      daemon: { ...HEALTHY_STATUS.daemon, alive: false, source: "daemon_json", pid: 1, pid_alive: true, is_running: null },
    });
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce(HEALTHY_SCHEDULE);
    renderSettings();

    expect(await screen.findByText(/process alive, API not responding/)).toBeInTheDocument();
  });

  it("daemon_json fallback with an unknowable pid keeps today's 'last known state' copy", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce({
      ...HEALTHY_STATUS,
      daemon: { ...HEALTHY_STATUS.daemon, alive: false, source: "daemon_json", pid: null, pid_alive: null, is_running: null },
    });
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce(HEALTHY_SCHEDULE);
    renderSettings();

    expect(await screen.findByText(/last known state/)).toBeInTheDocument();
  });

  it("kill switch active renders its real reason, not a generic warning", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce({
      ...HEALTHY_STATUS,
      kill_switch: { active: true, reason: "manual pause for maintenance" },
    });
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce(HEALTHY_SCHEDULE);
    renderSettings();

    // Reason renders twice by design: the Pipeline-status kill-switch notice
    // AND the Signal-generation section's own "Reason: ..." line.
    expect((await screen.findAllByText(/manual pause for maintenance/)).length).toBeGreaterThan(0);
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
    // Errors report's own generated_at freshness, not just the run status's.
    expect(await screen.findByText(/Errors \(60\) · as of \d+d ago/)).toBeInTheDocument();
  });

  it("a clean run (no errors) still surfaces the errors report's generated_at", async () => {
    renderSettings();
    expect(await screen.findByText(/Errors · as of \d+m ago/)).toBeInTheDocument();
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

    // Stage stepper: 4 dots, stage_index 1 -> dot 0 done, dot 1 current, dots 2-3 pending.
    const dots = screen.getAllByTestId("progress-stage-dot");
    expect(dots).toHaveLength(4);
    expect(dots.map((d) => d.dataset.state)).toEqual(["done", "current", "pending", "pending"]);

    // Per-stage symbol counter and server message, both previously unrendered.
    expect(screen.getByText("3/10 symbols in this stage")).toBeInTheDocument();
    expect(screen.getByText("Forecasting AAPL")).toBeInTheDocument();
  });

  it("progress with no per-symbol counter (symbols_total 0) omits that line, not a fabricated 0/0", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValueOnce({
      ...HEALTHY_STATUS,
      progress: {
        run_id: "orch-3", state: "running", stage: "data", stage_index: 0,
        stage_total: 6, symbols_done: 0, symbols_total: 0, percent: 4,
        message: "", started_at: "x", updated_at: "x",
        age_seconds: 5, is_terminal: false, stale: false,
      },
    });
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValueOnce(HEALTHY_SCHEDULE);
    renderSettings();

    expect(await screen.findByText(/data \(1\/6\) · 4%/)).toBeInTheDocument();
    expect(screen.queryByText(/symbols in this stage/)).not.toBeInTheDocument();
    expect(screen.getAllByTestId("progress-stage-dot")).toHaveLength(6);
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

function trigger(overrides: Partial<TriggerRunResult> = {}): TriggerRunResult {
  return {
    ok: true, run_id: "orch-x", state: "queued", error: null,
    existing_run_id: null, kill_switch_reason: null,
    ...overrides,
  };
}

describe("Settings screen — Run Now", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "serviceWorker", { value: {}, configurable: true });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
  });

  it("a successful trigger shows the queued confirmation", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    vi.spyOn(api, "triggerRun").mockResolvedValueOnce(trigger({ run_id: "orch-42" }));
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByText(/Run queued.*orch-42/)).toBeInTheDocument();
  });

  it("already_running renders the existing run id, not a generic error", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    vi.spyOn(api, "triggerRun").mockResolvedValueOnce(
      trigger({ ok: false, run_id: null, state: null, error: "already_running", existing_run_id: "orch-old" })
    );
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByText(/already in flight.*orch-old/)).toBeInTheDocument();
  });

  it("kill_switch_active renders the real reason", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    vi.spyOn(api, "triggerRun").mockResolvedValueOnce(
      trigger({ ok: false, run_id: null, state: null, error: "kill_switch_active", kill_switch_reason: "halted for review" })
    );
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByText(/halted for review/)).toBeInTheDocument();
  });

  it("unavailable renders 'not reachable'", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    vi.spyOn(api, "triggerRun").mockResolvedValueOnce(
      trigger({ ok: false, run_id: null, state: null, error: "unavailable" })
    );
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByText(/not reachable/)).toBeInTheDocument();
  });

  it("disabled while a run is already in flight (daemon.is_running)", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue({
      ...HEALTHY_STATUS,
      daemon: { ...HEALTHY_STATUS.daemon, is_running: true },
    });
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    renderSettings();

    expect(await screen.findByRole("button", { name: "Run now" })).toBeDisabled();
  });
});

describe("Settings screen — Restart daemon", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "serviceWorker", { value: {}, configurable: true });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
  });

  it("happy path: typed RESTART confirm calls restartDaemon and the success notice survives the background status reload it triggers", async () => {
    const user = userEvent.setup();
    // mockResolvedValue (not Once): PipelineStatusSection reloads status
    // after a restart attempt (onRestarted -> reloadStatus), exactly like
    // RunNowButton's onTriggered -- this exercises the SAME
    // stale-while-revalidate path b77438cb fixed, so it must not use a
    // single-shot mock that would leave the second call unresolved.
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    const restartSpy = vi.spyOn(api, "restartDaemon").mockResolvedValueOnce({
      restarting: true,
      message: "Process exiting in ~0.5s. Whether it comes back up depends on the process supervisor.",
    });
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "Restart daemon" }));
    expect(await screen.findByRole("dialog", { name: "Restart daemon" })).toBeInTheDocument();

    // Confirm stays disabled until the typed value exactly matches.
    const confirmBtn = screen.getByTestId("restart-daemon-confirm");
    expect(confirmBtn).toBeDisabled();
    await user.type(screen.getByLabelText('Type "RESTART" to confirm'), "RESTART");
    expect(confirmBtn).toBeEnabled();

    await user.click(confirmBtn);

    expect(restartSpy).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Process exiting in ~0.5s/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Restart daemon" })).not.toBeInTheDocument();

    // The confirm click also triggers a background status reload -- confirm
    // it actually happened, then confirm the success notice is STILL there
    // once it resolves (this is exactly the class of bug b77438cb fixed for
    // RunNowButton: a background reload must never wipe a just-rendered
    // confirmation).
    await waitFor(() => expect(api.getAutomationStatus).toHaveBeenCalledTimes(2));
    expect(screen.getByText(/Process exiting in ~0.5s/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restart daemon" })).toBeInTheDocument();
  });

  it("cancel: closes the dialog without calling restartDaemon", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    const restartSpy = vi.spyOn(api, "restartDaemon");
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "Restart daemon" }));
    await screen.findByRole("dialog", { name: "Restart daemon" });
    await user.type(screen.getByLabelText('Type "RESTART" to confirm'), "RESTART");

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog", { name: "Restart daemon" })).not.toBeInTheDocument();
    expect(restartSpy).not.toHaveBeenCalled();
  });

  it("a mistyped confirmation value (not an exact match) keeps Restart disabled", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "Restart daemon" }));
    const confirmBtn = screen.getByTestId("restart-daemon-confirm");

    await user.type(screen.getByLabelText('Type "RESTART" to confirm'), "restart please");
    expect(confirmBtn).toBeDisabled();
  });

  it("disabled up front while a pipeline run is already in flight (daemon.is_running), mirroring the server's 409 guard", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue({
      ...HEALTHY_STATUS,
      daemon: { ...HEALTHY_STATUS.daemon, is_running: true },
    });
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    renderSettings();

    expect(await screen.findByRole("button", { name: "Restart daemon" })).toBeDisabled();
    expect(screen.getByText(/Disabled while a pipeline run is active/)).toBeInTheDocument();
  });

  it("a restart failure (e.g. auth/gating rejected server-side) surfaces the real error, not a crash", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    vi.spyOn(api, "restartDaemon").mockRejectedValueOnce(new Error("Daemon not available."));
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "Restart daemon" }));
    await user.type(screen.getByLabelText('Type "RESTART" to confirm'), "RESTART");
    await user.click(screen.getByTestId("restart-daemon-confirm"));

    expect(await screen.findByText("Daemon not available.")).toBeInTheDocument();
  });
});

describe("Settings screen — Signal generation (pause/resume)", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "serviceWorker", { value: {}, configurable: true });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
  });

  it("toggling off opens a confirm dialog and pauses with the typed reason", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS); // kill_switch inactive -> running
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    const pauseSpy = vi.spyOn(api, "pauseAutomation").mockResolvedValueOnce({ active: true, reason: "lunch break" });
    renderSettings();

    const toggle = await screen.findByRole("switch", { name: /Signal generation: Running/ });
    await user.click(toggle);
    expect(screen.getByText("Pause signal generation?")).toBeInTheDocument();

    // Save is disabled until a reason is typed.
    const pauseBtn = screen.getByRole("button", { name: "Pause" });
    expect(pauseBtn).toBeDisabled();

    await user.type(screen.getByLabelText("Reason"), "lunch break");
    expect(pauseBtn).not.toBeDisabled();
    await user.click(pauseBtn);

    await waitFor(() => expect(pauseSpy).toHaveBeenCalledWith("lunch break"));
  });

  it("toggling on (from paused) opens a confirm dialog and resumes", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue({
      ...HEALTHY_STATUS,
      kill_switch: { active: true, reason: "was paused" },
    });
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    const resumeSpy = vi.spyOn(api, "resumeAutomation").mockResolvedValueOnce({ active: false, reason: null });
    renderSettings();

    const toggle = await screen.findByRole("switch", { name: /Signal generation: Paused/ });
    await user.click(toggle);
    expect(screen.getByText("Resume signal generation?")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Reason"), "back online");
    await user.click(screen.getByRole("button", { name: "Resume" }));

    await waitFor(() => expect(resumeSpy).toHaveBeenCalledWith("back online"));
  });

  it("resume is disabled from the paused state when advisory_only is false", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue({
      ...HEALTHY_STATUS,
      kill_switch: { active: true, reason: "live halt" },
      advisory_only: false,
    });
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    renderSettings();

    const toggle = await screen.findByRole("switch", { name: /Signal generation: Paused/ });
    expect(toggle).toBeDisabled();
    expect(
      screen.getByText(/Resume must be done at the console/)
    ).toBeInTheDocument();
  });

  it("a pause failure renders the server's error message", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    vi.spyOn(api, "pauseAutomation").mockRejectedValueOnce(new Error("Automation writes are disabled"));
    renderSettings();

    const toggle = await screen.findByRole("switch", { name: /Signal generation: Running/ });
    await user.click(toggle);
    await user.type(screen.getByLabelText("Reason"), "x");
    await user.click(screen.getByRole("button", { name: "Pause" }));

    expect(await screen.findByText("Automation writes are disabled")).toBeInTheDocument();
  });
});

describe("Settings screen — Schedule interval write", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "serviceWorker", { value: {}, configurable: true });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
  });

  it("not writable: shows the note only, no input", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue({
      ...HEALTHY_SCHEDULE,
      interval: { ...HEALTHY_SCHEDULE.interval, writable: false, note: "Writes are disabled." },
    });
    renderSettings();

    expect(await screen.findByText("Writes are disabled.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Configured interval (seconds)")).not.toBeInTheDocument();
  });

  it("writable: an in-range value saves and reloads the schedule", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    const scheduleSpy = vi
      .spyOn(api, "getAutomationSchedule")
      .mockResolvedValue({ ...HEALTHY_SCHEDULE, interval: { ...HEALTHY_SCHEDULE.interval, writable: true } });
    const setSpy = vi
      .spyOn(api, "setAutomationInterval")
      .mockResolvedValueOnce({ configured_value: 600, written: "600", applies: "next_daemon_restart" });
    renderSettings();

    const input = await screen.findByLabelText("Configured interval (seconds)");
    await user.clear(input);
    await user.type(input, "600");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(setSpy).toHaveBeenCalledWith(600));
    // onSaved triggers a reload -- getAutomationSchedule called again after mount.
    await waitFor(() => expect(scheduleSpy.mock.calls.length).toBeGreaterThan(1));
  });

  it("an out-of-range value (1-59) disables Save", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue({
      ...HEALTHY_SCHEDULE,
      interval: { ...HEALTHY_SCHEDULE.interval, writable: true },
    });
    renderSettings();

    const input = await screen.findByLabelText("Configured interval (seconds)");
    await user.clear(input);
    await user.type(input, "30");

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("0 is a valid value (parks the timer)", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue({
      ...HEALTHY_SCHEDULE,
      interval: { ...HEALTHY_SCHEDULE.interval, writable: true },
    });
    renderSettings();

    const input = await screen.findByLabelText("Configured interval (seconds)");
    await user.clear(input);
    await user.type(input, "0");

    expect(screen.getByRole("button", { name: "Save" })).not.toBeDisabled();
  });
});

describe("Settings screen — Active follows / Re-plan", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "serviceWorker", { value: {}, configurable: true });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
  });

  const FOLLOW: Follow = {
    pilot_id: "trend-following", amount: 500,
    created_at: "2026-07-01T00:00:00Z", updated_at: "2026-07-01T00:00:00Z",
    status: "active",
  };

  it("no active follows renders the honest empty state", async () => {
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    vi.spyOn(api, "getFollows").mockResolvedValueOnce([]);
    renderSettings();

    expect(await screen.findByText("No active follows")).toBeInTheDocument();
  });

  it("Re-plan calls follow() with the stored amount and renders queue_written honestly", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    vi.spyOn(api, "getFollows").mockResolvedValueOnce([FOLLOW]);
    const followSpy = vi.spyOn(api, "follow").mockResolvedValueOnce({
      follow: FOLLOW,
      planned_intents: [{ symbol: "AAPL", weight: 1, target_notional: 500, allow_place: false, conviction: 0.8 }],
      mode: "review",
      queue_written: true,
      notional_cap: 2500,
      min_amount: 100,
      notice: "gated",
    } as FollowResult);
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "Re-plan" }));
    expect(followSpy).toHaveBeenCalledWith("trend-following", 500);
    expect(await screen.findByText(/Re-planned — 1 order\(s\) queued\./)).toBeInTheDocument();
  });

  it("queue_written:false renders 'Preview only', never a false success claim", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
    vi.spyOn(api, "getFollows").mockResolvedValueOnce([FOLLOW]);
    vi.spyOn(api, "follow").mockResolvedValueOnce({
      follow: FOLLOW, planned_intents: [], mode: "off", queue_written: false,
      notional_cap: 0, min_amount: 100, notice: "off",
    } as FollowResult);
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "Re-plan" }));
    expect(
      await screen.findByText(/Preview only — execution mode is off, nothing was written\./)
    ).toBeInTheDocument();
  });
});

describe("Settings screen — Brokerage", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "serviceWorker", { value: {}, configurable: true });
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
  });

  /** A `BrokerageLoginJob` fixture, terminal by default -- these tests mock
   *  connectBrokerage/refreshBrokerage's OWN resolved value directly (rather
   *  than exercising useBrokerageLoginJob's 2s poll), so a test can return an
   *  already-succeeded/failed/timed-out job on the very first (202-equivalent)
   *  response and never need fake timers. The poll loop itself is covered by
   *  useBrokerageLoginJob.test.ts and RobinhoodConnectForm.test.tsx. */
  function loginJob(overrides: Partial<BrokerageLoginJob> = {}): BrokerageLoginJob {
    return {
      job_id: "job-1",
      mode: "connect",
      state: "succeeded",
      phase: "done",
      error_code: null,
      seconds_remaining: 170,
      connected: true,
      has_account_snapshot: true,
      ...overrides,
    };
  }

  it("disconnected: renders the Robinhood connect form, no Disconnect button", async () => {
    vi.spyOn(api, "getBrokerageStatus").mockResolvedValue({
      connected: false,
      has_account_snapshot: false,
      auto_refresh_enabled: true,
    });
    renderSettings();

    expect(await screen.findByLabelText(/robinhood email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^password$/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/authenticator/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Disconnect" })).not.toBeInTheDocument();
  });

  it("disconnected: still offers a one-click connect using .env credentials, alongside the typed form", async () => {
    vi.spyOn(api, "getBrokerageStatus").mockResolvedValue({
      connected: false,
      has_account_snapshot: false,
      auto_refresh_enabled: true,
    });
    renderSettings();

    expect(
      await screen.findByRole("button", { name: /connect using \.env credentials/i })
    ).toBeInTheDocument();
    // The typed form is still there too -- not an either/or.
    expect(screen.getByLabelText(/robinhood email/i)).toBeInTheDocument();
  });

  it("disconnected: clicking the .env-credentials button on success flips the section to connected", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getBrokerageStatus")
      .mockResolvedValueOnce({ connected: false, has_account_snapshot: false, auto_refresh_enabled: true })
      .mockResolvedValue({ connected: true, has_account_snapshot: true, auto_refresh_enabled: true });
    const refreshSpy = vi
      .spyOn(api, "refreshBrokerage")
      .mockResolvedValueOnce(loginJob({ mode: "refresh" }));
    renderSettings();

    await user.click(
      await screen.findByRole("button", { name: /connect using \.env credentials/i })
    );

    expect(refreshSpy).toHaveBeenCalledTimes(1);
    // Reloaded /brokerage/status now reports connected -> Disconnect appears,
    // the typed-credential form is gone.
    expect(await screen.findByRole("button", { name: "Disconnect" })).toBeInTheDocument();
    expect(screen.queryByLabelText(/robinhood email/i)).not.toBeInTheDocument();
  });

  it("disconnected: an honest failure (no usable .env credentials) shows the specific reason, form stays put", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getBrokerageStatus").mockResolvedValue({
      connected: false,
      has_account_snapshot: false,
      auto_refresh_enabled: true,
    });
    // The new async contract always 202s the POST itself -- an honest "no
    // credentials to try" failure is discovered via the job's OWN terminal
    // state, not a rejected request.
    vi.spyOn(api, "refreshBrokerage").mockResolvedValueOnce(
      loginJob({ mode: "refresh", state: "failed", error_code: "no_credentials", connected: false, has_account_snapshot: false })
    );
    renderSettings();

    await user.click(
      await screen.findByRole("button", { name: /connect using \.env credentials/i })
    );

    // See the timeout comment on the identically-shaped assertion in
    // "Force fresh login refreshes and shows a success notice" below.
    expect(
      await screen.findByText(/no robinhood credentials were available to try/i, {}, { timeout: 5000 })
    ).toBeInTheDocument();
    // Still disconnected -- the typed form remains available as a fallback.
    expect(screen.getByLabelText(/robinhood email/i)).toBeInTheDocument();
  });

  it("connected: renders status + Disconnect, and never the credential form", async () => {
    vi.spyOn(api, "getBrokerageStatus").mockResolvedValue({
      connected: true,
      has_account_snapshot: true,
      auto_refresh_enabled: true,
    });
    renderSettings();

    expect(await screen.findByRole("button", { name: "Disconnect" })).toBeInTheDocument();
    expect(screen.getByText(/snapshot ready/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^password$/i)).not.toBeInTheDocument();
  });

  it("a successful connect flips the section to the connected state", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getBrokerageStatus")
      .mockResolvedValueOnce({ connected: false, has_account_snapshot: false, auto_refresh_enabled: true })
      .mockResolvedValue({ connected: true, has_account_snapshot: true, auto_refresh_enabled: true });
    const connectSpy = vi.spyOn(api, "connectBrokerage").mockResolvedValueOnce(loginJob());
    renderSettings();

    await user.type(await screen.findByLabelText(/robinhood email/i), "user@example.com");
    await user.type(screen.getByLabelText(/^password$/i), "sUp3rS3cr3t!!");
    await user.click(screen.getByRole("button", { name: /^connect$/i }));

    // Reloaded /brokerage/status now reports connected -> Disconnect appears,
    // the form (and the submitted password) is gone.
    expect(await screen.findByRole("button", { name: "Disconnect" })).toBeInTheDocument();
    expect(connectSpy).toHaveBeenCalledWith({
      username: "user@example.com",
      password: "sUp3rS3cr3t!!",
    });
    expect(document.body.textContent).not.toContain("sUp3rS3cr3t!!");
  });

  it("Disconnect (after confirm) calls the API and returns to the connect form", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getBrokerageStatus")
      .mockResolvedValueOnce({ connected: true, has_account_snapshot: true, auto_refresh_enabled: true })
      .mockResolvedValue({ connected: false, has_account_snapshot: false, auto_refresh_enabled: true });
    const disconnectSpy = vi
      .spyOn(api, "disconnectBrokerage")
      .mockResolvedValueOnce({ connected: false });
    renderSettings();

    await user.click(await screen.findByRole("button", { name: "Disconnect" }));

    // Confirm modal: disambiguate the modal's Disconnect from the section's.
    const dialog = await screen.findByRole("dialog", { name: "Disconnect brokerage" });
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));

    await waitFor(() => expect(disconnectSpy).toHaveBeenCalledTimes(1));
    // Reloaded status now reports disconnected -> the connect form is back.
    expect(await screen.findByLabelText(/robinhood email/i)).toBeInTheDocument();
  });

  it("Disconnect clears a stale 'Force fresh login' success notice, not just the connect form", async () => {
    // Regression: found by manually driving the app in mock mode -- a
    // successful refresh's Notice survived a subsequent Disconnect and kept
    // rendering ("Refreshed just now...") in the now-disconnected section,
    // because the refresh job's state was never reset on disconnect.
    const user = userEvent.setup();
    vi.spyOn(api, "getBrokerageStatus")
      // 1) initial mount, 2) doRefresh's own reload() (still connected --
      // a successful refresh doesn't change credential presence), 3) the
      // reload() after Disconnect actually clears them.
      .mockResolvedValueOnce({ connected: true, has_account_snapshot: true, auto_refresh_enabled: true })
      .mockResolvedValueOnce({ connected: true, has_account_snapshot: true, auto_refresh_enabled: true })
      .mockResolvedValue({ connected: false, has_account_snapshot: false, auto_refresh_enabled: true });
    vi.spyOn(api, "refreshBrokerage").mockResolvedValueOnce(loginJob({ mode: "refresh" }));
    vi.spyOn(api, "disconnectBrokerage").mockResolvedValueOnce({ connected: false });
    renderSettings();

    await user.click(await screen.findByRole("button", { name: /force fresh login/i }));
    // CI-only timing note: this text depends on a fire-and-forget async
    // job-status chain (useBrokerageLoginJob's start() -> mocked
    // refreshBrokerage() -> setJob -> re-render) that the default 1000ms
    // findBy* timeout has been observed to occasionally undershoot on CI's
    // Linux runners (never reproduced locally on macOS across many repeat
    // runs) -- a generous explicit timeout costs nothing on the happy path
    // and removes the flake without changing what's being asserted.
    expect(
      await screen.findByText(/refreshed — robinhood account snapshot updated/i, {}, { timeout: 5000 })
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    const dialog = await screen.findByRole("dialog", { name: "Disconnect brokerage" });
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));

    await screen.findByLabelText(/robinhood email/i); // back to the connect form
    expect(
      screen.queryByText(/refreshed — robinhood account snapshot updated/i)
    ).not.toBeInTheDocument();
  });

  it("A successful typed-credential connect clears a stale '.env credentials' failure notice", async () => {
    // Regression, symmetric to the Disconnect case above: an earlier failed
    // .env-credentials attempt's error Notice survived a subsequent
    // successful typed-form connect.
    const user = userEvent.setup();
    vi.spyOn(api, "getBrokerageStatus")
      // 1) initial mount, 2) doRefresh's own reload() after the failed
      // .env-credentials attempt (still disconnected), 3) RobinhoodConnectForm's
      // onConnected reload() after the typed-form connect actually succeeds.
      .mockResolvedValueOnce({ connected: false, has_account_snapshot: false, auto_refresh_enabled: true })
      .mockResolvedValueOnce({ connected: false, has_account_snapshot: false, auto_refresh_enabled: true })
      .mockResolvedValue({ connected: true, has_account_snapshot: true, auto_refresh_enabled: true });
    vi.spyOn(api, "refreshBrokerage").mockResolvedValueOnce(
      loginJob({ mode: "refresh", state: "failed", error_code: "no_credentials", connected: false, has_account_snapshot: false })
    );
    vi.spyOn(api, "connectBrokerage").mockResolvedValueOnce(loginJob());
    renderSettings();

    await user.click(
      await screen.findByRole("button", { name: /connect using \.env credentials/i })
    );
    // See the timeout comment on the "Force fresh login" success-notice
    // assertion above -- same async job-status chain, same CI-only flake.
    expect(
      await screen.findByText(/no robinhood credentials were available to try/i, {}, { timeout: 5000 })
    ).toBeInTheDocument();

    await user.type(screen.getByLabelText(/robinhood email/i), "user@example.com");
    await user.type(screen.getByLabelText(/^password$/i), "sUp3rS3cr3t!!");
    await user.click(screen.getByRole("button", { name: /try again|^connect$/i }));

    await screen.findByRole("button", { name: "Disconnect" }); // now connected
    expect(
      screen.queryByText(/no robinhood credentials were available to try/i)
    ).not.toBeInTheDocument();
  });

  it("a status-fetch failure shows an ErrorState, not a crash", async () => {
    vi.spyOn(api, "getBrokerageStatus").mockRejectedValueOnce(new Error("brokerage down"));
    renderSettings();

    expect(await screen.findByText("brokerage down")).toBeInTheDocument();
  });

  it("Force fresh login refreshes and shows a success notice", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getBrokerageStatus").mockResolvedValue({
      connected: true,
      has_account_snapshot: true,
      auto_refresh_enabled: true,
    });
    const refreshSpy = vi
      .spyOn(api, "refreshBrokerage")
      .mockResolvedValueOnce(loginJob({ mode: "refresh" }));
    renderSettings();

    await user.click(await screen.findByRole("button", { name: /force fresh login/i }));

    expect(refreshSpy).toHaveBeenCalledTimes(1);
    // See the timeout comment on the identically-shaped assertion in
    // "Disconnect clears a stale 'Force fresh login' success notice" above.
    expect(
      await screen.findByText(/refreshed — robinhood account snapshot updated/i, {}, { timeout: 5000 })
    ).toBeInTheDocument();
  });

  it("Force fresh login timing out shows the honest timeout notice, never claiming denial", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getBrokerageStatus").mockResolvedValue({
      connected: true,
      has_account_snapshot: true,
      auto_refresh_enabled: true,
    });
    vi.spyOn(api, "refreshBrokerage").mockResolvedValueOnce(
      loginJob({
        mode: "refresh",
        state: "timeout",
        phase: "awaiting_approval",
        error_code: "timeout",
        seconds_remaining: 0,
      })
    );
    renderSettings();

    await user.click(await screen.findByRole("button", { name: /force fresh login/i }));

    // See the timeout comment on the identically-shaped assertions above --
    // same async job-status chain, same rare CI-only flake under full-suite
    // CPU contention.
    expect(
      await screen.findByText(/no approval came through in time\. nothing was saved\./i, {}, { timeout: 5000 })
    ).toBeInTheDocument();
    expect(screen.queryByText(/denied/i)).not.toBeInTheDocument();
  });

  it("a refresh request-level failure shows a warning notice, not a crash", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getBrokerageStatus").mockResolvedValue({
      connected: true,
      has_account_snapshot: true,
      auto_refresh_enabled: true,
    });
    vi.spyOn(api, "refreshBrokerage").mockRejectedValueOnce(
      new ApiError("Could not refresh the Robinhood account snapshot.", 502)
    );
    renderSettings();

    await user.click(await screen.findByRole("button", { name: /force fresh login/i }));

    expect(
      await screen.findByText("Could not refresh the Robinhood account snapshot.", {}, { timeout: 5000 })
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// The "AI providers" link card. Full toggle/provider-write coverage and the
// last-real-call telemetry section now live in AIControlCenter.test.tsx --
// this screen only needs to prove the card renders, links to /settings/ai,
// and surfaces the attention indicator (moved out once GET /llm/status grew
// a write path and the section became its own screen).
// ---------------------------------------------------------------------------

const _noCall = (provider: LlmProviderName) => ({
  provider, ok: null, error_kind: null, exception_type: null,
  http_status: null, checked_at: null, age_seconds: null, source: "none" as const,
});

function llmStatus(overrides: Partial<LlmStatus> = {}): LlmStatus {
  return {
    capabilities: [],
    capabilities_source: "test",
    providers: { claude: _noCall("claude"), gemini: _noCall("gemini"), openai: _noCall("openai") },
    providers_source: "test",
    telemetry_note: "Verdicts are recorded from REAL LLM calls only.",
    attention: false,
    attention_reason: null,
    writable: false,
    writable_note: "AI-capability writes are disabled (LLM_WRITES_ENABLED=false).",
    ...overrides,
  };
}

describe("Settings — AI providers link card", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "serviceWorker", { value: {}, configurable: true });
    // The card's siblings need SOME status/schedule to render the screen.
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(HEALTHY_STATUS);
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(HEALTHY_SCHEDULE);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
  });

  it("links to /settings/ai and shows a ready-count summary", async () => {
    vi.spyOn(api, "getLlmStatus").mockResolvedValue(
      llmStatus({
        capabilities: [
          {
            key: "claude_commentary", label: "Analyst rationale commentary",
            trigger: "on_demand", toggle_key: "LLM_COMMENTARY_ENABLED",
            provider_selector_setting: "LLM_COMMENTARY_RATIONALE_PROVIDER",
            provider_keys: ["ANTHROPIC_API_KEY"], active_provider: "claude",
            invalid_provider: null, enabled: true, key_present: true,
            built: true, status: "ready",
          },
          {
            key: "gemini_vision", label: "Gemini chart vision",
            trigger: "on_demand", toggle_key: "LLM_COMMENTARY_ENABLED",
            provider_selector_setting: null,
            provider_keys: ["GEMINI_API_KEY"], active_provider: null,
            invalid_provider: null, enabled: false, key_present: false,
            built: true, status: "disabled",
          },
        ],
      })
    );
    renderSettings();
    expect(await screen.findByText("1/2 ready")).toBeInTheDocument();
    const link = screen.getByText("AI providers").closest("a");
    expect(link).toHaveAttribute("href", "/settings/ai");
  });

  it("shows the attention indicator when a capability needs it", async () => {
    vi.spyOn(api, "getLlmStatus").mockResolvedValue(
      llmStatus({ attention: true, attention_reason: "invalid_key" })
    );
    renderSettings();
    expect(await screen.findByLabelText("needs attention")).toBeInTheDocument();
  });

  it("no attention indicator when nothing needs it", async () => {
    vi.spyOn(api, "getLlmStatus").mockResolvedValue(llmStatus());
    renderSettings();
    await screen.findByText("AI providers");
    expect(screen.queryByLabelText("needs attention")).not.toBeInTheDocument();
  });
});

describe("Settings screen — Tracked universe", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    __resetMockDataUniverse();
  });

  it("renders the Tracked universe card with the seeded chips", async () => {
    renderSettings();
    expect(await screen.findByText("Tracked universe")).toBeInTheDocument();
    expect(await screen.findByTestId("universe-chip-AAPL")).toBeInTheDocument();
  });

  it("renders the coverage-reconciliation diagnostic alongside the manager", async () => {
    renderSettings();
    expect(await screen.findByTestId("universe-coverage")).toBeInTheDocument();
  });
});

describe("Settings screen — Data Auto-Refresh", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the Data Auto-Refresh card with master toggle and interval controls", async () => {
    renderSettings();
    expect(await screen.findByTestId("auto-refresh-section")).toBeInTheDocument();
    expect(screen.getByText("Data Auto-Refresh")).toBeInTheDocument();
    expect(screen.getAllByText("Enable Auto-Refresh")[0]).toBeInTheDocument();
    expect(screen.getAllByText("Pause When Market Closed")[0]).toBeInTheDocument();
  });

  it("toggles master auto-refresh and persists to localStorage", async () => {
    renderSettings();
    const masterToggle = await screen.findByTestId("auto-refresh-master-toggle");

    expect(masterToggle).toHaveAttribute("aria-checked", "false");
    await userEvent.click(masterToggle);
    expect(masterToggle).toHaveAttribute("aria-checked", "true");
    expect(localStorage.getItem("stockpy.auto_refresh.enabled")).toBe("1");
  });

  it("updates interval via preset button clicks", async () => {
    renderSettings();
    const section = await screen.findByTestId("auto-refresh-section");
    const button60s = within(section).getByRole("button", { name: "60s" });

    await userEvent.click(button60s);
    expect(localStorage.getItem("stockpy.auto_refresh.interval_ms")).toBe("60000");
  });

  it("badge reads Disabled by default, and Active with a live category count once master is on and market-closed pausing is off", async () => {
    renderSettings();
    const section = await screen.findByTestId("auto-refresh-section");
    expect(within(section).getByText(/Auto-refresh Disabled/)).toBeInTheDocument();

    // Turn off "pause when market closed" first to avoid time-of-day
    // flakiness (a genuinely-closed market at test-run time would otherwise
    // leave the badge on "Paused — market closed" instead of "Active").
    await userEvent.click(within(section).getByTestId("auto-refresh-pause-closed-toggle"));
    await userEvent.click(within(section).getByTestId("auto-refresh-master-toggle"));

    expect(
      await within(section).findByText(/Auto-refresh Active — 5 of 5 categories, \d+s/)
    ).toBeInTheDocument();
  });

  it("debounced custom-duration input writes to localStorage after the debounce window", async () => {
    const user = userEvent.setup();
    renderSettings();
    const input = await screen.findByLabelText("Custom duration (sec)");

    await user.clear(input);
    await user.type(input, "45");

    await waitFor(
      () => expect(localStorage.getItem("stockpy.auto_refresh.interval_ms")).toBe("45000"),
      { timeout: 3000 }
    );
  });

  it("an out-of-range custom duration shows the invalid hint and never writes", async () => {
    const user = userEvent.setup();
    renderSettings();
    const input = await screen.findByLabelText("Custom duration (sec)");

    await user.clear(input);
    await user.type(input, "2");

    expect(await screen.findByText("Must be between 5 and 86400 seconds.")).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-invalid", "true");

    // Give the debounce window (500ms) time to fire if it were going to --
    // it must not, since the value is out of range.
    await new Promise((resolve) => setTimeout(resolve, 800));
    expect(localStorage.getItem("stockpy.auto_refresh.interval_ms")).toBeNull();
  });

  it("shows the relabeled 'Options & Market Analytics' category, never the old string", async () => {
    renderSettings();
    const section = await screen.findByTestId("auto-refresh-section");
    expect(within(section).getByText("Options & Market Analytics")).toBeInTheDocument();
    expect(within(section).queryByText("Options Matrix & Analytics")).not.toBeInTheDocument();
  });

  it("safety-telemetry toggle defaults on and is independent of the master toggle", async () => {
    renderSettings();
    const section = await screen.findByTestId("auto-refresh-section");
    const safetyToggle = within(section).getByTestId("auto-refresh-safety-telemetry-toggle");
    const masterToggle = within(section).getByTestId("auto-refresh-master-toggle");

    expect(safetyToggle).toHaveAttribute("aria-checked", "true");

    await userEvent.click(masterToggle); // master ON
    await userEvent.click(masterToggle); // master back OFF
    expect(safetyToggle).toHaveAttribute("aria-checked", "true"); // unaffected either way

    await userEvent.click(safetyToggle);
    expect(safetyToggle).toHaveAttribute("aria-checked", "false");
    expect(masterToggle).toHaveAttribute("aria-checked", "false"); // still unaffected
  });

  it("renders the Robinhood section with its own toggle (off by default) and minute-based presets", async () => {
    vi.spyOn(api, "getBrokerageStatus").mockResolvedValue({
      connected: false,
      has_account_snapshot: false,
      auto_refresh_enabled: true,
    } as BrokerageStatus);
    renderSettings();
    const section = await screen.findByTestId("auto-refresh-section");

    const robinhoodToggle = within(section).getByTestId("auto-refresh-robinhood-toggle");
    expect(robinhoodToggle).toHaveAttribute("aria-checked", "false");

    for (const label of ["15m", "30m", "1h", "4h", "12h"]) {
      expect(within(section).getByRole("button", { name: label })).toBeInTheDocument();
    }
    // 1h is the documented default selection.
    expect(await within(section).findByRole("button", { name: "1h" })).toHaveClass("btn-primary");
  });

  it("shows a cached-data-only warning when the backend Robinhood login gate is off", async () => {
    vi.spyOn(api, "getBrokerageStatus").mockResolvedValue({
      connected: false,
      has_account_snapshot: false,
      auto_refresh_enabled: false,
    } as BrokerageStatus);
    renderSettings();

    expect(await screen.findByText(/refresh cached data only/i)).toBeInTheDocument();
  });

  it("does not show the cached-data-only warning when the backend Robinhood login gate is on", async () => {
    vi.spyOn(api, "getBrokerageStatus").mockResolvedValue({
      connected: false,
      has_account_snapshot: false,
      auto_refresh_enabled: true,
    } as BrokerageStatus);
    renderSettings();

    await screen.findByTestId("auto-refresh-robinhood-toggle");
    expect(screen.queryByText(/refresh cached data only/i)).not.toBeInTheDocument();
  });
});
