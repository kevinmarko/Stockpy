import { useEffect, useId, useState } from "react";
import { TAB_HELP, glossaryDef } from "../help/helpContent";
import { helpSeen, markHelpSeen } from "../help/helpState";

/**
 * "How this works" education panel for a screen. Dismissible and self-teaching:
 * expanded on the operator's first visit to the tab (then marked seen), collapsed
 * on every later visit, and re-openable via the header toggle. Key concepts are
 * glossary terms that expand their plain-English definition inline.
 *
 * Renders nothing when `tabKey` has no entry in `TAB_HELP` (so it's safe to drop
 * into any screen). Content lives entirely in `help/helpContent.ts`.
 */
export function TabGuide({ tabKey }: { tabKey: string }) {
  const help = TAB_HELP[tabKey];
  // Start expanded only if this tab's guide hasn't been seen before. Lazy init so
  // the localStorage read happens once, not every render.
  const [expanded, setExpanded] = useState(() => (help ? !helpSeen(tabKey) : false));
  const [activeTerm, setActiveTerm] = useState<string | null>(null);
  const bodyId = useId();

  // Showing the guide counts as "seen" → it collapses on the next visit.
  useEffect(() => {
    if (help) markHelpSeen(tabKey);
  }, [help, tabKey]);

  if (!help) return null;

  return (
    <section className="tab-guide" data-testid={`tab-guide-${tabKey}`}>
      <button
        type="button"
        className="tab-guide-toggle"
        aria-expanded={expanded}
        aria-controls={bodyId}
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="tab-guide-q" aria-hidden>
          ?
        </span>
        <span className="tab-guide-title">How this works — {help.title}</span>
        <span className="tab-guide-chevron" aria-hidden>
          {expanded ? "▾" : "▸"}
        </span>
      </button>

      {expanded && (
        <div id={bodyId} className="tab-guide-body">
          <p className="tab-guide-desc">{help.description}</p>

          {help.keyConcepts.length > 0 && (
            <>
              <div className="tab-guide-terms">
                {help.keyConcepts.map((key) => {
                  const active = activeTerm === key;
                  return (
                    <button
                      key={key}
                      type="button"
                      className={`tab-guide-term${active ? " is-active" : ""}`}
                      aria-expanded={active}
                      onClick={() => setActiveTerm(active ? null : key)}
                    >
                      {key}
                    </button>
                  );
                })}
              </div>
              {activeTerm && (
                <p className="tab-guide-def" data-testid="tab-guide-def">
                  <strong>{activeTerm}</strong> — {glossaryDef(activeTerm) ?? "—"}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
