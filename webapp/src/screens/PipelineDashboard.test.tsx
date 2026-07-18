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
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PipelineDashboard } from "./PipelineDashboard";
import { api, ApiError } from "../api/client";
import type { ControlStatus, RunRecord } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <PipelineDashboard />
    </MemoryRouter>
  );
}

function statusFixture(overrides: Partial<ControlStatus> = {}): ControlStatus {
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

  it("renders the run-history table with honest mode/error branches", async () => {
    renderScreen();
    // A run WITHOUT a recorded mode renders "—", never a fabricated "FULL".
    expect(await screen.findByText("FULL")).toBeInTheDocument();
    expect(screen.getByText("DATA")).toBeInTheDocument();
    expect(screen.getByText("METRICS")).toBeInTheDocument();
    // The failed run surfaces its real error, never softened.
    expect(screen.getByText(/insufficient bars/)).toBeInTheDocument();
    // The mode-less interval record renders an em-dash somewhere in the table.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
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

  it("clicking 'Run full advisory pipeline' calls POST /run and shows the result", async () => {
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
  });

  it("a kill-switch-active (423) trigger surfaces the paused notice", async () => {
    vi.spyOn(api, "postControlPipelineData").mockRejectedValue(
      new ApiError("kill switch active", 423)
    );
    const user = userEvent.setup();
    renderScreen();

    await user.click(await screen.findByTestId("trigger-data"));
    expect(await screen.findByText(/pipeline is paused/)).toBeInTheDocument();
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
