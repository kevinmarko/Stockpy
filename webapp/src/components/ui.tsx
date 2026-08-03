import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ChangeEvent,
  type CSSProperties,
  type ReactNode,
  type TableHTMLAttributes,
} from "react";
import type { Headline, PilotCategory } from "../api/types";
import { fmtNum, fmtPct, timeAgo } from "../format";
import { categoryColor } from "../theme";

/**
 * InfoTip — touch-accessible replacement for native `title="..."` attributes.
 * A native `title` only ever shows on mouse hover; it never fires on tap, so
 * on this PWA (primarily used on phones) that content was silently invisible
 * to nearly every user. Opens on click, which fires identically for mouse
 * and touch, and is dismissed by tapping the trigger again, tapping
 * anywhere else, or Escape.
 *
 * The trigger is a real `<button>` (focusable; Enter/Space toggles it for
 * free, no extra keyboard wiring needed) wrapping whatever visible content
 * the caller passes as `children` (typically a badge or chip).
 * `triggerClassName`/`triggerStyle` let it keep looking exactly like the
 * `<span title=...>` it replaces. `ariaLabel` is for triggers with no
 * visible text of their own (e.g. a colored heatmap cell).
 *
 * The bubble is `position: fixed`, measured live off the trigger's
 * `getBoundingClientRect()` — never `position: absolute` inside an in-flow
 * wrapper — so it is never clipped by an ancestor's `overflow: auto/hidden`.
 * Several call sites live inside a horizontally-scrolling `.rail` or a
 * table wrapped in `overflowX: "auto"` (see SectorSelection.tsx), either of
 * which would silently clip an absolutely-positioned popover. It flips
 * above the trigger instead of below when there isn't room, and clamps
 * horizontally so it never runs off a narrow phone viewport.
 *
 * A focusable trigger nested inside ANOTHER native interactive element (a
 * `<button>` or an `<a>`/`<Link>`) is invalid HTML and breaks keyboard/
 * screen-reader behavior. This component's own onClick/onKeyDown always
 * `stopPropagation()`, so it's safe to nest inside a non-native clickable
 * row (`role="button"` on a `div`/`section`, per TradingHub.tsx's existing
 * pattern) — but a call site that can't avoid a REAL ancestor `<button>`/
 * `<a>` (e.g. `DeployableBadge` inside a Marketplace `<Link>` card) must not
 * use this component there at all; see `DeployableBadge`'s `interactive`
 * prop.
 */
