import { useState } from "react";
import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { Dashboard } from "./screens/Dashboard";
import { Comparison } from "./screens/Comparison";
import { Marketplace } from "./screens/Marketplace";
import { PilotDetail } from "./screens/PilotDetail";
import { Portfolio } from "./screens/Portfolio";
import { SymbolDetail } from "./screens/SymbolDetail";
import { Activity } from "./screens/Activity";
import { Models } from "./screens/Models";
import { PairsRadar } from "./screens/PairsRadar";
import { OptionsMatrix } from "./screens/OptionsMatrix";
import { Attribution } from "./screens/Attribution";
import { Observability } from "./screens/Observability";
import { StrategyHealth } from "./screens/StrategyHealth";
import { Calibration } from "./screens/Calibration";
import { PipelineDashboard } from "./screens/PipelineDashboard";
import { Settings } from "./screens/Settings";
import { StrategyMatrix } from "./screens/StrategyMatrix";
import { SettingsManager } from "./screens/SettingsManager";
import { AIControlCenter } from "./screens/AIControlCenter";
import { DataExplorer } from "./screens/DataExplorer";
import { SignalBreakdown } from "./screens/SignalBreakdown";
import { ForecastViewer } from "./screens/ForecastViewer";
import { Commands } from "./screens/Commands";
import { AgenticTrading } from "./screens/AgenticTrading";
import { ResearchHub } from "./screens/ResearchHub";
import { TradingHub } from "./screens/TradingHub";
import { OperationsHub } from "./screens/OperationsHub";
import { Onboarding } from "./screens/Onboarding";
import { readOnboarding } from "./onboarding";
import { usePwaStatus } from "./hooks/usePwaStatus";
import { useApi } from "./hooks/useApi";
import { api } from "./api/client";
import type { LlmStatus } from "./api/types";
import { Modal } from "./components/Modal";
import { theme } from "./theme";

/**
 * Which nav group a screen belongs to. Replaces the old MOBILE_PRIMARY_COUNT
 * array-slice split (whichever 3 items happened to be listed first) with an
 * explicit, per-item classification that can't silently drift when items are
 * added or reordered.
 *
 * "primary" -> the 3 always-visible mobile bottom-nav tabs. Chosen by actual
 * usage frequency (a 2026-07 UX audit), not by original insertion order:
 * Dashboard/Portfolio/Activity are checked constantly; everything else is an
 * occasional deep-dive. "settings" is its own single-item group so Settings
 * is reachable the same way as every other screen (mobile More sheet /
 * desktop sidebar) -- the persistent gear button (SettingsButton) remains an
 * ADDITIONAL fast-access shortcut (it also carries the update/LLM-attention
 * dots), not the only path.
 */
type NavSection = "primary" | "research" | "trading" | "operations" | "settings";

/** Group header shown above each non-primary cluster in the mobile "More" sheet and the desktop sidebar. */
const SECTION_LABEL: Record<Exclude<NavSection, "primary">, string> = {
  research: "Research",
  trading: "Trading Tools",
  operations: "Operations",
  settings: "Settings",
};

/** Render order for the non-primary groups (most to least frequently visited). */
const SECTION_ORDER: Exclude<NavSection, "primary">[] = ["research", "trading", "operations", "settings"];

/**
 * Hub screen route for each section, tapped from the section header itself
 * (mobile More sheet + desktop sidebar). "settings" has no hub screen -- it's
 * a single item (Settings) so its header stays plain, non-interactive text.
 */
const SECTION_ROUTE: Partial<Record<Exclude<NavSection, "primary">, string>> = {
  research: "/research",
  trading: "/trading",
  operations: "/operations",
};

