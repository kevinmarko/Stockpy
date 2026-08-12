import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { TabGuide } from "../components/TabGuide";
import { Modal } from "../components/Modal";
import { Button, EmptyState, ErrorState, Loading, StaleDataNotice } from "../components/ui";
import { fmtDateTime } from "../format";
import { theme } from "../theme";
import type { LiveTradeProposal } from "../api/types";

/**
 * Simple relative countdown to a future ISO timestamp -- e.g. "expires in
 * 12m". Deliberately a one-shot computation (no ticking interval): given
 * real capital may be involved once this is live, this screen intentionally
 * has no auto-refresh/polling that could look like the page is "handling"
 * itself -- a manual refresh (or reload-on-mount) is enough, matching
 * PaperBroker.tsx's pattern.
 */
function expiresIn(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "unknown";
  const mins = Math.round((then - Date.now()) / 60000);
  if (mins <= 0) return "expired";
  if (mins < 60) return `${mins}m`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.round(hrs / 24)}d`;
}

export function LiveTradeApprovals() {
  const pending = useApi(() => api.getPendingLiveTrades());
  const [confirmAction, setConfirmAction] = useState<{ kind: "approve" | "reject"; proposal: LiveTradeProposal } | null>(null);

  const approveMutation = useMutation((token: string) => api.approveLiveTrade(token));
  const rejectMutation = useMutation((token: string) => api.rejectLiveTrade(token));

  const activeMutation = confirmAction?.kind === "approve" ? approveMutation : rejectMutation;

  const handleConfirm = async () => {
    if (!confirmAction) return;
    const mutation = confirmAction.kind === "approve" ? approveMutation : rejectMutation;
    await mutation.run(confirmAction.proposal.token);
    if (!mutation.error) {
      setConfirmAction(null);
      pending.reload();
    }
  };

  const proposals = pending.data?.proposals ?? [];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", width: "100%", overflow: "hidden" }}>
      <div
        style={{
          padding: "16px 24px",
          borderBottom: `1px solid ${theme.border}`,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexShrink: 0,
        }}
      >
        <h1 style={{ margin: 0, fontSize: 24, fontWeight: 600 }}>Live Trade Approvals</h1>
        <Button variant="neutral" onClick={pending.reload} pending={pending.loading}>
          Refresh
        </Button>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: 24, display: "flex", flexDirection: "column", gap: 24 }}>
        <TabGuide tabKey="live-trade-approvals" />

        {pending.stale && <StaleDataNotice cachedAt={pending.cachedAt} onRetry={pending.reload} />}
        {pending.loading && <Loading lines={3} />}
        {!pending.loading && pending.error && (
          <ErrorState message={pending.error} status={pending.status} onRetry={pending.reload} />
        )}

        {!pending.loading && !pending.error && (
          <div>
            <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 12 }}>Pending Approval</h2>
            {proposals.length === 0 ? (
              <EmptyState
                title="No pending live-trade proposals"
                hint="Nothing is queued for approval right now. Any proposal an MCP tool raises will appear here for you to approve or reject before it can reach the broker."
              />
            ) : (
              <div style={{ background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}`, overflow: "hidden" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
                  <thead>
                    <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                      <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Symbol</th>
                      <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Side</th>
                      <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Qty</th>
                      <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Order Type</th>
                      <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Limit Price</th>
                      <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Strategy</th>
                      <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Proposed At</th>
                      <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Expires</th>
                      <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {proposals.map((p) => (
                      <tr key={p.token} style={{ borderBottom: `1px solid ${theme.border}` }}>
                        <td style={{ padding: "12px 16px", fontWeight: 500 }}>{p.symbol}</td>
                        <td style={{ padding: "12px 16px", color: p.side === "BUY" ? theme.growth : theme.decline }}>{p.side}</td>
                        <td style={{ padding: "12px 16px", textAlign: "right" }}>{p.qty}</td>
                        <td style={{ padding: "12px 16px" }}>{p.order_type}</td>
                        <td style={{ padding: "12px 16px", textAlign: "right" }}>
                          {p.limit_price != null ? `$${p.limit_price.toFixed(2)}` : "—"}
                        </td>
                        <td style={{ padding: "12px 16px" }}>{p.strategy_id}</td>
                        <td style={{ padding: "12px 16px", whiteSpace: "nowrap" }}>{fmtDateTime(p.proposed_at)}</td>
                        <td style={{ padding: "12px 16px", whiteSpace: "nowrap" }}>
                          {expiresIn(p.expires_at) === "expired" ? "expired" : `in ${expiresIn(p.expires_at)}`}
                        </td>
                        <td style={{ padding: "12px 16px", textAlign: "right", whiteSpace: "nowrap" }}>
                          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                            <Button
                              variant="primary"
                              onClick={() => setConfirmAction({ kind: "approve", proposal: p })}
                            >
                              Approve
                            </Button>
                            <Button
                              variant="neutral"
                              onClick={() => setConfirmAction({ kind: "reject", proposal: p })}
                            >
                              Reject
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>

      {confirmAction && (
        <Modal
          ariaLabel={confirmAction.kind === "approve" ? "Approve Live Trade" : "Reject Live Trade"}
          onClose={() => setConfirmAction(null)}
        >
          <div style={{ padding: 24 }}>
            <h2 style={{ margin: "0 0 16px 0" }}>
              {confirmAction.kind === "approve" ? "Approve" : "Reject"} {confirmAction.proposal.symbol} {confirmAction.proposal.side}
            </h2>
            <p style={{ margin: "0 0 16px 0", color: theme.textSecondary, lineHeight: 1.5 }}>
              {confirmAction.kind === "approve"
                ? `This will approve a REAL order for ${confirmAction.proposal.qty} share(s) of ${confirmAction.proposal.symbol} (${confirmAction.proposal.side}, ${confirmAction.proposal.order_type}${confirmAction.proposal.limit_price != null ? ` @ $${confirmAction.proposal.limit_price.toFixed(2)}` : ""}), letting it proceed to your live brokerage account.`
                : `This will reject the proposed ${confirmAction.proposal.side} of ${confirmAction.proposal.qty} share(s) of ${confirmAction.proposal.symbol}. It will never reach the broker.`}
            </p>
            {activeMutation.error && (
              <div style={{ padding: 12, background: "rgba(239, 68, 68, 0.1)", color: theme.decline, borderRadius: 4, marginBottom: 16 }}>
                {activeMutation.error}
              </div>
            )}
            <div style={{ display: "flex", gap: 12, justifyContent: "flex-end" }}>
              <button
                onClick={() => setConfirmAction(null)}
                style={{
                  padding: "8px 16px",
                  background: "transparent",
                  border: "none",
                  color: theme.textSecondary,
                  cursor: "pointer",
                  fontWeight: 500,
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleConfirm}
                disabled={activeMutation.pending}
                style={{
                  padding: "8px 16px",
                  background: confirmAction.kind === "approve" ? theme.growth : theme.decline,
                  border: "none",
                  color: "#fff",
                  borderRadius: 4,
                  cursor: activeMutation.pending ? "not-allowed" : "pointer",
                  fontWeight: 500,
                }}
              >
                {activeMutation.pending
                  ? confirmAction.kind === "approve"
                    ? "Approving..."
                    : "Rejecting..."
                  : confirmAction.kind === "approve"
                    ? "Approve"
                    : "Reject"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
