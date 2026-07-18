import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { usePoll } from "../hooks/usePoll";
import type {
  AgenticDiscovery,
  AgenticStatus,
  DecisionEntry,
  DiscoveryCandidate,
} from "../api/types";
import {
  Button,
  EmptyState,
  ErrorState,
  Input,
  Loading,
  StaleDataNotice,
} from "../components/ui";
import { Chip, ExecutionQueueSection, ModeBadge } from "../components/ExecutionQueueSection";
import { CopyCommandBlock } from "../components/CopyCommandBlock";
import { DecisionModal } from "../components/DecisionModal";
import { Modal } from "../components/Modal";
import { TabGuide } from "../components/TabGuide";
import { Toggle } from "../components/Toggle";
import { theme } from "../theme";
import { timeAgo } from "../format";

/**
 * Agentic Trading — the consolidated command center for the platform's
 * Robinhood-backed agentic loop: Pilots follow/mirror, the gated dry-run
 * order queue, scan-based candidate discovery, and the decision journal.
 * All previously scattered across Commands, Settings, and AIControlCenter.
 *
 * This is a monitoring + gating surface, not an order-placement UI: no
 * control here ever places a real trade. Every write (execution mode,
 * pause/resume, scan config) hits an endpoint that was ALREADY gated
 * server-side before this screen existed — see ExecutionQueueSection's
 * docstring for why order placement itself is out of reach entirely.
 */
export function AgenticTrading() {
  const status = useApi<AgenticStatus>(() => api.getAgenticStatus(), []);
  // The queue/status can change without user action (a pipeline cycle, a
  // scan run) -- poll gently while the tab is open, mirroring Settings'
  // pipeline-status convention.
  usePoll(status.reload, 30_000, !status.loading);

  return (
    <div className="screen">
      <div className="rail-head">
        <h1>Agentic Trading</h1>
      </div>
      <p style={{ color: theme.textSecondary, marginTop: -4, marginBottom: 16 }}>
        What the agent is doing, what it's found, and the gated controls that
        drive it. Placing a real order always requires a separate,
        human-confirmed step in Claude Code — nothing on this screen does that.
      </p>

      <TabGuide tabKey="agentic" />

      {status.stale && <StaleDataNotice cachedAt={status.cachedAt} onRetry={status.reload} />}
      {status.loading && <Loading lines={3} />}
      {!status.loading && status.error && (
        <ErrorState message={status.error} status={status.status} onRetry={status.reload} />
      )}
      {!status.loading && !status.error && status.data && (
        <AgentStatusHeader data={status.data} onChanged={status.reload} />
      )}

      <DiscoverySection />

      <ExecutionQueueSection />

      <DecisionJournalSection />

      <ControlsSection status={status.data} onChanged={status.reload} />
    </div>
  );
}

function SectionCard({
  title,
  sub,
  children,
}: {
  title: string;
  sub?: string;
  children: ReactNode;
}) {
  return (
    <section className="card card-pad" style={{ marginTop: 16 }}>
      <h2 style={{ margin: "0 0 2px", fontSize: "var(--t-title)" }}>{title}</h2>
      {sub && (
        <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0, marginBottom: 12 }}>
          {sub}
        </p>
      )}
      {children}
    </section>
  );
}

function AgentStatusHeader({
  data,
  onChanged,
}: {
  data: AgenticStatus;
  onChanged: () => void;
}) {
  return (
    <SectionCard title="Agent status">
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <ModeBadge mode={data.mode} />
        {data.kill_switch.active && <Chip label="Kill switch ACTIVE" tone="decline" />}
        <Chip
          label={data.advisory_only ? "Advisory only" : "Live trading enabled"}
          tone={data.advisory_only ? "muted" : "caution"}
        />
        <Chip
          label={`${data.follows.n_active} active follow${data.follows.n_active === 1 ? "" : "s"}`}
          tone="muted"
        />
      </div>
      {data.kill_switch.active && data.kill_switch.reason && (
        <p style={{ color: theme.caution, fontSize: 13, marginTop: 0, marginBottom: 12 }}>
          Reason: {data.kill_switch.reason}
        </p>
      )}
      <div className="list">
        <StatRow
          label="Advisory-loop agent"
          value={
            data.agent_loop.reason
              ? data.agent_loop.reason
              : `${data.agent_loop.cycle_count} cycles — last ${
                  data.agent_loop.last_cycle_iso ? timeAgo(data.agent_loop.last_cycle_iso) : "—"
                }, ${data.agent_loop.backlog_count} unactioned backlog`
          }
        />
        <StatRow
          label="Execution queue"
          value={
            data.queue.generated_at
              ? `${data.queue.n_placeable}/${data.queue.n_intents} placeable — updated ${timeAgo(
                  data.queue.generated_at
                )}${data.queue.stale ? " (stale)" : ""}`
              : "No queue yet"
          }
        />
        <StatRow
          label="Pilot follows"
          value={
            data.follows.n_active === 0
              ? "None active"
              : `$${data.follows.total_amount.toLocaleString()} across ${data.follows.n_active}`
          }
        />
      </div>
      <div style={{ marginTop: 12 }}>
        <Button variant="neutral" onClick={onChanged}>
          Refresh
        </Button>
      </div>
    </SectionCard>
  );
}

