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
import { Settings } from "./screens/Settings";
import { StrategyMatrix } from "./screens/StrategyMatrix";
import { Onboarding } from "./screens/Onboarding";
import { readOnboarding } from "./onboarding";
import { usePwaStatus } from "./hooks/usePwaStatus";
import { useApi } from "./hooks/useApi";
import { api } from "./api/client";
import type { LlmStatus } from "./api/types";
import { Modal } from "./components/Modal";
import { theme } from "./theme";

/**
 * How many NAV_ITEMS the mobile bottom bar shows directly before the rest fold
 * into the "More" sheet. Kept at 3 (Dashboard/Pilots/Activity) so the primary
 * three never get evicted — see the NAV_ITEMS comment below.
 */
const MOBILE_PRIMARY_COUNT = 3;

/** Shared between the mobile bottom tab bar and the desktop sidebar. */
const NAV_ITEMS: { to: string; label: string; ico: string; match: (p: string) => boolean }[] = [
  { to: "/", label: "Dashboard", ico: "⚡", match: (p) => p === "/" },
  { to: "/marketplace", label: "Pilots", ico: "🧭", match: (p) => p.startsWith("/marketplace") || p.startsWith("/pilots") },
  { to: "/activity", label: "Activity", ico: "🔔", match: (p) => p.startsWith("/activity") },
  { to: "/portfolio", label: "Portfolio", ico: "📊", match: (p) => p.startsWith("/portfolio") },
  { to: "/compare", label: "Compare", ico: "⚖️", match: (p) => p.startsWith("/compare") },
  { to: "/models", label: "Models", ico: "🧠", match: (p) => p.startsWith("/models") },
  { to: "/pairs", label: "Pairs radar", ico: "🔗", match: (p) => p.startsWith("/pairs") },
  { to: "/options", label: "Options", ico: "🎯", match: (p) => p.startsWith("/options") },
  { to: "/attribution", label: "Attribution", ico: "🧮", match: (p) => p.startsWith("/attribution") },
  // Last item: Sidebar (desktop) renders all of NAV_ITEMS, so this shows up
  // there automatically. BottomNav (mobile) shows only the first
  // MOBILE_PRIMARY_COUNT directly; everything after folds into the "More"
  // sheet -- EXCEPT /settings, which the fixed gear button (SettingsButton)
  // already covers, so the sheet omits it to avoid two paths to one screen.
  { to: "/settings", label: "Settings", ico: "⚙", match: (p) => p.startsWith("/settings") },
];

/**
 * Fixed gear button, every screen — navigates to /settings. Formerly opened a
 * local PwaStatusDrawer bottom sheet; that content is now folded into the
 * Settings screen (a "Data & Automation" section) so the gear means one
 * thing instead of two competing "settings" affordances. Keeps the
 * needRefresh amber dot, the one thing the drawer did that a plain route
 * link can't -- surfacing "update available" from any screen without the
 * operator having to visit Settings first.
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

  const primary = NAV_ITEMS.slice(0, MOBILE_PRIMARY_COUNT);
  // Everything after the primary three, minus /settings (the gear covers it) --
  // Portfolio, Compare, Models, Pairs, Options. Driven off NAV_ITEMS so the
  // sheet can never drift from the desktop sidebar.
  const secondary = NAV_ITEMS.slice(MOBILE_PRIMARY_COUNT).filter(
    (it) => it.to !== "/settings"
  );
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
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {secondary.map((it) => {
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
  return (
    <nav className="sidebar">
      <div className="sidebar-brand">
        <span aria-hidden>🧭</span> Stockpy Pilots
      </div>
      {NAV_ITEMS.map((it) => (
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
          <Route path="/portfolio" element={<Portfolio />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/settings/strategy" element={<StrategyMatrix />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
      <BottomNav />
      <SettingsButton />
    </div>
  );
}
