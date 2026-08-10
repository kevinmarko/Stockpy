import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { useBrokerageLoginJob } from "../hooks/useBrokerageLoginJob";
import { useDebounce } from "../hooks/useDebounce";
import { usePersistedState } from "../hooks/usePersistedState";
import { useExecutionMode } from "../components/ExecutionModeContext";
import { DynamicGrid, resetGridLayout } from "../components/DynamicGrid";
import type { ResponsiveLayouts } from "react-grid-layout";
import type {
  AgenticDiscovery,
  AgenticStatus,
  BrokerageStatus,
  DecisionEntry,
  DiscoveryCandidate,
} from "../api/types";
import {
  Button,
  EmptyState,
  ErrorState,
  Input,
  Loading,
  Notice,
  StaleDataNotice,
} from "../components/ui";
import { Chip, ExecutionQueueSection } from "../components/ExecutionQueueSection";
import { CopyCommandBlock } from "../components/CopyCommandBlock";
import { DecisionModal } from "../components/DecisionModal";
import { KillSwitchToggle } from "../components/KillSwitchToggle";
import { Modal } from "../components/Modal";
import { RlhfReviewQueue } from "../components/RlhfReviewQueue";
import { RobinhoodConnectForm } from "../components/RobinhoodConnectForm";
import { TabGuide } from "../components/TabGuide";
import { theme } from "../theme";
import { timeAgo } from "../format";
import ActiveTraderLadder from "../components/ActiveTraderLadder";
import { ModelHealthPanel } from "../components/ModelHealthPanel";

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
const AGENTIC_LAYOUTS: ResponsiveLayouts = {
  lg: [
    { i: "agent_status", x: 0, y: 0, w: 12, h: 8 },
    { i: "discovery", x: 0, y: 8, w: 12, h: 12 },
    { i: "execution", x: 0, y: 20, w: 12, h: 10 },
    { i: "advanced_visuals", x: 0, y: 30, w: 12, h: 14 },
    { i: "rlhf", x: 0, y: 44, w: 12, h: 10 },
    { i: "decision", x: 0, y: 54, w: 6, h: 12 },
    { i: "controls", x: 6, y: 54, w: 6, h: 12 },
  ],
  md: [
    { i: "agent_status", x: 0, y: 0, w: 10, h: 8 },
    { i: "discovery", x: 0, y: 8, w: 10, h: 12 },
    { i: "execution", x: 0, y: 20, w: 10, h: 10 },
    { i: "advanced_visuals", x: 0, y: 30, w: 10, h: 14 },
    { i: "rlhf", x: 0, y: 44, w: 10, h: 10 },
    { i: "decision", x: 0, y: 54, w: 10, h: 12 },
    { i: "controls", x: 0, y: 66, w: 10, h: 12 },
  ],
  sm: [
    { i: "agent_status", x: 0, y: 0, w: 6, h: 8 },
    { i: "discovery", x: 0, y: 8, w: 6, h: 12 },
    { i: "execution", x: 0, y: 20, w: 6, h: 10 },
    { i: "advanced_visuals", x: 0, y: 30, w: 6, h: 14 },
    { i: "rlhf", x: 0, y: 44, w: 6, h: 10 },
    { i: "decision", x: 0, y: 54, w: 6, h: 12 },
    { i: "controls", x: 0, y: 66, w: 6, h: 12 },
  ],
  xs: [
    { i: "agent_status", x: 0, y: 0, w: 4, h: 8 },
    { i: "discovery", x: 0, y: 8, w: 4, h: 12 },
    { i: "execution", x: 0, y: 20, w: 4, h: 10 },
    { i: "advanced_visuals", x: 0, y: 30, w: 4, h: 14 },
    { i: "rlhf", x: 0, y: 44, w: 4, h: 10 },
    { i: "decision", x: 0, y: 54, w: 4, h: 12 },
    { i: "controls", x: 0, y: 66, w: 4, h: 12 },
  ],
  xxs: [
    { i: "agent_status", x: 0, y: 0, w: 2, h: 8 },
    { i: "discovery", x: 0, y: 8, w: 2, h: 12 },
    { i: "execution", x: 0, y: 20, w: 2, h: 10 },
    { i: "advanced_visuals", x: 0, y: 30, w: 2, h: 14 },
    { i: "rlhf", x: 0, y: 44, w: 2, h: 10 },
    { i: "decision", x: 0, y: 54, w: 2, h: 12 },
    { i: "controls", x: 0, y: 66, w: 2, h: 12 },
  ],
};

