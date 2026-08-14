import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";
import { SettingsModules } from "./SettingsModules";

vi.mock("../api/client", () => ({
  api: {
    getStrategyMatrix: vi.fn(() =>
      Promise.resolve({
        modules: [{ name: "rsi", description: "RSI", weight: 0.1 }],
        disabled: [],
      })
    ),
    getTunables: vi.fn(() =>
      Promise.resolve({
        groups: [{ name: "General", fields: [{ key: "SAMPLE", type: "str", value: "1", default: "1", description: "" }] }],
        applies_counts: { immediately: 0, next_daemon_restart: 0, no_effect: 0, env_pinned: 0 },
        drift_keys: [],
      })
    ),
    getSentimentSettings: vi.fn(() =>
      Promise.resolve({
        groups: [{ name: "Sentiment", fields: [] }],
        applies_counts: { immediately: 0, next_daemon_restart: 0, no_effect: 0, env_pinned: 0 },
        drift_keys: [],
      })
    ),
    getSectorSelectionSettings: vi.fn(() =>
      Promise.resolve({
        groups: [{ name: "Sector", fields: [] }],
        applies_counts: { immediately: 0, next_daemon_restart: 0, no_effect: 0, env_pinned: 0 },
        drift_keys: [],
      })
    ),
    getFmpSettings: vi.fn(() =>
      Promise.resolve({
        groups: [{ name: "FMP", fields: [] }],
        applies_counts: { immediately: 0, next_daemon_restart: 0, no_effect: 0, env_pinned: 0 },
        drift_keys: [],
      })
    ),
    getEtfTransmissionSettings: vi.fn(() =>
      Promise.resolve({
        groups: [{ name: "ETF", fields: [] }],
        applies_counts: { immediately: 0, next_daemon_restart: 0, no_effect: 0, env_pinned: 0 },
        drift_keys: [],
      })
    ),
    getPaperBrokerSettings: vi.fn(() =>
      Promise.resolve({
        groups: [{ name: "Paper Broker", fields: [{ key: "PAPER_BROKER_ENABLED", type: "bool", value: true, default: true, description: "" }] }],
        applies_counts: { immediately: 0, next_daemon_restart: 0, no_effect: 0, env_pinned: 0 },
        drift_keys: [],
      })
    ),
    getCacheLongShortSettings: vi.fn(() =>
      Promise.resolve({
        groups: [{ name: "Cache Long/Short", fields: [] }],
        applies_counts: { immediately: 0, next_daemon_restart: 0, no_effect: 0, env_pinned: 0 },
        drift_keys: [],
      })
    ),
    getPrompts: vi.fn(() =>
      Promise.resolve({
        prompts: [],
      })
    ),
    getLlmStatus: vi.fn(() =>
      Promise.resolve({
        capabilities: [],
      })
    ),
    getFollows: vi.fn(() => Promise.resolve([])),
    getPilots: vi.fn(() => Promise.resolve([])),
    getThresholds: vi.fn(() => Promise.resolve({
      vix_stress: 30,
      vrp_min: 0.02,
      ivr_min: 50,
      half_life_min: 5,
      half_life_max: 60,
      z_entry: 2.0,
      z_exit: 0.0,
      z_stop: 4.0,
    })),
  },
}));

describe("SettingsModules", () => {
  it("renders all setting module links including Paper Broker", async () => {
    render(
      <MemoryRouter>
        <SettingsModules />
      </MemoryRouter>
    );

    expect(screen.getByText("Modules & Integrations")).toBeInTheDocument();
    expect(screen.getByText("Paper Broker")).toBeInTheDocument();
    expect(await screen.findByText("1 paper broker settings")).toBeInTheDocument();
    expect(screen.getByText("Signal modules")).toBeInTheDocument();
    expect(screen.getByText("Prompt Registry")).toBeInTheDocument();
  });
});