function StatRow({ label, value }: { label: string; value: string }) {
  // NOT the shared .row/.row-end pattern -- that CSS hard-codes
  // `white-space: nowrap` on the value column (correct for its real callers'
  // short values like a price or a badge), which overlapped the label here
  // once the value became a full descriptive sentence. Stacked layout wraps
  // normally at any width instead.
  return (
    <div style={{ padding: "10px 0", borderBottom: `1px solid ${theme.border}` }}>
      <div style={{ fontWeight: 500, fontSize: 13, color: theme.textPrimary }}>{label}</div>
      <div style={{ color: theme.textSecondary, fontSize: 13, marginTop: 2 }}>{value}</div>
    </div>
  );
}

/**
 * The exact phrasing for a per-scan-config Claude Code invocation. The
 * agentic-discovery skill's documented procedure (.claude/skills/
 * agentic-discovery/SKILL.md) runs EVERY `enabled: true` scan config by
 * default — there is no native "just this one" mode — so this command must
 * explicitly scope to a single named config, or copying it would silently
 * kick off every other enabled scan too.
 */
function scanConfigCommand(scanName: string): string {
  return `Run the agentic-discovery skill for just the '${scanName}' scan config in output/scan_configs.json — don't run the other enabled scans.`;
}

function DiscoverySection() {
  const discovery = useApi<AgenticDiscovery>(() => api.getAgenticDiscovery(), []);
  const [adding, setAdding] = useState(false);

  return (
    <SectionCard
      title="Discovery"
      sub="Symbols surfaced by a Robinhood broker scan, cross-referenced against the platform's own advisory engine — run via the agentic-discovery skill in Claude Code, not automatically."
    >
      {discovery.stale && <StaleDataNotice cachedAt={discovery.cachedAt} onRetry={discovery.reload} />}
      {discovery.loading && <Loading lines={2} />}
      {!discovery.loading && discovery.error && (
        <ErrorState message={discovery.error} status={discovery.status} onRetry={discovery.reload} />
      )}
      {!discovery.loading && !discovery.error && discovery.data && (
        <>
          {/* Candidate-list freshness (backlog finding #5): the whole file is
              a single overwrite snapshot (pilots/discovery.py +
              .claude/skills/agentic-discovery's "overwrite, don't merge"
              contract — each run replaces the prior one, it never persists
              incrementally), so `generated_at` is an honest answer to "how
              stale is this list." Null means no scan has run yet — the empty
              state below already covers that, so this renders nothing rather
              than a fabricated "as of never" line. */}
          {discovery.data.generated_at && (
            <p style={{ color: theme.textMuted, fontSize: 12, marginTop: -6, marginBottom: 12 }}>
              As of {timeAgo(discovery.data.generated_at)}
            </p>
          )}
          {discovery.data.candidates.length === 0 ? (
            <EmptyState
              title="No candidates yet"
              hint={discovery.data.reason ?? "No scan has run yet."}
            />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 16 }}>
              {discovery.data.candidates.map((c) => (
                <CandidateRow key={c.symbol} c={c} />
              ))}
            </div>
          )}

          <div style={{ marginBottom: 8 }}>
            <div className="tile-label" style={{ marginBottom: 6 }}>
              Scan configs
            </div>
            {discovery.data.scan_configs.length === 0 ? (
              <p style={{ color: theme.textMuted, fontSize: 13 }}>None configured yet.</p>
            ) : (
              <>
                <p style={{ color: theme.textMuted, fontSize: 12, marginTop: 0, marginBottom: 10 }}>
                  Copy a command below into a separate Claude Code session to run just that scan —
                  nothing on this screen runs it for you.
                </p>
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {discovery.data.scan_configs.map((cfg) => (
                    <div key={cfg.name} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <Chip label={cfg.enabled ? "enabled" : "disabled"} tone={cfg.enabled ? "growth" : "muted"} />
                        <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontSize: 13 }}>
                          {cfg.name}
                        </span>
                      </div>
                      <CopyCommandBlock
                        command={scanConfigCommand(cfg.name)}
                        testIdPrefix={`scan-cmd-${cfg.name}`}
                      />
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>

          {discovery.data.writable ? (
            <Button variant="neutral" onClick={() => setAdding(true)}>
              Add scan config
            </Button>
          ) : (
            <p style={{ color: theme.textMuted, fontSize: 12 }}>{discovery.data.note}</p>
          )}

          {adding && (
            <ScanConfigModal
              onClose={() => setAdding(false)}
              onSaved={() => {
                setAdding(false);
                discovery.reload();
              }}
            />
          )}
        </>
      )}
    </SectionCard>
  );
}

/**
 * One discovered candidate: identity + advisory read (a Link to its symbol
 * page) plus a "Watch" action that appends it to watchlist.txt so the pipeline
 * starts evaluating it. The Watch button is a SIBLING of the Link, never nested
 * inside it (nested interactive elements are invalid/ a11y-hostile). The button
 * degrades honestly — a 409 (WATCHLIST env precedence) or 422 (bad symbol)
 * surfaces the server's message rather than a fake success.
 *
 * `discovered_at` renders per-row rather than relying solely on the section's
 * "as of" line: today's contract overwrites the whole candidate file on every
 * scan run, so a row's own timestamp is normally within seconds of the
 * section-level `generated_at` — but it's still each candidate's own field,
 * and stays honest (not a copy of `generated_at`) if discovery ever starts
 * persisting incrementally instead of overwriting.
 */
function CandidateRow({ c }: { c: DiscoveryCandidate }) {
  const watch = useMutation(() => api.watchCandidate(c.symbol));
  const [logging, setLogging] = useState(false);
  // A successful call that only reports `already_present` is not an error, but
  // it's also not a fresh add — reflect both honestly.
  const added = watch.result?.added.length ? watch.result.added : null;
  const alreadyWatching =
    watch.result != null && watch.result.added.length === 0 && watch.result.already_present.length > 0;

  return (
    <div
      data-testid="discovery-candidate-row"
      style={{
        padding: "10px 12px",
        background: theme.surface,
        border: `1px solid ${theme.border}`,
        borderRadius: 8,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Link
          to={`/symbol/${encodeURIComponent(c.symbol)}`}
          style={{ textDecoration: "none", flex: 1, minWidth: 0 }}
        >
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 700, color: theme.textPrimary }}>{c.symbol}</span>
            {c.action ? (
              <span
                style={{
                  color: c.action === "BUY" ? theme.growth : theme.decline,
                  fontWeight: 600,
                  fontSize: 12,
                }}
              >
                {c.action}
              </span>
            ) : (
              <span style={{ color: theme.textMuted, fontSize: 12 }}>not scored</span>
            )}
            {c.conviction !== null && (
              <span style={{ color: theme.textMuted, fontSize: 12 }}>
                conviction {(c.conviction * 100).toFixed(0)}%
              </span>
            )}
            {c.scan_name && (
              <span style={{ color: theme.textMuted, fontSize: 11 }}>{c.scan_name}</span>
            )}
            {c.discovered_at && (
              <span style={{ color: theme.textMuted, fontSize: 11 }}>
                discovered {timeAgo(c.discovered_at)}
              </span>
            )}
          </div>
        </Link>
        <Button
          variant="neutral"
          onClick={() => setLogging(true)}
          style={{ padding: "4px 10px", fontSize: 12 }}
        >
          Log
        </Button>
        {added || alreadyWatching ? (
          <span
            data-testid="watch-status"
            style={{ color: theme.textMuted, fontSize: 12, whiteSpace: "nowrap" }}
          >
            {added ? "✓ Watching" : "Already watching"}
          </span>
        ) : (
          <Button
            variant="neutral"
            onClick={() => watch.run()}
            pending={watch.pending}
            style={{ padding: "4px 10px", fontSize: 12 }}
          >
            Watch
          </Button>
        )}
      </div>
      {c.scan_reason && (
        <div style={{ color: theme.textSecondary, fontSize: 12, marginTop: 6 }}>{c.scan_reason}</div>
      )}
      {added && (
        <div style={{ color: theme.growth, fontSize: 12, marginTop: 6 }}>
          Added to your watchlist — the pipeline will evaluate it on the next run. No order was placed.
        </div>
      )}
      {watch.error && (
        <div style={{ color: theme.caution, fontSize: 12, marginTop: 6 }}>{watch.error}</div>
      )}
      {logging && (
        <DecisionModal
          signal={{ symbol: c.symbol, action: c.action, conviction: c.conviction }}
          onClose={() => setLogging(false)}
          onLogged={() => setLogging(false)}
        />
      )}
    </div>
  );
}

function ScanConfigModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [minPrice, setMinPrice] = useState("5");
  const [minVolume, setMinVolume] = useState("1000000");
  const mutation = useMutation(() =>
    api.putScanConfig({
      name: name.trim(),
      filters: { min_price: Number(minPrice), min_volume: Number(minVolume) },
      enabled: true,
    })
  );

  const submit = async () => {
    const r = await mutation.run();
    if (r) onSaved();
  };

  return (
    <Modal ariaLabel="Add scan config" onClose={onClose}>
      <h2 style={{ margin: "0 0 2px", fontSize: "var(--t-title)" }}>Add scan config</h2>
      <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0 }}>
        Saved to output/scan_configs.json. The agentic-discovery skill reads
        this the next time it runs — nothing runs automatically.
      </p>
      <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} hint="e.g. high_momentum_breakout" />
      <Input
        label="Min price"
        type="number"
        value={minPrice}
        onChange={(e) => setMinPrice(e.target.value)}
      />
      <Input
        label="Min volume"
        type="number"
        value={minVolume}
        onChange={(e) => setMinVolume(e.target.value)}
      />
      {mutation.error && (
        <div className="notice notice-warn" style={{ marginTop: 10 }}>
          <span>⚠️</span>
          <span>{mutation.error}</span>
        </div>
      )}
      <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
        <Button variant="neutral" onClick={onClose} style={{ flex: 1 }}>
          Cancel
        </Button>
        <Button
          variant="primary"
          onClick={submit}
          disabled={!name.trim()}
          pending={mutation.pending}
          style={{ flex: 2 }}
        >
          Save
        </Button>
      </div>
    </Modal>
  );
}

