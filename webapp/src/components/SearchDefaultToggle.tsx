import { useSearchDefault } from "../context/SearchDefaultContext";
import { Toggle } from "./Toggle";
import { theme } from "../theme";

/**
 * SearchDefaultToggle — the operator-facing control for
 * `SearchDefaultContext`'s `universeFirst` preference (see that file's own
 * doc comment for the full mechanism). Before this component the toggle
 * MECHANISM existed (context + localStorage persistence, wired into
 * `App.tsx`) but nothing ever called `setUniverseFirst()` — this closes
 * that gap.
 *
 * This is a pure per-browser UI preference, not a mutating backend action
 * (contrast `KillSwitchToggle`/`Toggle`'s `pending`/error-toast machinery,
 * built for a real network round-trip) — `setUniverseFirst` is synchronous
 * and cannot fail, so there's nothing to await or roll back. The `Toggle`
 * component is still the right building block: it's this app's one
 * `role="switch"` a11y pattern (Space/Enter activation, `aria-checked`,
 * visible label carrying the state rather than color alone), and reusing
 * it keeps this control visually and behaviorally consistent with every
 * other on/off control in Settings.
 *
 * Labeling is deliberately explicit about which state does what (not just
 * "Universe-first search: on/off") — a toggle titled only with its own
 * mechanism name tells the operator nothing about the actual effect on the
 * search box they'll see next time they type into it.
 */
export function SearchDefaultToggle() {
  const { universeFirst, setUniverseFirst } = useSearchDefault();

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
      <Toggle
        checked={universeFirst}
        onChange={(next) => setUniverseFirst(next)}
        label={
          universeFirst
            ? "Search any stock first (default)"
            : "Search your saved list first (legacy)"
        }
        dataTestId="search-default-toggle"
      />
      <p
        style={{
          color: theme.textMuted,
          fontSize: "var(--t-caption)",
          marginTop: 0,
          lineHeight: 1.45,
        }}
      >
        {universeFirst
          ? "The search box across the app (Data Explorer, Signal Breakdown, Paper Broker, and more) leads with results from the whole market. Symbols on your tracked/saved list still appear, labeled \"Saved\", further down."
          : "The search box across the app leads with your tracked/saved symbols first, exactly like it did before September 2026. Wider market results still appear, labeled \"Not yet tracked\", further down."}
      </p>
    </div>
  );
}
