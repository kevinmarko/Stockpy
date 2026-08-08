import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { JobRecord, PromptBody, PromptEntry, PromptListResponse } from "../api/types";
import { useApi } from "../hooks/useApi";
import { Button, EmptyState, ErrorState, Loading, Notice, Select, Table } from "../components/ui";
import { Modal } from "../components/Modal";
import { TabGuide } from "../components/TabGuide";
import { theme } from "../theme";
import { DynamicGrid, resetGridLayout } from "../components/DynamicGrid";

/**
 * Prompt Registry — version control for every AI-facing instruction. Ports
 * `gui/panels/prompt_registry.py`'s registered-prompts table, per-ID resolved-
 * body viewer, unified-diff viewer, and pin/clear-pin control to the PWA.
 *
 * An `.env`-write-adjacent surface (the pin control persists to
 * PROMPT_REGISTRY_PINS), so it lives under /settings — reached from the
 * "Prompt Registry" link card on Settings.tsx — rather than top-level nav.
 *
 * `sync`/`verify`/`rollback`/`diff` at the manifest/CLI level are deliberately
 * NOT new endpoints: `sync` here drives the SAME `POST /jobs
 * {job_type: "command"}` path Commands.tsx uses for
 * `python -m prompt_registry sync`; a version diff is computed client-side
 * from two `GET /prompts/{id}?version=` fetches (no server-side diff route).
 *
 * Security banner is mandatory, mirroring the Streamlit tab exactly: the
 * registry only ever changes what the AI is TOLD, never what the platform is
 * PERMITTED to do — order submission, the advisory quarantine, the risk gate,
 * and the kill switch stay enforced in Python regardless of registry content.
 */
export function PromptRegistry() {
  const nav = useNavigate();
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/settings"));
  const { data, loading, error, status, reload } = useApi<PromptListResponse>(
    () => api.getPrompts(),
    [],
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <div className="screen" style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--s-3)" }}>
        <div>
          <button
            onClick={back}
            style={{ background: "none", padding: 0, cursor: "pointer", color: "var(--text-secondary)", fontSize: "var(--t-callout)", marginBottom: "var(--s-2)", border: "none" }}
          >
            ← Settings
          </button>
          <div style={{ display: "flex", alignItems: "baseline", gap: "var(--s-2)" }}>
            <h1 className="screen-title" style={{ margin: 0 }}>Prompt Registry</h1>
          </div>
        </div>
        <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-4)", alignItems: "center" }}>
          <button type="button" className="btn btn-neutral" onClick={() => resetGridLayout("prompt-registry")}>
            Reset Layout
          </button>
        </div>
      </div>
      <p className="screen-sub">
        Version control for every AI-facing instruction — resolved version, source,
        and pin state for each registered prompt.
      </p>

      <TabGuide tabKey="prompts" />
      <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column" }}>
        {loading && <Loading lines={4} />}
        {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
        {!loading && !error && data && (
          <div style={{ flex: 1, minHeight: 0 }}>
            <DynamicGrid
              layoutKey="prompt-registry"
              defaultLayouts={{
                lg: [
                  { i: "notices", x: 0, y: 0, w: 12, h: data.enabled ? 3 : 4, isResizable: false },
                  { i: "sync", x: 0, y: 4, w: 12, h: 2, isResizable: false },
                  { i: "table", x: 0, y: 6, w: 12, h: 14 }
                ]
              }}
            >
              <div key="notices" className="card card-pad drag-handle" style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)", overflow: "auto", cursor: "grab" }}>
                <Notice variant="info" data-testid="prompt-security-banner">
                  <span aria-hidden>🛡️</span>
                  <span>
                    Prompts are advisory text. The registry changes what the AI is <em>told</em> —
                    it cannot change what the platform is <em>permitted to do</em>. Order submission,
                    the advisory quarantine, the risk gate, and the kill switch are enforced in
                    Python and are not registry-controlled.
                  </span>
                </Notice>
                {!data.enabled && (
                  <Notice variant="warn" data-testid="prompt-registry-disabled-notice">
                    <span aria-hidden>📦</span>
                    <span>
                      Registry is disabled (<code>PROMPT_REGISTRY_ENABLED=false</code>). All
                      prompts resolve from the committed baseline — zero network calls.
                    </span>
                  </Notice>
                )}
              </div>

              <div key="sync" className="card card-pad drag-handle" style={{ display: "flex", alignItems: "center", cursor: "grab", overflow: "hidden" }}>
                <div onMouseDown={(e) => e.stopPropagation()} onTouchStart={(e) => e.stopPropagation()}>
                  <SyncNowControl registryEnabled={data.enabled} onSynced={reload} />
                </div>
              </div>

              <div key="table" className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden", padding: 0 }}>
                <div className="drag-handle" style={{ padding: "var(--s-3)", fontWeight: 600, borderBottom: "1px solid var(--border)", cursor: "grab" }}>Registered Prompts</div>
                <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
                  {data.prompts.length === 0 ? (
                    <EmptyState
                      title="No prompt IDs found"
                      hint={data.reason ?? "Run a sync or check that prompt_registry/baseline/ is intact."}
                    />
                  ) : (
                    <Table>
                      <thead>
                        <tr>
                          <th>Prompt ID</th>
                          <th>Resolved version</th>
                          <th>Source</th>
                          <th>Pinned</th>
                          <th className="num">Cached</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.prompts.map((p) => (
                          <PromptRow key={p.id} entry={p} onSelect={() => setSelectedId(p.id)} />
                        ))}
                      </tbody>
                    </Table>
                  )}
                </div>
              </div>
            </DynamicGrid>
          </div>
        )}
      </div>
      {selectedId && data && (
        <PromptDetailModal
          id={selectedId}
          writable={data.writable}
          writableNote={data.note}
          onClose={() => setSelectedId(null)}
          onPinChanged={reload}
        />
      )}
    </div>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  pin: "📌 pin",
  remote: "🌐 remote",
  cache: "💾 cache",
  baseline: "📦 baseline",
};

