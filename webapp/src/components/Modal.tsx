import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import { Drawer } from "vaul";
import { useMediaQuery } from "../hooks/useMediaQuery";

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

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
  size?: "default" | "wide";
}) {
  const isMobile = useMediaQuery("(max-width: 768px)");

  // Desktop implementation
  const sheetRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    return () => {
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

  if (isMobile) {
    return (
      <Drawer.Root
        open={true}
        onOpenChange={(open) => {
          if (!open) onClose();
        }}
        dismissible={closeOnBackdropClick}
      >
        <Drawer.Portal>
          <Drawer.Overlay
            className="sheet-backdrop"
            style={{ animation: "none" }}
            onClick={closeOnBackdropClick ? onClose : undefined}
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
            <div style={{ overflowY: "auto", flex: 1 }}>{children}</div>
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
