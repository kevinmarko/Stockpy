import { useNavigate } from "react-router";
import { TAB_HELP } from "../help/helpContent";
import { theme } from "../theme";

/**
 * TradingHub.tsx — landing screen for the "Trading Tools" nav section
 * (Attribution / Calibration / Commands — Agent moved to the primary mobile
 * tab bar per a `/user-research` pass; kept off this list so it stays in
 * sync with App.tsx's NAV_ITEMS `section: "trading"` membership rather than
 * duplicating a screen that's now one tap away already). Purely static
 * content: a card per screen with an icon, label, and one-line description,
 * tapped to navigate. Every description is sourced live from `TAB_HELP`
 * (`help/helpContent.ts`) rather than hand-copied, so it can never drift
 * from the real in-app help content.
 */
interface HubCard {
  to: string;
  label: string;
  icon: string;
  description: string;
}

const CARDS: HubCard[] = [
  { to: "/attribution", label: "Attribution", icon: "🧮", description: TAB_HELP.attribution.description },
  { to: "/calibration", label: "Calibration", icon: "🎚️", description: TAB_HELP.calibration.description },
  { to: "/commands", label: "Commands", icon: "⌨️", description: TAB_HELP.commands.description },
];

function HubCardRow({ card, onOpen }: { card: HubCard; onOpen: () => void }) {
  return (
    <section
      className="card card-pad"
      style={{ marginBottom: "var(--s-3)", cursor: "pointer" }}
      onClick={onOpen}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: "var(--s-3)" }}>
        <span style={{ fontSize: "var(--t-display)", lineHeight: 1 }}>{card.icon}</span>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: "var(--t-subhead)" }}>{card.label}</div>
          <p
            style={{
              color: theme.textSecondary,
              fontSize: "var(--t-label)",
              lineHeight: 1.5,
              marginTop: "var(--s-1)",
            }}
          >
            {card.description}
          </p>
        </div>
      </div>
    </section>
  );
}

export function TradingHub() {
  const nav = useNavigate();
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  return (
    <div className="screen">
      <button
        onClick={back}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          color: theme.textSecondary,
          fontSize: "var(--t-callout)",
          marginBottom: "var(--s-2)",
        }}
      >
        ← Pilots
      </button>
      <h1 className="screen-title">Trading Tools</h1>
      <p className="screen-sub">Grading and acting on your own portfolio.</p>

      <div style={{ marginTop: "var(--s-3)" }}>
        {CARDS.map((card) => (
          <HubCardRow key={card.to} card={card} onOpen={() => nav(card.to)} />
        ))}
      </div>
    </div>
  );
}
