import { useNavigate } from "react-router";
import { TAB_HELP } from "../help/helpContent";
import { theme } from "../theme";

/**
 * ResearchHub — landing screen for the "Research" nav section (see
 * App.tsx's NAV_ITEMS/SECTION_LABEL). A static overview of the section's 12
 * screens as clickable cards; someone else wires the section-header tap that
 * routes here. This screen owns only its own content and navigation.
 *
 * Card order mirrors NAV_ITEMS' research section exactly, so this list can't
 * silently drift out of sync with the nav again the way it previously did
 * (parity gap G2 — /sentiment and /sector-selection were missing from this
 * hub despite both having real NAV_ITEMS + route entries).
 *
 * Descriptions marked "TAB_HELP" in the spec read live off
 * `help/helpContent.ts`'s `TAB_HELP` map so this card's blurb can never drift
 * from the real in-app explainer text; the rest are static prose specific to
 * this hub (not duplicated anywhere else).
 *
 * Each card's drag-affordance (`.drag-handle`, icon + label header) is a
 * separate element from its click-to-navigate body (the description area,
 * `role="button"`) -- matching OperationsHub.tsx's pattern -- so grabbing the
 * card to reorder it and tapping it to navigate are never the same gesture.
 * Card CONTENT order is always driven by this static `CARDS` array (itself
 * mirroring NAV_ITEMS, per the G2 note above); react-grid-layout's persisted
 * drag positions in localStorage only ever change each card's visual x/y
 * placement, never which card that content is or the order it's iterated in
 * here -- see ResearchHub.test.tsx's G2 invariant test.
 */
interface HubCard {
  to: string;
  label: string;
  ico: string;
  description: string;
}

const CARDS: HubCard[] = [
  { to: "/marketplace", label: "Pilots", ico: "🧭", description: TAB_HELP.pilots.description },
  { to: "/compare", label: "Compare", ico: "⚖️", description: TAB_HELP.compare.description },
  { to: "/models", label: "Models", ico: "🧠", description: TAB_HELP.models.description },
  { to: "/strategy-health", label: "Strategy Health", ico: "🛡️", description: TAB_HELP["strategy-health"].description },
  { to: "/pairs", label: "Pairs radar", ico: "🔗", description: TAB_HELP.pairs.description },
  { to: "/options", label: "Options", ico: "🎯", description: TAB_HELP.options.description },
  { to: "/signals", label: "Signal Breakdown", ico: "🧬", description: TAB_HELP.signals.description },
  { to: "/sentiment", label: "Sentiment Dynamics", ico: "🎭", description: TAB_HELP.sentiment.description },
  { to: "/sector-selection", label: "Sector Selection", ico: "🧩", description: TAB_HELP["sector-selection"].description },
  { to: "/forecast", label: "Forecast Viewer", ico: "📈", description: TAB_HELP.forecast.description },
  { to: "/data-explorer", label: "Data Explorer", ico: "🗂️", description: TAB_HELP["data-explorer"].description },
  {
    to: "/research/trends-stitcher",
    label: "SVI Stitching Algorithm Demo",
    ico: "📊",
    description:
      "Demonstrates the overlapping-window stitching algorithm used to reconstruct a continuous Google Trends SVI series from adjacent 90-day intervals (live Google Trends data isn't wired up in this platform, so the demo runs on labeled proxy data).",
  },
];

export function ResearchHub() {
  const nav = useNavigate();

  return (
    <div className="screen" style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--s-3)" }}>
        <div>
          <h1 className="screen-title">Research</h1>
          <p className="screen-sub">
            Strategies and symbols worth a closer look before you act.
          </p>
        </div>
      </div>

      <div style={{ flex: 1, minHeight: 0, marginTop: "var(--s-4)", display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: "var(--s-4)", alignContent: "start" }}>
        {CARDS.map((c) => (
          <div key={c.to} style={{ minHeight: "200px" }}>
            <div className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
              <div
                style={{
                  padding: "var(--s-3)",
                  borderBottom: `1px solid rgba(255, 255, 255, 0.08)`,
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--s-2)"
                }}
              >
                <span aria-hidden style={{ fontSize: "var(--t-display)", lineHeight: 1 }}>
                  {c.ico}
                </span>
                <div style={{ fontWeight: 700, fontSize: "var(--t-subhead)" }}>{c.label}</div>
              </div>
              <div
                role="button"
                aria-label={c.label}
                tabIndex={0}
                onClick={() => nav(c.to)}
                onKeyDown={(e) => e.key === "Enter" && nav(c.to)}
                style={{
                  padding: "var(--s-3)",
                  flex: 1,
                  overflow: "auto",
                  color: theme.textMuted,
                  fontSize: "var(--t-label)",
                  lineHeight: 1.5,
                  cursor: "pointer"
                }}
              >
                {c.description}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}