import React, { useState } from "react";
import {
  Code,
  Play,
  ShieldCheck,
  AlertTriangle,
  Sparkles,
  Copy,
  Check,
  TrendingUp,
  BarChart2,
  RefreshCw,
  Cpu,
  Layers,
} from "lucide-react";
import { theme } from "../../theme";
import { api } from "../../api/client";
import { useMutation } from "../../hooks/useMutation";
import type {
  ResearchSynthesizeRequest,
  ResearchSynthesizeResponse,
  AutonomousBacktestRequest,
  AutonomousBacktestResponse,
} from "../../api/types";
import DemoDataBadge from "../DemoDataBadge";

export interface ResearchCopilotViewProps {
  initialPrompt?: string;
  initialUniverse?: string[];
  className?: string;
  onDeployStrategy?: (strategyId: string) => void;
}

const PRESET_TEMPLATES = [
  {
    name: "Vol-Targeted Mean Reversion",
    type: "Mean Reversion",
    lookback: 20,
    universe: ["SPY", "QQQ", "AAPL", "MSFT"],
    metric: "Sharpe Ratio",
    prompt:
      "Design a normalized rolling Z-score mean reversion signal with 20-day lookback, dynamic volatility targeting at 15% annualized vol, and entry threshold |Z| > 1.8.",
  },
  {
    name: "Dual Momentum Filter",
    type: "Momentum",
    lookback: 50,
    universe: ["NVDA", "AMD", "MSFT", "GOOGL", "AMZN"],
    metric: "Sortino Ratio",
    prompt:
      "Implement a dual-momentum strategy combining 50-day relative strength and 10-day short-term trend filter with strict 10% maximum trailing drawdown stop.",
  },
  {
    name: "Statistical Arbitrage Spread",
    type: "Pairs Trading",
    lookback: 30,
    universe: ["SPY", "QQQ"],
    metric: "Deflated Sharpe Ratio",
    prompt:
      "Construct a cointegrated pairs trading signal between SPY and QQQ using dynamic rolling Kalman beta estimation and Ornstein-Uhlenbeck mean-reverting bands.",
  },
  {
    name: "GARCH Volatility Regime Timing",
    type: "Volatility",
    lookback: 40,
    universe: ["SPY", "IWM", "TLT", "GLD"],
    metric: "Calmar Ratio",
    prompt:
      "Build an adaptive asset allocation strategy that toggles between equity momentum and treasury flight-to-safety based on GARCH(1,1) forward conditional volatility forecasts.",
  },
];

