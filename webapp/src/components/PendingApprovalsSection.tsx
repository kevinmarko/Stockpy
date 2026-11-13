import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import type { AgenticApprovalRow, AgenticApprovals, AgenticBurnin, AgenticCycleStatus } from "../api/types";
import { Chip, EmptyState, ErrorState, Loading, Notice, StaleDataNotice } from "./ui";
import { timeAgo } from "../format";
import { theme } from "../theme";

/**
 * "Pending approvals" card for the Agentic Trading screen (four-agent plan,
 * Phase 4). Read-only view of GET /agentic/approvals: the Robinhood trade
 * approvals the agents sent to the operator's phone, each with its validated
 * thesis, plus the latest unattended cycle's status.
 *
 * Deliberately has NO approve/decline buttons: approving happens only in the
 * Robinhood app (an agent or API can never approve), and the states shown are
 * the last ones the Trader agent recorded, not a live Robinhood read, which
 * the card says.
 */
export function PendingApprovalsSection({ refreshToken }: { refreshToken: number }) {
  const { data, loading, error, status, stale, cachedAt, reload } = useApi<AgenticApprovals>(
    () => api.getAgenticApprovals(),
    [refreshToken]
  );

  return (
    <section className="card card-pad" data-testid="pending-approvals" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ marginBottom: "var(--s-2)", paddingBottom: "var(--s-1)", borderBottom: `1px solid ${theme.border}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", flexWrap: "wrap" }}>
          <h2 style={{ margin: 0, fontSize: "var(--t-title)" }}>Pending approvals</h2>
          {data && (
            <Chip
              label={`${data.n_pending} waiting`}
              tone={data.n_pending > 0 ? "caution" : "muted"}
              title="Orders sent to your phone that you haven't approved or declined yet (as last recorded)."
            />
          )}
        </div>
        <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-1)", marginBottom: 0 }}>
          Orders the agents sent to your phone. Nothing trades until you tap Approve in the Robinhood app.
        </p>
      </div>

      {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}
      {loading && <Loading lines={2} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        <>
          {data.burnin && <BurninLine burnin={data.burnin} />}
          {data.last_cycle && <CycleLine cycle={data.last_cycle} />}

          {data.pending.length === 0 ? (
            <EmptyState title="Nothing waiting on you" hint="New approval requests appear here and on your phone." />
          ) : (
            <div className="list">
              {data.pending.map((r) => (
                <ApprovalRow key={r.approval_id} row={r} showThesis />
              ))}
            </div>
          )}

          {data.recent.length > 0 && (
            <>
              <h3 style={{ fontSize: "var(--t-body)", margin: "var(--s-3) 0 var(--s-1)", color: theme.textSecondary }}>
                Recently decided
              </h3>
              <div className="list">
                {data.recent.map((r) => (
                  <ApprovalRow key={r.approval_id} row={r} />
                ))}
              </div>
            </>
          )}

          <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-2)", marginBottom: 0 }}>
            {data.note}
          </p>
        </>
      )}
    </section>
  );
}

const STATE_LABEL: Record<string, { label: string; tone: "growth" | "decline" | "caution" | "muted" }> = {
  pending: { label: "Waiting for you", tone: "caution" },
  approved: { label: "Approved", tone: "growth" },
  auto_approved: { label: "Auto-approved", tone: "growth" },
  declined: { label: "Declined", tone: "decline" },
  expired: { label: "Expired", tone: "muted" },
};

function stateChip(state: string) {
  const s = STATE_LABEL[state] ?? { label: "State unknown", tone: "muted" as const };
  return <Chip label={s.label} tone={s.tone} title={`Recorded state: ${state}`} />;
}

function ApprovalRow({ row, showThesis = false }: { row: AgenticApprovalRow; showThesis?: boolean }) {
  const side = (row.side ?? "").toUpperCase() || "—";
  const qty = row.qty == null ? "qty unknown" : `${row.qty}`;
  return (
    <div className="row" data-testid={`approval-${row.approval_id}`}>
      <div className="row-main">
        <span className="row-title" style={{ fontWeight: 500 }}>
          {side} {qty} {row.symbol ?? "—"}
        </span>
        {showThesis && (
          <div style={{ fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
            {row.thesis ? (
              <>
                <div style={{ color: theme.textSecondary }}>{row.thesis}</div>
                {row.invalidation && (
                  <div style={{ color: theme.textMuted }}>Wrong if: {row.invalidation}</div>
                )}
              </>
            ) : (
              <div style={{ color: theme.textMuted }}>Thesis unavailable (none validated for this order).</div>
            )}
          </div>
        )}
      </div>
      <div className="row-end" style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "var(--s-0-5)" }}>
        {stateChip(row.state)}
        <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
          {showThesis ? `sent ${timeAgo(row.submitted_at)}` : `recorded ${timeAgo(row.recorded_at)}`}
        </span>
      </div>
    </div>
  );
}

function CycleLine({ cycle }: { cycle: AgenticCycleStatus }) {
  const when = timeAgo(cycle.finished_at ?? cycle.started_at);
  const who = cycle.launched_by === "timer" ? "timer" : "manual run";
  if (cycle.status === "failed") {
    return (
      <Notice variant="warn" style={{ marginBottom: "var(--s-2)" }}>
        <span>⚠️</span>
        <span>
          Last agent cycle ({who}, {when}) stopped at the {cycle.failed_stage ?? "unknown"} stage:{" "}
          {cycle.reason ?? "no reason recorded"}. No further orders were sent from it.
        </span>
      </Notice>
    );
  }
  const label =
    cycle.status === "running"
      ? `Agent cycle running (${who}, started ${when})`
      : `Last agent cycle: ${cycle.mode ?? "?"} mode, ${who}, ${when}${cycle.summary_title ? `. ${cycle.summary_title}` : ""}`;
  return (
    <p style={{ color: theme.textSecondary, fontSize: "var(--t-caption)", margin: "0 0 var(--s-2)" }}>{label}</p>
  );
}

/**
 * Agent 5 (Reviewer) verdict. READY is advice only: switching to live stays the
 * operator's own settings change, which the line says.
 */
function BurninLine({ burnin }: { burnin: AgenticBurnin }) {
  const progress =
    burnin.review_days != null && burnin.required_days != null
      ? `day ${burnin.review_days}/${burnin.required_days}`
      : "progress unknown";
  if (burnin.safety_failures.length > 0) {
    return (
      <Notice variant="warn" style={{ marginBottom: "var(--s-2)" }} data-testid="burnin-line">
        <span>⛔</span>
        <span>
          Burn-in safety check failed ({burnin.safety_failures.join(", ")}). Review before any more orders.
        </span>
      </Notice>
    );
  }
  return (
    <div data-testid="burnin-line" style={{ marginBottom: "var(--s-2)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", flexWrap: "wrap" }}>
        <span style={{ fontSize: "var(--t-caption)", color: theme.textSecondary }}>
          Burn-in {progress} (reviewed {burnin.day ?? "—"})
        </span>
        <Chip
          label={burnin.ready ? "Ready for live (your call)" : "Not ready"}
          tone={burnin.ready ? "growth" : "muted"}
          title="Agent 5 (Reviewer) verdict. Going live is always your own settings change."
        />
      </div>
      {!burnin.ready && burnin.unmet.length > 0 && (
        <ul style={{ margin: "var(--s-1) 0 0", paddingLeft: "var(--s-4)", color: theme.textMuted, fontSize: "var(--t-caption)" }}>
          {burnin.unmet.map((u) => (
            <li key={u}>{u}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
