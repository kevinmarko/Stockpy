import { useNavigate } from "react-router-dom";
import { TAB_HELP } from "../help/helpContent";
import { theme } from "../theme";

/**
 * ResearchHub — landing screen for the "Research" nav section (see
 * App.tsx's NAV_ITEMS/SECTION_LABEL). A static overview of the section's 9
 * screens as clickable cards; someone else wires the section-header tap that
 * routes here. This screen owns only its own content and navigation.
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
  {
    to: "/compare",
    label: "Compare",
    ico: "⚖️",
    description:
      "Side-by-side performance charts and stats for choosing between Pilots you're considering following.",
  },
  {
    to: "/models",
    label: "Models",
    ico: "🧠",
    description:
      "The ML model registry — every model's deployability gates, DSR/PBO, and training lineage.",
  },
  { to: "/strategy-health", label: "Strategy Health", ico: "🛡️", description: TAB_HELP["strategy-health"].description },
  {
    to: "/pairs",
    label: "Pairs radar",
    ico: "🔗",
    description:
      "Cointegration-based pairs trading signals — entry/exit z-scores and half-life per pair.",
  },
  { to: "/options", label: "Options", ico: "🎯", description: TAB_HELP.options.description },
  { to: "/signals", label: "Signal Breakdown", ico: "🧬", description: TAB_HELP.signals.description },
  { to: "/forecast", label: "Forecast Viewer", ico: "📈", description: TAB_HELP.forecast.description },
  {
    to: "/data-explorer",
    label: "Data Explorer",
    ico: "🗂️",
    description:
      "Raw price bars, fundamentals, and macro series for any symbol, straight from the pipeline.",
  },
];

export function ResearchHub() {
  const nav = useNavigate();

  return (
    <div className="screen">
      <h1 className="screen-title">Research</h1>
      <p className="screen-sub">
        Strategies and symbols worth a closer look before you act.
      </p>

      <div style={{ marginTop: 12 }}>
        {CARDS.map((c) => (
          <button
            key={c.to}
            type="button"
            onClick={() => nav(c.to)}
            className="card card-pad"
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 12,
              width: "100%",
              textAlign: "left",
              marginBottom: 12,
              background: "none",
              cursor: "pointer",
            }}
          >
            <span aria-hidden style={{ fontSize: 22, lineHeight: 1 }}>
              {c.ico}
            </span>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 700, fontSize: 15 }}>{c.label}</div>
              <div style={{ color: theme.textMuted, fontSize: 12.5, marginTop: 4, lineHeight: 1.5 }}>
                {c.description}
              </div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
