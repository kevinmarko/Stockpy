import { Link } from "react-router";
import { api } from "../api/client";
import type { Follow, LlmStatus, PromptListResponse, StrategyMatrix, TunablesResponse } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { theme } from "../theme";
import { SectionCard } from "../components/SectionCard";
import { Button, Loading, ErrorState, EmptyState } from "../components/ui";
import { fmtUsd } from "../format";

export function SettingsModules() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
      <div>
        <h2 style={{ margin: "0 0 var(--s-1)", fontSize: "var(--t-title)" }}>Modules &amp; Integrations</h2>
        <p style={{ color: "var(--text-secondary)", margin: 0, fontSize: "var(--t-body)" }}>
          Manage tunables, AI providers, and sub-systems.
        </p>
      </div>

      <SignalModulesLink />
      <TunablesLink />
      <SentimentLink />
      <SectorSelectionLink />
      <FmpLink />
      <EtfTransmissionLink />
      <PromptRegistryLink />
      <AiControlCenterLink />
      
      <ActiveFollowsSection />
    </div>
  );
}


/**
 * Entry point to the Strategy Matrix screen — a `.env`-write surface, so it
 * lives under /settings alongside every other write surface, not in top-level
 * nav. Shows a live "N modules · M disabled" summary and links to the editor.
 */
function SignalModulesLink() {
  const { data } = useApi<StrategyMatrix>(() => api.getStrategyMatrix(), []);
  const count = data?.modules.length ?? null;
  const disabledCount = data?.disabled.length ?? null;
  return (
    <Link
      to="/settings/strategy"
      className="card card-pad"
      style={{ display: "block", textDecoration: "none" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>Signal modules</div>
          <div style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-0-5)" }}>
            {count == null
              ? "Signal weights & enabled modules"
              : `${count} modules · ${disabledCount} disabled`}
          </div>
        </div>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-title)" }}>›</span>
      </div>
    </Link>
  );
}


/**
 * Entry point to the Settings Manager (runtime tunables) screen — a `.env`-write
 * surface (PUT /settings/tunables), so it lives under /settings alongside every
 * other write surface, not in top-level nav. Shows a live "N tunables · M
 * groups" summary and links to the editor.
 */
function TunablesLink() {
  const { data } = useApi<TunablesResponse>(() => api.getTunables(), []);
  const groupCount = data?.groups.length ?? null;
  const fieldCount =
    data?.groups.reduce((acc, g) => acc + g.fields.length, 0) ?? null;
  return (
    <Link
      to="/settings/tunables"
      className="card card-pad"
      style={{ display: "block", textDecoration: "none" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>Runtime tunables</div>
          <div style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-0-5)" }}>
            {fieldCount == null
              ? "Sizing, forecasting & data settings"
              : `${fieldCount} tunables · ${groupCount} groups`}
          </div>
        </div>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-title)" }}>›</span>
      </div>
    </Link>
  );
}


function SentimentLink() {
  const { data } = useApi<TunablesResponse>(() => api.getSentimentSettings(), []);
  const fieldCount = data?.groups.reduce((acc, g) => acc + g.fields.length, 0) ?? null;
  return (
    <Link
      to="/settings/sentiment"
      className="card card-pad"
      style={{ display: "block", textDecoration: "none" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>Sentiment &amp; News Ingestion</div>
          <div style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-0-5)" }}>
            {fieldCount == null
              ? "Sources, FinBERT, catalysts & attention"
              : `${fieldCount} ingestion settings`}
          </div>
        </div>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-title)" }}>›</span>
      </div>
    </Link>
  );
}


function SectorSelectionLink() {
  const { data } = useApi<TunablesResponse>(() => api.getSectorSelectionSettings(), []);
  const fieldCount = data?.groups.reduce((acc, g) => acc + g.fields.length, 0) ?? null;
  return (
    <Link
      to="/settings/sector-selection"
      className="card card-pad"
      style={{ display: "block", textDecoration: "none" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>Sector Selection</div>
          <div style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-0-5)" }}>
            {fieldCount == null
              ? "Top N, heat weights & similarity settings"
              : `${fieldCount} sector selection settings`}
          </div>
        </div>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-title)" }}>›</span>
      </div>
    </Link>
  );
}


function FmpLink() {
  const { data } = useApi<TunablesResponse>(() => api.getFmpSettings(), []);
  const fieldCount = data?.groups.reduce((acc, g) => acc + g.fields.length, 0) ?? null;
  return (
    <Link
      to="/settings/fmp"
      className="card card-pad"
      style={{ display: "block", textDecoration: "none" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>Financial Modeling Prep</div>
          <div style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-0-5)" }}>
            {fieldCount == null
              ? "API credentials, primary & diagnostic feeds"
              : `${fieldCount} FMP settings`}
          </div>
        </div>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-title)" }}>›</span>
      </div>
    </Link>
  );
}


