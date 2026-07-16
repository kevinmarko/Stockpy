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
import { Onboarding } from "./screens/Onboarding";
import { readOnboarding } from "./onboarding";
import { PwaStatusDrawer } from "./components/PwaStatusDrawer";

/** Shared between the mobile bottom tab bar and the desktop sidebar. */
const NAV_ITEMS: { to: string; label: string; ico: string; match: (p: string) => boolean }[] = [
  { to: "/", label: "Dashboard", ico: "⚡", match: (p) => p === "/" },
  { to: "/marketplace", label: "Pilots", ico: "🧭", match: (p) => p.startsWith("/marketplace") || p.startsWith("/pilots") },
  { to: "/activity", label: "Activity", ico: "🔔", match: (p) => p.startsWith("/activity") },
  { to: "/portfolio", label: "Portfolio", ico: "📊", match: (p) => p.startsWith("/portfolio") },
  { to: "/compare", label: "Compare", ico: "⚖️", match: (p) => p.startsWith("/compare") },
  { to: "/models", label: "Models", ico: "🧠", match: (p) => p.startsWith("/models") },
  { to: "/pairs", label: "Pairs radar", ico: "🔗", match: (p) => p.startsWith("/pairs") },
];

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
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
      <BottomNav />
      <PwaStatusDrawer />
    </div>
  );
}
