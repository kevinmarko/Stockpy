import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import type { CommandManifest } from "../api/types";
import { parseCommandLine, type Suggestion } from "../commandParse";
import {
  Button,
  EmptyState,
  ErrorState,
  Loading,
  StaleDataNotice,
} from "../components/ui";
import { ExecutionQueueSection } from "../components/ExecutionQueueSection";
import { theme } from "../theme";

/**
 * Commands — an autocomplete command bar over the platform's CLI manifest
 * (GET /commands, built offline by scripts/build_command_manifest.py). It
 * resolves commands/subcommands + aliases, lists options with descriptions,
 * defaults and choices, and validates missing/unknown args before submit.
 *
 * Compose-only: it produces the exact CLI string to run in a terminal (Copy) —
 * it never executes anything. Executing platform CLIs from a web UI would
 * bypass the advisory quarantine (ADVISORY_ONLY / kill switch / risk gate).
 */
export function Commands() {
  const { data, loading, error, status, stale, cachedAt, reload } =
    useApi<CommandManifest>(() => api.getCommands(), []);

  return (
    <div className="screen">
      <div className="rail-head">
        <h1>Commands</h1>
      </div>
      <p style={{ color: theme.textSecondary, marginTop: -4, marginBottom: 16 }}>
        Autocomplete for the platform's command-line tools. Compose a command,
        then copy it to run in your terminal — this screen never executes anything.
      </p>

      {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        data.commands.length === 0 ? (
          <EmptyState
            title="No commands available yet"
            hint={data.reason ?? "Run scripts/build_command_manifest.py to generate the manifest."}
          />
        ) : (
          <CommandBar commands={data.commands} />
        )
      )}

      <ExecutionQueueSection />
    </div>
  );
}

