/**
 * RetrospectiveM4Adversarial.test.tsx — Adversarial Frontend Stress Suite for Milestone 4 (WP-H)
 * ============================================================================================
 * Authoritative Requirements:
 * - .agents/ORIGINAL_REQUEST.md (§ R6, WP-H)
 * - .agents/PROJECT.md (§ 4 Interface Contracts, Feature #21)
 *
 * Verifies:
 * 1. WP-H worst-case combined state:
 *    - Manual trade
 *    - Pre-feature uncaptured snapshot
 *    - Failed bridge with error telemetry
 *    - Degenerate entry price ($0.00)
 *    - Null realized PnL pct and null holding period
 * 2. Absolute absence of "NaN", "undefined", or raw "[object Object]" leakages.
 * 3. Exact honest fallback strings rendered:
 *    - "Snapshot: Not captured"
 *    - "Snapshot not captured — trade predates retrospective logging."
 *    - "Manual Entry"
 *    - "Bridge: FAILED"
 *    - "Bridge Failure Telemetry"
 *    - "Evaluation data unavailable" (for MAE & MFE)
 *    - "—" (for Edge Ratio, Slippage, PnL %)
 * 4. Error and loading resilience without unhandled React crashes.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { RetrospectiveDetailModal } from "./RetrospectiveDetailModal";
import type {
  RetrospectiveTradeRecord,
  BridgeReliabilityResponse,
  PaperBrokerClosedTrade,
  BatchRetrospectiveInsightsResponse,
} from "../api/types";
import { api } from "../api/client";

// Mock useMediaQuery to force desktop modal sheet
vi.mock("../hooks/useMediaQuery", () => ({
  useMediaQuery: vi.fn().mockReturnValue(false),
}));

describe("Milestone 4 Adversarial UI State Testing (WP-H)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  // --------------------------------------------------------------------------
  // 1. WP-H Worst-Case Combined State
  // --------------------------------------------------------------------------
  describe("Worst-Case Combined Failure State (WP-H)", () => {
    const worstCaseTrade: RetrospectiveTradeRecord = {
      trade_id: 9999,
      symbol: "TEST_BAD",
      strategy_id: null,
      pilot_id: null,
      side: "BUY",
      qty: 10,
      entry_ts: null,
      entry_price: 0.0,
      exit_ts: "2026-09-08T15:00:00Z",
      exit_price: 100.0,
      commission: 0.0,
      realized_pnl: 0.0,
      realized_pnl_pct: null,
      holding_period_days: null,
      close_reason: "emergency_flatten",
      provenance: "manual",
      snapshot: {
        captured: false,
        decision_context_status: "not_captured",
        provenance: "manual",
        reason: "not captured",
      },
      bridge_status: "failed",
      bridged_trade_id: null,
      bridge_error: "Fatal disk I/O error on TransactionsStore write",
      bridged_at: null,
      excursion: {
        evaluation_status: "evaluation data unavailable",
        status: "evaluation data unavailable",
        bridge_reached: false,
        mae: null,
        mfe: null,
        edge_ratio: null,
        realized_slippage: null,
        reason: "Evaluation data unavailable: bridge status 'failed'",
      },
      calibration: {
        status: "not_applicable",
        calibration_status: "not_applicable",
        conviction: null,
        bin_range: null,
        bin_win_rate: null,
        historical_bin_win_rate: null,
        bin_trade_count: 0,
        calibration_error: null,
        reason: "Model calibration not applicable for manual or uncalibrated trades",
      },
      narrative: "Manual discretionary trade on TEST_BAD; snapshot not captured and evaluation data unavailable.",
    };

    it("renders honest fallback strings and badges without crashing", () => {
      render(
        <RetrospectiveDetailModal
          isOpen={true}
          onClose={vi.fn()}
          initialTrade={worstCaseTrade}
        />
      );

      // Symbol and Trade ID
      expect(screen.getByText("TEST_BAD")).toBeInTheDocument();
      expect(screen.getByText("Trade #9999")).toBeInTheDocument();

      // Side and Provenance
      expect(screen.getByText("BUY")).toBeInTheDocument();
      expect(screen.getByText("Manual Entry")).toBeInTheDocument();

      // Snapshot honest status
      expect(screen.getByText("Snapshot: Not captured")).toBeInTheDocument();
      expect(
        screen.getByText("Snapshot not captured — trade predates retrospective logging.")
      ).toBeInTheDocument();

      // Bridge status & error telemetry
      expect(screen.getByText("Bridge: FAILED")).toBeInTheDocument();
      expect(screen.getByText("Bridge Failure Telemetry")).toBeInTheDocument();
      expect(
        screen.getByText("Fatal disk I/O error on TransactionsStore write")
      ).toBeInTheDocument();

      // Excursion honest unavailable text
      const unavailableInstances = screen.getAllByText("Evaluation data unavailable");
      expect(unavailableInstances.length).toBeGreaterThanOrEqual(2); // For both MAE and MFE

      // Edge Ratio & Slippage null fallbacks
      const emDashes = screen.getAllByText("—");
      expect(emDashes.length).toBeGreaterThanOrEqual(2);

      // Calibration non-applicability
      expect(
        screen.getByText("Model calibration not applicable for manual or uncalibrated trades")
      ).toBeInTheDocument();

      // Narrative text
      expect(
        screen.getByText("Manual discretionary trade on TEST_BAD; snapshot not captured and evaluation data unavailable.")
      ).toBeInTheDocument();
    });

    it("contains zero occurrences of NaN, undefined, or [object Object]", () => {
      const { container } = render(
        <RetrospectiveDetailModal
          isOpen={true}
          onClose={vi.fn()}
          initialTrade={worstCaseTrade}
        />
      );

      const htmlContent = container.innerHTML;

      // Absolute anti-fabrication assertions: No NaN or undefined in rendered DOM
      expect(htmlContent).not.toMatch(/\bNaN\b/);
      expect(htmlContent).not.toMatch(/\bundefined\b/);
      expect(htmlContent).not.toMatch(/\[object Object\]/);
      expect(htmlContent).not.toMatch(/null%/);
    });

    it("handles zero realized P&L cleanly without sign corruption", () => {
      render(
        <RetrospectiveDetailModal
          isOpen={true}
          onClose={vi.fn()}
          initialTrade={worstCaseTrade}
        />
      );

      // 0.00 realized PnL
      expect(screen.getByText("+$0.00")).toBeInTheDocument();
    });
  });

  // --------------------------------------------------------------------------
  // 2. Corrupted & Extreme Degenerate State Testing
  // --------------------------------------------------------------------------
  describe("Extreme Corrupted Field Handling", () => {
    it("handles negative realized P&L and partial excursion fields without throwing", () => {
      const negativeTrade: RetrospectiveTradeRecord = {
        trade_id: 111,
        symbol: "CRASH_TEST",
        strategy_id: "panic_strategy",
        pilot_id: "pilot_zero",
        side: "SELL",
        qty: 100,
        entry_ts: "2026-09-01T12:00:00Z",
        entry_price: 50.0,
        exit_ts: "2026-09-02T12:00:00Z",
        exit_price: 60.0,
        commission: 0.0,
        realized_pnl: -1000.0,
        realized_pnl_pct: -0.20,
        holding_period_days: 1.0,
        close_reason: "stop_loss",
        provenance: "unknown",
        snapshot: null,
        bridge_status: "disabled",
        bridged_trade_id: null,
        bridge_error: null,
        bridged_at: null,
        excursion: {
          evaluation_status: "evaluation data unavailable",
          status: "evaluation data unavailable",
          bridge_reached: false,
          mae: null,
          mfe: null,
          edge_ratio: null,
          realized_slippage: null,
        },
        calibration: {
          status: "not_applicable",
          conviction: null,
        },
        narrative: "Executed unknown provenance SELL on CRASH_TEST.",
      };

      const { container } = render(
        <RetrospectiveDetailModal
          isOpen={true}
          onClose={vi.fn()}
          initialTrade={negativeTrade}
        />
      );

      expect(screen.getByText("CRASH_TEST")).toBeInTheDocument();
      expect(screen.getByText("$-1000.00")).toBeInTheDocument();
      expect(screen.getByText(/-20\.00%/)).toBeInTheDocument();
      expect(screen.getByText("Unknown Provenance")).toBeInTheDocument();
      expect(screen.getByText("Bridge: DISABLED")).toBeInTheDocument();

      // Zero NaN / undefined leakage
      expect(container.innerHTML).not.toMatch(/\bNaN\b/);
      expect(container.innerHTML).not.toMatch(/\bundefined\b/);
    });

    it("handles scored calibration with bin placement cleanly", () => {
      const calibratedTrade: RetrospectiveTradeRecord = {
        trade_id: 222,
        symbol: "CALIB_SYM",
        strategy_id: "alpha_model",
        pilot_id: "pilot_top",
        side: "BUY",
        qty: 50,
        entry_ts: "2026-09-05T10:00:00Z",
        entry_price: 200.0,
        exit_ts: "2026-09-07T15:00:00Z",
        exit_price: 210.0,
        commission: 0.0,
        realized_pnl: 500.0,
        realized_pnl_pct: 0.05,
        holding_period_days: 2.2,
        close_reason: "profit_target",
        provenance: "signal_driven",
        snapshot: {
          captured: true,
          decision_context_status: "captured",
          provenance: "signal_driven",
          conviction: 0.90,
          macro_regime: "LOW_VOLATILITY",
          raw_forecast: 0.035,
          signal_score: 0.85,
        },
        bridge_status: "bridged",
        bridged_trade_id: 888,
        bridge_error: null,
        bridged_at: "2026-09-07T15:00:01Z",
        excursion: {
          evaluation_status: "available",
          status: "available",
          bridge_reached: true,
          mae: -50.0,
          mfe: 600.0,
          edge_ratio: 12.0,
          realized_slippage: 0.01,
        },
        calibration: {
          status: "scored",
          conviction: 0.90,
          bin_range: "[0.8, 1.0]",
          bin_win_rate: 0.85,
          bin_trade_count: 30,
          calibration_error: 0.05,
        },
        narrative: "Signal-driven BUY on CALIB_SYM with 0.90 conviction.",
      };

      render(
        <RetrospectiveDetailModal
          isOpen={true}
          onClose={vi.fn()}
          initialTrade={calibratedTrade}
        />
      );

      expect(screen.getByText("CALIB_SYM")).toBeInTheDocument();
      expect(screen.getByText("Signal-Driven Strategy")).toBeInTheDocument();
      expect(screen.getByText("Snapshot: Captured")).toBeInTheDocument();
      expect(screen.getByText("90%")).toBeInTheDocument(); // Model Conviction
      expect(screen.getByText("LOW_VOLATILITY")).toBeInTheDocument();
      expect(screen.getByText("3.5%")).toBeInTheDocument(); // Raw forecast
      expect(screen.getByText("0.85")).toBeInTheDocument(); // Signal score
      expect(screen.getByText("$-50.00")).toBeInTheDocument(); // MAE
      expect(screen.getByText("+$600.00")).toBeInTheDocument(); // MFE
      expect(screen.getByText("12.00x")).toBeInTheDocument(); // Edge ratio
      expect(screen.getByText("[0.8, 1.0]")).toBeInTheDocument(); // Bin range
      expect(screen.getByText("85.0%")).toBeInTheDocument(); // Bin win rate
      expect(screen.getByText("30 trades")).toBeInTheDocument(); // Sample count
    });
  });

  // --------------------------------------------------------------------------
  // 3. Network Lifecycle & Async Error Handling
  // --------------------------------------------------------------------------
  describe("API Fetch, Loading, and Error States", () => {
    it("renders loading state when tradeId is passed without initialTrade", () => {
      vi.spyOn(api, "getRetrospectiveTrade").mockReturnValue(new Promise(() => {})); // Never resolves

      render(
        <RetrospectiveDetailModal
          isOpen={true}
          onClose={vi.fn()}
          tradeId={777}
        />
      );

      expect(screen.getByText("Loading trade retrospective details...")).toBeInTheDocument();
    });

    it("renders error alert when API call rejects", async () => {
      vi.spyOn(api, "getRetrospectiveTrade").mockRejectedValue(new Error("Network timeout 504"));

      render(
        <RetrospectiveDetailModal
          isOpen={true}
          onClose={vi.fn()}
          tradeId={777}
        />
      );

      expect(await screen.findByText("Unable to load retrospective")).toBeInTheDocument();
      expect(screen.getByText("Network timeout 504")).toBeInTheDocument();
    });

    it("renders nothing when isOpen is false", () => {
      const { container } = render(
        <RetrospectiveDetailModal
          isOpen={false}
          onClose={vi.fn()}
          tradeId={777}
        />
      );

      expect(container.innerHTML).toBe("");
    });
  });

  // --------------------------------------------------------------------------
  // 4. RetrospectiveJournal Screen Adversarial State Testing
  // --------------------------------------------------------------------------
  describe("RetrospectiveJournal Screen Resilience", () => {
    it("renders journal table with worst-case trade and switches to insights cleanly", async () => {
      const mockBridge: BridgeReliabilityResponse = {
        total_closed_trades: 1,
        bridged_count: 0,
        failed_count: 1,
        disabled_count: 0,
        completeness_pct: 0.0,
        status: "degraded",
        last_failure: {
          trade_id: 105,
          symbol: "GOOGL",
          timestamp: "2026-09-08T15:00:00Z",
          error: "Simulated bridge failure on legacy trade",
        },
      };

      const mockClosedTrades: PaperBrokerClosedTrade[] = [
        {
          trade_id: 105,
          symbol: "GOOGL",
          strategy_id: null,
          pilot_id: null,
          experiment_arm: null,
          side: "BUY",
          qty: 10,
          entry_ts: null,
          entry_price: 0,
          exit_ts: "2026-09-08T15:00:00Z",
          exit_price: 165.0,
          commission: 0,
          realized_pnl: 0,
          realized_pnl_pct: null,
          holding_period_days: null,
          close_reason: "unknown",
          leg_group_id: null,
        },
      ];

      const mockTrade105Retro: RetrospectiveTradeRecord = {
        trade_id: 105,
        symbol: "GOOGL",
        strategy_id: null,
        pilot_id: null,
        side: "BUY",
        qty: 10,
        entry_ts: null,
        entry_price: 0,
        exit_ts: "2026-09-08T15:00:00Z",
        exit_price: 165.0,
        commission: 0,
        realized_pnl: 0,
        realized_pnl_pct: null,
        holding_period_days: null,
        close_reason: "unknown",
        provenance: "manual",
        snapshot: {
          captured: false,
          decision_context_status: "not_captured",
          provenance: "manual",
          reason: "not captured",
        },
        bridge_status: "failed",
        bridged_trade_id: null,
        bridge_error: "Simulated bridge failure on legacy trade",
        bridged_at: null,
        excursion: {
          evaluation_status: "evaluation data unavailable",
          status: "evaluation data unavailable",
          bridge_reached: false,
          mae: null,
          mfe: null,
          edge_ratio: null,
          realized_slippage: null,
          reason: "Evaluation data unavailable: trade not bridged",
        },
        calibration: {
          status: "not_applicable",
          calibration_status: "not_applicable",
          conviction: null,
          bin_range: null,
          bin_win_rate: null,
          historical_bin_win_rate: null,
          bin_trade_count: 0,
          calibration_error: null,
          reason: "Model calibration not applicable for manual or uncalibrated trades",
        },
        narrative: "Manual discretionary trade on GOOGL; snapshot not captured and evaluation data unavailable.",
      };

      const mockInsightsData: BatchRetrospectiveInsightsResponse = {
        automated_cohort: {
          cohort_name: "Automated (Signal-Driven)",
          total_trades: 0,
          trade_count: 0,
          winning_trades: 0,
          losing_trades: 0,
          breakeven_trades: 0,
          win_rate: null,
          total_realized_pnl: 0,
          profit_factor: null,
          mean_holding_period_days: null,
          mean_edge_ratio: null,
          mean_mae: null,
          mean_mfe: null,
          symbols: [],
          calibration_brier_score: null,
          calibration_status: "Requires >= 5 calibrated trades for statistical validity",
        },
        manual_cohort: {
          cohort_name: "Manual (Discretionary)",
          total_trades: 1,
          trade_count: 1,
          winning_trades: 0,
          losing_trades: 0,
          breakeven_trades: 1,
          win_rate: null,
          total_realized_pnl: 0,
          profit_factor: null,
          mean_holding_period_days: null,
          mean_edge_ratio: null,
          mean_mae: null,
          mean_mfe: null,
          symbols: ["GOOGL"],
          calibration_status: "not_applicable",
        },
        unrecorded_cohort: {
          cohort_name: "Unrecorded (Pre-Feature / Missing Snapshot)",
          total_trades: 1,
          trade_count: 1,
          winning_trades: 0,
          losing_trades: 0,
          breakeven_trades: 1,
          win_rate: null,
          total_realized_pnl: 0,
          profit_factor: null,
          mean_holding_period_days: null,
          mean_edge_ratio: null,
          mean_mae: null,
          mean_mfe: null,
          symbols: ["GOOGL"],
          note: "1 historical trades predate retrospective decision capture and are excluded from causal attribution.",
        },
        contrastive_insights: [],
        bridge_health: {
          total_closed_trades: 1,
          bridged_count: 0,
          failed_count: 1,
          disabled_count: 0,
          completeness_pct: 0.0,
          status: "degraded",
        },
      };

      vi.spyOn(api, "getBridgeReliability").mockResolvedValue(mockBridge);
      vi.spyOn(api, "getPaperBrokerClosedTrades").mockResolvedValue(mockClosedTrades);
      vi.spyOn(api, "getRetrospectiveTrade").mockResolvedValue(mockTrade105Retro);
      vi.spyOn(api, "getRetrospectiveInsights").mockResolvedValue(mockInsightsData);

      const { RetrospectiveJournal } = await import("../screens/RetrospectiveJournal");
      const { container } = render(<RetrospectiveJournal />);

      // Bridge Banner
      expect(await screen.findByText("Bridge Degraded")).toBeInTheDocument();
      expect(screen.getByText("0.0%")).toBeInTheDocument(); // Completeness

      // Trade Journal Row
      expect(await screen.findByText("GOOGL")).toBeInTheDocument();
      expect(screen.getByText("+$0.00")).toBeInTheDocument();

      // Wait for retroactive enrichment
      expect(await screen.findAllByText("Bridge error")).toHaveLength(2); // For both MAE and MFE

      // Verify no NaN or undefined on Journal tab
      expect(container.innerHTML).not.toMatch(/\bNaN\b/);
      expect(container.innerHTML).not.toMatch(/\bundefined\b/);

      // Switch to Pattern Insights tab
      const insightsBtn = screen.getByRole("button", { name: /Pattern Insights/i });
      await (await import("@testing-library/user-event")).default.click(insightsBtn);

      // Notice and Isolated Cohort Cards
      expect(await screen.findByText(/Cohort Isolation Principle:/)).toBeInTheDocument();
      expect(screen.getByText("Automated (Signal-Driven) Cohort")).toBeInTheDocument();
      expect(screen.getByText("Manual (Discretionary) Cohort")).toBeInTheDocument();
      expect(screen.getByText("Calibration Non-Applicability Notice")).toBeInTheDocument();
      expect(screen.getByText(/Historical \/ Pre-Feature Notice:/)).toBeInTheDocument();

      // Verify no NaN or undefined on Insights tab
      expect(container.innerHTML).not.toMatch(/\bNaN\b/);
      expect(container.innerHTML).not.toMatch(/\bundefined\b/);
    });
  });
});
