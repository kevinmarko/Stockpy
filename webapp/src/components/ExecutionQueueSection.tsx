import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import type { ExecutionQueue, ExecutionQueueIntent } from "../api/types";
import { EmptyState, ErrorState, Loading, StaleDataNotice } from "./ui";
import { useExecutionMode } from "./ExecutionModeContext";
import { useMutation } from "../hooks/useMutation";
import { timeAgo } from "../format";
import { theme } from "../theme";
import { SignalContributionPanel } from "./SignalContributionPanel";

/**
 * Read-only view of the gated Robinhood execution queue
 * (`output/execution_queue.json` via GET /execution-queue). This is
 * deliberately NOT an order-placement UI: per execution/queue_builder.py's
 * module contract, only a live Claude Code agent session (the
 * robinhood-execution skill, paper-first with per-trade confirmation) ever
 * calls the Robinhood MCP's place_equity_order tool — there is no server-side
 * path for this component to trigger a real order even if it wanted to.
 *
 * Shared between the Commands screen and the Agentic Trading screen — lifted
 * out of Commands.tsx so the queue view isn't duplicated across both.
 */
export function ExecutionQueueSection() {
  const [isQueueMinimized, setIsQueueMinimized] = useState(false);
  const [filterAction, setFilterAction] = useState("ALL");
  const [filterFollowType, setFilterFollowType] = useState("ALL");
  const [filterStatus, setFilterStatus] = useState("ALL");
  const [minConviction, setMinConviction] = useState(0);
  const [reviewedIds, setReviewedIds] = useState<Set<string>>(new Set());

  const { data, loading, error, status, stale, cachedAt, reload } =
    useApi<ExecutionQueue>(
      () =>
        api.getExecutionQueue({
          action: filterAction,
          follow_type: filterFollowType,
          status_filter: filterStatus,
          min_conviction: minConviction > 0 ? minConviction / 100 : 0,
        }),
      [filterAction, filterFollowType, filterStatus, minConviction]
    );
  const filtersActive =
    filterAction !== "ALL" || filterFollowType !== "ALL" || filterStatus !== "ALL" || minConviction > 0;

  return (
    <div style={{ marginTop: 40 }} className="card card-pad">
      <div className="rail-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h2 style={{ margin: 0, fontSize: "var(--t-title)" }}>Robinhood execution queue</h2>
        </div>
        <button
          onClick={() => setIsQueueMinimized((prev) => !prev)}
          className="btn btn-neutral"
          style={{ padding: "4px 10px", fontSize: "var(--t-caption)" }}
          title={isQueueMinimized ? "Expand Queue" : "Minimize Queue"}
        >
          {isQueueMinimized ? "Expand ▲" : "Minimize ▼"}
        </button>
      </div>
      <p style={{ color: theme.textSecondary, marginTop: "var(--s-1)", marginBottom: "var(--s-3)" }}>
        What's currently staged to trade. To place any of these, ask me in Claude
        Code — I'll run the paper-first, per-trade-confirmed Robinhood flow
        (skills/robinhood-execution). Nothing here is ever placed automatically.
      </p>

      {/* Multi-attribute Filter Controls Bar */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: "var(--s-3)",
          padding: "var(--s-2-5) var(--s-3)",
          marginBottom: "var(--s-4)",
          background: theme.surface,
          border: `1px solid ${theme.border}`,
          borderRadius: "var(--r-sm)",
          fontSize: "var(--t-caption)",
        }}
      >
        <span style={{ fontWeight: 600, color: theme.textPrimary }}>Filters:</span>

        {/* Side Filter */}
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)" }}>
          <label htmlFor="queue-filter-side" style={{ color: theme.textSecondary }}>Side:</label>
          <select
            id="queue-filter-side"
            value={filterAction}
            onChange={(e) => setFilterAction(e.target.value)}
            style={{
              background: theme.base,
              color: theme.textPrimary,
              border: `1px solid ${theme.border}`,
              borderRadius: "var(--r-sm)",
              padding: "3px 8px",
              fontSize: "var(--t-caption)",
            }}
          >
            <option value="ALL">All Sides</option>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </select>
        </div>

        {/* Strategy Filter — options are the REAL attribution values present in
            the queue (advisory / composed / a followed Pilot's id), read from
            `available_follow_types` (always the unfiltered set, so this list
            stays stable across filter changes). Never a hardcoded guess at
            pilot names — those vary per-operator. */}
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)" }}>
          <label htmlFor="queue-filter-strategy" style={{ color: theme.textSecondary }}>Strategy:</label>
          <select
            id="queue-filter-strategy"
            value={filterFollowType}
            onChange={(e) => setFilterFollowType(e.target.value)}
            style={{
              background: theme.base,
              color: theme.textPrimary,
              border: `1px solid ${theme.border}`,
              borderRadius: "var(--r-sm)",
              padding: "3px 8px",
              fontSize: "var(--t-caption)",
            }}
          >
            <option value="ALL">All Strategies</option>
            {(data?.available_follow_types ?? []).map((ft) => (
              <option key={ft} value={ft}>
                {formatFollowType(ft)}
              </option>
            ))}
          </select>
        </div>

        {/* Status Filter */}
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)" }}>
          <label htmlFor="queue-filter-status" style={{ color: theme.textSecondary }}>Status:</label>
          <select
            id="queue-filter-status"
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            style={{
              background: theme.base,
              color: theme.textPrimary,
              border: `1px solid ${theme.border}`,
              borderRadius: "var(--r-sm)",
              padding: "3px 8px",
              fontSize: "var(--t-caption)",
            }}
          >
            <option value="ALL">All Statuses</option>
            <option value="Blocked">Blocked</option>
            <option value="Ready">Ready</option>
          </select>
        </div>

        {/* Min Conviction Range Slider */}
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)", minWidth: 180 }}>
          <label htmlFor="queue-filter-min-conviction" style={{ color: theme.textSecondary }}>
            Min Conviction: {minConviction}%
          </label>
          <input
            id="queue-filter-min-conviction"
            type="range"
            min="0"
            max="100"
            step="5"
            value={minConviction}
            onChange={(e) => setMinConviction(Number(e.target.value))}
            style={{ flex: 1, accentColor: theme.accent }}
          />
        </div>
      </div>

      {!isQueueMinimized && (
        <>
          {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}
          {loading && <Loading lines={2} />}
          {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
          {!loading && !error && data && (
            data.intents.length === 0 ? (
              <EmptyState
                title="No queued orders"
                hint={
                  data.reason ??
                  (filtersActive
                    ? "No execution items match the selected filter criteria."
                    : "The execution queue is empty.")
                }
              />
            ) : (
              <div>
                <div style={{ display: "flex", gap: "var(--s-2)", flexWrap: "wrap", marginBottom: "var(--s-3)" }}>
                  <ModeBadge mode={data.mode} />
                  {data.kill_switch_active && <Chip label="Kill switch ACTIVE" tone="decline" />}
                  {data.stale && <Chip label="Queue is stale" tone="caution" />}
                  <Chip label={`${data.n_placeable}/${data.n_intents} placeable`} tone="muted" />
                  <Chip label={`as of ${timeAgo(data.generated_at)}`} tone="muted" />
                </div>
                
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
                  {data.intents
                    .filter(intent => !reviewedIds.has(intent.client_order_id || `${intent.symbol}-${intent.side}`))
                    .map((intent) => (
                      <IntentRow 
                        key={intent.client_order_id || `${intent.symbol}-${intent.side}`} 
                        intent={intent} 
                        queueGeneratedAt={data.generated_at}
                        onReviewed={() => setReviewedIds(prev => new Set(prev).add(intent.client_order_id || `${intent.symbol}-${intent.side}`))}
                      />
                  ))}
                </div>

                {reviewedIds.size > 0 && (
                  <div style={{ marginTop: "var(--s-4)" }}>
                    <h3 style={{ fontSize: "var(--t-body)", color: theme.textSecondary, marginBottom: "var(--s-2)" }}>Reviewed</h3>
                    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
                      {data.intents
                        .filter(intent => reviewedIds.has(intent.client_order_id || `${intent.symbol}-${intent.side}`))
                        .map((intent) => (
                          <div key={`rev-${intent.client_order_id || `${intent.symbol}-${intent.side}`}`} style={{ padding: "var(--s-2) var(--s-3)", background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: "var(--r-sm)", opacity: 0.7, display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
                            <span style={{ color: theme.growth }}>✓</span>
                            <span style={{ fontWeight: 600 }}>{intent.symbol} {intent.action}</span>
                            <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>Reviewed and logged to decision journal.</span>
                          </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )
          )}
        </>
      )}
    </div>
  );
}

/** Title-cases a real `follow_type` value ("trend-following" -> "Trend Following")
 * for display — the underlying filter value stays the raw string. */
function formatFollowType(value: string): string {
  return value
    .split(/[-_]/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export function ModeBadge({ mode }: { mode: string }) {
  const tone = mode === "live" ? "decline" : mode === "review" ? "caution" : "muted";
  return <Chip label={`mode: ${mode}`} tone={tone} />;
}

export function Chip({
  label,
  tone,
}: {
  label: string;
  tone: "growth" | "decline" | "caution" | "muted";
}) {
  const color = tone === "muted" ? theme.textMuted : theme[tone];
  return (
    <span
      style={{
        fontSize: "var(--t-micro)",
        fontWeight: 600,
        padding: "3px 8px",
        borderRadius: "var(--r-pill)",
        border: `1px solid ${color}`,
        color,
      }}
    >
      {label}
    </span>
  );
}

function IntentRow({ intent, queueGeneratedAt, onReviewed }: { intent: ExecutionQueueIntent; queueGeneratedAt: string | null; onReviewed: () => void }) {
  const { advisoryOnly } = useExecutionMode();
  const [expanded, setExpanded] = useState(false);
  const [actionNotice, setActionNotice] = useState<string | null>(null);

  const mutation = useMutation((actionTaken: "acted" | "passed") =>
    api.logDecision({
      symbol: intent.symbol,
      action_taken: actionTaken,
      signal_action: intent.action,
      conviction: intent.conviction,
      notes: "Reviewed from execution queue"
    })
  );

  const handleAction = async (e: React.MouseEvent, action: "acted" | "passed") => {
    e.stopPropagation();
    const res = await mutation.run(action);
    if (res) {
      if (!advisoryOnly && action === "acted") {
        setActionNotice("To execute, run the robinhood-execution skill in Claude Code");
        // We delay hiding it to let the user read it
        setTimeout(() => onReviewed(), 4000);
      } else {
        onReviewed();
      }
    }
  };

  const size =
    intent.qty !== null
      ? `${intent.qty} sh`
      : intent.target_notional !== null
      ? `$${intent.target_notional.toLocaleString()}`
      : "—";
  return (
    <div
      data-testid="execution-intent-row"
      onClick={() => setExpanded(!expanded)}
      style={{
        padding: "var(--s-2-5) var(--s-3)",
        background: theme.surface,
        border: `1px solid ${theme.border}`,
        borderRadius: "var(--r-sm)",
        cursor: "pointer",
        transition: "background 0.2s"
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--s-2)", flexWrap: "wrap" }}>
        <span style={{ fontSize: "var(--t-caption)", color: theme.textMuted }}>{expanded ? "▼" : "▶"}</span>
        <Link
          to={`/symbol/${encodeURIComponent(intent.symbol)}`}
          style={{ fontWeight: 700, color: theme.textPrimary, textDecoration: "none" }}
        >
          {intent.symbol}
        </Link>
        <span style={{ color: intent.action === "BUY" ? theme.growth : theme.decline, fontWeight: 600, fontSize: "var(--t-caption)" }}>
          {intent.action}
        </span>
        <span style={{ color: theme.textSecondary, fontSize: "var(--t-caption)" }}>{size}</span>
        {intent.conviction !== null && (
          <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
            conviction {(intent.conviction * 100).toFixed(0)}%
          </span>
        )}
        {intent.follow_type && (
          <Chip label={formatFollowType(intent.follow_type)} tone="muted" />
        )}
        <span style={{ marginLeft: "auto" }}>
          {intent.allow_place ? (
            <Chip label="Ready to place" tone="growth" />
          ) : (
            <Chip label="Blocked" tone="caution" />
          )}
        </span>
      </div>
      
      {expanded && (
        <div style={{ marginTop: "var(--s-3)", padding: "var(--s-3)", background: theme.base, borderRadius: "var(--r-sm)" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--s-3)", marginBottom: "var(--s-3)" }}>
            <div>
              <div style={{ color: theme.textSecondary, fontSize: "var(--t-micro)", textTransform: "uppercase", fontWeight: 600 }}>Rationale</div>
              <div style={{ color: theme.textPrimary, fontSize: "var(--t-caption)", marginTop: "var(--s-1)" }}>
                {intent.rationale || "No rationale provided."}
              </div>
            </div>
            <div>
              <div style={{ color: theme.textSecondary, fontSize: "var(--t-micro)", textTransform: "uppercase", fontWeight: 600 }}>Metadata</div>
              <div style={{ color: theme.textPrimary, fontSize: "var(--t-caption)", marginTop: "var(--s-1)", display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
                <div><strong>Strategy:</strong> {intent.strategy || "Unknown"}</div>
                <div><strong>Sources:</strong> {(intent.sources || []).join(", ") || "None"}</div>
                <div><strong>Generated:</strong> {timeAgo(queueGeneratedAt)}</div>
              </div>
            </div>
          </div>
          
          <div style={{ marginTop: "var(--s-3)" }}>
            <SignalContributionPanel symbol={intent.symbol} />
          </div>

          {!intent.allow_place && intent.gate_reasons.length > 0 && (
            <div style={{ color: theme.caution, fontSize: "var(--t-caption)", marginTop: "var(--s-2)", padding: "var(--s-2)", background: "rgba(220, 38, 38, 0.05)", borderRadius: "var(--r-sm)" }}>
              <strong>Gate Reasons:</strong> {intent.gate_reasons.join("; ")}
            </div>
          )}

          <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--s-2)", marginTop: "var(--s-3)" }}>
            {actionNotice ? (
              <div style={{ color: theme.growth, fontSize: "var(--t-caption)", fontWeight: 600 }}>{actionNotice}</div>
            ) : (
              <>
                <button
                  onClick={(e) => handleAction(e, "passed")}
                  disabled={mutation.pending}
                  style={{
                    padding: "6px 12px",
                    borderRadius: "var(--r-sm)",
                    background: "transparent",
                    color: theme.textPrimary,
                    border: `1px solid ${theme.border}`,
                    cursor: mutation.pending ? "not-allowed" : "pointer",
                    fontSize: "var(--t-caption)",
                    fontWeight: 600
                  }}
                >
                  Pass
                </button>
                <button
                  onClick={(e) => handleAction(e, "acted")}
                  disabled={mutation.pending}
                  style={{
                    padding: "6px 12px",
                    borderRadius: "var(--r-sm)",
                    background: theme.accent,
                    color: "#fff",
                    border: "none",
                    cursor: mutation.pending ? "not-allowed" : "pointer",
                    fontSize: "var(--t-caption)",
                    fontWeight: 600
                  }}
                >
                  Mark as Reviewed
                </button>
              </>
            )}
          </div>
          {mutation.error && <div style={{ color: theme.caution, fontSize: "var(--t-caption)", marginTop: "var(--s-1)", textAlign: "right" }}>{mutation.error}</div>}
        </div>
      )}
    </div>
  );
}
