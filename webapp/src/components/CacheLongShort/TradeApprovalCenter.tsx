import { useState, useEffect } from "react";
import { theme } from "../../theme";
import { api } from "../../api/client";
import type { CacheLongShortPendingTrade } from "../../api/types";

export function TradeApprovalCenter() {
  const [trades, setTrades] = useState<CacheLongShortPendingTrade[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.getClsPendingApprovals()
      .then(setTrades)
      .catch(console.error);
  }, []);

  const handleToggle = (id: number) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const handleToggleAll = () => {
    if (selected.size === trades.length) setSelected(new Set());
    else setSelected(new Set(trades.map(t => t.lot_id)));
  };

  const handleApprove = async () => {
    if (selected.size === 0) return;
    setLoading(true);
    try {
      await api.approveClsBulk(Array.from(selected));
      alert(`Approved ${selected.size} trades!`);
      setTrades(trades.filter(t => !selected.has(t.lot_id)));
      setSelected(new Set());
    } catch (e: any) {
      console.error(e);
      alert(e.message || "Failed to approve trades");
    } finally {
      setLoading(false);
    }
  };

  if (trades.length === 0) {
    return (
      <div style={{ padding: 48, textAlign: "center", color: theme.textMuted, border: `1px dashed ${theme.border}`, borderRadius: 8 }}>
        No pending trades requiring approval.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0 }}>Pending Actions</h3>
        <button 
          className="btn btn-primary" 
          disabled={selected.size === 0 || loading}
          onClick={handleApprove}
          style={{ padding: "8px 16px", background: selected.size === 0 ? theme.surface2 : theme.accent, color: selected.size === 0 ? theme.textMuted : "#fff", border: "none", borderRadius: 4, cursor: selected.size === 0 ? "default" : "pointer" }}
        >
          {loading ? "Processing..." : `Approve Selected (${selected.size})`}
        </button>
      </div>

      <div className="card" style={{ background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8, overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
          <thead>
            <tr style={{ background: theme.surface2, borderBottom: `1px solid ${theme.border}` }}>
              <th style={{ padding: 12, width: 40 }}>
                <input 
                  type="checkbox" 
                  checked={trades.length > 0 && selected.size === trades.length} 
                  onChange={handleToggleAll} 
                />
              </th>
              <th style={{ padding: 12 }}>Lot ID</th>
              <th style={{ padding: 12 }}>Position ID</th>
              <th style={{ padding: 12 }}>Cost Basis</th>
            </tr>
          </thead>
          <tbody>
            {trades.map(trade => (
              <tr key={trade.lot_id} style={{ borderBottom: `1px solid ${theme.border}` }}>
                <td style={{ padding: 12 }}>
                  <input 
                    type="checkbox" 
                    checked={selected.has(trade.lot_id)} 
                    onChange={() => handleToggle(trade.lot_id)} 
                  />
                </td>
                <td style={{ padding: 12 }}>{trade.lot_id}</td>
                <td style={{ padding: 12 }}>{trade.position_id}</td>
                <td style={{ padding: 12 }}>${trade.cost_basis.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
