import { useState } from "react";
import { api } from "../api/client";
import type { RlhfKpis, RlhfProposal, RlhfReviewSubmitRequest, RlhfSftExportResult, RlhfSummary } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, EmptyState, ErrorState, Loading, Notice, Textarea, Tile } from "./ui";
import { Modal } from "./Modal";
import { theme } from "../theme";
import { fmtNum, timeAgo } from "../format";

const STAR_VALUES = [1, 2, 3, 4, 5] as const;

/**
 * RLHF Calibration Review Queue — nests inside the Agentic Trading screen
 * (see AgenticTrading.tsx), NOT a standalone route, and deliberately never
 * labeled "Calibration" anywhere user-facing: this repo already has an
 * unrelated `/calibration` screen (a statistical reliability curve), and
 * reusing that name here would be confusing.
 *
 * A human operator rates an AI trading agent's hypothetical paper-trade
 * proposals (1-5 stars + an optional corrective comment) to feed a future
 * fine-tuning pass. There is deliberately no "create proposal" UI here —
 * proposals originate only from the agent via an MCP tool + the API.
 */
export function RlhfReviewQueue({ refreshToken }: { refreshToken: number }) {
  const summary = useApi<RlhfSummary>(() => api.getRlhfSummary(), [refreshToken]);
  const [reviewing, setReviewing] = useState<RlhfProposal | null>(null);
  const exportMutation = useMutation(() => api.exportRlhfSft());

  return (
    <section className="card card-pad" style={{ marginTop: "var(--s-4)" }}>
      <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>RLHF Review Queue</h2>
      <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0, marginBottom: "var(--s-3)" }}>
        Rate the agent's hypothetical paper-trade proposals — every rating feeds a future fine-tuning
        pass. Nothing here ever places a real order.
      </p>

      {summary.loading && <Loading lines={2} />}
      {!summary.loading && summary.error && (
        <ErrorState message={summary.error} status={summary.status} onRetry={summary.reload} />
      )}
      {!summary.loading && !summary.error && summary.data && (
        <RlhfSummaryBody data={summary.data} onSelectProposal={setReviewing} exportMutation={exportMutation} />
      )}

      {reviewing && (
        <RlhfReviewModal
          proposal={reviewing}
          onClose={() => setReviewing(null)}
          onReviewed={() => summary.reload()}
        />
      )}
    </section>
  );
}

/**
 * Split out of RlhfReviewQueue so `data: RlhfSummary` (a component prop) is
 * non-null by construction -- avoids the TS closure-narrowing gap where
 * `summary.data.x` re-widens to `T | null` inside the .map() callback below.
 */
function RlhfSummaryBody({
  data,
  onSelectProposal,
  exportMutation,
}: {
  data: RlhfSummary;
  onSelectProposal: (p: RlhfProposal) => void;
  exportMutation: ReturnType<typeof useMutation<[], RlhfSftExportResult>>;
}) {
  return (
    <>
      <RlhfKpiStrip kpis={data.kpis} />

      {data.proposals.length === 0 ? (
        <EmptyState
          title="No proposals to review"
          hint={data.reason ?? "The agent hasn't proposed a paper trade yet."}
        />
      ) : (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "var(--s-2)",
            marginTop: "var(--s-1)",
            marginBottom: "var(--s-3)",
          }}
        >
          {data.proposals.map((p) => (
            <RlhfProposalRow key={p.id} proposal={p} writable={data.writable} onReview={() => onSelectProposal(p)} />
          ))}
        </div>
      )}

      {data.writable ? (
        // Deliberately understated -- this is a housekeeping action, not the
        // section's primary flow. Real export eligibility (which rated
        // proposals qualify) is entirely server-side; the client never
        // second-guesses it.
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2-5)", flexWrap: "wrap" }}>
          <Button variant="neutral" onClick={() => exportMutation.run()} pending={exportMutation.pending}>
            Export to SFT dataset
          </Button>
        </div>
      ) : (
        <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
          RLHF calibration writes are disabled (RLHF_CALIBRATION_ENABLED=false) — reviews and SFT export are
          read-only until an operator re-enables it in .env.
        </p>
      )}
      {exportMutation.error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
          <span>⚠️</span>
          <span>{exportMutation.error}</span>
        </Notice>
      )}
      {exportMutation.result && (
        <Notice variant="success" style={{ marginTop: "var(--s-2-5)" }} data-testid="rlhf-export-result">
          <span>✅</span>
          <span>
            Exported {exportMutation.result.exported_count} proposal
            {exportMutation.result.exported_count === 1 ? "" : "s"} to {exportMutation.result.file}.
          </span>
        </Notice>
      )}
    </>
  );
}

