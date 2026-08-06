import { useState } from "react";
import { theme } from "../../theme";
import { api } from "../../api/client";
import { useMutation } from "../../hooks/useMutation";
import { Input, Button, Notice } from "../ui";

export function ConfiguratorWizard() {
  const [ticker, setTicker] = useState("AAPL");
  const [allocation, setAllocation] = useState(10000);
  const simulate = useMutation(() => api.simulateCls({ ticker, allocation }));
  const start = useMutation(() => {
    const s = simulate.result;
    if (!s?.proxy_ticker || s.correlation_coefficient == null) {
      throw new Error("Simulate a strategy before starting it.");
    }
    return api.startCls({
      ticker,
      proxy_ticker: s.proxy_ticker,
      allocation,
      correlation_coefficient: s.correlation_coefficient,
    });
  });

  const simulated = simulate.result;
  const canStart = !!simulated?.found && simulated.proxy_ticker != null && simulated.correlation_coefficient != null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24, maxWidth: 600 }}>
      <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
        <h2 style={{ margin: "0 0 16px" }}>Step 1: Set Goals</h2>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Input
            label="Concentrated Ticker"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
          />
          <Input
            label="Target Allocation ($)"
            type="number"
            value={allocation}
            onChange={(e) => setAllocation(Number(e.target.value))}
          />
          {simulate.error && <Notice variant="warn">{simulate.error}</Notice>}
          <Button variant="primary" pending={simulate.pending} onClick={() => simulate.run()}>
            Simulate Strategy
          </Button>
        </div>
      </div>

      {simulated && (
        <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
          <h2 style={{ margin: "0 0 16px" }}>Step 2: Review Simulation</h2>

          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 24 }}>
            {!simulated.found ? (
              <Notice variant="info">{simulated.reason ?? "No usable proxy hedge found for this ticker."}</Notice>
            ) : (
              <>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: theme.textSecondary }}>Beta</span>
                  <span style={{ fontWeight: 600 }}>{simulated.beta != null ? simulated.beta.toFixed(2) : "—"}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: theme.textSecondary }}>Proxy Hedge</span>
                  <span style={{ fontWeight: 600, color: theme.growth }}>{simulated.proxy_ticker}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: theme.textSecondary }}>Correlation Coefficient</span>
                  <span style={{ fontWeight: 600 }}>
                    {simulated.correlation_coefficient != null ? simulated.correlation_coefficient.toFixed(2) : "—"}
                  </span>
                </div>
              </>
            )}
          </div>

          {start.error && <Notice variant="warn" style={{ marginBottom: 16 }}>{start.error}</Notice>}
          {start.result && (
            <Notice variant="success" style={{ marginBottom: 16 }}>
              Strategy started (position #{start.result.position_id}).
            </Notice>
          )}

          <Button variant="primary" block pending={start.pending} disabled={!canStart} onClick={() => start.run()}>
            Confirm &amp; Start Strategy
          </Button>
        </div>
      )}
    </div>
  );
}