/** Shared between the mobile bottom tab bar and the desktop sidebar. */
const NAV_ITEMS: { to: string; label: string; ico: string; match: (p: string) => boolean; section: NavSection }[] = [
  // Primary — checked constantly, always one tap away on mobile.
  { to: "/", label: "Dashboard", ico: "⚡", match: (p) => p === "/", section: "primary" },
  { to: "/portfolio", label: "Portfolio", ico: "📊", match: (p) => p.startsWith("/portfolio"), section: "primary" },
  { to: "/activity", label: "Activity", ico: "🔔", match: (p) => p.startsWith("/activity"), section: "primary" },
  // Research — vetting Pilots, symbols, and strategies before you act.
  { to: "/marketplace", label: "Pilots", ico: "🧭", match: (p) => p.startsWith("/marketplace") || p.startsWith("/pilots"), section: "research" },
  { to: "/compare", label: "Compare", ico: "⚖️", match: (p) => p.startsWith("/compare"), section: "research" },
  { to: "/models", label: "Models", ico: "🧠", match: (p) => p.startsWith("/models"), section: "research" },
  { to: "/strategy-health", label: "Strategy Health", ico: "🛡️", match: (p) => p.startsWith("/strategy-health"), section: "research" },
  { to: "/pairs", label: "Pairs radar", ico: "🔗", match: (p) => p.startsWith("/pairs"), section: "research" },
  { to: "/options", label: "Options", ico: "🎯", match: (p) => p.startsWith("/options"), section: "research" },
  { to: "/signals", label: "Signal Breakdown", ico: "🧬", match: (p) => p.startsWith("/signals"), section: "research" },
  { to: "/forecast", label: "Forecast Viewer", ico: "📈", match: (p) => p.startsWith("/forecast"), section: "research" },
  { to: "/data-explorer", label: "Data Explorer", ico: "🗂️", match: (p) => p.startsWith("/data-explorer"), section: "research" },
  // Trading Tools — grading and acting on your own portfolio.
  { to: "/attribution", label: "Attribution", ico: "🧮", match: (p) => p.startsWith("/attribution"), section: "trading" },
  { to: "/calibration", label: "Calibration", ico: "🎚️", match: (p) => p.startsWith("/calibration"), section: "trading" },
  { to: "/agentic", label: "Agent", ico: "🤖", match: (p) => p.startsWith("/agentic"), section: "trading" },
  { to: "/commands", label: "Commands", ico: "⌨️", match: (p) => p.startsWith("/commands"), section: "trading" },
  // Operations — the platform/pipeline itself, not a symbol or your money.
  { to: "/observability", label: "Mission Control", ico: "🛰️", match: (p) => p.startsWith("/observability"), section: "operations" },
  { to: "/pipeline", label: "Pipeline", ico: "🚀", match: (p) => p.startsWith("/pipeline"), section: "operations" },
  // Settings — also has the always-on gear shortcut (SettingsButton) below.
  { to: "/settings", label: "Settings", ico: "⚙", match: (p) => p.startsWith("/settings"), section: "settings" },
];

/**
 * Fixed gear button, every screen — navigates to /settings. Formerly opened a
 * local PwaStatusDrawer bottom sheet; that content is now folded into the
 * Settings screen (a "Data & Automation" section) so the gear means one
 * thing instead of two competing "settings" affordances. Keeps the
 * needRefresh amber dot, the one thing the drawer did that a plain route
 * link can't -- surfacing "update available" from any screen without the
 * operator having to visit Settings first. Settings is ALSO listed like any
 * other screen (mobile More sheet's "Settings" group / desktop sidebar) --
 * this button is a fast-access shortcut on top of that, not the only path.
 */
