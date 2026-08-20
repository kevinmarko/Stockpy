import React, { useState, useEffect, useMemo } from "react";
import {
  Radio,
  RefreshCw,
  RotateCcw,
  CheckCircle,
  AlertTriangle,
  Server,
  Search,
  Copy,
  Check,
  Clock,
  Layers,
  Sliders,
  X,
  Send,
} from "lucide-react";
import { theme } from "../../theme";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { useMutation } from "../../hooks/useMutation";
import type {
  FixSessionStatusResponse,
  FixSessionState,
  FixVenueRoutingStat,
  FixResetSeqRequest,
  FixTestRequestPayload,
} from "../../api/types";
import DemoDataBadge from "../DemoDataBadge";

export interface FixGatewayStatusRadarProps {
  className?: string;
  onSessionStateChange?: (state: FixSessionState) => void;
  onClose?: () => void;
}

export const FixGatewayStatusRadar: React.FC<FixGatewayStatusRadarProps> = ({
  className = "",
  onSessionStateChange,
  onClose,
}) => {
  const {
    data: sessionData,
    loading,
    reload,
  } = useApi<FixSessionStatusResponse>(() => api.getFixSessionStatus(), []);

  // Action states
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  // Sequence reset modal state
  const [showResetModal, setShowResetModal] = useState<boolean>(false);
  const [resetSeqInput, setResetSeqInput] = useState<number>(1);
  const [resetGapFill, setResetGapFill] = useState<boolean>(false);

  // Search & Filter for Audit Log
  const [logSearchQuery, setLogSearchQuery] = useState<string>("");
  const [logTypeFilter, setLogTypeFilter] = useState<string>("ALL");
  const [selectedVenue, setSelectedVenue] = useState<string | null>(null);

  // Mutations
  const testRequestMutation = useMutation((req?: FixTestRequestPayload) =>
    api.sendFixTestRequest(req)
  );

  const resetSeqMutation = useMutation((req: FixResetSeqRequest) =>
    api.resetFixSequence(req)
  );

  const reconnectMutation = useMutation(() => api.reconnectFixSession());

  // Notify parent of state change if needed
  useEffect(() => {
    if (sessionData && onSessionStateChange) {
      onSessionStateChange(sessionData.state);
    }
  }, [sessionData, onSessionStateChange]);

  // Handlers
  const handleSendTestRequest = async () => {
    setActionMessage(null);
    setActionError(null);
    try {
      const res = await testRequestMutation.run({});
      if (res && res.status === "ok") {
        setActionMessage(
          `Test Request (35=1) verified. Heartbeat response received in ${res.round_trip_ms ?? 1.25} ms.`
        );
        reload();
      } else {
        setActionError(res?.message || "Failed to execute Test Request.");
      }
    } catch (err: any) {
      setActionError(err?.message || "Test Request error.");
    }
  };

  const handleExecuteResetSeq = async () => {
    if (resetSeqInput < 1) return;
    setActionMessage(null);
    setActionError(null);
    try {
      const res = await resetSeqMutation.run({
        new_seq_num: resetSeqInput,
        gap_fill: resetGapFill,
      });
      if (res && res.status === "ok") {
        setActionMessage(
          `Sequence reset (35=4) to #${res.new_seq_num ?? resetSeqInput} applied.`
        );
        setShowResetModal(false);
        reload();
      } else {
        setActionError(res?.message || "Sequence reset failed.");
      }
    } catch (err: any) {
      setActionError(err?.message || "Sequence reset error.");
    }
  };

  const handleReconnectSession = async () => {
    setActionMessage(null);
    setActionError(null);
    try {
      const res = await reconnectMutation.run();
      if (res && res.status === "ok") {
        setActionMessage("FIX 4.4 Session reconnected and synchronized.");
        reload();
      } else {
        setActionError(res?.message || "Reconnect failed.");
      }
    } catch (err: any) {
      setActionError(err?.message || "Reconnect error.");
    }
  };

  const handleCopyLog = (rawText: string, index: number) => {
    navigator.clipboard.writeText(rawText).then(
      () => {
        setCopiedIndex(index);
        setTimeout(() => setCopiedIndex(null), 2000);
      },
      () => {
        // Clipboard writes can be rejected (permission denied, an
        // insecure/sandboxed context, etc.) -- don't claim Copied when it
        // didn't happen, and don't leave an unhandled promise rejection in
        // the console.
      }
    );
  };

  // State Badge Helpers
  const getSessionStateBadge = (state?: FixSessionState) => {
    switch (state) {
      case "ACTIVE":
        return {
          label: "ACTIVE (SYNCHRONIZED)",
          color: theme.growth,
          bg: "rgba(16, 185, 129, 0.15)",
          border: "rgba(16, 185, 129, 0.35)",
          pulse: true,
        };
      case "RESEND_REQUESTED":
      case "GAP_FILL_PROCESSING":
        return {
          label: "GAP RECOVERY (35=2)",
          color: theme.caution,
          bg: "rgba(245, 158, 11, 0.15)",
          border: "rgba(245, 158, 11, 0.35)",
          pulse: true,
        };
      case "CONNECTING":
      case "LOGON_SENT":
      case "LOGON_RECEIVED":
        return {
          label: "CONNECTING (LOGON)",
          color: theme.accent,
          bg: "rgba(56, 189, 248, 0.15)",
          border: "rgba(56, 189, 248, 0.35)",
          pulse: true,
        };
      case "DISCONNECTED":
      case "LOGOUT_SENT":
      case "SUSPENDED":
      default:
        return {
          label: "DISCONNECTED",
          color: theme.decline,
          bg: "rgba(239, 68, 68, 0.15)",
          border: "rgba(239, 68, 68, 0.35)",
          pulse: false,
        };
    }
  };

  const stateBadge = getSessionStateBadge(sessionData?.state);

  // Fallback venues if not populated
  const venues: FixVenueRoutingStat[] = useMemo(() => {
    if (sessionData?.venue_stats && sessionData.venue_stats.length > 0) {
      return sessionData.venue_stats;
    }
    return [
      {
        venue: "NYSE",
        market_center: "New York Stock Exchange",
        status: "ACTIVE",
        base_latency_ms: 1.1,
        current_latency_ms: 1.14,
        fill_rate_pct: 99.4,
        maker_fee: 0.0012,
        taker_fee: 0.003,
        maker_rebate: 0.002,
        liquidity_depth: 125000,
        share_of_flow_pct: 34.2,
      },
      {
        venue: "NASDAQ",
        market_center: "Nasdaq Stock Market",
        status: "ACTIVE",
        base_latency_ms: 0.9,
        current_latency_ms: 0.95,
        fill_rate_pct: 99.8,
        maker_fee: 0.0015,
        taker_fee: 0.003,
        maker_rebate: 0.0025,
        liquidity_depth: 140000,
        share_of_flow_pct: 38.5,
      },
      {
        venue: "BATS",
        market_center: "Cboe BZX Exchange",
        status: "ACTIVE",
        base_latency_ms: 0.7,
        current_latency_ms: 0.72,
        fill_rate_pct: 98.9,
        maker_fee: -0.002,
        taker_fee: 0.0025,
        maker_rebate: 0.002,
        liquidity_depth: 65000,
        share_of_flow_pct: 12.1,
      },
      {
        venue: "IEX",
        market_center: "Investors Exchange (D-Limit)",
        status: "ACTIVE",
        base_latency_ms: 1.8,
        current_latency_ms: 1.85,
        fill_rate_pct: 97.5,
        maker_fee: 0.0,
        taker_fee: 0.0009,
        maker_rebate: 0.0,
        liquidity_depth: 45000,
        share_of_flow_pct: 6.8,
      },
      {
        venue: "ARCA",
        market_center: "NYSE Arca Equities",
        status: "ACTIVE",
        base_latency_ms: 1.2,
        current_latency_ms: 1.23,
        fill_rate_pct: 99.1,
        maker_fee: -0.0022,
        taker_fee: 0.0028,
        maker_rebate: 0.0022,
        liquidity_depth: 85000,
        share_of_flow_pct: 8.4,
      },
    ];
  }, [sessionData?.venue_stats]);

  // Filtered Audit Log
  const filteredLogs = useMemo(() => {
    const rawLogs = sessionData?.audit_log || [];
    return rawLogs.filter((logLine) => {
      const lineStr = typeof logLine === "string" ? logLine : JSON.stringify(logLine);
      if (logTypeFilter !== "ALL") {
        if (logTypeFilter === "HEARTBEAT" && !lineStr.includes("35=0")) return false;
        if (logTypeFilter === "TEST_REQ" && !lineStr.includes("35=1")) return false;
        if (logTypeFilter === "EXEC_REPORT" && !lineStr.includes("35=8")) return false;
        if (logTypeFilter === "NEW_ORDER" && !lineStr.includes("35=D")) return false;
        if (logTypeFilter === "SEQ_RESET" && !lineStr.includes("35=4")) return false;
      }
      if (logSearchQuery.trim()) {
        const q = logSearchQuery.toLowerCase();
        if (!lineStr.toLowerCase().includes(q)) return false;
      }
      return true;
    });
  }, [sessionData?.audit_log, logTypeFilter, logSearchQuery]);

  // Syntax highlighter for raw FIX tags
  const renderHighlightedFix = (fixStr: string) => {
    const parts = fixStr.split(/[|\x01]/).filter(Boolean);
    return (
      <div
        style={{
          fontFamily: "var(--font-mono, monospace)",
          fontSize: "12px",
          lineHeight: "1.6",
          display: "flex",
          flexWrap: "wrap",
          gap: "4px 8px",
        }}
      >
        {parts.map((p, idx) => {
          const eqIdx = p.indexOf("=");
          if (eqIdx === -1) {
            return (
              <span key={idx} style={{ color: theme.textMuted }}>
                {p}
              </span>
            );
          }
          const tag = p.substring(0, eqIdx);
          const val = p.substring(eqIdx + 1);

          let tagColor: string = theme.textSecondary;
          let valColor: string = theme.textPrimary;

          if (tag === "35") {
            // MsgType
            tagColor = theme.accent;
            valColor = "#38bdf8";
          } else if (tag === "34") {
            // SeqNum
            tagColor = theme.caution;
            valColor = "#fbbf24";
          } else if (tag === "49" || tag === "56") {
            // Sender / Target
            tagColor = theme.growth;
            valColor = "#34d399";
          } else if (tag === "55") {
            // Symbol
            tagColor = "#60a5fa";
            valColor = "#93c5fd";
          } else if (tag === "39" || tag === "150") {
            // OrdStatus / ExecType
            tagColor = val === "2" ? theme.growth : theme.caution;
            valColor = val === "2" ? "#10b981" : "#f59e0b";
          } else if (tag === "11" || tag === "37" || tag === "112") {
            // IDs
            tagColor = "#c084fc";
            valColor = "#e9d5ff";
          } else if (tag === "10") {
            // Checksum
            tagColor = theme.textMuted;
            valColor = theme.textMuted;
          }

          return (
            <span
              key={idx}
              style={{
                background: "rgba(255, 255, 255, 0.03)",
                padding: "1px 5px",
                borderRadius: "3px",
                border: `1px solid rgba(255, 255, 255, 0.06)`,
              }}
            >
              <span style={{ color: tagColor, fontWeight: 600 }}>{tag}</span>
              <span style={{ color: theme.textMuted }}>=</span>
              <span style={{ color: valColor }}>{val}</span>
            </span>
          );
        })}
      </div>
    );
  };

  const inSeq = sessionData?.in_seq_num ?? 1;
  const outSeq = sessionData?.out_seq_num ?? 1;
  const gapDepth = sessionData?.gap_queue_depth ?? 0;
  const seqDelta = Math.abs(outSeq - inSeq);

  return (
    <div
      className={className}
      role="region"
      aria-label="FIX 4.4 Session Console & Routing Radar"
      style={{
        background: theme.surface,
        border: `1px solid ${theme.border}`,
        borderRadius: 8,
        padding: 20,
        display: "flex",
        flexDirection: "column",
        gap: 20,
        color: theme.textPrimary,
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
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div
            style={{
              padding: 8,
              borderRadius: 6,
              background: "rgba(56, 189, 248, 0.12)",
              color: theme.accent,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Radio size={20} />
          </div>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <h2
                style={{
                  margin: 0,
                  fontSize: 18,
                  fontWeight: 700,
                  color: theme.textPrimary,
                  letterSpacing: "-0.01em",
                }}
              >
                FIX 4.4 Gateway & Routing Radar
              </h2>
              <DemoDataBadge />
            </div>
            <div
              style={{
                fontSize: 12,
                color: theme.textSecondary,
                marginTop: 2,
                fontFamily: "var(--font-mono, monospace)",
              }}
            >
              Session: {sessionData?.session_id || "FIX.4.4:INVESTYO_PWA->FIX_GATEWAY"}
            </div>
          </div>
        </div>

        {/* Header Right / Status Badge & Global Actions */}
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "5px 12px",
              borderRadius: 20,
              fontSize: 11,
              fontWeight: 700,
              color: stateBadge.color,
              background: stateBadge.bg,
              border: `1px solid ${stateBadge.border}`,
            }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: stateBadge.color,
                boxShadow: stateBadge.pulse
                  ? `0 0 8px ${stateBadge.color}`
                  : "none",
                display: "inline-block",
              }}
            />
            {stateBadge.label}
          </div>

          <button
            onClick={() => reload()}
            disabled={loading}
            aria-label="Refresh FIX Gateway Session"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "6px 12px",
              background: theme.surface2,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 4,
              cursor: loading ? "not-allowed" : "pointer",
              fontSize: 12,
              fontWeight: 500,
            }}
          >
            <RefreshCw
              size={13}
              style={{
                animation: loading ? "spin 1s linear infinite" : "none",
              }}
            />
            Refresh
          </button>

          {onClose && (
            <button
              onClick={onClose}
              aria-label="Close FIX Radar"
              style={{
                background: "transparent",
                border: "none",
                color: theme.textMuted,
                cursor: "pointer",
                padding: 4,
                display: "flex",
                alignItems: "center",
              }}
            >
              <X size={18} />
            </button>
          )}
        </div>
      </div>

      {/* Action Status Feedback Flash */}
      {actionMessage && (
        <div
          role="status"
          aria-live="polite"
          style={{
            padding: "10px 14px",
            borderRadius: 6,
            background: "rgba(16, 185, 129, 0.1)",
            border: `1px solid rgba(16, 185, 129, 0.3)`,
            color: theme.growth,
            fontSize: 13,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <CheckCircle size={16} />
            <span>{actionMessage}</span>
          </div>
          <button
            onClick={() => setActionMessage(null)}
            style={{
              background: "transparent",
              border: "none",
              color: theme.growth,
              cursor: "pointer",
            }}
          >
            ✕
          </button>
        </div>
      )}

      {actionError && (
        <div
          role="alert"
          style={{
            padding: "10px 14px",
            borderRadius: 6,
            background: "rgba(239, 68, 68, 0.1)",
            border: `1px solid rgba(239, 68, 68, 0.3)`,
            color: theme.decline,
            fontSize: 13,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <AlertTriangle size={16} />
            <span>{actionError}</span>
          </div>
          <button
            onClick={() => setActionError(null)}
            style={{
              background: "transparent",
              border: "none",
              color: theme.decline,
              cursor: "pointer",
            }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Sequence Number Synchronizer & Session KPI Cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: 14,
        }}
      >
        {/* Inbound Sequence # */}
        <div
          style={{
            background: theme.surface2,
            border: `1px solid ${theme.border}`,
            borderRadius: 6,
            padding: 14,
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          <div
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: theme.textSecondary,
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            Inbound Sequence (Tag 34)
          </div>
          <div
            style={{
              fontSize: 24,
              fontWeight: 700,
              fontFamily: "var(--font-mono, monospace)",
              color: theme.growth,
            }}
          >
            #{inSeq.toLocaleString()}
          </div>
          <div style={{ fontSize: 11, color: theme.textMuted }}>
            Target: {sessionData?.target_comp_id || "FIX_GATEWAY"}
          </div>
        </div>

        {/* Outbound Sequence # */}
        <div
          style={{
            background: theme.surface2,
            border: `1px solid ${theme.border}`,
            borderRadius: 6,
            padding: 14,
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          <div
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: theme.textSecondary,
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            Outbound Sequence (Tag 34)
          </div>
          <div
            style={{
              fontSize: 24,
              fontWeight: 700,
              fontFamily: "var(--font-mono, monospace)",
              color: theme.accent,
            }}
          >
            #{outSeq.toLocaleString()}
          </div>
          <div style={{ fontSize: 11, color: theme.textMuted }}>
            Sender: {sessionData?.sender_comp_id || "INVESTYO_PWA"}
          </div>
        </div>

        {/* Gap Queue Depth */}
        <div
          style={{
            background: theme.surface2,
            border: `1px solid ${theme.border}`,
            borderRadius: 6,
            padding: 14,
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          <div
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: theme.textSecondary,
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            Gap Queue Depth
          </div>
          <div
            style={{
              fontSize: 24,
              fontWeight: 700,
              fontFamily: "var(--font-mono, monospace)",
              color: gapDepth === 0 ? theme.growth : theme.caution,
            }}
          >
            {gapDepth} {gapDepth === 0 ? "msgs (Clean)" : "buffered"}
          </div>
          <div style={{ fontSize: 11, color: theme.textMuted }}>
            Sequence Delta: {seqDelta} msgs
          </div>
        </div>

        {/* Last Heartbeat Status */}
        <div
          style={{
            background: theme.surface2,
            border: `1px solid ${theme.border}`,
            borderRadius: 6,
            padding: 14,
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          <div
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: theme.textSecondary,
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            Heartbeat Interval
          </div>
          <div
            style={{
              fontSize: 24,
              fontWeight: 700,
              color: theme.textPrimary,
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <Clock size={20} color={theme.accent} />
            {sessionData?.heartbeat_int || 30}s
          </div>
          <div
            style={{
              fontSize: 11,
              color: theme.textMuted,
              fontFamily: "var(--font-mono, monospace)",
            }}
          >
            {sessionData?.last_heartbeat_at
              ? new Date(sessionData.last_heartbeat_at).toLocaleTimeString()
              : "Live Active"}
          </div>
        </div>
      </div>

      {/* Administrative Control Actions */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 10,
          background: theme.surface2,
          padding: "12px 16px",
          borderRadius: 6,
          border: `1px solid ${theme.border}`,
          alignItems: "center",
        }}
      >
        <span
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: theme.textSecondary,
            marginRight: 4,
          }}
        >
          Administrative Actions:
        </span>

        {/* Send Test Request (35=1) */}
        <button
          onClick={handleSendTestRequest}
          disabled={testRequestMutation.pending}
          aria-label="Send FIX Test Request 35=1"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "8px 14px",
            background: theme.accent,
            border: "none",
            borderRadius: 4,
            color: "#000",
            fontWeight: 600,
            fontSize: 12,
            cursor: testRequestMutation.pending ? "not-allowed" : "pointer",
            opacity: testRequestMutation.pending ? 0.7 : 1,
          }}
        >
          <Send size={13} />
          {testRequestMutation.pending
            ? "Verifying Heartbeat..."
            : "Send Test Request (35=1)"}
        </button>

        {/* Reset Sequence (35=4) */}
        <button
          onClick={() => {
            setResetSeqInput(outSeq);
            setShowResetModal(true);
          }}
          disabled={resetSeqMutation.pending}
          aria-label="Open FIX Sequence Reset Dialog"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "8px 14px",
            background: theme.surface,
            border: `1px solid ${theme.borderStrong}`,
            borderRadius: 4,
            color: theme.textPrimary,
            fontWeight: 600,
            fontSize: 12,
            cursor: resetSeqMutation.pending ? "not-allowed" : "pointer",
          }}
        >
          <Sliders size={13} />
          Reset Sequence (35=4)
        </button>

        {/* Reconnect Session */}
        <button
          onClick={handleReconnectSession}
          disabled={reconnectMutation.pending}
          aria-label="Reconnect FIX Session"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "8px 14px",
            background: theme.surface,
            border: `1px solid ${theme.borderStrong}`,
            borderRadius: 4,
            color: theme.textPrimary,
            fontWeight: 600,
            fontSize: 12,
            cursor: reconnectMutation.pending ? "not-allowed" : "pointer",
          }}
        >
          <RotateCcw
            size={13}
            style={{
              animation: reconnectMutation.pending
                ? "spin 1s linear infinite"
                : "none",
            }}
          />
          {reconnectMutation.pending ? "Reconnecting..." : "Reconnect Session"}
        </button>
      </div>

      {/* Sequence Reset Modal / Form */}
      {showResetModal && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="FIX Sequence Reset Console"
          style={{
            background: theme.surface3,
            border: `1px solid ${theme.accent}`,
            borderRadius: 6,
            padding: 16,
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div style={{ fontWeight: 700, fontSize: 14, color: theme.textPrimary }}>
              Operator Sequence Reset (MsgType 35=4)
            </div>
            <button
              onClick={() => setShowResetModal(false)}
              style={{
                background: "transparent",
                border: "none",
                color: theme.textMuted,
                cursor: "pointer",
              }}
            >
              <X size={16} />
            </button>
          </div>

          <p style={{ margin: 0, fontSize: 12, color: theme.textSecondary }}>
            Resets outbound and inbound sequence numbers to synchronize after an
            unrecoverable sequence gap or cold exchange restart.
          </p>

          <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
            <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 8 }}>
              <span>Target Seq # (Tag 36):</span>
              <input
                type="number"
                min={1}
                value={resetSeqInput}
                onChange={(e) => setResetSeqInput(Math.max(1, parseInt(e.target.value) || 1))}
                aria-label="New Sequence Number"
                style={{
                  width: 100,
                  padding: "5px 8px",
                  background: theme.surface,
                  border: `1px solid ${theme.border}`,
                  color: theme.textPrimary,
                  borderRadius: 4,
                  fontFamily: "var(--font-mono, monospace)",
                }}
              />
            </label>

            <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={resetGapFill}
                onChange={(e) => setResetGapFill(e.target.checked)}
              />
              <span>GapFill Mode (Tag 123=Y)</span>
            </label>

            <div style={{ display: "flex", gap: 8, marginLeft: "auto" }}>
              <button
                onClick={() => setShowResetModal(false)}
                style={{
                  padding: "6px 12px",
                  background: "transparent",
                  border: `1px solid ${theme.border}`,
                  borderRadius: 4,
                  color: theme.textSecondary,
                  cursor: "pointer",
                  fontSize: 12,
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleExecuteResetSeq}
                disabled={resetSeqMutation.pending}
                style={{
                  padding: "6px 14px",
                  background: theme.growth,
                  border: "none",
                  borderRadius: 4,
                  color: "#000",
                  fontWeight: 600,
                  cursor: resetSeqMutation.pending ? "not-allowed" : "pointer",
                  fontSize: 12,
                }}
              >
                {resetSeqMutation.pending ? "Applying..." : "Confirm Reset"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Multi-Venue Execution Routing Radar */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 8,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Layers size={16} color={theme.accent} />
            <h3
              style={{
                margin: 0,
                fontSize: 15,
                fontWeight: 700,
                color: theme.textPrimary,
              }}
            >
              Multi-Venue Execution Routing Radar
            </h3>
          </div>
          <span style={{ fontSize: 12, color: theme.textMuted }}>
            5 Active Market Centers • Low Latency Direct Access (DMA)
          </span>
        </div>

        {/* Venue Cards Grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: 12,
          }}
        >
          {venues.map((v) => {
            const isSelected = selectedVenue === v.venue;
            return (
              <div
                key={v.venue}
                onClick={() => setSelectedVenue(isSelected ? null : v.venue)}
                role="button"
                tabIndex={0}
                aria-label={`Inspect Venue ${v.venue}`}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    setSelectedVenue(isSelected ? null : v.venue);
                  }
                }}
                style={{
                  background: isSelected ? "rgba(56, 189, 248, 0.08)" : theme.surface2,
                  border: `1px solid ${isSelected ? theme.accent : theme.border}`,
                  borderRadius: 6,
                  padding: 14,
                  display: "flex",
                  flexDirection: "column",
                  gap: 10,
                  cursor: "pointer",
                  transition: "all 0.15s ease",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                  }}
                >
                  <div>
                    <div
                      style={{
                        fontWeight: 800,
                        fontSize: 16,
                        color: theme.textPrimary,
                        letterSpacing: "0.02em",
                      }}
                    >
                      {v.venue}
                    </div>
                    <div style={{ fontSize: 11, color: theme.textSecondary }}>
                      {v.market_center || v.venue}
                    </div>
                  </div>
                  <span
                    style={{
                      padding: "2px 8px",
                      borderRadius: 12,
                      fontSize: 10,
                      fontWeight: 700,
                      background: "rgba(16, 185, 129, 0.12)",
                      color: theme.growth,
                      border: "1px solid rgba(16, 185, 129, 0.3)",
                    }}
                  >
                    {v.status || "ACTIVE"}
                  </span>
                </div>

                {/* Fill Rate Progress Bar */}
                <div>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: 11,
                      marginBottom: 4,
                    }}
                  >
                    <span style={{ color: theme.textMuted }}>Fill Rate</span>
                    <span style={{ fontWeight: 700, color: v.fill_rate_pct != null ? theme.growth : theme.textMuted }}>
                      {v.fill_rate_pct != null ? `${v.fill_rate_pct}%` : "—"}
                    </span>
                  </div>
                  <div
                    style={{
                      height: 6,
                      background: "rgba(255, 255, 255, 0.08)",
                      borderRadius: 3,
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        width: v.fill_rate_pct != null ? `${Math.min(100, Math.max(0, v.fill_rate_pct))}%` : "0%",
                        height: "100%",
                        background: theme.growth,
                        borderRadius: 3,
                      }}
                    />
                  </div>
                </div>

                {/* Latency & Fee Economics */}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: 8,
                    fontSize: 11,
                    background: "rgba(0, 0, 0, 0.2)",
                    padding: 8,
                    borderRadius: 4,
                  }}
                >
                  <div>
                    <div style={{ color: theme.textMuted }}>Latency</div>
                    <div
                      style={{
                        fontWeight: 700,
                        color:
                          (v.current_latency_ms || v.base_latency_ms) < 1.0
                            ? theme.growth
                            : theme.textPrimary,
                        fontFamily: "var(--font-mono, monospace)",
                      }}
                    >
                      {(v.current_latency_ms || v.base_latency_ms).toFixed(2)} ms
                    </div>
                  </div>
                  <div>
                    <div style={{ color: theme.textMuted }}>Flow Share</div>
                    <div
                      style={{
                        fontWeight: 700,
                        color: theme.accent,
                        fontFamily: "var(--font-mono, monospace)",
                      }}
                    >
                      {v.share_of_flow_pct != null ? `${v.share_of_flow_pct}%` : "—"}
                    </div>
                  </div>
                  <div>
                    <div style={{ color: theme.textMuted }}>Maker Rebate</div>
                    <div
                      style={{
                        fontWeight: 600,
                        color: (v.maker_rebate ?? 0) > 0 ? theme.growth : theme.textSecondary,
                        fontFamily: "var(--font-mono, monospace)",
                      }}
                    >
                      {(v.maker_rebate ?? 0) > 0
                        ? `+$${(v.maker_rebate ?? 0).toFixed(4)}`
                        : `$${v.maker_fee.toFixed(4)}`}
                    </div>
                  </div>
                  <div>
                    <div style={{ color: theme.textMuted }}>Taker Fee</div>
                    <div
                      style={{
                        fontWeight: 600,
                        color: theme.decline,
                        fontFamily: "var(--font-mono, monospace)",
                      }}
                    >
                      ${v.taker_fee.toFixed(4)}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Raw FIX 4.4 Audit Log Viewer */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 12,
          borderTop: `1px solid ${theme.border}`,
          paddingTop: 16,
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 10,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Server size={16} color={theme.accent} />
            <h3
              style={{
                margin: 0,
                fontSize: 15,
                fontWeight: 700,
                color: theme.textPrimary,
              }}
            >
              Raw FIX 4.4 Audit Log Viewer
            </h3>
            <span
              style={{
                fontSize: 11,
                padding: "2px 8px",
                borderRadius: 10,
                background: "rgba(255, 255, 255, 0.08)",
                color: theme.textSecondary,
                fontFamily: "var(--font-mono, monospace)",
              }}
            >
              {filteredLogs.length} events
            </span>
          </div>

          {/* Search & Filter Controls */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                background: theme.surface2,
                border: `1px solid ${theme.border}`,
                borderRadius: 4,
                padding: "4px 8px",
                gap: 6,
              }}
            >
              <Search size={13} color={theme.textMuted} />
              <input
                type="text"
                placeholder="Search tag (e.g. 35=8, 39=2)..."
                value={logSearchQuery}
                onChange={(e) => setLogSearchQuery(e.target.value)}
                aria-label="Filter Audit Logs"
                style={{
                  background: "transparent",
                  border: "none",
                  outline: "none",
                  color: theme.textPrimary,
                  fontSize: 12,
                  width: 170,
                }}
              />
            </div>

            {/* Type filter chips */}
            {["ALL", "HEARTBEAT", "EXEC_REPORT", "TEST_REQ", "NEW_ORDER", "SEQ_RESET"].map(
              (typeKey) => {
                const isActive = logTypeFilter === typeKey;
                return (
                  <button
                    key={typeKey}
                    onClick={() => setLogTypeFilter(typeKey)}
                    style={{
                      padding: "4px 8px",
                      borderRadius: 4,
                      fontSize: 11,
                      fontWeight: 600,
                      cursor: "pointer",
                      background: isActive ? theme.accent : theme.surface2,
                      color: isActive ? "#000" : theme.textSecondary,
                      border: `1px solid ${isActive ? theme.accent : theme.border}`,
                    }}
                  >
                    {typeKey}
                  </button>
                );
              }
            )}
          </div>
        </div>

        {/* Audit Log Stream Container */}
        <div
          role="log"
          aria-label="FIX Message Audit Stream"
          style={{
            background: "#080b0e",
            border: `1px solid ${theme.border}`,
            borderRadius: 6,
            padding: 12,
            maxHeight: 280,
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          {filteredLogs.length === 0 ? (
            <div
              style={{
                textAlign: "center",
                padding: 24,
                color: theme.textMuted,
                fontSize: 13,
              }}
            >
              No matching FIX 4.4 messages found for filter.
            </div>
          ) : (
            filteredLogs.map((rawLog, idx) => {
              const logStr = typeof rawLog === "string" ? rawLog : JSON.stringify(rawLog);
              return (
                <div
                  key={idx}
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    justifyContent: "space-between",
                    gap: 12,
                    background: "rgba(255, 255, 255, 0.02)",
                    padding: "8px 10px",
                    borderRadius: 4,
                    border: "1px solid rgba(255, 255, 255, 0.04)",
                  }}
                >
                  <div style={{ flex: 1, overflowX: "auto" }}>
                    {renderHighlightedFix(logStr)}
                  </div>
                  <button
                    onClick={() => handleCopyLog(logStr, idx)}
                    aria-label={`Copy FIX message ${idx + 1}`}
                    title="Copy Raw FIX string"
                    style={{
                      background: "transparent",
                      border: "none",
                      color: copiedIndex === idx ? theme.growth : theme.textMuted,
                      cursor: "pointer",
                      padding: 4,
                      display: "flex",
                      alignItems: "center",
                    }}
                  >
                    {copiedIndex === idx ? <Check size={14} /> : <Copy size={14} />}
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
};

export default FixGatewayStatusRadar;
