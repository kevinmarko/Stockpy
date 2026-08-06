import { useState, useEffect } from "react";
import { theme } from "../../theme";

export function TradeApprovalCenter() {
  const [trades, setTrades] = useState<any[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetch("/api/v1/strategy/cache-long-short/pending-approvals")
      .then(res => res.json())
      .then(data => setTrades(data.pending_trades || []))
      .catch(console.error);
  }, []);

  const handleToggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const handleToggleAll = () => {
    if (selected.size === trades.length) setSelected(new Set());
    else setSelected(new Set(trades.map(t => t.id)));
  };

  const handleApprove = async () => {
    if (selected.size === 0) return;
    setLoading(true);
    try {
      await fetch("/api/v1/strategy/cache-long-short/approve-bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trade_ids: Array.from(selected) })
      });
      alert(`Approved ${selected.size} trades!`);
      setTrades(trades.filter(t => !selected.has(t.id)));
      setSelected(new Set());
    } catch (e) {
      console.error(e);
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
              <th style={{ padding: 12 }}>Date</th>
              <th style={{ padding: 12 }}>Reason</th>
              <th style={{ padding: 12 }}>Action</th>
              <th style={{ padding: 12 }}>Impact</th>
            </tr>
          </thead>
          <tbody>
            {trades.map(trade => (
              <tr key={trade.id} style={{ borderBottom: `1px solid ${theme.border}` }}>
                <td style={{ padding: 12 }}>
                  <input 
                    type="checkbox" 
                    checked={selected.has(trade.id)} 
                    onChange={() => handleToggle(trade.id)} 
                  />
                </td>
                <td style={{ padding: 12 }}>{trade.date}</td>
                <td style={{ padding: 12 }}>{trade.reason}</td>
                <td style={{ padding: 12, fontWeight: 500 }}>{trade.action}</td>
                <td style={{ padding: 12, color: trade.impact.startsWith('-') ? theme.decline : theme.growth }}>{trade.impact}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