export function InfoTip({
  content,
  children,
  triggerClassName,
  triggerStyle,
  ariaLabel,
}: {
  content: ReactNode;
  /** Visible trigger content (e.g. a badge's label). Omit for a trigger with
   * no visible text of its own (e.g. a colored heatmap cell) -- pass
   * `ariaLabel` in that case so the button still has an accessible name. */
  children?: ReactNode;
  triggerClassName?: string;
  triggerStyle?: CSSProperties;
  ariaLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const bubbleRef = useRef<HTMLDivElement>(null);
  const bodyId = useId();

  // Pass 1: position from the trigger's own rect the instant it opens (the
  // bubble isn't in the DOM yet to measure).
  useLayoutEffect(() => {
    if (!open) {
      setCoords(null);
      return;
    }
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    setCoords({ top: rect.bottom + 6, left: rect.left });
  }, [open]);

  // Pass 2: once the bubble is actually in the DOM, clamp/flip against its
  // REAL measured size so it never runs off-screen.
  useLayoutEffect(() => {
    if (!open || !coords) return;
    const bubble = bubbleRef.current;
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!bubble || !rect) return;
    const bw = bubble.offsetWidth;
    const bh = bubble.offsetHeight;
    let top = rect.bottom + 6;
    if (bh && top + bh > window.innerHeight - 8) {
      top = Math.max(8, rect.top - bh - 6);
    }
    let left = rect.left;
    if (bw && left + bw > window.innerWidth - 8) {
      left = window.innerWidth - bw - 8;
    }
    left = Math.max(8, left);
    if (top !== coords.top || left !== coords.left) {
      setCoords({ top, left });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, content]);

  useEffect(() => {
    if (!open) return;
    function handleOutsideMouseDown(e: MouseEvent) {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (bubbleRef.current?.contains(target)) return;
      setOpen(false);
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    function handleDismiss() {
      setOpen(false);
    }
    document.addEventListener("mousedown", handleOutsideMouseDown);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("scroll", handleDismiss, true);
    window.addEventListener("resize", handleDismiss);
    return () => {
      document.removeEventListener("mousedown", handleOutsideMouseDown);
      document.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("scroll", handleDismiss, true);
      window.removeEventListener("resize", handleDismiss);
    };
  }, [open]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={triggerClassName}
        style={{
          appearance: "none",
          WebkitAppearance: "none",
          fontFamily: "inherit",
          textAlign: "inherit",
          ...triggerStyle,
        }}
        aria-expanded={open}
        aria-describedby={open ? bodyId : undefined}
        aria-label={ariaLabel}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onKeyDown={(e) => {
          // Escape is handled directly here rather than left to bubble to
          // the document-level listener below: React's stopPropagation()
          // (needed for the Enter/Space case right below) also stops the
          // native event from ever reaching that listener, since focus
          // never leaves this button while the bubble is open.
          if (e.key === "Escape") {
            e.stopPropagation();
            setOpen(false);
            return;
          }
          // Enter/Space on this trigger must not also activate an ancestor
          // clickable row (e.g. OptionsMatrix's DirectiveCard) -- keydown
          // bubbles even though the click it produces is already stopped
          // above.
          e.stopPropagation();
        }}
      >
        {children}
      </button>
      {open && coords && (
        <div
          ref={bubbleRef}
          id={bodyId}
          role="tooltip"
          className="tooltip-bubble"
          style={{ top: coords.top, left: coords.left }}
          onClick={(e) => e.stopPropagation()}
        >
          {content}
        </div>
      )}
    </>
  );
}

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
 *
 * `interactive` (default `true`) gates whether the badge is wrapped in an
 * `InfoTip` explaining the verdict. Set it `false` at any call site that
 * renders this badge inside a REAL native `<button>` or `<a>`/`<Link>` (e.g.
 * `PilotCard`'s Marketplace rail card, `Onboarding`'s Pilot-picker row) —
 * nesting another focusable trigger inside either is invalid HTML and breaks
 * keyboard/screen-reader behavior. Those sites already navigate to a detail
 * view (Pilot Detail, the next onboarding step) where this same badge is
 * NOT nested in a native interactive element, so the explanation is still
 * one tap away.
 */
export function DeployableBadge({
  deployable,
  interactive = true,
}: {
  deployable: boolean | null;
  interactive?: boolean;
}) {
  const good = !!deployable;
  const cls = good ? "badge badge-good" : "badge badge-bad";
  const label = good ? "● Deployable" : "▲ Not deployable";
  const explanation = good
    ? "Passes PBO/DSR/Sharpe/MaxDD gates"
    : "Fails a validation gate — not deployable";

  if (!interactive) {
    return <span className={cls}>{label}</span>;
  }
  return (
    <InfoTip triggerClassName={cls} content={explanation}>
      {label}
    </InfoTip>
  );
}

/** Small labelled metric badge for PBO / DSR honesty row. */
export function MetricBadge({
  label,
  value,
  good,
}: {
  label?: string;
  value?: string;
  good?: boolean | null;
}) {
  const cls =
    good == null ? "badge badge-neutral" : good ? "badge badge-good" : "badge badge-warn";
  return (
    <span className={cls}>
      {label && value ? `${label} ${value}` : label || value}
    </span>
  );
}

/**
 * The honesty row: DSR / PBO / Sharpe / MaxDD read straight off the validation
 * summary. `null` renders "—", never a fabricated value.
 */
export function HonestyRow({ h }: { h: Headline }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)" }}>
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
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
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
      <div style={{ fontSize: "var(--t-subhead)", fontWeight: 600, color: "var(--text-secondary)" }}>
        {title}
      </div>
      {hint && <div style={{ marginTop: "var(--s-1-5)" }}>{hint}</div>}
    </div>
  );
}

/**
 * Shared notice banner — routes through the `.notice`/`.notice-{variant}`
 * classes already declared in index.css, with WAI-ARIA live-region semantics
 * wired in centrally so no call site has to remember to add them itself.
 * `success`/`info` are non-interrupting confirmations
 * (`role="status"` + `aria-live="polite"`); `warn` represents something
 * needing attention — macro-regime-gate-off banners, stale-data notices,
 * mutation failures — and interrupts (`role="alert"` + `aria-live="assertive"`),
 * per WAI-ARIA authoring practices. `children` is rendered as-is (icon span +
 * text span, lists, inline retry buttons, etc.) so this stays a thin
 * accessibility/consistency wrapper, not a new layout contract.
 */
