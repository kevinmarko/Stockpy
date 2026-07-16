import { useId, type ButtonHTMLAttributes, type ChangeEvent, type ReactNode } from "react";
import type { Headline, PilotCategory } from "../api/types";
import { fmtNum, fmtPct, timeAgo } from "../format";
import { categoryColor } from "../theme";

/**
 * Category chip — a colored dot (validated categorical palette, see theme.ts)
 * plus the category name, which is ALWAYS rendered as visible text so identity
 * is never color-alone (mirrors SectorDonut's dot+label legend pattern).
 */
export function CategoryChip({ category }: { category: PilotCategory }) {
  return (
    <span className="chip">
      <span
        aria-hidden
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: categoryColor(category),
          flex: "0 0 auto",
        }}
      />
      {category}
    </span>
  );
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

/**
 * Shown when `useApi` served a GET from the localStorage offline-cache
 * fallback (client.ts) instead of a live response — generalizes Dashboard's
 * ad hoc "Offline: using cached data" notice to any screen.
 */
export function StaleDataNotice({
  cachedAt,
  onRetry,
}: {
  cachedAt?: string | null;
  onRetry?: () => void;
}) {
  return (
    <div
      className="notice notice-warn"
      style={{ marginBottom: 12, alignItems: "center" }}
      data-testid="stale-data-notice"
    >
      <span>
        Offline: showing cached data{cachedAt ? ` from ${timeAgo(cachedAt)}` : ""}.
      </span>
      {onRetry && (
        <button
          className="btn"
          onClick={onRetry}
          style={{ marginLeft: "auto", fontSize: 12, padding: "2px 8px" }}
        >
          Retry
        </button>
      )}
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

/**
 * Normal-sized text/number input — a SIBLING to the `.field` class, not a
 * replacement. `.field` is deliberately money-styled (22px/700/tabular-nums)
 * for the Follow amount input; leave it alone. This is for everything else
 * (e.g. a schedule interval, a pause reason) where 22px/700 would be wrong.
 * `--t-input` is a 16px hard floor (see the index.css token comment) — below
 * that, iOS Safari auto-zooms the page on focus.
 */
export function Input({
  label,
  value,
  onChange,
  type = "text",
  inputMode,
  invalid,
  hint,
  id,
  disabled,
  min,
  max,
  step,
}: {
  label: string;
  value: string | number;
  onChange: (e: ChangeEvent<HTMLInputElement>) => void;
  type?: "text" | "number" | "email" | "password";
  inputMode?: "text" | "numeric" | "decimal" | "email";
  invalid?: boolean;
  hint?: string;
  id?: string;
  disabled?: boolean;
  min?: number;
  max?: number;
  step?: number;
}) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const hintId = hint ? `${inputId}-hint` : undefined;

  return (
    <div>
      <label
        htmlFor={inputId}
        className="tile-label"
        style={{ display: "block", marginBottom: 6 }}
      >
        {label}
      </label>
      <input
        id={inputId}
        className="input"
        type={type}
        inputMode={inputMode}
        value={value}
        onChange={onChange}
        disabled={disabled}
        min={min}
        max={max}
        step={step}
        aria-invalid={invalid ? "true" : undefined}
        aria-describedby={hintId}
      />
      {hint && (
        <div
          id={hintId}
          style={{
            marginTop: 6,
            fontSize: "var(--t-caption)",
            color: invalid ? "var(--decline)" : "var(--text-muted)",
          }}
        >
          {hint}
        </div>
      )}
    </div>
  );
}

/**
 * Thin wrapper over the `.btn` class — exists so a mutation's `submitting`
 * boolean doesn't get hand-wired at every call site the way FollowModal does
 * (`disabled={submitting}` + a manually-inlined `<span className="spinner"/>`
 * ternary, repeated verbatim wherever a write button appears). `pending` sets
 * both `disabled` and `aria-busy` and swaps the label for the spinner.
 */
export function Button({
  children,
  variant = "neutral",
  block,
  pending,
  disabled,
  onClick,
  type = "button",
  ...rest
}: {
  children: ReactNode;
  variant?: "primary" | "neutral";
  block?: boolean;
  pending?: boolean;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className" | "children">) {
  const cls = [
    "btn",
    variant === "primary" ? "btn-primary" : "btn-neutral",
    block ? "btn-block" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      type={type}
      className={cls}
      disabled={disabled || pending}
      aria-busy={pending}
      onClick={onClick}
      {...rest}
    >
      {pending ? <span className="spinner" /> : children}
    </button>
  );
}
