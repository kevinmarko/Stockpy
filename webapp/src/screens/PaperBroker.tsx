import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { TabGuide } from "../components/TabGuide";
import { Modal } from "../components/Modal";
import { theme } from "../theme";

export function PaperBroker() {
  const account = useApi(() => api.getPaperBrokerAccount());
  const positions = useApi(() => api.getPaperBrokerPositions());
  const orders = useApi(() => api.getPaperBrokerOrders(100));
  const [showResetModal, setShowResetModal] = useState(false);
  const [resetCash, setResetCash] = useState(100000);

  const resetMutation = useMutation((cash: number) => api.resetPaperBroker(cash));

  const handleReset = async () => {
    await resetMutation.run(resetCash);
    if (!resetMutation.error) {
      setShowResetModal(false);
      account.reload();
      positions.reload();
      orders.reload();
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
        <h1 style={{ margin: 0, fontSize: 24, fontWeight: 600 }}>Paper Broker</h1>
        <button
          onClick={() => setShowResetModal(true)}
          style={{
            padding: "8px 16px",
            background: theme.surface,
            border: `1px solid ${theme.border}`,
            color: theme.textPrimary,
            borderRadius: 4,
            cursor: "pointer",
            fontWeight: 500
          }}
        >
          Reset Paper Account
        </button>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: 24, display: "flex", flexDirection: "column", gap: 24 }}>
        <TabGuide tabKey="paper-broker" />
        
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
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Unrealized P&L</th>
                </tr>
              </thead>
              <tbody>
                {positions.data?.length === 0 && (
                  <tr>
                    <td colSpan={6} style={{ padding: 24, textAlign: "center", color: theme.textSecondary }}>No open positions</td>
                  </tr>
                )}
                {positions.data?.map(p => (
                  <tr key={p.symbol} style={{ borderBottom: `1px solid ${theme.border}` }}>
                    <td style={{ padding: "12px 16px", fontWeight: 500 }}>{p.symbol}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>{p.qty}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>${p.avg_cost.toFixed(2)}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>{p.current_price ? `$${p.current_price.toFixed(2)}` : "—"}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>{p.market_value ? `$${p.market_value.toFixed(2)}` : "—"}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right", color: p.unrealized_pl && p.unrealized_pl >= 0 ? theme.growth : theme.decline }}>
                      {p.unrealized_pl ? `$${p.unrealized_pl.toFixed(2)}` : "—"}
                      {p.unrealized_pl_pct != null && ` (${(p.unrealized_pl_pct * 100).toFixed(2)}%)`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

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
    </div>
  );
}
