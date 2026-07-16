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
import { Settings } from "./screens/Settings";
import { Onboarding } from "./screens/Onboarding";
import { readOnboarding } from "./onboarding";
import { usePwaStatus } from "./hooks/usePwaStatus";
import { theme } from "./theme";

/** Shared between the mobile bottom tab bar and the desktop sidebar. */
const NAV_ITEMS: { to: string; label: string; ico: string; match: (p: string) => boolean }[] = [
  { to: "/", label: "Dashboard", ico: "⚡", match: (p) => p === "/" },
  { to: "/marketplace", label: "Pilots", ico: "🧭", match: (p) => p.startsWith("/marketplace") || p.startsWith("/pilots") },
  { to: "/activity", label: "Activity", ico: "🔔", match: (p) => p.startsWith("/activity") },
  { to: "/portfolio", label: "Portfolio", ico: "📊", match: (p) => p.startsWith("/portfolio") },
  { to: "/compare", label: "Compare", ico: "⚖️", match: (p) => p.startsWith("/compare") },
  { to: "/models", label: "Models", ico: "🧠", match: (p) => p.startsWith("/models") },
  { to: "/pairs", label: "Pairs radar", ico: "🔗", match: (p) => p.startsWith("/pairs") },
  // 8th item: Sidebar (desktop) renders all of NAV_ITEMS, so this shows up
  // there automatically. BottomNav (mobile) only ever renders
  // NAV_ITEMS.slice(0, 3) -- deliberately NOT reordering to force this in,
  // since that would evict Activity. On mobile the fixed gear button below
  // (SettingsButton) is the entry point instead.
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
    </button>
  );
}

/** Mobile-only fixed tab bar (top-level sections; hidden above the desktop breakpoint). */
function BottomNav() {
  const loc = useLocation();
  const nav = useNavigate();
  const path = loc.pathname;
  const items = NAV_ITEMS.slice(0, 3);
  return (
    <nav className="bottom-nav">
      {items.map((it) => (
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
          <Route path="/portfolio" element={<Portfolio />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
      <BottomNav />
      <SettingsButton />
    </div>
  );
}