function CommandBar({ commands }: { commands: CommandManifest["commands"] }) {
  const [input, setInput] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [open, setOpen] = useState(true);
  const [copied, setCopied] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const parsed = useMemo(() => parseCommandLine(input, commands), [input, commands]);
  const suggestions = parsed.suggestions;
  const errors = parsed.hints.filter((h) => h.level === "error");

  const accept = (s: Suggestion) => {
    const tokens = input.split(/\s+/).filter(Boolean);
    const typing = input.length > 0 && !/\s$/.test(input);
    const completingIndex = typing ? tokens.length - 1 : tokens.length;
    const prefix = tokens.slice(0, completingIndex);
    setInput([...prefix, s.value].join(" ") + " ");
    setActiveIndex(0);
    setOpen(true);
    setCopied(false);
    inputRef.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActiveIndex((i) => (suggestions.length ? (i + 1) % suggestions.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (suggestions.length ? (i - 1 + suggestions.length) % suggestions.length : 0));
    } else if (e.key === "Tab" && suggestions.length && open) {
      e.preventDefault();
      accept(suggestions[Math.min(activeIndex, suggestions.length - 1)]);
    } else if (e.key === "Enter" && suggestions.length && open) {
      e.preventDefault();
      accept(suggestions[Math.min(activeIndex, suggestions.length - 1)]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  const copy = () => {
    if (!parsed.composed) return;
    void navigator.clipboard?.writeText(parsed.composed);
    setCopied(true);
  };

  // The dropdown is for refining a command being typed; when the field is empty
  // the reference list below serves discovery, so they never both show the same
  // command at once.
  const showDropdown = open && suggestions.length > 0 && input.trim() !== "";
  const activeId = suggestions.length ? `cmd-opt-${Math.min(activeIndex, suggestions.length - 1)}` : undefined;

  return (
    <div>
      <div style={{ position: "relative" }}>
        <input
          ref={inputRef}
          className="input"
          data-testid="command-bar-input"
          role="combobox"
          aria-expanded={showDropdown}
          aria-controls="command-suggestions"
          aria-activedescendant={open ? activeId : undefined}
          aria-autocomplete="list"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          placeholder="Type a command, e.g. validation.harness --strategy …"
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setActiveIndex(0);
            setOpen(true);
            setCopied(false);
          }}
          onKeyDown={onKeyDown}
          onFocus={() => setOpen(true)}
          style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)" }}
        />

        {showDropdown && (
          <ul
            id="command-suggestions"
            data-testid="command-suggestions"
            role="listbox"
            style={{
              listStyle: "none",
              margin: "4px 0 0",
              padding: 4,
              position: "absolute",
              zIndex: 30,
              left: 0,
              right: 0,
              maxHeight: 320,
              overflowY: "auto",
              background: theme.surface2,
              border: `1px solid ${theme.borderStrong}`,
              borderRadius: 10,
            }}
          >
            {suggestions.map((s, i) => {
              const selected = i === Math.min(activeIndex, suggestions.length - 1);
              return (
                <li
                  key={`${s.kind}-${s.value}`}
                  id={`cmd-opt-${i}`}
                  role="option"
                  aria-selected={selected}
                  onMouseDown={(e) => {
                    e.preventDefault(); // keep focus in the input
                    accept(s);
                  }}
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    gap: 10,
                    padding: "8px 10px",
                    borderRadius: 8,
                    cursor: "pointer",
                    background: selected ? theme.surface3 : "transparent",
                  }}
                >
                  <span aria-hidden style={{ fontSize: 10, color: kindColor(s.kind), minWidth: 62 }}>
                    {s.kind}
                  </span>
                  <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontWeight: 600, color: theme.textPrimary }}>
                    {s.label}
                  </span>
                  {s.description && (
                    <span style={{ color: theme.textMuted, fontSize: 12 }}>{s.description}</span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Validation hints */}
      {parsed.hints.length > 0 && (
        <ul data-testid="command-hints" style={{ listStyle: "none", padding: 0, margin: "10px 0 0" }}>
          {parsed.hints.map((h, i) => (
            <li
              key={i}
              style={{
                color: h.level === "error" ? theme.decline : theme.caution,
                fontSize: 13,
                marginTop: 4,
              }}
            >
              {h.level === "error" ? "✗" : "!"} {h.message}
            </li>
          ))}
        </ul>
      )}

      {/* Composed command + copy */}
      {parsed.composed && (
        <div style={{ marginTop: 16 }}>
          <div className="tile-label" style={{ marginBottom: 6 }}>
            Command to run{errors.length ? " (incomplete — see above)" : ""}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "stretch" }}>
            <code
              data-testid="command-composed"
              style={{
                flex: 1,
                padding: "10px 12px",
                background: theme.surface,
                border: `1px solid ${theme.border}`,
                borderRadius: 8,
                fontFamily: "var(--font-mono, ui-monospace, monospace)",
                color: theme.textPrimary,
                overflowX: "auto",
                whiteSpace: "pre",
              }}
            >
              {parsed.composed}
            </code>
            <Button onClick={copy} data-testid="command-copy">
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
        </div>
      )}

      {/* Reference list when nothing typed yet */}
      {input.trim() === "" && (
        <div style={{ marginTop: 24 }}>
          <div className="tile-label" style={{ marginBottom: 8 }}>
            Available commands
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {commands.map((c) => (
              <button
                key={c.name}
                onClick={() => {
                  setInput(c.name + " ");
                  setOpen(true);
                  inputRef.current?.focus();
                }}
                style={{
                  textAlign: "left",
                  padding: "10px 12px",
                  background: theme.surface,
                  border: `1px solid ${theme.border}`,
                  borderRadius: 8,
                  cursor: "pointer",
                }}
              >
                <div style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontWeight: 700, color: theme.textPrimary }}>
                  {c.name}
                </div>
                {c.description && (
                  <div style={{ color: theme.textMuted, fontSize: 12, marginTop: 2 }}>{c.description}</div>
                )}
                <div style={{ color: theme.textSecondary, fontSize: 11, marginTop: 2 }}>{c.invocation}</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function kindColor(kind: Suggestion["kind"]): string {
  switch (kind) {
    case "command":
      return theme.accent;
    case "subcommand":
      return theme.growth;
    case "value":
      return theme.caution;
    default:
      return theme.textMuted;
  }
}