export function AgenticTrading() {
  const status = useApi<AgenticStatus>(() => api.getAgenticStatus(), []);
  const brokerageStatus = useApi<BrokerageStatus>(() => api.getBrokerageStatus(), []);

  useAutoPoll(
    () => {
      status.reload();
      brokerageStatus.reload();
    },
    "portfolio",
    { enabled: !status.loading && !brokerageStatus.loading, hasError: status.error != null }
  );

  const [refreshToken, setRefreshToken] = useState(0);
  const [showAuthModal, setShowAuthModal] = useState(false);
  // Persisted (not just component state) so the operator's last-viewed
  // ladder ticker survives a reload -- a non-sensitive UI preference, the
  // exact case usePersistedState documents itself for.
  const [ladderSymbol, setLadderSymbol] = usePersistedState("agentic-trading:ladder-symbol", "SPY");
  // Debounced before it reaches ActiveTraderLadder -- that component's
  // useApi/useLiveTick both re-fire (REST call + WebSocket reconnect) on
  // every `symbol` change, so an un-debounced per-keystroke value would fire
  // one of each per character typed. Matches the pattern SymbolInput.tsx
  // already uses for the same reason.
  const debouncedLadderSymbol = useDebounce(ladderSymbol, 250);

  // Drives the header's "Refresh Data" button through the same async
  // device-approval-push job flow as the auth modal's RobinhoodConnectForm
  // (a SEPARATE hook instance -- POST /brokerage/refresh needs no typed
  // credentials, it just re-authenticates with whatever is already
  // configured server-side), rather than the old fire-and-forget
  // `await api.refreshBrokerage()` call, which the new async contract would
  // otherwise make a no-op (the 202 response returns before the login
  // actually finishes).
  const brokerageRefresh = useBrokerageLoginJob();
  const isRefreshingData = brokerageRefresh.starting || brokerageRefresh.job?.state === "running";

  const reloadAll = () => {
    setRefreshToken((t) => t + 1);
    status.reload();
    brokerageStatus.reload();
  };

  const refreshAll = () => {
    void brokerageRefresh.start("refresh");
  };

  // Reload everything the instant the refresh job reaches a terminal state
  // (success or otherwise) -- keyed on job_id + state so each new refresh
  // attempt's own terminal transition fires this again.
  useEffect(() => {
    if (brokerageRefresh.job && brokerageRefresh.job.state !== "running") {
      reloadAll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [brokerageRefresh.job?.job_id, brokerageRefresh.job?.state]);

  const refreshFailureNotice = brokerageRefresh.error
    ? brokerageRefresh.error
    : brokerageRefresh.job?.state === "timeout"
      ? "No response came through in time. The last known data is still shown."
      : brokerageRefresh.job?.state === "failed"
        ? "Could not refresh the Robinhood account snapshot."
        : null;

  return (
    <div className="screen">
      {/* ---- Prominent Execution Mode Badge ---- */}
      <ExecutionModeBanner />

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "var(--s-3)", marginBottom: "var(--s-3)" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
            <h1 style={{ margin: 0 }}>Agentic Trading</h1>
            <Chip
              label={brokerageStatus.data?.connected ? "Robinhood Connected" : "Robinhood Disconnected"}
              tone={brokerageStatus.data?.connected ? "growth" : "caution"}
            />
          </div>
          <p style={{ color: theme.textSecondary, marginTop: "var(--s-1)", marginBottom: 0 }}>
            What the agent is doing, what it's found, and the gated controls that drive it.
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2-5)" }}>
          <Button variant="neutral" onClick={() => resetGridLayout("agentic")}>
            Reset Layout
          </Button>

          <Button variant="neutral" onClick={refreshAll} pending={isRefreshingData}>
            {isRefreshingData ? "Refreshing Data…" : "Refresh Data 🔄"}
          </Button>

          {brokerageStatus.data?.connected ? (
            <Button
              variant="neutral"
              onClick={async () => {
                try {
                  await api.disconnectBrokerage();
                  brokerageStatus.reload();
                } catch (e) {
                  console.error("Failed to disconnect:", e);
                }
              }}
              style={{ color: theme.decline, borderColor: theme.decline }}
              disabled={brokerageStatus.loading || !!brokerageStatus.error}
            >
              Disconnect Robinhood 🔓
            </Button>
          ) : (
            <Button 
              variant="primary" 
              onClick={() => setShowAuthModal(true)}
              disabled={brokerageStatus.loading || !!brokerageStatus.error}
              title={brokerageStatus.error ? "Brokerage service unavailable" : undefined}
            >
              Login to Robinhood 🔒
            </Button>
          )}
        </div>
      </div>

      <TabGuide tabKey="agentic" />

      {refreshFailureNotice && (
        <Notice variant="warn" style={{ marginBottom: "var(--s-3)" }}>
          <span>⚠️</span>
          <span>{refreshFailureNotice}</span>
        </Notice>
      )}

      {status.stale && <StaleDataNotice cachedAt={status.cachedAt} onRetry={status.reload} />}
      {status.loading && <Loading lines={3} />}
      {!status.loading && status.error && (
        <ErrorState message={status.error} status={status.status} onRetry={status.reload} />
      )}
      
      <div style={{ flex: 1, minHeight: 0 }}>
        <DynamicGrid layoutKey="agentic" defaultLayouts={AGENTIC_LAYOUTS}>
          {/* Agent Status */}
          <div key="agent_status" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            {!status.loading && !status.error && status.data && (
              <AgentStatusHeader data={status.data} onRefreshAll={refreshAll} />
            )}
          </div>

          {/* Discovery */}
          <div key="discovery" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <DiscoverySection refreshToken={refreshToken} />
          </div>

          {/* Execution Queue */}
          <div key="execution" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <ExecutionQueueSection />
          </div>

          {/* Advanced Quantitative Visualizations Section */}
          <div key="advanced_visuals" className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <div className="drag-handle" style={{ cursor: 'grab', marginBottom: 'var(--s-2)', paddingBottom: 'var(--s-1)', borderBottom: `1px solid ${theme.border}` }}>
              <h2 style={{ fontSize: "var(--t-title)", margin: 0 }}>Advanced Quantitative Visualizations</h2>
            </div>
            <div
              style={{
                flex: 1,
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
                gap: "var(--s-4)",
                overflowY: "auto",
                minHeight: 0
              }}
            >
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", marginBottom: "var(--s-2)" }}>
                  <label htmlFor="ladder-ticker-input" style={{ fontSize: "var(--t-caption)", fontWeight: 600, color: theme.textSecondary }}>
                    Ladder Ticker:
                  </label>
                  <input
                    id="ladder-ticker-input"
                    data-testid="ladder-ticker-input"
                    type="text"
                    value={ladderSymbol}
                    onChange={(e) => setLadderSymbol(e.target.value.toUpperCase())}
                    style={{
                      background: theme.surface2,
                      color: theme.textPrimary,
                      border: `1px solid ${theme.border}`,
                      borderRadius: "var(--r-sm)",
                      padding: "4px 8px",
                      width: "80px",
                      fontSize: "var(--t-caption)",
                      fontWeight: 600,
                    }}
                  />
                </div>
                <ActiveTraderLadder symbol={debouncedLadderSymbol} />
              </div>
              <ModelHealthPanel />
            </div>
          </div>

          {/* RLHF Review Queue */}
          <div key="rlhf" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <RlhfReviewQueue refreshToken={refreshToken} />
          </div>

          {/* Decision Journal */}
          <div key="decision" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <DecisionJournalSection refreshToken={refreshToken} />
          </div>

          {/* Controls */}
          <div key="controls" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <ControlsSection status={status.data} onChanged={status.reload} />
          </div>
        </DynamicGrid>
      </div>

      {showAuthModal && (
        <Modal ariaLabel="Robinhood Authentication Modal" onClose={() => setShowAuthModal(false)}>
          <RobinhoodConnectForm
            title="Robinhood On-Demand Authentication"
            subtitle="Approve the login with a tap in your Robinhood mobile app — verified with a read-only, on-demand login."
            onConnected={() => {
              setShowAuthModal(false);
              reloadAll();
            }}
          />
        </Modal>
      )}
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
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div className="drag-handle" style={{ cursor: 'grab', marginBottom: 'var(--s-2)', paddingBottom: 'var(--s-1)', borderBottom: `1px solid ${theme.border}` }}>
        <h2 style={{ margin: 0, fontSize: "var(--t-title)" }}>{title}</h2>
        {sub && (
          <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-1)", marginBottom: 0 }}>
            {sub}
          </p>
        )}
      </div>
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
        {children}
      </div>
    </section>
  );
}

