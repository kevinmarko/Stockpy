/**
 * ui.test.tsx — the design-system primitives added for the Settings screen:
 * `Input` (label/hint/invalid wiring) and `Button` (variant/block/pending).
 * The existing exports in ui.tsx (CategoryChip, DeployableBadge, etc.) are
 * already exercised indirectly by the screens that use them; these two are
 * new leaf components with no screen consumer yet, so they need direct tests.
 *
 * `InfoTip` also gets direct coverage here (in addition to being exercised
 * indirectly via Portfolio.test.tsx/StrategyHealth.test.tsx) since it
 * replaces every native `title=` attribute in the app and its open/close
 * interaction logic -- tap trigger again, tap outside, Escape -- is exactly
 * the behavior a native title= tooltip never gave touch users.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button, DeployableBadge, Input, InfoTip } from "./ui";

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

/**
 * InfoTip replaces every native `title="..."` in the app -- a native title
 * never fires on tap, only mouse hover, so on this touch-first PWA it was
 * silently invisible to nearly every user. These tests exercise the exact
 * interaction a tap-only device gets: closed by default, opened by a tap
 * (== click, identical for mouse and touch), and dismissed by tapping the
 * trigger again, tapping elsewhere, or Escape.
 */
describe("InfoTip", () => {
  it("is closed by default and opens on click, exposing the content via role=tooltip", async () => {
    const user = userEvent.setup();
    render(
      <InfoTip triggerClassName="badge badge-good" content="Passes every gate">
        ● Deployable
      </InfoTip>
    );
    const trigger = screen.getByRole("button", { name: "● Deployable" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    await user.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("Passes every gate");
    expect(trigger).toHaveAttribute("aria-describedby", tip.id);
  });

  it("clicking the trigger again closes it", async () => {
    const user = userEvent.setup();
    render(<InfoTip content="Explanation">Trigger</InfoTip>);
    const trigger = screen.getByRole("button", { name: "Trigger" });

    await user.click(trigger);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    await user.click(trigger);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("Escape closes it", async () => {
    const user = userEvent.setup();
    render(<InfoTip content="Explanation">Trigger</InfoTip>);
    await user.click(screen.getByRole("button", { name: "Trigger" }));
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("clicking outside the trigger and bubble closes it", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <InfoTip content="Explanation">Trigger</InfoTip>
        <button type="button">Elsewhere</button>
      </div>
    );
    await user.click(screen.getByRole("button", { name: "Trigger" }));
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Elsewhere" }));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("clicking inside the open bubble does not close it", async () => {
    const user = userEvent.setup();
    render(<InfoTip content="Explanation text">Trigger</InfoTip>);
    await user.click(screen.getByRole("button", { name: "Trigger" }));
    const tip = screen.getByRole("tooltip");

    await user.click(tip);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
  });

  it("a trigger with no visible children still has an accessible name via ariaLabel", () => {
    render(<InfoTip content="2024-01-05: alpha 0.482" ariaLabel="2024-01-05 attention weight" />);
    expect(screen.getByRole("button", { name: "2024-01-05 attention weight" })).toBeInTheDocument();
  });
});

describe("DeployableBadge", () => {
  it("interactive (default): tapping the badge reveals the honesty explanation", async () => {
    const user = userEvent.setup();
    render(<DeployableBadge deployable={true} />);
    const badge = screen.getByRole("button", { name: "● Deployable" });
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    await user.click(badge);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Passes PBO/DSR/Sharpe/MaxDD gates");
  });

  it("not deployable renders the honest failure badge and explanation", async () => {
    const user = userEvent.setup();
    render(<DeployableBadge deployable={false} />);
    await user.click(screen.getByRole("button", { name: "▲ Not deployable" }));
    expect(screen.getByRole("tooltip")).toHaveTextContent(
      "Fails a validation gate — not deployable"
    );
  });

  it("null (no backtest yet) renders the same honest not-deployable treatment as false", () => {
    render(<DeployableBadge deployable={null} />);
    expect(screen.getByRole("button", { name: "▲ Not deployable" })).toBeInTheDocument();
  });

  it("interactive=false renders a plain, non-focusable badge -- for call sites that nest it inside a real <button>/<a>, where a second focusable trigger would be invalid HTML", () => {
    render(
      <a href="/pilots/x">
        <DeployableBadge deployable={true} interactive={false} />
      </a>
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    const badge = within(screen.getByRole("link")).getByText("● Deployable");
    expect(badge.tagName).toBe("SPAN");
  });
});
