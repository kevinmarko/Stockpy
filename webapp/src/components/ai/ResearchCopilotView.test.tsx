import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ResearchCopilotView } from "./ResearchCopilotView";
import { api } from "../../api/client";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      synthesizeQuantResearch: vi.fn(),
      runAutonomousBacktest: vi.fn(),
    },
  };
});

describe("ResearchCopilotView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders header, archetype templates, and input controls", () => {
    render(<ResearchCopilotView />);
    expect(screen.getByText("AI Quant Research Copilot")).toBeInTheDocument();
    expect(screen.getByText("⚡ Vol-Targeted Mean Reversion")).toBeInTheDocument();
    expect(screen.getByText("⚡ Dual Momentum Filter")).toBeInTheDocument();
    expect(screen.getByText("Synthesize Strategy")).toBeInTheDocument();
  });

  it("applies a preset template when clicked", () => {
    render(<ResearchCopilotView />);
    const momentumBtn = screen.getByText("⚡ Dual Momentum Filter");
    fireEvent.click(momentumBtn);

    const promptArea = screen.getByPlaceholderText(/Describe your quantitative trading logic/i) as HTMLTextAreaElement;
    expect(promptArea.value).toContain("dual-momentum strategy");
  });

  it("synthesizes code and runs autonomous backtest successfully", async () => {
    const onDeployMock = vi.fn();

    vi.mocked(api.synthesizeQuantResearch).mockResolvedValueOnce({
      success: true,
      code: "def generate_signals(df):\n    return df['close'] * 0",
      metadata: { lookback: 20 },
      validation_passed: true,
      validation_errors: [],
      source_prompt: "Test prompt",
      synthesis_mode: "hypothesis",
      explanation: "Vectorized Z-Score calculation with volatility targeting.",
      target_asset_class: null,
      strategy_type: "Mean Reversion",
    });

    vi.mocked(api.runAutonomousBacktest).mockResolvedValueOnce({
      strategy_id: "syn_test_123",
      is_deployable: true,
      sharpe_ratio: 1.85,
      sortino_ratio: 2.6,
      max_drawdown: 0.11,
      pbo: 0.12,
      dsr: 0.98,
      turnover: 0.3,
      annualized_return: 0.25,
      cumulative_return: 0.65,
      win_rate: 0.58,
      calmar_ratio: 2.2,
      volatility: 0.13,
      gate_evaluations: {
        "pbo_gate (< 0.50)": true,
        "dsr_gate (> 0.95)": true,
        "sharpe_gate (> 0.50)": true,
        "max_drawdown_gate (< 0.30)": true,
      },
      failure_reasons: [],
      n_paths: 16,
      n_observations: 1008,
      execution_time_seconds: 1.2,
      cpcv_mean_oos_sharpe: 1.65,
      equity_curve: [
        { date: "2022-01-03", equity: 100000, drawdown: 0 },
        { date: "2022-06-01", equity: 115000, drawdown: 2.1 },
        { date: "2023-01-03", equity: 135000, drawdown: 1.5 },
      ],
    });

    render(<ResearchCopilotView onDeployStrategy={onDeployMock} />);

    const synthBtn = screen.getByRole("button", { name: /Synthesize Strategy/i });
    fireEvent.click(synthBtn);

    await waitFor(() => {
      expect(screen.getByText("AST Sandbox Security Check Passed (Zero Disallowed Imports/Calls)")).toBeInTheDocument();
    });

    expect(screen.getByText(/def generate_signals/)).toBeInTheDocument();
    // Real ResearchSynthesizeRequest accepts only prompt/strategy_type/target_asset_class.
    expect(api.synthesizeQuantResearch).toHaveBeenCalledWith(
      expect.objectContaining({
        prompt: expect.any(String),
        strategy_type: expect.any(String),
      })
    );
    const sentRequest = vi.mocked(api.synthesizeQuantResearch).mock.calls[0][0];
    expect(Object.keys(sentRequest).sort()).toEqual(["prompt", "strategy_type"]);

    const backtestBtn = screen.getByRole("button", { name: /Execute Autonomous Backtest/i });
    fireEvent.click(backtestBtn);

    await waitFor(() => {
      expect(screen.getByText("🚀 DEPLOYABLE (ALL GATES PASSED)")).toBeInTheDocument();
    });

    expect(screen.getByText("1.85")).toBeInTheDocument(); // Sharpe
    expect(screen.getByText("12.0%")).toBeInTheDocument(); // PBO
    expect(screen.getByText("0.980")).toBeInTheDocument(); // DSR

    const deployBtn = screen.getByRole("button", { name: /Deploy to Paper Broker/i });
    fireEvent.click(deployBtn);
    expect(onDeployMock).toHaveBeenCalledWith("syn_test_123");
  });

  it("handles AST safety violations gracefully", async () => {
    vi.mocked(api.synthesizeQuantResearch).mockResolvedValueOnce({
      success: false,
      code: "# Rejected code",
      metadata: {},
      validation_passed: false,
      validation_errors: ["Forbidden import: 'os' is explicitly blacklisted."],
      source_prompt: "Malicious prompt",
      synthesis_mode: "hypothesis",
      explanation: "Security violation.",
      target_asset_class: null,
      strategy_type: "Mean Reversion",
    });

    render(<ResearchCopilotView />);

    const synthBtn = screen.getByRole("button", { name: /Synthesize Strategy/i });
    fireEvent.click(synthBtn);

    await waitFor(() => {
      expect(screen.getByText("AST Security Violations Detected")).toBeInTheDocument();
      expect(screen.getByText("• Forbidden import: 'os' is explicitly blacklisted.")).toBeInTheDocument();
    });

    const backtestBtn = screen.getByRole("button", { name: /Execute Autonomous Backtest/i });
    expect(backtestBtn).toBeDisabled();
  });

  it("renders multi-regime breakdown and stability score when present in backtest results", async () => {
    vi.mocked(api.synthesizeQuantResearch).mockResolvedValueOnce({
      success: true,
      code: "def generate_signals(df):\n    return (df['Close'] > df['Close'].rolling(20).mean()).astype(float)",
      metadata: { lookback: 20 },
      validation_passed: true,
      validation_errors: [],
      source_prompt: "Regime-aware strategy",
      synthesis_mode: "hypothesis",
      explanation: "Dual regime strategy.",
      target_asset_class: null,
      strategy_type: "Mean Reversion",
    });

    vi.mocked(api.runAutonomousBacktest).mockResolvedValueOnce({
      strategy_id: "syn_regime_test",
      is_deployable: true,
      sharpe_ratio: 1.75,
      sortino_ratio: 2.3,
      max_drawdown: 0.12,
      pbo: 0.15,
      dsr: 0.97,
      turnover: 0.25,
      annualized_return: 0.22,
      cumulative_return: 0.55,
      win_rate: 0.56,
      calmar_ratio: 1.83,
      volatility: 0.12,
      gate_evaluations: {
        "pbo_gate (< 0.50)": true,
        "dsr_gate (> 0.95)": true,
        "sharpe_gate (> 0.50)": true,
        "max_drawdown_gate (< 0.30)": true,
      },
      failure_reasons: [],
      n_paths: 15,
      n_observations: 750,
      execution_time_seconds: 0.95,
      regime_breakdown: {
        "LOW_VOL_BULL": {
          sharpe: 2.15,
          sortino: 2.9,
          max_drawdown: 0.075,
          cumulative_return: 0.18,
          win_rate: 0.63,
          pnl_share: 0.65,
          n_bars: 300,
        },
        "HIGH_VOL_BEAR": {
          sharpe: 0.95,
          sortino: 1.25,
          max_drawdown: 0.115,
          cumulative_return: 0.025,
          win_rate: 0.51,
          pnl_share: 0.08,
          n_bars: 180,
        },
      },
      regime_stability_score: 0.88,
      passes_regime_stability: true,
      equity_curve: [],
    });

    render(<ResearchCopilotView />);

    const synthBtn = screen.getByRole("button", { name: /Synthesize Strategy/i });
    fireEvent.click(synthBtn);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Execute Autonomous Backtest/i })).toBeInTheDocument();
    });

    const backtestBtn = screen.getByRole("button", { name: /Execute Autonomous Backtest/i });
    fireEvent.click(backtestBtn);

    await waitFor(() => {
      expect(screen.getByText("Market Regime Performance & Overfitting Audit")).toBeInTheDocument();
    });

    expect(screen.getByText("88% (PASS)")).toBeInTheDocument();
    expect(screen.getByText("LOW VOL BULL")).toBeInTheDocument();
    expect(screen.getByText("HIGH VOL BEAR")).toBeInTheDocument();
    expect(screen.getByText("300 bars")).toBeInTheDocument();
    expect(screen.getByText("180 bars")).toBeInTheDocument();
  });
});
