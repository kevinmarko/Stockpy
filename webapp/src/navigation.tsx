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
  { to: "/forecast", label: "Forecast Viewer", ico: TrendingUp, match: (p) => p.startsWith("/forecast"), section: "research" },
  { to: "/data-explorer", label: "Data Explorer", ico: FolderOpen, match: (p) => p.startsWith("/data-explorer"), section: "research" },
  // Trading Tools
  { to: "/attribution", label: "Attribution", ico: Calculator, match: (p) => p.startsWith("/attribution"), section: "trading" },
  { to: "/calibration", label: "Calibration", ico: Sliders, match: (p) => p.startsWith("/calibration"), section: "trading" },
  { to: "/commands", label: "Commands", ico: Terminal, match: (p) => p.startsWith("/commands"), section: "trading" },
  // Operations
  { to: "/observability", label: "Mission Control", ico: Satellite, match: (p) => p.startsWith("/observability"), section: "operations" },
  { to: "/pipeline", label: "Pipeline", ico: Rocket, match: (p) => p.startsWith("/pipeline"), section: "operations" },
  { to: "/console", label: "Console", ico: Monitor, match: (p) => p.startsWith("/console"), section: "operations" },
  { to: "/operations/reports", label: "Report Library", ico: Library, match: (p) => p.startsWith("/operations/reports"), section: "operations" },
  { to: "/help", label: "Help & Glossary", ico: CircleHelp, match: (p) => p.startsWith("/help"), section: "operations" },
  // Settings
  { to: "/settings", label: "Settings", ico: Settings, match: (p) => p.startsWith("/settings"), section: "settings" },
];
