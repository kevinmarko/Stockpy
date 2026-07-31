/**
 * OperationsHub.test.tsx — the "Operations" nav-section hub renders one card
 * per screen (icon, label, description) and each card navigates to its
 * route. TAB_HELP-sourced descriptions are asserted against the LIVE text
 * (not a hard-coded duplicate) so the test catches drift; Console and Help &
 * Glossary were added to close parity gaps G1/G10 (previously unreachable /
 * nonexistent) -- see OperationsHub.tsx's own docstring.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { OperationsHub } from "./OperationsHub";
import { TAB_HELP } from "../help/helpContent";

function renderHub(initialPath = "/operations") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/operations" element={<OperationsHub />} />
        <Route path="/observability" element={<div>Mission Control landing</div>} />
        <Route path="/pipeline" element={<div>Pipeline landing</div>} />
        <Route path="/console" element={<div>Console landing</div>} />
        <Route path="/help" element={<div>Help landing</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe("OperationsHub screen", () => {
  it("renders the header", () => {
    renderHub();
    expect(screen.getByRole("heading", { name: "Operations" })).toBeInTheDocument();
    expect(
      screen.getByText("The platform and pipeline itself, not a symbol or your money.")
    ).toBeInTheDocument();
  });

  it("renders all 4 card labels", () => {
    renderHub();
    for (const label of ["Mission Control", "Pipeline", "Console", "Help & Glossary"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("renders the TAB_HELP-sourced card descriptions, never a hard-coded duplicate", () => {
    renderHub();
    expect(screen.getByText(TAB_HELP.observability.description)).toBeInTheDocument();
    expect(screen.getByText(TAB_HELP.pipeline.description)).toBeInTheDocument();
    expect(screen.getByText(TAB_HELP.console.description)).toBeInTheDocument();
  });

  it("clicking the Mission Control card navigates to /observability", async () => {
    const user = userEvent.setup();
    renderHub();
    await user.click(screen.getByRole("button", { name: /Mission Control/ }));
    expect(await screen.findByText("Mission Control landing")).toBeInTheDocument();
  });

  it("clicking the Pipeline card navigates to /pipeline", async () => {
    const user = userEvent.setup();
    renderHub();
    await user.click(screen.getByRole("button", { name: /Pipeline/ }));
    expect(await screen.findByText("Pipeline landing")).toBeInTheDocument();
  });

  it("clicking the Console card navigates to /console", async () => {
    const user = userEvent.setup();
    renderHub();
    await user.click(screen.getByRole("button", { name: /Console/ }));
    expect(await screen.findByText("Console landing")).toBeInTheDocument();
  });

  it("clicking the Help & Glossary card navigates to /help", async () => {
    const user = userEvent.setup();
    renderHub();
    await user.click(screen.getByRole("button", { name: /Help & Glossary/ }));
    expect(await screen.findByText("Help landing")).toBeInTheDocument();
  });
});
