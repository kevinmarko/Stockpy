import type { ReactNode } from "react";
import type { Headline, PilotCategory } from "../api/types";
import { fmtNum, fmtPct } from "../format";

/** Category chip. */
export function CategoryChip({ category }: { category: PilotCategory }) {
  return <span className="chip">{category}</span>;
}

/**
 * Deployable / not-deployable honesty badge. Never softened. `deployable` is
 * `null` for a Pilot with no backtest yet at all (vs. `false` for one that
 * failed a gate) — both render the same "not deployable" treatment here;
 * `null` is falsy so the ternary already does the right thing.
 */
export function DeployableBadge({ deployable }: { deployable: boolean | null }) {
  return deployable ? (
    <span className="badge badge-good" title="Passes PBO/DSR/Sharpe/MaxDD gates">
      ● Deployable
    </span>
  ) : (
    <span
      className="badge badge-bad"
      title="Fails a validation gate — not deployable"
    >
      ▲ Not deployable
    </span>
  );
}

/** Small labelled metric badge for PBO / DSR honesty row. */
export function MetricBadge({
  label,
  value,
  good,
}: {
  label: string;
  value: string;
  good?: boolean | null;
}) {
  const cls =
    good == null ? "badge badge-neutral" : good ? "badge badge-good" : "badge badge-warn";
  return (
    <span className={cls}>
      {label} {value}
    </span>
  );
}

/**
 * The honesty row: DSR / PBO / Sharpe / MaxDD read straight off the validation
 * summary. `null` renders "—", never a fabricated value.
 */
export function HonestyRow({ h }: { h: Headline }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
      <DeployableBadge deployable={h.deployable} />
      <MetricBadge
        label="DSR"
        value={h.dsr == null ? "—" : fmtNum(h.dsr, 3)}
        good={h.dsr == null ? null : h.dsr > 0.95}
      />
      <MetricBadge
        label="PBO"
        value={h.pbo == null ? "—" : fmtNum(h.pbo, 2)}
        good={h.pbo == null ? null : h.pbo < 0.5}
      />
      <MetricBadge
        label="Sharpe"
        value={h.sharpe == null ? "—" : fmtNum(h.sharpe, 2)}
        good={h.sharpe == null ? null : h.sharpe > 0.5}
      />
      <MetricBadge
        label="Max DD"
        value={h.max_drawdown == null ? "—" : fmtPct(h.max_drawdown, 0, { fromFraction: true })}
        good={h.max_drawdown == null ? null : h.max_drawdown < 0.3}
      />
    </div>
  );
}

export function Tile({
  label,
  value,
  tone,
}: {
  label: string;
  value: ReactNode;
  tone?: "pos" | "neg";
}) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className={`tile-value num ${tone ?? ""}`}>{value}</div>
    </div>
  );
}

export function Loading({ lines = 3 }: { lines?: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: 72 }} />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  hint,
}: {
  title: string;
  hint?: string;
}) {
  return (
    <div className="empty">
      <div style={{ fontSize: 15, fontWeight: 600, color: "var(--text-secondary)" }}>
        {title}
      </div>
      {hint && <div style={{ marginTop: 6 }}>{hint}</div>}
    </div>
  );
}

/** Distinguishes an honest "not run yet" 404 from a hard error. */
export function ErrorState({
  message,
  status,
  onRetry,
}: {
  message: string;
  status: number | null;
  onRetry?: () => void;
}) {
  const isColdStart = status === 404;
  return (
    <div className="empty">
      <div style={{ fontSize: 15, fontWeight: 600, color: "var(--text-secondary)" }}>
        {isColdStart ? "Nothing here yet" : "Couldn't load"}
      </div>
      <div style={{ marginTop: 6 }}>
        {isColdStart
          ? "Run the Stockpy pipeline to produce data, then pull to refresh."
          : message}
      </div>
      {onRetry && !isColdStart && (
        <button className="btn" style={{ marginTop: 16 }} onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
