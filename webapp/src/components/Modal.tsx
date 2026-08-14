import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Drawer } from "vaul";
import { useMediaQuery } from "../hooks/useMediaQuery";

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
 *
 * Below `(max-width: 768px)` (`useMediaQuery`, see `../hooks/useMediaQuery`),
 * Modal instead renders a vaul `Drawer` bottom sheet — see the mobile branch
 * below for its own, separate exit-animation contract.
 */
const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * vaul's own default exit-transition duration (`TRANSITIONS.DURATION` in
 * `node_modules/vaul/dist/index.mjs`, currently `0.5` seconds — vaul applies
 * it via `transition: transform .5s cubic-bezier(...)` on `[data-vaul-drawer]`
 * and mirrors it internally when scheduling its own `onAnimationEnd`
 * callback). Mirrored here, not guessed, so the real `onClose` prop fires
 * only once the sheet has actually finished sliding away — see the mobile
 * branch below for why this is needed at all. If vaul's bundled transition
 * duration ever changes, this constant needs to move with it.
 */
export const MOBILE_EXIT_ANIMATION_MS = 500;

/**
 * Lets content inside the mobile (vaul) branch's `children` request a close
 * that goes through the SAME visible -> (play exit animation) -> delayed-
 * real-onClose sequence a backdrop tap or drag-dismiss already gets (see
 * `requestClose` inside `Modal` below), instead of calling the raw `onClose`
 * prop directly -- which a consumer's own Cancel-button closure has no way to
 * know it should avoid, and which would skip the exit animation entirely by
 * unmounting the whole `<Modal>` subtree the instant the parent's `onClose`
 * handler flips its `show` state. `null` outside a mobile `Modal` (desktop
 * consumers don't need this -- the desktop dialog's unmount-is-instant
 * behavior is unchanged/out of scope, see the desktop branch at the bottom of
 * this file).
 */
const ModalRequestCloseContext = createContext<(() => void) | null>(null);

/**
 * Returns the nearest enclosing mobile `Modal`'s close-request function, or
 * `undefined` when there isn't one (desktop branch, or no `Modal` ancestor).
 * Prefer this over calling a captured `onClose` closure directly from inside
 * `Modal`'s `children` so a dismiss action (e.g. a Cancel button) gets the
 * same animated exit a backdrop tap does.
 */
export function useModalRequestClose(): (() => void) | undefined {
  return useContext(ModalRequestCloseContext) ?? undefined;
}

export function Modal({
  ariaLabel,
  onClose,
  children,
  closeOnBackdropClick = true,
  size = "default",
}: {
  ariaLabel: string;
  onClose: () => void;
  children: ReactNode;
  closeOnBackdropClick?: boolean;
  /** "wide" raises the desktop max-width (see `.sheet--wide` in index.css)
   *  for content-heavy dialogs. Below the 900px breakpoint both sizes are
   *  identical -- `.sheet`'s own `width: 100%` already fits the viewport. */
  size?: "default" | "wide";
}) {
  const isMobile = useMediaQuery("(max-width: 768px)");

  // Desktop implementation
  const sheetRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    return () => {
      if (document.activeElement && sheetRef.current?.contains(document.activeElement)) {
        (document.activeElement as HTMLElement).blur?.();
      }
      const el = previouslyFocused.current;
      if (el && el.isConnected) el.focus();
    };
  }, []);

  useEffect(() => {
    if (isMobile) return;
    const sheet = sheetRef.current;
    const focusable = sheet?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
    if (focusable && focusable.length > 0) {
      focusable[0].focus();
    } else {
      sheet?.focus();
    }
  }, [isMobile]);

  // --- Mobile (vaul) exit-animation state -----------------------------
  // `visible` decouples "the sheet should start animating away" from "the
  // real onClose prop has fired" (which the PARENT uses to unmount `Modal`
  // entirely). Previously `Drawer.Root`'s `open` was a hardcoded `true`
  // constant, so the parent's re-render (triggered by `onClose`) unmounted
  // the whole subtree in the same tick vaul/Radix would otherwise have used
  // to run the slide-down exit transition -- the sheet just vanished
  // instantly for every programmatic close (Cancel button, backdrop tap),
  // even though a real drag-to-dismiss gesture animated fine (vaul's own
  // drag machinery visually animates the sheet away DURING the drag itself,
  // calling `onOpenChange(false)` only once that's done). Declared
  // unconditionally (not inside `if (isMobile)`) per the Rules of Hooks --
  // unused on the desktop branch, which is otherwise untouched.
  const [visible, setVisible] = useState(true);
  const closingRef = useRef(false);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (closeTimerRef.current !== null) {
        clearTimeout(closeTimerRef.current);
      }
    };
  }, []);

  // The single choke point every mobile dismiss path (vaul's own
  // onOpenChange, the overlay's backdrop tap, and any `children` that opt in
  // via `useModalRequestClose`) routes through. Flips `visible` to false
  // (letting vaul/Radix's Presence machinery play the real exit transition
  // while `Modal` stays mounted) and defers the REAL `onClose` prop -- the
  // one that causes the parent to unmount `Modal` -- until that transition
  // has actually finished. Guarded by `closingRef` so a second dismiss
  // signal arriving mid-animation (e.g. both the overlay's onClick AND
  // vaul's internal onOpenChange firing for the same tap) doesn't restart
  // the timer or double-invoke `onClose`.
  const requestClose = useCallback(() => {
    if (closingRef.current) return;
    closingRef.current = true;
    setVisible(false);
    closeTimerRef.current = setTimeout(() => {
      closeTimerRef.current = null;
      onClose();
    }, MOBILE_EXIT_ANIMATION_MS);
  }, [onClose]);

  if (isMobile) {
    return (
      <Drawer.Root
        open={visible}
        onOpenChange={(open) => {
          if (!open) requestClose();
        }}
        dismissible={closeOnBackdropClick}
      >
        <Drawer.Portal>
          <Drawer.Overlay
            className="sheet-backdrop"
            style={{ animation: "none" }}
            onClick={closeOnBackdropClick ? requestClose : undefined}
          />
          <Drawer.Content
            className="sheet"
            aria-label={ariaLabel}
            style={{
              position: "fixed",
              bottom: 0,
              left: 0,
              right: 0,
              zIndex: 61,
              animation: "none", // let vaul handle animations
              display: "flex",
              flexDirection: "column",
            }}
          >
            <div className="sheet-grip" />
            <div style={{ overflowY: "auto", flex: 1 }}>
              <ModalRequestCloseContext.Provider value={requestClose}>
                {children}
              </ModalRequestCloseContext.Provider>
            </div>
          </Drawer.Content>
        </Drawer.Portal>
      </Drawer.Root>
    );
  }

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
        className={size === "wide" ? "sheet sheet--wide" : "sheet"}
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
