import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Modal — the app's reusable dialog scaffold. Extracted to fix a real a11y
 * bug present in both prior copy-pasted dialog implementations (FollowModal,
 * the now-removed PwaStatusDrawer): `role="dialog"`/`aria-modal="true"` were
 * placed on the BACKDROP element, not the dialog itself. The backdrop is the
 * overlay; `.sheet` is the actual dialog — screen readers were getting the
 * wrong element's bounds. Fixed here, not ported. (The backdrop carries no
 * ARIA role at all: `aria-modal="true"` on the dialog is the standard way to
 * tell assistive tech that content outside is inert — you must NOT also
 * `aria-hidden` the backdrop, since `.sheet` is nested inside it and would be
 * hidden right along with it via attribute inheritance.)
 *
 * Adds what neither prior implementation had:
 * - a focus trap (Tab/Shift+Tab cycle within the dialog's focusable elements)
 * - Escape-to-close, handled on the dialog node itself (not `document`) so it
 *   doesn't fight a nested overlay's own Escape handler
 * - focus restore to whatever triggered the modal, on unmount — null-checked
 *   via `isConnected`, since the trigger element may itself have unmounted
 *   (e.g. a list row that re-rendered away while the modal was open)
 *
 * Reuses `.sheet-backdrop`/`.sheet`/`.sheet-grip` CSS verbatim — zero style
 * change, so the >=900px "becomes a centered modal" media query keeps working.
 */
export function Modal({
  ariaLabel,
  onClose,
  children,
  closeOnBackdropClick = true,
}: {
  ariaLabel: string;
  onClose: () => void;
  children: ReactNode;
  closeOnBackdropClick?: boolean;
}) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    previouslyFocused.current = document.activeElement as HTMLElement | null;

    const sheet = sheetRef.current;
    const focusable = sheet?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
    if (focusable && focusable.length > 0) {
      focusable[0].focus();
    } else {
      sheet?.focus();
    }

    return () => {
      const el = previouslyFocused.current;
      if (el && el.isConnected) el.focus();
    };
  }, []);

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== "Tab") return;

    const sheet = sheetRef.current;
    const focusable = sheet
      ? Array.from(sheet.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
      : [];
    if (focusable.length === 0) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="sheet-backdrop"
      onClick={closeOnBackdropClick ? onClose : undefined}
    >
      <div
        ref={sheetRef}
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        <div className="sheet-grip" />
        {children}
      </div>
    </div>
  );
}
