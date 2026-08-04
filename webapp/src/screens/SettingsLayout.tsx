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
  ];

  return (
    <div className="screen" style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)", height: "100%" }}>
      <GlobalStatusBanner />
      
      <div style={{ display: "flex", gap: "var(--s-6)", flex: 1, alignItems: "flex-start" }}>
        {/* Sidebar */}
        <nav
          style={{
            width: "250px",
            display: "flex",
            flexDirection: "column",
            gap: "var(--s-2)",
            flexShrink: 0,
            position: "sticky",
            top: "var(--s-4)"
          }}
        >
          <h1 style={{ fontSize: "var(--t-title)", marginBottom: "var(--s-2)" }}>Settings</h1>
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
        </nav>

        {/* Content Area */}
        <div style={{ flex: 1, minWidth: 0, paddingBottom: "var(--s-8)" }}>
          <Outlet />
        </div>
      </div>
    </div>
  );
}
