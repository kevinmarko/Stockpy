/**
 * Commands.test.tsx — the CLI command bar renders autocomplete suggestions and
 * pre-submit validation hints from the mock manifest, and degrades honestly
 * (reason on an empty manifest; error state on a hard failure) — never a
 * fabricated command list.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Commands } from "./Commands";
import { api, ApiError } from "../api/client";
import { theme } from "../theme";

function renderCommands() {
  return render(
    <MemoryRouter>
      <Commands />
    </MemoryRouter>
  );
}

function type(value: string) {
  fireEvent.change(screen.getByTestId("command-bar-input"), { target: { value } });
}

describe("Commands screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lists available commands from the mock manifest", async () => {
    renderCommands();
    expect(await screen.findByRole("heading", { name: "Commands" })).toBeInTheDocument();
    // Reference list shows the manifest commands while nothing is typed.
    expect(await screen.findByText("validation.harness")).toBeInTheDocument();
    expect(screen.getByText("main.py")).toBeInTheDocument();
  });

  it("renders the manifest's generated_at freshness, never fabricated when null", async () => {
    renderCommands();
    // The mock's generated_at is a fixed past date -> a real "Nd ago" age.
    expect(await screen.findByText(/Manifest generated \d+d ago\./)).toBeInTheDocument();

    vi.spyOn(api, "getCommands").mockResolvedValueOnce({
      generated_at: null,
      command_count: 0,
      dead_letters: [],
      reason: "Run scripts/build_command_manifest.py.",
      commands: [],
    });
    renderCommands();
    expect(await screen.findByText(/Manifest generated unknown\./)).toBeInTheDocument();
  });

  it("suggests a resolved command's options after a space", async () => {
    renderCommands();
    await screen.findByText("main.py");
    type("main.py ");
    const listbox = await screen.findByTestId("command-suggestions");
    expect(within(listbox).getByText("--interval <SECONDS>")).toBeInTheDocument();
    // The default is surfaced in the option's description.
    expect(within(listbox).getByText(/default: 0/)).toBeInTheDocument();
  });

  it("flags a missing required option before submit", async () => {
    renderCommands();
    await screen.findByText("main.py");
    type("validation.harness ");
    const hints = await screen.findByTestId("command-hints");
    expect(within(hints).getByText(/missing required option: --strategy/)).toBeInTheDocument();
  });

  it("composes the runnable command once complete", async () => {
    renderCommands();
    await screen.findByText("main.py");
    type("validation.harness --strategy momentum");
    expect(await screen.findByTestId("command-composed")).toHaveTextContent(
      "python -m validation.harness --strategy momentum"
    );
  });

  it("resolves a subcommand by alias and completes its choices/options", async () => {
    renderCommands();
    await screen.findByText("main.py");
    type("prompt_registry g --");
    const listbox = await screen.findByTestId("command-suggestions");
    expect(within(listbox).getByText("--version")).toBeInTheDocument();
  });

  it("an empty manifest renders the honest reason, never a fabricated command", async () => {
    vi.spyOn(api, "getCommands").mockResolvedValueOnce({
      generated_at: null,
      command_count: 0,
      commands: [],
      reason: "No command manifest yet — run scripts/build_command_manifest.py.",
    });
    renderCommands();
    expect(
      await screen.findByText(/No command manifest yet/)
    ).toBeInTheDocument();
    expect(screen.queryByTestId("command-bar-input")).not.toBeInTheDocument();
  });

  it("a hard failure renders the error state", async () => {
    vi.spyOn(api, "getCommands").mockRejectedValueOnce(new ApiError("boom", 500));
    renderCommands();
    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
  });
});

describe("Robinhood execution queue section (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders queued intents with placeable/blocked badges, never a placement control", async () => {
    renderCommands();
    expect(
      await screen.findByRole("heading", { name: "Robinhood execution queue" })
    ).toBeInTheDocument();

    const rows = await screen.findAllByTestId("execution-intent-row");
    expect(rows).toHaveLength(2);

    const aapl = rows.find((r) => r.textContent?.includes("AAPL"))!;
    expect(within(aapl).getByText("Ready to place")).toBeInTheDocument();

    const tsla = rows.find((r) => r.textContent?.includes("TSLA"))!;
    expect(within(tsla).getByText("Blocked")).toBeInTheDocument();
    expect(within(tsla).getByText(/macro_kill_switch/)).toBeInTheDocument();

    // Compose-only invariant: this section never renders a place/execute button.
    expect(screen.queryByRole("button", { name: /place/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /execute/i })).not.toBeInTheDocument();

    // generated_at freshness is surfaced, not just the boolean `stale` chip.
    expect(screen.getByText("as of 5m ago")).toBeInTheDocument();
  });

  it("renders the Blocked chip in a caution tone, visually distinct from a muted chip", async () => {
    renderCommands();
    const rows = await screen.findAllByTestId("execution-intent-row");

    // The Blocked chip is amber (caution), so a blocked intent reads as blocked
    // at a glance — not the low-emphasis muted grey it used to render as.
    const tsla = rows.find((r) => r.textContent?.includes("TSLA"))!;
    const blocked = within(tsla).getByText("Blocked");
    expect(blocked).toHaveStyle({ color: theme.caution });

    // ...and it is visibly distinct from a genuinely muted/neutral chip on the
    // page (the "n/n placeable" summary chip still uses tone="muted").
    const placeableSummary = screen.getByText("1/2 placeable");
    expect(placeableSummary).toHaveStyle({ color: theme.textMuted });
    expect(blocked.style.color).not.toBe(placeableSummary.style.color);
  });

  it("an empty queue renders the honest reason, never a fabricated order", async () => {
    vi.spyOn(api, "getExecutionQueue").mockResolvedValueOnce({
      generated_at: null,
      mode: "off",
      kill_switch_active: false,
      max_notional_per_order: 0,
      n_intents: 0,
      n_placeable: 0,
      stale: false,
      age_seconds: null,
      intents: [],
      reason: "No execution queue yet — ROBINHOOD_EXECUTION_MODE may be 'off'.",
    });
    renderCommands();
    expect(
      await screen.findByText(/No execution queue yet/)
    ).toBeInTheDocument();
  });

  it("a hard failure renders the error state for this section independently", async () => {
    vi.spyOn(api, "getExecutionQueue").mockRejectedValueOnce(new ApiError("boom", 500));
    renderCommands();
    // The command bar (a separate useApi call) still loads successfully.
    expect(await screen.findByText("main.py")).toBeInTheDocument();
    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
  });
});
