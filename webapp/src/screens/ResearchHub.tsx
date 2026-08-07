import { useNavigate } from "react-router";
import { TAB_HELP } from "../help/helpContent";
import { theme } from "../theme";
import { DynamicGrid, resetGridLayout } from "../components/DynamicGrid";
import { Button } from "../components/ui";

/**
 * ResearchHub — landing screen for the "Research" nav section (see
 * App.tsx's NAV_ITEMS/SECTION_LABEL). A static overview of the section's 11
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
        <div style={{ display: "flex", gap: "var(--s-2)" }}>
          <Button variant="neutral" onClick={() => resetGridLayout("research-hub")}>Reset Layout</Button>
        </div>
      </div>

      <div style={{ flex: 1, minHeight: 0, marginTop: "var(--s-4)" }}>
        <DynamicGrid
          layoutKey="research-hub"
          defaultLayouts={{
            lg: CARDS.map((c, i) => ({
              i: c.to,
              x: (i % 3) * 4,
              y: Math.floor(i / 3) * 3,
              w: 4,
              h: 3,
              minW: 3,
              minH: 2,
            })),
          }}
        >
          {CARDS.map((c) => (
            <div key={c.to}>
              <button
                type="button"
                onClick={() => nav(c.to)}
                className="card card-pad drag-handle"
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "var(--s-3)",
                  width: "100%",
                  height: "100%",
                  textAlign: "left",
                  background: "none",
                  cursor: "grab",
                }}
              >
                <span aria-hidden style={{ fontSize: "var(--t-display)", lineHeight: 1 }}>
                  {c.ico}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 700, fontSize: "var(--t-subhead)" }}>{c.label}</div>
                  <div style={{ color: theme.textMuted, fontSize: "var(--t-label)", marginTop: "var(--s-1)", lineHeight: 1.5 }}>
                    {c.description}
                  </div>
                </div>
              </button>
            </div>
          ))}
        </DynamicGrid>
      </div>
    </div>
  );
}