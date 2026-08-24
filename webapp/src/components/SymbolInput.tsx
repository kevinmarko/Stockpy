import {
  Fragment,
  useEffect,
  useId,
  useMemo,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { Button } from "./ui";
import { useDebounce } from "../hooks/useDebounce";
import { api } from "../api/client";
import type { UniverseSymbol, SymbolSearchResult } from "../api/types";
import { loadUniverse, getCachedUniverse } from "./universeCache";

/**
 * Shared symbol entry bar for the per-symbol research screens (Data Explorer,
 * Signal Breakdown, Forecast Viewer, Sentiment Dynamics, Sector Selection),
 * Pairs Radar, Cache Long/Short, Paper Broker's Quick Trade, and Universe
 * Manager. An accessible combobox: as the operator types, it suggests
 * tickers from the tracked universe (`GET /universe`, or `trackedSymbols` --
 * see below) so they don't have to know a symbol by heart — every tracked
 * suggestion resolves to a real detail page. It ALSO suggests any FMP-known
 * symbol not yet tracked (`GET /data/symbol-search`, debounced, rendered
 * under a "Not yet tracked" section) unless `enableFmpSuggestions={false}`.
 * Selecting a suggestion (Enter on a highlighted row, Tab, or click) loads it
 * immediately, tracked or not.
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
}) {
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

  // Merged, flat list -- tracked first, then FMP results not already
  // tracked -- so keyboard nav (activeIndex) stays a single flat index
  // across both visual sections.
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
    return [...tracked, ...untracked];
  }, [trackedSuggestions, fmpResults, trackedSymbolSet, enableFmpSuggestions, q]);

  const showDropdown = open && suggestions.length > 0;
  const activeId =
    activeIndex >= 0 && activeIndex < suggestions.length
      ? `${listId}-opt-${activeIndex}`
      : undefined;

  const commit = (sym: string) => {
    const clean = sym.trim().toUpperCase();
    if (!clean) return;
    setValue(clean);
    if (onChange) onChange(clean);
    setOpen(false);
    setActiveIndex(-1);
    onSubmit(clean);
  };

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
      e.preventDefault();
      commit(suggestions[activeIndex].symbol);
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
          {hideButton
            ? "Type to search tracked symbols, or enter any ticker and press Enter."
            : "Type to search tracked symbols, or enter any ticker and press Load."}
        </div>

        {showDropdown && (
          <ul
            id={listId}
            className="combobox-list"
            data-testid="symbol-suggestions"
            role="listbox"
          >
            {suggestions.map((s, i) => {
              const selected = i === activeIndex;
              // Section header right before the first untracked row --
              // i === 0 covers the FMP-only case (no tracked matches at
              // all), the previous-row check covers the mixed case.
              const showHeader = !s.tracked && (i === 0 || suggestions[i - 1].tracked);
              return (
                <Fragment key={s.symbol}>
                  {showHeader && (
                    <li role="presentation" className="combobox-section-header" aria-hidden="true">
                      Not yet tracked
                    </li>
                  )}
                  <li
                    id={`${listId}-opt-${i}`}
                    className={`combobox-option${selected ? " is-active" : ""}`}
                    role="option"
                    aria-selected={selected}
                    onMouseDown={(e) => {
                      e.preventDefault(); // keep focus in the input through the click
                      commit(s.symbol);
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
        <Button type="submit" variant="primary" pending={pending}>
          {buttonText || "Load"}
        </Button>
      )}
    </form>
  );
}
