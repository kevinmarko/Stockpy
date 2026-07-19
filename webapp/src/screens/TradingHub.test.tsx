/**
 * TradingHub.test.tsx — the "Trading Tools" nav-section hub renders one card
 * per screen (icon, label, description) and each card navigates to its
 * route. Descriptions are asserted against the LIVE TAB_HELP text (not a
 * hard-coded duplicate) so the test catches drift.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { TradingHub } from "./TradingHub";
import { TAB_HELP } from "../help/helpContent";

function renderHub(initialPath = "/trading") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/trading" element={<TradingHub />} />
        <Route path="/attribution" element={<div>Attribution landing</div>} />
        <Route path="/calibration" element={<div>Calibration landing</div>} />
        <Route path="/commands" element={<div>Commands landing</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe("TradingHub screen", () => {
  it("renders the header", () => {
    renderHub();
    expect(screen.getByRole("heading", { name: "Trading Tools" })).toBeInTheDocument();
    expect(
      screen.getByText("Grading and acting on your own portfolio.")
    ).toBeInTheDocument();
  });

  it("renders all 3 card labels (Agent moved to the primary mobile tab bar, not listed here)", () => {
    renderHub();
    for (const label of ["Attribution", "Calibration", "Commands"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.queryByText("Agent")).not.toBeInTheDocument();
  });

  it("renders the live TAB_HELP descriptions for all 3 cards, never a hard-coded duplicate", () => {
    renderHub();
    expect(screen.getByText(TAB_HELP.attribution.description)).toBeInTheDocument();
    expect(screen.getByText(TAB_HELP.calibration.description)).toBeInTheDocument();
    expect(screen.getByText(TAB_HELP.commands.description)).toBeInTheDocument();
  });

  it("clicking the Attribution card navigates to /attribution", async () => {
    const user = userEvent.setup();
    renderHub();
    await user.click(screen.getByText("Attribution"));
    expect(await screen.findByText("Attribution landing")).toBeInTheDocument();
  });

  it("clicking the Calibration card navigates to /calibration", async () => {
    const user = userEvent.setup();
    renderHub();
    await user.click(screen.getByText("Calibration"));
    expect(await screen.findByText("Calibration landing")).toBeInTheDocument();
  });

  it("clicking the Commands card navigates to /commands", async () => {
    const user = userEvent.setup();
    renderHub();
    await user.click(screen.getByText("Commands"));
    expect(await screen.findByText("Commands landing")).toBeInTheDocument();
  });
});
