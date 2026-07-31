import { useNavigate } from "react-router";
import { TAB_HELP } from "../help/helpContent";
import { theme } from "../theme";

/**
 * OperationsHub — landing screen for the "Operations" nav section (see
 * App.tsx's NAV_ITEMS/SECTION_LABEL). A static overview of the section's 5
 * screens as clickable cards; someone else wires the section-header tap that
 * routes here. This screen owns only its own content and navigation.
 *
 * Descriptions marked "TAB_HELP" read live off `help/helpContent.ts`'s
 * `TAB_HELP` map so this card's blurb can never drift from the real in-app
 * explainer text; Help & Glossary's is static prose specific to this hub
 * (the Help screen has no TabGuide of its own -- see Help.tsx's docstring).
 *
 * Console was previously routed at /console (App.tsx) with NO nav entry and
 * NO hub card -- reachable only by typing the URL, despite being a fully
 * built screen (six one-click job launchers + live SSE log streaming). This
 * card is the fix (parity gap G1) -- see `.claude/skills/new-pwa-screen/
 * SKILL.md`'s nav-reachability trap. Help & Glossary (parity gap G10) lives
 * here for the same reason -- neither is a symbol/portfolio research screen,
 * and Settings (a different agent's file) already has its own write-surface
 * convention this isn't part of.
 *
 * Report Library (parity gap G5) is the same unreachable-route shape as
 * Console was -- built read-only against `output/`/`reports/` artifacts, not
 * a `.env`-write surface, so it belongs here alongside Console/Pipeline
 * rather than under Settings.
 */
interface HubCard {
  to: string;
  label: string;
  ico: string;
  description: string;
}

const CARDS: HubCard[] = [
  { to: "/observability", label: "Mission Control", ico: "🛰️", description: TAB_HELP.observability.description },
  { to: "/pipeline", label: "Pipeline", ico: "🚀", description: TAB_HELP.pipeline.description },
  { to: "/console", label: "Console", ico: "🖥️", description: TAB_HELP.console.description },
  { to: "/operations/reports", label: "Report Library", ico: "📚", description: TAB_HELP.reports.description },
  {
    to: "/help",
    label: "Help & Glossary",
    ico: "❓",
    description: "Search the platform's full glossary of terms, metrics, and gates -- every definition each screen's own \"How this works\" panel draws from, in one searchable place.",
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

      <div style={{ marginTop: "var(--s-3)" }}>
        {CARDS.map((c) => (
          <button
            key={c.to}
            type="button"
            onClick={() => nav(c.to)}
            className="card card-pad"
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: "var(--s-3)",
              width: "100%",
              textAlign: "left",
              marginBottom: "var(--s-3)",
              background: "none",
              cursor: "pointer",
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
        ))}
      </div>
    </div>
  );
}
