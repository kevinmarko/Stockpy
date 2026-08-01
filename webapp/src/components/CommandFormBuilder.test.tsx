/**
 * CommandFormBuilder.test.tsx — the "Form Mode" drawer that maps a
 * CommandSpec's options onto visual controls (toggle/select/date/text) and
 * compiles them into a runnable CLI string. Covers each control kind, the
 * subcommand switch, Reset, and the Close/Execute action bar.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CommandFormBuilder } from "./CommandFormBuilder";
import { REGISTERED_STRATEGIES } from "../commandParse";
import type { CommandSpec } from "../api/types";

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

describe("CommandFormBuilder", () => {
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

  it("Execute Command is absent when onRunCommand is not provided", () => {
    render(<CommandFormBuilder command={NO_OPTIONS_COMMAND} onClose={vi.fn()} />);
    expect(screen.queryByTestId("form-builder-run-button")).not.toBeInTheDocument();
  });

  it("Execute Command calls onRunCommand with the composed string, then onClose", async () => {
    const user = userEvent.setup();
    const onRunCommand = vi.fn();
    const onClose = vi.fn();
    render(
      <CommandFormBuilder
        command={NO_OPTIONS_COMMAND}
        onClose={onClose}
        onRunCommand={onRunCommand}
      />
    );
    await user.click(screen.getByTestId("form-builder-run-button"));
    expect(onRunCommand).toHaveBeenCalledWith(
      NO_OPTIONS_COMMAND.invocation,
      NO_OPTIONS_COMMAND,
      []
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
