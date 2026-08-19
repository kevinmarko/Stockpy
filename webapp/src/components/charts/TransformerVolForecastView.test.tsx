import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { TransformerVolForecastView } from "./TransformerVolForecastView";
import { api } from "../../api/client";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getTransformerForecast: vi.fn(),
    },
  };
});

describe("TransformerVolForecastView", () => {
  it("renders loading state then standard data without quantiles", async () => {
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
    expect(screen.queryByTestId("macro-conditioned-badge")).not.toBeInTheDocument();
  });

  it("renders multi-quantile cone, macro badge, and sample counts", async () => {
    (api.getTransformerForecast as any).mockResolvedValue({
      symbol: "NVDA",
      forecast: { "1d": 0.25, "5d": 0.28, "21d": 0.33, "60d": 0.38 },
      quantile_forecast: {
        "1d": { q10: 0.18, q50: 0.25, q90: 0.31 },
        "5d": { q10: 0.20, q50: 0.28, q90: 0.36 },
        "21d": { q10: 0.24, q50: 0.33, q90: 0.41 },
        "60d": { q10: 0.27, q50: 0.38, q90: 0.46 },
      },
      attention_heatmap: [
        [0.1, 0.2, 0.7],
        [0.3, 0.4, 0.3],
        [0.2, 0.1, 0.7],
      ],
      trained_samples: 150,
      macro_conditioned: true,
    });

    render(<TransformerVolForecastView symbol="NVDA" />);

    await waitFor(() => {
      expect(screen.getByText("🤖 Transformer Volatility Forecast: NVDA")).toBeInTheDocument();
    });

    // Verify Macro-Conditioned badge
    expect(screen.getByTestId("macro-conditioned-badge")).toBeInTheDocument();
    expect(screen.getByText("Macro-Conditioned")).toBeInTheDocument();

    // Verify Sample Count badge
    expect(screen.getByText(/Trained on/)).toBeInTheDocument();
    expect(screen.getByText("150")).toBeInTheDocument();

    // Verify Quantile Probabilistic Cone UI elements
    expect(screen.getByText("Probabilistic Cone (q₁₀ - q₉₀)")).toBeInTheDocument();
    expect(screen.getByLabelText("Multi-horizon volatility probabilistic cone")).toBeInTheDocument();

    // Verify quantile breakdown values
    expect(screen.getByText("25.0%")).toBeInTheDocument();
    expect(screen.getByText("18.0%")).toBeInTheDocument();
    expect(screen.getByText("31.0%")).toBeInTheDocument();

    // Verify accessible attention heatmap
    const heatmap = screen.getByTestId("attention-heatmap");
    expect(heatmap).toBeInTheDocument();
    expect(heatmap).toHaveAttribute("aria-label", "Temporal Self-Attention Matrix Heatmap");
  });

  it("renders an honest empty state when attention_heatmap is empty", async () => {
    (api.getTransformerForecast as any).mockResolvedValue({
      symbol: "MSFT",
      forecast: { "1d": 0.12 },
      attention_heatmap: [],
      macro_conditioned: false,
    });

    render(<TransformerVolForecastView symbol="MSFT" />);

    await waitFor(() => {
      expect(screen.getByText("🤖 Transformer Volatility Forecast: MSFT")).toBeInTheDocument();
    });

    expect(screen.getByText("No attention data available.")).toBeInTheDocument();
  });

  it("renders error message cleanly on failure", async () => {
    (api.getTransformerForecast as any).mockRejectedValue(new Error("Symbol not found"));

    render(<TransformerVolForecastView symbol="INVALID" />);

    await waitFor(() => {
      expect(screen.getByText("Symbol not found")).toBeInTheDocument();
    });
  });
});


