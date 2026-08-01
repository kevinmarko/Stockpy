import { useState, useEffect, useRef } from "react";
import { useToast } from "../components/ToastContext";
import { TabGuide } from "../components/TabGuide";
import { api } from "../api/client";
import type { JobRecord } from "../api/types";

interface LogEntry {
  id: string;
  time: string;
  level: "INFO" | "WARNING" | "ERROR";
  module: string;
  message: string;
  symbol?: string;
  status?: number;
}

interface ProcessInfo {
  pid: number;
  name: string;
  status: "running" | "stopped" | "idle";
  cpu: number; // %
  memoryRss: number; // MB
}

const INITIAL_PROCESSES: ProcessInfo[] = [
  { pid: 42429, name: "pytest runner", status: "running", cpu: 12.4, memoryRss: 145 },
  { pid: 42430, name: "main.py --interval 60", status: "running", cpu: 4.8, memoryRss: 312 },
  { pid: 42431, name: "api/pilots_api.py (uvicorn)", status: "running", cpu: 1.2, memoryRss: 88 },
  { pid: 42432, name: "orchestrator_daemon.py", status: "idle", cpu: 0.1, memoryRss: 190 },
];

export function Console() {
  const { addToast } = useToast();
  const [activeTab, setActiveTab] = useState<"logs" | "processes">("logs");

  const [activeJob, setActiveJob] = useState<JobRecord | null>(null);
  const [showBacktestModal, setShowBacktestModal] = useState(false);
  const [backtestStrategies, setBacktestStrategies] = useState("");

  useEffect(() => {
    if (!activeJob || !activeJob.job_id) return;
    const isTerminal = activeJob.status === "success" || activeJob.status === "failed" || activeJob.status === "cancelled";
    if (isTerminal || activeJob.is_running === false) return;

    const timer = setInterval(async () => {
      try {
        const res = await api.getJobStatus(activeJob.job_id);
        setActiveJob(res);
      } catch (err) {
        console.error("Job status fetch failed", err);
      }
    }, 1000);

    return () => clearInterval(timer);
  }, [activeJob]);

  const launchJob = async (jobType: string, params?: Record<string, any>) => {
    try {
      const res = await api.createJob(jobType, params);
      setActiveJob(res);
      addToast({
        type: "info",
        title: `Job ${jobType} Started`,
        description: `ID: ${res.job_id}`,
      });
    } catch (err: any) {
      addToast({
        type: "error",
        title: `Job ${jobType} Failed`,
        description: err?.message || "Failed to launch job",
      });
    }
  };

  const handleCancelJob = async () => {
    if (!activeJob) return;
    try {
      await api.cancelJob(activeJob.job_id);
      setActiveJob((prev) => (prev ? { ...prev, status: "cancelled", is_running: false } : null));
      addToast({
        type: "warning",
        title: "Job Cancelled",
        description: `Job ${activeJob.job_id} cancelled.`,
      });
    } catch (err: any) {
      addToast({
        type: "error",
        title: "Cancel Failed",
        description: err?.message || "Failed to cancel job",
      });
    }
  };

  const handleRunBacktest = () => {
    if (!backtestStrategies.trim()) {
      alert("Enter at least one strategy id (comma-separated).");
      return;
    }
    const strategies = backtestStrategies.split(",").map((s) => s.trim()).filter(Boolean);
    const today = new Date().toISOString().slice(0, 10);
    launchJob("validation", {
      strategies,
      start: "2024-01-01",
      end: today,
    });
    setShowBacktestModal(false);
  };

  // Terminal state
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const logContainerRef = useRef<HTMLDivElement>(null);

  // Process manager state
  const [processes, setProcesses] = useState<ProcessInfo[]>(INITIAL_PROCESSES);

  // Generate simulated streaming log lines
  useEffect(() => {
    const modules = ["GDELTSource", "DataEngine", "StrategyEngine", "AlpacaBroker", "RiskGate"];
    const symbols = ["AAPL", "NVDA", "MSFT", "AMZN", "SPY"];
    const levels: ("INFO" | "WARNING" | "ERROR")[] = ["INFO", "INFO", "INFO", "WARNING", "ERROR"];

    const interval = setInterval(() => {
      const level = levels[Math.floor(Math.random() * levels.length)];
      const mod = modules[Math.floor(Math.random() * modules.length)];
      const sym = symbols[Math.floor(Math.random() * symbols.length)];
      const now = new Date().toISOString().split("T")[1].slice(0, 8);

      let msg = `Executed pipeline cycle pass for ${sym}.`;
      let status: number | undefined;

      if (level === "WARNING") {
        msg = `Rate limit warning on ${mod} endpoint. Soft throttle active.`;
        status = 429;
      } else if (level === "ERROR") {
        msg = `Data fetch timeout for ticker ${sym} on ${mod}. Skipping item (dead-letter logged).`;
        status = 504;
      }

      const entry: LogEntry = {
        id: Math.random().toString(36).substring(2, 9),
        time: now,
        level,
        module: mod,
        message: msg,
        symbol: sym,
        status,
      };

      setLogs((prev) => [...prev.slice(-300), entry]);
    }, 1500);

    return () => clearInterval(interval);
  }, []);

  // Auto scroll terminal to bottom
  useEffect(() => {
    if (autoScroll && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  // Simulate process CPU/memory fluctuations
  useEffect(() => {
    const interval = setInterval(() => {
      setProcesses((prev) =>
        prev.map((proc) => {
          if (proc.status !== "running") return proc;
          const cpuDelta = (Math.random() - 0.5) * 2;
          const memDelta = (Math.random() - 0.5) * 5;
          return {
            ...proc,
            cpu: Math.max(0.1, Number((proc.cpu + cpuDelta).toFixed(1))),
            memoryRss: Math.max(20, Math.round(proc.memoryRss + memDelta)),
          };
        })
      );
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  // Filter logs by search query or tags (symbol:AAPL, module:GDELTSource, status:429)
  const filteredLogs = logs.filter((l) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    if (q.startsWith("symbol:")) {
      return l.symbol?.toLowerCase() === q.replace("symbol:", "");
    }
    if (q.startsWith("module:")) {
      return l.module?.toLowerCase() === q.replace("module:", "");
    }
    if (q.startsWith("status:")) {
      return String(l.status) === q.replace("status:", "");
    }
    return (
      l.message.toLowerCase().includes(q) ||
      l.module.toLowerCase().includes(q) ||
      l.level.toLowerCase().includes(q)
    );
  });

  const handleKillProcess = (pid: number) => {
    setProcesses((prev) =>
      prev.map((p) => (p.pid === pid ? { ...p, status: "stopped", cpu: 0 } : p))
    );
    addToast({
      type: "error",
      title: `Process ${pid} Terminated`,
      description: "SIGKILL signal issued by operator.",
    });
  };

  const handleRestartProcess = (pid: number) => {
    setProcesses((prev) =>
      prev.map((p) => (p.pid === pid ? { ...p, status: "running", cpu: 2.5 } : p))
    );
    addToast({
      type: "success",
      title: `Process ${pid} Restarted`,
      description: "Process re-initialized cleanly.",
    });
  };

  return (
    <div className="screen">
      <h1 className="screen-title">One-Click Command Center</h1>
      <p className="screen-sub">
        Real-time ANSI streaming log terminal, regex/tag filter engine, and active process supervisor.
      </p>

      <TabGuide tabKey="console" />

      {/* Quick Launchers */}
      <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }}>
        <h2 style={{ fontSize: "var(--t-subhead)", margin: "0 0 var(--s-3)" }}>Quick Launchers</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "var(--s-2-5)" }}>
          <button className="btn" onClick={() => launchJob("preflight")}>
            🛡️ Preflight Check
          </button>
          <button className="btn" onClick={() => launchJob("pytest")}>
            🧪 Run Test Suite
          </button>
          <button className="btn" onClick={() => launchJob("orchestrator")}>
            🚀 Advisory Pipeline
          </button>
          <button className="btn" onClick={() => launchJob("verify")}>
            ⚡ Full Verification
          </button>
          <button className="btn" onClick={() => launchJob("gravity")}>
            🔍 Gravity Audit
          </button>
          <button className="btn" onClick={() => setShowBacktestModal(true)}>
            📊 Run Backtest
          </button>
        </div>

        {activeJob && (
          <div style={{ marginTop: "var(--s-3)", padding: "var(--s-3)", background: "var(--surface-2)", borderRadius: "var(--r-sm)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <span style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>Active Job: </span>
              <span style={{ fontWeight: 600, color: "var(--accent)" }}>{activeJob.job_type}</span> (<span>{activeJob.status}</span>)
            </div>
            {activeJob.cancellable && activeJob.status === "running" && (
              <button className="btn btn-danger" style={{ fontSize: "var(--t-micro)" }} onClick={handleCancelJob}>
                Cancel Active Job
              </button>
            )}
          </div>
        )}

        {showBacktestModal && (
          <div style={{ marginTop: "var(--s-3)", padding: "var(--s-3)", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "var(--r-sm)" }}>
            <h4 style={{ margin: "0 0 var(--s-2)" }}>Strategies (comma-separated ids)</h4>
            <input
              type="text"
              className="input"
              placeholder="rsi2_mean_reversion, macd_trend"
              value={backtestStrategies}
              onChange={(e) => setBacktestStrategies(e.target.value)}
              style={{ width: "100%", marginBottom: "var(--s-2)" }}
            />
            <div style={{ display: "flex", gap: "var(--s-2)" }}>
              <button className="btn btn-primary" onClick={handleRunBacktest}>
                Run Backtest
              </button>
              <button className="btn" onClick={() => setShowBacktestModal(false)}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </section>

      {/* View Selector Tabs */}
      <div style={{ display: "flex", gap: "var(--s-2)", marginBottom: "var(--s-4)" }}>
        <button
          className={activeTab === "logs" ? "btn btn-primary" : "btn"}
          onClick={() => setActiveTab("logs")}
        >
          💻 Live Terminal Stream
        </button>
        <button
          className={activeTab === "processes" ? "btn btn-primary" : "btn"}
          onClick={() => setActiveTab("processes")}
        >
          ⚙️ Active Process Manager ({processes.filter((p) => p.status === "running").length})
        </button>
      </div>

      {activeTab === "logs" ? (
        <section className="card card-pad" style={{ background: "var(--base)", border: "1px solid var(--border)" }}>
          {/* Controls Bar */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "var(--s-3)",
              flexWrap: "wrap",
              gap: "var(--s-2)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
              <input
                type="text"
                placeholder="Filter logs (e.g. symbol:AAPL, module:RiskGate, 429)..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{
                  background: "var(--surface)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--r-xs)",
                  padding: "var(--s-2) var(--s-3)",
                  fontSize: "var(--t-caption)",
                  minWidth: "280px",
                }}
              />
              <button
                onClick={() => setLogs([])}
                style={{
                  background: "var(--surface-2)",
                  color: "var(--text-muted)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--r-xs)",
                  padding: "var(--s-2) var(--s-3)",
                  fontSize: "var(--t-caption)",
                  cursor: "pointer",
                }}
              >
                Clear Log
              </button>
            </div>

            <button
              onClick={() => setAutoScroll((prev) => !prev)}
              style={{
                background: autoScroll ? "rgba(16, 185, 129, 0.15)" : "var(--surface-2)",
                color: autoScroll ? "var(--growth)" : "var(--caution)",
                border: `1px solid ${autoScroll ? "var(--growth)" : "var(--caution)"}`,
                borderRadius: "var(--r-xs)",
                padding: "var(--s-2) var(--s-3)",
                fontSize: "var(--t-caption)",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {autoScroll ? "🔒 Auto-Scroll Active" : "⏸ Pause Scroll"}
            </button>
          </div>

          {/* Terminal Box */}
          <div
            ref={logContainerRef}
            style={{
              height: "460px",
              overflowY: "auto",
              fontFamily: "monospace",
              fontSize: "var(--t-caption)",
              background: "#050709",
              borderRadius: "var(--r-sm)",
              padding: "var(--s-3)",
              display: "flex",
              flexDirection: "column",
              gap: "4px",
              border: "1px solid rgba(255, 255, 255, 0.05)",
            }}
          >
            {filteredLogs.length === 0 ? (
              <div style={{ color: "var(--text-muted)", textAlign: "center", padding: "var(--s-5)" }}>
                Waiting for log stream output...
              </div>
            ) : (
              filteredLogs.map((log) => (
                <div key={log.id} style={{ display: "flex", gap: "var(--s-2)", lineHeight: 1.4 }}>
                  <span style={{ color: "var(--text-muted)" }}>[{log.time}]</span>
                  <span
                    style={{
                      fontWeight: 700,
                      color:
                        log.level === "ERROR"
                          ? "var(--decline)"
                          : log.level === "WARNING"
                          ? "var(--caution)"
                          : "var(--accent)",
                      minWidth: "65px",
                    }}
                  >
                    [{log.level}]
                  </span>
                  <span style={{ color: "var(--growth)", minWidth: "120px" }}>[{log.module}]</span>
                  <span style={{ color: "var(--text-primary)", flex: 1 }}>{log.message}</span>
                </div>
              ))
            )}
          </div>
        </section>
      ) : (
        <section className="card card-pad">
          <h2 style={{ fontSize: "var(--t-subhead)", margin: "0 0 var(--s-3)" }}>Active Background Processes</h2>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "var(--t-body)" }}>
              <thead>
                <tr style={{ background: "var(--surface-2)", borderBottom: "1px solid var(--border)" }}>
                  <th style={{ padding: "var(--s-2-5) var(--s-3)" }}>PID</th>
                  <th style={{ padding: "var(--s-2-5) var(--s-3)" }}>Process Name</th>
                  <th style={{ padding: "var(--s-2-5) var(--s-3)" }}>Status</th>
                  <th style={{ padding: "var(--s-2-5) var(--s-3)" }}>CPU Load</th>
                  <th style={{ padding: "var(--s-2-5) var(--s-3)" }}>Memory RSS</th>
                  <th style={{ padding: "var(--s-2-5) var(--s-3)" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {processes.map((proc) => (
                  <tr key={proc.pid} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "var(--s-3)", fontFamily: "monospace", fontWeight: 700 }}>{proc.pid}</td>
                    <td style={{ padding: "var(--s-3)", fontWeight: 600 }}>{proc.name}</td>
                    <td style={{ padding: "var(--s-3)" }}>
                      <span
                        style={{
                          padding: "2px 6px",
                          borderRadius: "var(--r-xs)",
                          fontSize: "var(--t-micro)",
                          fontWeight: 700,
                          background: proc.status === "running" ? "rgba(16, 185, 129, 0.15)" : "rgba(239, 68, 68, 0.2)",
                          color: proc.status === "running" ? "var(--growth)" : "var(--decline)",
                        }}
                      >
                        {proc.status.toUpperCase()}
                      </span>
                    </td>
                    <td style={{ padding: "var(--s-3)", fontFamily: "monospace" }}>{proc.cpu}%</td>
                    <td style={{ padding: "var(--s-3)", fontFamily: "monospace" }}>{proc.memoryRss} MB</td>
                    <td style={{ padding: "var(--s-3)" }}>
                      {proc.status === "running" ? (
                        <button
                          onClick={() => handleKillProcess(proc.pid)}
                          style={{
                            background: "rgba(239, 68, 68, 0.2)",
                            color: "var(--decline)",
                            border: "1px solid var(--decline)",
                            borderRadius: "var(--r-xs)",
                            padding: "2px 8px",
                            fontSize: "var(--t-caption)",
                            fontWeight: 600,
                            cursor: "pointer",
                          }}
                        >
                          Kill Process
                        </button>
                      ) : (
                        <button
                          onClick={() => handleRestartProcess(proc.pid)}
                          style={{
                            background: "rgba(16, 185, 129, 0.15)",
                            color: "var(--growth)",
                            border: "1px solid var(--growth)",
                            borderRadius: "var(--r-xs)",
                            padding: "2px 8px",
                            fontSize: "var(--t-caption)",
                            fontWeight: 600,
                            cursor: "pointer",
                          }}
                        >
                          Restart
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
