import { useEffect, useState } from "react";
import { Modal } from "./Modal";
import { api } from "../api/client";
import { theme } from "../theme";
import type { RetrospectiveTradeRecord } from "../api/types";
import {
  BrainCircuit,
  Compass,
  FileText,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  HelpCircle,
  Sliders,
  TrendingDown,
  TrendingUp,
} from "lucide-react";

export interface RetrospectiveDetailModalProps {
  isOpen: boolean;
  onClose: () => void;
  tradeId?: number | null;
  initialTrade?: RetrospectiveTradeRecord | null;
}

export function RetrospectiveDetailModal({
  isOpen,
  onClose,
  tradeId,
  initialTrade,
}: RetrospectiveDetailModalProps) {
  const [trade, setTrade] = useState<RetrospectiveTradeRecord | null>(initialTrade ?? null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initialTrade) {
      setTrade(initialTrade);
      setError(null);
      return;
    }
    if (isOpen && tradeId != null) {
      setLoading(true);
      setError(null);
      api
        .getRetrospectiveTrade(tradeId)
        .then((data) => {
          setTrade(data);
          setLoading(false);
        })
        .catch((err) => {
          setError(err?.message || `Failed to load retrospective trade ${tradeId}`);
          setLoading(false);
        });
    } else if (!isOpen) {
      setTrade(null);
      setError(null);
    }
  }, [isOpen, tradeId, initialTrade]);

  if (!isOpen) return null;

  return (
    <Modal
      ariaLabel={trade ? `Retrospective for ${trade.symbol} trade #${trade.trade_id}` : "Trade Retrospective"}
      onClose={onClose}
      size="wide"
    >
      <div style={{ padding: "var(--s-4)", display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
        {loading && (
          <div style={{ padding: 36, textAlign: "center", color: theme.textSecondary }}>
            Loading trade retrospective details...
          </div>
        )}

        {error && (
          <div
            style={{
              padding: 24,
              textAlign: "center",
              background: "rgba(230, 103, 103, 0.1)",
              border: `1px solid ${theme.decline}`,
              borderRadius: 8,
              color: theme.decline,
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: 6 }}>Unable to load retrospective</div>
            <div style={{ fontSize: 13, color: theme.textSecondary }}>{error}</div>
          </div>
        )}

        {!loading && !error && trade && (
          <>
            {/* Header: Title, Identifiers, Realized PnL */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
                flexWrap: "wrap",
                gap: 12,
                borderBottom: `1px solid ${theme.border}`,
                paddingBottom: "var(--s-3)",
              }}
            >
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ fontSize: 24, fontWeight: 700, color: theme.textPrimary }}>
                    {trade.symbol}
                  </span>
                  <span
                    style={{
                      padding: "2px 8px",
                      borderRadius: 4,
                      fontSize: 12,
                      fontWeight: 700,
                      backgroundColor: trade.side === "BUY" ? "rgba(80, 200, 120, 0.15)" : "rgba(230, 103, 103, 0.15)",
                      color: trade.side === "BUY" ? theme.growth : theme.decline,
                    }}
                  >
                    {trade.side}
                  </span>
                  <span style={{ fontSize: 13, color: theme.textMuted }}>
                    Trade #{trade.trade_id}
                  </span>
                </div>
                <div style={{ fontSize: 13, color: theme.textSecondary, marginTop: 4 }}>
                  {trade.strategy_id ? `Strategy: ${trade.strategy_id}` : "Discretionary / No Strategy"}
                  {trade.pilot_id ? ` • Pilot: ${trade.pilot_id}` : ""}
                  {trade.exit_ts ? ` • Closed: ${new Date(trade.exit_ts).toLocaleString()}` : ""}
                </div>
              </div>

              {/* Realized PnL Pill */}
              <div style={{ textAlign: "right" }}>
                <div
                  style={{
                    fontSize: 20,
                    fontWeight: 700,
                    color: trade.realized_pnl >= 0 ? theme.growth : theme.decline,
                  }}
                >
                  {trade.realized_pnl >= 0 ? "+" : ""}${trade.realized_pnl.toFixed(2)}
                </div>
                <div style={{ fontSize: 13, color: theme.textSecondary, marginTop: 2 }}>
                  {trade.realized_pnl_pct != null
                    ? `${(trade.realized_pnl_pct * 100).toFixed(2)}%`
                    : "—"}
                  {trade.holding_period_days != null
                    ? ` over ${trade.holding_period_days.toFixed(1)}d`
                    : ""}
                </div>
              </div>
            </div>

            {/* Badges: Provenance & Snapshot status */}
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              {/* Provenance Badge */}
              <div
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 10px",
                  borderRadius: 6,
                  fontSize: 12,
                  fontWeight: 600,
                  backgroundColor:
                    trade.provenance === "signal_driven"
                      ? "rgba(99, 102, 241, 0.15)"
                      : trade.provenance === "manual"
                      ? "rgba(245, 158, 11, 0.15)"
                      : "rgba(255, 255, 255, 0.08)",
                  color:
                    trade.provenance === "signal_driven"
                      ? "#818cf8"
                      : trade.provenance === "manual"
                      ? theme.caution
                      : theme.textMuted,
                  border: `1px solid ${
                    trade.provenance === "signal_driven"
                      ? "rgba(99, 102, 241, 0.3)"
                      : trade.provenance === "manual"
                      ? "rgba(245, 158, 11, 0.3)"
                      : theme.border
                  }`,
                }}
              >
                {trade.provenance === "signal_driven" && <BrainCircuit size={14} />}
                {trade.provenance === "manual" && <FileText size={14} />}
                {trade.provenance === "unknown" && <HelpCircle size={14} />}
                <span>
                  {trade.provenance === "signal_driven"
                    ? "Signal-Driven Strategy"
                    : trade.provenance === "manual"
                    ? "Manual Entry"
                    : "Unknown Provenance"}
                </span>
              </div>

              {/* Snapshot Status Badge */}
              <div
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 10px",
                  borderRadius: 6,
                  fontSize: 12,
                  fontWeight: 600,
                  backgroundColor:
                    trade.snapshot?.decision_context_status === "captured" || trade.snapshot?.captured
                      ? "rgba(80, 200, 120, 0.1)"
                      : "rgba(255, 255, 255, 0.05)",
                  color:
                    trade.snapshot?.decision_context_status === "captured" || trade.snapshot?.captured
                      ? theme.growth
                      : theme.textMuted,
                  border: `1px solid ${theme.border}`,
                }}
              >
                {trade.snapshot?.decision_context_status === "captured" || trade.snapshot?.captured ? (
                  <CheckCircle2 size={14} />
                ) : (
                  <XCircle size={14} />
                )}
                <span>
                  {trade.snapshot?.decision_context_status === "captured" || trade.snapshot?.captured
                    ? "Snapshot: Captured"
                    : "Snapshot: Not captured"}
                </span>
              </div>

              {/* Bridge Status Badge */}
              <div
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 10px",
                  borderRadius: 6,
                  fontSize: 12,
                  fontWeight: 600,
                  backgroundColor:
                    trade.bridge_status === "bridged"
                      ? "rgba(80, 200, 120, 0.1)"
                      : trade.bridge_status === "failed"
                      ? "rgba(230, 103, 103, 0.1)"
                      : "rgba(255, 255, 255, 0.05)",
                  color:
                    trade.bridge_status === "bridged"
                      ? theme.growth
                      : trade.bridge_status === "failed"
                      ? theme.decline
                      : theme.textMuted,
                  border: `1px solid ${theme.border}`,
                }}
              >
                {trade.bridge_status === "bridged" && <CheckCircle2 size={14} />}
                {trade.bridge_status === "failed" && <AlertTriangle size={14} />}
                <span>Bridge: {trade.bridge_status.toUpperCase()}</span>
              </div>
            </div>

            {/* Narrative Box */}
            <div
              style={{
                padding: 16,
                borderRadius: 8,
                backgroundColor: theme.surface2,
                border: `1px solid ${theme.borderStrong}`,
                lineHeight: 1.5,
              }}
            >
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: 0.8,
                  color: theme.textMuted,
                  marginBottom: 6,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <Compass size={13} />
                Retrospective Causal Summary
              </div>
              <div style={{ fontSize: 14, color: theme.textPrimary }}>{trade.narrative}</div>
            </div>

            {/* Hold-Period Excursion Analytics Grid */}
            <div>
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: 0.8,
                  color: theme.textSecondary,
                  marginBottom: 8,
                }}
              >
                Hold-Period Excursion &amp; Execution Analytics
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
                  gap: 12,
                }}
              >
                {/* MAE */}
                <div
                  style={{
                    padding: 12,
                    borderRadius: 6,
                    backgroundColor: theme.surface2,
                    border: `1px solid ${theme.border}`,
                  }}
                >
                  <div style={{ fontSize: 11, color: theme.textMuted, display: "flex", alignItems: "center", gap: 4 }}>
                    <TrendingDown size={12} color={theme.decline} />
                    MAE (Adverse)
                  </div>
                  <div style={{ fontSize: 16, fontWeight: 700, marginTop: 4, color: theme.textPrimary }}>
                    {trade.excursion.mae != null ? (
                      <span style={{ color: theme.decline }}>
                        -{(trade.excursion.mae * 100).toFixed(1)}%
                      </span>
                    ) : (
                      <span style={{ fontSize: 12, fontWeight: 500, color: theme.textMuted }}>
                        Evaluation data unavailable
                      </span>
                    )}
                  </div>
                </div>

                {/* MFE */}
                <div
                  style={{
                    padding: 12,
                    borderRadius: 6,
                    backgroundColor: theme.surface2,
                    border: `1px solid ${theme.border}`,
                  }}
                >
                  <div style={{ fontSize: 11, color: theme.textMuted, display: "flex", alignItems: "center", gap: 4 }}>
                    <TrendingUp size={12} color={theme.growth} />
                    MFE (Favorable)
                  </div>
                  <div style={{ fontSize: 16, fontWeight: 700, marginTop: 4, color: theme.textPrimary }}>
                    {trade.excursion.mfe != null ? (
                      <span style={{ color: theme.growth }}>
                        +{(trade.excursion.mfe * 100).toFixed(1)}%
                      </span>
                    ) : (
                      <span style={{ fontSize: 12, fontWeight: 500, color: theme.textMuted }}>
                        Evaluation data unavailable
                      </span>
                    )}
                  </div>
                </div>

                {/* Edge Ratio */}
                <div
                  style={{
                    padding: 12,
                    borderRadius: 6,
                    backgroundColor: theme.surface2,
                    border: `1px solid ${theme.border}`,
                  }}
                >
                  <div style={{ fontSize: 11, color: theme.textMuted }}>Edge Ratio (MFE / |MAE|)</div>
                  <div style={{ fontSize: 16, fontWeight: 700, marginTop: 4, color: theme.textPrimary }}>
                    {trade.excursion.edge_ratio != null ? (
                      `${trade.excursion.edge_ratio.toFixed(2)}x`
                    ) : (
                      <span style={{ color: theme.textMuted }}>—</span>
                    )}
                  </div>
                </div>

                {/* Slippage */}
                <div
                  style={{
                    padding: 12,
                    borderRadius: 6,
                    backgroundColor: theme.surface2,
                    border: `1px solid ${theme.border}`,
                  }}
                >
                  <div style={{ fontSize: 11, color: theme.textMuted }}>Realized Slippage</div>
                  <div style={{ fontSize: 16, fontWeight: 700, marginTop: 4, color: theme.textPrimary }}>
                    {trade.excursion.realized_slippage != null ? (
                      `$${trade.excursion.realized_slippage.toFixed(2)}`
                    ) : (
                      <span style={{ color: theme.textMuted }}>—</span>
                    )}
                  </div>
                </div>
              </div>

              {trade.excursion.reason && (
                <div style={{ fontSize: 12, color: theme.caution, marginTop: 6 }}>
                  * {trade.excursion.reason}
                </div>
              )}
            </div>

            {/* Entry Decision Snapshot Context */}
            <div
              style={{
                padding: 16,
                borderRadius: 8,
                backgroundColor: theme.surface,
                border: `1px solid ${theme.border}`,
              }}
            >
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: 0.8,
                  color: theme.textSecondary,
                  marginBottom: 10,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <BrainCircuit size={14} />
                Entry Decision Context (Forward-Only Capture)
              </div>

              {trade.snapshot?.captured ? (
                trade.provenance === "signal_driven" ? (
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
                    <div>
                      <div style={{ fontSize: 11, color: theme.textMuted }}>Model Conviction</div>
                      <div style={{ fontSize: 15, fontWeight: 600, color: theme.textPrimary, marginTop: 2 }}>
                        {trade.snapshot.conviction != null ? `${(trade.snapshot.conviction * 100).toFixed(0)}%` : "—"}
                      </div>
                    </div>
                    <div>
                      <div style={{ fontSize: 11, color: theme.textMuted }}>HMM Market Regime</div>
                      <div style={{ fontSize: 15, fontWeight: 600, color: theme.accent, marginTop: 2 }}>
                        {trade.snapshot.macro_regime || "—"}
                      </div>
                    </div>
                    <div>
                      <div style={{ fontSize: 11, color: theme.textMuted }}>Horizon Forecast</div>
                      <div style={{ fontSize: 15, fontWeight: 600, color: theme.textPrimary, marginTop: 2 }}>
                        {trade.snapshot.raw_forecast != null
                          ? `${(trade.snapshot.raw_forecast * 100).toFixed(1)}%`
                          : "—"}
                      </div>
                    </div>
                    <div>
                      <div style={{ fontSize: 11, color: theme.textMuted }}>Signal Score</div>
                      <div style={{ fontSize: 15, fontWeight: 600, color: theme.textPrimary, marginTop: 2 }}>
                        {trade.snapshot.signal_score != null ? trade.snapshot.signal_score.toFixed(2) : "—"}
                      </div>
                    </div>
                  </div>
                ) : trade.provenance === "manual" ? (
                  <div style={{ color: theme.textSecondary, fontSize: 13, lineHeight: 1.5 }}>
                    <div>👤 Manual trade — no algorithmic signal was evaluated at entry.</div>
                    {trade.snapshot.decision_rationale && (
                      <div style={{ marginTop: 6, fontStyle: "italic", color: theme.textPrimary }}>
                        "{trade.snapshot.decision_rationale}"
                      </div>
                    )}
                  </div>
                ) : (
                  // Provenance "unknown" with captured=true is not the same
                  // fact as "manual" -- collapsing the two into one branch
                  // (a prior version did) asserts a specific claim ("no
                  // algorithmic signal was evaluated") about a trade whose
                  // provenance is genuinely unrecorded, not confirmed manual.
                  <div style={{ color: theme.textMuted, fontSize: 13, fontStyle: "italic" }}>
                    Entry provenance was not recorded as either signal-driven or manual for this trade.
                  </div>
                )
              ) : (
                <div style={{ color: theme.textMuted, fontSize: 13, fontStyle: "italic" }}>
                  Snapshot not captured — trade predates retrospective logging.
                </div>
              )}
            </div>

            {/* Conviction Reliability Calibration */}
            <div
              style={{
                padding: 16,
                borderRadius: 8,
                backgroundColor: theme.surface,
                border: `1px solid ${theme.border}`,
              }}
            >
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: 0.8,
                  color: theme.textSecondary,
                  marginBottom: 10,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <Sliders size={14} />
                Conviction Calibration Placement
              </div>

              {trade.calibration.status === "available" ? (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
                  <div>
                    <div style={{ fontSize: 11, color: theme.textMuted }}>Conviction Bin</div>
                    <div style={{ fontSize: 15, fontWeight: 600, color: theme.textPrimary, marginTop: 2 }}>
                      {trade.calibration.bin_range
                        ? `${trade.calibration.bin_range[0].toFixed(2)} – ${trade.calibration.bin_range[1].toFixed(2)}`
                        : "—"}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, color: theme.textMuted }}>Historical Bin Win Rate</div>
                    <div
                      style={{
                        fontSize: 15,
                        fontWeight: 600,
                        marginTop: 2,
                        color:
                          (trade.calibration.bin_win_rate ?? trade.calibration.historical_bin_win_rate) != null
                            ? theme.growth
                            : theme.textMuted,
                      }}
                    >
                      {trade.calibration.bin_win_rate != null || trade.calibration.historical_bin_win_rate != null
                        ? `${(((trade.calibration.bin_win_rate ?? trade.calibration.historical_bin_win_rate) as number) * 100).toFixed(1)}%`
                        : "—"}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, color: theme.textMuted }}>Bin Trades Sample</div>
                    <div style={{ fontSize: 15, fontWeight: 600, color: theme.textPrimary, marginTop: 2 }}>
                      {/* CONSTRAINT #4: null (never attempted / not applicable)
                          must never render as a fabricated "0 trades" sample. */}
                      {trade.calibration.bin_trade_count != null ? `${trade.calibration.bin_trade_count} trades` : "—"}
                    </div>
                  </div>
                </div>
              ) : (
                <div style={{ color: theme.textMuted, fontSize: 13, fontStyle: "italic" }}>
                  {trade.calibration.reason || "Model calibration not applicable for manual or uncalibrated trades."}
                </div>
              )}
            </div>

            {/* Bridge Failure Details (if failed) */}
            {trade.bridge_status === "failed" && trade.bridge_error && (
              <div
                style={{
                  padding: 12,
                  borderRadius: 6,
                  backgroundColor: "rgba(230, 103, 103, 0.1)",
                  border: `1px solid ${theme.decline}`,
                  color: theme.decline,
                  fontSize: 13,
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: 2 }}>Bridge Failure Telemetry</div>
                <div>{trade.bridge_error}</div>
              </div>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}
