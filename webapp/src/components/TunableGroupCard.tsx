import React from "react";
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
  dirtyCount = 0,
  rejectedCount = 0,
  children,
}: TunableGroupCardProps) {
  if (fields.length === 0) return null;

  return (
    <section
      className="card"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
        border: `1px solid ${rejectedCount > 0 ? theme.decline : dirtyCount > 0 ? theme.accent : theme.border}`,
        margin: 0
      }}
    >
      <div
        className="drag-handle"
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "var(--s-3) var(--s-4)",
          background: theme.surface2,
          borderBottom: `1px solid ${theme.border}`,
          cursor: "grab",
          textAlign: "left",
          color: theme.textPrimary,
        }}
        data-testid={`group-header-${name.toLowerCase().replace(/[^a-z0-9]/g, "-")}`}
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
      </div>

      <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: "var(--s-4)" }}>
        {children}
      </div>
    </section>
  );
}
