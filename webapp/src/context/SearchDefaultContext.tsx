import React, { createContext, useContext, useState } from "react";

/**
 * Global toggle for `SymbolInput`'s default suggestion ORDER (not which
 * results it shows -- both modes still show both sections wherever
 * `enableFmpSuggestions` is on):
 *
 * - `universeFirst = true` (new default, 2026-09): FMP-wide results lead
 *   unlabeled, with currently-tracked matches shown second under a "Saved"
 *   header -- "search leads with the whole market."
 * - `universeFirst = false` (rollback / legacy default): tracked matches
 *   lead unlabeled (today's pre-change behavior, byte-identical), with
 *   untracked FMP results shown second under "Not yet tracked".
 *
 * This is a pure per-browser UI preference -- no backend involvement, no
 * `settings.py` field, nothing gated, no change to what data is fetched or
 * what the pipeline evaluates. A plain localStorage-backed React context is
 * therefore the right mechanism, NOT `pilots/feature_flags.py`'s Feature
 * Flags registry, which is scoped to backend write/execution-gate
 * capabilities (see that registry's own module docstring) -- an ordering
 * preference for an autocomplete dropdown carries none of that risk class.
 *
 * Sector Selection is untouched by this toggle either way: it always passes
 * `enableFmpSuggestions={false}` (its `GET /sector/selection` only reads
 * persisted DB state, so an untracked symbol there is a guaranteed
 * honest-empty dead end -- see `SymbolInput.tsx`'s own doc comment), which
 * suppresses the FMP section entirely regardless of ordering.
 *
 * Follows `ThemeContext.tsx`'s exact try/catch-around-localStorage pattern
 * and `ExplainTickerContext.tsx`'s safe-fallback-outside-provider pattern
 * (a component rendered without `SearchDefaultProvider` -- e.g. an isolated
 * component test -- gets the new default rather than crashing).
 */

const STORAGE_KEY = "stockpy_search_universe_first";

export interface SearchDefaultContextValue {
  universeFirst: boolean;
  setUniverseFirst: (value: boolean) => void;
}

const SearchDefaultContext = createContext<SearchDefaultContextValue | undefined>(undefined);

function readStored(): boolean {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "true") return true;
    if (stored === "false") return false;
  } catch {
    // Private browsing / storage blocked -- fall through to the default.
  }
  return true; // new default: universe-first
}

export function SearchDefaultProvider({
  children,
  initialUniverseFirst,
}: {
  children: React.ReactNode;
  /** Test-only escape hatch: skip the localStorage read and start from this
   * value instead. Production's one call site (`App.tsx`) never passes
   * this -- real usage always reads/persists via localStorage. */
  initialUniverseFirst?: boolean;
}) {
  const [universeFirst, setUniverseFirstState] = useState<boolean>(
    () => initialUniverseFirst ?? readStored()
  );

  const setUniverseFirst = (value: boolean) => {
    setUniverseFirstState(value);
    try {
      localStorage.setItem(STORAGE_KEY, String(value));
    } catch {
      // Preference just won't survive a reload in this browser -- non-fatal.
    }
  };

  return (
    <SearchDefaultContext.Provider value={{ universeFirst, setUniverseFirst }}>
      {children}
    </SearchDefaultContext.Provider>
  );
}

const dummySearchDefaultContext: SearchDefaultContextValue = {
  universeFirst: true,
  setUniverseFirst: () => {},
};

export function useSearchDefault(): SearchDefaultContextValue {
  const ctx = useContext(SearchDefaultContext);
  return ctx ?? dummySearchDefaultContext;
}
