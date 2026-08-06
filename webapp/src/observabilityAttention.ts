/**
 * observabilityAttention.ts — derives "does anything need a look right now"
 * from an already-fetched ObservabilitySummary. Pure and synchronous: no
 * network calls, no new backend fields. Every input already rides on the one
 * composite `GET /observability/summary` response Observability.tsx fetches;
 * Dashboard.tsx fetches the same endpoint to reuse this exact function so the
 * two screens can never disagree about what counts as "needs attention".
 *
 * Deliberately conservative — each check mirrors a threshold this codebase
 * already treats as notable elsewhere, rather than inventing a new one:
 *  - circuit breaker counts are already server-side deduped/classified
 *    (CircuitBreakerSummary.counts)
 *  - heartbeat: same >120s threshold HeartbeatSection already uses for its
 *    "neg" tone (Observability.tsx's HeartbeatSection)
 *  - sizing cap: only an actual ESCALATION event, not routine capping — per
 *    CLAUDE.md, "KELLY_CAP binding is routine for an established Kelly book"
 *    and is explicitly NOT alert-worthy on its own (see
 *    SIZING_CAP_ALERT_ENABLED's own documented rationale)
 *  - macro regime gate off: the legacy GUI already shows "a persistent red
 *    warning banner when the gate is off" — same posture, ported here
 *
 * Never fabricates an item from a null/missing field (CONSTRAINT #4) — a
 * section reporting `reason` (unavailable) contributes nothing here, it is
 * not treated as "something's wrong".
 */
import type { ObservabilitySummary } from "./api/types";

export type AttentionSeverity = "critical" | "warning";

export interface AttentionItem {
  id: string;
  severity: AttentionSeverity;
  label: string;
  /** Anchor id of the relevant Observability.tsx section, for a same-screen link. */
  anchor: string;
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export function deriveAttentionItems(summary: ObservabilitySummary): AttentionItem[] {
  const items: AttentionItem[] = [];

  const { critical, warning } = summary.circuit_breakers.counts;
  if (critical > 0) {
    items.push({
      id: "circuit-critical",
      severity: "critical",
      label: `${plural(critical, "critical circuit breaker")} tripped`,
      anchor: "circuit-breakers",
    });
  }
  if (warning > 0) {
    items.push({
      id: "circuit-warning",
      severity: "warning",
      label: `${plural(warning, "circuit breaker warning")}`,
      anchor: "circuit-breakers",
    });
  }

  if (summary.regime.macro_regime_gate_enabled === false) {
    items.push({
      id: "macro-gate-off",
      severity: "warning",
      label: "Macro regime gate is off — new BUYs run without the macro override",
      anchor: "macro-gate",
    });
  }

  if (summary.portfolio_heat.over_limit) {
    items.push({
      id: "portfolio-heat",
      severity: "critical",
      label: "Portfolio heat is over its configured limit",
      anchor: "portfolio-risk",
    });
  }

  if (summary.risk_gate_blocks.count > 0) {
    items.push({
      id: "risk-gate-blocks",
      severity: "warning",
      label: `${plural(summary.risk_gate_blocks.count, "order")} blocked by the risk gate`,
      anchor: "risk-gate-blocks",
    });
  }

  const escalations = summary.sizing_cap_audit.events.filter(
    (e) => e.binding_constraint === "escalation"
  ).length;
  if (escalations > 0) {
    items.push({
      id: "sizing-cap-escalation",
      severity: "warning",
      label: `${plural(escalations, "position")} under sizing-cap escalation`,
      anchor: "sizing-cap-audit",
    });
  }

  if (summary.heartbeat.age_seconds != null && summary.heartbeat.age_seconds > 120) {
    items.push({
      id: "heartbeat-stale",
      severity: "critical",
      label: "Orchestrator heartbeat is stale",
      anchor: "heartbeat",
    });
  }

  return items;
}
