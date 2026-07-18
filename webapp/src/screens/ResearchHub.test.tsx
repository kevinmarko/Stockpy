/**
 * ResearchHub.test.tsx — the Research section's landing hub: all 9 screen
 * cards render with their label + description, the TAB_HELP-sourced
 * descriptions read live off help/helpContent.ts (never a hard-coded
 * duplicate, so the test would catch drift), and clicking a card navigates
 * to that screen's route.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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
        <Route path="/forecast" element={<Stub marker="landed:forecast" />} />
        <Route path="/data-explorer" element={<Stub marker="landed:data-explorer" />} />
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

  it("renders all 9 card labels", () => {
    renderHub();
    for (const label of [
      "Pilots",
      "Compare",
      "Models",
      "Strategy Health",
      "Pairs radar",
      "Options",
      "Signal Breakdown",
      "Forecast Viewer",
      "Data Explorer",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("renders live TAB_HELP descriptions for all 9 cards, not a hard-coded duplicate", () => {
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
    expect(screen.getByText(TAB_HELP.forecast.description)).toBeInTheDocument();
    expect(
      screen.getByText(TAB_HELP["data-explorer"].description)
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
    ["Forecast Viewer", "landed:forecast"],
    ["Data Explorer", "landed:data-explorer"],
  ])("clicking the %s card navigates to its route", async (label, marker) => {
    const user = userEvent.setup();
    renderHub();
    // getByText(label) exact-matches the card's own label div (a leaf node
    // whose normalized text is exactly the label) -- NOT a regex/substring
    // match, which would ambiguously hit e.g. "Compare"'s description
    // ("...Pilots you're considering following.") when looking for "Pilots".
    // The click bubbles up from the label div to the enclosing card <button>.
    await user.click(screen.getByText(label));
    expect(await screen.findByText(marker)).toBeInTheDocument();
  });
});
