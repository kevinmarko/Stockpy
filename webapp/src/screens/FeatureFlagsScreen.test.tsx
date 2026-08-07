import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { FeatureFlagsScreen } from "./FeatureFlagsScreen";
import { api } from "../api/client";

describe("FeatureFlagsScreen", () => {
  it("renders correctly with loaded feature flags", async () => {
    vi.spyOn(api, "getFeatureFlags").mockResolvedValue({
      applies: "next_daemon_restart",
      env_drift: { detected: false, keys: [], note: "" },
      groups: [
        {
          name: "Feature Flags",
          fields: [
            {
              key: "DEAD_LETTER_RETRY_ENABLED",
              type: "boolean",
              value: true,
              default: true,
              description: "Enable Dead Letter Retry",
              min: undefined,
              max: undefined,
              step: undefined,
              options: undefined,
              liveness: { applies: "immediately", restart_reason: null, capture_sites: [], env_pinned: false, dangerous: true, source: "env_file" },
            },
          ],
        },
      ],
    });

    render(
      <MemoryRouter>
        <FeatureFlagsScreen />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Feature Flags")).toBeInTheDocument();
    });
    
    expect(screen.getByText("DEAD_LETTER_RETRY_ENABLED")).toBeInTheDocument();
  });
});
