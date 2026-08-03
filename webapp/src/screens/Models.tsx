import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api, ApiError } from "../api/client";
import type { ModelRow, ObservabilitySummary, Thresholds } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { useTrainingStatus } from "../hooks/useTrainingStatus";
import { Button, DeployableBadge, ErrorState, Loading, MetricBadge, InfoTip, Notice, Select } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { loadThresholds } from "../help/thresholds";
import { fmtDate, fmtNum, fmtPct } from "../format";
import { theme } from "../theme";
import SignalDriverWeights from "../components/SignalDriverWeights";
import ModelComparisonChart from "../components/ModelComparisonChart";

type DeployFilter = "all" | "deployable" | "not_deployable" | "needs_retrain";

const FILTERS: { value: DeployFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "deployable", label: "Deployable" },
  { value: "not_deployable", label: "Not Deployable" },
  { value: "needs_retrain", label: "Needs Retrain" },
];

type SortKey = "default" | "dsr" | "pbo" | "sharpe" | "maxdd";

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "default", label: "Default order" },
  { value: "dsr", label: "DSR" },
  { value: "pbo", label: "PBO" },
  { value: "sharpe", label: "Sharpe (CPCV OOS)" },
  { value: "maxdd", label: "Max DD (CPCV OOS)" },
];

// Plain numeric sort, nulls always last regardless of key -- mirrors
// Marketplace.tsx's byDesc convention. Descending order for every key
// (including PBO/MaxDD, where a smaller number is actually "better") since
// this is a generic "sort by this metric" control, not a goodness ranking.
function byValueDesc(sel: (m: ModelRow) => number | null) {
  return (a: ModelRow, b: ModelRow) => {
    const av = sel(a);
    const bv = sel(b);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return bv - av;
  };
}

const SORT_SELECTORS: Record<Exclude<SortKey, "default">, (m: ModelRow) => number | null> = {
  dsr: (m) => m.cpcv_dsr,
  pbo: (m) => m.pbo,
  sharpe: (m) => m.cpcv_mean_oos_sharpe,
  maxdd: (m) => m.cpcv_mean_oos_max_dd,
};

// Registry model names for a meta-labeler are always exactly
// `meta_labeler_<signal_id>` -- stripping this prefix always recovers a
// valid backend `--signal` value (ml.meta_bootstrap.META_LABELED_SIGNAL_IDS).
function metaLabelerSignal(modelName: string): string {
  return modelName.replace(/^meta_labeler_/, "");
}

/**
 * `thresholds` is live from `GET /thresholds` (`dsr_min`/`pbo_max`, mirroring
 * `validation/thresholds.py`'s `DSR_MIN`/`PBO_MAX` -- never re-typed as a
 * literal here). `null` while the fetch is in flight or failed degrades the
 * `good` color to "neutral" rather than guessing a gate.
 *
 * `cpcv_mean_oos_sharpe`/`cpcv_mean_oos_max_dd` badges always render
 * `good={null}` (neutral) -- they're informational CPCV out-of-sample
 * provenance numbers for these ML models, not gated against
 * `thresholds.net_sharpe_min`/`thresholds.max_drawdown_max` (those gates
 * apply to `validation/harness.py` strategies, a different system).
 */
function ModelCard({
  m,
  thresholds,
  isTraining,
  retrainError,
  onRetrain,
}: {
  m: ModelRow;
  thresholds: Thresholds | null;
  isTraining: boolean;
  retrainError?: string;
  onRetrain: (m: ModelRow) => void;
}) {
  const canRetrain = m.role === "cross_sectional_ranker" || m.role === "meta_labeler";
  return (
    <section className="card card-pad" style={{ marginBottom: "var(--s-3)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--s-2)" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: "var(--t-subhead)", wordBreak: "break-word" }}>{m.name}</div>
          {m.role && (
            <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>{m.role}</div>
          )}
        </div>
        <DeployableBadge deployable={m.deployable} />
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", marginTop: "var(--s-3)" }}>
        <MetricBadge
          label="DSR"
          value={m.cpcv_dsr == null ? "—" : fmtNum(m.cpcv_dsr, 3)}
          good={
            m.cpcv_dsr == null || thresholds == null ? null : m.cpcv_dsr > thresholds.dsr_min
          }
        />
        <MetricBadge
          label="PBO"
          value={m.pbo == null ? "—" : fmtNum(m.pbo, 2)}
          good={m.pbo == null || thresholds == null ? null : m.pbo < thresholds.pbo_max}
        />
        <MetricBadge
          label="OOS Sharpe (CPCV)"
          value={m.cpcv_mean_oos_sharpe == null ? "—" : fmtNum(m.cpcv_mean_oos_sharpe, 2)}
          good={null}
        />
        <MetricBadge
          label="OOS Max DD (CPCV)"
          value={m.cpcv_mean_oos_max_dd == null ? "—" : fmtPct(m.cpcv_mean_oos_max_dd, 0, { fromFraction: true })}
          good={null}
        />
        <MetricBadge
          label="Trained"
          value={
            m.age_days == null ? fmtDate(m.trained_date) : `${fmtDate(m.trained_date)} (${m.age_days}d ago)`
          }
        />
        <MetricBadge label="N" value={m.n_train == null ? "—" : String(m.n_train)} />
        {m.needs_retrain === true && (
          <InfoTip
            triggerClassName="badge badge-warn"
            content={
              thresholds == null
                ? "Older than the retrain window."
                : `Trained more than ${thresholds.retrain_window_days} days ago — flagged for the next retraining job.`
            }
          >
            ⏱ Needs retrain
          </InfoTip>
        )}
      </div>
      {m.notes && (
        <p style={{ color: theme.textSecondary, fontSize: "var(--t-label)", lineHeight: 1.5, marginTop: "var(--s-3)" }}>
          {m.notes}
        </p>
      )}
      {canRetrain && (
        <div style={{ marginTop: "var(--s-3)", display: "flex", flexDirection: "column", gap: "var(--s-1-5)", alignItems: "flex-start" }}>
          <Button variant="neutral" disabled={isTraining} onClick={() => onRetrain(m)}>
            {isTraining ? "Training…" : "Retrain Now"}
          </Button>
          {retrainError && (
            <span style={{ color: theme.decline, fontSize: "var(--t-caption)" }}>{retrainError}</span>
          )}
        </div>
      )}
    </section>
  );
}

