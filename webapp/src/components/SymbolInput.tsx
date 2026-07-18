import {
  useEffect,
  useId,
  useMemo,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { Button } from "./ui";
import { api } from "../api/client";
import type { UniverseSymbol } from "../api/types";

/**
 * Shared symbol entry bar for the per-symbol research screens (Data Explorer,
 * Signal Breakdown, Forecast Viewer). An accessible combobox: as the operator
 * types, it suggests tickers from the tracked universe (`GET /universe`) so they
 * don't have to know a symbol by heart — every suggestion resolves to a real
 * detail page. Selecting a suggestion (Enter on a highlighted row, Tab, or
 * click) loads it immediately.
 *
 * Free-text is preserved: pressing Load — or Enter with nothing highlighted —
 * submits whatever is typed, uppercased/trimmed, even if it isn't in the
 * universe (so arbitrary tickers still work). The universe fetch is lazy,
 * shared across all instances (module cache), and non-fatal: if it fails the
 * field silently degrades to a plain text input.
 *
 * Only commits to `onSubmit` on a deliberate action (submit / accept), never per
 * keystroke, so the owning screen's `useApi` refetches once per lookup.
 */

// Module-level cache: the universe is identical for every SymbolInput and rarely
// changes within a session, so fetch it at most once and share the result.
let universeCache: UniverseSymbol[] | null = null;
let universePromise: Promise<UniverseSymbol[]> | null = null;

function loadUniverse(): Promise<UniverseSymbol[]> {
  if (universeCache) return Promise.resolve(universeCache);
  if (!universePromise) {
    universePromise = api
      .getUniverse()
      .then((r) => {
        universeCache = r.symbols ?? [];
        return universeCache;
      })
      .catch((err) => {
        // Non-fatal to the user: degrade to a plain text field (free-text still
        // works). Still log so a real outage is diagnosable rather than silently
        // indistinguishable from "nothing tracked yet". Reset the promise so a
        // later mount can retry.
        console.warn("SymbolInput: failed to load the tracked-symbol universe", err);
        universePromise = null;
        return [];
      });
  }
  return universePromise;
}

/** Exposed for tests to reset the shared cache between cases. */
export function __resetUniverseCache() {
  universeCache = null;
  universePromise = null;
}

const MAX_SUGGESTIONS = 8;

export function SymbolInput({
  initial = "",
  onSubmit,
  label = "Symbol",
  pending,
}: {
  initial?: string;
  onSubmit: (symbol: string) => void;
  label?: string;
  pending?: boolean;
}) {
  const [value, setValue] = useState(initial);
  const [universe, setUniverse] = useState<UniverseSymbol[]>(universeCache ?? []);
  const [open, setOpen] = useState(false);
  // -1 = nothing highlighted → Enter submits the typed text (free-text default);
  // 0..n-1 = a suggestion is highlighted → Enter/Tab accept it.
  const [activeIndex, setActiveIndex] = useState(-1);
  const autoId = useId();
  const listId = `${autoId}-symbols`;
  const hintId = `${autoId}-hint`;

  useEffect(() => {
    let alive = true;
    void loadUniverse().then((u) => {
      if (alive) setUniverse(u);
    });
    return () => {
      alive = false;
    };
  }, []);

  const q = value.trim().toUpperCase();
  const suggestions = useMemo(() => {
    if (!q) return [];
    const starts: UniverseSymbol[] = [];
    const contains: UniverseSymbol[] = [];
    for (const u of universe) {
      const s = u.symbol;
      if (s === q) continue; // exact match needs no suggestion — Enter submits it
      if (s.startsWith(q)) starts.push(u);
      else if (s.includes(q)) contains.push(u);
    }
    return [...starts, ...contains].slice(0, MAX_SUGGESTIONS);
  }, [q, universe]);

  const showDropdown = open && suggestions.length > 0;
  const activeId =
    activeIndex >= 0 && activeIndex < suggestions.length
      ? `${listId}-opt-${activeIndex}`
      : undefined;

  const commit = (sym: string) => {
    const clean = sym.trim().toUpperCase();
    if (!clean) return;
    setValue(clean);
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
      style={{ display: "flex", gap: 8, alignItems: "flex-end", marginBottom: 16 }}
    >
      <div style={{ flex: 1, position: "relative" }}>
        <label
          htmlFor={autoId}
          className="tile-label"
          style={{ display: "block", marginBottom: 6 }}
        >
          {label}
        </label>
        <input
          id={autoId}
          className="input"
          data-testid="symbol-input"
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
            marginTop: 6,
            fontSize: "var(--t-caption)",
            color: "var(--text-muted)",
          }}
        >
          Type to search tracked symbols, or enter any ticker and press Load.
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
              return (
                <li
                  key={s.symbol}
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
              );
            })}
          </ul>
        )}
      </div>
      <Button type="submit" variant="primary" pending={pending}>
        Load
      </Button>
    </form>
  );
}
