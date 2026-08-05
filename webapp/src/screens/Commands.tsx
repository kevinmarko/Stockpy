import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useSearchParams } from "react-router";
import toast from "react-hot-toast";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { usePoll } from "../hooks/usePoll";
import type { CommandManifest, CommandSpec, CommandJobParams, JobRecord } from "../api/types";
import {
  parseCommandLine,
  highStakesReason,
  DISALLOWED_EXECUTE_COMMANDS,
  getCommandCategory,
  CATEGORIES,
  type Suggestion,
  type CommandCategory,
} from "../commandParse";
import {
  Button,
  EmptyState,
  ErrorState,
  Loading,
  StaleDataNotice,
} from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { ExecutionQueueSection } from "../components/ExecutionQueueSection";
import { CopyCommandBlock } from "../components/CopyCommandBlock";
import { Modal } from "../components/Modal";
import { LogStream } from "../components/LogStream";
import { CommandFormBuilder } from "../components/CommandFormBuilder";
import { RecentRunsLog } from "../components/RecentRunsLog";
import { timeAgo } from "../format";
import { theme } from "../theme";

const LOCAL_STORAGE_FAVORITES_KEY = "investyo_favorite_commands";

function getFavoriteCommands(): string[] {
  try {
    const raw = localStorage.getItem(LOCAL_STORAGE_FAVORITES_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function saveFavoriteCommands(favs: string[]) {
  try {
    localStorage.setItem(LOCAL_STORAGE_FAVORITES_KEY, JSON.stringify(favs));
  } catch {
    // ignore
  }
}

export function Commands() {
  const { data, loading, error, status, stale, cachedAt, reload } =
    useApi<CommandManifest>(() => api.getCommands(), []);

  const [searchParams, setSearchParams] = useSearchParams();
  const [activeTab, setActiveTab] = useState<"launcher" | "queue">("launcher");
  const [builderCommand, setBuilderCommand] = useState<CommandSpec | null>(null);

  // Check URL query parameters for builderCommand trigger (e.g. ?builder=validation.harness)
  useEffect(() => {
    const builderParam = searchParams.get("builder");
    if (builderParam && data?.commands) {
      const matched = data.commands.find((c) => c.name === builderParam);
      if (matched) {
        setBuilderCommand(matched);
      }
    }
  }, [searchParams, data]);

  return (
    <div className="screen">
      <div className="rail-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>Commands</h1>
        {/* Navigation Sub-Tabs */}
        <div style={{ display: "flex", gap: "var(--s-1)", background: theme.surface2, padding: "var(--s-1)", borderRadius: "var(--r-sm)" }}>
          <button
            onClick={() => setActiveTab("launcher")}
            style={{
              padding: "var(--s-1-5) var(--s-3)",
              borderRadius: "var(--r-sm)",
              border: "none",
              background: activeTab === "launcher" ? theme.surface : "transparent",
              color: activeTab === "launcher" ? theme.textPrimary : theme.textMuted,
              fontWeight: activeTab === "launcher" ? 600 : 400,
              cursor: "pointer",
              fontSize: "var(--t-body)",
            }}
          >
            💻 Command Launcher
          </button>
          <button
            onClick={() => setActiveTab("queue")}
            style={{
              padding: "var(--s-1-5) var(--s-3)",
              borderRadius: "var(--r-sm)",
              border: "none",
              background: activeTab === "queue" ? theme.surface : "transparent",
              color: activeTab === "queue" ? theme.textPrimary : theme.textMuted,
              fontWeight: activeTab === "queue" ? 600 : 400,
              cursor: "pointer",
              fontSize: "var(--t-body)",
            }}
          >
            📋 Staged Execution Queue
          </button>
        </div>
      </div>

      <p style={{ color: theme.textSecondary, marginTop: -4, marginBottom: "var(--s-4)" }}>
        Autocomplete and parameter builder for the platform's command-line tools. Compose commands,
        configure flags via Form Mode, or trigger global Command Palette with <kbd style={{ background: theme.surface3, padding: "2px 6px", borderRadius: 4 }}>Cmd + K</kbd>.
        {data && ` Manifest generated ${timeAgo(data.generated_at)}.`}
      </p>

      <TabGuide tabKey="commands" />

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
          <>
            {activeTab === "launcher" && (
              <CommandLauncher
                commands={data.commands}
                onOpenBuilder={(cmd) => setBuilderCommand(cmd)}
              />
            )}
            {(activeTab === "queue" || activeTab === "launcher") && (
              <div style={{ marginTop: activeTab === "launcher" ? "var(--s-6)" : 0 }}>
                <ExecutionQueueSection />
              </div>
            )}
          </>
        )
      )}

      {/* Form Mode Builder Modal */}
      {builderCommand && (
        <CommandFormBuilder
          command={builderCommand}
          onClose={() => {
            setBuilderCommand(null);
            if (searchParams.has("builder")) {
              searchParams.delete("builder");
              setSearchParams(searchParams);
            }
          }}
          onRunCommand={(_composed, _spec, _argTokens) => {
            // Managed inside CommandLauncher via job creation
          }}
        />
      )}
    </div>
  );
}

