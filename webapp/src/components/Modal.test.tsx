/**
 * Modal.test.tsx — pins the a11y contract, including a REGRESSION test for a
 * real bug found in both prior copy-pasted dialogs (FollowModal,
 * PwaStatusDrawer): `role="dialog"` was on the backdrop element instead of
 * the actual dialog (`.sheet`). Also covers what neither prior implementation
 * had: a focus trap, Escape-to-close, and focus restore on unmount.
 */
import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Modal } from "./Modal";

function TwoButtonModal({ onClose }: { onClose: () => void }) {
  return (
    <Modal ariaLabel="Test dialog" onClose={onClose}>
      <button>First</button>
      <button>Second</button>
    </Modal>
  );
}

/** Renders a trigger button that opens the modal, for focus-restore tests. */
function TriggerAndModal() {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button onClick={() => setOpen(true)}>Open dialog</button>
      {open && <TwoButtonModal onClose={() => setOpen(false)} />}
    </div>
  );
}

describe("Modal", () => {
  it("role=dialog and aria-modal are on .sheet, NOT the backdrop (regression pin)", () => {
    render(<TwoButtonModal onClose={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: "Test dialog" });
    expect(dialog).toHaveClass("sheet");
    expect(dialog).not.toHaveClass("sheet-backdrop");
    expect(dialog).toHaveAttribute("aria-modal", "true");

    // There must be exactly one dialog-role element -- the backdrop carries
    // no role at all (the prior bug put role="dialog" there too).
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("focus moves to the first focusable element on mount", async () => {
    render(<TwoButtonModal onClose={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "First" })).toHaveFocus()
    );
  });

  it("Tab cycles within the dialog (wraps from last back to first)", async () => {
    const user = userEvent.setup();
    render(<TwoButtonModal onClose={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "First" })).toHaveFocus()
    );

    await user.tab();
    expect(screen.getByRole("button", { name: "Second" })).toHaveFocus();

    // Tab again from the last element wraps back to the first.
    await user.tab();
    expect(screen.getByRole("button", { name: "First" })).toHaveFocus();

    // Shift+Tab from the first element wraps to the last.
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Second" })).toHaveFocus();
  });

  it("Escape calls onClose", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<TwoButtonModal onClose={onClose} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "First" })).toHaveFocus()
    );

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("backdrop click calls onClose", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<TwoButtonModal onClose={onClose} />);

    // The backdrop is the parent of the dialog; click it directly (not the
    // dialog itself, which stops propagation).
    await user.click(screen.getByRole("dialog").parentElement!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("clicking inside the sheet does NOT close the modal", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<TwoButtonModal onClose={onClose} />);

    await user.click(screen.getByRole("button", { name: "First" }));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("restores focus to the trigger element on unmount", async () => {
    const user = userEvent.setup();
    render(<TriggerAndModal />);

    const trigger = screen.getByRole("button", { name: "Open dialog" });
    trigger.focus();
    await user.click(trigger);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "First" })).toHaveFocus()
    );

    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });
});
