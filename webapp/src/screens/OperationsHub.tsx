import { useNavigate } from "react-router-dom";
import { theme } from "../theme";

/**
 * OperationsHub — landing screen for the "Operations" nav section (see
 * App.tsx's NAV_ITEMS/SECTION_LABEL). A static overview of the section's 2
 * screens as clickable cards; someone else wires the section-header tap that
 * routes here. This screen owns only its own content and navigation.
 *
 * Neither Mission Control nor Pipeline has a TAB_HELP entry yet, so both
 * descriptions below are static prose (not sourced from help/helpContent.ts).
 */
interface HubCard {
  to: string;
  label: string;
  ico: string;
  description: string;
}

const CARDS: HubCard[] = [
  {
    to: "/observability",
    label: "Mission Control",
    ico: "🛰️",
    description:
      "Recession telemetry and risk-gate status — Sahm Rule, HY OAS, yield curve, and forecast horizons.",
  },
  {
    to: "/pipeline",
    label: "Pipeline",
    ico: "🚀",
    description:
      "The orchestrator daemon's live status and manual pipeline run triggers.",
  },
];

export function OperationsHub() {
  const nav = useNavigate();

  return (
    <div className="screen">
      <h1 className="screen-title">Operations</h1>
      <p className="screen-sub">
        The platform and pipeline itself, not a symbol or your money.
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
