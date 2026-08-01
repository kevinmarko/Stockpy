import React, { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router";
import { Modal } from "./Modal";
import { parseCommandLine } from "../commandParse";
import type { CommandSpec } from "../api/types";

export interface CommandPaletteModalProps {
  isOpen?: boolean;
  onClose: () => void;
  commands?: CommandSpec[];
  onInspectTicker?: (symbol: string) => void;
  onPreviewReport?: (reportTitle: string) => void;
  onRunCommand?: (command: string, spec?: CommandSpec, args?: string[]) => void;
  onSelectCommandForBuilder?: (spec: CommandSpec) => void;
}

interface CommandItem {
  id: string;
  category: "⚡ Commands" | "📈 Tickers" | "📑 Reports" | "🧭 Navigation";
  label: string;
  sublabel?: string;
  action: () => void;
  spec?: CommandSpec;
}

const POPULAR_TICKERS = ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "SPY", "QQQ"];
const SAMPLE_REPORTS = [
  "Daily Briefing 2026-08-01",
  "Gravity Verification Report",
  "Sector Rotation Brief",
  "HMM Regime Shift Audit",
];

const NAV_TARGETS = [
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

export function CommandPaletteModal({
  isOpen = true,
  onClose,
  commands = [],
  onInspectTicker,
  onPreviewReport,
  onRunCommand,
  onSelectCommandForBuilder,
}: CommandPaletteModalProps) {
  let nav: (path: string) => void;
  try {
    nav = useNavigate();
  } catch {
    nav = () => {};
  }
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);

  useEffect(() => {
    if (isOpen) {
      setQuery("");
    }
  }, [isOpen]);

  const parsed = useMemo(() => {
    return parseCommandLine(query, commands);
  }, [query, commands]);

  const resolvedSpec = parsed.active;
  const hasErrorHint = parsed.hints.some((h) => h.level === "error");
  const isRunnable = Boolean(parsed.composed) && !hasErrorHint;
  const hints = parsed.hints;

  const items = useMemo(() => {
    const list: CommandItem[] = [];
    const q = query.trim().toLowerCase();

    // 1. Commands from manifest
    if (commands && commands.length > 0) {
      commands.forEach((c) => {
        if (!q || c.name.toLowerCase().includes(q) || (c.description && c.description.toLowerCase().includes(q))) {
          list.push({
            id: `cmd-spec-${c.name}`,
            category: "⚡ Commands",
            label: c.name,
            sublabel: c.description ?? undefined,
            spec: c,
            action: () => {
              setQuery(c.name + " ");
            },
          });
        }
      });
    }

    // Default CLI presets
    const cliPresets = [
      { cmd: "python3 main.py", desc: "Run one advisory cycle" },
      { cmd: "python3 main.py --interval 60", desc: "Run interval loop (60s)" },
      { cmd: "pytest", desc: "Run test suite" },
      { cmd: "python scripts/preflight_check.py", desc: "Pre-live readiness check" },
    ];
    cliPresets.forEach((c) => {
      if (!q || c.cmd.toLowerCase().includes(q) || c.desc.toLowerCase().includes(q)) {
        list.push({
          id: `cmd-${c.cmd}`,
          category: "⚡ Commands",
          label: c.cmd,
          sublabel: c.desc,
          action: () => {
            if (onRunCommand) {
              onRunCommand(c.cmd);
            } else {
              nav(`/commands?cmd=${encodeURIComponent(c.cmd)}`);
            }
            onClose();
          },
        });
      }
    });

    // 2. Tickers
    POPULAR_TICKERS.forEach((sym) => {
      if (!q || sym.toLowerCase().includes(q)) {
        list.push({
          id: `ticker-${sym}`,
          category: "📈 Tickers",
          label: sym,
          sublabel: "Inspect ticker details, signals & risk blocks",
          action: () => {
            if (onInspectTicker) {
              onInspectTicker(sym);
            } else {
              nav(`/symbol/${sym}`);
            }
            onClose();
          },
        });
      }
    });

    // 3. Reports
    SAMPLE_REPORTS.forEach((rep) => {
      if (!q || rep.toLowerCase().includes(q)) {
        list.push({
          id: `rep-${rep}`,
          category: "📑 Reports",
          label: rep,
          sublabel: "Preview HTML / Markdown briefing report",
          action: () => {
            if (onPreviewReport) {
              onPreviewReport(rep);
            } else {
              nav(`/reports`);
            }
            onClose();
          },
        });
      }
    });

    // 4. Navigation
    NAV_TARGETS.forEach((navItem) => {
      if (!q || navItem.label.toLowerCase().includes(q)) {
        list.push({
          id: `nav-${navItem.path}`,
          category: "🧭 Navigation",
          label: navItem.label,
          sublabel: `Navigate to ${navItem.path}`,
          action: () => {
            nav(navItem.path);
            onClose();
          },
        });
      }
    });

    return list;
  }, [query, commands, nav, onClose, onInspectTicker, onPreviewReport, onRunCommand]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => (prev + 1) % Math.max(1, items.length));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) => (prev - 1 + items.length) % Math.max(1, items.length));
    } else if (e.key === "Enter" && items[selectedIndex]) {
      e.preventDefault();
      items[selectedIndex].action();
    }
  };

  const grouped = useMemo(() => {
    const map = new Map<string, { item: CommandItem; globalIdx: number }[]>();
    items.forEach((item, idx) => {
      if (!map.has(item.category)) {
        map.set(item.category, []);
      }
      map.get(item.category)!.push({ item, globalIdx: idx });
    });
    return map;
  }, [items]);

  const handleCategoryClick = (categoryName: string) => {
    if (categoryName === "Testing & Validation") {
      const match = commands.find((c) => c.name.includes("harness") || c.name.includes("preflight") || c.name.includes("pytest"));
      if (match) {
        setQuery(match.name + " ");
      }
    } else if (categoryName === "Pipeline & Core") {
      const match = commands.find((c) => c.name.includes("main") || c.name.includes("orchestrator"));
      if (match) {
        setQuery(match.name + " ");
      }
    }
  };

  if (!isOpen) return null;

  return (
    <Modal ariaLabel="Universal Omni-Search" onClose={onClose}>
      <div onKeyDown={handleKeyDown} data-testid="command-palette-modal" style={{ width: "min(90vw, 680px)" }}>
        <div style={{ display: "flex", gap: "var(--s-2)", marginBottom: "var(--s-3)" }}>
          <input
            autoFocus
            type="text"
            data-testid="command-palette-input"
            placeholder="Search commands, tickers (NVDA), reports, screens..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{
              flex: 1,
              background: "var(--surface)",
              color: "var(--text-primary)",
              border: "1px solid var(--border)",
              borderRadius: "var(--r-sm)",
              padding: "var(--s-3) var(--s-4)",
              fontSize: "var(--t-input)",
              outline: "none",
              boxSizing: "border-box",
            }}
          />
        </div>

        {/* Validation hints when resolving commands */}
        {hints.length > 0 && (
          <div style={{ marginBottom: "var(--s-3)" }}>
            {hints.map((h, i) => (
              <div key={i} style={{ fontSize: "var(--t-caption)", color: "var(--decline)", marginBottom: "4px" }}>
                ⚠️ {h.message}
              </div>
            ))}
          </div>
        )}

        {/* Category browse when input is empty */}
        {!query && (
          <div style={{ marginBottom: "var(--s-4)" }}>
            <div style={{ fontSize: "var(--t-micro)", fontWeight: 700, color: "var(--text-muted)", marginBottom: "var(--s-2)" }}>
              Browse by Category
            </div>
            <div style={{ display: "flex", gap: "var(--s-2)", flexWrap: "wrap" }}>
              <button className="btn" onClick={() => handleCategoryClick("Pipeline & Core")}>
                Pipeline & Core
              </button>
              <button className="btn" onClick={() => handleCategoryClick("Testing & Validation")}>
                Testing & Validation
              </button>
            </div>
          </div>
        )}

        {query && (
          <div style={{ fontSize: "var(--t-micro)", fontWeight: 700, color: "var(--text-muted)", marginBottom: "var(--s-2)" }}>
            Suggestions ({items.length})
          </div>
        )}

        <div
          style={{
            maxHeight: "340px",
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: "var(--s-3)",
            paddingRight: "var(--s-1)",
          }}
        >
          {items.length === 0 ? (
            <div className="empty" style={{ padding: "var(--s-4)" }}>
              No matching commands, tickers, or reports found.
            </div>
          ) : (
            Array.from(grouped.entries()).map(([cat, list]) => (
              <div key={cat}>
                <div
                  style={{
                    fontSize: "var(--t-micro)",
                    fontWeight: 700,
                    color: "var(--text-muted)",
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                    marginBottom: "var(--s-1)",
                  }}
                >
                  {cat === "⚡ Commands" ? "Testing & Validation / Pipeline & Core" : cat}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                  {list.map(({ item, globalIdx }) => {
                    const isSelected = globalIdx === selectedIndex;
                    return (
                      <div
                        key={item.id}
                        onClick={item.action}
                        onMouseEnter={() => setSelectedIndex(globalIdx)}
                        style={{
                          padding: "var(--s-2-5) var(--s-3)",
                          borderRadius: "var(--r-xs)",
                          background: isSelected ? "var(--surface-3)" : "transparent",
                          border: `1px solid ${isSelected ? "var(--border-strong)" : "transparent"}`,
                          cursor: "pointer",
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                        }}
                      >
                        <div>
                          <div style={{ fontWeight: 600, color: "var(--text-primary)", fontSize: "var(--t-callout)" }}>
                            {item.label}
                          </div>
                          {item.sublabel && (
                            <div style={{ color: "var(--text-muted)", fontSize: "var(--t-caption)", marginTop: "2px" }}>
                              {item.sublabel}
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </div>

        {/* Resolved Command Action Footer */}
        {resolvedSpec && (
          <div
            style={{
              marginTop: "var(--s-3)",
              paddingTop: "var(--s-3)",
              borderTop: "1px solid var(--border)",
              display: "flex",
              justifyContent: "flex-end",
              gap: "var(--s-2)",
            }}
          >
            {onSelectCommandForBuilder && (
              <button
                className="btn"
                onClick={() => {
                  onSelectCommandForBuilder(resolvedSpec);
                  onClose();
                }}
              >
                Configure in Form Builder
              </button>
            )}
            <button
              className="btn btn-primary"
              disabled={!isRunnable}
              onClick={() => {
                if (isRunnable && parsed.composed && onRunCommand) {
                  onRunCommand(parsed.composed, resolvedSpec, parsed.argTokens);
                  onClose();
                }
              }}
            >
              Run Command
            </button>
          </div>
        )}
      </div>
    </Modal>
  );
}

