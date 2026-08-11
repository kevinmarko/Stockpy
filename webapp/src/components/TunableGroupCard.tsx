import React, { useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { TunableField } from "../api/types";
import { theme } from "../theme";

interface TunableGroupCardProps {
  name: string;
  fields: TunableField[];
  defaultOpen?: boolean;
  dirtyCount?: number;
  rejectedCount?: number;
  children: React.ReactNode;
}

export function TunableGroupCard({
  name,
  fields,
  defaultOpen = false,
  dirtyCount = 0,
  rejectedCount = 0,
  children,
}: TunableGroupCardProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  // Hooks must run unconditionally on every render of this instance -- called
  // ahead of the `fields.length === 0` early return below so the hook
  // count/order never varies across renders.
  const shouldReduceMotion = useReducedMotion();

  if (fields.length === 0) return null;

  // Snappy by design: this is a UI density control the operator may toggle
  // repeatedly while scanning a settings screen, not a slow reveal.
  // Collapses to near-instant when the OS reports a reduced-motion
  // preference (no local precedent for this elsewhere in webapp/src, so this
  // follows framer-motion's own `useReducedMotion` recommendation directly).
  const contentTransition = shouldReduceMotion
    ? { duration: 0.01 }
    : { duration: 0.2, ease: [0.4, 0, 0.2, 1] as const };

  return (
    <section
      className="card"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
        border: `1px solid ${rejectedCount > 0 ? theme.decline : dirtyCount > 0 ? theme.accent : theme.border}`,
        margin: 0,
      }}
    >
      <div
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "var(--s-3) var(--s-4)",
          background: theme.surface2,
          borderBottom: `1px solid ${theme.border}`,
          textAlign: "left",
          color: theme.textPrimary,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <h2 style={{ margin: 0, fontSize: "var(--t-title)", fontWeight: 700 }}>
            {name}
          </h2>
          <span
            style={{
              fontSize: "var(--t-caption)",
              color: theme.textMuted,
              background: theme.base,
              padding: "2px 8px",
              borderRadius: 12,
              border: `1px solid ${theme.border}`,
            }}
          >
            {fields.length} field{fields.length === 1 ? "" : "s"}
          </span>
          {dirtyCount > 0 && (
            <span
              style={{
                fontSize: "var(--t-caption)",
                color: theme.accent,
                background: "rgba(59, 130, 246, 0.1)",
                padding: "2px 8px",
                borderRadius: 12,
                fontWeight: 600,
              }}
            >
              {dirtyCount} modified
            </span>
          )}
          {rejectedCount > 0 && (
            <span
              style={{
                fontSize: "var(--t-caption)",
                color: theme.decline,
                background: "rgba(220, 38, 38, 0.1)",
                padding: "2px 8px",
                borderRadius: 12,
                fontWeight: 600,
              }}
            >
              {rejectedCount} rejected
            </span>
          )}
        </div>

        {/*
          This button is used to expand/collapse the content area.
          It stops propagation so clicks don't bubble up unnecessarily.
        */}
        <button
          type="button"
          onClick={() => setIsOpen((prev) => !prev)}
          onMouseDown={(e) => e.stopPropagation()}
          onTouchStart={(e) => e.stopPropagation()}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--s-1)",
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: theme.textMuted,
            fontSize: "var(--t-caption)",
            padding: "2px 6px",
          }}
          data-testid={`group-header-${name.toLowerCase().replace(/[^a-z0-9]/g, "-")}`}
        >
          <span
            style={{
              fontSize: 12,
              transform: isOpen ? "rotate(90deg)" : "rotate(0deg)",
              transition: "transform 0.15s ease",
              display: "inline-block",
            }}
          >
            ▶
          </span>
          {isOpen ? "Collapse" : "Expand"}
        </button>
      </div>

      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            key="content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={contentTransition}
            style={{ overflow: "auto", minHeight: 0, flex: 1 }}
          >
            <div style={{ padding: "var(--s-4)" }}>{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
