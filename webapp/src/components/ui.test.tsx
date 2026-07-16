/**
 * ui.test.tsx — the design-system primitives added for the Settings screen:
 * `Input` (label/hint/invalid wiring) and `Button` (variant/block/pending).
 * The existing exports in ui.tsx (CategoryChip, DeployableBadge, etc.) are
 * already exercised indirectly by the screens that use them; these two are
 * new leaf components with no screen consumer yet, so they need direct tests.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button, Input } from "./ui";

describe("Input", () => {
  it("wires the label to the input via htmlFor/id", () => {
    render(<Input label="Interval (seconds)" value="300" onChange={vi.fn()} />);
    expect(screen.getByLabelText("Interval (seconds)")).toHaveValue("300");
  });

  it("sets aria-invalid and renders the hint text describing the field", () => {
    render(
      <Input
        label="Interval (seconds)"
        value="10"
        onChange={vi.fn()}
        invalid
        hint="Must be 0 or between 60 and 86400."
      />
    );
    const input = screen.getByLabelText("Interval (seconds)");
    expect(input).toHaveAttribute("aria-invalid", "true");
    const hint = screen.getByText("Must be 0 or between 60 and 86400.");
    expect(input).toHaveAttribute("aria-describedby", hint.id);
  });

  it("omits aria-invalid when valid", () => {
    render(<Input label="Reason" value="" onChange={vi.fn()} />);
    expect(screen.getByLabelText("Reason")).not.toHaveAttribute("aria-invalid");
  });

  it("calls onChange with the new value", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Input label="Reason" value="" onChange={onChange} />);
    await user.type(screen.getByLabelText("Reason"), "x");
    expect(onChange).toHaveBeenCalled();
  });
});

describe("Button", () => {
  it("renders children and applies the primary/neutral variant class", () => {
    const { rerender } = render(<Button>Run now</Button>);
    expect(screen.getByRole("button", { name: "Run now" })).toHaveClass("btn-neutral");

    rerender(<Button variant="primary">Run now</Button>);
    expect(screen.getByRole("button", { name: "Run now" })).toHaveClass("btn-primary");
  });

  it("block adds btn-block", () => {
    render(<Button block>Run now</Button>);
    expect(screen.getByRole("button")).toHaveClass("btn-block");
  });

  it("pending: disables the button, sets aria-busy, and swaps the label for a spinner", () => {
    render(<Button pending>Run now</Button>);
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByText("Run now")).not.toBeInTheDocument();
    expect(btn.querySelector(".spinner")).toBeInTheDocument();
  });

  it("disabled prevents onClick", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        Run now
      </Button>
    );
    await user.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("click fires onClick when enabled", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Run now</Button>);
    await user.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
