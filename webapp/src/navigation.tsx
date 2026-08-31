import React, { useMemo } from "react";
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
  Filter,
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
  LineChart,
  LayoutTemplate,
  ShieldCheck,
} from "lucide-react";
import { useCustomViews } from "./customViews";

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
  {
    to: "/paper-broker",
    label: "Paper Broker",
    ico: () => (

      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M14 2H6a2 2 0 0 0-2 2v16c0 1.1.9 2 2 2h12a2 2 0 0 0 2-2V8l-6-6z" />
        <path d="M14 3v5h5M16 13H8M16 17H8M10 9H8" />
      </svg>
    ),
    match: (p) => p.startsWith("/paper-broker"),
    section: "trading",
  },

  // Primary
  { to: "/", label: "Dashboard", ico: Zap, match: (p) => p === "/", section: "primary" },
  { to: "/portfolio", label: "Portfolio", ico: BarChart2, match: (p) => p.startsWith("/portfolio"), section: "primary" },
  { to: "/activity", label: "Activity", ico: Bell, match: (p) => p.startsWith("/activity"), section: "primary" },
  { to: "/agentic", label: "Agent", ico: Bot, match: (p) => p.startsWith("/agentic"), section: "primary" },
  // Research
  { to: "/marketplace", label: "Pilots", ico: Compass, match: (p) => p.startsWith("/marketplace") || p.startsWith("/pilots"), section: "research" },
  { to: "/pilots-manager", label: "Pilots Manager", ico: Bot, match: (p) => p.startsWith("/pilots-manager"), section: "research" },
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
  { to: "/symbol-screener", label: "Symbol Screener", ico: Filter, match: (p) => p.startsWith("/symbol-screener"), section: "research" },
  { to: "/trade-history", label: "Trade History", ico: Briefcase, match: (p) => p.startsWith("/trade-history"), section: "research" },
  { to: "/research/trends-stitcher", label: "Trends Stitching", ico: Activity, match: (p) => p.startsWith("/research/trends-stitcher"), section: "research" },
  // Trading Tools
  { to: "/attribution", label: "Attribution", ico: Calculator, match: (p) => p.startsWith("/attribution"), section: "trading" },
  { to: "/calibration", label: "Calibration", ico: Sliders, match: (p) => p.startsWith("/calibration"), section: "trading" },
  { to: "/commands", label: "Commands", ico: Terminal, match: (p) => p.startsWith("/commands"), section: "trading" },
  { to: "/cache-long-short", label: "Cache L/S", ico: Briefcase, match: (p) => p.startsWith("/cache-long-short"), section: "trading" },
  { to: "/strategy-insights", label: "Strategy Insights", ico: LineChart, match: (p) => p.startsWith("/strategy-insights"), section: "trading" },
  // Operations
  { to: "/live-trade-approvals", label: "Live Trade Approvals", ico: ShieldCheck, match: (p) => p.startsWith("/live-trade-approvals"), section: "operations" },
  { to: "/observability", label: "Mission Control", ico: Satellite, match: (p) => p.startsWith("/observability"), section: "operations" },
  { to: "/pipeline", label: "Pipeline", ico: Rocket, match: (p) => p.startsWith("/pipeline"), section: "operations" },
  { to: "/console", label: "Console", ico: Monitor, match: (p) => p.startsWith("/console"), section: "operations" },
  { to: "/operations/reports", label: "Report Library", ico: Library, match: (p) => p.startsWith("/operations/reports"), section: "operations" },
  { to: "/create-data-app", label: "Create Data App", ico: LayoutTemplate, match: (p) => p.startsWith("/create-data-app"), section: "operations" },
  { to: "/help", label: "Help & Glossary", ico: CircleHelp, match: (p) => p.startsWith("/help"), section: "operations" },
  // Settings
  { to: "/settings", label: "Settings", ico: Settings, match: (p) => p.startsWith("/settings"), section: "settings" },
];

/**
 * NAV_ITEMS plus one entry per operator-saved custom view (see
 * customViews.ts), each routing to /app/:slug. Real, reactive nav injection
 * -- `useCustomViews()` is backed by `useSyncExternalStore`, so both
 * `Sidebar` and `BottomNav` (components/BottomNavigation.tsx) re-render the
 * instant a view is created/renamed/deleted from any screen or browser tab,
 * with no page reload required.
 */
export function useNavItems(): NavItem[] {
  const { views } = useCustomViews();
  return useMemo(
    () => [
      ...NAV_ITEMS,
      ...views.map(
        (v): NavItem => ({
          to: `/app/${v.slug}`,
          label: v.name,
          ico: LayoutTemplate,
          match: (p) => p === `/app/${v.slug}`,
          section: "operations",
        })
      ),
    ],
    [views]
  );
}
