import React, { useState, useMemo } from "react";
import { Link } from "react-router";
import {
  Globe,
  RefreshCw,
  Search,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  HelpCircle,
  Clock,
  Database,
  Radio,
  Layers,
} from "lucide-react";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { useAutoRefresh } from "../components/AutoRefreshContext";
import type { CoverageStatus, SyncReportResponse, SyncReportSymbol } from "../api/types";
import { Button, Loading, ErrorState } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { ExplainTickerButton } from "../components/ExplainTickerButton";
import { fmtUsd, fmtNum, timeAgo } from "../format";

export type FilterTab = "all" | "gaps" | "held" | "watchlists" | "excluded";

// Mirrors data.portfolio_sync.CoverageStatus badge styling/labels EXACTLY as
// used by the sibling coverage panel (webapp/src/components/UniverseCoverage.tsx,
// reachable from Settings -> Tracked Universe) -- duplicated here rather than
// imported, since the two components live in independently-owned files, but
// kept byte-identical on purpose: the same six-value backend enum must never
// read as two different vocabularies depending which screen an operator
// happens to be looking at (see CLAUDE.md's "Universe Transparency panel"
// bullet for the full history of that already-audited component).
const COVERAGE_BADGE_CLASS: Record<CoverageStatus, string> = {
  full: "badge-good",
  stale: "badge-warn",
  quotes_only: "badge-warn",
  equity_only: "badge-warn",
  uncovered: "badge-bad",
  unknown: "badge-neutral",
};

const COVERAGE_LABEL: Record<CoverageStatus, string> = {
  full: "Full",
  stale: "Stale",
  quotes_only: "Quotes only",
  equity_only: "Equity only",
  uncovered: "Uncovered",
  unknown: "Unknown",
};

/**
 * Rating-streak cell for a symbol row. `rating_consecutive_bad_cycles` is
 * `undefined`/`null` when the rating engine has no history for this symbol
 * yet -- NOT the same fact as a symbol verified clean for zero consecutive
 * bad cycles, and must never render identically to it (CONSTRAINT #4).
 * Mirrors `UniverseCoverage.tsx`'s `ratingCyclesLabel` null-vs-zero
 * distinction; duplicated rather than imported for the same cross-file
 * reason as the label maps above.
 */
function ratingCyclesCell(cycles: number | null | undefined): React.ReactElement {
  if (cycles == null) {
    return <span style={{ color: "var(--text-muted)" }}>—</span>;
  }
  if (cycles > 0) {
    return <span style={{ color: "var(--caution)" }}>{cycles} bad cycle(s)</span>;
  }
  return <span style={{ color: "var(--text-muted)" }}>0 bad</span>;
}

/**
 * UniverseTransparencyIdle -- rendered whenever the "robinhood" auto-refresh
 * category is off and the operator hasn't explicitly loaded the report yet.
 * `GET /data/sync-report` recomputes coverage live and CAN trigger a real
 * Robinhood login when the cached account snapshot is stale and the operator
 * has `ROBINHOOD_AUTO_REFRESH_ENABLED=True` (see CLAUDE.md) -- this screen
 * must not fetch on mount by default, mirroring the identical guard already
 * shipped and audited on `UniverseCoverage.tsx` (Settings -> Tracked
 * Universe), which reads this exact same endpoint. "Sync Universe"
 * (`POST /data/sync`) is a different, always-manual-only mutation that stays
 * reachable here too; a successful sync arms the live view so the operator
 * immediately sees what they just synced.
 */