function EtfTransmissionLink() {
  const { data } = useApi<TunablesResponse>(() => api.getEtfTransmissionSettings(), []);
  const fieldCount = data?.groups.reduce((acc, g) => acc + g.fields.length, 0) ?? null;
  return (
    <Link
      to="/settings/etf-transmission"
      className="card card-pad"
      style={{ display: "block", textDecoration: "none" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>ETF Volatility Transmission</div>
          <div style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-0-5)" }}>
            {fieldCount == null
              ? "Holdings ingestion, residualization & derates"
              : `${fieldCount} ETF transmission settings`}
          </div>
        </div>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-title)" }}>›</span>
      </div>
    </Link>
  );
}


/**
 * Entry point to the Prompt Registry screen — a `.env`-write-adjacent surface
 * (`PUT /prompts/pin` persists PROMPT_REGISTRY_PINS), so it lives under
 * /settings alongside every other write surface, not in top-level nav. Shows
 * a live "N prompts · M pinned" summary and links to the full table/diff/pin
 * editor. NOTE: the actual `<Route path="/settings/prompts">` is added by
 * Agent A's integration pass (see App.tsx's sole-editor convention) — this
 * card is wired ahead of that route landing, matching the pattern every other
 * *Link component here already uses.
 */
function PromptRegistryLink() {
  const { data } = useApi<PromptListResponse>(() => api.getPrompts(), []);
  const count = data?.prompts.length ?? null;
  const pinnedCount = data?.prompts.filter((p) => p.pinned_version != null).length ?? null;
  return (
    <Link
      to="/settings/prompts"
      className="card card-pad"
      style={{ display: "block", textDecoration: "none" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>Prompt Registry</div>
          <div style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-0-5)" }}>
            {count == null
              ? "Version control for AI-facing instructions"
              : `${count} prompts · ${pinnedCount} pinned`}
          </div>
        </div>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-title)" }}>›</span>
      </div>
    </Link>
  );
}


/**
 * Entry point to the AI Control Center screen -- a `.env`-write surface (PUT
 * /llm/setting), so it lives under /settings alongside every other write
 * surface, not in top-level nav. Shows a live "N capabilities · M ready"
 * summary plus an attention indicator, and links to the toggle/provider
 * editor + last-real-call telemetry (formerly an inline "AI providers"
 * section on this screen -- moved to its own screen once it grew a write
 * path, mirroring how Strategy Matrix already got its own /settings/strategy
 * route rather than staying inline here).
 */
function AiControlCenterLink() {
  const { data } = useApi<LlmStatus>(() => api.getLlmStatus(), []);
  const readyCount = data?.capabilities.filter((c) => c.status === "ready").length ?? null;
  const total = data?.capabilities.length ?? null;
  return (
    <Link
      to="/settings/ai"
      className="card card-pad"
      style={{ display: "block", textDecoration: "none" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>
            AI providers
            {data?.attention && (
              <span aria-label="needs attention" style={{ marginLeft: 6 }}>
                ⚠️
              </span>
            )}
          </div>
          <div style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-0-5)" }}>
            {total == null
              ? "LLM commentary, Gravity AI runner, Opal research"
              : `${readyCount}/${total} ready`}
          </div>
        </div>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-title)" }}>›</span>
      </div>
    </Link>
  );
}


/**
 * Per-pilot "Re-plan" over the EXISTING POST /pilots/{id}/follow endpoint --
 * zero new backend code. "Re-plan all" was cut from this feature: cross-
 * Pilot netting doesn't exist, so a naive loop would emit duplicate intents
 * for a symbol held by two Pilots (see the Data & Automation plan).
 */
function ActiveFollowsSection() {
  const {
    data: follows,
    loading,
    error,
    status: httpStatus,
    reload,
  } = useApi<Follow[]>(() => api.getFollows(), []);

  return (
    <SectionCard
      title="Active follows"
      sub="Re-plan recomputes and replaces output/execution_queue.json for that Pilot only."
    >
      {loading && <Loading lines={2} />}
      {!loading && error && (
        <ErrorState message={error} status={httpStatus} onRetry={reload} />
      )}
      {!loading && !error && follows && (
        follows.length === 0 ? (
          <EmptyState title="No active follows" />
        ) : (
          <div className="list">
            {follows.map((f) => (
              <FollowRow key={f.pilot_id} follow={f} />
            ))}
          </div>
        )
      )}
    </SectionCard>
  );
}


function FollowRow({ follow }: { follow: Follow }) {
  const { run, pending, result, error } = useMutation(() =>
    api.follow(follow.pilot_id, follow.amount)
  );

  return (
    <div className="row" style={{ alignItems: "flex-start" }}>
      <div className="row-main">
        <span className="row-title">{follow.pilot_id}</span>
        <span className="row-sub">{fmtUsd(follow.amount)}</span>
        {result && (
          <span
            className="row-sub"
            style={{ color: result.queue_written ? theme.growth : theme.textMuted }}
          >
            {result.queue_written
              ? `Re-planned — ${result.planned_intents.length} order(s) queued.`
              : "Preview only — execution mode is off, nothing was written."}
          </span>
        )}
        {error && (
          <span className="row-sub" style={{ color: theme.decline }}>
            {error}
          </span>
        )}
      </div>
      <Button variant="neutral" pending={pending} onClick={() => run()}>
        Re-plan
      </Button>
    </div>
  );
}
