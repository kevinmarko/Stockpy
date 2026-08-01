import React, { useId, useState } from "react";

interface CollapsiblePanelProps {
  title: string;
  badge?: string | number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

export function CollapsiblePanel({
  title,
  badge,
  defaultOpen = true,
  children,
}: CollapsiblePanelProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const panelId = useId();

  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={isOpen}
        aria-controls={panelId}
        style={{
          width: "100%",
          padding: "var(--s-3) var(--s-4)",
          background: "var(--surface-2)",
          border: "none",
          borderBottom: isOpen ? "1px solid var(--border)" : "none",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          cursor: "pointer",
          userSelect: "none",
          textAlign: "left",
          font: "inherit",
          color: "inherit",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <span aria-hidden style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>
            {isOpen ? "▼" : "▶"}
          </span>
          <h3 style={{ margin: 0, fontSize: "var(--t-subhead)", fontWeight: 600, color: "var(--text-primary)" }}>
            {title}
          </h3>
          {badge != null && (
            <span
              style={{
                fontSize: "var(--t-micro)",
                fontWeight: 700,
                background: "var(--surface-3)",
                color: "var(--accent)",
                padding: "2px 8px",
                borderRadius: "var(--r-pill)",
              }}
            >
              {badge}
            </span>
          )}
        </div>
      </button>
      {isOpen && (
        <div id={panelId} style={{ padding: "var(--s-4)" }}>
          {children}
        </div>
      )}
    </div>
  );
}
