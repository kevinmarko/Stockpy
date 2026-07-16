/**
 * Toggle.test.tsx — this is the app's only on/off ACTION control, so its
 * a11y contract and double-fire guard matter: role="switch"/aria-checked,
 * Space + Enter activation, disabled no-op, and pending state must both
 * mark aria-busy AND refuse a second click while a mutation is in flight.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Toggle } from "./Toggle";

describe("Toggle", () => {
  it("renders as role=switch with aria-checked reflecting state", () => {
    const { rerender } = render(
      <Toggle checked={false} onChange={vi.fn()} label="Signal generation" />
    );
    expect(screen.getByRole("switch", { name: "Signal generation" })).toHaveAttribute(
      "aria-checked",
      "false"
    );

    rerender(<Toggle checked={true} onChange={vi.fn()} label="Signal generation" />);
    expect(screen.getByRole("switch", { name: "Signal generation" })).toHaveAttribute(
      "aria-checked",
      "true"
    );
  });

  it("click calls onChange with the flipped value", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} label="Auto-poll" />);

    await user.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("Space and Enter both activate the switch (native button semantics)", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} label="Auto-poll" />);

    const el = screen.getByRole("switch");
    el.focus();
    await user.keyboard(" ");
    expect(onChange).toHaveBeenCalledWith(true);

    onChange.mockClear();
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("disabled: click is a no-op", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} label="Auto-poll" disabled />);

    const el = screen.getByRole("switch");
    expect(el).toBeDisabled();
    await user.click(el);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("pending: sets aria-busy and refuses a click (double-fire guard)", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} label="Auto-poll" pending />);

    const el = screen.getByRole("switch");
    expect(el).toHaveAttribute("aria-busy", "true");
    // pending sets `disabled` too (belt-and-suspenders with the CSS
    // pointer-events guard), so RTL's click on a disabled button is a no-op.
    expect(el).toBeDisabled();
    await user.click(el);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("the visible label text carries the state, never color alone", () => {
    render(<Toggle checked label="Signal generation: Running" onChange={vi.fn()} />);
    expect(screen.getByText("Signal generation: Running")).toBeInTheDocument();
  });
});
