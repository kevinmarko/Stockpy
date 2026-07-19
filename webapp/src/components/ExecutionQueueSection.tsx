import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import type { ExecutionQueue, ExecutionQueueIntent } from "../api/types";
import { EmptyState, ErrorState, Loading, StaleDataNotice } from "./ui";
import { timeAgo } from "../format";
import { theme } from "../theme";

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
  const { data, loading, error, status, stale, cachedAt, reload } =
    useApi<ExecutionQueue>(() => api.getExecutionQueue(), []);

  return (
    <div style={{ marginTop: 40 }}>
      <div className="rail-head">
        <h2>Robinhood execution queue</h2>
      </div>
      <p style={{ color: theme.textSecondary, marginTop: -4, marginBottom: 16 }}>
        What's currently staged to trade. To place any of these, ask me in Claude
        Code — I'll run the paper-first, per-trade-confirmed Robinhood flow
        (skills/robinhood-execution). Nothing here is ever placed automatically.
      </p>

      {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}
      {loading && <Loading lines={2} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        data.intents.length === 0 ? (
          <EmptyState
            title="No queued orders"
            hint={data.reason ?? "The execution queue is empty."}
          />
        ) : (
          <div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
              <ModeBadge mode={data.mode} />
              {data.kill_switch_active && <Chip label="Kill switch ACTIVE" tone="decline" />}
              {data.stale && <Chip label="Queue is stale" tone="caution" />}
              <Chip label={`${data.n_placeable}/${data.n_intents} placeable`} tone="muted" />
              <Chip label={`as of ${timeAgo(data.generated_at)}`} tone="muted" />
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {data.intents.map((intent) => (
                <IntentRow key={intent.client_order_id || `${intent.symbol}-${intent.side}`} intent={intent} />
              ))}
            </div>
          </div>
        )
      )}
    </div>
  );
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
        fontSize: 11,
        fontWeight: 600,
        padding: "3px 8px",
        borderRadius: 999,
        border: `1px solid ${color}`,
        color,
      }}
    >
      {label}
    </span>
  );
}

function IntentRow({ intent }: { intent: ExecutionQueueIntent }) {
  const size =
    intent.qty !== null
      ? `${intent.qty} sh`
      : intent.target_notional !== null
      ? `$${intent.target_notional.toLocaleString()}`
      : "—";
  return (
    <div
      data-testid="execution-intent-row"
      style={{
        padding: "10px 12px",
        background: theme.surface,
        border: `1px solid ${theme.border}`,
        borderRadius: 8,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <Link
          to={`/symbol/${encodeURIComponent(intent.symbol)}`}
          style={{ fontWeight: 700, color: theme.textPrimary, textDecoration: "none" }}
        >
          {intent.symbol}
        </Link>
        <span style={{ color: intent.action === "BUY" ? theme.growth : theme.decline, fontWeight: 600, fontSize: 12 }}>
          {intent.action}
        </span>
        <span style={{ color: theme.textSecondary, fontSize: 12 }}>{size}</span>
        {intent.conviction !== null && (
          <span style={{ color: theme.textMuted, fontSize: 12 }}>
            conviction {(intent.conviction * 100).toFixed(0)}%
          </span>
        )}
        <span style={{ marginLeft: "auto" }}>
          {intent.allow_place ? (
            <Chip label="Ready to place" tone="growth" />
          ) : (
            <Chip label="Blocked" tone="caution" />
          )}
        </span>
      </div>
      {intent.rationale && (
        <div style={{ color: theme.textSecondary, fontSize: 12, marginTop: 6 }}>{intent.rationale}</div>
      )}
      {!intent.allow_place && intent.gate_reasons.length > 0 && (
        <div style={{ color: theme.caution, fontSize: 12, marginTop: 4 }}>
          {intent.gate_reasons.join(", ")}
        </div>
      )}
    </div>
  );
}