function sourceLabel(source: string | null): string {
  if (!source) return "—";
  return SOURCE_LABEL[source] ?? source;
}

function PromptRow({ entry, onSelect }: { entry: PromptEntry; onSelect: () => void }) {
  return (
    <tr
      data-testid={`prompt-row-${entry.id}`}
      onClick={onSelect}
      style={{ cursor: "pointer" }}
    >
      <td style={{ fontWeight: 600, color: theme.textPrimary }}>{entry.id}</td>
      <td>{entry.resolved_version ?? "—"}</td>
      <td>{sourceLabel(entry.source)}</td>
      <td>{entry.pinned_version ?? "—"}</td>
      <td className="num">{entry.cached_version_count}</td>
    </tr>
  );
}

const TERMINAL_JOB_STATUSES = new Set(["success", "failed", "cancelled", "unknown"]);
const SYNC_POLL_MS = 750;
const SYNC_POLL_MAX_ATTEMPTS = 40; // ~30s cap so a stuck job can't hang the UI forever

function sleep(ms: number): Promise<void> {
  return new Promise((res) => setTimeout(res, ms));
}

/**
 * "Sync prompts" — drives the SAME gated `POST /jobs {job_type: "command"}`
 * path Commands.tsx uses for `python -m prompt_registry sync`, rather than a
 * bespoke sync endpoint (there isn't one — see this screen's docstring).
 * Polls job status to a terminal state, then reloads the prompt list so a
 * successful sync's newly-fetched remote versions actually show up.
 */
