import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { api, apiMeta } from "../api/client";
import type { StrategyReportCardRow } from "../api/types";
import { ErrorState, InfoTip, Loading } from "../components/ui";
import { fmtNum, fmtUsd } from "../format";
import { theme } from "../theme";

export function StrategyReportCard() {
  const navigate = useNavigate();
  const [data, setData] = useState<StrategyReportCardRow[] | null>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let canceled = false;
    api.getStrategyReportCard()
      .then(res => { if (!canceled) setData(res); })
      .catch(err => { if (!canceled) setError(err); });
    return () => { canceled = true; };
  }, []);

  if (error) return <ErrorState message={error.message} status={null} />;
  if (!data) return <Loading />;

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
        Predicted vs. Actual performance alignment.
      </p>

      <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
        {data.map(row => (
          <div key={row.pilot_id} style={{
            border: `1px solid ${theme.borderStrong}`,
            padding: "var(--s-4)",
            borderRadius: 8
          }}>
            <h2 style={{ fontSize: "var(--t-title)", marginBottom: "var(--s-3)", color: theme.textPrimary }}>
              {row.name}
            </h2>
            
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--s-4)" }}>
              {/* Predicted Panel */}
              <div style={{
                background: theme.surface2,
                padding: "var(--s-3)",
                borderRadius: 4,
                border: `1px solid ${theme.border}`
              }}>
                <h3 style={{ fontSize: "var(--t-caption)", textTransform: "uppercase", letterSpacing: "0.05em", color: theme.textSecondary, marginBottom: "var(--s-2)" }}>
                  Predicted (Backtest)
                </h3>
                
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: theme.textSecondary }}>Sharpe:</span>
                    <span style={{ color: theme.textPrimary, fontFamily: "monospace" }}>
                      {row.predicted.sharpe != null ? fmtNum(row.predicted.sharpe, 2) : "—"}
                    </span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: theme.textSecondary }}>Max DD:</span>
                    <span style={{ color: theme.textPrimary, fontFamily: "monospace" }}>
                      {row.predicted.max_drawdown != null ? (row.predicted.max_drawdown * 100).toFixed(1) + "%" : "—"}
                    </span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: theme.textSecondary }}>PBO:</span>
                    <span style={{ color: theme.textPrimary, fontFamily: "monospace" }}>
                      {row.predicted.pbo != null ? (row.predicted.pbo * 100).toFixed(1) + "%" : "—"}
                    </span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginTop: "var(--s-1)", paddingTop: "var(--s-1)", borderTop: `1px solid ${theme.border}` }}>
                    <span style={{ color: theme.textSecondary }}>Gate:</span>
                    <span style={{ 
                      color: row.predicted.deployable === true ? theme.growth : row.predicted.deployable === false ? theme.decline : theme.textSecondary,
                      fontWeight: 600
                    }}>
                      {row.predicted.deployable === true ? "PASS" : row.predicted.deployable === false ? "FAIL" : "N/A"}
                    </span>
                  </div>
                  {row.predicted.reason && (
                    <div style={{ fontSize: "var(--t-caption)", color: theme.decline, marginTop: "var(--s-1)" }}>
                      {row.predicted.reason}
                    </div>
                  )}
                </div>
              </div>

              {/* Actual Panel */}
              <div style={{
                background: theme.surface2,
                padding: "var(--s-3)",
                borderRadius: 4,
                border: `1px solid ${theme.border}`
              }}>
                <h3 style={{ fontSize: "var(--t-caption)", textTransform: "uppercase", letterSpacing: "0.05em", color: theme.textSecondary, marginBottom: "var(--s-2)" }}>
                  Actual (Live Proxy)
                </h3>
                
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: theme.textSecondary }}>
                      Realized Sharpe
                      <InfoTip content="Proxy for realized Sharpe, unit mismatch against predicted side's Sharpe." triggerClassName="chip" triggerStyle={{ marginLeft: 4 }}>?</InfoTip>
                    </span>
                    <span style={{ color: theme.textPrimary, fontFamily: "monospace" }}>
                      {row.actual.realized_sharpe_proxy != null ? fmtNum(row.actual.realized_sharpe_proxy, 2) : "—"}
                    </span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: theme.textSecondary }}>
                      Max DD USD
                      <InfoTip content="USD unit, mismatch against predicted side's fractional max_drawdown." triggerClassName="chip" triggerStyle={{ marginLeft: 4 }}>?</InfoTip>
                    </span>
                    <span style={{ color: theme.textPrimary, fontFamily: "monospace" }}>
                      {row.actual.max_cumulative_drawdown_usd != null ? fmtUsd(row.actual.max_cumulative_drawdown_usd) : "—"}
                    </span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: theme.textSecondary }}>Trades:</span>
                    <span style={{ color: theme.textPrimary, fontFamily: "monospace" }}>
                      {row.actual.trade_count}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