function DecisionJournalSection() {
  const decisions = useApi<DecisionEntry[]>(() => api.getDecisions({ limit: 10 }), []);

  return (
    <SectionCard title="Decision journal" sub="What you've actually done about recent recommendations, most recent first.">
      {decisions.loading && <Loading lines={2} />}
      {!decisions.loading && decisions.error && (
        <ErrorState message={decisions.error} status={decisions.status} onRetry={decisions.reload} />
      )}
      {!decisions.loading && !decisions.error && (!decisions.data || decisions.data.length === 0) && (
        <EmptyState title="No decisions logged yet" hint="Log a decision from a symbol's detail page." />
      )}
      {!decisions.loading && !decisions.error && decisions.data && decisions.data.length > 0 && (
        <div className="list">
          {decisions.data.map((d, i) => {
            const label = (
              <>
                {d.symbol ?? "—"}{" "}
                {d.action_taken === "acted"
                  ? "✅ Acted"
                  : d.action_taken === "passed"
                  ? "⏭ Passed"
                  : d.action_taken === "modified"
                  ? "🔁 Modified"
                  : "—"}
              </>
            );
            return (
              <div key={`${d.timestamp}-${i}`} className="row">
                <div className="row-main">
                  {/* Link to the symbol page when we have a symbol, matching the
                      Discovery candidate rows; a null-symbol decision stays plain
                      text (never a link to /symbol/—). */}
                  {d.symbol ? (
                    <Link
                      to={`/symbol/${encodeURIComponent(d.symbol)}`}
                      className="row-title"
                      style={{ fontWeight: 500, textDecoration: "none", color: theme.textPrimary }}
                    >
                      {label}
                    </Link>
                  ) : (
                    <span className="row-title" style={{ fontWeight: 500 }}>
                      {label}
                    </span>
                  )}
                  {d.notes && (
                    <div style={{ color: theme.textMuted, fontSize: 12, marginTop: 2 }}>{d.notes}</div>
                  )}
                </div>
                <div className="row-end">
                  <span style={{ color: theme.textMuted, fontSize: 12 }}>
                    {d.timestamp ? timeAgo(d.timestamp) : "—"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </SectionCard>
  );
}

function ControlsSection({
  status,
  onChanged,
}: {
  status: AgenticStatus | null;
  onChanged: () => void;
}) {
  const [confirmKind, setConfirmKind] = useState<"pause" | "resume" | null>(null);
  const [inputReason, setInputReason] = useState("");
  const pauseMutation = useMutation((r: string) => api.pauseAutomation(r));
  const resumeMutation = useMutation((r: string) => api.resumeAutomation(r));

  const active = status?.kill_switch.active ?? false;
  const running = !active;
  const busy = pauseMutation.pending || resumeMutation.pending;
  const resumeBlocked = !running && status !== null && !status.advisory_only;

  const openConfirm = (next: boolean) => {
    setInputReason("");
    setConfirmKind(next ? "resume" : "pause");
  };

  const confirmAction = async () => {
    if (confirmKind === "pause") await pauseMutation.run(inputReason);
    else if (confirmKind === "resume") await resumeMutation.run(inputReason);
    setConfirmKind(null);
    onChanged();
  };

  return (
    <SectionCard title="Controls">
      <div style={{ marginBottom: 16 }}>
        <Toggle
          checked={running}
          onChange={openConfirm}
          label={running ? "Agent: Running" : "Agent: Paused"}
          disabled={status === null || resumeBlocked}
          pending={busy}
        />
        {resumeBlocked && (
          <p style={{ color: theme.caution, fontSize: 12, marginTop: 8 }}>
            Resume must be done at the console while live trading is enabled.
          </p>
        )}
        <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: 8, lineHeight: 1.45 }}>
          Pausing does not stop the schedule — cycles still run, they just
          produce no recommendations (or submit no orders in live mode).
        </p>
        {(pauseMutation.error || resumeMutation.error) && (
          <div className="notice notice-warn" style={{ marginTop: 10 }}>
            <span>⚠️</span>
            <span>{pauseMutation.error ?? resumeMutation.error}</span>
          </div>
        )}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <Link to="/settings" className="card card-pad" style={{ textDecoration: "none" }}>
          <div style={{ fontWeight: 600, color: theme.textPrimary }}>Change execution mode →</div>
          <div style={{ color: theme.textMuted, fontSize: 12, marginTop: 2 }}>
            Advisory / simulation / paper / live — a deliberate safety ladder, managed in Settings.
          </div>
        </Link>
        <Link to="/marketplace" className="card card-pad" style={{ textDecoration: "none" }}>
          <div style={{ fontWeight: 600, color: theme.textPrimary }}>Manage Pilot follows →</div>
          <div style={{ color: theme.textMuted, fontSize: 12, marginTop: 2 }}>
            Follow, adjust, or cancel a Pilot — feeds this queue via the gated mirror rebalance.
          </div>
        </Link>
      </div>

      {confirmKind && (
        <Modal
          ariaLabel={confirmKind === "pause" ? "Pause agent" : "Resume agent"}
          onClose={() => setConfirmKind(null)}
        >
          <h2 style={{ margin: "0 0 2px", fontSize: "var(--t-title)" }}>
            {confirmKind === "pause" ? "Pause the agent?" : "Resume the agent?"}
          </h2>
          <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0 }}>
            {confirmKind === "pause"
              ? "New recommendations stop until resumed. The schedule keeps running."
              : "Recommendations resume on the next scheduled or manual run."}
          </p>
          <Input label="Reason" value={inputReason} onChange={(e) => setInputReason(e.target.value)} hint="Required." />
          <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
            <Button variant="neutral" onClick={() => setConfirmKind(null)} style={{ flex: 1 }}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={confirmAction}
              disabled={!inputReason.trim()}
              pending={busy}
              style={{ flex: 2 }}
            >
              {confirmKind === "pause" ? "Pause" : "Resume"}
            </Button>
          </div>
        </Modal>
      )}
    </SectionCard>
  );
}
