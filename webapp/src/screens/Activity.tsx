import { api, apiMeta } from "../api/client";
import type { AlertEntry, AlertsFeed } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading } from "../components/ui";
import { timeAgo } from "../format";
import { theme } from "../theme";

const LEVEL_STYLE: Record<string, { color: string; label: string }> = {
  CRITICAL: { color: theme.decline, label: "Critical" },
  ERROR: { color: theme.decline, label: "Error" },
  WARNING: { color: theme.caution, label: "Warning" },
  INFO: { color: theme.accent, label: "Info" },
  DEBUG: { color: theme.textMuted, label: "Debug" },
};

function LevelDot({ level }: { level: string | null }) {
  const style = (level && LEVEL_STYLE[level.toUpperCase()]) || {
    color: theme.textMuted,
    label: level ?? "—",
  };
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span
        aria-hidden
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: style.color,
          flex: "0 0 auto",
        }}
      />
      <span style={{ color: style.color, fontSize: 11.5, fontWeight: 700 }}>
        {style.label}
      </span>
    </span>
  );
}

function AlertCard({ entry }: { entry: AlertEntry }) {
  return (
    <div className="card card-pad" style={{ marginBottom: 10 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 4,
        }}
      >
        <LevelDot level={entry.level} />
        <span style={{ fontSize: 11.5, color: theme.textMuted }}>
          {timeAgo(entry.timestamp)}
        </span>
      </div>
      <div style={{ fontSize: 14, color: theme.textPrimary, lineHeight: 1.45 }}>
        {entry.message ?? "—"}
      </div>
    </div>
  );
}

export function Activity() {
  const { data, loading, error, status, reload } = useApi<AlertsFeed>(
    () => api.getAlerts(50),
    []
  );

  return (
    <div className="screen">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="screen-title">Activity</h1>
          <p className="screen-sub">Recent alerts from the Stockpy pipeline.</p>
        </div>
        {apiMeta.useMock && (
          <span className="chip" style={{ marginTop: 10 }} title="Running on mock data">
            demo
          </span>
        )}
      </div>

      {loading && <Loading lines={4} />}

      {!loading && error && (
        <ErrorState message={error} status={status} onRetry={reload} />
      )}

      {!loading && !error && data && (
        <>
          {data.entries.length === 0 ? (
            <div className="empty" style={{ padding: 30 }}>
              {data.reason ?? "No alerts yet."}
            </div>
          ) : (
            <div style={{ marginTop: 8 }}>
              {data.entries.map((e, i) => (
                <AlertCard key={`${e.timestamp ?? i}-${i}`} entry={e} />
              ))}
            </div>
          )}
          <p
            style={{
              color: theme.textMuted,
              fontSize: 11.5,
              marginTop: 20,
              textAlign: "center",
              lineHeight: 1.5,
            }}
          >
            Alerts are read from the structured alert log. Configure ALERT_FILE_PATH
            to enable the feed on a live backend.
          </p>
        </>
      )}
    </div>
  );
}
