import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";
import { SettingsPaperBroker } from "./SettingsPaperBroker";

vi.mock("../api/client", () => ({
  api: {
    getPaperBrokerSettings: vi.fn(() =>
      Promise.resolve({
        groups: [{
          name: "Paper Broker",
          fields: [
            {
              key: "PAPER_BROKER_ENABLED",
              type: "bool",
              value: true,
              default: true,
              description: "Toggle",
              liveness: { applies: "immediately" },
            }
          ]
        }],
        applies_counts: { immediately: 0, next_daemon_restart: 0, no_effect: 0, env_pinned: 0 },
        drift_keys: [],
      })
    ),
  },
}));

describe("SettingsPaperBroker", () => {
  it("renders the settings wrapper", async () => {
    render(
      <MemoryRouter>
        <SettingsPaperBroker />
      </MemoryRouter>
    );

    expect(await screen.findByText("Paper Broker")).toBeInTheDocument();
    expect(screen.getByText("Configure Paper Broker execution backend, slippage, and defaults.")).toBeInTheDocument();
  });
});
