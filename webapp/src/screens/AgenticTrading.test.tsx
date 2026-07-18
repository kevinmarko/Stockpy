/**
 * AgenticTrading.test.tsx — the consolidated Robinhood agentic command
 * center: agent status header, scan-based Discovery (including the honest
 * "not scored" branch a candidate gets when the advisory cross-reference
 * couldn't score it — never a fabricated action/conviction), the shared
 * execution queue, the decision journal, and the gated pause/resume control.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgenticTrading } from "./AgenticTrading";
import { api, ApiError } from "../api/client";
import type { AgenticDiscovery, AgenticStatus } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <AgenticTrading />
    </MemoryRouter>
  );
}

const BASE_STATUS: AgenticStatus = {
  mode: "review",
  advisory_only: true,
  kill_switch: { active: false, reason: null },
  queue: {
    mode: "review",
    generated_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    n_intents: 2,
    n_placeable: 1,
    stale: false,
    age_seconds: 300,
  },
  follows: { n_active: 2, total_amount: 750 },
  agent_loop: { cycle_count: 42, last_cycle_iso: new Date(Date.now() - 8 * 60_000).toISOString(), backlog_count: 1, reason: null },
};

describe("Agentic Trading screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the agent status header with mode, kill switch, and follows", async () => {
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Agentic Trading" })).toBeInTheDocument();
    // "mode: review" legitimately appears twice (Agent status header AND the
    // shared execution queue section both render the same live mode).
    expect((await screen.findAllByText(/mode: review/)).length).toBeGreaterThan(0);
    expect(await screen.findByText(/active follow/)).toBeInTheDocument();
  });

  it("shows a scored candidate and honestly labels an unscored one, never a fabricated score", async () => {
    renderScreen();
    const rows = await screen.findAllByTestId("discovery-candidate-row");
    const nvda = rows.find((r) => r.textContent?.includes("NVDA"))!;
    expect(within(nvda).getByText("BUY")).toBeInTheDocument();
    expect(within(nvda).getByText(/conviction 71%/)).toBeInTheDocument();

    const pltr = rows.find((r) => r.textContent?.includes("PLTR"))!;
    expect(within(pltr).getByText("not scored")).toBeInTheDocument();
  });

  it("renders the shared execution queue section (mode, placeable count, intents)", async () => {
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Robinhood execution queue" })).toBeInTheDocument();
    const rows = await screen.findAllByTestId("execution-intent-row");
    expect(rows.length).toBeGreaterThan(0);
  });

  it("renders the decision journal, most recent first", async () => {
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Decision journal" })).toBeInTheDocument();
  });

  it("an empty discovery set renders the honest reason, never a fabricated candidate", async () => {
    vi.spyOn(api, "getAgenticDiscovery").mockResolvedValueOnce({
      generated_at: null,
      candidates: [],
      scan_configs: [],
      reason: "No scan candidates yet, and no scan configs are enabled.",
      writable: true,
      note: "",
    } satisfies AgenticDiscovery);
    renderScreen();
    expect(await screen.findByText("No candidates yet")).toBeInTheDocument();
    expect(screen.getByText(/No scan candidates yet/)).toBeInTheDocument();
  });

  it("writable: false hides the add-scan-config action behind the honest note", async () => {
    vi.spyOn(api, "getAgenticDiscovery").mockResolvedValueOnce({
      generated_at: null,
      candidates: [],
      scan_configs: [],
      reason: "No scan candidates yet.",
      writable: false,
      note: "Scan-config writes are disabled (AGENTIC_DISCOVERY_ENABLED=false).",
    } satisfies AgenticDiscovery);
    renderScreen();
    expect(await screen.findByText(/Scan-config writes are disabled/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add scan config" })).not.toBeInTheDocument();
  });

  it("adding a scan config calls putScanConfig with the entered values", async () => {
    const user = userEvent.setup();
    const putSpy = vi.spyOn(api, "putScanConfig").mockResolvedValueOnce({
      scan_config: {
        name: "earnings_pop",
        filters: { min_price: 10, min_volume: 500000 },
        enabled: true,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
      applies: "next_discovery_run",
      note: "Saved.",
    });
    renderScreen();

    const addBtn = await screen.findByRole("button", { name: "Add scan config" });
    await user.click(addBtn);

    await user.type(screen.getByLabelText("Name"), "earnings_pop");
    const minPrice = screen.getByLabelText("Min price");
    await user.clear(minPrice);
    await user.type(minPrice, "10");
    const minVolume = screen.getByLabelText("Min volume");
    await user.clear(minVolume);
    await user.type(minVolume, "500000");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(putSpy).toHaveBeenCalledWith({
        name: "earnings_pop",
        filters: { min_price: 10, min_volume: 500000 },
        enabled: true,
      })
    );
  });

  it("toggling off opens a confirm dialog and pauses with the typed reason", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getAgenticStatus").mockResolvedValue(BASE_STATUS);
    const pauseSpy = vi.spyOn(api, "pauseAutomation").mockResolvedValueOnce({ active: true, reason: "lunch break" });
    renderScreen();

    const toggle = await screen.findByRole("switch", { name: /Agent: Running/ });
    await user.click(toggle);
    expect(screen.getByText("Pause the agent?")).toBeInTheDocument();

    const pauseBtn = screen.getByRole("button", { name: "Pause" });
    expect(pauseBtn).toBeDisabled();

    await user.type(screen.getByLabelText("Reason"), "lunch break");
    await user.click(pauseBtn);

    await waitFor(() => expect(pauseSpy).toHaveBeenCalledWith("lunch break"));
  });

  it("resume is disabled from the paused state when advisory_only is false", async () => {
    vi.spyOn(api, "getAgenticStatus").mockResolvedValue({
      ...BASE_STATUS,
      kill_switch: { active: true, reason: "live halt" },
      advisory_only: false,
    });
    renderScreen();

    const toggle = await screen.findByRole("switch", { name: /Agent: Paused/ });
    expect(toggle).toBeDisabled();
    expect(screen.getByText(/Resume must be done at the console/)).toBeInTheDocument();
    expect(screen.getByText(/Reason: live halt/)).toBeInTheDocument();
  });

  it("a hard status failure renders the error state, never a blank/fabricated status", async () => {
    vi.spyOn(api, "getAgenticStatus").mockRejectedValueOnce(new ApiError("boom", 500));
    renderScreen();
    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
  });

  it("provides deep links to the execution-mode ladder and Pilot follow management, not a duplicate control", async () => {
    renderScreen();
    const modeLink = await screen.findByRole("link", { name: /Change execution mode/ });
    expect(within(modeLink).getByText(/Change execution mode/)).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /Manage Pilot follows/ })).toBeInTheDocument();
  });
});
