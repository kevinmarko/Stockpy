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
      synthesis_id: "syn_test_123",
      prompt: "Test prompt",
      synthesized_code: "def generate_signals(df):\n    return df['close'] * 0",
      ast_safety_passed: true,
      ast_violations: [],
      suggested_parameters: { lookback: 20 },
      explanation: "Vectorized Z-Score calculation with volatility targeting.",
      model_used: "InvestYo-QuantSynthesizer-v4-DeepSeekR1",
      confidence_score: 0.95,
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
      synthesis_id: "syn_unsafe",
      prompt: "Malicious prompt",
      synthesized_code: "# Rejected code",
      ast_safety_passed: false,
      ast_violations: ["Forbidden import: 'os' is explicitly blacklisted."],
      suggested_parameters: {},
      explanation: "Security violation.",
      model_used: "InvestYo-QuantSynthesizer-v4",
      confidence_score: 0.1,
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

  it("handles synthesis api error", async () => {
    vi.mocked(api.synthesizeQuantResearch).mockRejectedValueOnce(new Error("Synthesis engine timed out"));
    render(<ResearchCopilotView />);

    const synthBtn = screen.getByRole("button", { name: /Synthesize Strategy/i });
    fireEvent.click(synthBtn);

    await waitFor(() => {
      expect(screen.getByText(/Synthesis engine timed out/i)).toBeInTheDocument();
    });
  });
});
