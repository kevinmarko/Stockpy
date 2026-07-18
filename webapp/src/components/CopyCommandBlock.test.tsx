/**
 * CopyCommandBlock.test.tsx — the shared "monospace command + Copy/Copied
 * button" pattern (lifted out of Commands.tsx). Covers the clipboard write,
 * the label flip, the disabled/empty-command no-op, and the resetKey-driven
 * "Copied" clearing (Commands.tsx relies on this to clear the indicator on
 * every keystroke, not just when the composed command text itself changes).
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CopyCommandBlock } from "./CopyCommandBlock";

const originalClipboard = navigator.clipboard;

function mockClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    writable: true,
    configurable: true,
  });
  return writeText;
}

describe("CopyCommandBlock", () => {
  afterEach(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: originalClipboard,
      writable: true,
      configurable: true,
    });
  });

  it("renders the command text", () => {
    render(<CopyCommandBlock command="python -m validation.harness --strategy momentum" />);
    expect(screen.getByTestId("command-composed")).toHaveTextContent(
      "python -m validation.harness --strategy momentum"
    );
  });

  it("clicking Copy calls navigator.clipboard.writeText with the exact command", () => {
    const writeText = mockClipboard();
    render(<CopyCommandBlock command="python -m main.py --interval 60" />);

    fireEvent.click(screen.getByTestId("command-copy"));
    expect(writeText).toHaveBeenCalledWith("python -m main.py --interval 60");
  });

  it("the button label flips from Copy to Copied after a click", () => {
    mockClipboard();
    render(<CopyCommandBlock command="python -m main.py" />);

    const btn = screen.getByTestId("command-copy");
    expect(btn).toHaveTextContent("Copy");
    fireEvent.click(btn);
    expect(btn).toHaveTextContent("Copied");
  });

  it("disabled: the Copy button is disabled and clicking it never calls the clipboard", () => {
    const writeText = mockClipboard();
    render(<CopyCommandBlock command="python -m main.py" disabled />);

    const btn = screen.getByTestId("command-copy");
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(writeText).not.toHaveBeenCalled();
    expect(btn).toHaveTextContent("Copy");
  });

  it("empty command: clicking Copy is a no-op even without an explicit disabled prop", () => {
    const writeText = mockClipboard();
    render(<CopyCommandBlock command="" />);

    fireEvent.click(screen.getByTestId("command-copy"));
    expect(writeText).not.toHaveBeenCalled();
    expect(screen.getByTestId("command-copy")).toHaveTextContent("Copy");
  });

  it("renders the optional label when provided, and omits it when not", () => {
    const { rerender } = render(
      <CopyCommandBlock command="python -m main.py" label="Command to run" />
    );
    expect(screen.getByText("Command to run")).toBeInTheDocument();

    rerender(<CopyCommandBlock command="python -m main.py" />);
    expect(screen.queryByText("Command to run")).not.toBeInTheDocument();
  });

  it("uses testIdPrefix to namespace testids for a second instance on the same screen", () => {
    render(<CopyCommandBlock command="/agentic-discovery run" testIdPrefix="agentic-prompt" />);
    expect(screen.getByTestId("agentic-prompt-composed")).toBeInTheDocument();
    expect(screen.getByTestId("agentic-prompt-copy")).toBeInTheDocument();
    expect(screen.queryByTestId("command-composed")).not.toBeInTheDocument();
  });

  it("Copied clears when resetKey changes, even if the command text itself is unchanged", () => {
    mockClipboard();
    const { rerender } = render(
      <CopyCommandBlock command="python -m main.py" resetKey="v1" />
    );
    const btn = screen.getByTestId("command-copy");
    fireEvent.click(btn);
    expect(btn).toHaveTextContent("Copied");

    // Same command text, but resetKey changed (mirrors Commands.tsx passing
    // the raw input string, which changes on every keystroke even when the
    // parsed/composed command doesn't).
    rerender(<CopyCommandBlock command="python -m main.py" resetKey="v2" />);
    expect(btn).toHaveTextContent("Copy");
  });

  it("without an explicit resetKey, Copied clears once the command itself changes", () => {
    mockClipboard();
    const { rerender } = render(<CopyCommandBlock command="python -m main.py" />);
    const btn = screen.getByTestId("command-copy");
    fireEvent.click(btn);
    expect(btn).toHaveTextContent("Copied");

    rerender(<CopyCommandBlock command="python -m main.py --interval 60" />);
    expect(btn).toHaveTextContent("Copy");
  });
});
