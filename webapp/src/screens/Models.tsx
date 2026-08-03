import { useEffect, useState, useMemo } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { ModelRow, Thresholds, ObservabilitySummary } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { DeployableBadge, ErrorState, Loading, MetricBadge, InfoTip } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { loadThresholds } from "../help/thresholds";
import { fmtDate, fmtNum, fmtPct } from "../format";
import { theme } from "../theme";
import SignalDriverWeights from "../components/SignalDriverWeights";
import ModelComparisonChart from "../components/ModelComparisonChart";

function ModelCard({ m, thresholds }: { m: ModelRow; thresholds: Thresholds | null }) {
  const [retraining, setRetraining] = useState(false);

  const handleRetrain = async () => {
    setRetraining(true);
    try {
      await api.createJob("validation", { model: m.name });
    } catch (e) {
      console.error("Retrain failed", e);
    } finally {
      setRetraining(false);
    }
  };

  return (
    <section className="card card-pad" style={{ marginBottom: "var(--s-3)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--s-2)" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: "var(--t-subhead)", wordBreak: "break-word" }}>{m.name}</div>
          {m.role && (
            <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>{m.role}</div>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <button 
            className="btn btn-secondary" 
            style={{ fontSize: "var(--t-caption)", padding: "var(--s-1) var(--s-2)" }}
            onClick={handleRetrain}
            disabled={retraining}
          >
            {retraining ? "Queued..." : "Retrain Now"}
          </button>
          <DeployableBadge deployable={m.deployable} />
        </div>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", marginTop: "var(--s-3)" }}>
        {m.is_active_in_regime === false && (
          <div className="badge badge-error" style={{ fontSize: "var(--t-caption)", padding: "var(--s-0-5) var(--s-1)", fontWeight: 600 }}>
            Paused by Macro Gate
          </div>
        )}
        <MetricBadge
          label="DSR"
          value={m.cpcv_dsr == null ? "—" : fmtNum(m.cpcv_dsr, 3)}
          good={m.cpcv_dsr == null || thresholds == null ? null : m.cpcv_dsr > thresholds.dsr_min}
        />
        <MetricBadge
          label="PBO"
          value={m.pbo == null ? "—" : fmtNum(m.pbo, 2)}
          good={m.pbo == null || thresholds == null ? null : m.pbo < thresholds.pbo_max}
        />
        <MetricBadge
          label="Sharpe"
          value={m.sharpe == null ? "—" : fmtNum(m.sharpe, 2)}
          good={m.sharpe == null || thresholds == null ? null : m.sharpe > thresholds.net_sharpe_min}
        />
        <MetricBadge
          label="Max DD"
          value={m.max_dd == null ? "—" : fmtPct(m.max_dd)}
          good={m.max_dd == null || thresholds == null ? null : m.max_dd < thresholds.max_drawdown_max}
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
    </section>
  );
}

export function Models() {
  const nav = useNavigate();
  const { data, loading, error, status, reload } = useApi<ModelRow[]>(
    () => api.getModels(),
    []
  );
  
  const { data: obsData } = useApi<ObservabilitySummary>(
    () => api.getObservabilitySummary("1M", 30),
    []
  );

  useAutoPoll(reload, "signals", { hasError: error != null });
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

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

  const [filter, setFilter] = useState<"all" | "deployable" | "not_deployable" | "needs_retrain">("all");
  const [sort, setSort] = useState<"dsr" | "pbo" | "sharpe" | "max_dd">("dsr");

  const filteredAndSortedData = useMemo(() => {
    if (!data) return [];
    let result = [...data];

    // Filter
    if (filter === "deployable") {
      result = result.filter(m => m.deployable === true);
    } else if (filter === "not_deployable") {
      result = result.filter(m => m.deployable === false);
    } else if (filter === "needs_retrain") {
      result = result.filter(m => m.needs_retrain === true);
    }

    // Sort
    result.sort((a, b) => {
      if (sort === "dsr") {
        return (b.cpcv_dsr ?? -Infinity) - (a.cpcv_dsr ?? -Infinity);
      } else if (sort === "pbo") {
        return (a.pbo ?? Infinity) - (b.pbo ?? Infinity);
      } else if (sort === "sharpe") {
        return (b.sharpe ?? -Infinity) - (a.sharpe ?? -Infinity);
      } else if (sort === "max_dd") {
        return (a.max_dd ?? Infinity) - (b.max_dd ?? Infinity);
      }
      return 0;
    });

    return result;
  }, [data, filter, sort]);

  const isMacroGatePaused = obsData?.regime?.macro_regime_gate_enabled && obsData?.regime?.kill_switch_active;

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
      
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: "var(--s-2)" }}>
        <div>
          <h1 className="screen-title" style={{ marginBottom: "var(--s-1)" }}>The models</h1>
          <p className="screen-sub" style={{ marginBottom: 0 }}>
            The ML models behind the platform, with their honest CPCV validation
            metrics. A model that fails a gate is shown as not deployable.
          </p>
        </div>
        {isMacroGatePaused && (
          <div className="badge badge-error" style={{ fontSize: "var(--t-body)", padding: "var(--s-2) var(--s-3)", fontWeight: 600 }}>
            Paused by Macro Gate
          </div>
        )}
      </div>

      <TabGuide tabKey="models" />

      <div className="chart-grid-2">
        <div style={{ height: 400 }}>
          <ModelComparisonChart />
        </div>
        <div style={{ height: 400 }}>
          <SignalDriverWeights />
        </div>
      </div>

      <div style={{ display: "flex", gap: "var(--s-4)", marginTop: "var(--s-6)", marginBottom: "var(--s-3)", alignItems: "center" }}>
        <div style={{ display: "flex", gap: "var(--s-2)" }}>
          {["all", "deployable", "not_deployable", "needs_retrain"].map(f => (
            <button
              key={f}
              onClick={() => setFilter(f as any)}
              className={filter === f ? "btn btn-primary" : "btn btn-secondary"}
              style={{ fontSize: "var(--t-label)", padding: "var(--s-1) var(--s-3)" }}
            >
              {f === "all" ? "All" : f === "deployable" ? "Deployable" : f === "not_deployable" ? "Not Deployable" : "Needs Retrain"}
            </button>
          ))}
        </div>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <span style={{ fontSize: "var(--t-label)", color: "var(--text-muted)", fontWeight: 600 }}>Sort By:</span>
          <select 
            value={sort} 
            onChange={e => setSort(e.target.value as any)}
            className="input"
            style={{ padding: "var(--s-1) var(--s-2)", fontSize: "var(--t-label)", width: "auto", backgroundColor: "var(--surface-2)" }}
          >
            <option value="dsr">DSR (High to Low)</option>
            <option value="pbo">PBO (Low to High)</option>
            <option value="sharpe">Sharpe (High to Low)</option>
            <option value="max_dd">Max DD (Low to High)</option>
          </select>
        </div>
      </div>

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && filteredAndSortedData && (
        filteredAndSortedData.length === 0 ? (
          <div className="empty" style={{ padding: "var(--s-7-5)" }}>
            {data?.length === 0 ? "No model registry available yet." : "No models match your filters."}
          </div>
        ) : (
          <div>
            {filteredAndSortedData.map((m) => (
              <ModelCard key={m.name} m={m} thresholds={thresholds} />
            ))}
          </div>
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

