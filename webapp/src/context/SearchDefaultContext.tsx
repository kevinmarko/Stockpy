import React, { createContext, useContext, useMemo } from "react";
import { usePersistedState } from "../hooks/usePersistedState";

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
 * Persistence delegates to the existing `usePersistedState` hook
 * (`../hooks/usePersistedState.ts`) -- the same "non-sensitive UI
 * preference" contract it already implements for other per-browser
 * settings -- rather than hand-rolling a second try/catch-around-localStorage
 * implementation. Follows `ExplainTickerContext.tsx`'s
 * safe-fallback-outside-provider pattern (a component rendered without
 * `SearchDefaultProvider` -- e.g. an isolated component test -- gets the
 * new default rather than crashing).
 */

const STORAGE_KEY = "stockpy_search_universe_first";

export interface SearchDefaultContextValue {
  universeFirst: boolean;
  setUniverseFirst: (value: boolean) => void;
}

const SearchDefaultContext = createContext<SearchDefaultContextValue | undefined>(undefined);

export function SearchDefaultProvider({
  children,
  initialUniverseFirst,
}: {
  children: React.ReactNode;
  /** Test-only escape hatch: seeds the persisted value's default instead of
   * the real `true`. Only takes effect while nothing is already stored for
   * `STORAGE_KEY` in this browser (`usePersistedState`'s own default-value
   * semantics) and only on this provider's FIRST mount -- changing this prop
   * on an already-mounted `SearchDefaultProvider` has no effect, since it's
   * read only inside a lazy `useState` initializer. Production's one call
   * site (`App.tsx`) never passes this -- real usage always starts from
   * whatever's already persisted. */
  initialUniverseFirst?: boolean;
}) {
  const [universeFirst, setUniverseFirst] = usePersistedState<boolean>(
    STORAGE_KEY,
    initialUniverseFirst ?? true
  );

  // Memoized so a `SearchDefaultProvider` re-render triggered by something
  // ELSE (e.g. an ancestor's unrelated state change in `App.tsx`) doesn't
  // hand every `useSearchDefault()` consumer a new object reference --
  // `usePersistedState`'s own setter is already `useCallback`-stable, so
  // this only produces a new value when `universeFirst` itself changes.
  const value = useMemo(
    () => ({ universeFirst, setUniverseFirst }),
    [universeFirst, setUniverseFirst]
  );

  return (
    <SearchDefaultContext.Provider value={value}>
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
