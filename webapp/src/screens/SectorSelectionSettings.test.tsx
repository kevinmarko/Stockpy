/**
 * SectorSelectionSettings.test.tsx — the dedicated /settings/sector-selection
 * sub-route screen. GenericSettingsEditor's shared dirty-tracking/save/
 * rejection engine is exercised in depth by SentimentSettings.test.tsx; this
 * file only confirms this screen wires to its OWN api methods and field set
 * (not the general tunables or sentiment editor's).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SectorSelectionSettings } from "./SectorSelectionSettings";
import { api, ApiError } from "../api/client";
import type { TunablesResponse } from "../api/types";

function baseSectorSelectionTunables(overrides: Partial<TunablesResponse> = {}): TunablesResponse {
  return {
    applies: "next_daemon_restart",
    groups: [
      {
        name: "Related Sector Selection",
        fields: [
          {
            key: "SECTOR_SELECTION_ENABLED", value: false, type: "boolean",
            default: false, description: "Master switch for the semantic Related Sector Selection feature.",
          },
          {
            key: "SECTOR_SELECTION_TOP_N", value: 3, type: "number",
            min: 1, max: 11, step: 1, default: 3,
            description: "Default number of top-ranked related sectors selected per target symbol.",
          },
        ],
      },
    ],
    env_drift: { detected: false, keys: [], note: "" },
    ...overrides,
  };
}

function renderScreen() {
  return render(
    <MemoryRouter>
      <SectorSelectionSettings />
    </MemoryRouter>,
  );
}

describe("SectorSelectionSettings screen", () => {
  afterEach(() => vi.restoreAllMocks());

  it("fetches via getSectorSelectionSettings and renders its own field set", async () => {
    const spy = vi.spyOn(api, "getSectorSelectionSettings").mockResolvedValue(baseSectorSelectionTunables());
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Sector Selection" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Related Sector Selection" })).toBeInTheDocument();
    expect(screen.getByLabelText("SECTOR_SELECTION_TOP_N")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("shows the honest cold-start state when GET 404s", async () => {
    vi.spyOn(api, "getSectorSelectionSettings").mockRejectedValue(new ApiError("not found", 404));
    renderScreen();
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
  });

  it("Save calls updateSectorSelectionSettings, not the sentiment or general tunables endpoint", async () => {
    vi.spyOn(api, "getSectorSelectionSettings").mockResolvedValue(baseSectorSelectionTunables());
    const sectorSpy = vi.spyOn(api, "updateSectorSelectionSettings").mockResolvedValue({
      written: { SECTOR_SELECTION_TOP_N: 5 },
      rejected: {},
      applies: "next_daemon_restart",
    });
    const sentimentSpy = vi.spyOn(api, "updateSentimentSettings");
    const generalSpy = vi.spyOn(api, "updateTunables");
    renderScreen();
    const input = (await screen.findByLabelText("SECTOR_SELECTION_TOP_N")) as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "5");
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() => expect(sectorSpy).toHaveBeenCalledTimes(1));
    expect(sectorSpy.mock.calls[0][0]).toEqual({ SECTOR_SELECTION_TOP_N: 5 });
    expect(sentimentSpy).not.toHaveBeenCalled();
    expect(generalSpy).not.toHaveBeenCalled();
  });
});
