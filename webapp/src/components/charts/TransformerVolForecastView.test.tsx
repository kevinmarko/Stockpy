import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { TransformerVolForecastView } from "./TransformerVolForecastView";
import { api } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: {
    getTransformerForecast: vi.fn(),
  },
}));

describe("TransformerVolForecastView", () => {
  it("renders loading state then data", async () => {
    (api.getTransformerForecast as any).mockResolvedValue({
      symbol: "AAPL",
      current_vol: 0.15,
      forecast_horizon: 30,
      forecast_trajectory: [0.15, 0.16, 0.17],
      cone_lower_bounds: [0.14, 0.15, 0.16],
      cone_upper_bounds: [0.16, 0.17, 0.18],
      attention_weights: [[0.1, 0.9]],
      feature_importance: { "RSI": 0.5, "MACD": 0.5 },
    });

    render(<TransformerVolForecastView symbol="AAPL" />);

    expect(screen.getByText("Loading AI Vol Forecast...")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("🤖 Transformer Volatility Forecast: AAPL")).toBeInTheDocument();
    });

    expect(screen.getByText("Cone Forecast (Horizon: 30d)")).toBeInTheDocument();
    expect(screen.getByText("Current Vol: 15.0%")).toBeInTheDocument();
    expect(screen.getByText("RSI")).toBeInTheDocument();
  });
});
