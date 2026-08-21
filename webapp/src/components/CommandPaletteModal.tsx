import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Modal } from "./Modal";
import { CopyCommandBlock } from "./CopyCommandBlock";
import type { CommandSpec, ReportFile, UniverseSymbol } from "../api/types";
import {
  parseCommandLine,
  getGhostText,
  tokenizeForHighlighting,
  getCommandCategory,
  CATEGORIES,
  type Suggestion,
  type HighlightToken,
} from "../commandParse";
import { theme } from "../theme";
import { Button } from "./ui";
import { api } from "../api/client";
import { loadUniverse } from "./universeCache";

interface CommandPaletteModalProps {
  isOpen: boolean;
  onClose: () => void;
  commands: CommandSpec[];
  onSelectCommandForBuilder?: (spec: CommandSpec) => void;
  onRunCommand?: (composed: string, spec: CommandSpec, argTokens: string[]) => void;
  /** Universal-search extras (Ticker/Report/Navigation categories) — all
   *  optional so the palette degrades to command-only search when a caller
   *  (or a test) doesn't wire them. */
  onInspectTicker?: (symbol: string) => void;
  onPreviewReport?: (reportName: string) => void;
  onNavigate?: (path: string) => void;
  /** Live registries from the command manifest, mirroring the free-text
   *  Command Bar's (Commands.tsx) own props -- optional so this modal still
   *  degrades to commandParse.ts's hardcoded REGISTERED_STRATEGIES/
   *  REGISTERED_OPTIONS_STRATEGIES fallbacks when a caller (or a test)
   *  doesn't wire them. */
  strategyRegistry?: string[];
  optionsStrategyRegistry?: string[];
}

/** Real, in-app routes only — never invented paths. Kept in sync with
 *  App.tsx's <Routes> by hand (small, stable list). */
const NAV_TARGETS: { label: string; path: string }[] = [
  { label: "Dashboard", path: "/" },
  { label: "Portfolio", path: "/portfolio" },
  { label: "Activity Feed", path: "/activity" },
  { label: "Agentic Trading", path: "/agentic" },
  { label: "Commands", path: "/commands" },
  { label: "Options Matrix", path: "/options" },
  { label: "Forecast Viewer", path: "/forecast" },
  { label: "Signal Breakdown", path: "/signals" },
  { label: "Observability / Mission Control", path: "/observability" },
  { label: "Console / Terminal", path: "/console" },
  { label: "Settings", path: "/settings" },
];

const MAX_TICKER_MATCHES = 6;
const MAX_REPORT_MATCHES = 5;

