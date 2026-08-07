/**
 * SettingsFeatureFlags.test.tsx — the dedicated /settings/feature-flags
 * sub-route screen. GenericSettingsEditor's shared dirty-tracking/save/
 * rejection engine is exercised in depth by SentimentSettings.test.tsx; this
 * file only confirms this screen wires to its OWN api methods and field set.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsFeatureFlags } from "./SettingsFeatureFlags";
import { api, ApiError } from "../api/client";
import type { TunablesResponse } from "../api/types";

function baseFeatureFlagsTunables(overrides: Partial<TunablesResponse> = {}): TunablesResponse {
  return {
    applies: "next_daemon_restart",
    groups: [
      {
        name: "Feature Flags",
        fields: [
          {
            key: "AGENTIC_DISCOVERY_ENABLED", value: false, type: "boolean",
            default: true, description: "Enable the LLM-driven research loop.",
          }
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
      <SettingsFeatureFlags />
    </MemoryRouter>,
  );
}

describe("SettingsFeatureFlags screen", () => {
  afterEach(() => vi.restoreAllMocks());

  it("fetches via getFeatureFlags and renders its own field set", async () => {
    const spy = vi.spyOn(api, "getFeatureFlags").mockResolvedValue(baseFeatureFlagsTunables());
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Feature Flags" })).toBeInTheDocument();
    
    // Check for the field description or input
    expect(await screen.findByText("Enable the LLM-driven research loop.")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("shows the honest cold-start state when GET 404s", async () => {
    vi.spyOn(api, "getFeatureFlags").mockRejectedValue(new ApiError("not found", 404));
    renderScreen();
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
  });

  it("Save calls updateFeatureFlags, not other endpoints", async () => {
    vi.spyOn(api, "getFeatureFlags").mockResolvedValue(baseFeatureFlagsTunables());
    const ffSpy = vi.spyOn(api, "updateFeatureFlags").mockResolvedValue({
      written: { "AGENTIC_DISCOVERY_ENABLED": true },
      rejected: {},
      applies: "next_daemon_restart",
    });
    
    renderScreen();
    
    const checkbox = (await screen.findByRole("switch")) as HTMLInputElement;
    await userEvent.click(checkbox);
    
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() => expect(ffSpy).toHaveBeenCalledTimes(1));
    expect(ffSpy.mock.calls[0][0]).toEqual({ "AGENTIC_DISCOVERY_ENABLED": true });
  });
});
