import { useState } from "react";
import { theme } from "../../theme";
import { api } from "../../api/client";
import type { CacheLongShortSimulateResult } from "../../api/types";

export function ConfiguratorWizard() {
  const [ticker, setTicker] = useState("AAPL");
  const [allocation, setAllocation] = useState(10000);
  const [simulated, setSimulated] = useState<CacheLongShortSimulateResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSimulate = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.simulateCls({ ticker, allocation });
      setSimulated(data);
    } catch (e: any) {
      console.error(e);
      setError(e.message || "Simulation failed");
    } finally {
      setLoading(false);
    }
  };

  const handleStart = async () => {
    if (!simulated?.proxy_ticker || !simulated?.correlation_coefficient) return;
    try {
      await api.startCls({
        ticker,
        proxy_ticker: simulated.proxy_ticker,
        allocation,
        correlation_coefficient: simulated.correlation_coefficient,
      });
      alert("Strategy started!");
    } catch (e: any) {
      console.error(e);
      alert(e.message || "Failed to start strategy");
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
          {error && <div style={{ color: theme.decline, fontWeight: 600 }}>{error}</div>}
          <button className="btn btn-primary" onClick={handleSimulate} disabled={loading} style={{ padding: "10px 16px", background: theme.accent, color: "#fff", border: "none", borderRadius: 4, cursor: "pointer" }}>
            {loading ? "Simulating..." : "Simulate Strategy"}
          </button>
        </div>
      </div>

      {simulated && (
        <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
          <h2 style={{ margin: "0 0 16px" }}>Step 2: Review Simulation</h2>
          
          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 24 }}>
            {!simulated.found ? (
              <div style={{ color: theme.decline }}>{simulated.reason}</div>
            ) : (
              <>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: theme.textSecondary }}>Beta</span>
                  <span style={{ fontWeight: 600 }}>{simulated.beta?.toFixed(2)}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: theme.textSecondary }}>Long Proxy</span>
                  <span style={{ fontWeight: 600, color: theme.growth }}>{simulated.proxy_ticker}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: theme.textSecondary }}>Correlation Coefficient</span>
                  <span style={{ fontWeight: 600 }}>{simulated.correlation_coefficient?.toFixed(2)}</span>
                </div>
              </>
            )}
          </div>

          <button className="btn btn-success" onClick={handleStart} disabled={!simulated.found} style={{ width: "100%", padding: "12px", background: simulated.found ? theme.growth : theme.border, color: "#fff", border: "none", borderRadius: 4, cursor: simulated.found ? "pointer" : "not-allowed", fontWeight: 600 }}>
            Confirm & Start Strategy
          </button>
        </div>
      )}
    </div>
  );
}
