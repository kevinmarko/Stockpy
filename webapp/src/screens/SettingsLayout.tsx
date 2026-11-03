import { useEffect, useRef } from "react";
import { Outlet, NavLink } from "react-router";
import { theme } from "../theme";
import { GlobalStatusBanner } from "../components/GlobalStatusBanner";

export function SettingsLayout() {
  const navItems = [
    { path: "/settings", end: true, label: "⚙️ General & Execution" },
    { path: "/settings/data", label: "🔄 Data & Schedule" },
    { path: "/settings/universe", label: "🎯 Tracked Universe" },
    { path: "/settings/brokers", label: "🔑 Brokers & Keys" },
    { path: "/settings/modules", label: "🎛️ Tunables & Modules" },
    { path: "/settings/feature-flags", label: "🚩 Feature Flags" },
  ];

  // On a phone the links are a scrollable strip: centre the active one on mount. Set scrollLeft
  // directly (scrollIntoView could also scroll the page, and jsdom lacks it).
  const linksRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const strip = linksRef.current;
    const active = strip?.querySelector<HTMLElement>("a.active");
    if (strip && active) strip.scrollLeft = active.offsetLeft - (strip.clientWidth - active.offsetWidth) / 2;
  }, []);

  return (
    <div className="screen" style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)", height: "100%" }}>
      <GlobalStatusBanner />
      
      {/* Layout lives in index.css (.split-*): stacked on phones, rail + content from 768px.
          It was an inline fixed 250px sidebar, which left the content ~60px wide on a phone. */}
      <div className="split-pane" style={{ flex: 1 }}>
        {/* Sidebar */}
        <nav className="split-rail">
          <h1 style={{ fontSize: "var(--t-title)", marginBottom: "var(--s-2)" }}>Settings</h1>
          <div className="split-rail-links" ref={linksRef}>
            {navItems.map((item) => (
              <NavLink
                key={item.path}
                to={item.path}
                end={item.end}
                style={({ isActive }) => ({
                  padding: "var(--s-2) var(--s-3)",
                  borderRadius: "var(--r-md)",
                  textDecoration: "none",
                  color: isActive ? theme.textPrimary : theme.textSecondary,
                  background: isActive ? theme.surface3 : "transparent",
                  fontWeight: isActive ? 600 : 400,
                  transition: "background 0.2s, color 0.2s",
                })}
              >
                {item.label}
              </NavLink>
            ))}
          </div>
        </nav>

        {/* Content Area */}
        <div className="split-content">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