function SyncNowControl({
  registryEnabled,
  onSynced,
}: {
  registryEnabled: boolean;
  onSynced: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const run = async () => {
    setPending(true);
    setMessage(null);
    setFailed(false);
    try {
      let job: JobRecord = await api.createJob("command", {
        command: "prompt_registry",
        subcommand: "sync",
        args: [],
        confirm: false,
      });
      for (
        let attempt = 0;
        attempt < SYNC_POLL_MAX_ATTEMPTS && !TERMINAL_JOB_STATUSES.has(job.status);
        attempt++
      ) {
        await sleep(SYNC_POLL_MS);
        job = await api.getJobStatus(job.job_id);
      }
      if (job.status === "success") {
        setMessage("Sync complete.");
        onSynced();
      } else if (TERMINAL_JOB_STATUSES.has(job.status)) {
        setFailed(true);
        setMessage(`Sync ${job.status} — check server logs for details.`);
      } else {
        setFailed(true);
        setMessage("Sync is still running — check back later.");
      }
    } catch (err: unknown) {
      setFailed(true);
      setMessage(err instanceof Error ? err.message : "Sync failed.");
    } finally {
      setPending(false);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--s-2)",
        flexWrap: "wrap",
        marginBottom: "var(--s-2)",
      }}
    >
      <Button onClick={run} pending={pending} disabled={!registryEnabled} data-testid="prompt-sync-now">
        🔄 Sync prompts
      </Button>
      {!registryEnabled && (
        <span style={{ fontSize: "var(--t-caption)", color: theme.textMuted }}>
          Enable PROMPT_REGISTRY_ENABLED to sync.
        </span>
      )}
      {message && (
        <span
          data-testid="prompt-sync-message"
          style={{ fontSize: "var(--t-caption)", color: failed ? theme.decline : theme.growth }}
        >
          {message}
        </span>
      )}
    </div>
  );
}

type DiffLine = { type: "same" | "add" | "remove"; text: string };

/**
 * Classic LCS-based line diff — no diff library dependency exists in this
 * app, and a prompt body is small (a few hundred lines at most), so the
 * O(n*m) DP table is trivial cost. Not a generic library; scoped to exactly
 * this screen's need (two full-text version bodies -> a unified line list).
 */
