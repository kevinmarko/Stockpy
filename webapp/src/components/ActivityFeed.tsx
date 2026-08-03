import { useEffect, useState, useRef, useCallback } from "react";
import { api } from "../api/client";
import type { AlertEntry, AlertsFeed } from "../api/types";
import { ApiError } from "../api/types";
import { ErrorState, Loading,  } from "./ui";
import { Toggle } from "./Toggle";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { useAutoRefresh } from "./AutoRefreshContext";
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
    <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--s-1-5)" }}>
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
      <span style={{ color: style.color, fontSize: "var(--t-footnote)", fontWeight: 700 }}>
        {style.label}
      </span>
    </span>
  );
}

export function getAlertCategory(entry: AlertEntry): "SYSTEM" | "EXECUTION" | "RISK" | "REGIME" {
  const t = entry.extra?.type as string | undefined;
  if (!t) return "SYSTEM";
  if (["fill", "order", "trade", "execution"].includes(t)) return "EXECUTION";
  if (["risk", "constraint"].includes(t)) return "RISK";
  if (["regime", "hmm", "macro"].includes(t)) return "REGIME";
  return "SYSTEM";
}

const CATEGORY_COLORS = {
  SYSTEM: theme.borderStrong,
  EXECUTION: theme.accent,
  RISK: theme.decline,
  REGIME: theme.caution,
};

function AlertCard({ entry }: { entry: AlertEntry }) {
  const category = getAlertCategory(entry);
  const borderColor = CATEGORY_COLORS[category];

  return (
    <div
      className="card card-pad"
      style={{
        marginBottom: "var(--s-2-5)",
        background: theme.surface,
        border: `1px solid ${theme.border}`,
        borderLeft: `4px solid ${borderColor}`,
      }}
      data-testid="alert-card"
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "var(--s-1)",
        }}
      >
        <LevelDot level={entry.level} />
        <span style={{ fontSize: "var(--t-footnote)", color: theme.textMuted }}>
          {timeAgo(entry.timestamp)}
        </span>
      </div>
      <div style={{ fontSize: "var(--t-body)", color: theme.textPrimary, lineHeight: 1.45 }}>
        {entry.message ?? "—"}
      </div>
      {category === "EXECUTION" && (
        <div style={{ marginTop: "var(--s-2)" }}>
          <button className="btn btn-primary" style={{ fontSize: "var(--t-caption)", padding: "var(--s-1) var(--s-3)" }}>
            Review Trade
          </button>
        </div>
      )}
      {category === "REGIME" && (
        <div style={{ marginTop: "var(--s-2)" }}>
          <button className="btn btn-secondary" style={{ fontSize: "var(--t-caption)", padding: "var(--s-1) var(--s-3)" }}>
            View Regime
          </button>
        </div>
      )}
      {category === "RISK" && (
        <div style={{ marginTop: "var(--s-2)" }}>
          <button className="btn btn-secondary" style={{ fontSize: "var(--t-caption)", padding: "var(--s-1) var(--s-3)" }}>
            Risk Details
          </button>
        </div>
      )}
    </div>
  );
}

export function ActivityFeed({
  limit = 20,
  pilotIds,
  pollIntervalMs,
  categoryFilter,
}: { limit?: number; pilotIds?: string[]; pollIntervalMs?: number; categoryFilter?: string | null }) {
  const [pollingActive, setPollingActive] = useState(true);
  const { autoRefreshEnabled, observabilityRefreshEnabled } = useAutoRefresh();
  // The local toggle only controls whether THIS component asks to poll --
  // it can't override the global master switch or the "observability"
  // category being off. Rendering it as if it still worked in that case
  // would be a second lie about whether polling is actually happening.
  const pollingGatedOff = !autoRefreshEnabled || !observabilityRefreshEnabled;
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

  // hasError: false is deliberate -- fetchAlerts(true)'s background mode
  // already swallows failures by design (see the catch block above), so
  // there's no error signal here to back off on. customIntervalMs is only
  // passed through when a caller actually supplies pollIntervalMs; otherwise
  // it's undefined and useAutoPoll falls through to the global/category
  // interval via resolveIntervalMs("observability").
  useAutoPoll(() => fetchAlerts(true), "observability", {
    enabled: pollingActive,
    hasError: false,
    customIntervalMs: pollIntervalMs,
  });

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
  
  const finalAlerts = categoryFilter && categoryFilter !== "ALL"
    ? filteredAlerts.filter(a => getAlertCategory(a) === categoryFilter)
    : filteredAlerts;

  return (
    <div data-testid="activity-feed-widget">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-3)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <button
            className="btn"
            onClick={handleManualRefresh}
            style={{ fontSize: "var(--t-micro)", padding: "var(--s-1) var(--s-2)" }}
            data-testid="refresh-alerts-btn"
          >
            Refresh
          </button>
          <Toggle
            label={pollingGatedOff ? "Auto-poll (off in Settings)" : "Auto-poll"}
            checked={pollingActive}
            onChange={setPollingActive}
            dataTestId="toggle-polling-checkbox"
            disabled={pollingGatedOff}
          />
        </div>
      </div>

      {loading && <Loading lines={3} />}

      {!loading && error && (
        <ErrorState message={error} status={status} onRetry={handleManualRefresh} />
      )}

      {!loading && !error && (
        <>
          {finalAlerts.length === 0 ? (
            <div className="empty" style={{ padding: "var(--s-5)" }} data-testid="empty-alerts">
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
              {finalAlerts.slice(0, limit).map((e, i) => (
                <AlertCard key={`${e.timestamp ?? i}-${i}`} entry={e} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
