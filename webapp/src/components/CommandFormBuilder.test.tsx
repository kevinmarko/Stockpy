/**
 * CommandFormBuilder.test.tsx — the "Form Mode" drawer that maps a
 * CommandSpec's options onto visual controls (toggle/select/date/text) and
 * compiles them into a runnable CLI string. Covers each control kind, the
 * subcommand switch, Reset, the Close button, and the embedded Run control
 * (which reuses RunCommandControl's exact job-creation logic -- see
 * Commands.test.tsx's "Run button" describe block for the sibling coverage
 * of that same control via the free-text Command Bar's entry point).
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import toast from "react-hot-toast";
import { CommandFormBuilder } from "./CommandFormBuilder";
import { api } from "../api/client";
import { REGISTERED_STRATEGIES } from "../commandParse";
import type { CommandSpec, JobRecord } from "../api/types";

// Mirrors Commands.test.tsx's exact convention: react-hot-toast's `toast()`
// needs no <Toaster/> mounted to run safely in jsdom.
vi.mock("react-hot-toast", () => {
  const mock = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: mock };
});

beforeEach(() => {
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
});

/** A believable "running" JobRecord for a command job -- mirrors
 * Commands.test.tsx's own commandJobRecord helper. */
function commandJobRecord(job_id: string): JobRecord {
  return {
    job_id,
    job_type: "command" as unknown as JobRecord["job_type"],
    status: "running",
    cancellable: true,
  };
}

const HARNESS_COMMAND: CommandSpec = {
  name: "validation.harness",
  invocation: "python3 -m validation.harness",
  aliases: [],
  description: "Run the strategy validation harness.",
  positionals: [],
  subcommands: [],
  options: [
    {
      name: "--strategy",
      aliases: ["--strategy"],
      description: "Strategy to validate",
      default: null,
      choices: null,
      required: true,
      arg_kind: "optional",
      metavar: "NAME",
      takes_value: true,
    },
    {
      name: "--start",
      aliases: ["--start"],
      description: "Start date",
      default: null,
      choices: null,
      required: true,
      arg_kind: "optional",
      metavar: "YYYY-MM-DD",
      takes_value: true,
    },
    {
      name: "--dry-run",
      aliases: ["--dry-run"],
      description: "Do not persist results",
      default: false,
      choices: null,
      required: false,
      arg_kind: "optional",
      metavar: null,
      takes_value: false,
    },
    {
      name: "--format",
      aliases: ["--format"],
      description: "Output format",
      default: "json",
      choices: ["json", "html"],
      required: false,
      arg_kind: "optional",
      metavar: null,
      takes_value: true,
    },
  ],
};

const REFRESH_VALIDATIONS_COMMAND: CommandSpec = {
  name: "refresh_validations.py",
  invocation: "python -m scripts.refresh_validations",
  aliases: [],
  description: "Walk-forward strategy validation cadence (concurrent).",
  positionals: [],
  subcommands: [],
  options: [
    {
      name: "--strategies",
      aliases: ["--strategies"],
      description: "Comma-separated strategies to validate; omit for the whole registry.",
      default: null,
      choices: null,
      required: false,
      arg_kind: "optional",
      metavar: "NAMES",
      takes_value: true,
    },
    {
      name: "--workers",
      aliases: ["--workers"],
      description: "Concurrent workers",
      default: "4",
      choices: null,
      required: false,
      arg_kind: "optional",
      metavar: "N",
      takes_value: true,
    },
  ],
};

const FAKE_STRATEGY_REGISTRY = ["alpha_strat", "beta_strat", "gamma_strat", "delta_strat", "epsilon_strat"];

const NO_OPTIONS_COMMAND: CommandSpec = {
  name: "scripts/preflight_check.py",
  invocation: "python3 scripts/preflight_check.py",
  aliases: [],
  description: null,
  positionals: [],
  subcommands: [],
  options: [],
};

const PARENT_WITH_SUBCOMMANDS: CommandSpec = {
  name: "prompt_registry",
  invocation: "python3 -m prompt_registry",
  aliases: [],
  description: "Prompt registry CLI.",
  positionals: [],
  subcommands: [
    {
      name: "list",
      invocation: "list",
      aliases: [],
      description: "List prompts",
      positionals: [],
      subcommands: [],
      options: [],
    },
    {
      name: "pin",
      invocation: "pin",
      aliases: [],
      description: "Pin a version",
      positionals: [],
      subcommands: [],
      options: [
        {
          name: "--version",
          aliases: ["--version"],
          description: "Version to pin",
          default: null,
          choices: null,
          required: true,
          arg_kind: "optional",
          metavar: null,
          takes_value: true,
        },
      ],
    },
  ],
  options: [],
};

const KILL_SWITCH_COMMAND: CommandSpec = {
  name: "execution.kill_switch",
  invocation: "python -m execution.kill_switch",
  aliases: [],
  description: "Global kill switch control.",
  positionals: [],
  subcommands: [],
  options: [
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
  ],
};

