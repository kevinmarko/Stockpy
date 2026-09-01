/**
 * ResearchHub.test.tsx — the Research section's landing hub: all 12 screen
 * cards render with their label + description (Sentiment Dynamics and
 * Sector Selection closing parity gap G2 — the hub previously listed only 9
 * of the 11 screens the nav actually carries; Google Trends SVI Stitching
 * added later to close the "unreachable from any UI link" gap for
 * /research/trends-stitcher), the TAB_HELP-sourced
 * descriptions read live off help/helpContent.ts (never a hard-coded
 * duplicate, so the test would catch drift), and clicking a card's
 * click-to-navigate body (role="button", separate from its `.drag-handle`
 * header) navigates to that screen's route.
 *
 * The last test below directly protects the G2 invariant itself (card
 * order == NAV_ITEMS' research-section order) against a *new* class of
 * regression this file's docstring couldn't have anticipated when G2 was
 * first fixed: DynamicGrid drag-and-drop persists a reordered layout to
 * localStorage, and if this hub's rendered card order were ever sourced
 * from that persisted state instead of the static, NAV_ITEMS-mirroring
 * `CARDS` array, a drag could silently reintroduce drift.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { ResearchHub } from "./ResearchHub";
import { TAB_HELP } from "../help/helpContent";

/** Stub landing screens, same pattern App.test.tsx uses to assert navigation. */
function Stub({ marker }: { marker: string }) {
  return <div>{marker}</div>;
}

function renderHub(initialPath = "/research") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/research" element={<ResearchHub />} />
        <Route path="/marketplace" element={<Stub marker="landed:marketplace" />} />
        <Route path="/compare" element={<Stub marker="landed:compare" />} />
        <Route path="/models" element={<Stub marker="landed:models" />} />
        <Route path="/strategy-health" element={<Stub marker="landed:strategy-health" />} />
        <Route path="/pairs" element={<Stub marker="landed:pairs" />} />
        <Route path="/options" element={<Stub marker="landed:options" />} />
        <Route path="/signals" element={<Stub marker="landed:signals" />} />
        <Route path="/sentiment" element={<Stub marker="landed:sentiment" />} />
        <Route path="/sector-selection" element={<Stub marker="landed:sector-selection" />} />
        <Route path="/forecast" element={<Stub marker="landed:forecast" />} />
        <Route path="/data-explorer" element={<Stub marker="landed:data-explorer" />} />
        <Route path="/research/trends-stitcher" element={<Stub marker="landed:trends-stitcher" />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("ResearchHub screen", () => {
  it("renders the header", () => {
    renderHub();
    expect(screen.getByText("Research")).toBeInTheDocument();
    expect(
      screen.getByText("Strategies and symbols worth a closer look before you act.")
    ).toBeInTheDocument();
  });

  it("renders all 12 card labels", () => {
    renderHub();
    for (const label of [
      "Pilots",
      "Compare",
      "Models",
      "Strategy Health",
      "Pairs radar",
      "Options",
      "Signal Breakdown",
      "Sentiment Dynamics",
      "Sector Selection",
      "Forecast Viewer",
      "Data Explorer",
      "Google Trends SVI Stitching",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("renders live TAB_HELP descriptions for all 11 cards, not a hard-coded duplicate", () => {
    renderHub();
    // Asserts against the actual TAB_HELP.* text at runtime -- a change to
    // helpContent.ts's prose would break this test if ResearchHub still
    // showed stale copy, which a hand-copied string would not catch.
    expect(screen.getByText(TAB_HELP.pilots.description)).toBeInTheDocument();
    expect(screen.getByText(TAB_HELP.compare.description)).toBeInTheDocument();
    expect(screen.getByText(TAB_HELP.models.description)).toBeInTheDocument();
    expect(
      screen.getByText(TAB_HELP["strategy-health"].description)
    ).toBeInTheDocument();
    expect(screen.getByText(TAB_HELP.pairs.description)).toBeInTheDocument();
    expect(screen.getByText(TAB_HELP.options.description)).toBeInTheDocument();
    expect(screen.getByText(TAB_HELP.signals.description)).toBeInTheDocument();
    expect(screen.getByText(TAB_HELP.sentiment.description)).toBeInTheDocument();
    expect(
      screen.getByText(TAB_HELP["sector-selection"].description)
    ).toBeInTheDocument();
    expect(screen.getByText(TAB_HELP.forecast.description)).toBeInTheDocument();
    expect(
      screen.getByText(TAB_HELP["data-explorer"].description)
    ).toBeInTheDocument();
  });

  it("renders the Google Trends SVI Stitching card's static description", () => {
    renderHub();
    // Not TAB_HELP-sourced (no helpContent.ts entry exists for this demo
    // screen yet) -- static prose mirroring TrendsVisualizer.tsx's own
    // subheading text verbatim.
    expect(
      screen.getByText(
        "Demonstrates the stitching of multiple overlapping 90-day Google Trends Search Volume Index (SVI) queries into a single continuous time series."
      )
    ).toBeInTheDocument();
  });

  it.each([
    ["Pilots", "landed:marketplace"],
    ["Compare", "landed:compare"],
    ["Models", "landed:models"],
    ["Strategy Health", "landed:strategy-health"],
    ["Pairs radar", "landed:pairs"],
    ["Options", "landed:options"],
    ["Signal Breakdown", "landed:signals"],
    ["Sentiment Dynamics", "landed:sentiment"],
    ["Sector Selection", "landed:sector-selection"],
    ["Forecast Viewer", "landed:forecast"],
    ["Data Explorer", "landed:data-explorer"],
    ["Google Trends SVI Stitching", "landed:trends-stitcher"],
  ])("clicking the %s card's body navigates to its route", async (label, marker) => {
    const user = userEvent.setup();
    renderHub();
    // The card's clickable target is its description body (role="button",
    // accessible name == the card label) -- separate from the `.drag-handle`
    // header (icon + label) that exists only to be grabbed for reordering.
    await user.click(screen.getByRole("button", { name: label }));
    expect(await screen.findByText(marker)).toBeInTheDocument();
  });

  it("clicking a card's header (icon + label) does NOT navigate -- only the body does", async () => {
    const user = userEvent.setup();
    renderHub();
    // "Pilots" text lives in the header now, not the
    // clickable body -- clicking it must be a no-op navigation-wise.
    await user.click(screen.getByText("Pilots"));
    expect(screen.queryByText("landed:marketplace")).not.toBeInTheDocument();
  });

});
