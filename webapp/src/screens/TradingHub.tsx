import { useNavigate } from "react-router-dom";
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
      style={{ marginBottom: 12, cursor: "pointer" }}
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
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <span style={{ fontSize: 22, lineHeight: 1 }}>{card.icon}</span>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 15 }}>{card.label}</div>
          <p
            style={{
              color: theme.textSecondary,
              fontSize: 12.5,
              lineHeight: 1.5,
              marginTop: 4,
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
          fontSize: 14,
          marginBottom: 8,
        }}
      >
        ← Pilots
      </button>
      <h1 className="screen-title">Trading Tools</h1>
      <p className="screen-sub">Grading and acting on your own portfolio.</p>

      <div style={{ marginTop: 12 }}>
        {CARDS.map((card) => (
          <HubCardRow key={card.to} card={card} onOpen={() => nav(card.to)} />
        ))}
      </div>
    </div>
  );
}
