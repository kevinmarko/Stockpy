import {
  Fragment,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { Button } from "./ui";
import { useDebounce } from "../hooks/useDebounce";
import { api } from "../api/client";
import type { UniverseSymbol, SymbolSearchResult } from "../api/types";
import { loadUniverse, getCachedUniverse } from "./universeCache";
import { useSearchDefault } from "../context/SearchDefaultContext";

/**
 * Shared symbol entry bar for the per-symbol research screens (Data Explorer,
 * Signal Breakdown, Forecast Viewer, Sentiment Dynamics, Sector Selection),
 * Pairs Radar, Cache Long/Short, Paper Broker's Quick Trade, and Universe
 * Manager. An accessible combobox: as the operator types, it suggests
 * tickers from the tracked universe (`GET /universe`, or `trackedSymbols` --
 * see below) so they don't have to know a symbol by heart — every tracked
 * suggestion resolves to a real detail page. It ALSO suggests any FMP-known
 * symbol not yet tracked (`GET /data/symbol-search`, debounced) unless
 * `enableFmpSuggestions={false}`. Selecting a suggestion (Enter on a
 * highlighted row, Tab, or click) loads it immediately, tracked or not.
 *
 * Default ORDER of the two sections is universe-first (2026-09, "Invert The
 * Default" -- see `../context/SearchDefaultContext.tsx`): FMP-wide results
 * lead unlabeled, tracked matches follow under a "Saved" header. Flipping the
 * operator's `useSearchDefault()` toggle off reproduces the pre-change
 * tracked-first default exactly (tracked leads unlabeled, untracked follows
 * under "Not yet tracked") -- a per-browser preference, not a per-call-site
 * one; every call site gets the same order except Sector Selection, which is
 * unaffected either way since it always sets `enableFmpSuggestions={false}`.
 *
 * Free-text is preserved: pressing Load — or Enter with nothing highlighted —
 * submits whatever is typed, uppercased/trimmed, even if it isn't in the
 * universe or FMP's results (so arbitrary tickers still work). The tracked
 * universe fetch is lazy, shared across all instances (module cache) UNLESS
 * `trackedSymbols` is supplied, and non-fatal: if either fetch fails the
 * field silently degrades (tracked → plain text input; FMP → tracked-only
 * suggestions).
 *
 * Only commits to `onSubmit` on a deliberate action (submit / accept), never per
 * keystroke, so the owning screen's `useApi` refetches once per lookup.
 */

// The tracked-symbol universe cache/fetcher lives in ./universeCache.ts (a
// pure module, no React) so this file only exports the `SymbolInput`
// component -- see that file for the module-level cache and fetch contract.

const MAX_TRACKED_SUGGESTIONS = 8;
const MAX_FMP_SUGGESTIONS = 5;

type SuggestionRow = { symbol: string; action: string | null; tracked: boolean };

export function SymbolInput({
  initial = "",
  onSubmit,
  label = "Symbol",
  hint,
  pending,
  hideButton,
  buttonText,
  onChange,
  testId = "symbol-input",
  trackedSymbols,
  enableFmpSuggestions = true,
  requireExactMatch = false,
  autoSubmitOnExactMatch = false,
}: {
  initial?: string;
  onSubmit: (symbol: string) => void;
  label?: string;
  hint?: React.ReactNode;
  pending?: boolean;
  hideButton?: boolean;
  buttonText?: string;
  onChange?: (symbol: string) => void;
  /** Override the default `data-testid` -- needed when more than one
   * SymbolInput renders on the same screen (e.g. PairsRadar's Symbol Y/X),
   * since `screen.getByTestId` requires a unique match. Defaults to
   * "symbol-input" to stay compatible with every existing single-instance
   * caller/test. */
  testId?: string;
  /** Supply the caller's OWN tracked-symbol list instead of the shared
   * `GET /universe` module cache. Universe Manager needs this: its own
   * tracked set is `DEFAULT_TICKERS` (`GET/PUT /data/universe`), a
   * different list from the pipeline-snapshot universe every other
   * SymbolInput instance suggests from -- passing this avoids suggesting
   * from the wrong universe entirely, and skips the shared cache fetch. */
  trackedSymbols?: string[];
  /** Set `false` to suppress the FMP "not yet tracked" section entirely --
   * used by Sector Selection, where `GET /sector/selection` only ever reads
   * persisted DB state, so an untracked symbol is a guaranteed honest-empty
   * dead end and surfacing one here would just be misleading. */
  enableFmpSuggestions?: boolean;
  /** Opt-in, stricter mode: reject free-text submission (Load button and
   * bare Enter) unless the typed value exactly matches a symbol already
   * known to be real -- either the tracked universe or a live
   * `GET /data/symbol-search` result. Selecting an actual suggestion row
   * (click, or Enter/Tab while one is highlighted) always works, since a
   * suggestion is real by construction. Defaults to `false`, preserving
   * every existing caller's free-text-always-works behavior exactly --
   * only Paper Broker's Quick Trade panel opts in, since that's the one
   * screen where a bad typo silently produces a plausible-looking "no
   * quote available" error instead of an obviously-invalid disabled
   * button. */
  requireExactMatch?: boolean;
  /** Opt-in: once the typed value resolves to a known real symbol (same
   * `isKnownSymbol` check `requireExactMatch` already gates the button on),
   * fire `onSubmit` automatically instead of waiting for a manual button
   * click -- so finishing a valid ticker behaves like picking a suggestion
   * already does (a suggestion click always auto-submits; only the
   * exact-typed-match path previously required an extra click). Fires once
   * per newly-resolved symbol (guarded by a ref, not on every keystroke --
   * `q` is already 200ms-debounced) and re-arms if the value changes to a
   * different symbol. Meaningless without `requireExactMatch` (there is no
   * "known" gate to react to) and defaults to `false`, preserving every
   * other caller's manual-submit-only behavior exactly -- only Paper
   * Broker's Quick Trade panel opts in. */
  autoSubmitOnExactMatch?: boolean;
}) {
  const { universeFirst } = useSearchDefault();
  const [value, setValue] = useState(initial);
  const [universe, setUniverse] = useState<UniverseSymbol[]>(
    trackedSymbols ? [] : getCachedUniverse() ?? []
  );
  const [fmpResults, setFmpResults] = useState<SymbolSearchResult[]>([]);
  const [open, setOpen] = useState(false);
  // -1 = nothing highlighted → Enter submits the typed text (free-text default);
  // 0..n-1 = a suggestion is highlighted → Enter/Tab accept it.
  const [activeIndex, setActiveIndex] = useState(-1);
  const autoId = useId();
  const listId = `${autoId}-symbols`;
  const hintId = `${autoId}-hint`;

  useEffect(() => {
    if (trackedSymbols) return; // caller supplies its own tracked list -- no shared fetch needed
    let alive = true;
    void loadUniverse().then((u) => {
      if (alive) setUniverse(u);
    });
    return () => {
      alive = false;
    };
  }, [trackedSymbols]);

  const debouncedValue = useDebounce(value, 200);
  const q = debouncedValue.trim().toUpperCase();

  // trackedSymbols (when supplied) wins over the shared universe cache --
  // reshaped to the same {symbol, action} shape so the rest of this
  // component doesn't need to know which source it came from.
  const trackedList = useMemo<UniverseSymbol[]>(
    () =>
      trackedSymbols
        ? trackedSymbols.map((s) => ({ symbol: s.toUpperCase(), action: null }))
        : universe,
    [trackedSymbols, universe]
  );

  const trackedSuggestions = useMemo(() => {
    if (!q) return [];
    const starts: UniverseSymbol[] = [];
    const contains: UniverseSymbol[] = [];
    for (const u of trackedList) {
      const s = u.symbol;
      if (s === q) continue; // exact match needs no suggestion — Enter submits it
      if (s.startsWith(q)) starts.push(u);
      else if (s.includes(q)) contains.push(u);
    }
    return [...starts, ...contains].slice(0, MAX_TRACKED_SUGGESTIONS);
  }, [q, trackedList]);

  // Debounced live FMP symbol search for the "not yet tracked" section.
  // Non-fatal: a failed/disabled fetch just leaves this section empty,
  // matching the tracked-universe fetch's own degrade-silently contract.
  useEffect(() => {
    if (!enableFmpSuggestions || !q) {
      setFmpResults([]);
      return;
    }
    let alive = true;
    api
      .getSymbolSearch(q, MAX_FMP_SUGGESTIONS)
      .then((res) => {
        if (alive) setFmpResults(res.results ?? []);
      })
      .catch(() => {
        if (alive) setFmpResults([]);
      });
    return () => {
      alive = false;
    };
  }, [q, enableFmpSuggestions]);

  const trackedSymbolSet = useMemo(
    () => new Set(trackedList.map((u) => u.symbol)),
    [trackedList]
  );

  // Merged, flat list so keyboard nav (activeIndex) stays a single flat
  // index across both visual sections. Section ORDER depends on
  // `universeFirst` (see ../context/SearchDefaultContext.tsx): universe-first
  // (new default) puts untracked FMP results ahead of tracked/"Saved"
  // matches; tracked-first (rollback) puts tracked matches ahead of
  // untracked, reproducing the pre-2026-09 default exactly.
  const suggestions = useMemo<SuggestionRow[]>(() => {
    const tracked: SuggestionRow[] = trackedSuggestions.map((u) => ({
      symbol: u.symbol,
      action: u.action,
      tracked: true,
    }));
    if (!enableFmpSuggestions) return tracked;
    const untracked: SuggestionRow[] = fmpResults
      .filter((r) => r.symbol !== q && !trackedSymbolSet.has(r.symbol))
      .slice(0, MAX_FMP_SUGGESTIONS)
      .map((r) => ({ symbol: r.symbol, action: null, tracked: false }));
    return universeFirst ? [...untracked, ...tracked] : [...tracked, ...untracked];
  }, [trackedSuggestions, fmpResults, trackedSymbolSet, enableFmpSuggestions, q, universeFirst]);

  const showDropdown = open && suggestions.length > 0;
  const activeId =
    activeIndex >= 0 && activeIndex < suggestions.length
      ? `${listId}-opt-${activeIndex}`
      : undefined;

  // Checked against the FULL tracked set and raw fmpResults, not the merged
  // `suggestions` list above -- that list deliberately excludes an exact
  // already-tracked match (trackedSuggestions skips `s === q`, since a
  // symbol you already typed exactly needs no suggestion row), which would
  // otherwise make a perfectly valid, already-tracked ticker look "unknown"
  // here. Only meaningful when `requireExactMatch` is set; unused (and free
  // of any behavioral effect) otherwise.
  const isKnownSymbol =
    q.length > 0 && (trackedSymbolSet.has(q) || fmpResults.some((r) => r.symbol === q));

  const commit = (sym: string, opts?: { fromSuggestion?: boolean }) => {
    const clean = sym.trim().toUpperCase();
    if (!clean) return;
    if (requireExactMatch && !opts?.fromSuggestion) {
      const known = trackedSymbolSet.has(clean) || fmpResults.some((r) => r.symbol === clean);
      if (!known) return; // reject silently -- the disabled button already signals why
    }
    setValue(clean);
    if (onChange) onChange(clean);
    setOpen(false);
    setActiveIndex(-1);
    onSubmit(clean);
  };

  // Auto-submit once the (debounced) typed value resolves to a known real
  // symbol -- so finishing a valid ticker behaves like clicking a suggestion
  // already does, instead of silently waiting for a manual button press.
  // `lastAutoSubmitted` guards against refiring on every render for the same
  // resolved symbol (isKnownSymbol/q stay stable once resolved); it resets
  // once the query no longer matches, so retyping the same symbol later
  // re-arms it.
  const lastAutoSubmittedRef = useRef<string | null>(null);
  useEffect(() => {
    // Gated on requireExactMatch too, not just autoSubmitOnExactMatch: without
    // it, free text always submits anyway (Enter / blur+Load), so there is
    // nothing distinctly "exact match" to react to, and firing here would
    // submit mid-typing before the operator ever pressed Enter or Load.
    if (!autoSubmitOnExactMatch || !requireExactMatch) return;
    if (!isKnownSymbol) {
      lastAutoSubmittedRef.current = null;
      return;
    }
    if (lastAutoSubmittedRef.current === q) return;
    lastAutoSubmittedRef.current = q;
    commit(q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, isKnownSymbol, autoSubmitOnExactMatch, requireExactMatch]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    commit(value);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!suggestions.length) return;
      setOpen(true);
      setActiveIndex((i) => (i + 1) % suggestions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!suggestions.length) return;
      setOpen(true);
      setActiveIndex((i) =>
        i <= 0 ? suggestions.length - 1 : i - 1
      );
    } else if (e.key === "Escape") {
      setOpen(false);
      setActiveIndex(-1);
    } else if (
      (e.key === "Enter" || e.key === "Tab") &&
      showDropdown &&
      activeIndex >= 0
    ) {
      // A suggestion is highlighted → accept it (and load it on Enter).
      // Always a real, known symbol by construction -- bypass requireExactMatch.
      e.preventDefault();
      commit(suggestions[activeIndex].symbol, { fromSuggestion: true });
    }
    // Enter with nothing highlighted falls through to the form's submit handler,
    // preserving free-text lookup of any ticker.
  };

  return (
    <form
      onSubmit={submit}
      style={{ display: "flex", gap: "var(--s-2)", alignItems: "flex-end", marginBottom: "var(--s-4)" }}
    >
      <div style={{ flex: 1, position: "relative" }}>
        <label
          htmlFor={autoId}
          className="tile-label"
          style={{ display: "block", marginBottom: "var(--s-1-5)" }}
        >
          {label}
        </label>
        <input
          id={autoId}
          className="input"
          data-testid={testId}
          role="combobox"
          aria-expanded={showDropdown}
          aria-controls={listId}
          aria-activedescendant={activeId}
          aria-autocomplete="list"
          aria-describedby={hintId}
          autoCapitalize="characters"
          autoCorrect="off"
          autoComplete="off"
          spellCheck={false}
          inputMode="text"
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            if (onChange) onChange(e.target.value);
            setOpen(true);
            setActiveIndex(-1);
          }}
          onKeyDown={onKeyDown}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            setOpen(false);
            setActiveIndex(-1);
          }}
        />
        <div
          id={hintId}
          style={{
            marginTop: "var(--s-1-5)",
            fontSize: "var(--t-caption)",
            color: "var(--text-muted)",
          }}
        >
          {requireExactMatch
            ? autoSubmitOnExactMatch
              ? "Pick a suggested symbol, or finish typing a recognized ticker to load it automatically."
              : "Pick a suggested symbol -- only a recognized, quotable ticker can be submitted."
            : enableFmpSuggestions && universeFirst
            ? hideButton
              ? "Type to search any stock, or pick a saved symbol below."
              : "Type to search any stock, or enter any ticker and press Load."
            : hideButton
            ? "Type to search tracked symbols, or enter any ticker and press Enter."
            : "Type to search tracked symbols, or enter any ticker and press Load."}
        </div>
        {requireExactMatch && value.trim().length > 0 && !isKnownSymbol && (
          <div
            style={{
              marginTop: "var(--s-1)",
              fontSize: "var(--t-caption)",
              color: "var(--decline)",
            }}
          >
            Not a recognized ticker yet -- pick one from the suggestions below.
          </div>
        )}

        {showDropdown && (
          <ul
            id={listId}
            className="combobox-list"
            data-testid="symbol-suggestions"
            role="listbox"
          >
            {suggestions.map((s, i) => {
              const selected = i === activeIndex;
              // Only the SECONDARY section ever gets a header -- whichever
              // section is primary (per `universeFirst`: untracked in the
              // new universe-first default, tracked in tracked-first
              // rollback mode) stays unlabeled, even when it's the entire
              // list. The secondary section gets its header at its first row
              // REGARDLESS of whether that's i === 0 (the primary section had
              // zero matches, so the secondary section IS the whole list --
              // matches the pre-2026-09 "Not yet tracked" behavior, which
              // always labeled the untracked section even with no tracked
              // matches at all) or a genuine mid-list transition.
              //
              // `enableFmpSuggestions` gates this entirely: when it's false
              // (Sector Selection is the one call site that sets it), there
              // is no untracked section at all -- `suggestions` is just
              // `tracked` verbatim (see the useMemo above), every row has
              // `tracked: true`, and there is no "secondary" section for a
              // header to distinguish. Without this guard,
              // `s.tracked === universeFirst` is trivially true for row 0
              // whenever `universeFirst` is `true` (today's default),
              // spuriously rendering a "Saved" header over an entirely-
              // tracked, entirely-unsectioned list -- a real regression
              // caught by a live browser check, since pre-2026-09 this call
              // site's `!s.tracked` condition could never be true and so
              // never rendered a header at all. Fixed by requiring a
              // genuine two-section split to exist before labeling either
              // half of it.
              const showHeader =
                enableFmpSuggestions &&
                s.tracked === universeFirst &&
                (i === 0 || suggestions[i - 1].tracked !== s.tracked);
              return (
                <Fragment key={s.symbol}>
                  {showHeader && (
                    <li role="presentation" className="combobox-section-header" aria-hidden="true">
                      {s.tracked ? "Saved" : "Not yet tracked"}
                    </li>
                  )}
                  <li
                    id={`${listId}-opt-${i}`}
                    className={`combobox-option${selected ? " is-active" : ""}`}
                    role="option"
                    aria-selected={selected}
                    onMouseDown={(e) => {
                      e.preventDefault(); // keep focus in the input through the click
                      // Always a real, known symbol by construction -- bypass requireExactMatch.
                      commit(s.symbol, { fromSuggestion: true });
                    }}
                  >
                    <span className="combobox-symbol">{s.symbol}</span>
                    {s.action && <span className="combobox-action">{s.action}</span>}
                  </li>
                </Fragment>
              );
            })}
          </ul>
        )}
        {hint && (
          <div style={{ marginTop: "var(--s-1)", fontSize: "var(--t-body)", color: "var(--text-muted)" }}>
            {hint}
          </div>
        )}
      </div>
      {!hideButton && (
        <Button
          type="submit"
          variant="primary"
          pending={pending}
          disabled={requireExactMatch && !isKnownSymbol}
        >
          {buttonText || "Load"}
        </Button>
      )}
    </form>
  );
}
