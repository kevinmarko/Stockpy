/**
 * Modal.test.tsx — pins the a11y contract, including a REGRESSION test for a
 * real bug found in both prior copy-pasted dialogs (FollowModal,
 * PwaStatusDrawer): `role="dialog"` was on the backdrop element instead of
 * the actual dialog (`.sheet`). Also covers what neither prior implementation
 * had: a focus trap, Escape-to-close, and focus restore on unmount.
 *
 * `useMediaQuery` is mocked module-wide (jsdom's default viewport doesn't
 * match `Modal`'s own `(max-width: 768px)` query, so without a mock every
 * test exercises the desktop branch regardless -- the mock lets the mobile
 * describe block below force the vaul branch explicitly). The top-level
 * `describe("Modal", ...)` block resets it to `false` in a `beforeEach` so
 * the 7 pre-existing tests keep exercising the desktop path unmodified.
 */
import { useState } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Modal, MOBILE_EXIT_ANIMATION_MS, useModalRequestClose } from "./Modal";
import { useMediaQuery } from "../hooks/useMediaQuery";

vi.mock("../hooks/useMediaQuery", () => ({
  useMediaQuery: vi.fn(),
}));

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
  beforeEach(() => {
    vi.mocked(useMediaQuery).mockReturnValue(false);
  });

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

/**
 * Mobile (vaul) branch — regression coverage for the exit-animation gap: a
 * hardcoded `open={true}` on `Drawer.Root` meant the parent's own
 * `{show && <Modal>}` re-render unmounted the whole subtree the instant
 * `onClose` fired, giving vaul's slide-down transition zero time to run for
 * any programmatic close (only a real drag-to-dismiss gesture animated,
 * since vaul completes that animation itself before ever calling
 * `onOpenChange`). The fix decouples "close requested" from "actually gone":
 * `visible` state now truly drives `Drawer.Root`'s `open`, and the real
 * `onClose` prop -- the one that causes the PARENT to unmount `Modal` -- is
 * deferred by `MOBILE_EXIT_ANIMATION_MS` (vaul's own real transition
 * duration, not a guessed number).
 *
 * These tests assert on `onClose`'s call TIMING, not on `Drawer.Content`'s
 * own DOM presence: jsdom doesn't run real CSS animations, so asserting
 * "the sheet node is still mounted" wouldn't reliably distinguish a working
 * fix from a broken one here. The `onClose` prop is the one thing entirely
 * inside this component's own control -- and it's also the literal cause of
 * the real bug (the PARENT unmounting `Modal` prematurely), so proving it no
 * longer fires synchronously IS the direct, load-bearing proof of the fix.
 */
describe("Modal — mobile (vaul) branch: deferred exit", () => {
  beforeEach(() => {
    vi.mocked(useMediaQuery).mockReturnValue(true);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** A Cancel button wired the way this fix intends real consumers to wire
   *  theirs: through `useModalRequestClose()`, not by calling a captured
   *  `onClose` closure directly (which Modal has no way to intercept -- see
   *  the `useModalRequestClose` doc comment in Modal.tsx). */
  function CancelButton() {
    const requestClose = useModalRequestClose();
    return <button onClick={requestClose}>Cancel</button>;
  }

  it("does not call the real onClose synchronously when a Cancel-style close is requested", () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    render(
      <Modal ariaLabel="Mobile test dialog" onClose={onClose}>
        <CancelButton />
      </Modal>
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    // A deliberate gap: the real onClose (which would cause the parent to
    // unmount Modal) has NOT fired yet, immediately after the request.
    expect(onClose).not.toHaveBeenCalled();
  });

  it("calls the real onClose only after vaul's exit-animation duration elapses", async () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    render(
      <Modal ariaLabel="Mobile test dialog" onClose={onClose}>
        <CancelButton />
      </Modal>
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(MOBILE_EXIT_ANIMATION_MS);
    });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not double-invoke onClose when the close is requested more than once while closing", async () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    render(
      <Modal ariaLabel="Mobile test dialog" onClose={onClose}>
        <CancelButton />
      </Modal>
    );

    const cancel = screen.getByRole("button", { name: "Cancel" });
    fireEvent.click(cancel);
    fireEvent.click(cancel);
    fireEvent.click(cancel);

    await act(async () => {
      vi.advanceTimersByTime(MOBILE_EXIT_ANIMATION_MS);
    });

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
