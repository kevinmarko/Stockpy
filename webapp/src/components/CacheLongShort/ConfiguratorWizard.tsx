import { useState } from "react";
import { theme } from "../../theme";

export function ConfiguratorWizard() {
  const [ticker, setTicker] = useState("AAPL");
  const [allocation, setAllocation] = useState(10000);
  const [simulated, setSimulated] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const handleSimulate = async () => {
    setLoading(true);
    try {
      // Direct fetch mock until client is fully updated
      const res = await fetch("/api/v1/strategy/cache-long-short/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker, allocation })
      });
      const data = await res.json();
      setSimulated(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleStart = async () => {
    try {
      await fetch("/api/v1/strategy/cache-long-short/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker, allocation })
      });
      alert("Strategy started!");
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24, maxWidth: 600 }}>
      <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
        <h2 style={{ margin: "0 0 16px" }}>Step 1: Set Goals</h2>
        
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div>
            <label style={{ display: "block", marginBottom: 8, fontWeight: 600 }}>Concentrated Ticker</label>
            <input 
              className="input"
              value={ticker} 
              onChange={e => setTicker(e.target.value.toUpperCase())} 
              style={{ width: "100%", padding: 8 }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: 8, fontWeight: 600 }}>Target Allocation ($)</label>
            <input 
              className="input"
              type="number" 
              value={allocation} 
              onChange={e => setAllocation(Number(e.target.value))}
              style={{ width: "100%", padding: 8 }}
            />
          </div>
          <button className="btn btn-primary" onClick={handleSimulate} disabled={loading} style={{ padding: "10px 16px", background: theme.accent, color: "#fff", border: "none", borderRadius: 4, cursor: "pointer" }}>
            {loading ? "Simulating..." : "Simulate Strategy"}
          </button>
        </div>
      </div>

      {simulated && (
        <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
          <h2 style={{ margin: "0 0 16px" }}>Step 2: Review Simulation</h2>
          
          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 24 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: theme.textSecondary }}>Beta</span>
              <span style={{ fontWeight: 600 }}>{simulated.beta}</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: theme.textSecondary }}>Long Proxy</span>
              <span style={{ fontWeight: 600, color: theme.growth }}>{simulated.overlay.long_proxy} (${simulated.overlay.long_size})</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: theme.textSecondary }}>Short Proxy</span>
              <span style={{ fontWeight: 600, color: theme.decline }}>{simulated.overlay.short_proxy} (${simulated.overlay.short_size})</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", borderTop: `1px solid ${theme.border}`, paddingTop: 12 }}>
              <span style={{ color: theme.textSecondary }}>Net Exposure</span>
              <span style={{ fontWeight: 600 }}>${simulated.overlay.net_exposure}</span>
            </div>
          </div>

          <button className="btn btn-success" onClick={handleStart} style={{ width: "100%", padding: "12px", background: theme.growth, color: "#fff", border: "none", borderRadius: 4, cursor: "pointer", fontWeight: 600 }}>
            Confirm & Start Strategy
          </button>
        </div>
      )}
    </div>
  );
}
