import { Link } from "react-router";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import type { ExecutionQueue as ExecutionQueueData, ExecutionQueueIntent } from "../../api/types";
import { EmptyState, ErrorState, Loading, StaleDataNotice } from "../ui";
import { Chip, ModeBadge } from "../ExecutionQueueSection";
import { theme } from "../../theme";
import { timeAgo } from "../../format";

/**
 * Pilots Manager's own read-only glance at the gated Robinhood execution
 * queue (GET /execution-queue). A simpler sibling of
 * components/ExecutionQueueSection.tsx (the Agentic Trading / Commands
 * screens' fuller filterable + expandable-rationale view) -- this card is
 * unfiltered and non-interactive, matching what a "what's queued right now"
 * summary needs on this screen. Never places an order, and never renders
 * Approve/Reject controls: per execution/queue_builder.py's module contract,
 * only a live Claude Code agent session (the robinhood-execution skill,
 * paper-first with per-trade confirmation) can ever place one -- there is no
 * server-side path for this component to trigger a real trade.
 */
export function ExecutionQueue() {
  const { data, loading, error, status, stale, cachedAt, reload } = useApi<ExecutionQueueData>(
    () => api.getExecutionQueue(),
    []
  );

  return (
    <div className="card card-pad">
      <div className="card-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 className="card-title" style={{ margin: 0 }}>
          Execution Queue
        </h3>
        {data && <ModeBadge mode={data.mode} />}
      </div>
      <div className="card-content" style={{ marginTop: "var(--s-2)" }}>
        {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}
        {loading && <Loading lines={2} />}
        {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
        {!loading && !error && data && data.intents.length === 0 && (
          <EmptyState title="No pending trades." hint={data.reason ?? "The execution queue is empty."} />
        )}
        {!loading && !error && data && data.intents.length > 0 && (
          <>
            <div style={{ display: "flex", gap: "var(--s-2)", flexWrap: "wrap", marginBottom: "var(--s-3)" }}>
              {data.kill_switch_active && <Chip label="Kill switch ACTIVE" tone="decline" />}
              {data.stale && <Chip label="Queue is stale" tone="caution" />}
              <Chip label={`${data.n_placeable}/${data.n_intents} placeable`} tone="muted" />
              {data.generated_at && <Chip label={`as of ${timeAgo(data.generated_at)}`} tone="muted" />}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
              {data.intents.map((intent) => (
                <QueueRow key={intent.client_order_id || `${intent.symbol}-${intent.side}`} intent={intent} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function QueueRow({ intent }: { intent: ExecutionQueueIntent }) {
  const size =
    intent.qty !== null
      ? `${intent.qty} sh`
      : intent.target_notional !== null
      ? `$${intent.target_notional.toLocaleString()}`
      : "—";

  return (
    <div
      data-testid="pilots-queue-row"
      style={{
        padding: "var(--s-2-5) var(--s-3)",
        background: theme.surface,
        border: `1px solid ${theme.border}`,
        borderRadius: "var(--r-sm)",
        display: "flex",
        alignItems: "baseline",
        gap: "var(--s-2)",
        flexWrap: "wrap",
      }}
    >
      <Link
        to={`/symbol/${encodeURIComponent(intent.symbol)}`}
        style={{ fontWeight: 700, color: theme.textPrimary, textDecoration: "none" }}
      >
        {intent.symbol}
      </Link>
      <span
        style={{
          color: intent.action === "BUY" ? theme.growth : theme.decline,
          fontWeight: 600,
          fontSize: "var(--t-caption)",
        }}
      >
        {intent.action}
      </span>
      <span style={{ color: theme.textSecondary, fontSize: "var(--t-caption)" }}>{size}</span>
      {intent.conviction !== null && (
        <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
          conviction {(intent.conviction * 100).toFixed(0)}%
        </span>
      )}
      {intent.follow_type && <Chip label={intent.follow_type} tone="muted" />}
      <span style={{ marginLeft: "auto" }}>
        {intent.allow_place ? <Chip label="Ready to place" tone="growth" /> : <Chip label="Blocked" tone="caution" />}
      </span>
    </div>
  );
}