function SettingsButton() {
  const nav = useNavigate();
  const pwa = usePwaStatus();
  // ONE fetch per app load -- SettingsButton lives in App's shell (outside
  // <Routes>), so it mounts once and does NOT re-mount on navigation. No
  // usePoll: LLM config changes on an operator's .env edit, not on a timer.
  // On failure `llm` stays undefined -> no dot: an absent dot is the ABSENCE
  // of a claim, never a fabricated all-clear NOR a false key alarm when the
  // real problem is the network (the Settings screen shows the honest error).
  const { data: llm } = useApi<LlmStatus>(() => api.getLlmStatus(), []);
  const llmAttention = llm?.attention === true;
  return (
    <button
      className="btn"
      onClick={() => nav("/settings")}
      aria-label="Settings"
      data-testid="settings-button"
      style={{
        position: "fixed",
        right: 16,
        bottom: 76, // clears the mobile bottom-nav; desktop has no bottom-nav so this just floats
        zIndex: 40,
        width: 40,
        height: 40,
        borderRadius: "50%",
        padding: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: theme.surface2,
        border: `1px solid ${theme.borderStrong}`,
      }}
    >
      <span aria-hidden style={{ fontSize: 16 }}>
        ⚙
      </span>
      {pwa.needRefresh && (
        <span
          aria-hidden
          data-testid="pwa-update-dot"
          style={{
            position: "absolute",
            top: 2,
            right: 2,
            width: 9,
            height: 9,
            borderRadius: "50%",
            background: theme.caution,
            border: `2px solid ${theme.base}`,
          }}
        />
      )}
      {llmAttention && (
        <span
          aria-hidden
          data-testid="llm-config-dot"
          title="An enabled AI capability is missing or was rejected a key"
          style={{
            position: "absolute",
            top: 2,
            left: 2,
            width: 9,
            height: 9,
            borderRadius: "50%",
            background: theme.caution,
            border: `2px solid ${theme.base}`,
          }}
        />
      )}
    </button>
  );
}

