import { useEffect, useState, useRef, useCallback } from "react";
import { api } from "../api/client";
import type { AlertEntry, AlertsFeed } from "../api/types";
import { ApiError } from "../api/types";
import { ErrorState, Loading } from "./ui";
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
  // Honesty: an unknown/null level is NEVER promoted to a fabricated severity.
  // It renders the raw level string if present, else "—", in the muted color —
  // mirroring the pre-existing Activity screen's LevelDot idiom.
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
    <div
      className="card card-pad"
      style={{ marginBottom: 10, background: theme.surface, border: `1px solid ${theme.border}` }}
      data-testid="alert-card"
    >
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
      <div style={{ fontSize: 13, color: theme.textPrimary, lineHeight: 1.45 }}>
        {entry.message ?? "—"}
      </div>
    </div>
  );
}

export function ActivityFeed({
  limit = 20,
  pilotIds,
  pollIntervalMs = 30000,
}: { limit?: number; pilotIds?: string[]; pollIntervalMs?: number }) {
  const [pollingActive, setPollingActive] = useState(true);
  // Keep the whole feed (not just entries) so the honest `reason` string is
  // available for the empty state instead of a hardcoded placeholder.
  const [feed, setFeed] = useState<AlertsFeed | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);

  const isFetchingRef = useRef(false);

  const fetchAlerts = useCallback(async (isBackground = false) => {
    if (isFetchingRef.current) return;
    isFetchingRef.current = true;

    if (!isBackground) {
      setLoading(true);
      setError(null);
      setStatus(null);
    }

    try {
      const data = await api.getAlerts(limit);
      setFeed(data ?? { entries: [], reason: null });
      setError(null);
      setStatus(null);
    } catch (e: unknown) {
      // Background poll failures never clobber the last good feed or surface an
      // error banner — only a foreground (mount / manual refresh) failure does.
      if (!isBackground) {
        setError(e instanceof Error ? e.message : "Failed to fetch alerts");
        setStatus(e instanceof ApiError ? e.status : null);
      }
    } finally {
      setLoading(false);
      isFetchingRef.current = false;
    }
  }, [limit]);

  useEffect(() => {
    fetchAlerts(false);
  }, [fetchAlerts]);

  useEffect(() => {
    if (!pollingActive) return;
    const interval = setInterval(() => {
      fetchAlerts(true);
    }, pollIntervalMs);
    return () => clearInterval(interval);
  }, [pollingActive, pollIntervalMs, fetchAlerts]);

  const handleManualRefresh = () => {
    fetchAlerts(false);
  };

  const entries = feed?.entries ?? [];
  const reason = feed?.reason ?? null;

  // pilotIds filters ONLY on an exact `extra.pilot_id` match — never message-text
  // substring matching, never an alias table. An alert whose message mentions a
  // pilot by name but carries no `extra.pilot_id` is NOT attributed to it.
  const validEntries = entries.filter((a) => a && typeof a === "object");
  const filteredAlerts =
    pilotIds && pilotIds.length > 0
      ? validEntries.filter((a) => pilotIds.includes(String(a.extra?.pilot_id)))
      : validEntries;

  const isLargeList = filteredAlerts.length > 100;

  return (
    <div data-testid="activity-feed-widget">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            className="btn"
            onClick={handleManualRefresh}
            style={{ fontSize: 11, padding: "4px 8px" }}
            data-testid="refresh-alerts-btn"
          >
            Refresh
          </button>
          <label style={{ fontSize: 11, color: theme.textSecondary, display: "flex", alignItems: "center", gap: 4 }}>
            <input
              type="checkbox"
              checked={pollingActive}
              onChange={(e) => setPollingActive(e.target.checked)}
              data-testid="toggle-polling-checkbox"
            />
            Auto-poll
          </label>
        </div>
      </div>

      {loading && <Loading lines={3} />}

      {!loading && error && (
        <ErrorState message={error} status={status} onRetry={handleManualRefresh} />
      )}

      {!loading && !error && (
        <>
          {filteredAlerts.length === 0 ? (
            <div className="empty" style={{ padding: 20 }} data-testid="empty-alerts">
              {reason ?? "No alerts yet."}
            </div>
          ) : (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                maxHeight: 300,
                overflowY: "auto",
                ...(isLargeList ? { contentVisibility: "auto", containIntrinsicSize: "0 100px" } : {}),
              }}
            >
              {filteredAlerts.slice(0, limit).map((e, i) => (
                <AlertCard key={`${e.timestamp ?? i}-${i}`} entry={e} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
