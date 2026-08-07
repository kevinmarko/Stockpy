import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { SettingsCacheLongShort } from "./SettingsCacheLongShort";
import { api } from "../api/client";

describe("SettingsCacheLongShort", () => {
  it("renders correctly with loaded settings", async () => {
    vi.spyOn(api, "getCacheLongShortSettings").mockResolvedValue({
      applies: "next_daemon_restart",
      env_drift: { detected: false, keys: [], note: "" },
      groups: [
        {
          name: "Cache Long/Short Overlay",
          fields: [
            {
              key: "CACHE_LONG_SHORT_ENABLED",
              type: "boolean",
              value: false,
              default: false,
              description: "Enable Cache Long/Short strategy",
              min: undefined,
              max: undefined,
              step: undefined,
              options: undefined,
              liveness: { applies: "immediately", restart_reason: null, capture_sites: [], env_pinned: false, dangerous: false, source: "env_file" },
            },
          ],
        },
      ],
    });

    render(
      <MemoryRouter>
        <SettingsCacheLongShort />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Cache Long/Short Strategy")).toBeInTheDocument();
    });
    
    expect(screen.getByText("CACHE_LONG_SHORT_ENABLED")).toBeInTheDocument();
  });
});