/** Mobile-only fixed tab bar (top-level sections; hidden above the desktop breakpoint). */
function BottomNav() {
  const loc = useLocation();
  const nav = useNavigate();
  const path = loc.pathname;
  const [moreOpen, setMoreOpen] = useState(false);

  const primary = NAV_ITEMS.filter((it) => it.section === "primary");
  // Everything non-primary, grouped by section for the sheet. Driven off
  // NAV_ITEMS so it can never drift from the desktop sidebar.
  const secondary = NAV_ITEMS.filter((it) => it.section !== "primary");
  const moreActive = secondary.some((it) => it.match(path));

  const go = (to: string) => {
    setMoreOpen(false);
    nav(to);
  };

  return (
    <>
      <nav className="bottom-nav">
        {primary.map((it) => (
          <button
            key={it.to}
            className={`nav-item ${it.match(path) ? "active" : ""}`}
            onClick={() => nav(it.to)}
          >
            <span className="nav-ico" aria-hidden>
              {it.ico}
            </span>
            {it.label}
          </button>
        ))}
        <button
          className={`nav-item ${moreActive ? "active" : ""}`}
          onClick={() => setMoreOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
          data-testid="more-nav-button"
        >
          <span className="nav-ico" aria-hidden>
            ☰
          </span>
          More
        </button>
      </nav>
      {moreOpen && (
        <Modal ariaLabel="More sections" onClose={() => setMoreOpen(false)}>
          <h2 style={{ margin: "0 0 12px", fontSize: "var(--t-title)" }}>More</h2>
          <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            {SECTION_ORDER.map((section) => {
              const items = secondary.filter((it) => it.section === section);
              if (items.length === 0) return null;
              const hubRoute = SECTION_ROUTE[section];
              return (
                <div key={section}>
                  <h3
                    onClick={hubRoute ? () => go(hubRoute) : undefined}
                    tabIndex={hubRoute ? 0 : undefined}
                    onKeyDown={
                      hubRoute
                        ? (e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              go(hubRoute);
                            }
                          }
                        : undefined
                    }
                    style={{
                      margin: "0 0 8px",
                      fontSize: 12,
                      fontWeight: 700,
                      letterSpacing: "0.04em",
                      textTransform: "uppercase",
                      color: theme.textMuted,
                      display: "flex",
                      alignItems: "center",
                      gap: 4,
                      cursor: hubRoute ? "pointer" : "default",
                    }}
                  >
                    {SECTION_LABEL[section]}
                    {hubRoute && (
                      <span aria-hidden style={{ fontSize: 11 }}>
                        →
                      </span>
                    )}
                  </h3>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {items.map((it) => {
                      const active = it.match(path);
                      return (
                        <button
                          key={it.to}
                          onClick={() => go(it.to)}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 12,
                            width: "100%",
                            justifyContent: "flex-start",
                            padding: "12px 14px",
                            minHeight: 48,
                            background: active ? theme.surface2 : "transparent",
                            border: `1px solid ${active ? theme.borderStrong : theme.border}`,
                            borderRadius: 10,
                            color: active ? theme.textPrimary : theme.textSecondary,
                            fontSize: 15,
                            fontWeight: 600,
                            cursor: "pointer",
                          }}
                        >
                          <span aria-hidden style={{ fontSize: 20 }}>
                            {it.ico}
                          </span>
                          <span>{it.label}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </Modal>
      )}
    </>
  );
}

/** Desktop-only left sidebar (hidden below the desktop breakpoint — see .sidebar in index.css). */
function Sidebar() {
  const loc = useLocation();
  const nav = useNavigate();
  const path = loc.pathname;
  const primary = NAV_ITEMS.filter((it) => it.section === "primary");
  const secondary = NAV_ITEMS.filter((it) => it.section !== "primary");

  const renderItem = (it: (typeof NAV_ITEMS)[number]) => (
    <button
      key={it.to}
      className={`nav-item ${it.match(path) ? "active" : ""}`}
      onClick={() => nav(it.to)}
    >
      <span className="nav-ico" aria-hidden>
        {it.ico}
      </span>
      {it.label}
    </button>
  );

  return (
    <nav className="sidebar">
      <div className="sidebar-brand">
        <span aria-hidden>🧭</span> Stockpy Pilots
      </div>
      {primary.map(renderItem)}
      {SECTION_ORDER.map((section) => {
        const items = secondary.filter((it) => it.section === section);
        if (items.length === 0) return null;
        const hubRoute = SECTION_ROUTE[section];
        return (
          <div key={section} style={{ marginTop: 14 }}>
            <div
              onClick={hubRoute ? () => nav(hubRoute) : undefined}
              tabIndex={hubRoute ? 0 : undefined}
              onKeyDown={
                hubRoute
                  ? (e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        nav(hubRoute);
                      }
                    }
                  : undefined
              }
              style={{
                margin: "0 10px 4px",
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: "0.04em",
                textTransform: "uppercase",
                color: theme.textMuted,
                display: "flex",
                alignItems: "center",
                gap: 4,
                cursor: hubRoute ? "pointer" : "default",
              }}
            >
              {SECTION_LABEL[section]}
              {hubRoute && (
                <span aria-hidden style={{ fontSize: 10 }}>
                  →
                </span>
              )}
            </div>
            {items.map(renderItem)}
          </div>
        );
      })}
    </nav>
  );
}

export default function App() {
  const [done, setDone] = useState(() => readOnboarding().completed);

  if (!done) {
    return (
      <div className="app app-standalone">
        <Routes>
          <Route path="*" element={<Onboarding onDone={() => setDone(true)} />} />
        </Routes>
      </div>
    );
  }

  return (
    <div className="app app-shell">
      <Sidebar />
      <div className="app-main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/marketplace" element={<Marketplace />} />
          <Route path="/compare" element={<Comparison />} />
          <Route path="/pilots/:id" element={<PilotDetail />} />
          <Route path="/symbol/:ticker" element={<SymbolDetail />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/models" element={<Models />} />
          <Route path="/pairs" element={<PairsRadar />} />
          <Route path="/options" element={<OptionsMatrix />} />
          <Route path="/attribution" element={<Attribution />} />
          <Route path="/observability" element={<Observability />} />
          <Route path="/strategy-health" element={<StrategyHealth />} />
          <Route path="/calibration" element={<Calibration />} />
          <Route path="/pipeline" element={<PipelineDashboard />} />
          <Route path="/data-explorer" element={<DataExplorer />} />
          <Route path="/signals" element={<SignalBreakdown />} />
          <Route path="/forecast" element={<ForecastViewer />} />
          <Route path="/commands" element={<Commands />} />
          <Route path="/agentic" element={<AgenticTrading />} />
          <Route path="/research" element={<ResearchHub />} />
          <Route path="/trading" element={<TradingHub />} />
          <Route path="/operations" element={<OperationsHub />} />
          <Route path="/portfolio" element={<Portfolio />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/settings/strategy" element={<StrategyMatrix />} />
          <Route path="/settings/tunables" element={<SettingsManager />} />
          <Route path="/settings/ai" element={<AIControlCenter />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
      <BottomNav />
      <SettingsButton />
    </div>
  );
}
