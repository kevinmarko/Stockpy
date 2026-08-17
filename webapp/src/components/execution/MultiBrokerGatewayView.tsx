import React, { useState } from "react";
import {
  Server,
  Activity,
  Zap,
  RefreshCw,
  ArrowRight,
  Radio,
  CheckCircle,
  Repeat,
} from "lucide-react";
import { theme } from "../../theme";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { useMutation } from "../../hooks/useMutation";
import type {
  MultiBrokerStatusResponse,
  BrokerHealthStatusDto,
  BrokerFailoverRequest,
} from "../../api/types";
import DemoDataBadge from "../DemoDataBadge";

export interface MultiBrokerGatewayViewProps {
  className?: string;
  onFailoverSuccess?: (newBrokerId: string) => void;
}

export const MultiBrokerGatewayView: React.FC<MultiBrokerGatewayViewProps> = ({
  className = "",
  onFailoverSuccess,
}) => {
  const { data: status, loading, error, reload } = useApi<MultiBrokerStatusResponse>(
    () => api.getMultiBrokerStatus(),
    []
  );

  const [selectedTargetBroker, setSelectedTargetBroker] = useState<string>("");
  const [failoverReason, setFailoverReason] = useState<string>("");
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const failoverMutation = useMutation((req: BrokerFailoverRequest) =>
    api.triggerBrokerFailover(req)
  );

  const handleTriggerFailover = async (targetId: string, reasonText?: string) => {
    setActionMessage(null);
    const res = await failoverMutation.run({
      target_broker: targetId,
      reason: reasonText || failoverReason || "Manual operator failover request",
    });

    if (res && res.status === "ok") {
      setActionMessage(`Switched active broker to ${res.active_broker}`);
      reload();
      if (onFailoverSuccess) {
        onFailoverSuccess(res.active_broker);
      }
    }
  };

  const getCircuitBadge = (circuit: BrokerHealthStatusDto["circuit_state"]) => {
    switch (circuit) {
      case "closed":
        return {
          label: "CIRCUIT CLOSED (HEALTHY)",
          color: theme.growth,
          bg: "rgba(16, 185, 129, 0.12)",
          border: "rgba(16, 185, 129, 0.3)",
        };
      case "half_open":
        return {
          label: "HALF-OPEN (CANARY PROBE)",
          color: theme.caution,
          bg: "rgba(245, 158, 11, 0.12)",
          border: "rgba(245, 158, 11, 0.3)",
        };
      case "open":
      default:
        return {
          label: "CIRCUIT OPEN (TRIPPED)",
          color: theme.decline,
          bg: "rgba(239, 68, 68, 0.12)",
          border: "rgba(239, 68, 68, 0.3)",
        };
    }
  };

  const getConnectionBadge = (conn: BrokerHealthStatusDto["connection_state"]) => {
    switch (conn) {
      case "connected":
        return {
          label: "CONNECTED",
          color: theme.growth,
          bg: "rgba(16, 185, 129, 0.15)",
        };
      case "degraded":
        return {
          label: "DEGRADED",
          color: theme.caution,
          bg: "rgba(245, 158, 11, 0.15)",
        };
      case "failing":
      case "disconnected":
      default:
        return {
          label: conn.toUpperCase(),
          color: theme.decline,
          bg: "rgba(239, 68, 68, 0.15)",
        };
    }
  };

  if (loading && !status) {
    return (
      <div style={{ padding: 24, textAlign: "center", color: theme.textSecondary }}>
        <RefreshCw size={20} className="animate-spin" style={{ display: "inline-block", marginRight: 8 }} />
        Loading Multi-Broker Gateway status...
      </div>
    );
  }

  if (error && !status) {
    return (
      <div style={{ padding: 16, color: theme.decline, background: "rgba(239,68,68,0.1)", borderRadius: 6 }}>
        Error loading broker gateway status: {String(error)}
      </div>
    );
  }

  const activeBrokerId = status?.active_broker_id || "alpaca";
  const brokerList = status?.brokers ? Object.values(status.brokers) : [];
  const audits = status?.recent_routing_audits || [];

  return (
    <div
      className={`multi-broker-gateway-container ${className}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 16,
        background: theme.base,
        color: theme.textPrimary,
        borderRadius: 8,
        padding: 16,
        border: `1px solid ${theme.border}`,
      }}
    >
      {/* Header Bar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 12,
          borderBottom: `1px solid ${theme.border}`,
          paddingBottom: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 8,
              background: "rgba(16, 185, 129, 0.15)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: theme.growth,
            }}
          >
            <Server size={20} />
          </div>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <h2 style={{ margin: 0, fontSize: "1.15rem", fontWeight: 700 }}>
                Unified Multi-Broker Execution Gateway
              </h2>
              <DemoDataBadge />
            </div>
            <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
              Circuit-Breaker Failover Engine • Heartbeat Latency Telemetry • Multi-Venue Routing
            </div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button
            onClick={() => reload()}
            style={{
              background: theme.surface,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 4,
              padding: "6px 12px",
              fontSize: "0.75rem",
              display: "flex",
              alignItems: "center",
              gap: 6,
              cursor: "pointer",
            }}
          >
            <RefreshCw size={13} />
            Refresh Telemetry
          </button>
        </div>
      </div>

      {/* Gateway Overview Banner */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: 12,
          background: theme.surface,
          padding: 14,
          borderRadius: 6,
          border: `1px solid ${theme.border}`,
        }}
      >
        <div>
          <div style={{ fontSize: "0.72rem", color: theme.textSecondary }}>Active Primary Gateway</div>
          <div style={{ fontSize: "1.15rem", fontWeight: 800, color: theme.growth, display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
            <Radio size={16} className="animate-pulse" />
            {activeBrokerId.toUpperCase()}
          </div>
          <div style={{ fontSize: "0.68rem", color: theme.textSecondary, marginTop: 2 }}>
            Failover Mode: {status?.manual_override_broker_id ? "MANUAL OVERRIDE ACTIVE" : "AUTO"}
          </div>
        </div>

        <div>
          <div style={{ fontSize: "0.72rem", color: theme.textSecondary }}>Total Orders Routed</div>
          <div style={{ fontSize: "1.15rem", fontWeight: 700, color: theme.accent, marginTop: 2 }}>
            {(status?.total_orders_routed || 0).toLocaleString()}
          </div>
          <div style={{ fontSize: "0.68rem", color: theme.textSecondary, marginTop: 2 }}>
            Across all registered venues
          </div>
        </div>

        <div>
          <div style={{ fontSize: "0.72rem", color: theme.textSecondary }}>Failover Event Count</div>
          <div style={{ fontSize: "1.15rem", fontWeight: 700, color: status?.total_failovers ? theme.caution : theme.growth, marginTop: 2 }}>
            {status?.total_failovers || 0}
          </div>
          <div style={{ fontSize: "0.68rem", color: theme.textSecondary, marginTop: 2 }}>
            Last: {status?.last_failover_time ? new Date(status.last_failover_time).toLocaleTimeString() : "None"}
          </div>
        </div>

        <div>
          <div style={{ fontSize: "0.72rem", color: theme.textSecondary }}>Priority Routing Hierarchy</div>
          <div style={{ fontSize: "0.78rem", fontWeight: 600, color: theme.textPrimary, marginTop: 4 }}>
            {(status?.priority_hierarchy || ["alpaca", "interactive_brokers", "tradier", "fmp_paper"]).join(" → ")}
          </div>
        </div>
      </div>

      {/* Action Notification */}
      {actionMessage && (
        <div
          style={{
            background: "rgba(16, 185, 129, 0.12)",
            border: "1px solid rgba(16, 185, 129, 0.3)",
            color: theme.growth,
            padding: "8px 12px",
            borderRadius: 6,
            fontSize: "0.8rem",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <CheckCircle size={16} />
          <span>{actionMessage}</span>
        </div>
      )}

      {/* Manual Failover Control Section */}
      <div
        style={{
          background: theme.surface,
          border: `1px solid ${theme.border}`,
          borderRadius: 6,
          padding: 12,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 10,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Zap size={16} color={theme.accent} />
          <span style={{ fontSize: "0.82rem", fontWeight: 600 }}>Manual Failover Operator Override:</span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <select
            value={selectedTargetBroker}
            onChange={(e) => setSelectedTargetBroker(e.target.value)}
            style={{
              padding: "5px 10px",
              background: theme.base,
              color: theme.textPrimary,
              border: `1px solid ${theme.border}`,
              borderRadius: 4,
              fontSize: "0.8rem",
            }}
          >
            <option value="">Select Target Broker...</option>
            {brokerList.map((b) => (
              <option key={b.broker_id} value={b.broker_id} disabled={b.broker_id === activeBrokerId}>
                {b.broker_id.toUpperCase()} ({b.latency_ms}ms, {b.circuit_state.toUpperCase()})
              </option>
            ))}
          </select>

          <input
            type="text"
            placeholder="Reason (optional)"
            value={failoverReason}
            onChange={(e) => setFailoverReason(e.target.value)}
            style={{
              padding: "5px 10px",
              background: theme.base,
              color: theme.textPrimary,
              border: `1px solid ${theme.border}`,
              borderRadius: 4,
              fontSize: "0.8rem",
              minWidth: 160,
            }}
          />

          <button
            onClick={() => {
              if (selectedTargetBroker) {
                handleTriggerFailover(selectedTargetBroker, failoverReason);
              }
            }}
            disabled={!selectedTargetBroker || failoverMutation.pending}
            style={{
              background: selectedTargetBroker ? theme.accent : theme.border,
              color: selectedTargetBroker ? "#000" : theme.textSecondary,
              border: "none",
              borderRadius: 4,
              padding: "6px 14px",
              fontWeight: 700,
              fontSize: "0.78rem",
              cursor: selectedTargetBroker && !failoverMutation.pending ? "pointer" : "not-allowed",
            }}
          >
            {failoverMutation.pending ? "Failing over..." : "Switch Active Broker"}
          </button>
        </div>
      </div>

      {/* Broker Health & Circuit Breakers Grid */}
      <div>
        <div style={{ fontSize: "0.85rem", fontWeight: 700, marginBottom: 10, display: "flex", alignItems: "center", gap: 6 }}>
          <Activity size={16} color={theme.growth} />
          Broker Adapters & Circuit-Breaker State Machine
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
            gap: 12,
          }}
        >
          {brokerList.map((broker) => {
            const isActive = broker.broker_id === activeBrokerId;
            const circuitBadge = getCircuitBadge(broker.circuit_state);
            const connBadge = getConnectionBadge(broker.connection_state);

            return (
              <div
                key={broker.broker_id}
                style={{
                  background: theme.surface,
                  border: `1px solid ${isActive ? theme.growth : theme.border}`,
                  borderRadius: 6,
                  padding: 12,
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                  position: "relative",
                }}
              >
                {/* Broker Title & Active Indicator */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span style={{ fontSize: "0.95rem", fontWeight: 700 }}>
                      {broker.broker_id.toUpperCase()}
                    </span>
                    {isActive && (
                      <span
                        style={{
                          fontSize: "0.65rem",
                          fontWeight: 800,
                          padding: "2px 6px",
                          borderRadius: 10,
                          background: "rgba(16, 185, 129, 0.2)",
                          color: theme.growth,
                          border: `1px solid ${theme.growth}`,
                        }}
                      >
                        ACTIVE
                      </span>
                    )}
                  </div>

                  <span
                    style={{
                      fontSize: "0.68rem",
                      fontWeight: 700,
                      padding: "2px 6px",
                      borderRadius: 4,
                      background: connBadge.bg,
                      color: connBadge.color,
                    }}
                  >
                    {connBadge.label}
                  </span>
                </div>

                {/* Circuit Breaker State */}
                <div
                  style={{
                    fontSize: "0.68rem",
                    fontWeight: 700,
                    padding: "3px 8px",
                    borderRadius: 4,
                    background: circuitBadge.bg,
                    color: circuitBadge.color,
                    border: `1px solid ${circuitBadge.border}`,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <Zap size={11} />
                  {circuitBadge.label}
                </div>

                {/* Latency & Error Rate Metrics */}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr 1fr",
                    gap: 6,
                    background: theme.base,
                    padding: 8,
                    borderRadius: 4,
                    border: `1px solid ${theme.border}`,
                  }}
                >
                  <div>
                    <div style={{ fontSize: "0.62rem", color: theme.textSecondary }}>Latency</div>
                    <div
                      style={{
                        fontSize: "0.85rem",
                        fontWeight: 700,
                        color: broker.latency_ms < 50 ? theme.growth : broker.latency_ms < 150 ? theme.caution : theme.decline,
                      }}
                    >
                      {broker.latency_ms.toFixed(1)}ms
                    </div>
                  </div>

                  <div>
                    <div style={{ fontSize: "0.62rem", color: theme.textSecondary }}>P95 Latency</div>
                    <div style={{ fontSize: "0.85rem", fontWeight: 700, color: theme.textPrimary }}>
                      {broker.p95_latency_ms.toFixed(1)}ms
                    </div>
                  </div>

                  <div>
                    <div style={{ fontSize: "0.62rem", color: theme.textSecondary }}>Error Rate</div>
                    <div
                      style={{
                        fontSize: "0.85rem",
                        fontWeight: 700,
                        color: broker.error_rate === 0 ? theme.growth : broker.error_rate < 0.05 ? theme.caution : theme.decline,
                      }}
                    >
                      {(broker.error_rate * 100).toFixed(1)}%
                    </div>
                  </div>
                </div>

                {/* Latency Bar visualization */}
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.62rem", color: theme.textSecondary, marginBottom: 2 }}>
                    <span>Latency Bar (Threshold: 500ms)</span>
                    <span>{broker.latency_ms.toFixed(1)}ms</span>
                  </div>
                  <div style={{ width: "100%", height: 4, background: theme.base, borderRadius: 2, overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${Math.min(100, (broker.latency_ms / 300) * 100)}%`,
                        height: "100%",
                        background:
                          broker.latency_ms < 50
                            ? theme.growth
                            : broker.latency_ms < 150
                            ? theme.caution
                            : theme.decline,
                        borderRadius: 2,
                        transition: "width 0.3s ease",
                      }}
                    />
                  </div>
                </div>

                {/* Status message */}
                <div style={{ fontSize: "0.7rem", color: theme.textSecondary, minHeight: 28 }}>
                  {broker.status_message}
                </div>

                {/* Action button */}
                {!isActive && (
                  <button
                    onClick={() => handleTriggerFailover(broker.broker_id, "Manual override route request")}
                    disabled={failoverMutation.pending || !broker.is_routable}
                    style={{
                      marginTop: "auto",
                      background: broker.is_routable ? theme.surface3 : theme.border,
                      border: `1px solid ${broker.is_routable ? theme.accent : theme.border}`,
                      color: broker.is_routable ? theme.accent : theme.textSecondary,
                      borderRadius: 4,
                      padding: "4px 8px",
                      fontSize: "0.72rem",
                      fontWeight: 600,
                      cursor: broker.is_routable && !failoverMutation.pending ? "pointer" : "not-allowed",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: 4,
                    }}
                  >
                    <ArrowRight size={12} />
                    Route Primary Traffic Here
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Recent Routing & Failover Audits Table */}
      {audits.length > 0 && (
        <div style={{ background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 6, padding: 12 }}>
          <div style={{ fontSize: "0.85rem", fontWeight: 700, marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
            <Repeat size={15} color={theme.accent} />
            Recent Multi-Venue Order Routing & Failover Audit Trail
          </div>

          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.75rem" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.border}`, color: theme.textSecondary, textAlign: "left" }}>
                  <th style={{ padding: "6px 8px" }}>Time</th>
                  <th style={{ padding: "6px 8px" }}>Order ID</th>
                  <th style={{ padding: "6px 8px" }}>Symbol</th>
                  <th style={{ padding: "6px 8px" }}>Side / Qty</th>
                  <th style={{ padding: "6px 8px" }}>Primary Broker</th>
                  <th style={{ padding: "6px 8px" }}>Executed Broker</th>
                  <th style={{ padding: "6px 8px" }}>Failover?</th>
                  <th style={{ padding: "6px 8px" }}>Latency</th>
                  <th style={{ padding: "6px 8px" }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {audits.map((a) => (
                  <tr key={a.client_order_id} style={{ borderBottom: `1px solid rgba(255,255,255,0.05)` }}>
                    <td style={{ padding: "6px 8px", color: theme.textSecondary }}>
                      {new Date(a.timestamp).toLocaleTimeString()}
                    </td>
                    <td style={{ padding: "6px 8px", fontFamily: "monospace" }}>{a.client_order_id}</td>
                    <td style={{ padding: "6px 8px", fontWeight: 700 }}>{a.symbol}</td>
                    <td
                      style={{
                        padding: "6px 8px",
                        color: a.side === "BUY" ? theme.growth : theme.decline,
                        fontWeight: 600,
                      }}
                    >
                      {a.side} {a.qty}
                    </td>
                    <td style={{ padding: "6px 8px" }}>{a.primary_broker_id.toUpperCase()}</td>
                    <td style={{ padding: "6px 8px", fontWeight: 700, color: theme.accent }}>
                      {(a.executed_broker_id || a.primary_broker_id).toUpperCase()}
                    </td>
                    <td style={{ padding: "6px 8px" }}>
                      {a.was_failover ? (
                        <span style={{ color: theme.caution, fontWeight: 700 }}>
                          ⚡ YES ({a.failover_reason || "circuit_trip"})
                        </span>
                      ) : (
                        <span style={{ color: theme.textSecondary }}>Direct</span>
                      )}
                    </td>
                    <td style={{ padding: "6px 8px" }}>{a.total_latency_ms.toFixed(1)}ms</td>
                    <td style={{ padding: "6px 8px", color: theme.growth, fontWeight: 700 }}>{a.final_status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};
