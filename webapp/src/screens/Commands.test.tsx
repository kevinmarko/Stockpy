/**
 * Commands.test.tsx — the CLI command bar renders autocomplete suggestions and
 * pre-submit validation hints from the mock manifest, and degrades honestly
 * (reason on an empty manifest; error state on a hard failure) — never a
 * fabricated command list.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import toast from "react-hot-toast";
import { Commands } from "./Commands";
import { api, ApiError } from "../api/client";
import { theme } from "../theme";
import type { CommandManifest, CommandSpec, JobRecord } from "../api/types";

// react-hot-toast's `toast()` needs no <Toaster/> mounted to run safely in
// jsdom; mocking it here lets the Run-button tests below assert a toast was
// actually fired for the command-launch outcome, mirroring
// PipelineDashboard.test.tsx's own convention.
vi.mock("react-hot-toast", () => {
  const mock = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: mock };
});

// vi.restoreAllMocks() (used by every describe block's own afterEach below)
// only restores spies created via vi.spyOn -- it leaves the toast mock's
// call history untouched between tests, so clear it explicitly.
beforeEach(() => {
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
});

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

// ---- Fixtures for the "Run button" describe block below ------------------
//
// The shared MOCK_COMMAND_MANIFEST (api/mock.ts) already carries a plain
// `main.py` command with a `--refresh-account` option, so the "main.py
// --refresh-account requires confirmation" test can reuse it unmodified.
// It does NOT carry `execution.kill_switch` (an --activate/--deactivate
// option) or `app_shell.py` (a no-subprocess local-launcher command), so
// those two tests mock `api.getCommands` with a small custom manifest built
// here instead of editing the shared fixture — isolating this test file from
// the parallel agent's own edits to api/mock.ts (per this file's existing
// convention, e.g. the "an empty manifest renders..." test above).
function buildManifest(commands: CommandSpec[]): CommandManifest {
  return {
    generated_at: "2026-07-17T12:00:00+00:00",
    command_count: commands.length,
    dead_letters: [],
    reason: null,
    commands,
  };
}

const KILL_SWITCH_COMMAND: CommandSpec = {
  name: "execution.kill_switch",
  invocation: "python -m execution.kill_switch",
  aliases: [],
  description: "Global kill switch control.",
  positionals: [],
  subcommands: [],
  options: [
    {
      name: "--status",
      aliases: ["--status"],
      description: "show current state",
      default: false,
      choices: null,
      required: false,
      arg_kind: "optional",
      metavar: null,
      takes_value: false,
    },
    {
      name: "--activate",
      aliases: ["--activate"],
      description: "activate the global kill switch",
      default: false,
      choices: null,
      required: false,
      arg_kind: "optional",
      metavar: null,
      takes_value: false,
    },
    {
      name: "--deactivate",
      aliases: ["--deactivate"],
      description: "deactivate the global kill switch",
      default: false,
      choices: null,
      required: false,
      arg_kind: "optional",
      metavar: null,
      takes_value: false,
    },
  ],
};

const APP_SHELL_COMMAND: CommandSpec = {
  name: "app_shell.py",
  invocation: "python3 app_shell.py",
  aliases: [],
  description: "Unified Command Center — native desktop window (pywebview).",
  positionals: [],
  subcommands: [],
  options: [],
};

const MAIN_PY_COMMAND: CommandSpec = {
  name: "main.py",
  invocation: "python3 main.py",
  aliases: [],
  description: "Clean advisory orchestrator.",
  positionals: [],
  subcommands: [],
  options: [
    {
      name: "--interval",
      aliases: ["--interval"],
      description: "refresh cadence in seconds (0 = run once)",
      default: 0,
      choices: null,
      required: false,
      arg_kind: "optional",
      metavar: "SECONDS",
      takes_value: true,
    },
    {
      name: "--refresh-account",
      aliases: ["--refresh-account"],
      description: "force a fresh Robinhood login this run",
      default: false,
      choices: null,
      required: false,
      arg_kind: "optional",
      metavar: null,
      takes_value: false,
    },
  ],
};

/** A believable "running" JobRecord for a command job, cast past the strict
 * `JobType` union (which the parallel agent's implementation is expected to
 * extend with `"command"`) since this test file must stay independently
 * runnable against the spec regardless of that union's exact state. */
