/**
 * Help.test.tsx — the searchable full-glossary screen (parity gap G10).
 * Covers: every glossary term renders collapsed by default, search filters
 * by term AND by definition text, an unmatched query renders the honest
 * empty state (not a silently-empty list), a term expands to show its
 * definition, and a live-threshold entry degrades to "—" (never a guessed
 * number) while thresholds haven't loaded.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Help } from "./Help";
import { GLOSSARY } from "../help/helpContent";
import { __resetThresholdsCache } from "../help/thresholds";
import { api } from "../api/client";

function renderScreen() {
  return render(
    <MemoryRouter>
      <Help />
    </MemoryRouter>
  );
}

describe("Help screen", () => {
  beforeEach(() => __resetThresholdsCache());
  afterEach(() => vi.restoreAllMocks());

  it("renders the header with the real glossary term count", () => {
    renderScreen();
    const count = Object.keys(GLOSSARY).length;
    expect(screen.getByRole("heading", { name: "Help & Glossary" })).toBeInTheDocument();
    expect(screen.getByTestId("help-result-count")).toHaveTextContent(`${count} of ${count} terms`);
  });

  it("lists every glossary term, collapsed by default", () => {
    renderScreen();
    expect(screen.getByTestId("help-term-conviction")).toBeInTheDocument();
    expect(screen.getByTestId("help-term-deployable")).toBeInTheDocument();
    expect(screen.queryByTestId("help-def-conviction")).not.toBeInTheDocument();
  });

  it("expanding a term shows its definition", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(screen.getByTestId("help-term-conviction"));
    expect(screen.getByTestId("help-def-conviction")).toHaveTextContent(
      /how confident the system is/i
    );
  });

  it("filters by a substring of the term itself", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.type(screen.getByTestId("help-search"), "kelly");
    expect(screen.getByTestId("help-term-kelly target")).toBeInTheDocument();
    expect(screen.queryByTestId("help-term-conviction")).not.toBeInTheDocument();
  });

  it("filters by a substring of the definition text, not just the term", async () => {
    const user = userEvent.setup();
    renderScreen();
    // "Engle-Granger" only appears in the "cointegration" definition, not in
    // the term name itself -- proves the search scans definitions too.
    await user.type(screen.getByTestId("help-search"), "engle-granger");
    expect(screen.getByTestId("help-term-cointegration")).toBeInTheDocument();
    expect(screen.queryByTestId("help-term-conviction")).not.toBeInTheDocument();
  });

  it("renders the honest empty state for a query matching nothing, not a fabricated list", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.type(screen.getByTestId("help-search"), "zzznomatch");
    expect(screen.getByTestId("help-empty")).toHaveTextContent("No matching terms");
    expect(screen.queryByTestId("help-glossary-list")).not.toBeInTheDocument();
  });

  it("renders the real live threshold once GET /thresholds resolves", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(screen.getByTestId("help-term-deployable"));
    // The element exists immediately (rendered with the honest "—"
    // placeholder before the mock's GET /thresholds delay resolves), so
    // waitFor is needed here to assert the eventual, resolved text -- a bare
    // findByTestId would resolve on first paint, before the real value loads.
    await waitFor(() =>
      expect(screen.getByTestId("help-def-deployable")).toHaveTextContent("PBO < 0.5")
    );
  });

  it("degrades a live-threshold entry to '—' (never a guessed number) when the fetch fails", async () => {
    vi.spyOn(api, "getThresholds").mockRejectedValue(new Error("offline"));
    const user = userEvent.setup();
    renderScreen();
    await user.click(screen.getByTestId("help-term-deployable"));
    const def = screen.getByTestId("help-def-deployable");
    expect(def).toHaveTextContent("PBO < —");
  });
});
