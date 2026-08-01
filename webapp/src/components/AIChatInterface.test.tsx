/**
 * AIChatInterface.test.tsx — the panel is only CSS-translated off-screen
 * when closed (isOpen=false), not unmounted (so the slide-in/out transition
 * keeps working). Covers the fix: it must not remain keyboard-focusable or
 * exposed to assistive tech while visually hidden.
 *
 * jsdom (vitest's DOM environment) does not implement the `inert` IDL
 * property reflection, so these assert on the raw `inert` HTML attribute
 * via getAttribute rather than the `.inert` DOM property.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AIChatInterface from "./AIChatInterface";

function getPanel(container: HTMLElement): HTMLDivElement {
  const panel = container.querySelector('[data-testid="ai-chat-panel"]');
  if (!panel) throw new Error("panel not found");
  return panel as HTMLDivElement;
}

describe("AIChatInterface closed-panel a11y", () => {
  it("marks the panel inert and aria-hidden when closed", () => {
    const { container } = render(<AIChatInterface isOpen={false} onClose={() => {}} />);
    const panel = getPanel(container);
    expect(panel.getAttribute("inert")).not.toBeNull();
    expect(panel).toHaveAttribute("aria-hidden", "true");
  });

  it("is neither inert nor aria-hidden=true when open", () => {
    const { container } = render(<AIChatInterface isOpen={true} onClose={() => {}} />);
    const panel = getPanel(container);
    expect(panel.getAttribute("inert")).toBeNull();
    expect(panel).toHaveAttribute("aria-hidden", "false");
  });

  it("textarea sits inside the inert subtree while the panel is closed", () => {
    render(<AIChatInterface isOpen={false} onClose={() => {}} />);
    const textarea = screen.getByPlaceholderText("Ask a question about your portfolio...");
    const panel = textarea.closest('[data-testid="ai-chat-panel"]') as HTMLDivElement;
    expect(panel.getAttribute("inert")).not.toBeNull();
  });
});
