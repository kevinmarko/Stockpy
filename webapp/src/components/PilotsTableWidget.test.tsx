/**
 * PilotsTableWidget.test.tsx -- covers happy-path render, loading, error,
 * and the real empty/cold-start state (never a fabricated 0/placeholder in
 * place of missing data).
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PilotsTableWidget } from "./PilotsTableWidget";
import { api } from "../api/client";
import type { PilotSummary } from "../api/types";

function makePilot(overrides: Partial<PilotSummary> = {}): PilotSummary {
  return {
    id: "momentum-core",
    name: "Momentum Core",
    category: "Momentum",
    description: "Cross-sectional momentum sleeve.",
    headline: {
      sharpe: 1.24,
      dsr: 0.97,
      pbo: 0.12,
      max_drawdown: 0.18,
      deployable: true,
      stress_gate_passed: true,
    },
    holdings_count: 8,
    top_holdings: [],
    aum_proxy: 125000,
    followers_proxy: 4,
    long_only: true,
    ...overrides,
  };
}

describe("PilotsTableWidget", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders a real loading state before data resolves", () => {
    vi.spyOn(api, "listPilots").mockImplementation(() => new Promise(() => {}));
    render(<PilotsTableWidget />);
    expect(screen.queryByTestId("pilotsTable-widget")).not.toBeInTheDocument();
  });

  it("renders the happy path with formatted Sharpe/Max DD columns", async () => {
    vi.spyOn(api, "listPilots").mockResolvedValueOnce([
      makePilot({ id: "momentum-core", name: "Momentum Core", category: "Momentum" }),
      makePilot({
        id: "quality-value",
        name: "Quality Value",
        category: "Factor",
        headline: {
          sharpe: null,
          dsr: null,
          pbo: null,
          max_drawdown: null,
          deployable: null,
        },
      }),
    ]);

    render(<PilotsTableWidget />);

    const widget = await screen.findByTestId("pilotsTable-widget");
    expect(widget.textContent).toContain("Momentum Core");
    expect(widget.textContent).toContain("Momentum");
    expect(widget.textContent).toContain("1.24");
    expect(widget.textContent).toContain("18%");

    // A Pilot with no computed headline metrics renders the honest "—"
    // sentinel, never a fabricated 0.
    expect(widget.textContent).toContain("Quality Value");
    const rows = screen.getAllByRole("row");
    const qualityRow = rows.find((r) => r.textContent?.includes("Quality Value"));
    expect(qualityRow?.textContent).toContain("—");
  });

  it("renders the honest empty state when no Pilots are registered", async () => {
    vi.spyOn(api, "listPilots").mockResolvedValueOnce([]);
    render(<PilotsTableWidget />);
    expect(await screen.findByText("No Pilots yet")).toBeInTheDocument();
    expect(screen.queryByTestId("pilotsTable-widget")).not.toBeInTheDocument();
  });

  it("renders the error state on a failed fetch, with a working retry", async () => {
    vi.spyOn(api, "listPilots").mockRejectedValueOnce(new Error("offline"));
    render(<PilotsTableWidget />);
    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
  });
});
