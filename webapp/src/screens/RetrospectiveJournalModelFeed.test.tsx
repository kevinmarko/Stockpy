/**
 * RetrospectiveJournalModelFeed.test.tsx — the bridge banner's "Excluded
 * from Models" tile: closed paper trades deliberately NOT fed to the models
 * (options, manual clicks, delta hedges, untagged). Uses the real mock
 * fixture for the happy path and a spy for an older backend that omits
 * excluded_count (must render "—", never a fabricated 0).
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RetrospectiveJournal } from "./RetrospectiveJournal";
import { api } from "../api/client";
import { MOCK_BRIDGE_RELIABILITY } from "../api/mock";

function renderScreen() {
  return render(
    <MemoryRouter>
      <RetrospectiveJournal />
    </MemoryRouter>
  );
}

async function excludedTileValue(): Promise<string | null> {
  const label = await screen.findByText("Excluded from Models");
  return label.closest(".tile")!.querySelector(".tile-value")!.textContent;
}

describe("RetrospectiveJournal — model-feed exclusions", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the mock fixture's excluded_count", async () => {
    renderScreen();
    expect(MOCK_BRIDGE_RELIABILITY.excluded_count).toBe(3);
    await screen.findByText("Excluded from Models");
    expect(await excludedTileValue()).toBe("3");
  });

  it("renders — (not 0) when an older backend omits excluded_count", async () => {
    const { excluded_count: _omit, ...legacy } = MOCK_BRIDGE_RELIABILITY;
    vi.spyOn(api, "getBridgeReliability").mockResolvedValue(legacy);
    renderScreen();
    await screen.findByText("Excluded from Models");
    expect(await excludedTileValue()).toBe("—");
  });
});