describe("CommandFormBuilder", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns null when command is null", () => {
    const { container } = render(
      <CommandFormBuilder command={null} onClose={vi.fn()} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders an honest empty-state message when the command has no options", () => {
    render(<CommandFormBuilder command={NO_OPTIONS_COMMAND} onClose={vi.fn()} />);
    expect(
      screen.getByText(/takes no additional flags or parameters/i)
    ).toBeInTheDocument();
  });

  it("a strategy option with no manifest choices falls back to REGISTERED_STRATEGIES", () => {
    render(<CommandFormBuilder command={HARNESS_COMMAND} onClose={vi.fn()} />);
    const select = screen.getByDisplayValue("-- Select --strategy --");
    for (const strategy of REGISTERED_STRATEGIES) {
      expect(within(select).getByRole("option", { name: strategy })).toBeInTheDocument();
    }
  });

  it("an option whose name includes 'start' renders a date input", () => {
    render(<CommandFormBuilder command={HARNESS_COMMAND} onClose={vi.fn()} />);
    const dateInput = document.querySelector('input[type="date"]');
    expect(dateInput).not.toBeNull();
  });

  it("an option with explicit manifest choices uses those, not the strategy fallback", () => {
    render(<CommandFormBuilder command={HARNESS_COMMAND} onClose={vi.fn()} />);
    const select = screen.getByDisplayValue("json");
    expect(within(select).getByRole("option", { name: "json" })).toBeInTheDocument();
    expect(within(select).getByRole("option", { name: "html" })).toBeInTheDocument();
    expect(within(select).queryByRole("option", { name: REGISTERED_STRATEGIES[0] })).not.toBeInTheDocument();
  });

  it("toggling a boolean flag and filling required fields updates the compiled command", async () => {
    const user = userEvent.setup();
    render(<CommandFormBuilder command={HARNESS_COMMAND} onClose={vi.fn()} />);

    await user.selectOptions(screen.getByDisplayValue("-- Select --strategy --"), REGISTERED_STRATEGIES[0]);
    const dateInput = document.querySelector('input[type="date"]') as HTMLInputElement;
    await user.type(dateInput, "2026-01-01");
    await user.click(screen.getByRole("switch", { name: "--dry-run" }));

    const composed = screen.getByTestId("command-composed");
    expect(composed.textContent).toContain("--strategy");
    expect(composed.textContent).toContain(REGISTERED_STRATEGIES[0]);
    expect(composed.textContent).toContain("--start");
    expect(composed.textContent).toContain("2026-01-01");
    expect(composed.textContent).toContain("--dry-run");
  });

  it("Reset clears entered values back to their manifest defaults", async () => {
    const user = userEvent.setup();
    render(<CommandFormBuilder command={HARNESS_COMMAND} onClose={vi.fn()} />);

    await user.click(screen.getByRole("switch", { name: "--dry-run" }));
    expect(screen.getByRole("switch", { name: "--dry-run" })).toHaveAttribute("aria-checked", "true");

    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(screen.getByRole("switch", { name: "--dry-run" })).toHaveAttribute("aria-checked", "false");
  });

  it("switching the subcommand selector swaps the rendered option set", async () => {
    const user = userEvent.setup();
    render(<CommandFormBuilder command={PARENT_WITH_SUBCOMMANDS} onClose={vi.fn()} />);

    expect(screen.queryByText("--version")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Select Subcommand"), "pin");
    expect(screen.getByText("--version")).toBeInTheDocument();
  });

  it("Close calls onClose", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<CommandFormBuilder command={NO_OPTIONS_COMMAND} onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("a Run control is always present for a runnable command", () => {
    render(<CommandFormBuilder command={NO_OPTIONS_COMMAND} onClose={vi.fn()} />);
    expect(screen.getByTestId("command-run-button")).toBeInTheDocument();
  });

  it("Run actually launches the command via createJob, shows status, and does NOT close the modal", async () => {
    const createJobSpy = vi
      .spyOn(api, "createJob")
      .mockResolvedValueOnce(commandJobRecord("mock-job-1"));
    const onClose = vi.fn();

    render(<CommandFormBuilder command={NO_OPTIONS_COMMAND} onClose={onClose} />);
    fireEvent.click(screen.getByTestId("command-run-button"));

    await waitFor(() =>
      expect(createJobSpy).toHaveBeenCalledWith(
        "command",
        expect.objectContaining({
          command: NO_OPTIONS_COMMAND.name,
          subcommand: null,
          args: [],
          confirm: true,
        })
      )
    );
    expect(await screen.findByTestId("command-run-status")).toBeInTheDocument();
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    // The whole point of this fix: launching a run must NOT close the modal
    // out from under the operator before they can see the job status/log.
    expect(onClose).not.toHaveBeenCalled();
  });

  it("a selected subcommand is sent separately, not folded into args", async () => {
    const user = userEvent.setup();
    const createJobSpy = vi
      .spyOn(api, "createJob")
      .mockResolvedValueOnce(commandJobRecord("mock-job-2"));

    render(<CommandFormBuilder command={PARENT_WITH_SUBCOMMANDS} onClose={vi.fn()} />);
    await user.selectOptions(screen.getByLabelText("Select Subcommand"), "pin");
    await user.type(screen.getByPlaceholderText("Enter value..."), "v2");
    fireEvent.click(screen.getByTestId("command-run-button"));

    await waitFor(() =>
      expect(createJobSpy).toHaveBeenCalledWith(
        "command",
        expect.objectContaining({
          command: "prompt_registry",
          subcommand: "pin",
          args: ["--version", "v2"],
          confirm: true,
        })
      )
    );
  });

  it("a high-stakes command opens a confirm dialog nested inside the form builder, and confirming still calls createJob", async () => {
    const user = userEvent.setup();
    const createJobSpy = vi
      .spyOn(api, "createJob")
      .mockResolvedValueOnce(commandJobRecord("mock-job-3"));

    render(<CommandFormBuilder command={KILL_SWITCH_COMMAND} onClose={vi.fn()} />);
    await user.click(screen.getByRole("switch", { name: "--activate" }));
    fireEvent.click(screen.getByTestId("command-run-button"));

    const dialog = await screen.findByTestId("command-confirm");
    expect(createJobSpy).not.toHaveBeenCalled();
    expect(within(dialog).getByText(/kill switch/i)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByTestId("command-confirm-yes"));
    await waitFor(() =>
      expect(createJobSpy).toHaveBeenCalledWith(
        "command",
        expect.objectContaining({
          command: "execution.kill_switch",
          args: ["--activate"],
          confirm: true,
        })
      )
    );
  });

  describe("plural --strategies multi-select (scripts/refresh_validations.py)", () => {
    it("defaults to every strategy in the supplied registry pre-selected", () => {
      render(
        <CommandFormBuilder
          command={REFRESH_VALIDATIONS_COMMAND}
          onClose={vi.fn()}
          strategyRegistry={FAKE_STRATEGY_REGISTRY}
        />
      );

      const composed = screen.getByTestId("command-composed");
      for (const name of FAKE_STRATEGY_REGISTRY) {
        expect(composed.textContent).toContain(name);
      }
      expect(screen.getByText(`${FAKE_STRATEGY_REGISTRY.length} of ${FAKE_STRATEGY_REGISTRY.length} selected`)).toBeInTheDocument();
    });

    it("Clear then re-toggling one name back on leaves only that name in the compiled command", async () => {
      const user = userEvent.setup();
      render(
        <CommandFormBuilder
          command={REFRESH_VALIDATIONS_COMMAND}
          onClose={vi.fn()}
          strategyRegistry={FAKE_STRATEGY_REGISTRY}
        />
      );

      await user.click(screen.getByRole("button", { name: "Clear" }));
      expect(screen.getByText(`0 of ${FAKE_STRATEGY_REGISTRY.length} selected`)).toBeInTheDocument();

      await user.click(screen.getByRole("switch", { name: FAKE_STRATEGY_REGISTRY[1] }));

      const composed = screen.getByTestId("command-composed");
      expect(composed.textContent).toContain(FAKE_STRATEGY_REGISTRY[1]);
      for (const name of FAKE_STRATEGY_REGISTRY) {
        if (name !== FAKE_STRATEGY_REGISTRY[1]) {
          expect(composed.textContent).not.toContain(name);
        }
      }
    });

    it("Select All after Clear restores the full comma-joined list", async () => {
      const user = userEvent.setup();
      render(
        <CommandFormBuilder
          command={REFRESH_VALIDATIONS_COMMAND}
          onClose={vi.fn()}
          strategyRegistry={FAKE_STRATEGY_REGISTRY}
        />
      );

      await user.click(screen.getByRole("button", { name: "Clear" }));
      await user.click(screen.getByRole("button", { name: "Select All" }));

      const composed = screen.getByTestId("command-composed");
      for (const name of FAKE_STRATEGY_REGISTRY) {
        expect(composed.textContent).toContain(name);
      }
      expect(screen.getByText(`${FAKE_STRATEGY_REGISTRY.length} of ${FAKE_STRATEGY_REGISTRY.length} selected`)).toBeInTheDocument();
    });

    it("falls back to REGISTERED_STRATEGIES when no strategyRegistry prop is passed", () => {
      render(<CommandFormBuilder command={REFRESH_VALIDATIONS_COMMAND} onClose={vi.fn()} />);
      expect(screen.getByText(`${REGISTERED_STRATEGIES.length} of ${REGISTERED_STRATEGIES.length} selected`)).toBeInTheDocument();
    });

    it("does NOT affect the singular --strategy select on validation.harness -- still starts empty with no strategyRegistry prop", () => {
      render(<CommandFormBuilder command={HARNESS_COMMAND} onClose={vi.fn()} />);
      const select = screen.getByDisplayValue("-- Select --strategy --");
      expect(select.tagName).toBe("SELECT");
      for (const strategy of REGISTERED_STRATEGIES) {
        expect(within(select).getByRole("option", { name: strategy })).toBeInTheDocument();
      }
    });
  });
});
