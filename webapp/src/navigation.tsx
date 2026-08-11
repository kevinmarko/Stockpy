import React from "react";
import {
  Zap,
  BarChart2,
  Bell,
  Bot,
  Compass,
  Scale,
  BrainCircuit,
  Shield,
  Link,
  Target,
  Dna,
  Activity,
  Puzzle,
  TrendingUp,
  FolderOpen,
  Calculator,
  Sliders,
  Terminal,
  Satellite,
  Rocket,
  Monitor,
  Library,
  CircleHelp,
  Settings,
  Briefcase,
  LayoutTemplate,
} from "lucide-react";

export type NavSection = "primary" | "research" | "trading" | "operations" | "settings";

export const SECTION_LABEL: Record<Exclude<NavSection, "primary">, string> = {
  research: "Research",
  trading: "Trading Tools",
  operations: "Operations",
  settings: "Settings",
};

export const SECTION_ORDER: Exclude<NavSection, "primary">[] = ["trading", "research", "operations", "settings"];

export const SECTION_ROUTE: Partial<Record<Exclude<NavSection, "primary">, string>> = {
  research: "/research",
  trading: "/trading",
  operations: "/operations",
};

export interface NavItem {
  to: string;
  label: string;
  ico: React.ElementType;
  match: (p: string) => boolean;
  section: NavSection;
}

export const NAV_ITEMS: NavItem[] = [
  // Primary
  { to: "/", label: "Dashboard", ico: Zap, match: (p) => p === "/", section: "primary" },
  { to: "/portfolio", label: "Portfolio", ico: BarChart2, match: (p) => p.startsWith("/portfolio"), section: "primary" },
  { to: "/activity", label: "Activity", ico: Bell, match: (p) => p.startsWith("/activity"), section: "primary" },
  { to: "/agentic", label: "Agent", ico: Bot, match: (p) => p.startsWith("/agentic"), section: "primary" },
  // Research
  { to: "/marketplace", label: "Pilots", ico: Compass, match: (p) => p.startsWith("/marketplace") || p.startsWith("/pilots"), section: "research" },
  { to: "/compare", label: "Compare", ico: Scale, match: (p) => p.startsWith("/compare"), section: "research" },
  { to: "/models", label: "Models", ico: BrainCircuit, match: (p) => p.startsWith("/models"), section: "research" },
  { to: "/strategy-health", label: "Strategy Health", ico: Shield, match: (p) => p.startsWith("/strategy-health"), section: "research" },
  { to: "/pairs", label: "Pairs radar", ico: Link, match: (p) => p.startsWith("/pairs"), section: "research" },
  { to: "/options", label: "Options", ico: Target, match: (p) => p.startsWith("/options"), section: "research" },
  { to: "/signals", label: "Signal Breakdown", ico: Dna, match: (p) => p.startsWith("/signals"), section: "research" },
  { to: "/sentiment", label: "Sentiment Dynamics", ico: Activity, match: (p) => p.startsWith("/sentiment"), section: "research" },
  { to: "/sector-selection", label: "Sector Selection", ico: Puzzle, match: (p) => p.startsWith("/sector-selection"), section: "research" },
  { to: "/forecast", label: "Forecast Viewer", ico: TrendingUp, match: (p) => p === "/forecast", section: "research" },
  { to: "/forecast/backfill", label: "Forecast Backfill", ico: TrendingUp, match: (p) => p.startsWith("/forecast/backfill"), section: "research" },
  { to: "/data-explorer", label: "Data Explorer", ico: FolderOpen, match: (p) => p.startsWith("/data-explorer"), section: "research" },
  // Trading Tools
  { to: "/attribution", label: "Attribution", ico: Calculator, match: (p) => p.startsWith("/attribution"), section: "trading" },
  { to: "/calibration", label: "Calibration", ico: Sliders, match: (p) => p.startsWith("/calibration"), section: "trading" },
  { to: "/commands", label: "Commands", ico: Terminal, match: (p) => p.startsWith("/commands"), section: "trading" },
  { to: "/cache-long-short", label: "Cache L/S", ico: Briefcase, match: (p) => p.startsWith("/cache-long-short"), section: "trading" },
  // Operations
  { to: "/observability", label: "Mission Control", ico: Satellite, match: (p) => p.startsWith("/observability"), section: "operations" },
  { to: "/pipeline", label: "Pipeline", ico: Rocket, match: (p) => p.startsWith("/pipeline"), section: "operations" },
  { to: "/console", label: "Console", ico: Monitor, match: (p) => p.startsWith("/console"), section: "operations" },
  { to: "/operations/reports", label: "Report Library", ico: Library, match: (p) => p.startsWith("/operations/reports"), section: "operations" },
  { to: "/create-data-app", label: "Create Data App", ico: LayoutTemplate, match: (p) => p.startsWith("/create-data-app"), section: "operations" },
  { to: "/help", label: "Help & Glossary", ico: CircleHelp, match: (p) => p.startsWith("/help"), section: "operations" },
  // Settings
  { to: "/settings", label: "Settings", ico: Settings, match: (p) => p.startsWith("/settings"), section: "settings" },
];

