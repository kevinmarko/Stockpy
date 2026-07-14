import { useState } from "react";
import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { Marketplace } from "./screens/Marketplace";
import { PilotDetail } from "./screens/PilotDetail";
import { Portfolio } from "./screens/Portfolio";
import { SymbolDetail } from "./screens/SymbolDetail";
import { Onboarding } from "./screens/Onboarding";
import { readOnboarding } from "./onboarding";

function BottomNav() {
  const loc = useLocation();
  const nav = useNavigate();
  const path = loc.pathname;
  const items: { to: string; label: string; ico: string; match: (p: string) => boolean }[] = [
    { to: "/", label: "Pilots", ico: "🧭", match: (p) => p === "/" || p.startsWith("/pilots") },
    { to: "/portfolio", label: "Portfolio", ico: "📊", match: (p) => p.startsWith("/portfolio") },
  ];
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

export default function App() {
  const [done, setDone] = useState(() => readOnboarding().completed);

  if (!done) {
    return (
      <div className="app">
        <Routes>
          <Route path="*" element={<Onboarding onDone={() => setDone(true)} />} />
        </Routes>
      </div>
    );
  }

  return (
    <div className="app">
      <Routes>
        <Route path="/" element={<Marketplace />} />
        <Route path="/pilots/:id" element={<PilotDetail />} />
        <Route path="/symbol/:ticker" element={<SymbolDetail />} />
        <Route path="/portfolio" element={<Portfolio />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <BottomNav />
    </div>
  );
}