/** The 6 real `RlhfKpis` fields, nothing invented (no "Reward Model Score" /
 *  "Policy KL Divergence" -- this platform computes no such thing). */
function RlhfKpiStrip({ kpis }: { kpis: RlhfKpis }) {
  const distribution = STAR_VALUES.map((v) => `${v}★ ${kpis.rating_distribution[String(v)] ?? 0}`).join("  ·  ");
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-3)", marginBottom: "var(--s-3)" }}>
      <Tile label="Pending review" value={String(kpis.pending_count)} />
      <Tile label="Reviewed" value={String(kpis.reviewed_count)} />
      <Tile
        label="Avg. human rating"
        value={kpis.average_human_rating == null ? "—" : `${fmtNum(kpis.average_human_rating, 2)} ★`}
      />
      <Tile label="Rating distribution" value={distribution} />
      <Tile label="Auto-approved" value={String(kpis.auto_approved_count)} />
      <Tile label="SFT exported" value={String(kpis.sft_exported_count)} />
    </div>
  );
}

function RlhfProposalRow({
  proposal,
  writable,
  onReview,
}: {
  proposal: RlhfProposal;
  writable: boolean;
  onReview: () => void;
}) {
  const actionColor =
    proposal.action === "BUY" ? theme.growth : proposal.action === "SELL" ? theme.decline : theme.textMuted;
  const priceLabel = proposal.price == null ? "—" : `$${proposal.price.toFixed(2)}`;
  const qtyLabel = proposal.quantity == null ? "—" : `${proposal.quantity} sh`;
  const rationale =
    proposal.rationale.length > 160 ? `${proposal.rationale.slice(0, 160)}…` : proposal.rationale;

  return (
    <div
      data-testid="rlhf-proposal-row"
      style={{
        padding: "var(--s-2-5) var(--s-3)",
        background: theme.surface,
        border: `1px solid ${theme.border}`,
        borderRadius: "var(--r-sm)",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--s-2)", flexWrap: "wrap" }}>
        <span style={{ fontWeight: 700, color: theme.textPrimary }}>{proposal.symbol}</span>
        <span style={{ color: actionColor, fontWeight: 600, fontSize: "var(--t-caption)" }}>{proposal.action}</span>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
          confidence {(proposal.confidence * 100).toFixed(0)}%
        </span>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
          {qtyLabel} @ {priceLabel}
        </span>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-micro)" }}>{timeAgo(proposal.created_at)}</span>
        {writable ? (
          <Button
            variant="neutral"
            onClick={onReview}
            style={{ marginLeft: "auto", padding: "var(--s-1) var(--s-2-5)", fontSize: "var(--t-caption)" }}
          >
            Review
          </Button>
        ) : (
          <span style={{ marginLeft: "auto", color: theme.textMuted, fontSize: "var(--t-caption)" }}>
            reviews disabled
          </span>
        )}
      </div>
      <div style={{ color: theme.textSecondary, fontSize: "var(--t-caption)", marginTop: "var(--s-1-5)" }}>
        {rationale}
      </div>
    </div>
  );
}

/** The server's stable failure tags (see types.ts's RlhfReviewSubmitResult
 *  doc comment) mapped to a specific, actionable message -- never a generic
 *  "Request failed" for the two documented cases. */
function friendlyReviewError(raw: string): string {
  if (raw.startsWith("not_found")) {
    return "This proposal no longer exists — refresh the queue and try again.";
  }
  if (raw.startsWith("already_reviewed")) {
    return "This proposal was already reviewed (maybe in another tab) — refresh the queue.";
  }
  if (raw.startsWith("invalid_rating")) {
    return "Rating must be between 1 and 5.";
  }
  return raw;
}

