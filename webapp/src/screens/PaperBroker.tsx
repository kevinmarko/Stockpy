import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { TabGuide } from "../components/TabGuide";
import { Modal } from "../components/Modal";
import { theme } from "../theme";
import { ScenarioHeatmap } from "../components/options/ScenarioHeatmap";
import { VolSurfaceView } from "../components/options/VolSurfaceView";
import { EarningsCrushScanner } from "../components/options/EarningsCrushScanner";
import { UnusualFlowFeed } from "../components/options/UnusualFlowFeed";
import { VolForecastScanner } from "../components/options/VolForecastScanner";
import { GammaScalperView } from "../components/options/GammaScalperView";
import { DispersionScanner } from "../components/options/DispersionScanner";
import { ZeroDteDesk } from "../components/options/ZeroDteDesk";
import { VpinGauge } from "../components/options/VpinGauge";
import { SmartOrderRouterView } from "../components/options/SmartOrderRouterView";
import { GexProfileView } from "../components/options/GexProfileView";
import { LobDepthView } from "../components/options/LobDepthView";
import { CopulaSpreadView } from "../components/options/CopulaSpreadView";
import { MarketMakerAgentView } from "../components/options/MarketMakerAgentView";
import type { RollOrderRequest } from "../api/types";

export function PaperBroker() {
  const account = useApi(() => api.getPaperBrokerAccount());
  const positions = useApi(() => api.getPaperBrokerPositions());
  const orders = useApi(() => api.getPaperBrokerOrders(100));
  const candidates = useApi(() => api.getStrategyOptionsCandidates());
  const greeks = useApi(() => api.getPaperBrokerGreeks());
  const deltaHedge = useApi(() => api.getDeltaHedgePreview());
  const metaStatus = useApi(() => api.getOptionsMetaModelStatus());

  const [showResetModal, setShowResetModal] = useState(false);
  const [resetCash, setResetCash] = useState(100000);
  const [execStatus, setExecStatus] = useState<string | null>(null);
  const [showVolSurface, setShowVolSurface] = useState(false);
  const [showEarningsCrush, setShowEarningsCrush] = useState(false);
  const [showUnusualFlow, setShowUnusualFlow] = useState(false);
  const [showVolScanner, setShowVolScanner] = useState(false);
  const [showGammaScalper, setShowGammaScalper] = useState(false);
  const [showDispersion, setShowDispersion] = useState(false);
  const [showZeroDte, setShowZeroDte] = useState(false);
  const [showVpin, setShowVpin] = useState(false);
  const [showSor, setShowSor] = useState(false);
  const [showGex, setShowGex] = useState(false);
  const [showLob, setShowLob] = useState(false);
  const [showCopula, setShowCopula] = useState(false);
  const [showMarketMaker, setShowMarketMaker] = useState(false);

  // Position Roll state
  const [rollingPosition, setRollingPosition] = useState<{ symbol: string; qty: number } | null>(null);
  const [rollTargetExp, setRollTargetExp] = useState("2026-10-16");
  const [rollTargetStrike, setRollTargetStrike] = useState<number | undefined>(undefined);

  // Backtest state
  const [backtestStrategy, setBacktestStrategy] = useState("Put Credit Spread");
  const [backtestTicker, setBacktestTicker] = useState("SPY");
  const [backtestStart, setBacktestStart] = useState("2020-01-01");
  const [backtestEnd, setBacktestEnd] = useState("2024-01-01");
  const [backtestResult, setBacktestResult] = useState<import("../api/types").OptionsBacktestResponse | null>(null);

  const resetMutation = useMutation((cash: number) => api.resetPaperBroker(cash));
  const execMutation = useMutation(() => api.executeStrategyOptions());
  const settleMutation = useMutation(() => api.settleExpiredPaperOptions());
  const retrainMutation = useMutation(() => api.retrainOptionsMetaModel());
  const backtestMutation = useMutation((params: import("../api/types").OptionsBacktestParams) => api.runOptionsBacktest(params));
  const manageExitsMutation = useMutation((force?: boolean) => api.managePaperOptionsExits({ force }));
  const deltaHedgeMutation = useMutation(() => api.executeDeltaHedge());
  const rollMutation = useMutation((req: RollOrderRequest) => api.rollPaperOptionPosition(req));

  const handleReset = async () => {
    await resetMutation.run(resetCash);
    if (!resetMutation.error) {
      setShowResetModal(false);
      account.reload();
      positions.reload();
      orders.reload();
      candidates.reload();
      greeks.reload();
      deltaHedge.reload();
    }
  };

  const handleExecuteStrategyOptions = async () => {
    setExecStatus(null);
    const res = await execMutation.run();
    if (res) {
      setExecStatus(`Successfully executed ${res.executed_count} strategy trades (${res.skipped_count} skipped, ${res.failed_count} failed).`);
      account.reload();
      positions.reload();
      orders.reload();
      candidates.reload();
      greeks.reload();
      deltaHedge.reload();
    }
  };

  const handleSettleExpired = async () => {
    setExecStatus(null);
    const res = await settleMutation.run();
    if (res) {
      setExecStatus(`Settled ${res.settled_count} expired option contracts.`);
      account.reload();
      positions.reload();
      orders.reload();
      greeks.reload();
      deltaHedge.reload();
    }
  };

  const handleManageExits = async () => {
    setExecStatus(null);
    const res = await manageExitsMutation.run();
    if (res) {
      setExecStatus(res.message || `Evaluated ${res.evaluated_count} positions: closed ${res.closed_count}.`);
      account.reload();
      positions.reload();
      orders.reload();
      greeks.reload();
      deltaHedge.reload();
    }
  };

  const handleExecuteDeltaHedge = async () => {
    setExecStatus(null);
    const res = await deltaHedgeMutation.run();
    if (res) {
      setExecStatus(res.message || `Successfully executed delta hedge order: ${res.side} ${res.shares} ${res.symbol} @ $${res.price.toFixed(2)}.`);
      account.reload();
      positions.reload();
      orders.reload();
      greeks.reload();
      deltaHedge.reload();
    }
  };

  const handleRollSubmit = async () => {
    if (!rollingPosition) return;
    // rollingPosition.symbol is "{TICKER} {YYYY-MM-DD} ${STRIKE} {CALL|PUT}".
    // Parse it to build the close leg and the new-expiration open leg the
    // backend's RollOrderRequest actually requires (symbol/close_legs/open_legs)
    // -- defaulting the open leg's strike to the current strike when the
    // operator didn't pick a different one (a pure calendar roll).
    const parts = rollingPosition.symbol.trim().split(/\s+/);
    const ticker = parts[0];
    const optType = parts[parts.length - 1];
    const currentStrike = parseFloat((parts[2] || "").replace("$", ""));
    const targetStrike = rollTargetStrike ?? currentStrike;
    const qty = Math.abs(rollingPosition.qty);
    const isShort = rollingPosition.qty < 0;
    const newSymbol = `${ticker} ${rollTargetExp} $${targetStrike.toFixed(2)} ${optType}`;
    const res = await rollMutation.run({
      symbol: ticker,
      close_legs: [{ symbol: rollingPosition.symbol, side: isShort ? "buy" : "sell", qty }],
      open_legs: [{ symbol: newSymbol, side: isShort ? "sell" : "buy", qty }],
      contracts: qty,
    });
    if (res) {
      setRollingPosition(null);
      setExecStatus(res.message || `Successfully rolled ${rollingPosition.symbol} to ${rollTargetExp}.`);
      account.reload();
      positions.reload();
      orders.reload();
      greeks.reload();
      deltaHedge.reload();
    }
  };

  const handleRetrainMeta = async () => {
    const res = await retrainMutation.run();
    if (res) {
      setExecStatus(`Stage 4 ML Meta-Labeler retrained on ${res.trained_samples} trades (Accuracy: ${res.accuracy}%, ROC-AUC: ${res.roc_auc}).`);
      metaStatus.reload();
    }
  };

  const handleRunBacktest = async () => {
    const res = await backtestMutation.run({
      strategy: backtestStrategy,
      ticker: backtestTicker.trim().toUpperCase(),
      start_date: backtestStart,
      end_date: backtestEnd,
      initial_capital: 100000,
    });
    if (res) {
      setBacktestResult(res);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", width: "100%", overflow: "hidden" }}>
      <div style={{
        padding: "16px 24px",
        borderBottom: `1px solid ${theme.border}`,
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        flexShrink: 0
      }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 600 }}>Paper Broker</h1>
          <div style={{ fontSize: 12, color: theme.textSecondary, marginTop: 2 }}>
            Simulated Execution, Greeks Risk, Options Backtest & Stage 4 ML Engine
          </div>
        </div>
        <div style={{ display: "flex", gap: 12 }}>
          <button
            onClick={handleManageExits}
            disabled={manageExitsMutation.pending}
            style={{
              padding: "8px 14px",
              background: "rgba(245, 158, 11, 0.12)",
              border: "1px solid rgba(245, 158, 11, 0.4)",
              color: theme.caution,
              borderRadius: 4,
              cursor: manageExitsMutation.pending ? "not-allowed" : "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            {manageExitsMutation.pending ? "Evaluating Exits..." : "⚡ Manage Exits"}
          </button>
          <button
            onClick={() => setShowEarningsCrush(!showEarningsCrush)}
            style={{
              padding: "8px 14px",
              background: showEarningsCrush ? theme.growth : theme.surface,
              border: `1px solid ${showEarningsCrush ? theme.growth : theme.border}`,
              color: showEarningsCrush ? "#000" : theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            ⚡ Earnings Crush
          </button>
          <button
            onClick={() => setShowUnusualFlow(!showUnusualFlow)}
            style={{
              padding: "8px 14px",
              background: showUnusualFlow ? "#818cf8" : theme.surface,
              border: `1px solid ${showUnusualFlow ? "#818cf8" : theme.border}`,
              color: showUnusualFlow ? "#000" : theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            🌊 Unusual Flow
          </button>
          <button
            onClick={() => setShowVolScanner(!showVolScanner)}
            style={{
              padding: "8px 14px",
              background: showVolScanner ? "#f59e0b" : theme.surface,
              border: `1px solid ${showVolScanner ? "#f59e0b" : theme.border}`,
              color: showVolScanner ? "#000" : theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            🎯 Vol Scanner
          </button>
          <button
            onClick={() => setShowDispersion(!showDispersion)}
            style={{
              padding: "8px 14px",
              background: showDispersion ? theme.accent : theme.surface,
              border: `1px solid ${showDispersion ? theme.accent : theme.border}`,
              color: showDispersion ? "#000" : theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            🌐 Dispersion
          </button>
          <button
            onClick={() => setShowZeroDte(!showZeroDte)}
            style={{
              padding: "8px 14px",
              background: showZeroDte ? theme.growth : theme.surface,
              border: `1px solid ${showZeroDte ? theme.growth : theme.border}`,
              color: showZeroDte ? "#000" : theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            ⚡ 0DTE Desk
          </button>
          <button
            onClick={() => setShowVpin(!showVpin)}
            style={{
              padding: "8px 14px",
              background: showVpin ? theme.accent : theme.surface,
              border: `1px solid ${showVpin ? theme.accent : theme.border}`,
              color: showVpin ? "#000" : theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            ⏱ VPIN Toxicity
          </button>
          <button
            onClick={() => setShowSor(!showSor)}
            style={{
              padding: "8px 14px",
              background: showSor ? theme.growth : theme.surface,
              border: `1px solid ${showSor ? theme.growth : theme.border}`,
              color: showSor ? "#000" : theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            🔀 Smart Router
          </button>
          <button
            onClick={() => setShowGex(!showGex)}
            style={{
              padding: "8px 14px",
              background: showGex ? theme.accent : theme.surface,
              border: `1px solid ${showGex ? theme.accent : theme.border}`,
              color: showGex ? "#000" : theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            📊 GEX Profile
          </button>
          <button
            onClick={() => setShowLob(!showLob)}
            style={{
              padding: "8px 14px",
              background: showLob ? theme.growth : theme.surface,
              border: `1px solid ${showLob ? theme.growth : theme.border}`,
              color: showLob ? "#000" : theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            🪜 LOB Depth
          </button>
          <button
            onClick={() => setShowCopula(!showCopula)}
            style={{
              padding: "8px 14px",
              background: showCopula ? theme.accent : theme.surface,
              border: `1px solid ${showCopula ? theme.accent : theme.border}`,
              color: showCopula ? "#000" : theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            🔗 Copula Stat Arb
          </button>
          <button
            onClick={() => setShowMarketMaker(!showMarketMaker)}
            style={{
              padding: "8px 14px",
              background: showMarketMaker ? theme.growth : theme.surface,
              border: `1px solid ${showMarketMaker ? theme.growth : theme.border}`,
              color: showMarketMaker ? "#000" : theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            🤖 MM Agent Sim
          </button>
          <button
            onClick={() => setShowGammaScalper(!showGammaScalper)}
            style={{
              padding: "8px 14px",
              background: showGammaScalper ? theme.growth : theme.surface,
              border: `1px solid ${showGammaScalper ? theme.growth : theme.border}`,
              color: showGammaScalper ? "#000" : theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            ⚡ Gamma Scalper
          </button>
          <button
            onClick={() => setShowVolSurface(!showVolSurface)}
            style={{
              padding: "8px 14px",
              background: showVolSurface ? theme.accent : theme.surface,
              border: `1px solid ${showVolSurface ? theme.accent : theme.border}`,
              color: showVolSurface ? "#000" : theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 500,
              fontSize: 13,
            }}
          >
            🌊 Vol Surface
          </button>
          <button
            onClick={handleSettleExpired}
            disabled={settleMutation.pending}
            style={{
              padding: "8px 14px",
              background: theme.surface,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 4,
              cursor: settleMutation.pending ? "not-allowed" : "pointer",
              fontWeight: 500,
              fontSize: 13,
            }}
          >
            {settleMutation.pending ? "Settling..." : "⏱ Settle Expired Options"}
          </button>
          <button
            onClick={() => setShowResetModal(true)}
            style={{
              padding: "8px 14px",
              background: theme.surface,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 500,
              fontSize: 13,
            }}
          >
            Reset Paper Account
          </button>
        </div>
      </div>


      <div style={{ flex: 1, overflowY: "auto", padding: 24, display: "flex", flexDirection: "column", gap: 24 }}>
        <TabGuide tabKey="paper-broker" />

        {/* Dispersion Arbitrage Desk */}
        {showDispersion && (
          <DispersionScanner
            initialIndex="QQQ"
            onTradeExecuted={() => {
              account.reload();
              positions.reload();
              orders.reload();
              candidates.reload();
              greeks.reload();
              deltaHedge.reload();
            }}
            onClose={() => setShowDispersion(false)}
          />
        )}

        {/* 0DTE Momentum Breakout Desk */}
        {showZeroDte && (
          <ZeroDteDesk
            initialSymbol="SPY"
            onTradeExecuted={() => {
              account.reload();
              positions.reload();
              orders.reload();
              candidates.reload();
              greeks.reload();
              deltaHedge.reload();
            }}
            onClose={() => setShowZeroDte(false)}
          />
        )}

        {/* VPIN Toxicity Meter Desk */}
        {showVpin && (
          <VpinGauge
            initialSymbol="SPY"
            onClose={() => setShowVpin(false)}
          />
        )}

        {/* Multi-Leg Smart Order Router (SOR) & Legging Desk */}
        {showSor && (
          <SmartOrderRouterView
            initialSymbol="SPY"
            spotPrice={account.data?.equity ? 546.50 : 546.50}
            onClose={() => setShowSor(false)}
          />
        )}

        {/* Options Gamma Exposure (GEX) Profile Desk */}
        {showGex && (
          <GexProfileView
            initialSymbol="SPY"
            spotPrice={account.data?.equity ? 546.50 : 546.50}
            onClose={() => setShowGex(false)}
          />
        )}

        {/* Level-3 Limit Order Book (LOB) Depth Simulator Desk */}
        {showLob && (
          <LobDepthView
            initialSymbol="SPY"
            spotPrice={account.data?.equity ? 546.50 : 546.50}
            onClose={() => setShowLob(false)}
          />
        )}

        {/* Copula Statistical Arbitrage & Dynamic Kalman Beta Desk */}
        {showCopula && (
          <CopulaSpreadView
            initialPair="SPY/QQQ"
            onClose={() => setShowCopula(false)}
          />
        )}

        {/* Avellaneda-Stoikov DRL Market Maker Agent Desk */}
        {showMarketMaker && (
          <MarketMakerAgentView
            initialSymbol="SPY"
            spotPrice={account.data?.equity ? 546.50 : 546.50}
            onClose={() => setShowMarketMaker(false)}
          />
        )}

        {/* Volatility Forecast & Strike Mispricing Scanner */}
        {showVolScanner && (
          <VolForecastScanner initialSymbol="SPY" onClose={() => setShowVolScanner(false)} />
        )}

        {/* Gamma Scalping Simulator */}
        {showGammaScalper && (
          <GammaScalperView initialSymbol="SPY" spotPrice={account.data?.equity ? 505.20 : 505.20} onClose={() => setShowGammaScalper(false)} />
        )}

        {/* Volatility Surface Drawer/Section */}
        {showVolSurface && (
          <VolSurfaceView initialSymbol="SPY" onClose={() => setShowVolSurface(false)} />
        )}

        {/* Earnings Crush Scanner Drawer/Section */}
        {showEarningsCrush && (
          <EarningsCrushScanner
            onTradeExecuted={() => {
              account.reload();
              positions.reload();
              orders.reload();
              candidates.reload();
              greeks.reload();
              deltaHedge.reload();
            }}
            onClose={() => setShowEarningsCrush(false)}
          />
        )}

        {/* Unusual Options Flow Feed Drawer/Section */}
        {showUnusualFlow && (
          <UnusualFlowFeed onClose={() => setShowUnusualFlow(false)} />
        )}
        
        {account.data && (
          <div style={{ display: "flex", gap: 16 }}>
            <div style={{ flex: 1, padding: 16, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
              <div style={{ color: theme.textSecondary, fontSize: 13, marginBottom: 4 }}>Equity</div>
              <div style={{ fontSize: 24, fontWeight: 600 }}>${account.data.equity.toLocaleString("en-US", { minimumFractionDigits: 2 })}</div>
            </div>
            <div style={{ flex: 1, padding: 16, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
              <div style={{ color: theme.textSecondary, fontSize: 13, marginBottom: 4 }}>Cash</div>
              <div style={{ fontSize: 24, fontWeight: 600 }}>${account.data.cash.toLocaleString("en-US", { minimumFractionDigits: 2 })}</div>
            </div>
            <div style={{ flex: 1, padding: 16, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
              <div style={{ color: theme.textSecondary, fontSize: 13, marginBottom: 4 }}>Buying Power</div>
              <div style={{ fontSize: 24, fontWeight: 600 }}>${account.data.buying_power.toLocaleString("en-US", { minimumFractionDigits: 2 })}</div>
            </div>
          </div>
        )}

        {/* Portfolio Greeks Risk Exposure Row */}
        {greeks.data && (
          <div>
            <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12, color: theme.textPrimary }}>
              📊 Portfolio Risk & Aggregate Greeks
            </h2>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
              <div style={{ padding: 14, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
                <div style={{ color: theme.textSecondary, fontSize: 12, marginBottom: 4 }}>Net Delta (Δ)</div>
                <div style={{ fontSize: 18, fontWeight: 600, color: greeks.data.net_delta_shares >= 0 ? theme.growth : theme.decline }}>
                  {greeks.data.net_delta_shares > 0 ? "+" : ""}{greeks.data.net_delta_shares.toFixed(1)} sh
                </div>
                <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>
                  ${greeks.data.net_dollar_delta.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })} notional
                </div>
              </div>

              <div style={{ padding: 14, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
                <div style={{ color: theme.textSecondary, fontSize: 12, marginBottom: 4 }}>Net Gamma (Γ)</div>
                <div style={{ fontSize: 18, fontWeight: 600 }}>
                  {greeks.data.net_gamma.toFixed(4)}
                </div>
                <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>Δ acceleration / $1 move</div>
              </div>

              <div style={{ padding: 14, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
                <div style={{ color: theme.textSecondary, fontSize: 12, marginBottom: 4 }}>Daily Theta (Θ)</div>
                <div style={{ fontSize: 18, fontWeight: 600, color: greeks.data.net_theta_daily >= 0 ? theme.growth : theme.decline }}>
                  {greeks.data.net_theta_daily >= 0 ? "+" : ""}${greeks.data.net_theta_daily.toFixed(2)}/day
                </div>
                <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>Time decay income/cost</div>
              </div>

              <div style={{ padding: 14, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
                <div style={{ color: theme.textSecondary, fontSize: 12, marginBottom: 4 }}>Net Vega (𝒱)</div>
                <div style={{ fontSize: 18, fontWeight: 600, color: greeks.data.net_vega_1pct >= 0 ? theme.growth : theme.decline }}>
                  {greeks.data.net_vega_1pct >= 0 ? "+" : ""}${greeks.data.net_vega_1pct.toFixed(2)}
                </div>
                <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>P&L per +1.0% IV move</div>
              </div>

              <div style={{ padding: 14, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
                <div style={{ color: theme.textSecondary, fontSize: 12, marginBottom: 4 }}>SPY β-Weighted Delta</div>
                <div style={{ fontSize: 18, fontWeight: 600 }}>
                  {greeks.data.beta_weighted_delta_spy > 0 ? "+" : ""}{greeks.data.beta_weighted_delta_spy.toFixed(1)} SPY sh
                </div>
                <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>Market-equivalent exposure</div>
              </div>
            </div>

            {greeks.data.positions_with_missing_data && greeks.data.positions_with_missing_data.length > 0 && (
              <div style={{
                marginTop: 12,
                padding: "8px 12px",
                background: "rgba(234, 179, 8, 0.12)",
                border: "1px solid rgba(234, 179, 8, 0.3)",
                borderRadius: 6,
                color: "#eab308",
                fontSize: 12,
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}>
                <span>⚠️ Incomplete Greeks Read:</span>
                <span>
                  {greeks.data.positions_with_missing_data.length} position(s) excluded from totals due to missing live quote ({greeks.data.positions_with_missing_data.join(", ")}).
                </span>
              </div>
            )}

            {/* Dynamic Delta Hedge Risk Neutralization Card */}
            {deltaHedge.data && (
              <div
                style={{
                  marginTop: 12,
                  padding: 16,
                  background: theme.surface,
                  borderRadius: 8,
                  border: `1px solid ${deltaHedge.data.is_within_tolerance ? theme.border : theme.caution}`,
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  flexWrap: "wrap",
                  gap: 12,
                }}
              >
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontSize: 14, fontWeight: 600 }}>⚖️ Dynamic Delta Hedging (SPY β-Weighted)</span>
                    <span
                      style={{
                        fontSize: 11,
                        padding: "2px 8px",
                        borderRadius: 4,
                        fontWeight: 600,
                        background: deltaHedge.data.is_within_tolerance
                          ? "rgba(16, 185, 129, 0.15)"
                          : "rgba(245, 158, 11, 0.15)",
                        color: deltaHedge.data.is_within_tolerance ? theme.growth : theme.caution,
                      }}
                    >
                      {deltaHedge.data.is_within_tolerance ? "✓ Within Tolerance (±25 sh)" : "⚠ Rebalance Required"}
                    </span>
                  </div>
                  <div style={{ fontSize: 12, color: theme.textSecondary, marginTop: 4 }}>
                    Net Exposure: {deltaHedge.data.beta_weighted_delta_spy > 0 ? "+" : ""}{deltaHedge.data.beta_weighted_delta_spy.toFixed(1)} SPY sh (${deltaHedge.data.net_dollar_delta.toLocaleString()} notional).
                    {deltaHedge.data.action !== "NONE" ? (
                      <span style={{ marginLeft: 6, color: theme.accent, fontWeight: 500 }}>
                        Target Order: {deltaHedge.data.action} {Math.abs(deltaHedge.data.required_hedge_shares)} SPY @ ~${deltaHedge.data.spy_spot_price.toFixed(2)} (Est. Cost: ${deltaHedge.data.estimated_cost.toLocaleString()})
                      </span>
                    ) : (
                      <span style={{ marginLeft: 6, color: theme.textMuted }}> Portfolio is delta-neutral within tolerance band.</span>
                    )}
                  </div>
                </div>

                <button
                  onClick={handleExecuteDeltaHedge}
                  disabled={deltaHedgeMutation.pending || deltaHedge.data.action === "NONE"}
                  style={{
                    padding: "8px 16px",
                    background: deltaHedge.data.action !== "NONE" ? theme.accent : theme.surface3,
                    border: "none",
                    color: deltaHedge.data.action !== "NONE" ? "#000" : theme.textMuted,
                    borderRadius: 4,
                    cursor: (deltaHedgeMutation.pending || deltaHedge.data.action === "NONE") ? "not-allowed" : "pointer",
                    fontWeight: 600,
                    fontSize: 13,
                  }}
                >
                  {deltaHedgeMutation.pending
                    ? "Hedging..."
                    : deltaHedge.data.action !== "NONE"
                    ? `Execute Delta Hedge (${deltaHedge.data.action} ${Math.abs(deltaHedge.data.required_hedge_shares)} SPY)`
                    : "Portfolio Delta Neutral"}
                </button>
              </div>
            )}
          </div>
        )}


        {/* Automated Strategy Options Section */}
        <div style={{
          padding: 20,
          background: theme.surface,
          borderRadius: 8,
          border: `1px solid ${theme.border}`,
          display: "flex",
          flexDirection: "column",
          gap: 16
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <h2 style={{ fontSize: 18, fontWeight: 600, margin: "0 0 4px 0" }}>⚡ Automated Strategy Options Execution</h2>
              <div style={{ color: theme.textSecondary, fontSize: 13 }}>
                Quantitative options directives from technical_options_engine passing VRP and regime gates ready for auto paper trading.
              </div>
            </div>
            <button
              onClick={handleExecuteStrategyOptions}
              disabled={execMutation.pending || !candidates.data?.candidates?.length}
              style={{
                padding: "8px 16px",
                background: theme.accent,
                border: "none",
                color: theme.base,
                borderRadius: 4,
                cursor: (execMutation.pending || !candidates.data?.candidates?.length) ? "not-allowed" : "pointer",
                fontWeight: 600,
                opacity: (!candidates.data?.candidates?.length || execMutation.pending) ? 0.6 : 1
              }}
            >
              {execMutation.pending ? "Executing..." : `Execute ${candidates.data?.candidates?.length || 0} Strategy Trades`}
            </button>
          </div>

          {execStatus && (
            <div style={{
              padding: "10px 14px",
              background: "rgba(16, 185, 129, 0.15)",
              color: theme.growth,
              borderRadius: 6,
              fontSize: 13,
              fontWeight: 500
            }}>
              {execStatus}
            </div>
          )}

          {execMutation.error && (
            <div style={{
              padding: "10px 14px",
              background: "rgba(239, 68, 68, 0.15)",
              color: theme.decline,
              borderRadius: 6,
              fontSize: 13,
              fontWeight: 500
            }}>
              {execMutation.error}
            </div>
          )}

          {candidates.data?.candidates && candidates.data.candidates.length > 0 ? (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                    <th style={{ padding: "8px 12px", color: theme.textSecondary, fontWeight: 600 }}>Symbol</th>
                    <th style={{ padding: "8px 12px", color: theme.textSecondary, fontWeight: 600 }}>Strategy</th>
                    <th style={{ padding: "8px 12px", color: theme.textSecondary, fontWeight: 600 }}>Action</th>
                    <th style={{ padding: "8px 12px", color: theme.textSecondary, fontWeight: 600, textAlign: "right" }}>Net Premium</th>
                    <th style={{ padding: "8px 12px", color: theme.textSecondary, fontWeight: 600, textAlign: "right" }}>IVR</th>
                    <th style={{ padding: "8px 12px", color: theme.textSecondary, fontWeight: 600 }}>Trend Bias</th>
                    <th style={{ padding: "8px 12px", color: theme.textSecondary, fontWeight: 600 }}>Target DTE</th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.data.candidates.map((c, idx) => (
                    <tr key={idx} style={{ borderBottom: `1px solid ${theme.border}` }}>
                      <td style={{ padding: "8px 12px", fontWeight: 600 }}>{c.symbol}</td>
                      <td style={{ padding: "8px 12px" }}>{c.strategy}</td>
                      <td style={{ padding: "8px 12px" }}>
                        <span style={{
                          padding: "2px 6px",
                          borderRadius: 4,
                          fontSize: 11,
                          fontWeight: 600,
                          background: c.action.toLowerCase() === "open" ? "rgba(16, 185, 129, 0.15)" : "rgba(239, 68, 68, 0.15)",
                          color: c.action.toLowerCase() === "open" ? theme.growth : theme.decline
                        }}>
                          {c.action.toUpperCase()}
                        </span>
                      </td>
                      <td style={{ padding: "8px 12px", textAlign: "right" }}>
                        {c.net_premium != null ? `$${c.net_premium.toFixed(2)}` : "—"}
                      </td>
                      <td style={{ padding: "8px 12px", textAlign: "right" }}>
                        {c.ivr != null ? `${c.ivr.toFixed(1)}%` : "—"}
                      </td>
                      <td style={{ padding: "8px 12px" }}>{c.trend_bias}</td>
                      <td style={{ padding: "8px 12px" }}>{c.target_dte}d</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div style={{ color: theme.textSecondary, fontSize: 13, fontStyle: "italic" }}>
              No strategy options directives currently meet VRP / regime gates.
            </div>
          )}
        </div>


        <div>
          <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 12 }}>Positions</h2>
          <div style={{ background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}`, overflow: "hidden" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Symbol</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Qty</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Avg Cost</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Current Price</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Market Value</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Delta (Δ)</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Theta (Θ/d)</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Vega (𝒱/1%)</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Unrealized P&L</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {positions.data?.length === 0 && (
                  <tr>
                    <td colSpan={10} style={{ padding: 24, textAlign: "center", color: theme.textSecondary }}>No open positions</td>
                  </tr>
                )}
                {positions.data?.map(p => {
                  const isOption = p.symbol.includes(" ") && p.symbol.includes("$");
                  const isShort = p.qty < 0;
                  const posGreek = greeks.data?.positions?.find(g => g.symbol === p.symbol);

                  return (
                    <tr key={p.symbol} style={{ borderBottom: `1px solid ${theme.border}` }}>
                      <td style={{ padding: "12px 16px", fontWeight: 500 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span>{p.symbol}</span>
                          {isOption && (
                            <span style={{
                              fontSize: 10,
                              fontWeight: 600,
                              padding: "2px 6px",
                              borderRadius: 4,
                              background: "rgba(99, 102, 241, 0.15)",
                              color: "#818cf8",
                              letterSpacing: 0.5
                            }}>
                              OPTION
                            </span>
                          )}
                          {isShort && (
                            <span style={{
                              fontSize: 10,
                              fontWeight: 600,
                              padding: "2px 6px",
                              borderRadius: 4,
                              background: "rgba(239, 68, 68, 0.15)",
                              color: theme.decline,
                              letterSpacing: 0.5
                            }}>
                              SHORT
                            </span>
                          )}
                        </div>
                      </td>
                      <td style={{ padding: "12px 16px", textAlign: "right", fontWeight: isShort ? 600 : 400, color: isShort ? theme.decline : theme.textPrimary }}>
                        {p.qty}
                      </td>
                      <td style={{ padding: "12px 16px", textAlign: "right" }}>${p.avg_cost.toFixed(2)}</td>
                      <td style={{ padding: "12px 16px", textAlign: "right" }}>{p.current_price ? `$${p.current_price.toFixed(2)}` : "—"}</td>
                      <td style={{ padding: "12px 16px", textAlign: "right" }}>{p.market_value ? `$${p.market_value.toFixed(2)}` : "—"}</td>
                      <td style={{ padding: "12px 16px", textAlign: "right", color: (posGreek?.position_delta ?? 0) >= 0 ? theme.growth : theme.decline }}>
                        {posGreek ? `${posGreek.position_delta > 0 ? "+" : ""}${posGreek.position_delta.toFixed(1)}` : "—"}
                      </td>
                      <td style={{ padding: "12px 16px", textAlign: "right", color: (posGreek?.position_theta_daily ?? 0) >= 0 ? theme.growth : theme.decline }}>
                        {posGreek ? `${posGreek.position_theta_daily >= 0 ? "+" : ""}$${posGreek.position_theta_daily.toFixed(2)}` : "—"}
                      </td>
                      <td style={{ padding: "12px 16px", textAlign: "right", color: (posGreek?.position_vega_1pct ?? 0) >= 0 ? theme.growth : theme.decline }}>
                        {posGreek ? `${posGreek.position_vega_1pct >= 0 ? "+" : ""}$${posGreek.position_vega_1pct.toFixed(2)}` : "—"}
                      </td>
                      <td style={{ padding: "12px 16px", textAlign: "right", color: p.unrealized_pl && p.unrealized_pl >= 0 ? theme.growth : theme.decline }}>
                        {p.unrealized_pl ? `$${p.unrealized_pl.toFixed(2)}` : "—"}
                        {p.unrealized_pl_pct != null && ` (${(p.unrealized_pl_pct * 100).toFixed(2)}%)`}
                      </td>
                      <td style={{ padding: "12px 16px", textAlign: "right" }}>
                        {isOption ? (
                          <button
                            onClick={() => {
                              setRollingPosition({ symbol: p.symbol, qty: p.qty });
                              setRollTargetExp("2026-10-16");
                              setRollTargetStrike(undefined);
                            }}
                            style={{
                              padding: "4px 8px",
                              background: theme.surface2,
                              border: `1px solid ${theme.border}`,
                              color: theme.accent,
                              borderRadius: 4,
                              cursor: "pointer",
                              fontSize: 11,
                              fontWeight: 600,
                            }}
                          >
                            🔄 Roll
                          </button>
                        ) : (
                          <span style={{ color: theme.textMuted }}>—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* 2D Scenario Matrix & Stress Testing Grid */}
        <ScenarioHeatmap onRefresh={() => { positions.reload(); greeks.reload(); deltaHedge.reload(); }} />

        <div>

          <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 12 }}>Orders (Last 100)</h2>
          <div style={{ background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}`, overflow: "hidden" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Date</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Symbol</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Side</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Qty</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Price</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Status</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Filled</th>
                </tr>
              </thead>
              <tbody>
                {orders.data?.length === 0 && (
                  <tr>
                    <td colSpan={7} style={{ padding: 24, textAlign: "center", color: theme.textSecondary }}>No recent orders</td>
                  </tr>
                )}
                {orders.data?.map(o => (
                  <tr key={o.order_id} style={{ borderBottom: `1px solid ${theme.border}` }}>
                    <td style={{ padding: "12px 16px", whiteSpace: "nowrap" }}>{new Date(o.created_at).toLocaleString()}</td>
                    <td style={{ padding: "12px 16px", fontWeight: 500 }}>{o.symbol}</td>
                    <td style={{ padding: "12px 16px", color: o.side === "BUY" ? theme.growth : theme.decline }}>{o.side}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>{o.qty}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>${o.price.toFixed(2)}</td>
                    <td style={{ padding: "12px 16px" }}>{o.status}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>
                      {o.filled_qty} {o.filled_avg_price ? ` @ $${o.filled_avg_price.toFixed(2)}` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Stage 4 ML Meta-Labeler Status & Retraining */}
        <div style={{ background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}`, padding: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>🧠 Stage 4 ML Options Meta-Labeler</h2>
              <div style={{ fontSize: 13, color: theme.textSecondary, marginTop: 4 }}>
                Predicts probability of profit P(Win) &amp; applies dynamic contract sizing multipliers [0.30, 1.50]
              </div>
            </div>
            <button
              onClick={handleRetrainMeta}
              disabled={retrainMutation.pending}
              style={{
                padding: "8px 16px",
                background: theme.accent,
                border: "none",
                color: "#000",
                borderRadius: 4,
                cursor: retrainMutation.pending ? "not-allowed" : "pointer",
                fontWeight: 600,
                fontSize: 13,
              }}
            >
              {retrainMutation.pending ? "Training Classifier..." : "⚡ Retrain Meta-Model"}
            </button>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16 }}>
            <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
              <div style={{ fontSize: 12, color: theme.textSecondary }}>Training Samples</div>
              <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4 }}>
                {metaStatus.data?.n_samples.toLocaleString() ?? "—"}
              </div>
            </div>
            <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
              <div style={{ fontSize: 12, color: theme.textSecondary }}>Model Accuracy</div>
              <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4, color: theme.growth }}>
                {metaStatus.data?.train_accuracy != null ? `${metaStatus.data.train_accuracy}%` : "—"}
              </div>
            </div>
            <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
              <div style={{ fontSize: 12, color: theme.textSecondary }}>ROC-AUC Score</div>
              <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4 }}>
                {metaStatus.data?.train_roc_auc != null ? metaStatus.data.train_roc_auc.toFixed(3) : "—"}
              </div>
            </div>
            <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
              <div style={{ fontSize: 12, color: theme.textSecondary }}>Last Retrained</div>
              <div style={{ fontSize: 13, fontWeight: 500, marginTop: 6, color: theme.textSecondary }}>
                {metaStatus.data?.trained_at ? new Date(metaStatus.data.trained_at).toLocaleDateString() : "Active"}
              </div>
            </div>
          </div>
        </div>

        {/* Interactive Options Strategy Backtesting Harness */}
        <div style={{ background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}`, padding: 20 }}>
          <div style={{ marginBottom: 16 }}>
            <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>🔬 Interactive Options Strategy Backtest Harness</h2>
            <div style={{ fontSize: 13, color: theme.textSecondary, marginTop: 4 }}>
              Simulate multi-leg option strategies with daily Black-Scholes mark-to-market pricing, profit-taking, stop-losses, and historical stress tests.
            </div>
          </div>

          <div style={{ display: "flex", gap: 12, alignItems: "flex-end", marginBottom: 20, flexWrap: "wrap" }}>
            <div style={{ flex: 2, minWidth: 180 }}>
              <label style={{ display: "block", fontSize: 12, color: theme.textSecondary, marginBottom: 4 }}>Strategy</label>
              <select
                value={backtestStrategy}
                onChange={e => setBacktestStrategy(e.target.value)}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  background: theme.base,
                  border: `1px solid ${theme.border}`,
                  color: theme.textPrimary,
                  borderRadius: 4,
                  fontSize: 13,
                }}
              >
                <option value="Put Credit Spread">Put Credit Spread (Bullish / Neutral)</option>
                <option value="Call Credit Spread">Call Credit Spread (Bearish / Neutral)</option>
                <option value="Iron Condor">Iron Condor (Range-Bound / Neutral)</option>
                <option value="Bull Call Spread">Bull Call Spread (Directional Debit)</option>
                <option value="Bear Put Spread">Bear Put Spread (Directional Debit)</option>
                <option value="Long Straddle">Long Straddle (High Volatility Expansion)</option>
              </select>
            </div>
            <div style={{ flex: 1, minWidth: 90 }}>
              <label style={{ display: "block", fontSize: 12, color: theme.textSecondary, marginBottom: 4 }}>Ticker</label>
              <input
                type="text"
                value={backtestTicker}
                onChange={e => setBacktestTicker(e.target.value)}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  background: theme.base,
                  border: `1px solid ${theme.border}`,
                  color: theme.textPrimary,
                  borderRadius: 4,
                  fontSize: 13,
                }}
              />
            </div>
            <div style={{ flex: 1, minWidth: 120 }}>
              <label style={{ display: "block", fontSize: 12, color: theme.textSecondary, marginBottom: 4 }}>Start Date</label>
              <input
                type="date"
                value={backtestStart}
                onChange={e => setBacktestStart(e.target.value)}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  background: theme.base,
                  border: `1px solid ${theme.border}`,
                  color: theme.textPrimary,
                  borderRadius: 4,
                  fontSize: 13,
                }}
              />
            </div>
            <div style={{ flex: 1, minWidth: 120 }}>
              <label style={{ display: "block", fontSize: 12, color: theme.textSecondary, marginBottom: 4 }}>End Date</label>
              <input
                type="date"
                value={backtestEnd}
                onChange={e => setBacktestEnd(e.target.value)}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  background: theme.base,
                  border: `1px solid ${theme.border}`,
                  color: theme.textPrimary,
                  borderRadius: 4,
                  fontSize: 13,
                }}
              />
            </div>
            <button
              onClick={handleRunBacktest}
              disabled={backtestMutation.pending}
              style={{
                padding: "8px 20px",
                background: theme.accent,
                border: "none",
                color: "#000",
                borderRadius: 4,
                cursor: backtestMutation.pending ? "not-allowed" : "pointer",
                fontWeight: 600,
                fontSize: 13,
                height: 35,
              }}
            >
              {backtestMutation.pending ? "Simulating..." : "▶ Run Backtest"}
            </button>
          </div>

          {backtestResult && (
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 12 }}>
                <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
                  <div style={{ fontSize: 11, color: theme.textSecondary }}>Total Return</div>
                  <div style={{ fontSize: 16, fontWeight: 600, color: backtestResult.total_return_pct >= 0 ? theme.growth : theme.decline, marginTop: 2 }}>
                    {backtestResult.total_return_pct > 0 ? "+" : ""}{backtestResult.total_return_pct.toFixed(2)}%
                  </div>
                </div>
                <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
                  <div style={{ fontSize: 11, color: theme.textSecondary }}>Sharpe Ratio</div>
                  <div style={{ fontSize: 16, fontWeight: 600, marginTop: 2 }}>
                    {backtestResult.sharpe_ratio.toFixed(2)}
                  </div>
                </div>
                <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
                  <div style={{ fontSize: 11, color: theme.textSecondary }}>Sortino Ratio</div>
                  <div style={{ fontSize: 16, fontWeight: 600, marginTop: 2 }}>
                    {backtestResult.sortino_ratio.toFixed(2)}
                  </div>
                </div>
                <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
                  <div style={{ fontSize: 11, color: theme.textSecondary }}>Max Drawdown</div>
                  <div style={{ fontSize: 16, fontWeight: 600, color: theme.decline, marginTop: 2 }}>
                    -{backtestResult.max_drawdown_pct.toFixed(2)}%
                  </div>
                </div>
                <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
                  <div style={{ fontSize: 11, color: theme.textSecondary }}>Win Rate</div>
                  <div style={{ fontSize: 16, fontWeight: 600, marginTop: 2 }}>
                    {backtestResult.win_rate_pct.toFixed(1)}% ({backtestResult.winning_trades}/{backtestResult.total_trades})
                  </div>
                </div>
                <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
                  <div style={{ fontSize: 11, color: theme.textSecondary }}>Profit Factor</div>
                  <div style={{ fontSize: 16, fontWeight: 600, marginTop: 2 }}>
                    {backtestResult.profit_factor.toFixed(2)}
                  </div>
                </div>
              </div>

              {/* Stress Gate and Deployability status */}
              <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                <span style={{
                  padding: "4px 8px",
                  borderRadius: 4,
                  fontSize: 12,
                  fontWeight: 600,
                  background: backtestResult.passes_stress ? "rgba(34, 197, 94, 0.15)" : "rgba(239, 68, 68, 0.15)",
                  color: backtestResult.passes_stress ? theme.growth : theme.decline,
                }}>
                  {backtestResult.passes_stress ? "✓ Passed Tail Stress Gate (Lehman, Volmageddon, COVID, Yen)" : "✗ Failed Stress Gate"}
                </span>
                <span style={{
                  padding: "4px 8px",
                  borderRadius: 4,
                  fontSize: 12,
                  fontWeight: 600,
                  background: backtestResult.deployable ? "rgba(34, 197, 94, 0.15)" : "rgba(239, 68, 68, 0.15)",
                  color: backtestResult.deployable ? theme.growth : theme.decline,
                }}>
                  {backtestResult.deployable ? "✓ Strategy Deployable" : "✗ Gated (Non-Deployable)"}
                </span>
                <span style={{ fontSize: 12, color: theme.textSecondary, marginLeft: "auto" }}>
                  PBO: {backtestResult.pbo.toFixed(2)} | DSR: {backtestResult.dsr.toFixed(2)}
                </span>
              </div>
            </div>
          )}
        </div>
      </div>


      {showResetModal && (
        <Modal ariaLabel="Reset Paper Broker" onClose={() => setShowResetModal(false)}>
          <div style={{ padding: 24 }}>
            <h2 style={{ margin: "0 0 16px 0" }}>Reset Paper Broker</h2>
            <p style={{ margin: "0 0 16px 0", color: theme.textSecondary, lineHeight: 1.5 }}>
              This will wipe all paper positions and orders and reset your account to the specified starting cash.
            </p>
            <div style={{ marginBottom: 24 }}>
              <label style={{ display: "block", marginBottom: 8, fontSize: 14, fontWeight: 500 }}>Starting Cash</label>
              <input 
                type="number" 
                value={resetCash} 
                onChange={e => setResetCash(Number(e.target.value))}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  background: theme.base,
                  border: `1px solid ${theme.border}`,
                  color: theme.textPrimary,
                  borderRadius: 4
                }}
              />
            </div>
            {resetMutation.error && (
              <div style={{ padding: 12, background: "rgba(239, 68, 68, 0.1)", color: theme.decline, borderRadius: 4, marginBottom: 16 }}>
                {resetMutation.error}
              </div>
            )}
            <div style={{ display: "flex", gap: 12, justifyContent: "flex-end" }}>
              <button 
                onClick={() => setShowResetModal(false)}
                style={{
                  padding: "8px 16px",
                  background: "transparent",
                  border: "none",
                  color: theme.textSecondary,
                  cursor: "pointer",
                  fontWeight: 500
                }}
              >
                Cancel
              </button>
              <button 
                onClick={handleReset}
                disabled={resetMutation.pending}
                style={{
                  padding: "8px 16px",
                  background: theme.decline,
                  border: "none",
                  color: "#fff",
                  borderRadius: 4,
                  cursor: resetMutation.pending ? "not-allowed" : "pointer",
                  fontWeight: 500
                }}
              >
                {resetMutation.pending ? "Resetting..." : "Reset"}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {rollingPosition && (
        <Modal ariaLabel="Roll Option Position" onClose={() => setRollingPosition(null)}>
          <div style={{ padding: 24 }}>
            <h2 style={{ margin: "0 0 8px 0", fontSize: 18, fontWeight: 600 }}>Roll Option Position</h2>
            <div style={{ color: theme.textSecondary, fontSize: 13, marginBottom: 16 }}>
              Atomically close <strong>{rollingPosition.symbol}</strong> ({rollingPosition.qty > 0 ? "LONG" : "SHORT"} {Math.abs(rollingPosition.qty)}x) and open replacement contracts in the next expiration cycle.
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 14, marginBottom: 20 }}>
              <div>
                <label style={{ display: "block", marginBottom: 6, fontSize: 13, fontWeight: 500 }}>
                  Target Expiration Date
                </label>
                <input
                  type="date"
                  value={rollTargetExp}
                  onChange={(e) => setRollTargetExp(e.target.value)}
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    background: theme.base,
                    border: `1px solid ${theme.border}`,
                    color: theme.textPrimary,
                    borderRadius: 4,
                    fontSize: 13,
                  }}
                />
              </div>

              <div>
                <label style={{ display: "block", marginBottom: 6, fontSize: 13, fontWeight: 500 }}>
                  Target Strike (Optional, leave blank to maintain strike)
                </label>
                <input
                  type="number"
                  value={rollTargetStrike ?? ""}
                  onChange={(e) => setRollTargetStrike(e.target.value ? Number(e.target.value) : undefined)}
                  placeholder="e.g. 500"
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    background: theme.base,
                    border: `1px solid ${theme.border}`,
                    color: theme.textPrimary,
                    borderRadius: 4,
                    fontSize: 13,
                  }}
                />
              </div>
            </div>

            {rollMutation.error && (
              <div
                style={{
                  padding: 12,
                  background: "rgba(239, 68, 68, 0.15)",
                  color: theme.decline,
                  borderRadius: 4,
                  marginBottom: 16,
                  fontSize: 13,
                }}
              >
                {rollMutation.error}
              </div>
            )}

            <div style={{ display: "flex", gap: 12, justifyContent: "flex-end" }}>
              <button
                onClick={() => setRollingPosition(null)}
                style={{
                  padding: "8px 16px",
                  background: "transparent",
                  border: "none",
                  color: theme.textSecondary,
                  cursor: "pointer",
                  fontWeight: 500,
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleRollSubmit}
                disabled={rollMutation.pending}
                style={{
                  padding: "8px 16px",
                  background: theme.accent,
                  border: "none",
                  color: "#000",
                  borderRadius: 4,
                  cursor: rollMutation.pending ? "not-allowed" : "pointer",
                  fontWeight: 600,
                  fontSize: 13,
                }}
              >
                {rollMutation.pending ? "Rolling Position..." : "Confirm & Execute Roll"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
