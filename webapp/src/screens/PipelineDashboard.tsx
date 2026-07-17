import { useState, useEffect } from "react";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { Loading, ErrorState } from "../components/ui";
import { theme } from "../theme";

export function PipelineDashboard() {
  const [tick, setTick] = useState(0);

  const statusApi = useApi<{ is_running: boolean; current_run_id: string | null; run_history: any[] }>(
    async () => (await api.getControlStatus()) as { is_running: boolean; current_run_id: string | null; run_history: any[] },
    [tick]
  );

  // Poll status every 5 seconds
  useEffect(() => {
    const interval = setInterval(() => setTick((t) => t + 1), 5000);
    return () => clearInterval(interval);
  }, []);

  const [isTriggering, setIsTriggering] = useState(false);
  const [triggerError, setTriggerError] = useState<string | null>(null);

  const handleTrigger = async (type: "full" | "data" | "metrics") => {
    try {
      setIsTriggering(true);
      setTriggerError(null);
      if (type === "full") {
        await api.postControlRun();
      } else if (type === "data") {
        await api.postControlPipelineData();
      } else if (type === "metrics") {
        await api.postControlPipelineMetrics();
      }
      statusApi.reload();
    } catch (err: any) {
      setTriggerError(err.message || "Failed to trigger run.");
    } finally {
      setIsTriggering(false);
    }
  };

  if (statusApi.loading && !statusApi.data) {
    return <Loading />;
  }

  if (statusApi.error && !statusApi.data) {
    return <ErrorState message={statusApi.error} status={statusApi.status} />;
  }

  const { is_running, current_run_id, run_history } = statusApi.data || { is_running: false, current_run_id: null, run_history: [] };

  return (
    <div className="view-container">
      <header style={{ marginBottom: 24 }}>
        <h1>Pipeline Dashboard</h1>
        <p style={{ color: theme.textMuted }}>Monitor pipeline health and trigger background processes.</p>
      </header>

      {triggerError && (
        <div style={{ padding: 12, marginBottom: 16, background: theme.caution, color: theme.base, borderRadius: 4 }}>
          <strong>Error:</strong> {triggerError}
        </div>
      )}

      <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", marginBottom: 32 }}>
        <div className="tile">
          <h2>Status</h2>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 16 }}>
            <div style={{ 
              width: 12, height: 12, borderRadius: "50%", 
              background: is_running ? theme.accent : theme.textMuted 
            }} />
            <span style={{ fontSize: "1.2rem", fontWeight: 600 }}>
              {is_running ? "Running" : "Idle"}
            </span>
          </div>
          {is_running && current_run_id && (
            <p style={{ marginTop: 8, color: theme.textMuted }}>Current Run ID: {current_run_id}</p>
          )}
        </div>

        <div className="tile">
          <h2>Controls</h2>
          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 16 }}>
            <button 
              className="btn btn-primary" 
              disabled={is_running || isTriggering}
              onClick={() => handleTrigger("full")}
            >
              Run Full Advisory Pipeline
            </button>
            <div style={{ display: "flex", gap: 8 }}>
              <button 
                className="btn" 
                style={{ flex: 1 }}
                disabled={is_running || isTriggering}
                onClick={() => handleTrigger("data")}
              >
                Run Data Backfill
              </button>
              <button 
                className="btn" 
                style={{ flex: 1 }}
                disabled={is_running || isTriggering}
                onClick={() => handleTrigger("metrics")}
              >
                Run Metrics Precompute
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="tile">
        <h2>Run History</h2>
        {run_history.length === 0 ? (
          <p style={{ marginTop: 16, color: theme.textMuted }}>No recent runs.</p>
        ) : (
          <div style={{ overflowX: "auto", marginTop: 16 }}>
            <table style={{ width: "100%", textAlign: "left", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.borderStrong}` }}>
                  <th style={{ padding: 8 }}>Run ID</th>
                  <th style={{ padding: 8 }}>Mode</th>
                  <th style={{ padding: 8 }}>State</th>
                  <th style={{ padding: 8 }}>Started</th>
                  <th style={{ padding: 8 }}>Duration</th>
                </tr>
              </thead>
              <tbody>
                {run_history.map((run: any) => (
                  <tr key={run.run_id} style={{ borderBottom: `1px solid #232c38` }}>
                    <td style={{ padding: 8, fontFamily: "monospace", fontSize: "0.85rem" }}>
                      {run.run_id.slice(0, 8)}...
                    </td>
                    <td style={{ padding: 8 }}>
                      <span style={{ 
                        padding: "2px 6px", borderRadius: 4, fontSize: "0.75rem",
                        background: theme.surface2, color: theme.textMuted,
                        textTransform: "uppercase"
                      }}>
                        {run.mode || "FULL"}
                      </span>
                    </td>
                    <td style={{ padding: 8 }}>
                      <span style={{ 
                        color: run.state === "succeeded" ? theme.accent : 
                               run.state === "failed" ? theme.caution : 
                               theme.textMuted 
                      }}>
                        {run.state.toUpperCase()}
                      </span>
                    </td>
                    <td style={{ padding: 8 }}>{new Date(run.started_at).toLocaleString()}</td>
                    <td style={{ padding: 8 }}>
                      {run.duration_seconds ? `${run.duration_seconds.toFixed(1)}s` : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
