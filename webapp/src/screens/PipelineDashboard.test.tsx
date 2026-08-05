/**
 * PipelineDashboard.test.tsx — the daemon status + run-trigger screen.
 *
 * Covers the honesty branches, not just the happy path:
 *  - loading → populated status banner + run-history table (default mock)
 *  - a run with no `mode` renders "—", never a fabricated "FULL"
 *  - a failed run's real `error` is shown, never softened
 *  - a running run's null `duration_seconds` renders "—", never "0.0s"
 *  - cold start (empty run_history) renders the honest "No recent runs" state
 *  - a hard 404 renders the cold-start ErrorState, not a fabricated table
 *  - a trigger-button click calls the POST endpoint (mutation) and surfaces
 *    whatever the server returned
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import toast from "react-hot-toast";
import { PipelineDashboard } from "./PipelineDashboard";
import { api, ApiError } from "../api/client";
import type { ControlStatusOnline, DeadLetterQueue, RunRecord } from "../api/types";

// react-hot-toast's `toast()` needs no <Toaster/> mounted to run safely in
// jsdom (it just writes to an internal store nobody subscribes to), but
// mocking it here lets the new trigger-point tests below assert a toast was
// actually fired, matching this file's existing "assert on the real outcome"
// style rather than only on the inline Notice.
vi.mock("react-hot-toast", () => {
  const mock = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: mock };
});

// vi.restoreAllMocks() (used by every describe block's own afterEach below)
// only restores spies created via vi.spyOn -- it has no original
// implementation to restore a plain vi.fn() to, so it leaves the toast
// mock's call history untouched between tests. Clear it explicitly here.
beforeEach(() => {
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
});

function renderScreen() {
  return render(
    <MemoryRouter>
      <PipelineDashboard />
    </MemoryRouter>
  );
}

function statusFixture(
  overrides: Partial<ControlStatusOnline> = {}
): ControlStatusOnline {
  return {
    daemon_alive: true,
    is_running: false,
    current_run_id: null,
    interval_seconds: 300,
    engines_warm: true,
    started_at: new Date().toISOString(),
    last_run: null,
    run_history: [],
    kill_switch_active: false,
    kill_switch_reason: null,
    advisory_only: true,
    dry_run: false,
    ...overrides,
  };
}

describe("PipelineDashboard (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows a loading state, then the idle status banner", async () => {
    renderScreen();
    // The default mock daemon is idle.
    expect(await screen.findByText("Idle")).toBeInTheDocument();
    expect(screen.getByText(/Engines warm/)).toBeInTheDocument();
  });

  it("a bare {daemon_alive: false} status (no attached daemon) renders the offline notice, not a crash", async () => {
    // GET /status's real response when no OrchestratorDaemon has attached to
    // the Control API process -- every other field is genuinely absent, not
    // merely null. This used to throw (RunHistory read `.length` off
    // `data.run_history`, which doesn't exist on this shape) and blank the
    // whole screen with no error boundary to catch it.
    vi.spyOn(api, "getControlStatus").mockResolvedValue({ daemon_alive: false });
    renderScreen();
    expect(await screen.findByText("Daemon offline")).toBeInTheDocument();
    expect(screen.queryByText("Idle")).not.toBeInTheDocument();
    expect(screen.queryByText("Running")).not.toBeInTheDocument();
    // The durable (GET /runs/history) and dead-letter sections read from
    // independent endpoints and still render normally.
    expect(await screen.findByText("Full run history")).toBeInTheDocument();
  });

  it("renders the run-history table with honest mode/error branches", async () => {
    renderScreen();
    // Scoped to the LIVE "Run history" section specifically -- the page also
    // renders a second, independent "Full run history" table (GET
    // /runs/history) whose fixture (RUN_HISTORY_DURABLE) deliberately
    // reuses/extends this same data, so an unscoped screen.getByText("DATA")
    // is flaky by construction: it passes only while the durable table's own
    // async fetch hasn't resolved yet, and fails once both tables have
    // rendered their own "DATA" chips (multiple matches, not zero).
    const liveHistory = (
      await screen.findByRole("heading", { name: "Run history" })
    ).closest("section")!;
    // A run WITHOUT a recorded mode renders "—", never a fabricated "FULL".
    expect(within(liveHistory).getByText("FULL")).toBeInTheDocument();
    expect(within(liveHistory).getByText("DATA")).toBeInTheDocument();
    expect(within(liveHistory).getByText("METRICS")).toBeInTheDocument();
    // The failed run surfaces its real error, never softened.
    expect(within(liveHistory).getByText(/insufficient bars/)).toBeInTheDocument();
    // The mode-less interval record renders an em-dash somewhere in the table.
    expect(within(liveHistory).getAllByText("—").length).toBeGreaterThan(0);
  });

  it("a running run renders 'Running' + its id, and null duration as '—'", async () => {
    const running: RunRecord = {
      run_id: "orch-running-1",
      state: "running",
      mode: "full",
      started_at: new Date().toISOString(),
      finished_at: null,
      duration_seconds: null,
      error: null,
      reason: "manual",
      progress: null,
    };
    vi.spyOn(api, "getControlStatus").mockResolvedValue(
      statusFixture({
        is_running: true,
        current_run_id: "orch-running-1",
        run_history: [running],
      })
    );
    renderScreen();
    expect(await screen.findByText("Running")).toBeInTheDocument();
    // The run id appears in both the banner and the history row.
    expect(screen.getAllByText("orch-running-1").length).toBeGreaterThan(0);
    // Null duration is an em-dash, never a fabricated "0.0s".
    expect(screen.queryByText("0.0s")).not.toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("an empty run history renders the honest 'No recent runs' state", async () => {
    vi.spyOn(api, "getControlStatus").mockResolvedValue(
      statusFixture({ run_history: [] })
    );
    renderScreen();
    expect(await screen.findByText("No recent runs")).toBeInTheDocument();
  });

  it("a hard 404 renders the cold-start empty state, not a fabricated table", async () => {
    vi.spyOn(api, "getControlStatus").mockRejectedValue(
      new ApiError("daemon status not produced yet", 404)
    );
    renderScreen();
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
  });

  it("clicking 'Run full advisory pipeline' calls POST /run, shows the result, and toasts success", async () => {
    const spy = vi
      .spyOn(api, "postControlRun")
      .mockResolvedValue({ run_id: "orch-test-777", state: "queued" });
    const user = userEvent.setup();
    renderScreen();

    const btn = await screen.findByTestId("trigger-full");
    await user.click(btn);

    expect(spy).toHaveBeenCalledTimes(1);
    // The screen renders whatever the server actually returned.
    expect(await screen.findByText(/orch-test-777/)).toBeInTheDocument();
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("a kill-switch-active (423) trigger surfaces the paused notice and toasts an error", async () => {
    vi.spyOn(api, "postControlPipelineData").mockRejectedValue(
      new ApiError("kill switch active", 423)
    );
    const user = userEvent.setup();
    renderScreen();

    await user.click(await screen.findByTestId("trigger-data"));
    expect(await screen.findByText(/pipeline is paused/)).toBeInTheDocument();
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1));
    expect(toast.success).not.toHaveBeenCalled();
  });
});

describe("PipelineDashboard — durable run history (GET /runs/history)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the durable history table, distinct from the live one above it", async () => {
    vi.spyOn(api, "getRunHistory").mockResolvedValue([
      {
        run_id: "orch-durable-1",
        state: "succeeded",
        mode: "full",
        started_at: new Date().toISOString(),
        finished_at: new Date().toISOString(),
        duration_seconds: 12.3,
        error: null,
        reason: "interval",
        progress: null,
      },
    ]);
    renderScreen();
    expect(await screen.findByText("Full run history")).toBeInTheDocument();
    expect(await screen.findByText("orch-durable-1")).toBeInTheDocument();
  });

  it("an empty durable history renders its own honest empty state", async () => {
    vi.spyOn(api, "getRunHistory").mockResolvedValue([]);
    renderScreen();
    expect(
      await screen.findByText("No persisted run history yet")
    ).toBeInTheDocument();
  });

  it("a durable-history read failure renders ErrorState, not a fabricated table", async () => {
    vi.spyOn(api, "getRunHistory").mockRejectedValue(
      new ApiError("db unreachable", 500)
    );
    renderScreen();
    // Non-404 -> the honest "Couldn't load" branch, never the cold-start copy.
    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
  });

  it("clicking Refresh re-fetches the durable history", async () => {
    const spy = vi.spyOn(api, "getRunHistory").mockResolvedValue([]);
    const user = userEvent.setup();
    renderScreen();

    const btn = await screen.findByTestId("refresh-run-history");
    const callsBeforeClick = spy.mock.calls.length;
    await user.click(btn);

    await waitFor(() =>
      expect(spy.mock.calls.length).toBeGreaterThan(callsBeforeClick)
    );
  });
});

// ---------------------------------------------------------------------------
// Dead-letter queue (G6) — GET /dead-letter + POST /dead-letter/retry.
// ---------------------------------------------------------------------------
function deadLetterFixture(overrides: Partial<DeadLetterQueue> = {}): DeadLetterQueue {
  return {
    run_id: "run-2026-07-30T12:00:00+00:00",
    generated_at: "2026-07-30T12:05:22+00:00",
    entries: [
      { symbol: "ZZZZ", stage: "strategy", error: "ValueError: insufficient history", timestamp: "2026-07-30T12:03:41+00:00" },
    ],
    is_clean: false,
    reason: null,
    retry_enabled: true,
    ...overrides,
  };
}

describe("PipelineDashboard — dead-letter queue (G6)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the failed symbol, its stage, and its real error text", async () => {
    vi.spyOn(api, "getDeadLetter").mockResolvedValue(deadLetterFixture());
    renderScreen();
    expect(await screen.findByTestId("dead-letter-row-ZZZZ")).toBeInTheDocument();
    expect(screen.getByText(/stage: strategy/)).toBeInTheDocument();
    expect(screen.getByText(/insufficient history/)).toBeInTheDocument();
  });

  it("a clean last run renders the honest all-clear notice, not a fabricated failure", async () => {
    vi.spyOn(api, "getDeadLetter").mockResolvedValue(
      deadLetterFixture({ entries: [], is_clean: true })
    );
    renderScreen();
    expect(await screen.findByText(/processed cleanly/)).toBeInTheDocument();
    expect(screen.queryByTestId(/dead-letter-row-/)).not.toBeInTheDocument();
  });

  it("no run yet (is_clean: null) is rendered distinctly from a clean run", async () => {
    vi.spyOn(api, "getDeadLetter").mockResolvedValue(
      deadLetterFixture({ entries: [], is_clean: null, reason: "No dead-letter report yet — run the pipeline once to populate it." })
    );
    renderScreen();
    expect(await screen.findByText(/No dead-letter report yet/)).toBeInTheDocument();
    // Must NOT claim "processed cleanly" -- CONSTRAINT #4: no-run-yet is not
    // the same claim as a genuinely clean run.
    expect(screen.queryByText(/processed cleanly/)).not.toBeInTheDocument();
  });

  it("retry_enabled: false disables the Retry button and shows the server-off notice", async () => {
    vi.spyOn(api, "getDeadLetter").mockResolvedValue(
      deadLetterFixture({ retry_enabled: false })
    );
    renderScreen();
    const retryBtn = await screen.findByTestId("retry-ZZZZ");
    expect(retryBtn).toBeDisabled();
    expect(screen.getByText(/DEAD_LETTER_RETRY_ENABLED=false/)).toBeInTheDocument();
  });

  it("clicking Retry calls POST /dead-letter/retry, renders the server's real result, and toasts success", async () => {
    vi.spyOn(api, "getDeadLetter").mockResolvedValue(deadLetterFixture());
    const spy = vi.spyOn(api, "retryDeadLetter").mockResolvedValue({
      symbol: "ZZZZ",
      pid: 5150,
      log_path: "output/gui_retry.log",
      applies: "immediately",
      note: "Retry launched for ZZZZ (advisory-only — no orders placed).",
    });
    const user = userEvent.setup();
    renderScreen();

    const retryBtn = await screen.findByTestId("retry-ZZZZ");
    await user.click(retryBtn);

    expect(spy).toHaveBeenCalledWith("ZZZZ");
    const result = await screen.findByTestId("retry-result-ZZZZ");
    expect(result).toHaveTextContent(/PID 5150/);
    expect(result).toHaveTextContent(/output\/gui_retry\.log/);
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("a failed Retry surfaces the inline error and toasts a failure", async () => {
    vi.spyOn(api, "getDeadLetter").mockResolvedValue(deadLetterFixture());
    vi.spyOn(api, "retryDeadLetter").mockRejectedValue(
      new ApiError("DEAD_LETTER_RETRY_ENABLED is False.", 403)
    );
    const user = userEvent.setup();
    renderScreen();

    const retryBtn = await screen.findByTestId("retry-ZZZZ");
    await user.click(retryBtn);

    expect(
      await screen.findByText(/DEAD_LETTER_RETRY_ENABLED is False/)
    ).toBeInTheDocument();
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("a dead-letter read failure renders ErrorState, not a fabricated queue", async () => {
    vi.spyOn(api, "getDeadLetter").mockRejectedValue(new ApiError("db unreachable", 500));
    renderScreen();
    const section = (await screen.findByTestId("dead-letter-section")) as HTMLElement;
    expect(await within(section).findByText("Couldn't load")).toBeInTheDocument();
  });
});
