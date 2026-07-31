import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { GLOSSARY, glossaryDef } from "../help/helpContent";
import { loadThresholds } from "../help/thresholds";
import type { Thresholds } from "../api/types";
import { theme } from "../theme";

/**
 * Help — a searchable index over the FULL `GLOSSARY` (help/helpContent.ts),
 * the webapp's counterpart to Streamlit's Help tab search box (parity gap
 * G10). Every other screen's `<TabGuide>` only ever surfaces a small,
 * curated `keyConcepts` subset for that one screen; this is the one place
 * every term is reachable regardless of which screen defined it.
 *
 * Deliberately no per-screen `<TabGuide tabKey="help">` on THIS screen: the
 * whole page already IS the "how this works" content, so nesting another
 * collapsible explainer for itself would be circular. `helpContent.ts`
 * therefore carries no "help" TAB_HELP entry.
 *
 * No docs/HOW_TO_GUIDE.md deep link, unlike Streamlit's `GlossaryEntry`
 * (which carries a `guide_anchor`): the webapp's `GLOSSARY` has no anchor
 * field (see helpContent.ts's own module docstring) and is not checked
 * against that doc, so fabricating a link here would be an unverifiable
 * claim -- CONSTRAINT #4. Live-threshold entries (PBO/DSR/Sharpe/Kelly/etc.)
 * still resolve to their real current value via the same `loadThresholds()`
 * + `glossaryDef()` machinery `TabGuide` uses, degrading every number to
 * "—" (never a guess) while thresholds haven't loaded or the fetch failed.
 */
export function Help() {
  const nav = useNavigate();
  const [query, setQuery] = useState("");
  const [openTerm, setOpenTerm] = useState<string | null>(null);
  const [thresholds, setThresholds] = useState<Thresholds | null>(null);
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  useEffect(() => {
    let alive = true;
    void loadThresholds().then((t) => {
      if (alive) setThresholds(t);
    });
    return () => {
      alive = false;
    };
  }, []);

  const terms = useMemo(
    () => Object.keys(GLOSSARY).sort((a, b) => a.localeCompare(b)),
    []
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return terms;
    return terms.filter((term) => {
      if (term.toLowerCase().includes(q)) return true;
      const def = glossaryDef(term, thresholds);
      return def != null && def.toLowerCase().includes(q);
    });
  }, [terms, query, thresholds]);

  return (
    <div className="screen">
      <button
        onClick={back}
        style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: theme.textSecondary, fontSize: "var(--t-callout)", marginBottom: "var(--s-2)" }}
      >
        ← Back
      </button>
      <h1 className="screen-title">Help &amp; Glossary</h1>
      <p className="screen-sub">
        Search the platform's full glossary of {terms.length} terms — every metric, gate,
        and concept used across the app, in one place.
      </p>

      <input
        type="search"
        className="input"
        placeholder='Search terms (e.g. "kelly", "deployable", "z-score")'
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        aria-label="Search glossary"
        data-testid="help-search"
      />

      <div style={{ marginTop: "var(--s-3)", color: theme.textMuted, fontSize: "var(--t-caption)" }} data-testid="help-result-count">
        {filtered.length} of {terms.length} terms
      </div>

      {filtered.length === 0 ? (
        <div className="empty" style={{ marginTop: "var(--s-3)" }} data-testid="help-empty">
          <div style={{ fontSize: "var(--t-subhead)", fontWeight: 600, color: theme.textSecondary }}>
            No matching terms
          </div>
          <div style={{ marginTop: "var(--s-1-5)" }}>Try a different search.</div>
        </div>
      ) : (
        <div
          style={{ marginTop: "var(--s-3)", display: "flex", flexDirection: "column", gap: "var(--s-2)" }}
          data-testid="help-glossary-list"
        >
          {filtered.map((term) => {
            const open = openTerm === term;
            return (
              <div key={term} className="card" style={{ overflow: "hidden" }}>
                <button
                  type="button"
                  onClick={() => setOpenTerm(open ? null : term)}
                  aria-expanded={open}
                  data-testid={`help-term-${term}`}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    width: "100%",
                    textAlign: "left",
                    padding: "var(--s-2-5) var(--s-3)",
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: theme.textPrimary,
                    fontWeight: 700,
                    fontSize: "var(--t-callout)",
                    textTransform: "capitalize",
                  }}
                >
                  <span>{term}</span>
                  <span aria-hidden style={{ color: theme.textMuted }}>
                    {open ? "▾" : "▸"}
                  </span>
                </button>
                {open && (
                  <div
                    style={{
                      padding: "0 var(--s-3) var(--s-3)",
                      color: theme.textSecondary,
                      fontSize: "var(--t-body)",
                      lineHeight: 1.5,
                    }}
                    data-testid={`help-def-${term}`}
                  >
                    {glossaryDef(term, thresholds) ?? "—"}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
