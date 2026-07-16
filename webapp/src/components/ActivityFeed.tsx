import { useEffect, useState, useRef, useCallback } from "react";
import { api } from "../api/client";
import type { AlertEntry } from "../api/types";
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
  // Missing level category values default to INFO
  const normalizedLevel = (level || "INFO").toUpperCase();
  const style = LEVEL_STYLE[normalizedLevel] || {
    color: theme.textMuted,
    label: normalizedLevel,
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
    <div className="card card-pad" style={{ marginBottom: 10, background: theme.surface, border: `1px solid ${theme.border}` }} data-testid="alert-card">
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

export function ActivityFeed({ limit = 20, filterPilotIds }: { limit?: number; filterPilotIds?: string[] }) {
  const [pollingActive, setPollingActive] = useState(true);
  const [alerts, setAlerts] = useState<AlertEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  const isFetchingRef = useRef(false);

  const fetchAlerts = useCallback(async (isBackground = false) => {
    if (isFetchingRef.current) return;
    isFetchingRef.current = true;

    if (!isBackground) {
      setLoading(true);
      setError(null);
    }

    try {
      const data = await api.getAlerts(limit);
      setAlerts((data && data.entries) || []);
      setError(null);
    } catch (e: any) {
      if (!isBackground) {
        setError(e?.message || "Failed to fetch alerts");
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
    }, 10000);
    return () => clearInterval(interval);
  }, [pollingActive, fetchAlerts]);

  const handleManualRefresh = () => {
    fetchAlerts(false);
  };

  // Filter alerts by pilot IDs if specified
  const validAlerts = Array.isArray(alerts) ? alerts.filter(a => a && typeof a === "object") : [];
  const filteredAlerts = filterPilotIds && filterPilotIds.length > 0
    ? validAlerts.filter(a => {
        if (!a) return false;
        const msg = (a.message || "").toLowerCase();
        return filterPilotIds.some(id => {
          const name = id === "trend-following" ? "trend follower"
                     : id === "dip-buyer" ? "dip buyer"
                     : id === "balanced-blend" ? "balanced blend"
                     : id.replace(/-/g, " ").toLowerCase();
          return msg.includes(id.toLowerCase()) || msg.includes(name) || (a.extra && (a.extra as any).pilot_id === id);
        });
      })
    : validAlerts;

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
        <ErrorState message={error} onRetry={handleManualRefresh} />
      )}

      {!loading && !error && (
        <>
          {filteredAlerts.length === 0 ? (
            <div className="empty" style={{ padding: 20 }} data-testid="empty-alerts">
              No alerts yet.
            </div>
          ) : (
            <div 
              style={{ 
                display: "flex", 
                flexDirection: "column", 
                maxHeight: 300, 
                overflowY: "auto",
                ...(isLargeList ? { contentVisibility: "auto", containIntrinsicSize: "0 100px" } : {})
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
