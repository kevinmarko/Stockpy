import { useId } from "react";

/**
 * Toggle — an on/off action control, built as `<button role="switch">`, NOT a
 * checkbox. There was no Toggle anywhere in this app before this component;
 * the only precedent was three raw, unstyled `<input type="checkbox">`
 * elements (ActivityFeed, Dashboard, Comparison). A checkbox models a form
 * FIELD (its value is read on submit); this models an ACTION (flipping it
 * fires a mutation immediately — e.g. pause/resume signal generation), so a
 * native `<button>` with `role="switch"`/`aria-checked` is the correct
 * semantics and gives Space/Enter activation for free.
 *
 * `pending` is not cosmetic: the round-trip behind a real toggle (e.g. the
 * kill switch) is a network call, not instant. Without a pending state, a
 * double-tap fires the mutation twice. `aria-busy` is the a11y signal;
 * `pointer-events: none` (via CSS on `[aria-busy="true"]`) is the actual guard.
 */
export function Toggle({
  checked,
  onChange,
  label,
  disabled = false,
  pending = false,
  describedBy,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
  pending?: boolean;
  describedBy?: string;
}) {
  const labelId = useId();

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-labelledby={labelId}
      aria-describedby={describedBy}
      aria-busy={pending}
      disabled={disabled || pending}
      className="switch-wrap"
      onClick={() => onChange(!checked)}
    >
      <span className={`switch-track${checked ? " on" : ""}`}>
        <span className="switch-thumb" />
      </span>
      <span id={labelId} className="switch-label">
        {label}
      </span>
    </button>
  );
}