export const ResearchCopilotView: React.FC<ResearchCopilotViewProps> = ({
  initialPrompt = PRESET_TEMPLATES[0].prompt,
  initialUniverse = PRESET_TEMPLATES[0].universe,
  className = "",
  onDeployStrategy,
}) => {
  const [promptText, setPromptText] = useState(initialPrompt);
  const [strategyType, setStrategyType] = useState(PRESET_TEMPLATES[0].type);
  const [universeInput, setUniverseInput] = useState(initialUniverse.join(", "));
  const [lookbackDays, setLookbackDays] = useState(PRESET_TEMPLATES[0].lookback);
  const [targetMetric, setTargetMetric] = useState(PRESET_TEMPLATES[0].metric);

  const [copiedCode, setCopiedCode] = useState(false);
  const [synthesisResult, setSynthesisResult] = useState<ResearchSynthesizeResponse | null>(null);
  const [backtestResult, setBacktestResult] = useState<AutonomousBacktestResponse | null>(null);

  const synthesizeMutation = useMutation((req: ResearchSynthesizeRequest) =>
    api.synthesizeQuantResearch(req)
  );

  const backtestMutation = useMutation((req: AutonomousBacktestRequest) =>
    api.runAutonomousBacktest(req)
  );

  const handleApplyPreset = (preset: typeof PRESET_TEMPLATES[0]) => {
    setPromptText(preset.prompt);
    setStrategyType(preset.type);
    setUniverseInput(preset.universe.join(", "));
    setLookbackDays(preset.lookback);
    setTargetMetric(preset.metric);
  };

  const handleSynthesize = async () => {
    // universeInput/lookbackDays/targetMetric are local UI state consumed
    // only by the (separate, already-correct) backtest request below -- the
    // real ResearchSynthesizeRequest accepts only prompt/strategy_type/
    // target_asset_class, so they're intentionally not sent here.
    const res = await synthesizeMutation.run({
      prompt: promptText,
      strategy_type: strategyType,
    });

    if (res) {
      setSynthesisResult(res);
      // Auto reset backtest result when new code is synthesized
      setBacktestResult(null);
    }
  };

  const handleRunBacktest = async () => {
    if (!synthesisResult?.code) return;
    const universe = universeInput
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);

    const res = await backtestMutation.run({
      strategy_code: synthesisResult.code,
      // No synthesis_id exists on the real synthesize response -- generate a
      // client-side id purely for UI/backtest bookkeeping (never implied to
      // have come from the API).
      strategy_id: typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : undefined,
      symbols: universe,
      initial_capital: 100000,
      cpcv_folds: 6,
      purge_window: 5,
      embargo_window: 5,
      transaction_cost_bps: 5.0,
    });

    if (res) {
      setBacktestResult(res);
    }
  };

  const handleCopyCode = () => {
    if (!synthesisResult?.code) return;
    navigator.clipboard.writeText(synthesisResult.code).then(
      () => {
        setCopiedCode(true);
        setTimeout(() => setCopiedCode(false), 2000);
      },
      () => {
        // Clipboard writes can be rejected (permission denied, an
        // insecure/sandboxed context, etc.) -- don't claim Copied when it
        // didn't happen, and don't leave an unhandled promise rejection in
        // the console.
      }
    );
  };

  return (
    <div
      className={`research-copilot-container ${className}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 16,
        background: theme.base,
        color: theme.textPrimary,
        borderRadius: 8,
        padding: 16,
        border: `1px solid ${theme.border}`,
      }}
    >
      {/* Header Bar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 12,
          borderBottom: `1px solid ${theme.border}`,
          paddingBottom: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 8,
              background: "rgba(56, 189, 248, 0.15)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: theme.accent,
            }}
          >
            <Sparkles size={20} />
          </div>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <h2 style={{ margin: 0, fontSize: "1.15rem", fontWeight: 700 }}>
                AI Quant Research Copilot
              </h2>
              <DemoDataBadge />
            </div>
            <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
              Natural Language Strategy Synthesis • AST Security Sandbox • Purged-CV Backtester
            </div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div
            style={{
              fontSize: "0.75rem",
              padding: "4px 10px",
              borderRadius: 12,
              background: "rgba(16, 185, 129, 0.12)",
              color: theme.growth,
              border: "1px solid rgba(16, 185, 129, 0.3)",
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <Cpu size={13} />
            DeepSeek-R1 / InvestYo Synthesizer v4
          </div>
        </div>
      </div>

      {/* Preset Strategy Chips */}
      <div>
        <div style={{ fontSize: "0.75rem", color: theme.textSecondary, marginBottom: 6 }}>
          Quick Archetype Templates:
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {PRESET_TEMPLATES.map((preset) => (
            <button
              key={preset.name}
              onClick={() => handleApplyPreset(preset)}
              style={{
                background: theme.surface,
                border: `1px solid ${theme.border}`,
                color: theme.textPrimary,
                borderRadius: 16,
                padding: "4px 12px",
                fontSize: "0.75rem",
                cursor: "pointer",
                transition: "all 0.15s ease",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = theme.accent;
                e.currentTarget.style.color = theme.accent;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = theme.border;
                e.currentTarget.style.color = theme.textPrimary;
              }}
            >
              ⚡ {preset.name}
            </button>
          ))}
        </div>
      </div>

      {/* IDE Input Controls & Parameters */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: 12,
          background: theme.surface,
          padding: 12,
          borderRadius: 6,
          border: `1px solid ${theme.border}`,
        }}
      >
        <div>
          <label style={{ fontSize: "0.75rem", color: theme.textSecondary, display: "block", marginBottom: 4 }}>
            Strategy Family
          </label>
          <select
            value={strategyType}
            onChange={(e) => setStrategyType(e.target.value)}
            style={{
              width: "100%",
              padding: "6px 8px",
              background: theme.base,
              color: theme.textPrimary,
              border: `1px solid ${theme.border}`,
              borderRadius: 4,
              fontSize: "0.8rem",
            }}
          >
            <option value="Mean Reversion">Mean Reversion</option>
            <option value="Momentum">Momentum / Trend Following</option>
            <option value="Pairs Trading">Pairs Trading / Cointegration</option>
            <option value="Volatility">Volatility Regime Timing</option>
            <option value="Statistical Arbitrage">Statistical Arbitrage</option>
            <option value="Cross-Sectional Factor">Cross-Sectional Factor</option>
          </select>
        </div>

        <div>
          <label style={{ fontSize: "0.75rem", color: theme.textSecondary, display: "block", marginBottom: 4 }}>
            Universe Symbols (comma-separated)
          </label>
          <input
            type="text"
            value={universeInput}
            onChange={(e) => setUniverseInput(e.target.value)}
            placeholder="e.g. SPY, QQQ, AAPL, MSFT"
            style={{
              width: "100%",
              padding: "6px 8px",
              background: theme.base,
              color: theme.textPrimary,
              border: `1px solid ${theme.border}`,
              borderRadius: 4,
              fontSize: "0.8rem",
            }}
          />
        </div>

        <div>
          <label style={{ fontSize: "0.75rem", color: theme.textSecondary, display: "block", marginBottom: 4 }}>
            Lookback Period (Days)
          </label>
          <input
            type="number"
            value={lookbackDays}
            onChange={(e) => setLookbackDays(Number(e.target.value))}
            min={5}
            max={252}
            style={{
              width: "100%",
              padding: "6px 8px",
              background: theme.base,
              color: theme.textPrimary,
              border: `1px solid ${theme.border}`,
              borderRadius: 4,
              fontSize: "0.8rem",
            }}
          />
        </div>

        <div>
          <label style={{ fontSize: "0.75rem", color: theme.textSecondary, display: "block", marginBottom: 4 }}>
            Target Optimization Metric
          </label>
          <select
            value={targetMetric}
            onChange={(e) => setTargetMetric(e.target.value)}
            style={{
              width: "100%",
              padding: "6px 8px",
              background: theme.base,
              color: theme.textPrimary,
              border: `1px solid ${theme.border}`,
              borderRadius: 4,
              fontSize: "0.8rem",
            }}
          >
            <option value="Sharpe Ratio">Deflated Sharpe Ratio (DSR)</option>
            <option value="Sortino Ratio">Sortino Ratio</option>
            <option value="Calmar Ratio">Calmar Ratio (Drawdown-Adjusted)</option>
            <option value="PBO Minimum">PBO Minimization (Bailey 2014)</option>
          </select>
        </div>
      </div>

      {/* Natural Language Prompt Area */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <label style={{ fontSize: "0.8rem", fontWeight: 600, color: theme.textPrimary }}>
            Research Prompt & Quantitative Specification:
          </label>
          <span style={{ fontSize: "0.7rem", color: theme.textSecondary }}>
            Markdown / LaTeX formula specifications supported
          </span>
        </div>
        <textarea
          value={promptText}
          onChange={(e) => setPromptText(e.target.value)}
          rows={3}
          style={{
            width: "100%",
            padding: 10,
            background: theme.surface,
            color: theme.textPrimary,
            border: `1px solid ${theme.border}`,
            borderRadius: 6,
            fontSize: "0.85rem",
            fontFamily: "inherit",
            resize: "vertical",
          }}
          placeholder="Describe your quantitative trading logic, filters, and mathematical formulations..."
        />
      </div>

      {/* Synthesize Button */}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
        <button
          onClick={handleSynthesize}
          disabled={synthesizeMutation.pending || !promptText.trim()}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            background: synthesizeMutation.pending ? theme.border : theme.accent,
            color: synthesizeMutation.pending ? theme.textSecondary : "#000",
            border: "none",
            borderRadius: 6,
            padding: "8px 18px",
            fontWeight: 700,
            fontSize: "0.85rem",
            cursor: synthesizeMutation.pending ? "not-allowed" : "pointer",
            transition: "all 0.15s ease",
          }}
        >
          {synthesizeMutation.pending ? (
            <>
              <RefreshCw size={15} className="icon-spin" />
              Synthesizing Alpha AST...
            </>
          ) : (
            <>
              <Sparkles size={15} />
              Synthesize Strategy
            </>
          )}
        </button>
      </div>

      {/* Synthesis Error Display */}
      {synthesizeMutation.error && (
        <div
          style={{
            background: "rgba(239, 68, 68, 0.12)",
            border: "1px solid rgba(239, 68, 68, 0.3)",
            color: theme.decline,
            padding: 10,
            borderRadius: 6,
            fontSize: "0.8rem",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <AlertTriangle size={16} />
          <span>Synthesis Error: {String(synthesizeMutation.error)}</span>
        </div>
      )}

      {/* Synthesized Code & AST Safety Banner */}
      {synthesisResult && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 10,
            background: theme.surface,
            border: `1px solid ${theme.border}`,
            borderRadius: 6,
            padding: 14,
          }}
        >
          {/* AST Safety Verification Bar */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: 8,
              padding: "8px 12px",
              borderRadius: 6,
              background: synthesisResult.validation_passed
                ? "rgba(16, 185, 129, 0.1)"
                : "rgba(239, 68, 68, 0.1)",
              border: `1px solid ${
                synthesisResult.validation_passed
                  ? "rgba(16, 185, 129, 0.3)"
                  : "rgba(239, 68, 68, 0.3)"
              }`,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              {synthesisResult.validation_passed ? (
                <>
                  <ShieldCheck size={18} color={theme.growth} />
                  <span style={{ fontSize: "0.8rem", fontWeight: 600, color: theme.growth }}>
                    AST Sandbox Security Check Passed (Zero Disallowed Imports/Calls)
                  </span>
                </>
              ) : (
                <>
                  <AlertTriangle size={18} color={theme.decline} />
                  <span style={{ fontSize: "0.8rem", fontWeight: 600, color: theme.decline }}>
                    AST Security Violations Detected
                  </span>
                </>
              )}
            </div>

            <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
              Synthesis Mode: {synthesisResult.synthesis_mode}
            </div>
          </div>

          {/* Validation Errors List */}
          {!synthesisResult.validation_passed && synthesisResult.validation_errors.length > 0 && (
            <div
              style={{
                background: "rgba(239, 68, 68, 0.08)",
                padding: 8,
                borderRadius: 4,
                fontSize: "0.75rem",
                color: theme.decline,
              }}
            >
              {synthesisResult.validation_errors.map((v, i) => (
                <div key={i}>• {v}</div>
              ))}
            </div>
          )}

          {/* Explanation Callout */}
          {synthesisResult.explanation && (
            <div
              style={{
                fontSize: "0.78rem",
                color: theme.textSecondary,
                background: theme.base,
                padding: "8px 12px",
                borderRadius: 4,
                border: `1px solid ${theme.border}`,
              }}
            >
              💡 <strong>Quant Architecture:</strong> {synthesisResult.explanation}
            </div>
          )}

          {/* Code Header & Copy Button */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginTop: 4,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.8rem", fontWeight: 600 }}>
              <Code size={15} color={theme.accent} />
              <span>Synthesized Python Alpha Kernel (`generate_signals` Entrypoint)</span>
            </div>
            <button
              onClick={handleCopyCode}
              style={{
                background: "transparent",
                border: `1px solid ${theme.border}`,
                color: copiedCode ? theme.growth : theme.textSecondary,
                borderRadius: 4,
                padding: "3px 8px",
                fontSize: "0.72rem",
                display: "flex",
                alignItems: "center",
                gap: 4,
                cursor: "pointer",
              }}
            >
              {copiedCode ? <Check size={12} /> : <Copy size={12} />}
              {copiedCode ? "Copied" : "Copy Code"}
            </button>
          </div>

          {/* Code Display Pre */}
          <pre
            style={{
              background: "#080c14",
              border: `1px solid rgba(255, 255, 255, 0.1)`,
              borderRadius: 6,
              padding: 12,
              color: "#38bdf8",
              fontFamily: 'Consolas, Monaco, "Courier New", monospace',
              fontSize: "0.78rem",
              lineHeight: 1.45,
              overflowX: "auto",
              maxHeight: 280,
              margin: 0,
            }}
          >
            {synthesisResult.code}
          </pre>

          {/* Action Bar for Backtesting */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginTop: 6,
              flexWrap: "wrap",
              gap: 8,
            }}
          >
            <div style={{ fontSize: "0.75rem", color: theme.textSecondary, display: "flex", alignItems: "center", gap: 4 }}>
              <Layers size={13} />
              Combinatorial Purged Cross-Validation (CPCV: N=16 paths, 6-fold)
            </div>

            <button
              onClick={handleRunBacktest}
              disabled={backtestMutation.pending || !synthesisResult.validation_passed}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                background: synthesisResult.validation_passed ? theme.growth : theme.border,
                color: synthesisResult.validation_passed ? "#000" : theme.textSecondary,
                border: "none",
                borderRadius: 6,
                padding: "8px 18px",
                fontWeight: 700,
                fontSize: "0.85rem",
                cursor:
                  backtestMutation.pending || !synthesisResult.validation_passed
                    ? "not-allowed"
                    : "pointer",
                transition: "all 0.15s ease",
              }}
            >
              {backtestMutation.pending ? (
                <>
                  <RefreshCw size={15} className="icon-spin" />
                  Running Combinatorial Purged-CV...
                </>
              ) : (
                <>
                  <Play size={15} fill="#000" />
                  Execute Autonomous Backtest
                </>
              )}
            </button>
          </div>
        </div>
      )}

      {/* Backtest Error Display */}
      {backtestMutation.error && (
        <div
          style={{
            background: "rgba(239, 68, 68, 0.12)",
            border: "1px solid rgba(239, 68, 68, 0.3)",
            color: theme.decline,
            padding: 10,
            borderRadius: 6,
            fontSize: "0.8rem",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <AlertTriangle size={16} />
          <span>Backtest Execution Error: {String(backtestMutation.error)}</span>
        </div>
      )}

      {/* Backtest & Institutional Overfitting Metrics Dashboard */}
      {backtestResult && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 14,
            background: theme.surface,
            border: `1px solid ${theme.border}`,
            borderRadius: 6,
            padding: 16,
          }}
        >
          {/* Header & Deployability Verdict */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: 10,
              borderBottom: `1px solid ${theme.border}`,
              paddingBottom: 10,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <BarChart2 size={18} color={theme.accent} />
              <h3 style={{ margin: 0, fontSize: "1rem", fontWeight: 700 }}>
                CPCV & Overfitting Validation Summary
              </h3>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div
                style={{
                  padding: "4px 12px",
                  borderRadius: 14,
                  fontSize: "0.8rem",
                  fontWeight: 800,
                  background: backtestResult.is_deployable
                    ? "rgba(16, 185, 129, 0.15)"
                    : "rgba(239, 68, 68, 0.15)",
                  color: backtestResult.is_deployable ? theme.growth : theme.decline,
                  border: `1px solid ${
                    backtestResult.is_deployable
                      ? "rgba(16, 185, 129, 0.4)"
                      : "rgba(239, 68, 68, 0.4)"
                  }`,
                }}
              >
                {backtestResult.is_deployable ? "🚀 DEPLOYABLE (ALL GATES PASSED)" : "⛔ GATED (FAILED GATES)"}
              </div>

              {backtestResult.is_synthetic_data && (
                <div
                  style={{
                    padding: "4px 12px",
                    borderRadius: 14,
                    fontSize: "0.8rem",
                    fontWeight: 800,
                    background: "rgba(245, 158, 11, 0.15)",
                    color: "#f59e0b",
                    border: "1px solid rgba(245, 158, 11, 0.4)",
                  }}
                >
                  ⚠️ SYNTHETIC DATA FALLBACK
                </div>
              )}

              {backtestResult.is_deployable && !backtestResult.is_synthetic_data && onDeployStrategy && (
                <button
                  onClick={() => onDeployStrategy(backtestResult.strategy_id)}
                  style={{
                    background: theme.accent,
                    color: "#000",
                    border: "none",
                    borderRadius: 4,
                    padding: "4px 12px",
                    fontWeight: 700,
                    fontSize: "0.75rem",
                    cursor: "pointer",
                  }}
                >
                  Deploy to Paper Broker
                </button>
              )}
            </div>
          </div>

          {/* Key Metric Grid */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
              gap: 10,
            }}
          >
            <MetricCard
              label="Sharpe Ratio"
              value={backtestResult.sharpe_ratio != null ? backtestResult.sharpe_ratio.toFixed(2) : "—"}
              sub={backtestResult.cpcv_mean_oos_sharpe != null ? `OOS: ${backtestResult.cpcv_mean_oos_sharpe.toFixed(2)}` : "OOS: —"}
              color={backtestResult.sharpe_ratio != null && backtestResult.sharpe_ratio >= 1.5 ? theme.growth : theme.accent}
            />
            <MetricCard
              label="Sortino Ratio"
              value={backtestResult.sortino_ratio != null ? backtestResult.sortino_ratio.toFixed(2) : "—"}
              sub="Downside Adjusted"
              color={theme.growth}
            />
            <MetricCard
              label="PBO Overfitting"
              value={backtestResult.pbo != null ? `${(backtestResult.pbo * 100).toFixed(1)}%` : "—"}
              sub="Bailey < 50%"
              color={backtestResult.pbo != null && backtestResult.pbo < 0.3 ? theme.growth : theme.caution}
            />
            <MetricCard
              label="Deflated Sharpe (DSR)"
              value={backtestResult.dsr != null ? backtestResult.dsr.toFixed(3) : "—"}
              sub="DSR > 0.95 Gate"
              color={backtestResult.dsr != null && backtestResult.dsr >= 0.95 ? theme.growth : theme.decline}
            />
            <MetricCard
              label="Max Drawdown"
              value={backtestResult.max_drawdown != null ? `-${(backtestResult.max_drawdown * 100).toFixed(1)}%` : "—"}
              sub="Peak-to-Trough"
              color={backtestResult.max_drawdown != null && backtestResult.max_drawdown < 0.15 ? theme.growth : theme.decline}
            />
            <MetricCard
              label="Annualized Return"
              value={backtestResult.annualized_return != null ? `+${(backtestResult.annualized_return * 100).toFixed(1)}%` : "—"}
              sub={backtestResult.win_rate != null ? `Win: ${(backtestResult.win_rate * 100).toFixed(0)}%` : "Win: —"}
              color={theme.growth}
            />
            <MetricCard
              label="Calmar Ratio"
              value={backtestResult.calmar_ratio != null ? backtestResult.calmar_ratio.toFixed(2) : "—"}
              sub="Ret / MaxDD"
              color={theme.accent}
            />
            <MetricCard
              label="Daily Turnover"
              value={backtestResult.turnover != null ? `${(backtestResult.turnover * 100).toFixed(0)}%` : "—"}
              sub="Rebalance Cost"
              color={theme.textPrimary}
            />
          </div>

          {/* Gate Verification Badges */}
          <div style={{ background: theme.base, padding: 10, borderRadius: 6, border: `1px solid ${theme.border}` }}>
            <div style={{ fontSize: "0.75rem", color: theme.textSecondary, marginBottom: 6 }}>
              Institutional Deployability Gates (Bailey & Lopez de Prado 2014):
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {Object.entries(backtestResult.gate_evaluations).map(([gate, passed]) => (
                <div
                  key={gate}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    fontSize: "0.72rem",
                    padding: "3px 8px",
                    borderRadius: 4,
                    background: passed ? "rgba(16, 185, 129, 0.1)" : "rgba(239, 68, 68, 0.1)",
                    color: passed ? theme.growth : theme.decline,
                    border: `1px solid ${passed ? "rgba(16, 185, 129, 0.3)" : "rgba(239, 68, 68, 0.3)"}`,
                  }}
                >
                  {passed ? <Check size={12} /> : <AlertTriangle size={12} />}
                  <span>{gate}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Multi-Regime Performance Breakdown & Stability Audit */}
          {backtestResult.regime_breakdown && Object.keys(backtestResult.regime_breakdown).length > 0 && (
            <div style={{ background: theme.base, padding: 12, borderRadius: 6, border: `1px solid ${theme.border}` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
                <div style={{ fontSize: "0.8rem", fontWeight: 700, display: "flex", alignItems: "center", gap: 6 }}>
                  <Layers size={14} color={theme.accent} />
                  <span>Market Regime Performance & Overfitting Audit</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: "0.72rem", color: theme.textSecondary }}>
                    Regime Stability:
                  </span>
                  <span
                    style={{
                      fontSize: "0.75rem",
                      fontWeight: 800,
                      padding: "2px 8px",
                      borderRadius: 4,
                      background: backtestResult.passes_regime_stability !== false ? "rgba(16, 185, 129, 0.15)" : "rgba(239, 68, 68, 0.15)",
                      color: backtestResult.passes_regime_stability !== false ? theme.growth : theme.decline,
                      border: `1px solid ${backtestResult.passes_regime_stability !== false ? "rgba(16, 185, 129, 0.3)" : "rgba(239, 68, 68, 0.3)"}`,
                    }}
                  >
                    {backtestResult.regime_stability_score != null ? `${(backtestResult.regime_stability_score * 100).toFixed(0)}%` : "100%"} (
                    {backtestResult.passes_regime_stability !== false ? "PASS" : "FAIL"})
                  </span>
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 8 }}>
                {Object.entries(backtestResult.regime_breakdown).map(([regimeName, regMetrics]) => (
                  <div
                    key={regimeName}
                    style={{
                      background: theme.surface,
                      border: `1px solid ${theme.border}`,
                      borderRadius: 6,
                      padding: "8px 10px",
                      display: "flex",
                      flexDirection: "column",
                      gap: 4,
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <span style={{ fontSize: "0.72rem", fontWeight: 700, color: theme.accent }}>
                        {regimeName.replace(/_/g, " ")}
                      </span>
                      <span style={{ fontSize: "0.65rem", color: theme.textSecondary }}>
                        {regMetrics.n_bars} bars
                      </span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem" }}>
                      <span style={{ color: theme.textSecondary }}>Sharpe:</span>
                      <span style={{ fontWeight: 700, color: regMetrics.sharpe >= 1.0 ? theme.growth : theme.textPrimary }}>
                        {regMetrics.sharpe.toFixed(2)}
                      </span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem" }}>
                      <span style={{ color: theme.textSecondary }}>MaxDD:</span>
                      <span style={{ fontWeight: 700, color: regMetrics.max_drawdown < 0.20 ? theme.growth : theme.decline }}>
                        {(regMetrics.max_drawdown * 100).toFixed(1)}%
                      </span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem" }}>
                      <span style={{ color: theme.textSecondary }}>PnL Share:</span>
                      <span style={{ fontWeight: 700, color: regMetrics.pnl_share >= 0 ? theme.growth : theme.decline }}>
                        {(regMetrics.pnl_share * 100).toFixed(1)}%
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Mini Equity Curve Visualizer */}
          {backtestResult.equity_curve && backtestResult.equity_curve.length > 0 && (
            <div style={{ background: theme.base, padding: 12, borderRadius: 6, border: `1px solid ${theme.border}` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <div style={{ fontSize: "0.78rem", fontWeight: 600, display: "flex", alignItems: "center", gap: 6 }}>
                  <TrendingUp size={14} color={theme.growth} />
                  <span>Out-of-Sample Equity Trajectory ($100k Base)</span>
                </div>
                <div style={{ fontSize: "0.72rem", color: theme.textSecondary }}>
                  Total Observations: {backtestResult.n_observations} • CPCV Paths: {backtestResult.n_paths}
                </div>
              </div>

              <div style={{ width: "100%", height: 110, position: "relative" }}>
                <svg
                  viewBox="0 0 500 100"
                  preserveAspectRatio="none"
                  style={{ width: "100%", height: "100%", overflow: "visible" }}
                >
                  <defs>
                    <linearGradient id="copilotEqGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={theme.growth} stopOpacity="0.3" />
                      <stop offset="100%" stopColor={theme.growth} stopOpacity="0.0" />
                    </linearGradient>
                  </defs>

                  {/* Polyline & Area */}
                  {(() => {
                    const pts = backtestResult.equity_curve!;
                    const minEq = Math.min(...pts.map((p) => p.equity));
                    const maxEq = Math.max(...pts.map((p) => p.equity));
                    const span = maxEq - minEq || 1;

                    const coords = pts.map((p, idx) => {
                      const x = (idx / (pts.length - 1)) * 500;
                      const y = 90 - ((p.equity - minEq) / span) * 80;
                      return `${x.toFixed(1)},${y.toFixed(1)}`;
                    });

                    const areaCoords = `0,100 ${coords.join(" ")} 500,100`;

                    return (
                      <>
                        <polygon points={areaCoords} fill="url(#copilotEqGrad)" />
                        <polyline
                          points={coords.join(" ")}
                          fill="none"
                          stroke={theme.growth}
                          strokeWidth="2"
                        />
                      </>
                    );
                  })()}
                </svg>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

interface MetricCardProps {
  label: string;
  value: string;
  sub: string;
  color?: string;
}

const MetricCard: React.FC<MetricCardProps> = ({ label, value, sub, color = theme.textPrimary }) => (
  <div
    style={{
      background: theme.base,
      border: `1px solid ${theme.border}`,
      borderRadius: 6,
      padding: "8px 10px",
      display: "flex",
      flexDirection: "column",
      gap: 2,
    }}
  >
    <div style={{ fontSize: "0.68rem", color: theme.textSecondary }}>{label}</div>
    <div style={{ fontSize: "1.05rem", fontWeight: 700, color }}>{value}</div>
    <div style={{ fontSize: "0.65rem", color: theme.textSecondary }}>{sub}</div>
  </div>
);
