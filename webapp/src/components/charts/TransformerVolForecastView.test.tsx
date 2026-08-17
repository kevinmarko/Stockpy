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
      forecast: { "1d": 0.15, "5d": 0.16, "21d": 0.17, "60d": 0.18 },
      attention_heatmap: [
        [0.1, 0.9],
        [0.5, 0.4],
      ],
    });

    render(<TransformerVolForecastView symbol="AAPL" />);

    expect(screen.getByText("Loading AI Vol Forecast...")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("🤖 Transformer Volatility Forecast: AAPL")).toBeInTheDocument();
    });

    expect(screen.getByText("Multi-Horizon Volatility Forecast")).toBeInTheDocument();
    expect(screen.getByText("1d")).toBeInTheDocument();
    expect(screen.getByText("60d")).toBeInTheDocument();
    expect(screen.getByText("15.0%")).toBeInTheDocument();
    expect(screen.getByTestId("attention-heatmap")).toBeInTheDocument();
  });

  it("renders an honest empty state when attention_heatmap is empty", async () => {
    (api.getTransformerForecast as any).mockResolvedValue({
      symbol: "MSFT",
      forecast: { "1d": 0.12 },
      attention_heatmap: [],
    });

    render(<TransformerVolForecastView symbol="MSFT" />);

    await waitFor(() => {
      expect(screen.getByText("🤖 Transformer Volatility Forecast: MSFT")).toBeInTheDocument();
    });

    expect(screen.getByText("No attention data available.")).toBeInTheDocument();
  });
});
