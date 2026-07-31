import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { usePoll } from "../hooks/usePoll";
import type { CommandManifest, CommandSpec, CommandJobParams, JobRecord } from "../api/types";
import {
  parseCommandLine,
  highStakesReason,
  DISALLOWED_EXECUTE_COMMANDS,
  type Suggestion,
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
import { timeAgo } from "../format";
import { theme } from "../theme";

/**
 * Commands — an autocomplete command bar over the platform's CLI manifest
 * (GET /commands, built offline by scripts/build_command_manifest.py). It
 * resolves commands/subcommands + aliases, lists options with descriptions,
 * defaults and choices, and validates missing/unknown args before submit.
 *
 * Composing and copying the CLI string always works, with no gate. A Run
 * control additionally executes the composed command via the backend's
 * `"command"` job type on the existing job-execution infrastructure (the same
 * `POST /jobs` used by the Console screen's fixed one-click actions) — but
 * that path is disabled server-side unless the operator has explicitly set
 * `COMMAND_EXECUTION_ENABLED`, so a fresh/default deployment behaves exactly
 * as before (copy-only). High-stakes commands (the kill switch, a forced
 * broker re-login) require an extra confirmation dialog before the run
 * request is even sent (see `commandParse.ts`'s `highStakesReason`).
 * `app_shell.py` pops a native desktop window on the server host, not the
 * browser, so it stays copy-only regardless of the flag.
 */
export function Commands() {
  const { data, loading, error, status, stale, cachedAt, reload } =
    useApi<CommandManifest>(() => api.getCommands(), []);

  return (
    <div className="screen">
      <div className="rail-head">
        <h1>Commands</h1>
      </div>
      <p style={{ color: theme.textSecondary, marginTop: -4, marginBottom: "var(--s-4)" }}>
        Autocomplete for the platform's command-line tools. Compose a command,
        copy it to run in your own terminal, or run it here directly.
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
                    e.preventDefault(); // keep focus in the input
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
          <div style={{ marginTop: "var(--s-3)" }}>
            <RunCommandControl
              command={parsed.command}
              subcommand={parsed.subcommand}
              argTokens={parsed.argTokens}
              disabled={errors.length > 0}
              composed={parsed.composed}
              resetKey={input}
            />
          </div>
        </div>
      )}

      {/* Reference list when nothing typed yet */}
      {input.trim() === "" && (
        <div style={{ marginTop: "var(--s-6)" }}>
          <div className="tile-label" style={{ marginBottom: "var(--s-2)" }}>
            Available commands
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
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
                  padding: "var(--s-2-5) var(--s-3)",
                  background: theme.surface,
                  border: `1px solid ${theme.border}`,
                  borderRadius: "var(--r-sm)",
                  cursor: "pointer",
                }}
              >
                <div style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontWeight: 700, color: theme.textPrimary }}>
                  {c.name}
                </div>
                {c.description && (
                  <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>{c.description}</div>
                )}
                <div style={{ color: theme.textSecondary, fontSize: "var(--t-micro)", marginTop: "var(--s-0-5)" }}>{c.invocation}</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// Mirrors Console.tsx's own TERMINAL_STATUSES (not exported there) -- the set
// of JobRecord.status values past which polling stops.
const TERMINAL_STATUSES = new Set(["success", "failed", "cancelled", "unknown"]);

/**
 * RunCommandControl — executes the command bar's composed command via the
 * backend's gated `"command"` job type, reusing the same job-lifecycle
 * pattern as Console.tsx (createJob -> poll getJobStatus -> LogStream, with a
 * Cancel button while cancellable). A high-stakes command (kill switch
 * activate/deactivate, a forced Robinhood re-login) requires the operator to
 * confirm via a Modal before the run request is ever sent.
 */
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
  const [error, setError] = useState<string | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState(false);

  // A different composed command (the operator edited the bar) invalidates
  // whatever job/error/confirmation state belonged to the previous one --
  // otherwise a stale "success" badge or log stream could linger next to an
  // unrelated command.
  useEffect(() => {
    setActiveJob(null);
    setError(null);
    setPendingConfirm(false);
  }, [resetKey]);

  usePoll(
    async () => {
      if (!activeJob) return;
      try {
        setActiveJob(await api.getJobStatus(activeJob.job_id));
      } catch {
        // A transient poll failure isn't fatal -- just try again next tick.
      }
    },
    1500,
    Boolean(activeJob) && !TERMINAL_STATUSES.has(activeJob?.status ?? "")
  );

  const runCommand = async () => {
    try {
      const params: CommandJobParams = {
        command: command!.name,
        subcommand: subcommand?.name ?? null,
        args: argTokens,
        confirm: true,
      };
      // Spread into a fresh object literal: api.createJob's `params` is typed
      // Record<string, unknown> (the same untyped bag every other job type
      // shares), and CommandJobParams (no index signature) isn't directly
      // assignable to that -- but a fresh literal built from it is exempt
      // from the index-signature check the same way any inline literal is.
      const job = await api.createJob("command", { ...params });
      setActiveJob(job);
      setError(null);
    } catch (err: any) {
      setError(err?.message ?? String(err));
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
        setActiveJob(await api.getJobStatus(activeJob.job_id));
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
