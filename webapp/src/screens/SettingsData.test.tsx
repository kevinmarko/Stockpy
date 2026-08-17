import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsData } from "./SettingsData";
import { api } from "../api/client";
import type { AutomationSchedule, AutomationStatus, TunablesResponse } from "../api/types";
import { AutoRefreshProvider } from "../components/AutoRefreshContext";

const mockSchedule: AutomationSchedule = {
  interval: {
    running_value: 300,
    configured_value: 300,
    drift: false,
    writable: true,
    note: "Valid intervals: 0 (disabled) or 60..86400 seconds.",
  },
  cron: {
    installed: null,
    source: "deploy/crontab.txt",
    entries: [
      {
        schedule: "0 21 * * 1-5",
        command: "main_orchestrator.py",
        comment: "Daily pipeline run",
      },
    ],
    note: "Cron entries from repository.",
  },
};

const mockStatus: AutomationStatus = {
  daemon: {
    alive: true,
    source: "control_api",
    pid: 12345,
    pid_alive: true,
    port: 8601,
    started_at: "2026-08-17T09:00:00Z",
    interval_seconds: 300,
    is_running: false,
    current_run_id: null,
    engines_warm: true,
  },
  last_run: null,
  last_run_source: "daemon_memory",
  pipeline: {
    snapshot_age_seconds: 120,
    snapshot_age_source: "timestamp",
    heartbeat_age_seconds: 30,
    heartbeat_note: "Fresh",
  },
  progress: null,
  kill_switch: {
    active: false,
    reason: null,
  },
  advisory_only: true,
  alpaca_paper: true,
  dry_run: true,
  errors: {
    entries: [],
    entry_count: 0,
    generated_at: "2026-08-17T09:00:00Z",
  },
};

const mockTunables: TunablesResponse = {
  applies: "immediately",
  applies_counts: { immediately: 1, next_daemon_restart: 0, no_effect: 0, env_pinned: 0 },
  groups: [
    {
      name: "Advanced / Config",
      fields: [
        {
          key: "ORCHESTRATOR_EXTENDED_HOURS_ONLY",
          value: true,
          type: "boolean",
          default: true,
          description: "Limit automatic runs to extended hours.",
          liveness: {
            applies: "immediately",
            restart_reason: null,
            capture_sites: [],
            env_pinned: false,
            dangerous: false,
            source: "runtime_store",
          },
        },
      ],
    },
  ],
  env_drift: { detected: false, keys: [], note: "" },
};

describe("SettingsData — Schedule and Data Auto-Refresh", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(api, "getAutomationSchedule").mockResolvedValue(mockSchedule);
    vi.spyOn(api, "getAutomationStatus").mockResolvedValue(mockStatus);
    vi.spyOn(api, "getBrokerageStatus").mockResolvedValue({
      connected: false,
      has_account_snapshot: false,
      auto_refresh_enabled: false,
    });
    vi.spyOn(api, "getTunables").mockResolvedValue(mockTunables);
    vi.spyOn(api, "setAutomationInterval").mockResolvedValue({
      configured_value: 300,
      written: "300",
      applies: "immediately",
    });
    vi.spyOn(api, "updateTunables").mockResolvedValue({
      written: { ORCHESTRATOR_EXTENDED_HOURS_ONLY: false },
      rejected: {},
      applies: "immediately",
      applies_counts: { immediately: 1, next_daemon_restart: 0, no_effect: 0, env_pinned: 0 },
      per_key_applies: { ORCHESTRATOR_EXTENDED_HOURS_ONLY: "immediately" },
      restart_required: false,
      restart_endpoint: "POST /daemon/restart",
      note: "Saved to .env and applied to the running process — no restart needed.",
    });
  });

  const renderComponent = () =>
    render(
      <AutoRefreshProvider>
        <SettingsData />
      </AutoRefreshProvider>
    );

  it("renders the schedule section with active schedule badge and controls", async () => {
    renderComponent();

    await waitFor(() => {
      expect(screen.getByText("Pipeline Schedule")).toBeInTheDocument();
    });

    expect(screen.getByText("Schedule Active (300s)")).toBeInTheDocument();
    expect(screen.getAllByText("Enable Scheduled Pipeline Runs").length).toBeGreaterThan(0);
    expect(screen.getByText("Limit to Extended Market Hours (4 AM – 8 PM ET)")).toBeInTheDocument();
  });

  it("toggling Enable Scheduled Pipeline Runs OFF sets interval to 0", async () => {
    const user = userEvent.setup();
    renderComponent();

    await waitFor(() => {
      expect(screen.getByTestId("pipeline-schedule-master-toggle")).toBeInTheDocument();
    });

    const masterToggle = screen.getByTestId("pipeline-schedule-master-toggle");
    await user.click(masterToggle);

    expect(api.setAutomationInterval).toHaveBeenCalledWith(0);
  });

  it("toggling Limit to Extended Market Hours updates the tunable setting", async () => {
    const user = userEvent.setup();
    renderComponent();

    await waitFor(() => {
      expect(screen.getByTestId("extended-hours-only-toggle")).toBeInTheDocument();
    });

    const hoursToggle = screen.getByTestId("extended-hours-only-toggle");
    await user.click(hoursToggle);

    expect(api.updateTunables).toHaveBeenCalledWith({
      ORCHESTRATOR_EXTENDED_HOURS_ONLY: false,
    });
  });

  it("clicking an interval preset updates the interval immediately", async () => {
    const user = userEvent.setup();
    renderComponent();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "1m" })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "1m" }));
    expect(api.setAutomationInterval).toHaveBeenCalledWith(60);
  });
});
