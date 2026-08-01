import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CollapsiblePanel } from "./CollapsiblePanel";

describe("CollapsiblePanel", () => {
  it("is a real, keyboard-focusable button with aria-expanded reflecting state", () => {
    render(
      <CollapsiblePanel title="Section" badge={3}>
        <p>Body content</p>
      </CollapsiblePanel>
    );
    const toggle = screen.getByRole("button", { name: /Section/ });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Body content")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Body content")).not.toBeInTheDocument();
  });

  it("respects defaultOpen=false", () => {
    render(
      <CollapsiblePanel title="Collapsed" defaultOpen={false}>
        <p>Hidden</p>
      </CollapsiblePanel>
    );
    expect(screen.getByRole("button", { name: /Collapsed/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Hidden")).not.toBeInTheDocument();
  });
});
