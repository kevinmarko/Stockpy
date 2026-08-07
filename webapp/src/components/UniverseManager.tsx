import { useState, useMemo } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { UniverseListResponse } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, ErrorState, Input, Loading } from "./ui";
import { SymbolInput } from "./SymbolInput";
import { theme } from "../theme";
import { Modal } from "./Modal";

export function UniverseManager({ onSelect }: { onSelect?: (symbol: string) => void }) {
  const nav = useNavigate();
  const loaded = useApi<UniverseListResponse>(() => api.getDataUniverse(), []);
  const { run: save, pending, error: saveError } = useMutation(
    api.updateDataUniverse,
    { successMessage: "Universe updated" }
  );
  const [symbols, setSymbols] = useState<string[] | null>(null);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [bulkImportOpen, setBulkImportOpen] = useState(false);
  const [bulkText, setBulkText] = useState("");

  const list = symbols ?? loaded.data?.symbols ?? [];
  
  const filteredList = useMemo(() => {
    return list.filter(s => s.toLowerCase().includes(search.toLowerCase()));
  }, [list, search]);

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

  const addSymbol = async (sym: string) => {
    sym = sym.trim().toUpperCase();
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

  const addBulk = async () => {
    const newSyms = bulkText.split(/[\s,]+/).map(s => s.trim().toUpperCase()).filter(s => s.length > 0);
    const uniqueNew = newSyms.filter(s => !list.includes(s));
    if (uniqueNew.length > 0) {
      await persist([...list, ...uniqueNew]);
    }
    setBulkImportOpen(false);
    setBulkText("");
  };

  const clearAll = async () => {
    if (window.confirm("Are you sure you want to clear all tracked symbols?")) {
       await persist([]);
    }
  };

  return (
    <div data-testid="universe-manager">
      {loaded.loading && <Loading lines={1} />}
      {!loaded.loading && loaded.error && (
        <ErrorState message={loaded.error} status={loaded.status} onRetry={loaded.reload} />
      )}
      {!loaded.loading && !loaded.error && (
        <>
          <div style={{ display: "flex", gap: "var(--s-2)", marginBottom: "var(--s-3)", alignItems: "flex-end" }}>
            <div style={{ flex: 1 }}>
              <Input
                label="Search Tracking List"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Filter symbols..."
              />
            </div>
            <Button variant="neutral" onClick={() => setBulkImportOpen(true)} disabled={pending}>
              Bulk Import CSV
            </Button>
            <Button variant="neutral" onClick={clearAll} disabled={pending || list.length === 0} style={{ color: theme.decline }}>
              Clear All
            </Button>
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", marginBottom: "var(--s-4)", maxHeight: "400px", overflowY: "auto", padding: "4px" }}>
            {filteredList.length === 0 ? (
              <span style={{ fontSize: "var(--t-body)", color: theme.textMuted }}>
                {list.length === 0 ? "No symbols tracked yet." : "No symbols match your search."}
              </span>
            ) : (
              filteredList.map((s) => (
                <span
                  key={s}
                  data-testid={`universe-chip-${s}`}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "var(--s-1-5)",
                    background: theme.surface2,
                    border: `1px solid ${theme.border}`,
                    borderRadius: 20,
                    padding: "var(--s-1) var(--s-1-5) var(--s-1) var(--s-3)",
                    fontSize: "var(--t-body)",
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
                    style={{ background: "none", border: "none", cursor: "pointer", color: theme.textMuted, fontSize: "var(--t-subhead)", lineHeight: 1, padding: "0 var(--s-0-5)" }}
                  >
                    ×
                  </button>
                </span>
              ))
            )}
          </div>

          <SymbolInput
            // SymbolInput only reads `initial` on mount (it's uncontrolled
            // internally), so re-keying on `draft` forces a remount -- and
            // therefore a visible clear of the typed text -- once addSymbol
            // resets draft back to "" after a successful add. Without this
            // the input silently kept showing the just-added ticker.
            key={draft}
            label="Add a stock"
            initial={draft}
            onSubmit={(sym) => {
              setDraft(sym);
              void addSymbol(sym);
            }}
            hint="Enter any ticker and press Add — it joins your tracked universe."
            buttonText="Add"
            pending={pending}
          />
          {(note || saveError) && (
            <div style={{ marginTop: "var(--s-2)", fontSize: "var(--t-body)", color: saveError ? theme.decline : theme.textMuted }}>
              {saveError ?? note}
            </div>
          )}
        </>
      )}

      {bulkImportOpen && (
        <Modal ariaLabel="Bulk Import" onClose={() => setBulkImportOpen(false)}>
          <h2 style={{ margin: "0 0 var(--s-4) 0" }}>Bulk Import Tickers</h2>
          <p style={{ color: theme.textSecondary, marginBottom: "var(--s-4)" }}>
            Paste a comma-separated or space-separated list of tickers.
          </p>
          <textarea
            value={bulkText}
            onChange={(e) => setBulkText(e.target.value)}
            style={{
              width: "100%",
              height: "150px",
              background: theme.surface,
              color: theme.textPrimary,
              border: `1px solid ${theme.border}`,
              borderRadius: "var(--r-md)",
              padding: "var(--s-2)",
              fontFamily: "monospace"
            }}
            placeholder="AAPL, MSFT, TSLA..."
          />
          <div style={{ display: "flex", gap: "var(--s-2-5)", marginTop: "var(--s-4)" }}>
            <Button variant="neutral" onClick={() => setBulkImportOpen(false)} style={{ flex: 1 }}>
              Cancel
            </Button>
            <Button variant="primary" onClick={addBulk} pending={pending} style={{ flex: 2 }} disabled={bulkText.trim().length === 0}>
              Import
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
