import { useState, type FormEvent } from "react";
import { Input, Button } from "./ui";

/**
 * Shared symbol entry bar for the per-symbol research screens (Data Explorer,
 * Signal Breakdown, Forecast Viewer). Controlled locally; only commits an
 * upper-cased, trimmed symbol to `onSubmit` when the form is submitted, so the
 * owning screen's `useApi` refetches once per deliberate lookup (not per
 * keystroke). Empty input is a no-op.
 */
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

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const sym = value.trim().toUpperCase();
    if (sym) onSubmit(sym);
  };

  return (
    <form
      onSubmit={submit}
      style={{ display: "flex", gap: 8, alignItems: "flex-end", marginBottom: 16 }}
    >
      <div style={{ flex: 1 }}>
        <Input
          label={label}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          inputMode="text"
          hint="Enter a ticker and press Load."
        />
      </div>
      <Button type="submit" variant="primary" pending={pending}>
        Load
      </Button>
    </form>
  );
}