function RlhfReviewModal({
  proposal,
  onClose,
  onReviewed,
}: {
  proposal: RlhfProposal;
  onClose: () => void;
  onReviewed: () => void;
}) {
  const [rating, setRating] = useState<1 | 2 | 3 | 4 | 5 | null>(null);
  const [correction, setCorrection] = useState("");
  const { run, pending, error, result } = useMutation((body: RlhfReviewSubmitRequest) =>
    api.submitRlhfReview(proposal.id, body)
  );

  const submit = async () => {
    if (rating == null) return;
    const r = await run({ human_rating: rating, human_correction: correction.trim() || undefined });
    // Refresh the pending list / KPI strip the moment the write actually
    // lands, rather than deferring to the "Done" click -- the modal itself
    // stays open so the operator still sees the confirmation.
    if (r) onReviewed();
  };

  const actionColor =
    proposal.action === "BUY" ? theme.growth : proposal.action === "SELL" ? theme.decline : theme.textMuted;

  return (
    <Modal ariaLabel={`Review RLHF proposal for ${proposal.symbol}`} onClose={onClose}>
      <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>
        Review proposal — {proposal.symbol}
      </h2>
      <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0, marginBottom: "var(--s-2)" }}>
        <strong style={{ color: actionColor }}>{proposal.action}</strong> · confidence{" "}
        {(proposal.confidence * 100).toFixed(0)}%
        {proposal.price != null && <> · ${proposal.price.toFixed(2)}</>}
      </p>
      <p style={{ color: theme.textPrimary, fontSize: "var(--t-body)", marginTop: 0, marginBottom: "var(--s-2-5)" }}>
        {proposal.rationale}
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", marginBottom: "var(--s-2)" }}>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>RSI(2) {fmtNum(proposal.rsi, 1)}</span>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
          Sentiment {fmtNum(proposal.sentiment_score, 2)}
        </span>
      </div>
      {proposal.extra_context && Object.keys(proposal.extra_context).length > 0 && (
        <div
          data-testid="rlhf-extra-context"
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--s-1-5)",
            marginBottom: "var(--s-3)",
            padding: "var(--s-1-5) var(--s-2)",
            background: theme.surface2,
            border: `1px solid ${theme.border}`,
            borderRadius: "var(--r-sm)",
          }}
        >
          {Object.entries(proposal.extra_context).map(([key, value]) => (
            <span key={key} style={{ color: theme.textMuted, fontSize: "var(--t-micro)" }}>
              <strong style={{ color: theme.textSecondary }}>{key}</strong>:{" "}
              {typeof value === "number" ? fmtNum(value, 2) : String(value)}
            </span>
          ))}
        </div>
      )}

      {result ? (
        <div style={{ marginTop: "var(--s-1)" }}>
          <Notice variant="success" data-testid="rlhf-review-result">
            <span>✅</span>
            <span>
              Rated {result.human_rating} star{result.human_rating === 1 ? "" : "s"}
              {result.sft_exported ? " · exported to the SFT dataset" : ""}.
            </span>
          </Notice>
          <div style={{ display: "flex", marginTop: "var(--s-4)" }}>
            <Button variant="primary" block onClick={onClose}>
              Done
            </Button>
          </div>
        </div>
      ) : (
        <>
          <div
            role="radiogroup"
            aria-label="Star rating"
            style={{ display: "flex", gap: "var(--s-1)", marginBottom: "var(--s-2-5)" }}
          >
            {STAR_VALUES.map((v) => (
              <button
                key={v}
                type="button"
                role="radio"
                aria-checked={rating === v}
                aria-label={`${v} star${v === 1 ? "" : "s"}`}
                onClick={() => setRating(v)}
                style={{
                  appearance: "none",
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  fontSize: 28,
                  lineHeight: 1,
                  padding: 2,
                  color: rating != null && v <= rating ? theme.caution : theme.textMuted,
                }}
              >
                ★
              </button>
            ))}
          </div>

          <Textarea
            id="rlhf-correction"
            label="Corrective comment (optional)"
            value={correction}
            onChange={(e) => setCorrection(e.target.value)}
            rows={3}
            placeholder="e.g. 'Rationale ignored the upcoming earnings date', 'Confidence too high given the macro regime'"
          />

          {rating == null && (
            <Notice variant="info" style={{ marginTop: "var(--s-2-5)" }}>
              <span>ℹ️</span>
              <span>Select a star rating before submitting.</span>
            </Notice>
          )}
          {error && (
            <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
              <span>⚠️</span>
              <span>{friendlyReviewError(error)}</span>
            </Notice>
          )}

          <div style={{ display: "flex", gap: "var(--s-2-5)", marginTop: "var(--s-4)" }}>
            <Button variant="neutral" onClick={onClose} style={{ flex: 1 }}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={submit}
              disabled={rating == null || pending}
              pending={pending}
              style={{ flex: 2 }}
            >
              Submit rating
            </Button>
          </div>
        </>
      )}
    </Modal>
  );
}
