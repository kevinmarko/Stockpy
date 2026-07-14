/**
 * PilotDetail.test.tsx — renders against the real mock API. Focuses on the
 * honesty invariants (CONSTRAINT #4): a Pilot with no persisted backtest curve
 * must render "No backtest series yet" with its `reason`, never a fabricated
 * line; a non-deployable Pilot's badge must render plainly. Also covers the
 * Follow CTA opening the modal and an unknown pilot id's 404 state.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { PilotDetail } from "./PilotDetail";

function renderDetail(id: string) {
  return render(
    <MemoryRouter initialEntries={[`/pilots/${id}`]}>
      <Routes>
        <Route path="/pilots/:id" element={<PilotDetail />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("PilotDetail screen (real mock API)", () => {
  it("renders holdings, sector allocation, and the Follow CTA for a deployable pilot", async () => {
    renderDetail("trend-following");

    expect(await screen.findByRole("heading", { name: "Trend Follower" })).toBeInTheDocument();
    expect(screen.getByText(/Holdings/)).toBeInTheDocument();
    expect(screen.getByText(/Sector allocation/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Follow/ })).toBeInTheDocument();
  });

  it("value-quality (curve:null) renders the honest 'no backtest series yet' panel with its reason, never a fabricated chart", async () => {
    renderDetail("value-quality");

    await screen.findByRole("heading", { name: /value/i });
    expect(screen.getByText("No backtest series yet")).toBeInTheDocument();
    // The mock's honest `reason` string is what's actually surfaced (not a
    // silently-different generic message the component invented itself).
    expect(
      screen.getByText(/this pilot's validation report has no persisted return curve/i)
    ).toBeInTheDocument();
    // No SVG series was rendered — the chart section stayed empty.
    expect(document.querySelector(".recharts-area")).not.toBeInTheDocument();
  });

  it("momentum-burst renders its 'Not deployable' badge plainly, never hidden or softened", async () => {
    renderDetail("momentum-burst");

    await screen.findByRole("heading", { name: /momentum/i });
    // Both the honesty badge and the description call out the failed gate —
    // it must never be hidden or softened into a passing-looking state.
    expect(screen.getAllByText(/not deployable/i).length).toBeGreaterThan(0);
  });

  it("clicking Follow opens the FollowModal with the unmissable gated-queue notice", async () => {
    const user = userEvent.setup();
    renderDetail("trend-following");

    const followBtn = await screen.findByRole("button", { name: /Follow/ });
    await user.click(followBtn);

    expect(
      screen.getByText(/this creates a/i, { exact: false })
    ).toBeInTheDocument();
    expect(screen.getByText(/No order is placed automatically/i)).toBeInTheDocument();
  });

  it("an unknown pilot id renders the honest 404 'Nothing here yet' state", async () => {
    renderDetail("does-not-exist");
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
  });
});
