import { useNavigate } from "react-router";
import { api, apiMeta } from "../api/client";
import type { StrategyReportCardRow, StrategyReportCardSnapshot } from "../api/types";
import { ErrorState, InfoTip, Loading } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { fmtNum, fmtPct, fmtUsd } from "../format";
import { theme } from "../theme";

/**
 * Strategy Report Card — every catalog Pilot's honest, PBO/DSR-gated backtest
 * ("Predicted") placed directly alongside its live paper-trading track
 * record ("Actual"), so an operator can see whether real results are
 * tracking what the backtest promised. Also includes non-Pilot "buckets" --
 * a strategy_id seen in closed paper trades with no catalog match (e.g. a
 * manually-run structure) -- which NEVER carry a predicted side (see
 * `pilots/strategy_report_card.py`).
 *
 * Predicted and Actual are two INDEPENDENT measurements with different units
 * and methodology (see `StrategyReportCardActual`'s field comments in
 * types.ts) -- they render as two visually distinct panels, never merged
 * into one combined verdict. A `null` value on either side renders "—",
 * never a fabricated "0"/"N/A" that could read as a real measured zero.
 */
export function StrategyReportCard() {
  const navigate = useNavigate();
  const { data, loading, error, status, reload } = useApi<StrategyReportCardSnapshot>(
    () => api.getStrategyReportCard(),
    []
  );
  useAutoPoll(reload, "observability", { hasError: error != null });

  return (
    <div className="screen-container">
      <button
        className="btn btn-link"
        style={{ paddingLeft: 0, marginBottom: "var(--s-4)" }}
        onClick={() => navigate("/strategy-health")}
      >
        ← Strategy Health
      </button>

      <div style={{ display: "flex", gap: "var(--s-2)", alignItems: "center", marginBottom: "var(--s-1)" }}>
        <h1 className="screen-title">Strategy Report Card</h1>
        {apiMeta.useMock && (
          <InfoTip triggerClassName="chip" content="Running on mock data">
            demo
          </InfoTip>
        )}
      </div>
      <p className="screen-sub" style={{ marginBottom: "var(--s-4)" }}>
        Predicted (validated backtest) vs. Actual (live paper-trading track record) --
        two independent measurements, shown side by side, never blended into one number.
      </p>

      {loading && <Loading lines={4} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        data.length === 0 ? (
          <div className="empty" style={{ padding: "var(--s-7-5)" }}>
            No strategies to report on yet.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
            {data.map((row) => (
              <ReportCardRow key={row.pilot_id} row={row} />
            ))}
          </div>
        )
      )}
    </div>
  );
}

/** null -> "PASS"/"FAIL" would be dishonest for a row with no backtest at
 * all; "—" (never a fabricated "N/A"/"0") is what renders in that case. */
function GateLabel({ deployable }: { deployable: boolean | null }) {
  if (deployable === true) return <span style={{ color: theme.growth, fontWeight: 600 }}>PASS</span>;
  if (deployable === false) return <span style={{ color: theme.decline, fontWeight: 600 }}>FAIL</span>;
  return <span style={{ color: theme.textSecondary, fontWeight: 600 }}>—</span>;
}

function StatRow({ label, value, tip }: { label: string; value: string; tip?: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--s-2)" }}>
      <span style={{ color: theme.textSecondary }}>
        {label}
        {tip && (
          <InfoTip content={tip} triggerClassName="chip" triggerStyle={{ marginLeft: 4 }}>
            ?
          </InfoTip>
        )}
      </span>
      <span style={{ color: theme.textPrimary, fontFamily: "monospace" }}>{value}</span>
    </div>
  );
}