// ---------------------------------------------------------------------------
// Dynamic nav items (Save to Dashboard from Create Data App).
//
// A `NavItem`'s `ico`/`match` fields aren't serializable (a React component
// reference and a closure), so only `{to, label}` is persisted -- `ico` is
// always LayoutTemplate and `match` is always a startsWith(to) check for a
// restored item, matching what CreateDataApp.tsx already passes when it
// first creates one. This is enough to survive a page reload (localStorage,
// same device/browser); it is NOT synced anywhere server-side -- a
// different device/browser, or a cleared localStorage, won't see it. See
// `create_data_app`/`save_data_app` in api/pilots_api.py for the matching
// backend-side caveat (no Data App data model exists yet either).
// ---------------------------------------------------------------------------

const _DYNAMIC_NAV_STORAGE_KEY = "investyo.dynamicNavItems.v1";

interface _StoredDynamicNavItem {
  to: string;
  label: string;
}

function _readStoredDynamicNavItems(): _StoredDynamicNavItem[] {
  try {
    const raw = window.localStorage.getItem(_DYNAMIC_NAV_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (x): x is _StoredDynamicNavItem =>
        x && typeof x.to === "string" && typeof x.label === "string"
    );
  } catch {
    // Unavailable (SSR/private-browsing) or corrupt -- degrade to "nothing
    // saved yet" rather than throwing.
    return [];
  }
}

function _writeStoredDynamicNavItems(items: _StoredDynamicNavItem[]) {
  try {
    window.localStorage.setItem(_DYNAMIC_NAV_STORAGE_KEY, JSON.stringify(items));
  } catch {
    // Best-effort only -- a full/unavailable localStorage must not break
    // the in-memory nav update that already happened.
  }
}

/** Restore any previously-saved Data Apps into NAV_ITEMS. Call once at
 * startup (App.tsx). Safe to call more than once -- later calls are no-ops
 * because the `to` path already exists in NAV_ITEMS. */
export function restoreDynamicNavItems() {
  for (const stored of _readStoredDynamicNavItems()) {
    if (NAV_ITEMS.some((it) => it.to === stored.to)) continue;
    NAV_ITEMS.push({
      to: stored.to,
      label: stored.label,
      ico: LayoutTemplate,
      match: (p) => p.startsWith(stored.to),
      section: "operations",
    });
  }
}

export function addDynamicNavItem(item: NavItem) {
  const existingIdx = NAV_ITEMS.findIndex((it) => it.to === item.to);
  if (existingIdx >= 0) {
    NAV_ITEMS[existingIdx] = item;
  } else {
    NAV_ITEMS.push(item);
  }

  const stored = _readStoredDynamicNavItems().filter((s) => s.to !== item.to);
  stored.push({ to: item.to, label: item.label });
  _writeStoredDynamicNavItems(stored);

  window.dispatchEvent(new Event("navItemsChanged"));
}

