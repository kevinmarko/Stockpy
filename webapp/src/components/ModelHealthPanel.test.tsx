/**
 * ModelHealthPanel.test.tsx — the panel links into the Forecasts screen
 * (forecasting rebuild F6b): to the universe table with no symbol, and to the
 * symbol's ledger view (`?symbol=`) with one.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { ModelHealthPanel } from "./ModelHealthPanel";

describe("ModelHealthPanel → Forecasts link", () => {
  it("with no symbol, links to the Forecasts universe table", () => {
    render(
      <MemoryRouter>
        <ModelHealthPanel />
      </MemoryRouter>
    );
    expect(screen.getByText("No model-health data")).toBeInTheDocument();
    expect(screen.getByTestId("model-health-forecasts-link").getAttribute("href")).toBe("/forecast");
  });

  it("with a symbol, links to that symbol on the Forecasts screen", async () => {
    render(
      <MemoryRouter>
        <ModelHealthPanel symbol="AAPL" />
      </MemoryRouter>
    );
    expect((await screen.findByTestId("model-health-forecasts-link")).getAttribute("href")).toBe("/forecast?symbol=AAPL");
  });
});