function commandJobRecord(job_id: string): JobRecord {
  return {
    job_id,
    job_type: "command" as unknown as JobRecord["job_type"],
    status: "running",
    cancellable: true,
  };
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

describe("Commands screen — Run button", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows a Run button once a valid command is composed, but not while validation errors are present", async () => {
    renderCommands();
    await screen.findByText("main.py");

    // Missing required --strategy -> validation error, no runnable command.
    type("validation.harness ");
    await screen.findByTestId("command-hints");
    const runWhileInvalid = screen.queryByRole("button", { name: /run/i });
    if (runWhileInvalid) {
      // Acceptable alternate implementation: rendered but disabled.
      expect(runWhileInvalid).toBeDisabled();
    } else {
      expect(runWhileInvalid).not.toBeInTheDocument();
    }

    // Completing the command clears the error and reveals an enabled Run button.
    type("validation.harness --strategy momentum");
    const runButton = await screen.findByRole("button", { name: /run/i });
    expect(runButton).toBeEnabled();
  });

  it("clicking Run for a non-high-stakes command calls createJob directly, with no confirmation dialog, and toasts success", async () => {
    const createJobSpy = vi
      .spyOn(api, "createJob")
      .mockResolvedValueOnce(commandJobRecord("mock-job-1"));

    renderCommands();
    await screen.findByText("main.py");
    type("validation.harness --strategy momentum");

    const runButton = await screen.findByRole("button", { name: /run/i });
    fireEvent.click(runButton);

    await waitFor(() =>
      expect(createJobSpy).toHaveBeenCalledWith(
        "command",
        expect.objectContaining({
          command: "validation.harness",
          args: ["--strategy", "momentum"],
          confirm: true,
        })
      )
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("app_shell.py shows no Run button and shows the local/native-window note instead", async () => {
    vi.spyOn(api, "getCommands").mockResolvedValueOnce(buildManifest([APP_SHELL_COMMAND]));

    renderCommands();
    await screen.findByText("app_shell.py");
    type("app_shell.py ");

    await screen.findByTestId("command-composed");
    expect(screen.queryByRole("button", { name: /run/i })).not.toBeInTheDocument();
    // Exact copy may vary; match on either substring the spec calls out. Use
    // the innermost (deepest) matching node to avoid `getByText` tripping
    // over every ancestor whose aggregated textContent also contains it.
    const matches = screen.getAllByText((_, node) => {
      const text = node?.textContent ?? "";
      return /opens a native/i.test(text) || /on the server/i.test(text);
    });
    const innermost = matches.find((el) => !matches.some((other) => other !== el && el.contains(other)));
    expect(innermost).toBeInTheDocument();
  });

  it("execution.kill_switch --activate opens a confirmation dialog before calling createJob", async () => {
    vi.spyOn(api, "getCommands").mockResolvedValueOnce(buildManifest([KILL_SWITCH_COMMAND]));
    const createJobSpy = vi
      .spyOn(api, "createJob")
      .mockResolvedValueOnce(commandJobRecord("mock-job-2"));

    renderCommands();
    await screen.findByText("execution.kill_switch");
    type("execution.kill_switch --activate");

    const runButton = await screen.findByRole("button", { name: /run/i });
    fireEvent.click(runButton);

    const dialog = await screen.findByRole("dialog");
    expect(createJobSpy).not.toHaveBeenCalled();
    // The dialog surfaces the reason and the composed command.
    expect(within(dialog).getByText(/kill_switch/i)).toBeInTheDocument();

    // Cancel closes it without ever calling createJob.
    const cancelButton = within(dialog).getByRole("button", { name: /cancel/i });
    fireEvent.click(cancelButton);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(createJobSpy).not.toHaveBeenCalled();

    // Re-open and confirm this time.
    fireEvent.click(screen.getByRole("button", { name: /run/i }));
    const dialog2 = await screen.findByRole("dialog");
    const confirmButton = within(dialog2)
      .getAllByRole("button")
      .find((b) => b !== within(dialog2).queryByRole("button", { name: /cancel/i }) && /run|activate|yes|confirm/i.test(b.textContent ?? ""));
    expect(confirmButton).toBeDefined();
    fireEvent.click(confirmButton!);

    await waitFor(() =>
      expect(createJobSpy).toHaveBeenCalledWith(
        "command",
        expect.objectContaining({
          command: "execution.kill_switch",
          args: expect.arrayContaining(["--activate"]),
          confirm: true,
        })
      )
    );
  });

  it("execution.kill_switch --deactivate also requires confirmation", async () => {
    vi.spyOn(api, "getCommands").mockResolvedValueOnce(buildManifest([KILL_SWITCH_COMMAND]));
    const createJobSpy = vi
      .spyOn(api, "createJob")
      .mockResolvedValueOnce(commandJobRecord("mock-job-3"));

    renderCommands();
    await screen.findByText("execution.kill_switch");
    type("execution.kill_switch --deactivate");

    const runButton = await screen.findByRole("button", { name: /run/i });
    fireEvent.click(runButton);

    await screen.findByRole("dialog");
    expect(createJobSpy).not.toHaveBeenCalled();
  });

  it("main.py --refresh-account requires confirmation before calling createJob", async () => {
    // main.py + --refresh-account is already in the shared mock manifest, but
    // pin an explicit fixture here too so this test doesn't silently depend on
    // shared-fixture edits made elsewhere.
    vi.spyOn(api, "getCommands").mockResolvedValueOnce(buildManifest([MAIN_PY_COMMAND]));
    const createJobSpy = vi
      .spyOn(api, "createJob")
      .mockResolvedValueOnce(commandJobRecord("mock-job-4"));

    renderCommands();
    await screen.findByText("main.py");
    type("main.py --refresh-account");

    const runButton = await screen.findByRole("button", { name: /run/i });
    fireEvent.click(runButton);

    const dialog = await screen.findByRole("dialog");
    expect(createJobSpy).not.toHaveBeenCalled();

    const confirmButton = within(dialog)
      .getAllByRole("button")
      .find((b) => !/cancel/i.test(b.textContent ?? ""));
    expect(confirmButton).toBeDefined();
    fireEvent.click(confirmButton!);

    await waitFor(() =>
      expect(createJobSpy).toHaveBeenCalledWith(
        "command",
        expect.objectContaining({
          command: "main.py",
          args: expect.arrayContaining(["--refresh-account"]),
          confirm: true,
        })
      )
    );
  });

  it("Configure -> Form Mode -> Run actually launches the command (regression guard for the two run paths drifting apart)", async () => {
    const createJobSpy = vi
      .spyOn(api, "createJob")
      .mockResolvedValueOnce(commandJobRecord("mock-job-5"));

    renderCommands();
    await screen.findByText("main.py");

    fireEvent.click(await screen.findByRole("button", { name: "Configure prompt_registry" }));
    const modal = await screen.findByTestId("command-form-builder");
    fireEvent.change(within(modal).getByLabelText("Select Subcommand"), { target: { value: "list" } });

    fireEvent.click(within(modal).getByTestId("command-run-button"));

    await waitFor(() =>
      expect(createJobSpy).toHaveBeenCalledWith(
        "command",
        expect.objectContaining({
          command: "prompt_registry",
          subcommand: "list",
          args: [],
          confirm: true,
        })
      )
    );
    // The modal stays open so the operator can watch the job status.
    expect(await within(modal).findByTestId("command-run-status")).toBeInTheDocument();
  });

  it("the grid card's copy button confirms the copy instead of giving silent feedback", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    renderCommands();
    await screen.findByText("main.py");

    const copyButton = await screen.findByRole("button", { name: "Copy main.py" });
    expect(copyButton).toHaveTextContent("📋");
    fireEvent.click(copyButton);

    expect(writeText).toHaveBeenCalledWith("python3 main.py");
    expect(copyButton).toHaveTextContent("✅");
  });

  it("a rejected createJob renders an inline error message, toasts a failure, and does not crash", async () => {
    vi.spyOn(api, "createJob").mockRejectedValueOnce(
      new ApiError("COMMAND_EXECUTION_ENABLED is False.", 403)
    );

    renderCommands();
    await screen.findByText("main.py");
    type("validation.harness --strategy momentum");

    const runButton = await screen.findByRole("button", { name: /run/i });
    fireEvent.click(runButton);

    expect(await screen.findByText(/COMMAND_EXECUTION_ENABLED is False/)).toBeInTheDocument();
    // The screen is still up and interactive -- no crash / unmount.
    expect(screen.getByRole("heading", { name: "Commands" })).toBeInTheDocument();
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1));
    expect(toast.success).not.toHaveBeenCalled();
  });
});
