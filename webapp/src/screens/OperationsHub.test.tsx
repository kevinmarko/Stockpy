/**
 * OperationsHub.test.tsx — the "Operations" nav-section hub renders one card
 * per screen (icon, label, description) and each card navigates to its
 * route. Neither screen has a TAB_HELP entry, so both descriptions are
 * asserted against the exact static prose the spec calls for.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { OperationsHub } from "./OperationsHub";

function renderHub(initialPath = "/operations") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/operations" element={<OperationsHub />} />
        <Route path="/observability" element={<div>Mission Control landing</div>} />
        <Route path="/pipeline" element={<div>Pipeline landing</div>} />
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

  it("renders both card labels", () => {
    renderHub();
    for (const label of ["Mission Control", "Pipeline"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("renders both card descriptions", () => {
    renderHub();
    expect(
      screen.getByText(
        "Recession telemetry and risk-gate status — Sahm Rule, HY OAS, yield curve, and forecast horizons."
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "The orchestrator daemon's live status and manual pipeline run triggers."
      )
    ).toBeInTheDocument();
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
});
