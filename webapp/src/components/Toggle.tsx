import { useId, useState, useEffect, type ReactNode } from "react";
import toast from "react-hot-toast";

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
  dataTestId,
}: {
  checked: boolean;
  onChange: (next: boolean) => Promise<void> | void;
  label: ReactNode;
  disabled?: boolean;
  pending?: boolean;
  describedBy?: string;
  dataTestId?: string;
}) {
  const labelId = useId();
  const [optimisticChecked, setOptimisticChecked] = useState(checked);
  const [isMutating, setIsMutating] = useState(false);

  // Sync with prop when not mutating
  useEffect(() => {
    if (!isMutating) setOptimisticChecked(checked);
  }, [checked, isMutating]);

  const handleChange = async () => {
    const next = !optimisticChecked;
    setOptimisticChecked(next);
    setIsMutating(true);
    try {
      const result = onChange(next);
      if (result instanceof Promise) {
        await result;
      }
    } catch (err: any) {
      setOptimisticChecked(!next);
      toast.error(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Update failed</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
            {err.message || "Could not save toggle state."}
          </span>
        </div>
      );
    } finally {
      setIsMutating(false);
    }
  };

  const busy = pending || isMutating;

  return (
    <button
      type="button"
      role="switch"
      aria-checked={optimisticChecked}
      aria-labelledby={labelId}
      aria-describedby={describedBy}
      aria-busy={busy}
      disabled={disabled || busy}
      className="switch-wrap"
      onClick={handleChange}
      data-testid={dataTestId}
    >
      <span className={`switch-track${optimisticChecked ? " on" : ""}`}>
        <span className="switch-thumb" />
      </span>
      <span id={labelId} className="switch-label">
        {label}
      </span>
    </button>
  );
}
