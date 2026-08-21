/**
 * DynamicCircuitBreakerBadge.tsx
 * ===============================
 * Real-time operational badge and telemetry card for the intraday dynamic circuit breaker.
 *
 * Visualizes:
 * - Animated pulse indicator (Green = NORMAL, Amber = CAUTION, Red = SOFT_HALT / HARD_HALT)
 * - Current circuit breaker state pill
 * - Key risk microstructure metrics:
 *   - Volatility Jump Z-score (Z_σ)
 *   - Volume-Synchronized Probability of Toxicity (VPIN)
 *   - Order Flow Imbalance (OFI)
 *   - Intraday Loss Velocity (dL/dt)
 * - Prominent alert banner if a halt or caution reason is active.
 *
 * Conforms to:
 * - Stockpy design tokens (theme.ts & index.css)
 * - Strict unmount cleanup & isMounted / cancellation guard
 * - Full ARIA semantics for screen readers (role="status", aria-live="polite", aria-label)
 */

import React, { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { CircuitBreakerState, CircuitBreakerStatusResponse } from "../../api/types";
import { timeAgo } from "../../format";
import { theme, alpha } from "../../theme";

export interface DynamicCircuitBreakerBadgeProps {
  /** Optional pre-fetched or controlled status object */
  status?: CircuitBreakerStatusResponse | null;
  /** Whether to render a compact single-pill badge rather than a full card */
  compact?: boolean;
  /** Whether to automatically poll status (default: true when status not supplied) */
  autoRefresh?: boolean;
  /** Poll interval in milliseconds (default: 5000) */
  pollIntervalMs?: number;
  /** Optional container className */
  className?: string;
  /** Optional inline container styles */
  style?: React.CSSProperties;
}

interface StateConfig {
  label: string;
  color: string;
  bg: string;
  pulseClass: string;
  description: string;
}

function getStateConfig(state: CircuitBreakerState): StateConfig {
  switch (state) {
    case "CAUTION":
      return {
        label: "CAUTION",
        color: theme.caution,
        bg: alpha(theme.caution, "20"),
        pulseClass: "pulse-dot",
        description: "Elevated market toxicity or volatility detected",
      };
    case "SOFT_HALT":
      return {
        label: "SOFT HALT",
        color: theme.decline,
        bg: alpha(theme.decline, "20"),
        pulseClass: "pulse-dot",
        description: "New risk-increasing BUY orders paused; exits permitted",
      };
    case "HARD_HALT":
      return {
        label: "HARD HALT",
        color: theme.decline,
        bg: alpha(theme.decline, "25"),
        pulseClass: "pulse-dot",
        description: "Critical breach — all order submissions blocked",
      };
    case "NORMAL":
    default:
      return {
        label: "NORMAL",
        color: theme.growth,
        bg: alpha(theme.growth, "20"),
        pulseClass: "pulse-dot",
        description: "Standard trading conditions — all guardrails clear",
      };
  }
}

export const DynamicCircuitBreakerBadge: React.FC<DynamicCircuitBreakerBadgeProps> = ({
  status: controlledStatus,
  compact = false,
  autoRefresh = true,
  pollIntervalMs = 5000,
  className,
  style,
}) => {
  const [internalStatus, setInternalStatus] = useState<CircuitBreakerStatusResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(!controlledStatus);
  const [error, setError] = useState<string | null>(null);

  const isControlled = controlledStatus !== undefined;
  const activeStatus = isControlled ? controlledStatus : internalStatus;

  useEffect(() => {
    if (isControlled) {
      setLoading(false);
      return;
    }

    let isMounted = true;

    const fetchStatus = async () => {
      try {
        const res = await api.getCircuitBreakerStatus();
        if (isMounted) {
          setInternalStatus(res);
          setError(null);
          setLoading(false);
        }
      } catch (err: unknown) {
        if (isMounted) {
          setError(err instanceof Error ? err.message : "Failed to load circuit breaker status");
          setLoading(false);
        }
      }
    };

    fetchStatus();

    let intervalId: ReturnType<typeof setInterval> | null = null;
    if (autoRefresh && pollIntervalMs > 0) {
      intervalId = setInterval(fetchStatus, pollIntervalMs);
    }

    return () => {
      isMounted = false;
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  }, [isControlled, autoRefresh, pollIntervalMs]);

  if (loading && !activeStatus) {
    return (
      <div
        role="status"
        aria-live="polite"
        aria-label="Loading circuit breaker status"
        data-testid="circuit-breaker-loading"
        className={className}
        style={{
          background: theme.surface,
          border: `1px solid ${theme.border}`,
          borderRadius: compact ? "var(--r-pill)" : "var(--r-md)",
          padding: compact ? "4px 10px" : "16px",
          color: theme.textMuted,
          fontSize: "var(--t-micro)",
          ...style,
        }}
      >
        <span className="pulse-dot" aria-hidden="true" style={{ color: theme.textMuted }} />
        <span>Loading circuit breaker status...</span>
      </div>
    );
  }

  if (error && !activeStatus) {
    return (
      <div
        role="alert"
        data-testid="circuit-breaker-error"
        className={className}
        style={{
          background: alpha(theme.decline, "15"),
          border: `1px solid ${theme.decline}`,
          borderRadius: compact ? "var(--r-pill)" : "var(--r-md)",
          padding: compact ? "4px 10px" : "12px 16px",
          color: theme.decline,
          fontSize: "0.8rem",
          fontWeight: 600,
          ...style,
        }}
      >
        ⚠️ {error}
      </div>
    );
  }

  if (!activeStatus) {
    return null;
  }

  const state = activeStatus.state || "NORMAL";
  const cfg = getStateConfig(state);

  if (compact) {
    return (
      <div
        role="status"
        aria-live="polite"
        aria-label={`Circuit breaker status: ${cfg.label}`}
        data-testid="circuit-breaker-badge-compact"
        className={className}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "6px",
          padding: "4px 10px",
          borderRadius: "var(--r-pill)",
          background: cfg.bg,
          border: `1px solid ${cfg.color}`,
          color: cfg.color,
          fontSize: "var(--t-micro)",
          fontWeight: 700,
          letterSpacing: "0.02em",
          ...style,
        }}
      >
        <span
          className={cfg.pulseClass}
          aria-hidden="true"
          data-testid="circuit-breaker-pulse-dot"
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            backgroundColor: cfg.color,
            boxShadow: `0 0 6px ${cfg.color}`,
            color: cfg.color,
            display: "inline-block",
            flexShrink: 0,
          }}
        />
        <span data-testid="circuit-breaker-state-label">{cfg.label}</span>
      </div>
    );
  }

  return (
    <section
      role="status"
      aria-live="polite"
      aria-label={`Dynamic circuit breaker status: ${cfg.label}`}
      data-testid="circuit-breaker-card"
      className={className}
      style={{
        background: theme.surface,
        border: `1px solid ${state === "NORMAL" ? theme.border : cfg.color}`,
        borderRadius: "var(--r-md)",
        padding: "16px",
        display: "flex",
        flexDirection: "column",
        gap: "12px",
        ...style,
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "8px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontSize: "0.95rem", fontWeight: 700, color: theme.textPrimary }}>
            ⚡ Dynamic Circuit Breaker
          </span>
          <span
            data-testid="circuit-breaker-state-badge"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "2px 8px",
              borderRadius: "var(--r-pill)",
              background: cfg.bg,
              border: `1px solid ${cfg.color}`,
              color: cfg.color,
              fontSize: "0.75rem",
              fontWeight: 700,
              letterSpacing: "0.03em",
            }}
          >
            <span
              className={cfg.pulseClass}
              aria-hidden="true"
              data-testid="circuit-breaker-pulse-dot"
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                backgroundColor: cfg.color,
                boxShadow: `0 0 6px ${cfg.color}`,
                color: cfg.color,
                display: "inline-block",
                flexShrink: 0,
              }}
            />
            <span data-testid="circuit-breaker-state-label">{cfg.label}</span>
          </span>
        </div>

        {activeStatus.updated_at && (
          <span
            data-testid="circuit-breaker-updated-at"
            style={{
              fontSize: "0.75rem",
              color: theme.textMuted,
            }}
          >
            Updated {timeAgo(activeStatus.updated_at)}
          </span>
        )}
      </div>

      {/* Scope note: volatility-jump, VPIN, and loss-velocity are all wired
          to a live, automatic updater when CIRCUIT_BREAKER_ENABLED is on.
          OFI specifically (and therefore the compound OFI+VPIN flash-crash
          shield, which needs both) stays manual-only -- no configured
          market-data provider populates bid/ask size anywhere in this
          codebase, so there is no real order-flow-imbalance signal to
          compute automatically. */}
      <div
        data-testid="circuit-breaker-scope-note"
        style={{
          fontSize: "0.7rem",
          color: theme.textMuted,
          lineHeight: 1.4,
        }}
      >
        Automatic updates cover volatility jumps, VPIN (bar-level toxicity),
        and the loss-velocity brake (live when the operator enables
        CIRCUIT_BREAKER_ENABLED). OFI stays manual-only — no configured data
        provider supplies real bid/ask size — so the compound OFI+VPIN
        flash-crash shield below cannot trigger automatically either;
        reachable via the kill-switch CLI instead.
      </div>

      {/* Metrics Row */}
      <div
        data-testid="circuit-breaker-metrics-grid"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
          gap: "8px",
          background: theme.surface2,
          borderRadius: "var(--r-sm)",
          padding: "10px",
        }}
      >
        <div data-testid="metric-vol-zscore">
          <div style={{ fontSize: "0.7rem", color: theme.textMuted, fontWeight: 600, textTransform: "uppercase" }}>
            Vol Z-Score (5m)
          </div>
          <div
            data-testid="metric-vol-zscore-value"
            style={{
              fontSize: "0.95rem",
              fontWeight: 700,
              color:
                Math.abs(activeStatus.volatility_zscore) > 3.0
                  ? theme.decline
                  : Math.abs(activeStatus.volatility_zscore) > 2.0
                  ? theme.caution
                  : theme.textPrimary,
            }}
          >
            {activeStatus.volatility_zscore >= 0
              ? `+${activeStatus.volatility_zscore.toFixed(2)}σ`
              : `${activeStatus.volatility_zscore.toFixed(2)}σ`}
          </div>
        </div>

        <div data-testid="metric-vpin">
          <div style={{ fontSize: "0.7rem", color: theme.textMuted, fontWeight: 600, textTransform: "uppercase" }}>
            VPIN Toxicity
          </div>
          <div
            data-testid="metric-vpin-value"
            style={{
              fontSize: "0.95rem",
              fontWeight: 700,
              color:
                activeStatus.vpin > 0.40
                  ? theme.decline
                  : activeStatus.vpin > 0.25
                  ? theme.caution
                  : theme.textPrimary,
            }}
          >
            {activeStatus.vpin.toFixed(2)}
          </div>
        </div>

        <div data-testid="metric-ofi">
          <div style={{ fontSize: "0.7rem", color: theme.textMuted, fontWeight: 600, textTransform: "uppercase" }}>
            OFI Imbalance
          </div>
          <div
            data-testid="metric-ofi-value"
            style={{
              fontSize: "0.95rem",
              fontWeight: 700,
              color:
                activeStatus.ofi < -1000
                  ? theme.decline
                  : activeStatus.ofi < -400
                  ? theme.caution
                  : theme.textPrimary,
            }}
          >
            {activeStatus.ofi >= 0
              ? `+${activeStatus.ofi.toFixed(1)}`
              : `${activeStatus.ofi.toFixed(1)}`}
          </div>
        </div>

        <div data-testid="metric-loss-velocity">
          <div style={{ fontSize: "0.7rem", color: theme.textMuted, fontWeight: 600, textTransform: "uppercase" }}>
            Loss Velocity
          </div>
          <div
            data-testid="metric-loss-velocity-value"
            style={{
              fontSize: "0.95rem",
              fontWeight: 700,
              color:
                activeStatus.loss_velocity_per_min < -100
                  ? theme.decline
                  : activeStatus.loss_velocity_per_min < -30
                  ? theme.caution
                  : theme.textPrimary,
            }}
          >
            {activeStatus.loss_velocity_per_min < 0
              ? `-$${Math.abs(activeStatus.loss_velocity_per_min).toFixed(2)}/min`
              : `$${activeStatus.loss_velocity_per_min.toFixed(2)}/min`}
          </div>
        </div>
      </div>

      {/* Active Halt / Caution Reason Alert Banner */}
      {activeStatus.reason && (
        <div
          role="alert"
          data-testid="circuit-breaker-reason-alert"
          style={{
            background: cfg.bg,
            border: `1px solid ${cfg.color}`,
            borderRadius: "var(--r-sm)",
            padding: "8px 12px",
            color: cfg.color,
            fontSize: "0.8rem",
            display: "flex",
            alignItems: "flex-start",
            gap: "8px",
          }}
        >
          <span aria-hidden="true" style={{ fontSize: "1.1rem", lineHeight: 1 }}>
            {state === "NORMAL" ? "ℹ️" : state === "CAUTION" ? "⚠️" : "🛑"}
          </span>
          <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
            <span style={{ fontWeight: 700 }}>
              {state === "HARD_HALT"
                ? "CRITICAL HARD HALT TRIGGERED"
                : state === "SOFT_HALT"
                ? "PROTECTIVE SOFT HALT ACTIVE"
                : "CAUTION NOTICE"}
            </span>
            <span
              data-testid="circuit-breaker-reason-text"
              style={{ color: theme.textPrimary, fontSize: "0.75rem" }}
            >
              {activeStatus.reason}
            </span>
          </div>
        </div>
      )}
    </section>
  );
};
