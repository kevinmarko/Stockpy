/**
 * CommandPaletteModal.test.tsx — the global Cmd+K palette: fuzzy suggestion
 * list, ghost text, category browse when empty, and the Configure/Run action
 * bar once a command resolves.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CommandPaletteModal } from "./CommandPaletteModal";
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
  ],
};

const PREFLIGHT_COMMAND: CommandSpec = {
  name: "scripts/preflight_check.py",
  invocation: "python3 scripts/preflight_check.py",
  aliases: [],
  description: "Pre-live readiness gate.",
  positionals: [],
  subcommands: [],
  options: [],
};

const COMMANDS = [HARNESS_COMMAND, PREFLIGHT_COMMAND];

describe("CommandPaletteModal", () => {
  it("renders nothing when isOpen is false", () => {
    const { container } = render(
      <CommandPaletteModal isOpen={false} onClose={vi.fn()} commands={COMMANDS} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the palette and focuses the input when isOpen is true", async () => {
    render(<CommandPaletteModal isOpen onClose={vi.fn()} commands={COMMANDS} />);
    expect(screen.getByTestId("command-palette-modal")).toBeInTheDocument();
    await vi.waitFor(() =>
      expect(screen.getByTestId("command-palette-input")).toHaveFocus()
    );
  });

  it("shows the category browse list when the input is empty", () => {
    render(<CommandPaletteModal isOpen onClose={vi.fn()} commands={COMMANDS} />);
    expect(screen.getByText("Browse by Category")).toBeInTheDocument();
    expect(screen.getByText("Pipeline & Core")).toBeInTheDocument();
    expect(screen.getByText("Testing & Validation")).toBeInTheDocument();
  });

  it("typing a partial command shows matching suggestions and hides the category browse", async () => {
    const user = userEvent.setup();
    render(<CommandPaletteModal isOpen onClose={vi.fn()} commands={COMMANDS} />);

    await user.type(screen.getByTestId("command-palette-input"), "harness");
    expect(screen.getByText(/Suggestions/)).toBeInTheDocument();
    expect(screen.getByText("validation.harness")).toBeInTheDocument();
    expect(screen.queryByText("Browse by Category")).not.toBeInTheDocument();
  });

  it("accepting a suggestion via click fills the input with that command", async () => {
    const user = userEvent.setup();
    render(<CommandPaletteModal isOpen onClose={vi.fn()} commands={COMMANDS} />);

    await user.type(screen.getByTestId("command-palette-input"), "preflight");
    await user.click(screen.getByText("scripts/preflight_check.py"));

    expect(screen.getByTestId("command-palette-input")).toHaveValue(
      "scripts/preflight_check.py "
    );
  });

  it("clicking a category populates the input with its first command in that category", async () => {
    const user = userEvent.setup();
    render(<CommandPaletteModal isOpen onClose={vi.fn()} commands={COMMANDS} />);

    // Both fixture commands categorize as "testing" (getCommandCategory
    // matches "validation" and "preflight" alike) -- the first command in
    // array order, HARNESS_COMMAND, is what should be filled in.
    await user.click(screen.getByText("Testing & Validation"));
    expect(screen.getByTestId("command-palette-input")).toHaveValue(
      "validation.harness "
    );
  });

  it("resolving a command with a required option missing shows a validation hint and disables Run", async () => {
    const user = userEvent.setup();
    const onRunCommand = vi.fn();
    render(
      <CommandPaletteModal
        isOpen
        onClose={vi.fn()}
        commands={COMMANDS}
        onRunCommand={onRunCommand}
      />
    );

    await user.type(screen.getByTestId("command-palette-input"), "validation.harness ");
    expect(screen.getByText(/missing required option/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Run Command/ })).toBeDisabled();
  });

  it("Configure in Form Builder calls onSelectCommandForBuilder with the resolved command, then closes", async () => {
    const user = userEvent.setup();
    const onSelectCommandForBuilder = vi.fn();
    const onClose = vi.fn();
    render(
      <CommandPaletteModal
        isOpen
        onClose={onClose}
        commands={COMMANDS}
        onSelectCommandForBuilder={onSelectCommandForBuilder}
      />
    );

    await user.type(screen.getByTestId("command-palette-input"), "scripts/preflight_check.py ");
    await user.click(screen.getByRole("button", { name: /Configure in Form Builder/ }));

    expect(onSelectCommandForBuilder).toHaveBeenCalledWith(PREFLIGHT_COMMAND);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Run Command calls onRunCommand with the composed string once every required option is present, then closes", async () => {
    const user = userEvent.setup();
    const onRunCommand = vi.fn();
    const onClose = vi.fn();
    render(
      <CommandPaletteModal
        isOpen
        onClose={onClose}
        commands={COMMANDS}
        onRunCommand={onRunCommand}
      />
    );

    await user.type(
      screen.getByTestId("command-palette-input"),
      "validation.harness --strategy garch_vol_target"
    );
    await user.click(screen.getByRole("button", { name: /Run Command/ }));

    expect(onRunCommand).toHaveBeenCalledWith(
      "python3 -m validation.harness --strategy garch_vol_target",
      HARNESS_COMMAND,
      ["--strategy", "garch_vol_target"]
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Escape calls onClose", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<CommandPaletteModal isOpen onClose={onClose} commands={COMMANDS} />);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("reopening resets the input to empty", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <CommandPaletteModal isOpen onClose={vi.fn()} commands={COMMANDS} />
    );
    await user.type(screen.getByTestId("command-palette-input"), "harness");
    expect(screen.getByTestId("command-palette-input")).toHaveValue("harness");

    rerender(<CommandPaletteModal isOpen={false} onClose={vi.fn()} commands={COMMANDS} />);
    rerender(<CommandPaletteModal isOpen onClose={vi.fn()} commands={COMMANDS} />);
    expect(screen.getByTestId("command-palette-input")).toHaveValue("");
  });
});