function CommandLauncher({
  commands,
  onOpenBuilder,
}: {
  commands: CommandManifest["commands"];
  onOpenBuilder: (cmd: CommandSpec) => void;
}) {
  const [selectedCategory, setSelectedCategory] = useState<CommandCategory | "all">("all");
  const [favorites, setFavorites] = useState<string[]>(() => getFavoriteCommands());

  const toggleFavorite = (name: string) => {
    setFavorites((prev) => {
      const next = prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name];
      saveFavoriteCommands(next);
      return next;
    });
  };

  const filteredCommands = useMemo(() => {
    if (selectedCategory === "all") return commands;
    return commands.filter((c) => getCommandCategory(c.name) === selectedCategory);
  }, [commands, selectedCategory]);

  return (
    <div>
      {/* Autocomplete Input Bar */}
      <CommandBar commands={commands} onOpenBuilder={onOpenBuilder} />

      {/* Category Badges Filter */}
      <div style={{ display: "flex", gap: "var(--s-2)", margin: "var(--s-5) 0 var(--s-3)", flexWrap: "wrap" }}>
        <button
          onClick={() => setSelectedCategory("all")}
          style={{
            padding: "var(--s-1-5) var(--s-3)",
            borderRadius: "var(--r-sm)",
            border: `1px solid ${selectedCategory === "all" ? theme.accent : theme.border}`,
            background: selectedCategory === "all" ? theme.surface3 : theme.surface,
            color: selectedCategory === "all" ? theme.accent : theme.textSecondary,
            fontWeight: 600,
            cursor: "pointer",
            fontSize: "var(--t-caption)",
          }}
        >
          All Commands ({commands.length})
        </button>

        {CATEGORIES.map((cat) => {
          const count = commands.filter((c) => getCommandCategory(c.name) === cat.id).length;
          const isSelected = selectedCategory === cat.id;
          return (
            <button
              key={cat.id}
              onClick={() => setSelectedCategory(cat.id)}
              style={{
                padding: "var(--s-1-5) var(--s-3)",
                borderRadius: "var(--r-sm)",
                border: `1px solid ${isSelected ? theme.accent : theme.border}`,
                background: isSelected ? theme.surface3 : theme.surface,
                color: isSelected ? theme.accent : theme.textSecondary,
                fontWeight: 600,
                cursor: "pointer",
                fontSize: "var(--t-caption)",
              }}
            >
              {cat.icon} {cat.label} ({count})
            </button>
          );
        })}
      </div>

      {/* Command Cards Grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: "var(--s-3)", marginBottom: "var(--s-6)" }}>
        {filteredCommands.map((c) => {
          const isFav = favorites.includes(c.name);
          const cat = CATEGORIES.find((item) => item.id === getCommandCategory(c.name));
          return (
            <div
              key={c.name}
              style={{
                background: theme.surface,
                border: `1px solid ${theme.border}`,
                borderRadius: "var(--r-sm)",
                padding: "var(--s-3-5)",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                position: "relative",
              }}
            >
              <div>
                {/* Header row with icon & star */}
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--s-1-5)" }}>
                  <span style={{ fontSize: "var(--t-micro)", padding: "2px 6px", borderRadius: 4, background: theme.surface2, color: theme.textMuted }}>
                    {cat?.icon} {cat?.label}
                  </span>
                  <button
                    onClick={() => toggleFavorite(c.name)}
                    aria-label={`Favorite ${c.name}`}
                    style={{
                      background: "none",
                      border: "none",
                      cursor: "pointer",
                      fontSize: "1.1rem",
                      color: isFav ? "#facc15" : theme.textMuted,
                    }}
                  >
                    {isFav ? "★" : "☆"}
                  </button>
                </div>

                <div style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontWeight: 700, fontSize: "var(--t-body)", color: theme.textPrimary }}>
                  {c.name}
                </div>

                {c.description && (
                  <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-1)", lineHeight: "1.4" }}>
                    {c.description}
                  </div>
                )}
                <div
                  style={{
                    color: theme.textSecondary,
                    fontSize: "var(--t-micro)",
                    fontFamily: "var(--font-mono, ui-monospace, monospace)",
                    marginTop: "var(--s-2)",
                    background: theme.surface2,
                    padding: "var(--s-1) var(--s-2)",
                    borderRadius: 4,
                    overflowX: "auto",
                  }}
                >
                  {c.invocation}
                </div>
              </div>

              {/* Action Buttons */}
              <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-3)", paddingTop: "var(--s-2)", borderTop: `1px solid ${theme.border}` }}>
                <Button
                  variant="neutral"
                  onClick={() => onOpenBuilder(c)}
                  aria-label={`Configure ${c.name}`}
                  style={{ flex: 1, fontSize: "var(--t-caption)" }}
                >
                  🛠️ Configure
                </Button>
                <Button
                  variant="neutral"
                  onClick={() => {
                    navigator.clipboard.writeText(c.invocation);
                  }}
                  aria-label={`Copy ${c.name}`}
                  style={{ fontSize: "var(--t-caption)" }}
                >
                  📋
                </Button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CommandBar({
  commands,
  onOpenBuilder,
}: {
  commands: CommandManifest["commands"];
  onOpenBuilder: (cmd: CommandSpec) => void;
}) {
  const [input, setInput] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [open, setOpen] = useState(true);
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
              margin: "var(--s-1) 0 0",
              padding: "var(--s-1)",
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
                    e.preventDefault();
                    accept(s);
                  }}
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    gap: "var(--s-2-5)",
                    padding: "var(--s-2) var(--s-2-5)",
                    borderRadius: "var(--r-sm)",
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
                    <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>{s.description}</span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Validation hints */}
      {parsed.hints.length > 0 && (
        <ul data-testid="command-hints" style={{ listStyle: "none", padding: 0, margin: "var(--s-2-5) 0 0" }}>
          {parsed.hints.map((h, i) => (
            <li
              key={i}
              style={{
                color: h.level === "error" ? theme.decline : theme.caution,
                fontSize: "var(--t-body)",
                marginTop: "var(--s-1)",
              }}
            >
              {h.level === "error" ? "✗" : "!"} {h.message}
            </li>
          ))}
        </ul>
      )}

      {/* Composed command + copy */}
      {parsed.composed && (
        <div style={{ marginTop: "var(--s-4)" }}>
          <CopyCommandBlock
            command={parsed.composed}
            label={`Command to run${errors.length ? " (incomplete — see above)" : ""}`}
            resetKey={input}
          />
          <div style={{ marginTop: "var(--s-3)", display: "flex", gap: "var(--s-2)", alignItems: "center" }}>
            <RunCommandControl
              command={parsed.command}
              subcommand={parsed.subcommand}
              argTokens={parsed.argTokens}
              disabled={errors.length > 0}
              composed={parsed.composed}
              resetKey={input}
            />
            {parsed.command && (
              <Button variant="neutral" onClick={() => onOpenBuilder(parsed.command!)}>
                Configure in Form Mode 🛠️
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const TERMINAL_STATUSES = new Set(["success", "failed", "cancelled", "unknown"]);

function RunCommandControl({
  command,
  subcommand,
  argTokens,
  disabled,
  composed,
  resetKey,
}: {
  command: CommandSpec | null;
  subcommand: CommandSpec | null;
  argTokens: string[];
  disabled: boolean;
  composed: string;
  resetKey: unknown;
}) {
  const [activeJob, setActiveJob] = useState<JobRecord | null>(null);
  const [recentJobs, setRecentJobs] = useState<JobRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState(false);

  useEffect(() => {
    setActiveJob(null);
    setError(null);
    setPendingConfirm(false);
  }, [resetKey]);

  usePoll(
    async () => {
      if (!activeJob) return;
      try {
        const updated = await api.getJobStatus(activeJob.job_id);
        setActiveJob(updated);
        setRecentJobs((prev) => {
          const idx = prev.findIndex((j) => j.job_id === updated.job_id);
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = updated;
            return next;
          }
          return [updated, ...prev];
        });
      } catch {
        // ignore transient poll failure
      }
    },
    1500,
    Boolean(activeJob) && !TERMINAL_STATUSES.has(activeJob?.status ?? "")
  );

  const runCommand = async () => {
    // The compact "Job {id} — {status}" line below (plus LogStream/
    // RecentRunsLog) is a persistent panel, but it only helps an operator
    // who's still looking at this exact spot on the screen -- mirrors
    // Console.tsx's own QUICK_ACTIONS launcher, which toasts launch
    // success/failure in addition to its own always-visible job table for
    // the same reason.
    const label = subcommand ? `${command!.name} ${subcommand.name}` : command!.name;
    try {
      const params: CommandJobParams = {
        command: command!.name,
        subcommand: subcommand?.name ?? null,
        args: argTokens,
        confirm: true,
      };
      const job = await api.createJob("command", { ...params });
      setActiveJob(job);
      setRecentJobs((prev) => [job, ...prev]);
      setError(null);
      toast.success(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Command launched</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
            {label} — job {job.job_id}
          </span>
        </div>
      );
    } catch (err: any) {
      const message = err?.message ?? String(err);
      setError(message);
      toast.error(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>{label} failed to launch</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
            {message}
          </span>
        </div>
      );
    }
  };

  const handleRunClick = () => {
    const reason = highStakesReason(command, argTokens);
    if (reason) {
      setPendingConfirm(true);
    } else {
      void runCommand();
    }
  };

  const handleCancel = async () => {
    if (!activeJob) return;
    try {
      const res = await api.cancelJob(activeJob.job_id);
      if (res.cancelled) {
        const updated = await api.getJobStatus(activeJob.job_id);
        setActiveJob(updated);
      } else {
        setError("Cancel was requested but could not be confirmed — the job may still be running.");
      }
    } catch (err: any) {
      setError(err?.message ?? String(err));
    }
  };

  if (!command) return null;

  if (DISALLOWED_EXECUTE_COMMANDS.has(command.name)) {
    return (
      <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }} data-testid="command-run-disallowed">
        {command.name} opens a native desktop window on the server — copy the command above and run it locally.
      </div>
    );
  }

  const reason = highStakesReason(command, argTokens);

  return (
    <div>
      <Button onClick={handleRunClick} disabled={disabled} data-testid="command-run-button">
        Run
      </Button>

      {error && (
        <div style={{ color: theme.decline, fontSize: "var(--t-body)", marginTop: "var(--s-2)" }} data-testid="command-run-error">
          {error}
        </div>
      )}

      {activeJob && (
        <div style={{ marginTop: "var(--s-3)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2-5)" }}>
            <span style={{ color: theme.textSecondary, fontSize: "var(--t-caption)" }} data-testid="command-run-status">
              Job {activeJob.job_id} — {activeJob.status}
            </span>
            {activeJob.cancellable && activeJob.is_running !== false && (
              <Button variant="neutral" onClick={handleCancel} data-testid="command-run-cancel">
                Cancel
              </Button>
            )}
          </div>
          <div style={{ marginTop: "var(--s-2-5)" }}>
            <LogStream jobId={activeJob.job_id} isStreaming={Boolean(activeJob)} />
          </div>
        </div>
      )}

      {recentJobs.length > 0 && (
        <div style={{ marginTop: "var(--s-5)" }}>
          <RecentRunsLog jobs={recentJobs} />
        </div>
      )}

      {pendingConfirm && (
        <Modal ariaLabel="Confirm command" onClose={() => setPendingConfirm(false)}>
          <div data-testid="command-confirm">
            <div className="tile-label" style={{ marginBottom: "var(--s-2)" }}>
              Confirm command
            </div>
            <p style={{ color: theme.textSecondary, marginTop: 0 }}>{reason}</p>
            <code
              style={{
                display: "block",
                padding: "var(--s-2-5) var(--s-3)",
                background: theme.surface,
                border: `1px solid ${theme.border}`,
                borderRadius: "var(--r-sm)",
                fontFamily: "var(--font-mono, ui-monospace, monospace)",
                color: theme.textPrimary,
                overflowX: "auto",
                whiteSpace: "pre",
              }}
            >
              {composed}
            </code>
            <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-4)" }}>
              <Button
                variant="neutral"
                onClick={() => setPendingConfirm(false)}
                data-testid="command-confirm-cancel"
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={() => {
                  setPendingConfirm(false);
                  void runCommand();
                }}
                data-testid="command-confirm-yes"
              >
                Yes, run it
              </Button>
            </div>
          </div>
        </Modal>
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
