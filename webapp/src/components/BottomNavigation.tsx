import { useState, useEffect } from "react";
import { useLocation, useNavigate } from "react-router";
import { Modal } from "./Modal";
import { theme } from "../theme";
import { NAV_ITEMS, SECTION_ORDER, SECTION_LABEL, SECTION_ROUTE } from "../navigation";
import { Menu, ArrowRight, Compass } from "lucide-react";

/** Mobile-only fixed tab bar (top-level sections; hidden above the desktop breakpoint). */
export function BottomNav() {
  const loc = useLocation();
  const nav = useNavigate();
  const path = loc.pathname;
  const [moreOpen, setMoreOpen] = useState(false);
  const [, setTick] = useState(0);

  useEffect(() => {
    const handle = () => setTick(t => t + 1);
    window.addEventListener("navItemsChanged", handle);
    return () => window.removeEventListener("navItemsChanged", handle);
  }, []);

  const primary = NAV_ITEMS.filter((it) => it.section === "primary");
  const secondary = NAV_ITEMS.filter((it) => it.section !== "primary");
  const moreActive = secondary.some((it) => it.match(path));

  const go = (to: string) => {
    setMoreOpen(false);
    nav(to);
  };

  return (
    <>
      <nav className="bottom-nav">
        {primary.map((it) => {
          const Icon = it.ico;
          return (
            <button
              key={it.to}
              className={`nav-item ${it.match(path) ? "active" : ""}`}
              onClick={() => nav(it.to)}
            >
              <span className="nav-ico" aria-hidden>
                <Icon size={20} strokeWidth={2.5} />
              </span>
              {it.label}
            </button>
          );
        })}
        <button
          className={`nav-item ${moreActive ? "active" : ""}`}
          onClick={() => setMoreOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
          data-testid="more-nav-button"
        >
          <span className="nav-ico" aria-hidden>
            <Menu size={20} strokeWidth={2.5} />
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
                      <span aria-hidden style={{ display: "inline-flex", alignItems: "center" }}>
                        <ArrowRight size={14} strokeWidth={3} />
                      </span>
                    )}
                  </h3>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {items.map((it) => {
                      const active = it.match(path);
                      const Icon = it.ico;
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
                          <span aria-hidden style={{ display: "inline-flex", alignItems: "center", color: active ? theme.growth : undefined }}>
                            <Icon size={20} strokeWidth={2.5} />
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
export function Sidebar() {
  const loc = useLocation();
  const nav = useNavigate();
  const path = loc.pathname;
  const [, setTick] = useState(0);

  useEffect(() => {
    const handle = () => setTick(t => t + 1);
    window.addEventListener("navItemsChanged", handle);
    return () => window.removeEventListener("navItemsChanged", handle);
  }, []);

  const primary = NAV_ITEMS.filter((it) => it.section === "primary");
  const secondary = NAV_ITEMS.filter((it) => it.section !== "primary");

  const renderItem = (it: (typeof NAV_ITEMS)[number]) => {
    const Icon = it.ico;
    return (
      <button
        key={it.to}
        className={`nav-item ${it.match(path) ? "active" : ""}`}
        onClick={() => nav(it.to)}
      >
        <span className="nav-ico" aria-hidden>
          <Icon size={18} strokeWidth={2.5} />
        </span>
        {it.label}
      </button>
    );
  };

  return (
    <nav className="sidebar">
      <div className="sidebar-brand">
        <span aria-hidden><Compass size={18} strokeWidth={3} /></span> Stockpy Pilots
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
                <span aria-hidden style={{ display: "inline-flex", alignItems: "center" }}>
                  <ArrowRight size={12} strokeWidth={3} />
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