export function Models() {
  const nav = useNavigate();
  const { data, loading, error, status, reload } = useApi<ModelRow[]>(
    () => api.getModels(),
    []
  );
  useAutoPoll(reload, "signals", { hasError: error != null });
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  // Live deployability-gate thresholds (GET /thresholds, session-cached) so
  // the footer's "Deployable = ..." summary and each card's DSR/PBO badge
  // color quote the SAME numbers validation/thresholds.py actually enforces
  // -- never a hard-coded literal. Mirrors TabGuide.tsx's loadThresholds()
  // usage and StrategyHealth.tsx's identical pattern.
  const [thresholds, setThresholds] = useState<Thresholds | null>(null);
  useEffect(() => {
    let alive = true;
    void loadThresholds().then((t) => {
      if (alive) setThresholds(t);
    });
    return () => {
      alive = false;
    };
  }, []);

  // Page-level macro-regime-gate banner. The macro gate is portfolio-wide,
  // not per-model, so this is a single banner, not a per-card badge --
  // sourced from ONE honest backend field combination, never re-derived
  // from anything else. `kill_switch_active` (the operator's manual global
  // kill-switch FILE) is a DIFFERENT, unrelated mechanism and is
  // deliberately not used here.
  const { data: obsData } = useApi<ObservabilitySummary>(
    () => api.getObservabilitySummary("1M", 30),
    []
  );
  const macroGatePaused =
    obsData?.regime?.macro_regime_gate_enabled === true &&
    obsData?.regime?.macro_kill_switch === true;

  // ---- Retrain Now: dispatch by role, track the returned job_id, and
  // reflect its live status via the /ws/training/status broadcast rather
  // than a boolean that flips back the instant the POST resolves. ----
  const trainingStatuses = useTrainingStatus();
  const [trainingJobs, setTrainingJobs] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState<Record<string, boolean>>({});
  const [retrainErrors, setRetrainErrors] = useState<Record<string, string | undefined>>({});
  const timeoutsRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  useEffect(() => {
    const timeouts = timeoutsRef.current;
    return () => {
      Object.values(timeouts).forEach(clearTimeout);
    };
  }, []);

  // Free a model's tracked job the instant the WS reports it finished
  // (rather than waiting out the 10-minute fallback timeout below), and
  // refresh the registry so the card picks up its new trained_date/metrics.
  useEffect(() => {
    setTrainingJobs((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const [name, jobId] of Object.entries(prev)) {
        if (trainingStatuses[jobId]?.status === "finished") {
          delete next[name];
          changed = true;
        }
      }
      if (changed) reload();
      return changed ? next : prev;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trainingStatuses]);

  async function handleRetrain(m: ModelRow) {
    setRetrainErrors((prev) => ({ ...prev, [m.name]: undefined }));
    setSubmitting((prev) => ({ ...prev, [m.name]: true }));
    try {
      const job =
        m.role === "cross_sectional_ranker"
          ? await api.createJob("train_lgbm")
          : await api.createJob("train_meta", { signal: metaLabelerSignal(m.name) });
      setTrainingJobs((prev) => ({ ...prev, [m.name]: job.job_id }));
      if (timeoutsRef.current[m.name]) clearTimeout(timeoutsRef.current[m.name]);
      // Safety net: if the WS never delivers a "finished" frame for this
      // job (dropped connection, server restart mid-job, ...), the button
      // must not stay disabled forever.
      timeoutsRef.current[m.name] = setTimeout(() => {
        setTrainingJobs((prev) => {
          if (prev[m.name] !== job.job_id) return prev;
          const next = { ...prev };
          delete next[m.name];
          return next;
        });
      }, 10 * 60 * 1000);
    } catch (e) {
      const msg =
        e instanceof ApiError && e.status === 409
          ? "Another training job is already running."
          : e instanceof Error
            ? e.message
            : "Failed to start the training job.";
      setRetrainErrors((prev) => ({ ...prev, [m.name]: msg }));
    } finally {
      setSubmitting((prev) => ({ ...prev, [m.name]: false }));
    }
  }

  // ---- Deployability filter + sort (client-side, over the fetched
  // registry -- the registry is small enough that a server round trip per
  // filter/sort change would be pure overhead). ----
  const [filterKey, setFilterKey] = useState<DeployFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("default");

  const visibleModels = useMemo(() => {
    const all = data ?? [];
    const filtered =
      filterKey === "all"
        ? all
        : filterKey === "deployable"
          ? all.filter((m) => m.deployable === true)
          : filterKey === "not_deployable"
            ? all.filter((m) => m.deployable !== true)
            : all.filter((m) => m.needs_retrain === true);
    if (sortKey === "default") return filtered;
    return [...filtered].sort(byValueDesc(SORT_SELECTORS[sortKey]));
  }, [data, filterKey, sortKey]);

  return (
    <div className="screen">
      <button
        onClick={back}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          color: theme.textSecondary,
          fontSize: "var(--t-callout)",
          marginBottom: "var(--s-2)",
        }}
      >
        ← Pilots
      </button>
      <h1 className="screen-title">The models</h1>
      <p className="screen-sub">
        The ML models behind the platform, with their honest CPCV validation
        metrics. A model that fails a gate is shown as not deployable.
      </p>

      <TabGuide tabKey="models" />

      {macroGatePaused && (
        <Notice variant="warn" style={{ marginTop: "var(--s-3)" }}>
          <span>
            New buy orders are paused by the macro regime gate — the macro
            kill-switch is active.
          </span>
        </Notice>
      )}

      <div className="chart-grid-2">
        <div style={{ height: 400 }}>
          <ModelComparisonChart />
        </div>
        <div style={{ height: 400 }}>
          <SignalDriverWeights />
        </div>
      </div>

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        data.length === 0 ? (
          <div className="empty" style={{ padding: "var(--s-7-5)" }}>
            No model registry available yet.
          </div>
        ) : (
          <>
            <div
              style={{
                display: "flex",
                gap: "var(--s-2)",
                overflowX: "auto",
                paddingBottom: "var(--s-2)",
                marginTop: "var(--s-4)",
                scrollbarWidth: "none",
              }}
            >
              {FILTERS.map((f) => (
                <button
                  key={f.value}
                  className="chip"
                  style={{
                    background: filterKey === f.value ? theme.accent : "transparent",
                    color: filterKey === f.value ? "#fff" : theme.textPrimary,
                    border: `1px solid ${filterKey === f.value ? theme.accent : theme.borderStrong}`,
                    cursor: "pointer",
                    fontSize: "var(--t-label)",
                    padding: "var(--s-1-5) var(--s-3)",
                    whiteSpace: "nowrap",
                  }}
                  onClick={() => setFilterKey(f.value)}
                >
                  {f.label}
                </button>
              ))}
            </div>

            <div style={{ marginTop: "var(--s-3)", maxWidth: 220 }}>
              <Select
                label="Sort by"
                value={sortKey}
                onChange={(e) => setSortKey(e.target.value as SortKey)}
                options={SORT_OPTIONS}
                testId="models-sort-select"
              />
            </div>

            {visibleModels.length === 0 ? (
              <div className="empty" style={{ padding: "var(--s-7-5)" }}>
                No models match the selected filter.
              </div>
            ) : (
              <div style={{ marginTop: "var(--s-3)" }}>
                {visibleModels.map((m) => (
                  <ModelCard
                    key={m.name}
                    m={m}
                    thresholds={thresholds}
                    isTraining={Boolean(submitting[m.name]) || trainingJobs[m.name] != null}
                    retrainError={retrainErrors[m.name]}
                    onRetrain={handleRetrain}
                  />
                ))}
              </div>
            )}
          </>
        )
      )}
      <p
        style={{
          color: theme.textMuted,
          fontSize: "var(--t-footnote)",
          marginTop: "var(--s-5)",
          textAlign: "center",
          lineHeight: 1.5,
        }}
      >
        Deployable = CPCV-DSR &gt; {fmtNum(thresholds?.dsr_min, 2)} AND PBO &lt;{" "}
        {fmtNum(thresholds?.pbo_max, 2)}. Metrics are never loosened to force a
        green badge.
        <br />
        Needs retrain = trained more than{" "}
        {thresholds?.retrain_window_days == null ? "—" : thresholds.retrain_window_days}{" "}
        days ago.
      </p>
    </div>
  );
}