export function CommandPaletteModal({
  isOpen,
  onClose,
  commands,
  onSelectCommandForBuilder,
  onRunCommand,
  onInspectTicker,
  onPreviewReport,
  onNavigate,
  strategyRegistry = [],
  optionsStrategyRegistry = [],
}: CommandPaletteModalProps) {
  const [input, setInput] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [universe, setUniverse] = useState<UniverseSymbol[]>([]);
  const [reports, setReports] = useState<ReportFile[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    setInput("");
    setActiveIndex(0);
    const focusTimer = setTimeout(() => inputRef.current?.focus(), 50);

    let alive = true;
    // Both real, already-cached-elsewhere reads -- never fabricated ticker/
    // report lists. A failure here just means those two search categories
    // stay empty; the CLI command search below is unaffected.
    void loadUniverse().then((u) => {
      if (alive) setUniverse(u);
    });
    void api
      .getReports()
      .then((r) => {
        if (alive) setReports(r.reports ?? []);
      })
      .catch(() => {
        if (alive) setReports([]);
      });
    return () => {
      alive = false;
      // No explicit blur here -- this input always renders inside Modal's
      // sheetRef subtree (Modal.tsx), whose own cleanup blurs it (covering
      // both the desktop and mobile branches) before this cleanup runs.
      clearTimeout(focusTimer);
    };
  }, [isOpen]);

  const parsed = useMemo(
    () => parseCommandLine(input, commands, strategyRegistry, optionsStrategyRegistry),
    [input, commands, strategyRegistry, optionsStrategyRegistry]
  );
  const suggestions = parsed.suggestions;
  const ghostText = useMemo(() => getGhostText(input, suggestions), [input, suggestions]);
  const highlightedTokens = useMemo(() => tokenizeForHighlighting(input, commands), [input, commands]);

  const q = input.trim().toLowerCase();
  const tickerMatches = useMemo(() => {
    if (!q) return [];
    return universe.filter((u) => u.symbol.toLowerCase().includes(q)).slice(0, MAX_TICKER_MATCHES);
  }, [q, universe]);
  const reportMatches = useMemo(() => {
    if (!q) return [];
    return reports.filter((r) => r.name.toLowerCase().includes(q)).slice(0, MAX_REPORT_MATCHES);
  }, [q, reports]);
  const navMatches = useMemo(() => {
    if (!q) return [];
    return NAV_TARGETS.filter((n) => n.label.toLowerCase().includes(q));
  }, [q]);
  const hasOmniMatches = tickerMatches.length > 0 || reportMatches.length > 0 || navMatches.length > 0;

  if (!isOpen) return null;

  const accept = (s: Suggestion) => {
    const tokens = input.split(/\s+/).filter(Boolean);
    const typing = input.length > 0 && !/\s$/.test(input);
    const completingIndex = typing ? tokens.length - 1 : tokens.length;
    const prefix = tokens.slice(0, completingIndex);
    setInput([...prefix, s.value].join(" ") + " ");
    setActiveIndex(0);
    inputRef.current?.focus();
  };

  const goTicker = (symbol: string) => {
    onClose();
    onInspectTicker?.(symbol);
  };
  const goReport = (name: string) => {
    onClose();
    onPreviewReport?.(name);
  };
  const goNav = (path: string) => {
    onClose();
    if (onNavigate) onNavigate(path);
    else window.location.assign(path);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (suggestions.length ? (i + 1) % suggestions.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (suggestions.length ? (i - 1 + suggestions.length) % suggestions.length : 0));
    } else if ((e.key === "Tab" || e.key === "ArrowRight") && ghostText) {
      e.preventDefault();
      if (suggestions.length) {
        accept(suggestions[Math.min(activeIndex, suggestions.length - 1)]);
      }
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (suggestions.length && input.trim() !== "" && !parsed.command) {
        accept(suggestions[Math.min(activeIndex, suggestions.length - 1)]);
      } else if (parsed.composed && parsed.command) {
        if (onRunCommand) {
          onRunCommand(parsed.composed, parsed.command, parsed.argTokens);
          onClose();
        }
      } else if (!parsed.command && tickerMatches[0]) {
        // No CLI command resolved and nothing left to autocomplete -- if the
        // top ticker/report/nav match is unambiguous, Enter accepts it too,
        // matching a conventional omni-search's "Enter picks the top hit".
        goTicker(tickerMatches[0].symbol);
      } else if (!parsed.command && !tickerMatches.length && reportMatches[0]) {
        goReport(reportMatches[0].name);
      } else if (!parsed.command && !tickerMatches.length && !reportMatches.length && navMatches[0]) {
        goNav(navMatches[0].path);
      }
    }
    // Escape is intentionally NOT handled here -- Modal already closes on
    // Escape (see Modal.tsx), and this handler doesn't stopPropagation, so
    // duplicating it here just calls onClose() a second time for one press.
  };

  return (
    <Modal ariaLabel="Command Palette" onClose={onClose} size="wide">
      <div data-testid="command-palette-modal">
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--s-3)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
            <span style={{ fontSize: "1.2rem" }}>⚡</span>
            <span style={{ fontWeight: 700, fontSize: "var(--t-subhead)", color: theme.textPrimary }}>
              Command Palette
            </span>
          </div>
          <span
            style={{
              background: theme.surface2,
              padding: "2px 8px",
              borderRadius: "var(--r-sm)",
              fontSize: "var(--t-micro)",
              color: theme.textMuted,
              fontFamily: "var(--font-mono, ui-monospace, monospace)",
            }}
          >
            ESC to close
          </span>
        </div>

        {/* Syntax-highlighted Overlay Input Container */}
        <div style={{ position: "relative", marginBottom: "var(--s-3)" }}>
          {/* Formatted background preview layer */}
          <div
            aria-hidden
            style={{
              position: "absolute",
              inset: 0,
              padding: "var(--s-2-5) var(--s-3)",
              fontFamily: "var(--font-mono, ui-monospace, monospace)",
              fontSize: "var(--t-body)",
              lineHeight: "1.5",
              pointerEvents: "none",
              whiteSpace: "pre-wrap",
              color: "transparent",
              zIndex: 1,
            }}
          >
            {highlightedTokens.map((tok, idx) => (
              <span key={idx} style={{ color: getTokenColor(tok.type) }}>
                {tok.text}
              </span>
            ))}
            {ghostText && <span style={{ color: theme.textMuted, opacity: 0.5 }}>{ghostText}</span>}
          </div>

          {/* Actual Input Field */}
          <input
            ref={inputRef}
            className="input"
            data-testid="command-palette-input"
            placeholder="Search commands, tickers (NVDA), reports, screens, or type flags..."
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              setActiveIndex(0);
            }}
            onKeyDown={onKeyDown}
            style={{
              fontFamily: "var(--font-mono, ui-monospace, monospace)",
              fontSize: "var(--t-body)",
              padding: "var(--s-2-5) var(--s-3)",
              background: "transparent",
              color: input ? "transparent" : theme.textPrimary,
              caretColor: theme.textPrimary,
              position: "relative",
              zIndex: 2,
            }}
          />
        </div>

        {/* Composed CLI Preview Bar if resolved */}
        {parsed.composed && (
          <div style={{ marginBottom: "var(--s-3)" }}>
            <CopyCommandBlock command={parsed.composed} label="Compiled Execution Target" />
            {parsed.command && (
              <div style={{ marginTop: "var(--s-2)", display: "flex", justifyContent: "flex-end", gap: "var(--s-2)" }}>
                {onSelectCommandForBuilder && (
                  <Button
                    variant="neutral"
                    onClick={() => {
                      if (parsed.command) onSelectCommandForBuilder(parsed.command);
                      onClose();
                    }}
                  >
                    Configure in Form Builder 🛠️
                  </Button>
                )}
                {onRunCommand && (
                  <Button
                    variant="primary"
                    disabled={parsed.hints.some((h) => h.level === "error")}
                    onClick={() => {
                      if (parsed.command && parsed.composed) {
                        onRunCommand(parsed.composed, parsed.command, parsed.argTokens);
                        onClose();
                      }
                    }}
                  >
                    Run Command 🚀
                  </Button>
                )}
              </div>
            )}
          </div>
        )}

        {/* Validation Errors/Warnings */}
        {parsed.hints.length > 0 && (
          <div style={{ marginBottom: "var(--s-3)" }}>
            {parsed.hints.map((h, i) => (
              <div
                key={i}
                style={{
                  fontSize: "var(--t-caption)",
                  color: h.level === "error" ? theme.decline : theme.caution,
                  marginBottom: 2,
                }}
              >
                {h.level === "error" ? "✗" : "!"} {h.message}
              </div>
            ))}
          </div>
        )}

        {/* Suggestions List (CLI command/subcommand/option/value completions) */}
        {suggestions.length > 0 && (
          <div style={{ maxHeight: 220, overflowY: "auto", borderTop: `1px solid ${theme.border}`, paddingTop: "var(--s-2)" }}>
            <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted, marginBottom: "var(--s-1)", textTransform: "uppercase" }}>
              Suggestions ({suggestions.length})
            </div>
            {suggestions.map((s, i) => {
              const isSelected = i === Math.min(activeIndex, suggestions.length - 1);
              return (
                <div
                  key={`${s.kind}-${s.value}-${i}`}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    accept(s);
                  }}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "var(--s-2) var(--s-2-5)",
                    borderRadius: "var(--r-sm)",
                    background: isSelected ? theme.surface3 : "transparent",
                    cursor: "pointer",
                    marginBottom: 2,
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
                    <span
                      style={{
                        fontSize: 10,
                        padding: "1px 6px",
                        borderRadius: 4,
                        fontWeight: 600,
                        background: getKindBg(s.kind),
                        color: getKindColor(s.kind),
                      }}
                    >
                      {s.kind}
                    </span>
                    <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontWeight: 600, color: theme.textPrimary }}>
                      {s.label}
                    </span>
                  </div>
                  {s.description && (
                    <span style={{ fontSize: "var(--t-caption)", color: theme.textMuted, maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {s.description}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Universal quick-results: tickers / reports / navigation. Additive
            to the CLI suggestions above -- typing "NVDA" surfaces the ticker
            even though it never resolves as a CLI suggestion. */}
        {input.trim() !== "" && hasOmniMatches && (
          <div style={{ marginTop: "var(--s-2)", borderTop: `1px solid ${theme.border}`, paddingTop: "var(--s-2)" }}>
            {tickerMatches.length > 0 && (
              <OmniSection title="📈 Tickers">
                {tickerMatches.map((t) => (
                  <OmniRow
                    key={t.symbol}
                    label={t.symbol}
                    sublabel={t.action ? `Tracked · ${t.action}` : "Inspect ticker details, signals & risk blocks"}
                    onClick={() => goTicker(t.symbol)}
                  />
                ))}
              </OmniSection>
            )}
            {reportMatches.length > 0 && (
              <OmniSection title="📑 Reports">
                {reportMatches.map((r) => (
                  <OmniRow
                    key={r.name}
                    label={r.name}
                    sublabel="Preview report content"
                    onClick={() => goReport(r.name)}
                  />
                ))}
              </OmniSection>
            )}
            {navMatches.length > 0 && (
              <OmniSection title="🧭 Navigation">
                {navMatches.map((n) => (
                  <OmniRow key={n.path} label={n.label} sublabel={`Navigate to ${n.path}`} onClick={() => goNav(n.path)} />
                ))}
              </OmniSection>
            )}
          </div>
        )}

        {/* Category Discovery List when search input is empty */}
        {input.trim() === "" && (
          <div style={{ marginTop: "var(--s-2)", borderTop: `1px solid ${theme.border}`, paddingTop: "var(--s-3)" }}>
            <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted, marginBottom: "var(--s-2)", textTransform: "uppercase" }}>
              Browse by Category
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--s-2)" }}>
              {CATEGORIES.map((cat) => {
                const count = commands.filter((c) => getCommandCategory(c.name) === cat.id).length;
                return (
                  <div
                    key={cat.id}
                    onClick={() => {
                      const firstInCat = commands.find((c) => getCommandCategory(c.name) === cat.id);
                      if (firstInCat) setInput(firstInCat.name + " ");
                    }}
                    style={{
                      padding: "var(--s-2) var(--s-3)",
                      background: theme.surface,
                      border: `1px solid ${theme.border}`,
                      borderRadius: "var(--r-sm)",
                      cursor: "pointer",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", fontWeight: 600, color: theme.textPrimary }}>
                      <span>{cat.icon}</span>
                      <span>{cat.label}</span>
                      <span style={{ fontSize: "var(--t-micro)", color: theme.textMuted, marginLeft: "auto" }}>
                        ({count})
                      </span>
                    </div>
                    <div style={{ fontSize: "var(--t-caption)", color: theme.textMuted, marginTop: "var(--s-0-5)" }}>
                      {cat.description}
                    </div>
                  </div>
                );
              })}
            </div>

            {(universe.length > 0 || reports.length > 0) && (
              <div style={{ marginTop: "var(--s-3)" }}>
                <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted, marginBottom: "var(--s-2)", textTransform: "uppercase" }}>
                  Quick jump
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-1-5)" }}>
                  {universe.slice(0, MAX_TICKER_MATCHES).map((u) => (
                    <button
                      key={u.symbol}
                      className="btn"
                      style={{ fontSize: "var(--t-caption)", padding: "var(--s-1) var(--s-2-5)" }}
                      onClick={() => goTicker(u.symbol)}
                    >
                      📈 {u.symbol}
                    </button>
                  ))}
                  {reports.slice(0, 3).map((r) => (
                    <button
                      key={r.name}
                      className="btn"
                      style={{ fontSize: "var(--t-caption)", padding: "var(--s-1) var(--s-2-5)" }}
                      onClick={() => goReport(r.name)}
                    >
                      📑 {r.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

function OmniSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={{ marginBottom: "var(--s-2-5)" }}>
      <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted, marginBottom: "var(--s-1)", textTransform: "uppercase" }}>
        {title}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>{children}</div>
    </div>
  );
}

function OmniRow({ label, sublabel, onClick }: { label: string; sublabel?: string; onClick: () => void }) {
  return (
    <div
      onMouseDown={(e) => {
        e.preventDefault();
        onClick();
      }}
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "var(--s-2) var(--s-2-5)",
        borderRadius: "var(--r-sm)",
        cursor: "pointer",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLDivElement).style.background = theme.surface3;
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLDivElement).style.background = "transparent";
      }}
    >
      <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontWeight: 600, color: theme.textPrimary }}>
        {label}
      </span>
      {sublabel && (
        <span style={{ fontSize: "var(--t-caption)", color: theme.textMuted, maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {sublabel}
        </span>
      )}
    </div>
  );
}

function getTokenColor(type: HighlightToken["type"]): string {
  switch (type) {
    case "interpreter":
      return theme.accent;
    case "command":
      return theme.growth;
    case "subcommand":
      return theme.accent;
    case "option":
      return theme.caution;
    case "flag":
      return theme.caution;
    case "value":
      return theme.textPrimary;
    default:
      return theme.textMuted;
  }
}

function getKindColor(kind: Suggestion["kind"]): string {
  switch (kind) {
    case "command":
      return "#38bdf8";
    case "subcommand":
      return "#4ade80";
    case "option":
      return "#facc15";
    case "value":
      return "#f472b6";
    default:
      return theme.textMuted;
  }
}

function getKindBg(kind: Suggestion["kind"]): string {
  switch (kind) {
    case "command":
      return "rgba(56, 189, 248, 0.15)";
    case "subcommand":
      return "rgba(74, 222, 128, 0.15)";
    case "option":
      return "rgba(250, 204, 21, 0.15)";
    case "value":
      return "rgba(244, 114, 182, 0.15)";
    default:
      return "rgba(255, 255, 255, 0.1)";
  }
}