export function Notice({
  variant,
  children,
  style,
  "data-testid": dataTestId,
}: {
  variant: "success" | "warn" | "info";
  children: ReactNode;
  style?: CSSProperties;
  "data-testid"?: string;
}) {
  const isWarn = variant === "warn";
  return (
    <div
      className={`notice notice-${variant}`}
      style={style}
      role={isWarn ? "alert" : "status"}
      aria-live={isWarn ? "assertive" : "polite"}
      data-testid={dataTestId}
    >
      {children}
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
    <Notice
      variant="warn"
      style={{ marginBottom: "var(--s-3)", alignItems: "center" }}
      data-testid="stale-data-notice"
    >
      <span>
        Offline: showing cached data{cachedAt ? ` from ${timeAgo(cachedAt)}` : ""}.
      </span>
      {onRetry && (
        <button
          className="btn"
          onClick={onRetry}
          style={{ marginLeft: "auto", fontSize: "var(--t-caption)", padding: "var(--s-0-5) var(--s-2)" }}
        >
          Retry
        </button>
      )}
    </Notice>
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
      <div style={{ fontSize: "var(--t-subhead)", fontWeight: 600, color: "var(--text-secondary)" }}>
        {isColdStart ? "Nothing here yet" : "Couldn't load"}
      </div>
      <div style={{ marginTop: "var(--s-1-5)" }}>
        {isColdStart
          ? "Run the Stockpy pipeline to produce data, then pull to refresh."
          : message}
      </div>
      {onRetry && !isColdStart && (
        <button className="btn" style={{ marginTop: "var(--s-4)" }} onClick={onRetry}>
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
  placeholder,
  className,
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
  placeholder?: string;
  className?: string;
}) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const hintId = hint ? `${inputId}-hint` : undefined;

  return (
    <div>
      <label
        htmlFor={inputId}
        className="tile-label"
        style={{ display: "block", marginBottom: "var(--s-1-5)" }}
      >
        {label}
      </label>
      <input
        id={inputId}
        className={`input ${className ?? ""}`}
        type={type}
        inputMode={inputMode}
        value={value}
        onChange={onChange}
        disabled={disabled}
        min={min}
        max={max}
        step={step}
        placeholder={placeholder}
        aria-invalid={invalid ? "true" : undefined}
        aria-describedby={hintId}
      />
      {hint && (
        invalid ? (
          <div
            id={hintId}
            style={{
              marginTop: "var(--s-1-5)",
              fontSize: "var(--t-caption)",
              color: "var(--decline)",
            }}
          >
            {hint}
          </div>
        ) : (
          <details id={hintId} style={{ marginTop: "var(--s-1-5)", fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>
            <summary style={{ cursor: "pointer", userSelect: "none", color: "var(--text-secondary)", outline: "none" }}>More info</summary>
            <div style={{ marginTop: "var(--s-1)", lineHeight: 1.4 }}>{hint}</div>
          </details>
        )
      )}
    </div>
  );
}

/**
 * Labeled dropdown — matches `Input`'s contract (label, hint, invalid, id,
 * aria-describedby) so the six ad hoc `<select>`s across the app (provider
 * pickers, sort/filter/metric selectors) get one consistent treatment
 * instead of each hand-rolling its own inline styles + label wiring.
 *
 * `options` is a plain `{ value, label }[]` — every real call site's option
 * list is flat strings (no disabled options, no `<optgroup>`), so there's no
 * need for a raw-children escape hatch.
 *
 * `hideLabel` is for the one real call site (ValidationTrend's inline metric
 * selector) that has no visible caption at all today — rather than force a
 * label into the UI or leave the control with no accessible name, the label
 * text is still supplied but applied via `aria-label` instead of a rendered
 * `<label>`.
 */
export function Select({
  label,
  hideLabel,
  value,
  onChange,
  options,
  invalid,
  hint,
  id,
  disabled,
  testId,
  className,
}: {
  label: string;
  hideLabel?: boolean;
  value: string;
  onChange: (e: ChangeEvent<HTMLSelectElement>) => void;
  options: { value: string; label: string }[];
  invalid?: boolean;
  hint?: string;
  id?: string;
  disabled?: boolean;
  testId?: string;
  className?: string;
}) {
  const autoId = useId();
  const selectId = id ?? autoId;
  const hintId = hint ? `${selectId}-hint` : undefined;

  return (
    <div>
      {!hideLabel && (
        <label
          htmlFor={selectId}
          className="tile-label"
          style={{ display: "block", marginBottom: 6 }}
        >
          {label}
        </label>
      )}
      <div className={`select-wrap ${className ?? ""}`}>
        <select
          id={selectId}
          className={`select ${className ?? ""}`}
          value={value}
          onChange={onChange}
          disabled={disabled}
          aria-invalid={invalid ? "true" : undefined}
          aria-describedby={hintId}
          aria-label={hideLabel ? label : undefined}
          data-testid={testId}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      {hint && (
        invalid ? (
          <div
            id={hintId}
            style={{
              marginTop: 6,
              fontSize: "var(--t-caption)",
              color: "var(--decline)",
            }}
          >
            {hint}
          </div>
        ) : (
          <details id={hintId} style={{ marginTop: 6, fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>
            <summary style={{ cursor: "pointer", userSelect: "none", color: "var(--text-secondary)", outline: "none" }}>More info</summary>
            <div style={{ marginTop: "var(--s-1)", lineHeight: 1.4 }}>{hint}</div>
          </details>
        )
      )}
    </div>
  );
}

/**
 * Labeled multi-line text field — same label/hint/invalid/id/aria-describedby
 * contract as `Input`, for the two ad hoc `<textarea>`s in the app (a free-text
 * decision note, a JSON-blob settings field). `monospace` + `spellCheck` are
 * exposed because the two real call sites genuinely disagree on both (prose
 * note vs. raw JSON) — not speculative knobs.
 */
export function Textarea({
  label,
  value,
  onChange,
  rows = 3,
  placeholder,
  invalid,
  hint,
  id,
  disabled,
  spellCheck,
  monospace,
}: {
  label: string;
  value: string;
  onChange: (e: ChangeEvent<HTMLTextAreaElement>) => void;
  rows?: number;
  placeholder?: string;
  invalid?: boolean;
  hint?: string;
  id?: string;
  disabled?: boolean;
  spellCheck?: boolean;
  monospace?: boolean;
}) {
  const autoId = useId();
  const textareaId = id ?? autoId;
  const hintId = hint ? `${textareaId}-hint` : undefined;

  return (
    <div>
      <label
        htmlFor={textareaId}
        className="tile-label"
        style={{ display: "block", marginBottom: 6 }}
      >
        {label}
      </label>
      <textarea
        id={textareaId}
        className={`textarea${monospace ? " textarea-mono" : ""}`}
        value={value}
        onChange={onChange}
        rows={rows}
        placeholder={placeholder}
        disabled={disabled}
        spellCheck={spellCheck}
        aria-invalid={invalid ? "true" : undefined}
        aria-describedby={hintId}
      />
      {hint && (
        invalid ? (
          <div
            id={hintId}
            style={{
              marginTop: 6,
              fontSize: "var(--t-caption)",
              color: "var(--decline)",
            }}
          >
            {hint}
          </div>
        ) : (
          <details id={hintId} style={{ marginTop: 6, fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>
            <summary style={{ cursor: "pointer", userSelect: "none", color: "var(--text-secondary)", outline: "none" }}>More info</summary>
            <div style={{ marginTop: "var(--s-1)", lineHeight: 1.4 }}>{hint}</div>
          </details>
        )
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

/**
 * Thin wrapper over the `.table` class (index.css) — standardizes th/td
 * padding, header styling, and row borders across the app's dozen
 * hand-rolled tables. Deliberately no `Th`/`Td` subcomponents: every table
 * in this codebase is a plain `<table><thead><tr><th>…` /
 * `<tbody><tr><td>…` structure, and the CSS class alone is enough to
 * standardize them — no header/cell abstraction earns its keep here. Add
 * `className="num"` to a `<th>`/`<td>` for right-aligned tabular-nums (the
 * existing `.num` class, extended by `.table` to also right-align).
 * Callers keep their own `overflowX: "auto"` wrapper div and any per-table
 * `minWidth`/`fontSize` override via `style` — inline styles still win over
 * this class.
 */
export function Table({
  children,
  ...rest
}: { children: ReactNode } & TableHTMLAttributes<HTMLTableElement>) {
  return (
    <table className="table" {...rest}>
      {children}
    </table>
  );
}
