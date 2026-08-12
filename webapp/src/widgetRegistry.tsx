import type { ComponentType } from "react";
import type { CustomViewWidgets } from "./customViews";
import { EdgeByStrategyChart } from "./components/EdgeByStrategyChart";
import { SymbolSignalOverlayChart } from "./components/SymbolSignalOverlayChart";
import { PilotsTableWidget } from "./components/PilotsTableWidget";
import { SentimentMiniChart } from "./components/SentimentMiniChart";
import { PortfolioHeatWidget } from "./components/PortfolioHeatWidget";
import { OptionsDirectiveSummary } from "./components/OptionsDirectiveSummary";
import { SignalBreakdownMiniWidget } from "./components/SignalBreakdownMiniWidget";
import { MacroRegimeBanner } from "./components/MacroRegimeBanner";

/**
 * widgetRegistry.tsx -- the single source of truth for "which real widget
 * component (and section heading) a given CustomViewWidgets key maps to",
 * shared by `screens/CreateDataApp.tsx`'s live preview and
 * `screens/CustomView.tsx`'s /app/:slug renderer.
 *
 * A PR #697 code-review finding: this mapping used to live independently in
 * THREE places -- `CreateDataApp.tsx`'s own `WIDGET_LABELS` map, its live-
 * preview `key === "..."` chain, and `CustomView.tsx`'s render `switch` --
 * with nothing tying them together. Adding a 10th widget meant remembering
 * to touch all three by hand; nothing would fail loudly if you missed one
 * (a widget checkbox that exists but never actually renders anywhere, or
 * renders in one screen and not the other).
 *
 * `aiChat` is deliberately NOT part of `WIDGET_COMPONENTS` -- the two
 * screens render genuinely different things for it, not two copies of one
 * component: `CreateDataApp.tsx`'s live preview shows a static, non-
 * interactive "[Chat Preview]" placeholder (there is no chat context to
 * open in a preview), while `CustomView.tsx` renders the real, interactive
 * "Ask AI" button wired to `useChat().openChat(...)`. Unifying those would
 * mean either faking a working chat button in the preview or wiring a real
 * chat context into a preview that was never meant to have one. Both
 * screens keep their own small, explicit `aiChat` branch; `WIDGET_LABELS`
 * still covers it (it needs a checkbox label either way).
 */
export const WIDGET_LABELS: Record<keyof CustomViewWidgets, string> = {
  edgeByStrategy: "Edge-by-strategy chart",
  symbolOverlay: "Symbol price + signal overlay chart",
  aiChat: "“Ask AI about this view” chat shortcut",
  pilotsTable: "Pilots holdings table",
  sentimentMini: "Sentiment history mini-chart",
  portfolioHeat: "Portfolio heat gauge",
  optionsDirective: "Options directive summary",
  signalBreakdown: "Signal breakdown mini-chart",
  macroRegime: "Macro regime banner",
};

/** "All 9 widget keys, in stable order" -- derived from `WIDGET_LABELS`
 * (kept exhaustive at compile time by the `Record<keyof CustomViewWidgets,
 * string>` annotation above), same convention `CreateDataApp.tsx` used
 * before this module existed. */
export const ALL_WIDGET_KEYS = Object.keys(WIDGET_LABELS) as (keyof CustomViewWidgets)[];

export type NonChatWidgetKey = Exclude<keyof CustomViewWidgets, "aiChat">;

/** Component + section heading for every widget except `aiChat` (see the
 * module doc above for why that one stays out of this map). Both
 * `CreateDataApp.tsx`'s live preview and `CustomView.tsx`'s renderer spread
 * the view's per-widget config (`widgetConfigs[key]`, e.g.
 * `{ defaultTicker: "TSLA" }` for `symbolOverlay`) onto `Component` as
 * props -- identical prop contract, same as before this refactor. */
export const WIDGET_COMPONENTS: Record<NonChatWidgetKey, { Component: ComponentType<any>; heading: string }> = {
  edgeByStrategy: { Component: EdgeByStrategyChart, heading: "Edge per strategy" },
  symbolOverlay: { Component: SymbolSignalOverlayChart, heading: "Price history & signal overlay" },
  pilotsTable: { Component: PilotsTableWidget, heading: "Pilots" },
  sentimentMini: { Component: SentimentMiniChart, heading: "Sentiment history" },
  portfolioHeat: { Component: PortfolioHeatWidget, heading: "Portfolio heat" },
  optionsDirective: { Component: OptionsDirectiveSummary, heading: "Options directives" },
  signalBreakdown: { Component: SignalBreakdownMiniWidget, heading: "Signal breakdown" },
  macroRegime: { Component: MacroRegimeBanner, heading: "Macro regime" },
};
