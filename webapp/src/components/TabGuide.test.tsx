/**
 * TabGuide.test.tsx — the "How this works" education panel: expanded on first
 * visit, collapsed (but reopenable) on later visits, with inline glossary
 * definitions, and inert for an unknown tab.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { TabGuide } from "./TabGuide";
import { resetHelpSeen } from "../help/helpState";

beforeEach(() => {
  resetHelpSeen();
});

describe("TabGuide", () => {
  it("is expanded on first visit and shows the description", () => {
    render(<TabGuide tabKey="calibration" />);
    expect(screen.getByRole("button", { name: /how this works/i })).toHaveAttribute(
      "aria-expanded",
      "true"
    );
    expect(screen.getByText(/honesty surface/i)).toBeInTheDocument();
  });

  it("starts collapsed on a later visit (seen persisted)", () => {
    const first = render(<TabGuide tabKey="calibration" />); // marks seen
    first.unmount();

    render(<TabGuide tabKey="calibration" />);
    expect(screen.getByRole("button", { name: /how this works/i })).toHaveAttribute(
      "aria-expanded",
      "false"
    );
    expect(screen.queryByText(/honesty surface/i)).not.toBeInTheDocument();
  });

  it("reopens when the header toggle is clicked", async () => {
    const user = userEvent.setup();
    const first = render(<TabGuide tabKey="calibration" />);
    first.unmount();

    render(<TabGuide tabKey="calibration" />);
    await user.click(screen.getByRole("button", { name: /how this works/i }));
    expect(screen.getByText(/honesty surface/i)).toBeInTheDocument();
  });

  it("expands a key-concept term to reveal its definition", async () => {
    const user = userEvent.setup();
    render(<TabGuide tabKey="calibration" />);

    expect(screen.queryByTestId("tab-guide-def")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "conviction" }));
    const def = screen.getByTestId("tab-guide-def");
    expect(def).toHaveTextContent(/how confident the system is/i);
  });

  it("renders nothing for an unknown tab key", () => {
    const { container } = render(<TabGuide tabKey="does-not-exist" />);
    expect(container).toBeEmptyDOMElement();
  });
});
