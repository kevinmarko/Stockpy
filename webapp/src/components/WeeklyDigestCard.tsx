import { useApi } from "../hooks/useApi";
import { api } from "../api/client";
import { WeeklyDigestEntry } from "../api/types";
import { theme } from "../theme";
import { Loading } from "./ui";

export function WeeklyDigestCard() {
  const { data, loading, error } = useApi<WeeklyDigestEntry[]>(
    () => api.getWeeklyDigest(),
    []
  );

  if (error) {
    return (
      <section className="card card-pad" style={{ marginBottom: "var(--s-4)", border: `1px solid ${theme.danger}` }}>
        <h2 style={{ fontSize: "var(--t-title)", margin: "0 0 var(--s-1)" }}>This Week's Digest</h2>
        <p style={{ color: theme.danger, fontSize: "var(--t-body)", margin: 0 }}>
          Failed to load weekly digest: {error.message}
        </p>
      </section>
    );
  }

  if (loading && !data) {
    return (
      <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }}>
        <h2 style={{ fontSize: "var(--t-title)", margin: "0 0 var(--s-1)" }}>This Week's Digest</h2>
        <Loading lines={2} />
      </section>
    );
  }

  const entries = data ?? [];

  return (
    <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }}>
      <h2 style={{ fontSize: "var(--t-title)", margin: "0 0 var(--s-1)" }}>This Week's Digest</h2>
      {entries.length === 0 ? (
        <p style={{ color: theme.textMuted, fontSize: "var(--t-body)", margin: 0 }}>
          No digest available.
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
          {entries.map((item, idx) => (
            <div key={idx} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--s-3)" }}>
                <span style={{ fontWeight: 700, fontSize: "var(--t-body)", color: theme.textPrimary, minWidth: 60 }}>
                  {item.symbol}
                </span>
                <span
                  style={{
                    fontSize: "var(--t-micro)",
                    fontWeight: 700,
                    color: theme.accent,
                    background: "rgba(99,102,241,0.12)",
                    padding: "2px 6px",
                    borderRadius: "var(--r-2xs)",
                    textTransform: "uppercase"
                  }}
                >
                  {item.type}
                </span>
              </div>
              <span style={{ color: theme.textSecondary, fontSize: "var(--t-footnote)", textAlign: "right" }}>
                {item.reason}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
