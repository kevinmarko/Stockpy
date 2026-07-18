import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { UniverseListResponse } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, ErrorState, Input, Loading } from "./ui";
import { theme } from "../theme";

/**
 * Add/remove any stock from the tracked universe (`settings.DEFAULT_TICKERS`),
 * persisted via the data API's `PUT /data/universe`. Lives in Settings
 * alongside every other `.env`-write control in this app (Schedule, Execution
 * Mode, Signal modules, Brokerage) rather than on a browsing screen. Clicking
 * a chip's symbol navigates to that symbol's detail page by default, or calls
 * `onSelect` when the caller wants different behavior.
 *
 * After a write we render the PUT's *echoed* list, NOT a re-GET: in a live
 * process the GET reads the `settings` singleton, which a `.env` write does not
 * reach until the next process restart — a re-GET would show the old list and
 * look like the write failed.
 */
export function UniverseManager({ onSelect }: { onSelect?: (symbol: string) => void }) {
  const nav = useNavigate();
  const loaded = useApi<UniverseListResponse>(() => api.getDataUniverse(), []);
  const { run: save, pending, error: saveError } = useMutation(api.updateDataUniverse);
  const [symbols, setSymbols] = useState<string[] | null>(null);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState<string | null>(null);

  const list = symbols ?? loaded.data?.symbols ?? [];
  const go = (symbol: string) => {
    if (onSelect) onSelect(symbol);
    else nav(`/symbol/${encodeURIComponent(symbol)}`);
  };

  const persist = async (next: string[], added?: string) => {
    setNote(null);
    const res = await save(next);
    if (res) {
      setSymbols(res.symbols);
      if (added) go(added);
    }
  };

  const add = async () => {
    const sym = draft.trim().toUpperCase();
    if (!sym) return;
    if (list.includes(sym)) {
      setNote(`${sym} is already tracked.`);
      setDraft("");
      go(sym);
      return;
    }
    await persist([...list, sym], sym);
    setDraft("");
  };

  const remove = (sym: string) => persist(list.filter((s) => s !== sym));

  return (
    <div data-testid="universe-manager">
      {loaded.loading && <Loading lines={1} />}
      {!loaded.loading && loaded.error && (
        <ErrorState message={loaded.error} status={loaded.status} onRetry={loaded.reload} />
      )}
      {!loaded.loading && !loaded.error && (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
            {list.length === 0 ? (
              <span style={{ fontSize: 13, color: theme.textMuted }}>No symbols tracked yet.</span>
            ) : (
              list.map((s) => (
                <span
                  key={s}
                  data-testid={`universe-chip-${s}`}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    background: theme.surface2,
                    border: `1px solid ${theme.border}`,
                    borderRadius: 20,
                    padding: "4px 6px 4px 12px",
                    fontSize: 13,
                  }}
                >
                  <button
                    type="button"
                    onClick={() => go(s)}
                    style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: theme.textPrimary, fontWeight: 600 }}
                  >
                    {s}
                  </button>
                  <button
                    type="button"
                    aria-label={`Remove ${s}`}
                    data-testid={`universe-remove-${s}`}
                    onClick={() => remove(s)}
                    disabled={pending}
                    style={{ background: "none", border: "none", cursor: "pointer", color: theme.textMuted, fontSize: 15, lineHeight: 1, padding: "0 2px" }}
                  >
                    ×
                  </button>
                </span>
              ))
            )}
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              void add();
            }}
            style={{ display: "flex", gap: 8, alignItems: "flex-end" }}
          >
            <div style={{ flex: 1 }}>
              <Input
                label="Add a stock"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                hint="Enter any ticker and press Add — it joins your tracked universe."
              />
            </div>
            <Button type="submit" variant="primary" pending={pending}>
              Add
            </Button>
          </form>
          {(note || saveError) && (
            <div style={{ marginTop: 8, fontSize: 13, color: saveError ? theme.decline : theme.textMuted }}>
              {saveError ?? note}
            </div>
          )}
        </>
      )}
    </div>
  );
}
