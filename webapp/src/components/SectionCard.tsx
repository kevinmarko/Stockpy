import type { ReactNode } from "react";
import { theme } from "../theme";

export function SectionCard({
  title,
  sub,
  children,
}: {
  title: string;
  sub?: string;
  children: ReactNode;
}) {
  return (
    <section className="card card-pad">
      <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>{title}</h2>
      {sub && (
        <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0, marginBottom: "var(--s-3)" }}>
          {sub}
        </p>
      )}
      {children}
    </section>
  );
}
