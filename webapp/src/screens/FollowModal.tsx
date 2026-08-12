import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { FollowResult, PilotSummary, Thresholds } from "../api/types";
import { fmtPct, fmtUsd } from "../format";
import { loadThresholds } from "../help/thresholds";
import { theme } from "../theme";
import { Notice } from "../components/ui";
import { resolveMinAmount } from "./resolveMinAmount";

const MODE_LABEL: Record<string, { label: string; cls: string }> = {
  off: { label: "OFF — nothing is written", cls: "badge-neutral" },
  review: { label: "REVIEW — preview only", cls: "badge-warn" },
  paper: { label: "PAPER — simulated fills", cls: "badge-warn" },
  live: { label: "LIVE — real orders (per-trade confirm)", cls: "badge-bad" },
};

/**
 * Follow flow modal. Amount input (min + notional cap), planned_intents preview,
 * execution mode, and an unmissable "this creates a gated queue you must confirm"
 * notice. It NEVER presents a follow as an executed trade.
 */
export function FollowModal({
  pilot,
  onClose,
  onFollowed,
}: {
  pilot: PilotSummary;
  onClose: () => void;
  onFollowed?: (r: FollowResult) => void;
}) {
  const [amount, setAmount] = useState<number>(500);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<FollowResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [thresholds, setThresholds] = useState<Thresholds | null>(null);

  // Lazy, session-cached fetch (see help/thresholds.ts) so the pre-submit
  // minimum-allocation copy quotes the live settings.FOLLOW_MIN_AMOUNT
  // instead of a re-typed literal. `null` (not yet loaded, or the fetch
  // failed) renders "—" via fmtUsd rather than a fabricated guess.
  useEffect(() => {
    let alive = true;
    void loadThresholds().then((t) => {
      if (alive) setThresholds(t);
    });
    return () => {
      alive = false;
    };
  }, []);

  const minAmount = resolveMinAmount(result, thresholds);
  const belowMin = minAmount != null && amount < minAmount;

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const r = await api.follow(pilot.id, amount);
      setResult(r);
      onFollowed?.(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Follow failed");
    } finally {
      setSubmitting(false);
    }
  };

  const mode = result?.mode ?? "review";
  const modeInfo = MODE_LABEL[mode] ?? MODE_LABEL.review;

  // The real sum of planned target notionals -- the honest "what was
  // actually allocated" figure. result.follow.amount is always the raw
  // requested amount (see the comment at its usage site below), so it must
  // never be presented as "allocated" on its own.
  const allocatedTotal =
    result?.planned_intents.reduce((sum, it) => sum + it.target_notional, 0) ?? 0;
  // A cent of slack absorbs floating-point rounding across per-symbol
  // target_notional values, not a fabricated tolerance for a real gap.
  const wasReduced = !!result && result.follow.amount - allocatedTotal > 0.01;

  return (
    <div
      className="sheet-backdrop"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Follow ${pilot.name}`}
    >
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-grip" />

        {!result ? (
          <>
            <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>Follow {pilot.name}</h2>
            <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0 }}>
              Allocate a dollar amount to build a proportional, gated order queue.
            </p>

            <label className="tile-label" htmlFor="follow-amount">
              Amount (USD)
            </label>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
              <span style={{ fontSize: "var(--t-display)", fontWeight: 700, color: theme.textMuted }}>
                $
              </span>
              <input
                id="follow-amount"
                className="field"
                type="number"
                inputMode="decimal"
                min={minAmount ?? undefined}
                step={0.01}
                value={amount}
                onChange={(e) => setAmount(Math.max(0, Number(e.target.value)))}
              />
            </div>

            <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-3)" }}>
              {[250, 500, 1000, 2500].map((a) => (
                <button
                  key={a}
                  className="chip"
                  style={{ flex: 1, justifyContent: "center", minHeight: 40 }}
                  onClick={() => setAmount(a)}
                >
                  ${a}
                </button>
              ))}
            </div>

            {/* Always visible (not just when violated) so the minimum is never
                silently absent — "—" (fmtUsd's null rendering) while the live
                GET /thresholds fetch hasn't resolved yet, never a hardcoded
                literal. */}
            <p
              style={{
                color: belowMin ? theme.caution : theme.textMuted,
                fontSize: "var(--t-caption)",
                marginTop: "var(--s-2)",
              }}
            >
              {belowMin
                ? `Minimum allocation is ${fmtUsd(minAmount)}.`
                : `Minimum allocation: ${fmtUsd(minAmount)}`}
            </p>

            <Notice variant="warn" style={{ marginTop: "var(--s-4)" }}>
              <span>⚠️</span>
              <span>
                This creates a <strong>gated, paper-first order queue you must
                confirm</strong>. No order is placed automatically — the broker path
                stays quarantined until you approve each trade.
              </span>
            </Notice>

            {error && (
              <Notice variant="warn" style={{ marginTop: "var(--s-3)" }}>
                {error}
              </Notice>
            )}

            <div style={{ display: "flex", gap: "var(--s-2-5)", marginTop: "var(--s-4-5)" }}>
              <button className="btn" style={{ flex: 1 }} onClick={onClose}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                style={{ flex: 2 }}
                disabled={submitting || belowMin || amount <= 0}
                onClick={submit}
              >
                {submitting ? <span className="spinner" /> : "Preview queue"}
              </button>
            </div>
          </>
        ) : (
          <>
            <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>Queue preview</h2>
            <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0 }}>
              {fmtUsd(allocatedTotal)} allocated to {pilot.name} across{" "}
              {result.planned_intents.length} planned orders.
            </p>

            {/* result.follow.amount is always the raw REQUESTED amount (the
                backend never rewrites the persisted follow record) -- it is
                NOT what was actually planned whenever sizing and/or the
                per-order notional cap reduced the allocation. Showing the
                requested figure as "allocated" would misstate what actually
                happened, so the headline above uses the real sum of planned
                target notionals instead. This notice surfaces the gap
                honestly without over-attributing it to one specific cause:
                either the position-sizing ceiling (below) or the per-order
                notional cap (already shown further down) could be the
                reason, and this UI has no reliable way to isolate which. */}
            {wasReduced && (
              <Notice variant="warn" style={{ marginTop: "var(--s-2)" }}>
                <span>🛡️</span>
                <span>
                  Your requested {fmtUsd(result.follow.amount)} was reduced to{" "}
                  {fmtUsd(allocatedTotal)} by this Pilot's position-sizing and/or
                  per-order limits.
                </span>
              </Notice>
            )}

            {/* Always visible (not conditional on wasReduced) -- states the
                sizing ceiling in effect this cycle as a plain fact, never a
                causal claim about why any particular reduction happened. */}
            {result.sizing_path && (
              <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-1)" }}>
                Sizing this cycle:{" "}
                {result.kelly_weight != null
                  ? `${fmtPct(result.kelly_weight, 1, { fromFraction: true })} of account equity`
                  : "unavailable"}{" "}
                ({result.sizing_path}).
              </p>
            )}

            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--s-2)",
                margin: "var(--s-2) 0 var(--s-3-5)",
              }}
            >
              <span className="tile-label" style={{ margin: 0 }}>
                Execution mode
              </span>
              <span className={`badge ${modeInfo.cls}`}>{modeInfo.label}</span>
            </div>

            <div className="card card-pad" style={{ padding: 0 }}>
              <div className="list" style={{ padding: "0 var(--s-3-5)" }}>
                {result.planned_intents.map((it) => (
                  <div className="row" key={it.symbol}>
                    <div className="row-main">
                      <span className="row-title">
                        <span
                          className="badge badge-good"
                          style={{ marginRight: 6, padding: "2px 7px" }}
                        >
                          BUY
                        </span>
                        {it.symbol}
                      </span>
                      <span className="row-sub">
                        {fmtPct(it.weight, 1, { fromFraction: true })} of allocation ·
                        conviction {it.conviction.toFixed(2)}
                      </span>
                    </div>
                    <div className="row-end">
                      <div className="num" style={{ fontWeight: 700 }}>
                        {fmtUsd(it.target_notional)}
                      </div>
                      <div
                        className="row-sub"
                        style={{ color: it.allow_place ? theme.caution : theme.textMuted }}
                      >
                        {it.allow_place ? "placeable" : "gated"}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <Notice variant="info" style={{ marginTop: "var(--s-3-5)" }}>
              <span>ℹ️</span>
              <span>{result.notice}</span>
            </Notice>

            <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-2-5)" }}>
              Per-order notional cap{" "}
              {result.notional_cap > 0 ? fmtUsd(result.notional_cap) : "not configured"}.
              {result.queue_written
                ? " Written to the execution queue — confirm it in the robinhood-execution flow."
                : " Nothing written."}
            </p>

            <button
              className="btn btn-primary btn-block"
              style={{ marginTop: "var(--s-3-5)" }}
              onClick={onClose}
            >
              Done
            </button>
          </>
        )}
      </div>
    </div>
  );
}
