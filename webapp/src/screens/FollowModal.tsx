import { useState } from "react";
import { api } from "../api/client";
import type { FollowResult, PilotSummary } from "../api/types";
import { fmtPct, fmtUsd } from "../format";
import { theme } from "../theme";

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

  const minAmount = result?.min_amount ?? 100;
  const belowMin = amount < minAmount;

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
            <h2 style={{ margin: "0 0 2px", fontSize: 20 }}>Follow {pilot.name}</h2>
            <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0 }}>
              Allocate a dollar amount to build a proportional, gated order queue.
            </p>

            <label className="tile-label" htmlFor="follow-amount">
              Amount (USD)
            </label>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 22, fontWeight: 700, color: theme.textMuted }}>
                $
              </span>
              <input
                id="follow-amount"
                className="field"
                type="number"
                inputMode="decimal"
                min={minAmount}
                step={0.01}
                value={amount}
                onChange={(e) => setAmount(Math.max(0, Number(e.target.value)))}
              />
            </div>

            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
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

            {belowMin && (
              <p style={{ color: theme.caution, fontSize: 12, marginTop: 8 }}>
                Minimum allocation is {fmtUsd(minAmount)}.
              </p>
            )}

            <div className="notice notice-warn" style={{ marginTop: 16 }}>
              <span>⚠️</span>
              <span>
                This creates a <strong>gated, paper-first order queue you must
                confirm</strong>. No order is placed automatically — the broker path
                stays quarantined until you approve each trade.
              </span>
            </div>

            {error && (
              <p style={{ color: theme.decline, fontSize: 13, marginTop: 12 }}>
                {error}
              </p>
            )}

            <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
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
            <h2 style={{ margin: "0 0 2px", fontSize: 20 }}>Queue preview</h2>
            <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0 }}>
              {fmtUsd(result.follow.amount)} allocated to {pilot.name} across{" "}
              {result.planned_intents.length} planned orders.
            </p>

            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                margin: "8px 0 14px",
              }}
            >
              <span className="tile-label" style={{ margin: 0 }}>
                Execution mode
              </span>
              <span className={`badge ${modeInfo.cls}`}>{modeInfo.label}</span>
            </div>

            <div className="card card-pad" style={{ padding: 0 }}>
              <div className="list" style={{ padding: "0 14px" }}>
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

            <div className="notice notice-info" style={{ marginTop: 14 }}>
              <span>ℹ️</span>
              <span>{result.notice}</span>
            </div>

            <p style={{ color: theme.textMuted, fontSize: 12, marginTop: 10 }}>
              Per-order notional cap{" "}
              {result.notional_cap > 0 ? fmtUsd(result.notional_cap) : "not configured"}.
              {result.queue_written
                ? " Written to the execution queue — confirm it in the robinhood-execution flow."
                : " Nothing written."}
            </p>

            <button
              className="btn btn-primary btn-block"
              style={{ marginTop: 14 }}
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