function UniverseTransparencyIdle({ onLoad }: { onLoad: () => void }) {
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<{ text: string; isError: boolean } | null>(null);

  const handleSyncNow = async () => {
    setSyncing(true);
    setSyncMessage(null);
    try {
      const res = await api.postDataSync();
      setSyncMessage({
        text: `Sync completed: ${res.default_tickers.length} tickers configured. ${res.note || ""}`,
        isError: false,
      });
      onLoad();
    } catch (err: any) {
      setSyncMessage({
        text: err?.message || "Universe sync failed.",
        isError: true,
      });
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div
      className="page"
      data-testid="universe-transparency-screen"
      style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}
    >
      <div
        style={{
          background: "var(--surface)",
          padding: "var(--s-4)",
          borderRadius: "var(--r-md)",
          border: "1px solid var(--border)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <Globe size={22} color="var(--accent)" />
          <h1 style={{ margin: 0, fontSize: "var(--t-title)", fontWeight: 700 }}>
            Universe Transparency
          </h1>
        </div>
        <p
          style={{
            margin: "var(--s-1) 0 0",
            color: "var(--text-secondary)",
            fontSize: "var(--t-body)",
          }}
        >
          Real-time market data coverage, portfolio sync status, and data pipeline verification
          across all tracked symbols.
        </p>
        <p
          style={{
            margin: "var(--s-1) 0 0",
            color: "var(--text-muted)",
            fontSize: "var(--t-caption)",
          }}
        >
          Looking to add or remove symbols? Manage the list in{" "}
          <Link to="/settings/universe" style={{ color: "var(--accent)" }}>
            Settings → Tracked Universe
          </Link>
          .
        </p>
      </div>

      <TabGuide tabKey="universe" />

      <div
        data-testid="universe-transparency-idle"
        style={{
          background: "var(--surface)",
          padding: "var(--s-6)",
          borderRadius: "var(--r-sm)",
          border: "1px dashed var(--border)",
          textAlign: "center",
        }}
      >
        <p style={{ margin: "0 0 var(--s-3)", color: "var(--text-secondary)" }}>
          Coverage report not loaded. Fetching it can trigger a live Robinhood login.
        </p>
        <div style={{ display: "flex", justifyContent: "center", gap: "var(--s-2)", flexWrap: "wrap" }}>
          <Button variant="neutral" onClick={onLoad} data-testid="universe-transparency-load">
            Load coverage report
          </Button>
          <Button
            variant="primary"
            onClick={handleSyncNow}
            disabled={syncing}
            data-testid="sync-now-button"
            style={{ display: "inline-flex", alignItems: "center", gap: "var(--s-1)" }}
          >
            <Layers size={14} className={syncing ? "spin" : ""} />
            <span>{syncing ? "Syncing..." : "Sync Universe"}</span>
          </Button>
        </div>
        {syncMessage && (
          <div
            className={`notice ${syncMessage.isError ? "notice-warn" : "notice-success"}`}
            style={{ marginTop: "var(--s-3)" }}
            role="status"
          >
            <span>{syncMessage.text}</span>
          </div>
        )}
        <p style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)", margin: "var(--s-3) 0 0" }}>
          — or turn on Robinhood auto-refresh in Settings → Data & Automation.
        </p>
      </div>
    </div>
  );
}

function UniverseTransparencyLive() {
  const { data, loading, error, status, reload } = useApi<SyncReportResponse>(
    () => api.getSyncReport(),
    [],
  );
  useAutoPoll(reload, "robinhood", { hasError: error != null });

  const [activeTab, setActiveTab] = useState<FilterTab>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<{ text: string; isError: boolean } | null>(null);
  const [reincluding, setReincluding] = useState<string | null>(null);

  const rows: SyncReportSymbol[] = useMemo(() => {
    if (!data?.symbols) return [];
    return Object.values(data.symbols).sort((a, b) => a.symbol.localeCompare(b.symbol));
  }, [data]);

  const counts = useMemo(() => {
    const c: Record<CoverageStatus, number> = {
      full: 0,
      stale: 0,
      quotes_only: 0,
      equity_only: 0,
      uncovered: 0,
      unknown: 0,
    };
    let held = 0;
    let watchlists = 0;
    let excluded = 0;

    for (const r of rows) {
      c[r.coverage] = (c[r.coverage] || 0) + 1;
      if (r.held) held++;
      if (r.watchlists && r.watchlists.length > 0) watchlists++;
      if (r.rating_excluded) excluded++;
    }

    const gaps = rows.length - c.full;

    return {
      ...c,
      total: rows.length,
      held,
      watchlists,
      excluded,
      gaps,
    };
  }, [rows]);

  const filteredRows = useMemo(() => {
    let result = rows;

    // Filter by tab
    if (activeTab === "gaps") {
      result = result.filter((r) => r.coverage !== "full");
    } else if (activeTab === "held") {
      result = result.filter((r) => r.held);
    } else if (activeTab === "watchlists") {
      result = result.filter((r) => r.watchlists && r.watchlists.length > 0);
    } else if (activeTab === "excluded") {
      result = result.filter((r) => r.rating_excluded);
    }

    // Filter by search query
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toUpperCase();
      result = result.filter(
        (r) =>
          r.symbol.includes(q) ||
          (r.watchlists && r.watchlists.some((w) => w.toUpperCase().includes(q))) ||
          (r.diagnostic && r.diagnostic.toUpperCase().includes(q)),
      );
    }

    return result;
  }, [rows, activeTab, searchQuery]);

  const handleSyncNow = async () => {
    setSyncing(true);
    setSyncMessage(null);
    try {
      const res = await api.postDataSync();
      setSyncMessage({
        text: `Sync completed: ${res.default_tickers.length} tickers configured. ${res.note || ""}`,
        isError: false,
      });
      reload();
    } catch (err: any) {
      setSyncMessage({
        text: err?.message || "Universe sync failed.",
        isError: true,
      });
    } finally {
      setSyncing(false);
    }
  };

  const handleReinclude = async (symbol: string) => {
    setReincluding(symbol);
    try {
      await api.reincludeSymbol(symbol);
      reload();
    } catch (err: any) {
      alert(`Failed to re-include ${symbol}: ${err?.message || "Unknown error"}`);
    } finally {
      setReincluding(null);
    }
  };

  return (
    <div
      className="page"
      data-testid="universe-transparency-screen"
      style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}
    >
      {/* Header & Provenance Banner */}
      <div
        style={{
          background: "var(--surface)",
          padding: "var(--s-4)",
          borderRadius: "var(--r-md)",
          border: "1px solid var(--border)",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            flexWrap: "wrap",
            gap: "var(--s-3)",
          }}
        >
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
              <Globe size={22} color="var(--accent)" />
              <h1 style={{ margin: 0, fontSize: "var(--t-title)", fontWeight: 700 }}>
                Universe Transparency
              </h1>
            </div>
            <p
              style={{
                margin: "var(--s-1) 0 0",
                color: "var(--text-secondary)",
                fontSize: "var(--t-body)",
              }}
            >
              Real-time market data coverage, portfolio sync status, and data pipeline verification
              across all tracked symbols.
            </p>
            <p
              style={{
                margin: "var(--s-1) 0 0",
                color: "var(--text-muted)",
                fontSize: "var(--t-caption)",
              }}
            >
              Looking to add or remove symbols? Manage the list in{" "}
              <Link to="/settings/universe" style={{ color: "var(--accent)" }}>
                Settings → Tracked Universe
              </Link>
              .
            </p>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
            <Button
              variant="neutral"
              onClick={reload}
              disabled={loading}
              style={{ display: "inline-flex", alignItems: "center", gap: "var(--s-1)" }}
            >
              <RefreshCw size={14} className={loading ? "spin" : ""} />
              <span>Refresh</span>
            </Button>
            <Button
              variant="primary"
              onClick={handleSyncNow}
              disabled={syncing}
              data-testid="sync-now-button"
              style={{ display: "inline-flex", alignItems: "center", gap: "var(--s-1)" }}
            >
              <Layers size={14} className={syncing ? "spin" : ""} />
              <span>{syncing ? "Syncing..." : "Sync Universe"}</span>
            </Button>
          </div>
        </div>

        {/* Sync message feedback */}
        {syncMessage && (
          <div
            className={`notice ${syncMessage.isError ? "notice-warn" : "notice-success"}`}
            style={{ marginTop: "var(--s-3)" }}
            role="status"
          >
            <span>{syncMessage.text}</span>
          </div>
        )}

        {/* Provenance Metadata Bar */}
        {data && (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--s-4)",
              marginTop: "var(--s-3)",
              paddingTop: "var(--s-3)",
              borderTop: "1px solid var(--border)",
              fontSize: "var(--t-caption)",
              color: "var(--text-muted)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1)" }}>
              <Clock size={13} />
              <span>
                Generated: <strong>{timeAgo(data.generated_at)}</strong> ({data.generated_at})
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1)" }}>
              <Radio size={13} />
              <span>
                Quotes Source: <strong>{data.provider_source || "None"}</strong>
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1)" }}>
              <Database size={13} />
              <span>
                Fundamentals Source: <strong>{data.fundamentals_source || "None"}</strong>
              </span>
            </div>
          </div>
        )}
      </div>

      <TabGuide tabKey="universe" />

      {/* 6-KPI Summary Cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
          gap: "var(--s-3)",
        }}
      >
        <div
          data-testid="kpi-total"
          style={{
            background: "var(--surface)",
            padding: "var(--s-3)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
          }}
        >
          <div style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>
            Total Tracked
          </div>
          <div style={{ fontSize: "24px", fontWeight: 700, color: "var(--text-primary)" }}>
            {loading ? "…" : counts.total}
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginTop: "2px" }}>
            Holdings ∪ Watchlists
          </div>
        </div>

        <div
          data-testid="kpi-full"
          style={{
            background: "var(--surface)",
            padding: "var(--s-3)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
          }}
        >
          <div style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>
            Fully Covered
          </div>
          <div style={{ fontSize: "24px", fontWeight: 700, color: "var(--growth)" }}>
            {loading ? "…" : counts.full}
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--growth)", marginTop: "2px" }}>
            Quotes + Fundamentals OK
          </div>
        </div>

        <div
          data-testid="kpi-stale"
          style={{
            background: "var(--surface)",
            padding: "var(--s-3)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
          }}
        >
          <div style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>
            Stale Quotes
          </div>
          <div style={{ fontSize: "24px", fontWeight: 700, color: "var(--caution)" }}>
            {loading ? "…" : counts.stale}
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginTop: "2px" }}>
            Outdated market bars
          </div>
        </div>

        <div
          data-testid="kpi-quotes_only"
          style={{
            background: "var(--surface)",
            padding: "var(--s-3)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
          }}
        >
          <div style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>
            Quotes Only
          </div>
          <div style={{ fontSize: "24px", fontWeight: 700, color: "var(--caution)" }}>
            {loading ? "…" : counts.quotes_only}
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginTop: "2px" }}>
            Missing fundamentals
          </div>
        </div>

        <div
          data-testid="kpi-equity_only"
          style={{
            background: "var(--surface)",
            padding: "var(--s-3)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
          }}
        >
          <div style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>
            Equity Only
          </div>
          <div style={{ fontSize: "24px", fontWeight: 700, color: "var(--caution)" }}>
            {loading ? "…" : counts.equity_only}
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginTop: "2px" }}>
            Held position, no quote
          </div>
        </div>

        <div
          data-testid="kpi-uncovered"
          style={{
            background: "var(--surface)",
            padding: "var(--s-3)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
          }}
        >
          <div style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>
            Uncovered Gaps
          </div>
          <div style={{ fontSize: "24px", fontWeight: 700, color: "var(--decline)" }}>
            {loading ? "…" : counts.uncovered}
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--decline)", marginTop: "2px" }}>
            No quote or fundamentals
          </div>
        </div>
      </div>

      {/* Filter Tabs & Search Toolbar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "var(--s-3)",
          background: "var(--surface)",
          padding: "var(--s-3)",
          borderRadius: "var(--r-sm)",
          border: "1px solid var(--border)",
        }}
      >
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-1)" }}>
          <button
            type="button"
            className={`btn btn-sm ${activeTab === "all" ? "btn-primary" : "btn-neutral"}`}
            onClick={() => setActiveTab("all")}
            data-testid="filter-tab-all"
          >
            All ({counts.total})
          </button>
          <button
            type="button"
            className={`btn btn-sm ${activeTab === "gaps" ? "btn-primary" : "btn-neutral"}`}
            onClick={() => setActiveTab("gaps")}
            data-testid="filter-tab-gaps"
          >
            Issues & Gaps ({counts.gaps})
          </button>
          <button
            type="button"
            className={`btn btn-sm ${activeTab === "held" ? "btn-primary" : "btn-neutral"}`}
            onClick={() => setActiveTab("held")}
            data-testid="filter-tab-held"
          >
            Held ({counts.held})
          </button>
          <button
            type="button"
            className={`btn btn-sm ${activeTab === "watchlists" ? "btn-primary" : "btn-neutral"}`}
            onClick={() => setActiveTab("watchlists")}
            data-testid="filter-tab-watchlists"
          >
            Watchlists ({counts.watchlists})
          </button>
          <button
            type="button"
            className={`btn btn-sm ${activeTab === "excluded" ? "btn-primary" : "btn-neutral"}`}
            onClick={() => setActiveTab("excluded")}
            data-testid="filter-tab-excluded"
          >
            Excluded ({counts.excluded})
          </button>
        </div>

        <div style={{ position: "relative", minWidth: "220px" }}>
          <Search
            size={14}
            style={{
              position: "absolute",
              left: "10px",
              top: "50%",
              transform: "translateY(-50%)",
              color: "var(--text-muted)",
            }}
          />
          <input
            type="text"
            placeholder="Search ticker, watchlist..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            data-testid="search-input"
            style={{
              width: "100%",
              padding: "6px 12px 6px 30px",
              fontSize: "var(--t-caption)",
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              borderRadius: "var(--r-sm)",
              color: "var(--text-primary)",
              outline: "none",
            }}
          />
        </div>
      </div>

      {/* Main Table / Stream Verification Matrix */}
      {loading && <Loading lines={6} />}

      {!loading && error && (
        <ErrorState message={error} status={status} onRetry={reload} />
      )}

      {/* Genuinely-empty universe (no held positions, no watchlists at all)
          renders a distinct, honest cold-start message -- never the generic
          "no symbols found matching your filter criteria" text, which would
          misrepresent "nothing is tracked yet" as "your filter is too
          narrow" (mirrors UniverseCoverage.tsx's universe-coverage-empty
          state and its Symbol Screener pointer for the wider market). */}
      {!loading && !error && rows.length === 0 && (
        <div
          data-testid="universe-transparency-empty"
          style={{
            padding: "var(--s-6)",
            textAlign: "center",
            background: "var(--surface)",
            borderRadius: "var(--r-sm)",
            border: "1px dashed var(--border)",
            color: "var(--text-muted)",
          }}
        >
          <p style={{ margin: "0 0 var(--s-2)" }}>
            No symbols tracked yet — a held position or a Robinhood/watchlist-file entry will
            appear here once one exists, or click Sync Universe above to discover them.
          </p>
          <p style={{ margin: 0 }}>
            Looking for a specific symbol? Check the{" "}
            <Link to="/symbol-screener" style={{ color: "var(--growth)" }}>
              Symbol Screener
            </Link>{" "}
            for the wider market.
          </p>
        </div>
      )}

      {!loading && !error && rows.length > 0 && filteredRows.length === 0 && (
        <div
          data-testid="empty-filtered-state"
          style={{
            padding: "var(--s-6)",
            textAlign: "center",
            background: "var(--surface)",
            borderRadius: "var(--r-sm)",
            border: "1px dashed var(--border)",
            color: "var(--text-muted)",
          }}
        >
          No symbols found matching your filter criteria.
        </div>
      )}

      {!loading && !error && filteredRows.length > 0 && (
        <div
          style={{
            background: "var(--surface)",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--border)",
            overflowX: "auto",
          }}
        >
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              textAlign: "left",
              fontSize: "var(--t-caption)",
            }}
          >
            <thead>
              <tr
                style={{
                  background: "var(--surface-2)",
                  borderBottom: "1px solid var(--border)",
                  color: "var(--text-muted)",
                }}
              >
                <th style={{ padding: "var(--s-2) var(--s-3)" }}>Symbol</th>
                <th style={{ padding: "var(--s-2) var(--s-3)" }}>Coverage</th>
                <th style={{ padding: "var(--s-2) var(--s-3)" }}>Position Status</th>
                <th style={{ padding: "var(--s-2) var(--s-3)" }}>Data Streams (Quotes / Fund / Fcst)</th>
                <th style={{ padding: "var(--s-2) var(--s-3)" }}>Rating & Exclusion</th>
                <th style={{ padding: "var(--s-2) var(--s-3)" }}>Watchlists / Diagnostics</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((r) => (
                <tr
                  key={r.symbol}
                  data-testid={`universe-row-${r.symbol}`}
                  style={{
                    borderBottom: "1px solid var(--border)",
                    transition: "background 0.15s ease",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "var(--surface-2)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "transparent";
                  }}
                >
                  {/* Symbol with Explain Button */}
                  <td style={{ padding: "var(--s-2) var(--s-3)" }}>
                    <div style={{ display: "inline-flex", alignItems: "center", gap: "2px" }}>
                      <span style={{ fontWeight: 700, fontSize: "var(--t-body)", color: "var(--text-primary)" }}>
                        {r.symbol}
                      </span>
                      <ExplainTickerButton symbol={r.symbol} />
                    </div>
                  </td>

                  {/* Coverage Badge */}
                  <td style={{ padding: "var(--s-2) var(--s-3)" }}>
                    <span
                      className={`badge ${COVERAGE_BADGE_CLASS[r.coverage] ?? "badge-neutral"}`}
                      style={{ fontSize: "11px", textTransform: "uppercase" }}
                    >
                      {COVERAGE_LABEL[r.coverage] ?? r.coverage}
                    </span>
                  </td>

                  {/* Position Details */}
                  <td style={{ padding: "var(--s-2) var(--s-3)" }}>
                    {r.held ? (
                      <div>
                        <span className="badge badge-neutral" style={{ marginRight: "var(--s-1)" }}>
                          Held: {fmtNum(r.quantity, 2)} sh
                        </span>
                        <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "2px" }}>
                          Avg: {fmtUsd(r.avg_cost)} · Price: {fmtUsd(r.current_price)}
                          {r.market_value != null && ` · MV: ${fmtUsd(r.market_value)}`}
                        </div>
                      </div>
                    ) : (
                      <span style={{ color: "var(--text-muted)" }}>Not Held</span>
                    )}
                  </td>

                  {/* Stream Verification Checklist */}
                  <td style={{ padding: "var(--s-2) var(--s-3)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
                      {/* Quote stream */}
                      <span
                        title={`Quote: ${r.is_stale_quote ? "Stale" : "Live"} (${r.quote_source || "unknown"})`}
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "3px",
                          color: r.is_stale_quote ? "var(--caution)" : "var(--growth)",
                        }}
                      >
                        {r.is_stale_quote ? <Clock size={12} /> : <CheckCircle2 size={12} />}
                        <span>Quote</span>
                      </span>

                      {/* Fundamentals stream */}
                      <span
                        title={`Fundamentals: ${r.has_fundamentals ? "Present" : "Missing"}`}
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "3px",
                          color: r.has_fundamentals ? "var(--growth)" : "var(--text-muted)",
                        }}
                      >
                        {r.has_fundamentals ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
                        <span>Fund</span>
                      </span>

                      {/* Forecast stream */}
                      <span
                        title={`Forecast: ${r.forecast_available ? "Available" : "None"}`}
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "3px",
                          color: r.forecast_available ? "var(--growth)" : "var(--text-muted)",
                        }}
                      >
                        {r.forecast_available ? <CheckCircle2 size={12} /> : <HelpCircle size={12} />}
                        <span>Fcst</span>
                      </span>
                    </div>
                  </td>

                  {/* Rating Streak & Re-include */}
                  <td style={{ padding: "var(--s-2) var(--s-3)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
                      {r.rating_excluded && (
                        <span className="badge badge-bad" style={{ fontSize: "11px" }}>
                          Excluded
                        </span>
                      )}
                      {ratingCyclesCell(r.rating_consecutive_bad_cycles)}
                      {r.rating_excluded && (
                        <button
                          type="button"
                          className="btn btn-xs btn-secondary"
                          onClick={() => handleReinclude(r.symbol)}
                          disabled={reincluding === r.symbol}
                          data-testid={`reinclude-btn-${r.symbol}`}
                          style={{ marginLeft: "var(--s-1)" }}
                        >
                          {reincluding === r.symbol ? "Re-including..." : "Re-include"}
                        </button>
                      )}
                    </div>
                  </td>

                  {/* Watchlists & Diagnostics */}
                  <td style={{ padding: "var(--s-2) var(--s-3)" }}>
                    <div>
                      {r.watchlists && r.watchlists.length > 0 ? (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "2px" }}>
                          {r.watchlists.map((w) => (
                            <span
                              key={w}
                              style={{
                                padding: "1px 4px",
                                background: "var(--surface-3)",
                                borderRadius: "3px",
                                fontSize: "10px",
                                color: "var(--text-muted)",
                              }}
                            >
                              {w}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span style={{ color: "var(--text-muted)" }}>—</span>
                      )}
                      {r.diagnostic && (
                        <div
                          style={{
                            fontSize: "10px",
                            color: "var(--decline)",
                            marginTop: "2px",
                            display: "flex",
                            alignItems: "center",
                            gap: "2px",
                          }}
                        >
                          <AlertTriangle size={10} />
                          <span>{r.diagnostic}</span>
                        </div>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/**
 * UniverseTransparency -- gates the fetch itself, not just a subsequent
 * poll. Opening this route (e.g. via nav) must never itself trigger a live
 * Robinhood login: the idle view renders (and mounts no `useApi`/
 * `useAutoPoll` at all) until the "robinhood" auto-refresh category is on,
 * or the operator explicitly arms it via "Load coverage report" / a manual
 * sync. See `UniverseTransparencyIdle`'s docstring and
 * `UniverseCoverage.tsx`'s identical, already-audited precedent.
 */
export const UniverseTransparency: React.FC = () => {
  const { robinhoodRefreshEnabled } = useAutoRefresh();
  const [armed, setArmed] = useState(false);
  if (!robinhoodRefreshEnabled && !armed) {
    return <UniverseTransparencyIdle onLoad={() => setArmed(true)} />;
  }
  return <UniverseTransparencyLive />;
};

export default UniverseTransparency;