function ReportCardRow({ row }: { row: StrategyReportCardRow }) {
  const { predicted, actual } = row;

  return (
    <div
      style={{
        border: `1px solid ${theme.borderStrong}`,
        padding: "var(--s-4)",
        borderRadius: 8,
      }}
      data-testid={`report-card-row-${row.pilot_id}`}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--s-2)", marginBottom: "var(--s-3)" }}>
        <h2 style={{ fontSize: "var(--t-title)", margin: 0, color: theme.textPrimary }}>{row.name}</h2>
        <span className="chip">{row.category}</span>
        {!row.is_pilot && (
          <InfoTip
            triggerClassName="chip"
            content="Seen in closed paper trades but has no matching entry in the Pilot catalog -- there is no backtest to report for it."
          >
            non-pilot bucket
          </InfoTip>
        )}
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "var(--s-4)",
        }}
      >
        {/* Predicted Panel */}
        <div
          style={{
            background: theme.surface2,
            padding: "var(--s-3)",
            borderRadius: 4,
            border: `1px solid ${theme.border}`,
          }}
        >
          <h3
            style={{
              fontSize: "var(--t-caption)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              color: theme.textSecondary,
              marginBottom: "var(--s-2)",
            }}
          >
            Predicted (Backtest)
          </h3>

          <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
            <StatRow label="Sharpe:" value={fmtNum(predicted.sharpe, 2)} />
            <StatRow label="Max DD:" value={fmtPct(predicted.max_drawdown, 1, { fromFraction: true })} />
            <StatRow label="PBO:" value={fmtPct(predicted.pbo, 1, { fromFraction: true })} />
            <StatRow label="DSR:" value={fmtNum(predicted.dsr, 2)} />
            {predicted.is_options_selling === true && (
              <StatRow
                label="Stress gate:"
                value={
                  predicted.stress_gate_passed === true
                    ? "passed"
                    : predicted.stress_gate_passed === false
                      ? "failed"
                      : "—"
                }
                tip="Options-selling strategies must also survive the OCT 2008 / FEB 2018 / MAR 2020 / AUG 2024 tail-scenario stress windows."
              />
            )}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                marginTop: "var(--s-1)",
                paddingTop: "var(--s-1)",
                borderTop: `1px solid ${theme.border}`,
              }}
            >
              <span style={{ color: theme.textSecondary }}>Gate:</span>
              <GateLabel deployable={predicted.deployable} />
            </div>
            {predicted.reason && (
              <div style={{ fontSize: "var(--t-caption)", color: theme.textMuted, marginTop: "var(--s-1)" }}>
                {predicted.reason}
              </div>
            )}
          </div>
        </div>

        {/* Actual Panel */}
        <div
          style={{
            background: theme.surface2,
            padding: "var(--s-3)",
            borderRadius: 4,
            border: `1px solid ${theme.border}`,
          }}
        >
          <h3
            style={{
              fontSize: "var(--t-caption)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              color: theme.textSecondary,
              marginBottom: "var(--s-2)",
            }}
          >
            Actual (Live Paper Trading)
          </h3>

          <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
            <StatRow
              label="Trades:"
              value={String(actual.trade_count)}
            />
            <StatRow
              label="Realized Sharpe"
              value={fmtNum(actual.realized_sharpe_proxy, 2)}
              tip="Per-trade realized-P&L proxy, not directly comparable to the Predicted side's backtest Sharpe (different unit and methodology)."
            />
            <StatRow
              label="Max DD (USD)"
              value={fmtUsd(actual.max_cumulative_drawdown_usd)}
              tip="Cumulative USD drawdown across closed paper trades -- not directly comparable to the Predicted side's fractional max_drawdown (dollars vs. a fraction of backtest equity)."
            />
            <StatRow label="Win rate:" value={fmtPct(actual.win_rate, 0, { fromFraction: true })} />
            <StatRow label="Avg P&L:" value={fmtPct(actual.avg_realized_pnl_pct, 2, { fromFraction: true, signed: true })} />
            <StatRow label="Total realized P&L:" value={fmtUsd(actual.total_realized_pnl_usd)} />
            {actual.reason && (
              <div style={{ fontSize: "var(--t-caption)", color: theme.textMuted, marginTop: "var(--s-1)" }}>
                {actual.reason}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