function AgentStatusHeader({
  data,
  onRefreshAll,
}: {
  data: AgenticStatus;
  onRefreshAll: () => void;
}) {
  return (
    <SectionCard title="Agent status">
      <ExecutionLadder currentMode={data.mode} />
      <div style={{ display: "flex", gap: "var(--s-2)", flexWrap: "wrap", marginBottom: "var(--s-3)" }}>
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
        <p style={{ color: theme.caution, fontSize: "var(--t-body)", marginTop: 0, marginBottom: "var(--s-3)" }}>
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
      <div style={{ marginTop: "var(--s-3)" }}>
        <Button variant="neutral" onClick={onRefreshAll}>
          Refresh all
        </Button>
      </div>
    </SectionCard>
  );
}

function ExecutionLadder({ currentMode }: { currentMode: string }) {
  const steps = ["advisory", "simulation", "paper", "live"];
  // Handle edge cases like "off" or "review" by defaulting appropriately or leaving unhighlighted
  const currentIndex = steps.indexOf(currentMode);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        marginBottom: "var(--s-4)",
        gap: "var(--s-2)",
        overflowX: "auto",
        paddingBottom: "var(--s-1)",
        scrollbarWidth: "none",
        msOverflowStyle: "none",
      }}
      className="hide-scrollbar"
    >
      {steps.map((step, idx) => {
        const isActive = idx === currentIndex;
        const isPast = currentIndex !== -1 && idx < currentIndex;
        
        let color: string = theme.textMuted;
        let bg: string = "transparent";
        let borderColor: string = theme.border;
        
        if (isActive) {
           color = theme.base;
           bg = step === "live" ? theme.decline : theme.accent;
           borderColor = "transparent";
        } else if (isPast) {
           color = theme.textPrimary;
           borderColor = theme.textPrimary;
        }

        return (
          <div key={step} style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
            <div
              style={{
                padding: "var(--s-1) var(--s-3)",
                borderRadius: "999px",
                fontSize: "var(--t-caption)",
                fontWeight: isActive ? 600 : 400,
                color,
                background: bg,
                border: `1px solid ${borderColor}`,
                textTransform: "capitalize",
                whiteSpace: "nowrap",
              }}
            >
              {step}
            </div>
            {idx < steps.length - 1 && (
              <div
                style={{
                  width: 24,
                  height: 1,
                  background: isPast ? theme.textPrimary : theme.border,
                }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

function StatRow({ label, value }: { label: string; value: string }) {
  // NOT the shared .row/.row-end pattern -- that CSS hard-codes
  // `white-space: nowrap` on the value column (correct for its real callers'
  // short values like a price or a badge), which overlapped the label here
  // once the value became a full descriptive sentence. Stacked layout wraps
  // normally at any width instead.
  return (
    <div style={{ padding: "var(--s-2-5) 0", borderBottom: `1px solid ${theme.border}` }}>
      <div style={{ fontWeight: 500, fontSize: "var(--t-body)", color: theme.textPrimary }}>{label}</div>
      <div style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-0-5)" }}>{value}</div>
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

function DiscoverySection({ refreshToken }: { refreshToken: number }) {
  // `refreshToken` (from the parent's "Refresh all") is a useApi dependency so
  // this section refetches when the header's Refresh all is clicked -- it has
  // no 30s poll of its own, unlike status.
  const discovery = useApi<AgenticDiscovery>(() => api.getAgenticDiscovery(), [refreshToken]);
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
            <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: -6, marginBottom: "var(--s-3)" }}>
              As of {timeAgo(discovery.data.generated_at)}
            </p>
          )}
          {discovery.data.candidates.length === 0 ? (
            <EmptyState
              title="No candidates yet"
              hint={discovery.data.reason ?? "No scan has run yet."}
            />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)", marginBottom: "var(--s-4)" }}>
              {discovery.data.candidates.map((c) => (
                <CandidateRow key={c.symbol} c={c} />
              ))}
            </div>
          )}

          <div style={{ marginBottom: "var(--s-2)" }}>
            <div className="tile-label" style={{ marginBottom: "var(--s-1-5)" }}>
              Scan configs
            </div>
            {discovery.data.scan_configs.length === 0 ? (
              <p style={{ color: theme.textMuted, fontSize: "var(--t-body)" }}>None configured yet.</p>
            ) : (
              <>
                <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: 0, marginBottom: "var(--s-2-5)" }}>
                  Copy a command below into a separate Claude Code session to run just that scan —
                  nothing on this screen runs it for you.
                </p>
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
                  {discovery.data.scan_configs.map((cfg) => (
                    <div key={cfg.name} style={{ display: "flex", flexDirection: "column", gap: "var(--s-1-5)" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
                        <Chip label={cfg.enabled ? "enabled" : "disabled"} tone={cfg.enabled ? "growth" : "muted"} />
                        <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontSize: "var(--t-body)" }}>
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
            <div style={{ display: "flex", gap: "var(--s-2-5)", alignItems: "center" }}>
              <Button variant="primary" onClick={() => setAdding(true)}>
                Add scan config
              </Button>
              <span style={{ color: theme.accent, fontSize: "var(--t-caption)", fontWeight: 500 }}>
                🔍 Launch Broker Scan & Sector Filter
              </span>
            </div>
          ) : (
            <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>{discovery.data.note}</p>
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
        padding: "var(--s-2-5) var(--s-3)",
        background: theme.surface,
        border: `1px solid ${theme.border}`,
        borderRadius: "var(--r-sm)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
        <Link
          to={`/symbol/${encodeURIComponent(c.symbol)}`}
          style={{ textDecoration: "none", flex: 1, minWidth: 0 }}
        >
          <div style={{ display: "flex", alignItems: "baseline", gap: "var(--s-2)", flexWrap: "wrap" }}>
            <span style={{ fontWeight: 700, color: theme.textPrimary }}>{c.symbol}</span>
            {c.action ? (
              <span
                style={{
                  color: c.action === "BUY" ? theme.growth : theme.decline,
                  fontWeight: 600,
                  fontSize: "var(--t-caption)",
                }}
              >
                {c.action}
              </span>
            ) : (
              <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>not scored</span>
            )}
            {c.conviction !== null && (
              <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
                conviction {(c.conviction * 100).toFixed(0)}%
              </span>
            )}
            {c.scan_name && (
              <span style={{ color: theme.textMuted, fontSize: "var(--t-micro)" }}>{c.scan_name}</span>
            )}
            {c.discovered_at && (
              <span style={{ color: theme.textMuted, fontSize: "var(--t-micro)" }}>
                discovered {timeAgo(c.discovered_at)}
              </span>
            )}
          </div>
        </Link>
        <Button
          variant="neutral"
          onClick={() => setLogging(true)}
          style={{ padding: "var(--s-1) var(--s-2-5)", fontSize: "var(--t-caption)" }}
        >
          Log
        </Button>
        {added || alreadyWatching ? (
          <span
            data-testid="watch-status"
            style={{ color: theme.textMuted, fontSize: "var(--t-caption)", whiteSpace: "nowrap" }}
          >
            {added ? "✓ Watching" : "Already watching"}
          </span>
        ) : (
          <Button
            variant="neutral"
            onClick={() => watch.run()}
            pending={watch.pending}
            style={{ padding: "var(--s-1) var(--s-2-5)", fontSize: "var(--t-caption)" }}
          >
            Watch
          </Button>
        )}
      </div>
      {c.scan_reason && (
        <div style={{ color: theme.textSecondary, fontSize: "var(--t-caption)", marginTop: "var(--s-1-5)" }}>
          {typeof c.scan_reason === 'object' ? JSON.stringify(c.scan_reason) : c.scan_reason}
        </div>
      )}
      {added && (
        <div style={{ color: theme.growth, fontSize: "var(--t-caption)", marginTop: "var(--s-1-5)" }}>
          Added to your watchlist — the pipeline will evaluate it on the next run. No order was placed.
        </div>
      )}
      {watch.error && (
        <div style={{ color: theme.caution, fontSize: "var(--t-caption)", marginTop: "var(--s-1-5)" }}>{watch.error}</div>
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
  const [sector, setSector] = useState("ALL");
  const [minPrice, setMinPrice] = useState("5");
  const [minVolume, setMinVolume] = useState("1000000");

  const mutation = useMutation(() =>
    api.putScanConfig({
      name: name.trim(),
      filters: {
        sector: sector !== "ALL" ? sector : undefined,
        min_price: Number(minPrice) || 0,
        min_volume: Number(minVolume) || 0,
      },
      enabled: true,
    })
  );

  const submit = async () => {
    const r = await mutation.run();
    if (r) onSaved();
  };

  return (
    <Modal ariaLabel="Add scan config" onClose={onClose}>
      <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>Launch Broker Scan / Add Config</h2>
      <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0 }}>
        Saved to output/scan_configs.json with sector and volume filters. The agentic-discovery skill reads this when executed.
      </p>
      <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} hint="e.g. high_momentum_breakout" />

      <div style={{ marginBottom: "var(--s-2-5)" }}>
        <label htmlFor="scan-sector" className="tile-label">Sector Filter</label>
        <select
          id="scan-sector"
          value={sector}
          onChange={(e) => setSector(e.target.value)}
          style={{
            width: "100%",
            background: theme.base,
            color: theme.textPrimary,
            border: `1px solid ${theme.border}`,
            borderRadius: "var(--r-sm)",
            padding: "8px 12px",
            fontSize: "var(--t-body)",
            marginTop: "var(--s-1)",
          }}
        >
          <option value="ALL">All Sectors</option>
          <option value="Technology">Technology</option>
          <option value="Financial">Financial</option>
          <option value="Healthcare">Healthcare</option>
          <option value="Energy">Energy</option>
          <option value="Consumer">Consumer</option>
          <option value="Industrial">Industrial</option>
        </select>
      </div>

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
        <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
          <span>⚠️</span>
          <span>{mutation.error}</span>
        </Notice>
      )}
      <div style={{ display: "flex", gap: "var(--s-2-5)", marginTop: "var(--s-4-5)" }}>
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

function DecisionJournalSection({ refreshToken }: { refreshToken: number }) {
  // Same as DiscoverySection: no poll of its own, so the parent's "Refresh
  // all" token is a useApi dependency to force a refetch on demand.
  const decisions = useApi<DecisionEntry[]>(() => api.getDecisions({ limit: 10 }), [refreshToken]);

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
                    <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>{d.notes}</div>
                  )}
                </div>
                <div className="row-end">
                  <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
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
  return (
    <SectionCard title="Controls">
      <div style={{ marginBottom: "var(--s-4)" }}>
        {/* The SAME global kill switch as Settings → Signal generation
            (execution/kill_switch.py), shared via KillSwitchToggle so the two
            surfaces can't drift again (UX backlog finding #6). `showReason` is
            left off here because the Agent status header above already renders
            the live pause reason — turning it on would duplicate it. The
            control is disabled until status has loaded; `advisoryOnly` defaults
            to true (the safe state) since resume is only ever blocked once the
            switch is already paused. */}
        <KillSwitchToggle
          noun="Signal generation"
          active={status?.kill_switch.active ?? false}
          reason={status?.kill_switch.reason ?? null}
          advisoryOnly={status?.advisory_only ?? true}
          onChanged={onChanged}
          disabled={status === null}
        />
        <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-2)" }}>
          Same global kill switch as Settings → Signal generation.
        </p>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2-5)" }}>
        <Link to="/settings" className="card card-pad" style={{ textDecoration: "none" }}>
          <div style={{ fontWeight: 600, color: theme.textPrimary }}>Change execution mode →</div>
          <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
            Advisory / simulation / paper / live — a deliberate safety ladder, managed in Settings.
          </div>
        </Link>
        <Link to="/marketplace" className="card card-pad" style={{ textDecoration: "none" }}>
          <div style={{ fontWeight: 600, color: theme.textPrimary }}>Manage Pilot follows →</div>
          <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
            Follow, adjust, or cancel a Pilot — feeds this queue via the gated mirror rebalance.
          </div>
        </Link>
      </div>
    </SectionCard>
  );
}

/**
 * Prominent execution mode banner — always visible at the top of the /agentic
 * page. Reads from the global ExecutionModeContext (no extra API calls).
 */
function ExecutionModeBanner() {
  const { advisoryOnly, killSwitchActive, killSwitchReason, dryRun, loading } = useExecutionMode();

  if (loading) return null;

  const isLive = !advisoryOnly && !dryRun;

  return (
    <div style={{ marginBottom: "var(--s-3)", display: "flex", flexWrap: "wrap", gap: "var(--s-2)", alignItems: "center" }}>
      <span
        className={`execution-mode-badge ${isLive ? "execution-mode-badge--live" : "execution-mode-badge--advisory"}`}
      >
        <span style={{ fontSize: 10 }}>{isLive ? "🟢" : "🟠"}</span>
        {isLive ? "Live Trading" : "Advisory Only / Paper"}
      </span>
      {killSwitchActive && (
        <span
          className="execution-mode-badge"
          style={{ background: "rgba(239, 68, 68, 0.12)", border: "1px solid var(--decline)", color: "var(--decline)" }}
          title={killSwitchReason ?? undefined}
        >
          🛑 Kill Switch Active
        </span>
      )}
      {!advisoryOnly && dryRun && (
        <span
          className="execution-mode-badge execution-mode-badge--advisory"
        >
          Dry Run
        </span>
      )}
    </div>
  );
}
