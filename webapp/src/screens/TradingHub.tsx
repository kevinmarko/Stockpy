import { useNavigate } from "react-router";
import { TAB_HELP } from "../help/helpContent";
import { theme } from "../theme";
import { DynamicGrid, resetGridLayout } from "../components/DynamicGrid";

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
 *
 * Each card splits a `.drag-handle` header (icon + label, grabbed to
 * reorder) from a separately-clickable `role="button"` body (the
 * description, tapped to navigate) -- matching OperationsHub.tsx's and
 * ResearchHub.tsx's pattern, so all three hub screens resolve the
 * click-vs-drag conflict the same way. This replaces an earlier
 * onDoubleClick-to-navigate workaround (single click was reserved for
 * dragging the whole card) that made this screen's tap gesture inconsistent
 * with its two sibling hubs.
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
    <div className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
      <div
        className="drag-handle"
        style={{
          padding: "var(--s-3)",
          borderBottom: `1px solid rgba(255, 255, 255, 0.08)`,
          cursor: "grab",
          display: "flex",
          alignItems: "center",
          gap: "var(--s-2)"
        }}
      >
        <span aria-hidden style={{ fontSize: "var(--t-display)", lineHeight: 1 }}>
          {card.icon}
        </span>
        <div style={{ fontWeight: 700, fontSize: "var(--t-subhead)" }}>{card.label}</div>
      </div>
      <div
        role="button"
        aria-label={card.label}
        tabIndex={0}
        onClick={onOpen}
        onKeyDown={(e) => e.key === "Enter" && onOpen()}
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
        {card.description}
      </div>
    </div>
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
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="screen-title" style={{ marginTop: "var(--s-2)" }}>Trading Tools</h1>
          <p className="screen-sub">Grading and acting on your own portfolio.</p>
        </div>
        <button className="btn btn-neutral" onClick={() => resetGridLayout("tradingHub")}>Reset Layout</button>
      </div>

      <div style={{ marginTop: "var(--s-3)" }}>
        <DynamicGrid
          layoutKey="tradingHub"
          defaultLayouts={{
            lg: CARDS.map((card, i) => ({ i: card.to, x: (i % 3) * 4, y: Math.floor(i / 3) * 2, w: 4, h: 2, minW: 3, minH: 2 })),
          }}
        >
          {CARDS.map((card) => (
            <div key={card.to}>
              <HubCardRow card={card} onOpen={() => nav(card.to)} />
            </div>
          ))}
        </DynamicGrid>
      </div>
    </div>
  );
}