function computeLineDiff(a: string, b: string): DiffLine[] {
  const linesA = a.split("\n");
  const linesB = b.split("\n");
  const n = linesA.length;
  const m = linesB.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] =
        linesA[i] === linesB[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const result: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (linesA[i] === linesB[j]) {
      result.push({ type: "same", text: linesA[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      result.push({ type: "remove", text: linesA[i] });
      i++;
    } else {
      result.push({ type: "add", text: linesB[j] });
      j++;
    }
  }
  while (i < n) {
    result.push({ type: "remove", text: linesA[i] });
    i++;
  }
  while (j < m) {
    result.push({ type: "add", text: linesB[j] });
    j++;
  }
  return result;
}

const DIFF_LINE_STYLE: Record<DiffLine["type"], { bg: string; prefix: string }> = {
  same: { bg: "transparent", prefix: "  " },
  add: { bg: "rgba(34, 197, 94, 0.12)", prefix: "+ " },
  remove: { bg: "rgba(239, 68, 68, 0.12)", prefix: "- " },
};

/**
 * Detail view for one prompt ID: view the resolved body, diff two versions,
 * and (when `writable`) pin to a specific version or clear an existing pin.
 * Fetches its own `GET /prompts/{id}` on mount for the fuller `cached_versions`/
 * `has_baseline` fields the list row doesn't carry.
 */
function PromptDetailModal({
  id,
  writable,
  writableNote,
  onClose,
  onPinChanged,
}: {
  id: string;
  writable: boolean;
  writableNote: string;
  onClose: () => void;
  onPinChanged: () => void;
}) {
  const { data, loading, error, status, reload } = useApi<PromptBody>(() => api.getPrompt(id), [id]);

  const versionChoices = useMemo(() => {
    if (!data) return [];
    const choices = [...data.cached_versions];
    if (data.has_baseline && !choices.includes("baseline")) choices.push("baseline");
    return choices;
  }, [data]);

  return (
    <Modal ariaLabel={`Prompt ${id}`} onClose={onClose}>
      <div style={{ padding: "var(--s-3)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-2)" }}>
          <h2 style={{ fontSize: "var(--t-title)", margin: 0 }}>{id}</h2>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{ background: "none", border: "none", cursor: "pointer", fontSize: "var(--t-title)", color: theme.textMuted }}
          >
            ×
          </button>
        </div>

        {loading && <Loading lines={3} />}
        {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
        {!loading && !error && data && (
          <>
            {!data.found ? (
              <EmptyState title="Prompt unavailable" hint={data.reason ?? undefined} />
            ) : (
              <ResolvedBodySection body={data.body ?? ""} version={data.version} source={data.source} />
            )}

            {versionChoices.length >= 2 && <DiffSection id={id} versionChoices={versionChoices} />}

            <PinSection
              id={id}
              writable={writable}
              writableNote={writableNote}
              versionChoices={versionChoices}
              currentBodyVersion={data.found ? data.version : null}
              onPinChanged={() => {
                onPinChanged();
                reload();
              }}
            />
          </>
        )}
      </div>
    </Modal>
  );
}

function ResolvedBodySection({
  body,
  version,
  source,
}: {
  body: string;
  version: string | null;
  source: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <section style={{ marginBottom: "var(--s-3)" }}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        data-testid="prompt-view-resolved-toggle"
        style={{
          background: "none",
          border: `1px solid ${theme.border}`,
          borderRadius: 8,
          padding: "var(--s-1-5) var(--s-2)",
          cursor: "pointer",
          width: "100%",
          textAlign: "left",
          color: theme.textPrimary,
          fontSize: "var(--t-body)",
        }}
      >
        👁️ View resolved body · {sourceLabel(source)} · {version ?? "—"} {expanded ? "▲" : "▼"}
      </button>
      {expanded && (
        <pre
          data-testid="prompt-resolved-body"
          style={{
            marginTop: "var(--s-1-5)",
            padding: "var(--s-2)",
            background: theme.surface2,
            borderRadius: 8,
            fontSize: "var(--t-caption)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            maxHeight: 320,
            overflowY: "auto",
          }}
        >
          {body}
        </pre>
      )}
    </section>
  );
}

function DiffSection({ id, versionChoices }: { id: string; versionChoices: string[] }) {
  const [expanded, setExpanded] = useState(false);
  const [verA, setVerA] = useState(versionChoices[0]);
  const [verB, setVerB] = useState(versionChoices[Math.min(1, versionChoices.length - 1)]);
  const [diff, setDiff] = useState<DiffLine[] | null>(null);
  const [comparing, setComparing] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);

  useEffect(() => {
    setDiff(null);
    setCompareError(null);
  }, [verA, verB]);

  const compare = async () => {
    setComparing(true);
    setCompareError(null);
    try {
      const [a, b] = await Promise.all([api.getPrompt(id, verA), api.getPrompt(id, verB)]);
      if (!a.found || a.body == null) {
        setCompareError(`Version '${verA}' could not be resolved.`);
      } else if (!b.found || b.body == null) {
        setCompareError(`Version '${verB}' could not be resolved.`);
      } else {
        setDiff(computeLineDiff(a.body, b.body));
      }
    } catch (err: unknown) {
      setCompareError(err instanceof Error ? err.message : "Diff failed.");
    } finally {
      setComparing(false);
    }
  };

  return (
    <section style={{ marginBottom: "var(--s-3)" }}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        data-testid="prompt-diff-toggle"
        style={{
          background: "none",
          border: `1px solid ${theme.border}`,
          borderRadius: 8,
          padding: "var(--s-1-5) var(--s-2)",
          cursor: "pointer",
          width: "100%",
          textAlign: "left",
          color: theme.textPrimary,
          fontSize: "var(--t-body)",
        }}
      >
        🔍 Diff two versions {expanded ? "▲" : "▼"}
      </button>
      {expanded && (
        <div style={{ marginTop: "var(--s-1-5)" }}>
          <div style={{ display: "flex", gap: "var(--s-2)", marginBottom: "var(--s-2)" }}>
            <div style={{ flex: 1 }}>
              <Select
                label="Version A (from)"
                value={verA}
                onChange={(e) => setVerA(e.target.value)}
                options={versionChoices.map((v) => ({ value: v, label: v }))}
                testId="prompt-diff-version-a"
              />
            </div>
            <div style={{ flex: 1 }}>
              <Select
                label="Version B (to)"
                value={verB}
                onChange={(e) => setVerB(e.target.value)}
                options={versionChoices.map((v) => ({ value: v, label: v }))}
                testId="prompt-diff-version-b"
              />
            </div>
          </div>
          <Button onClick={compare} pending={comparing} data-testid="prompt-diff-compare">
            Compare
          </Button>
          {compareError && (
            <p style={{ color: theme.decline, fontSize: "var(--t-caption)", marginTop: "var(--s-1-5)" }}>
              {compareError}
            </p>
          )}
          {diff && (
            <pre
              data-testid="prompt-diff-output"
              style={{
                marginTop: "var(--s-2)",
                padding: "var(--s-2)",
                background: theme.surface2,
                borderRadius: 8,
                fontSize: "var(--t-caption)",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                maxHeight: 320,
                overflowY: "auto",
              }}
            >
              {diff.every((line) => line.type === "same") ? (
                "No differences between the two versions."
              ) : (
                diff.map((line, idx) => {
                  const style = DIFF_LINE_STYLE[line.type];
                  return (
                    <div key={idx} style={{ background: style.bg }}>
                      {style.prefix}
                      {line.text}
                    </div>
                  );
                })
              )}
            </pre>
          )}
        </div>
      )}
    </section>
  );
}

/**
 * Pin / clear-pin control — writable only when the server reports
 * `PromptListResponse.writable` (PROMPT_REGISTRY_WRITES_ENABLED), matching
 * StrategyMatrix's identical `writable`-gated pattern. Auto-rollback (pin to
 * the previous cached version) is not a separate action here — the operator
 * picks the desired older version from the same dropdown this control
 * already offers, which is exactly what a rollback IS.
 */
function PinSection({
  id,
  writable,
  writableNote,
  versionChoices,
  currentBodyVersion,
  onPinChanged,
}: {
  id: string;
  writable: boolean;
  writableNote: string;
  versionChoices: string[];
  currentBodyVersion: string | null;
  onPinChanged: () => void;
}) {
  const [target, setTarget] = useState(versionChoices[0] ?? "");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!target && versionChoices.length > 0) setTarget(versionChoices[0]);
  }, [versionChoices, target]);

  const setPin = async () => {
    setPending(true);
    setMessage(null);
    setFailed(false);
    try {
      const result = await api.putPromptPin({ prompt_id: id, version: target || null });
      setMessage(result.note);
      onPinChanged();
    } catch (err: unknown) {
      setFailed(true);
      setMessage(err instanceof Error ? err.message : "Pin failed.");
    } finally {
      setPending(false);
    }
  };

  const clearPin = async () => {
    setPending(true);
    setMessage(null);
    setFailed(false);
    try {
      const result = await api.putPromptPin({ prompt_id: id, version: null });
      setMessage(result.note);
      onPinChanged();
    } catch (err: unknown) {
      setFailed(true);
      setMessage(err instanceof Error ? err.message : "Clear pin failed.");
    } finally {
      setPending(false);
    }
  };

  return (
    <section>
      <h3 style={{ fontSize: "var(--t-body)", fontWeight: 700, margin: "0 0 var(--s-1-5)" }}>
        ↩ Pin / rollback
      </h3>
      <p style={{ fontSize: "var(--t-caption)", color: theme.textMuted, margin: "0 0 var(--s-2)" }}>
        Pins persist to <code>.env</code> and take effect on the next daemon restart —
        this process is never hot-swapped. {writableNote}
      </p>
      {!writable ? (
        <Notice variant="info" data-testid="prompt-pin-disabled-notice">
          <span>Pin writes are disabled server-side.</span>
        </Notice>
      ) : versionChoices.length === 0 ? (
        <p style={{ fontSize: "var(--t-caption)", color: theme.textMuted }}>
          No versions available to pin.
        </p>
      ) : (
        <div style={{ display: "flex", gap: "var(--s-2)", alignItems: "flex-end", flexWrap: "wrap" }}>
          <div style={{ flex: 1, minWidth: 160 }}>
            <Select
              label="Pin to version"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              options={versionChoices.map((v) => ({ value: v, label: v }))}
              testId="prompt-pin-target"
            />
          </div>
          <Button onClick={setPin} pending={pending} data-testid="prompt-pin-set">
            📌 Set pin
          </Button>
          {currentBodyVersion && (
            <Button variant="neutral" onClick={clearPin} pending={pending} data-testid="prompt-pin-clear">
              🗑️ Clear pin
            </Button>
          )}
        </div>
      )}
      {message && (
        <p
          data-testid="prompt-pin-message"
          style={{ marginTop: "var(--s-1-5)", fontSize: "var(--t-caption)", color: failed ? theme.decline : theme.growth }}
        >
          {message}
        </p>
      )}
    </section>
  );
}
