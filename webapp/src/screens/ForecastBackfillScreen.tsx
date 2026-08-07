import { useMemo, useState, useEffect } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { ForecastBackfillSummary } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useBackfillJob } from "../hooks/useBackfillJob";
import { ErrorState, Loading, MetricBadge } from "../components/ui";
import { fmtDate, fmtNum } from "../format";
import { theme } from "../theme";
import { PHASE_LABEL, formatBackfillCountdown, backfillFailureMessage } from "../forecastBackfillCopy";

export function ForecastBackfillScreen() {
  const nav = useNavigate();
  const { data, loading, error, status, reload } = useApi<ForecastBackfillSummary>(
    () => api.getForecastBackfill(),
    []
  );

  const { job, starting, error: jobError, notice, start, cancel, reset } = useBackfillJob();
  // "Tracking" covers the brief window after a 409-resumed start() where we
  // know we're polling an existing job (notice is set) but haven't received
  // its first real status yet (job is still null) -- treated the same as
  // job?.state === "running" for disabling the controls and offering Cancel,
  // since there genuinely IS an active job being tracked either way.
  const tracking = job?.state === "running" || (notice !== null && job === null);
  const running = starting || tracking;

  useEffect(() => {
    if (job?.state === "succeeded") {
      void reload();
    }
  }, [job?.state, reload]);

  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([]);
  const [thetaC, setThetaC] = useState<number>(0.50);

  // Strategy names are derived from the last run's own model_key set
  // ("{strategy}_{horizon}d") rather than hardcoded here -- this backend
  // (ml/forecast_backfill.py) reads its strategy list dynamically from
  // signals.registry.global_registry, so a fixed frontend list drifts the
  // moment a strategy is added/removed there, and can silently offer a
  // name (e.g. one that was never actually a registered SignalModule) that
  // strategy_ids would filter every real strategy out for, producing a
  // silent zero-model run with no error surfaced.
  const allStrategies = useMemo(() => {
    const names = new Set<string>();
    for (const key of Object.keys(data?.metrics ?? {})) {
      const match = key.match(/^(.+)_\d+d$/);
      if (match) names.add(match[1]);
    }
    return Array.from(names).sort();
  }, [data?.metrics]);


  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  const handleRunBackfill = async () => {
    reset();
    const params = {
      strategy_ids: selectedStrategies.length > 0 ? selectedStrategies : undefined,
      theta_c: thetaC
    };
    await start(params);
  };

  const metrics = data?.metrics || {};
  let modelKeys = Object.keys(metrics).sort((a, b) => {
    // Sort active (proven) models first
    const aActive = metrics[a]?.is_active ? 1 : 0;
    const bActive = metrics[b]?.is_active ? 1 : 0;
    if (aActive !== bActive) return bActive - aActive;
    return a.localeCompare(b);
  });
  if (selectedStrategies.length > 0) {
    modelKeys = modelKeys.filter(key => selectedStrategies.some(s => key.startsWith(s)));
  }

  return (
    <div className="screen">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-4)" }}>
        <button className="btn btn-ghost" onClick={back} type="button">
          ← Back
        </button>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-4)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
            <label style={{ fontSize: "14px", fontWeight: 600 }}>
              θ<sub>c</sub>: {thetaC.toFixed(2)}
            </label>
            <input 
              type="range" 
              min="0" max="1" step="0.05" 
              value={thetaC} 
              onChange={e => setThetaC(parseFloat(e.target.value))}
              disabled={running}
            />
          </div>
          <button
            className="btn btn-primary"
            onClick={() => void handleRunBackfill()}
            disabled={running}
            type="button"
          >
            {running ? "🔄 Processing..." : "🚀 Run Forecast Backfill"}
          </button>
        </div>
      </div>

      <header style={{ marginBottom: "var(--s-4)" }}>
        <h1 style={{ fontSize: "var(--t-title)", fontWeight: 800 }}>
          Agentic Forecast Backfill & Meta-Labeling
        </h1>
        <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-1)" }}>
          Multi-horizon (10d, 30d, 60d, 90d) confidence forecast backfilling for Time-Series Momentum (TSMOM)
          and Cross-Sectional Momentum (CSMOM) via Financial Modeling Prep (FMP).
        </p>
      </header>

      {(job || jobError || starting || notice) && (
        <div
          className="card card-pad"
          style={{
            marginBottom: "var(--s-4)",
            borderColor: (job?.state === "failed" || job?.state === "timeout" || job?.state === "cancelled" || jobError) ? theme.decline : running ? theme.accent : theme.growth,
            color: theme.textPrimary,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center"
          }}
        >
          <div>
            {starting
              ? "Starting…"
              : jobError
              ? `Error: ${jobError}`
              : job?.state === "running"
              ? `${job.phase ? PHASE_LABEL[job.phase] : "Starting…"} (Step ${job.step} of ${job.total_steps}) - ${formatBackfillCountdown(job.seconds_remaining)} remaining`
              : job?.state === "succeeded"
              ? `Success! Processed ${job.sample_rows} historical rows across horizons.`
              : job
              ? backfillFailureMessage(job)
              : notice}
          </div>
          <div>
            {tracking && (
              <button className="btn btn-ghost" style={{ color: theme.decline }} onClick={() => void cancel()} type="button">
                Cancel
              </button>
            )}
            {(job?.state === "failed" || job?.state === "timeout" || job?.state === "cancelled" || job?.state === "succeeded" || jobError) && (
              <button className="btn btn-ghost" onClick={() => { reset(); if (job?.state === "succeeded") void reload(); }} type="button">
                Dismiss
              </button>
            )}
          </div>
        </div>
      )}

      {loading ? (
        <Loading lines={4} />
      ) : error ? (
        <ErrorState message={error} status={status} onRetry={() => void reload()} />
      ) : (
        <>
          {(data?.dropped_tickers?.length ?? 0) > 0 && (
            <div
              className="card card-pad"
              style={{ marginBottom: "var(--s-4)", borderColor: theme.caution, color: theme.textPrimary }}
            >
              <strong>No real market data for {data?.dropped_tickers?.length} ticker(s)</strong> —
              FMP and the fallback provider both returned nothing, so they were dropped from the current run
              (they will be removed from the watchlist after 3 consecutive failures): {data?.dropped_tickers?.join(", ")}.
            </div>
          )}

          <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }}>
            <div style={{ fontWeight: 700, fontSize: "var(--t-subhead)", marginBottom: "var(--s-3)" }}>
              Pipeline Status & Data Sourcing
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-3)" }}>
              <MetricBadge label="Status" value={data?.status || "Ready"} good={true} />
              <MetricBadge
                label="Last Run"
                value={data?.timestamp ? fmtDate(data.timestamp) : "Not yet run"}
              />
              <MetricBadge
                label="Horizons"
                value={data?.horizons?.map((h) => `${h}d`).join(", ") || "10d, 30d, 60d, 90d"}
              />
              <MetricBadge
                label="Tickers Universe"
                value={data?.tickers?.length ? `${data.tickers.length} symbols` : "Default"}
              />
              <MetricBadge
                label="Total Backfilled Rows"
                value={data?.total_rows ? String(data.total_rows) : "N/A"}
              />
              <MetricBadge label="Data Provider" value="FMP (Financial Modeling Prep)" good={true} />
            </div>
          </section>

          <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-3)" }}>
              <div style={{ fontWeight: 700, fontSize: "var(--t-subhead)" }}>
                Trained Meta-Labelers Performance
              </div>
              <select 
                multiple 
                value={selectedStrategies} 
                onChange={e => setSelectedStrategies(Array.from(e.target.selectedOptions, option => option.value))}
                style={{ 
                  background: theme.surface2, 
                  color: theme.textPrimary, 
                  border: `1px solid ${theme.border}`,
                  borderRadius: "4px",
                  padding: "4px"
                }}
              >
                {allStrategies.map(s => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
            <div style={{ fontSize: "12px", color: theme.textMuted, marginBottom: "16px" }}>
              Hold Cmd/Ctrl to select multiple. If none selected, runs all available.
            </div>
            {modelKeys.length === 0 ? (
              <p style={{ color: theme.textMuted }}>
                No trained meta-labeler metrics available yet. Click &quot;Run Forecast Backfill&quot; above to train models.
              </p>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
                  <thead>
                    <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                      <th style={{ padding: "var(--s-2)", color: theme.textMuted }}>Model Key</th>
                      <th style={{ padding: "var(--s-2)", color: theme.textMuted }}>Status</th>
                      <th style={{ padding: "var(--s-2)", color: theme.textMuted }}>Accuracy</th>
                      <th style={{ padding: "var(--s-2)", color: theme.textMuted }}>ROC-AUC</th>
                      <th style={{ padding: "var(--s-2)", color: theme.textMuted }}>Train N</th>
                      <th style={{ padding: "var(--s-2)", color: theme.textMuted }}>Test N</th>
                      <th style={{ padding: "var(--s-2)", color: theme.textMuted }}>Split Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {modelKeys.map((key) => {
                      const m = metrics[key];
                      const isHighAcc = m.accuracy >= 0.50;
                      return (
                        <tr key={key} style={{ borderBottom: `1px solid ${theme.border}` }}>
                          <td style={{ padding: "var(--s-2)", fontWeight: 600 }}>{key}</td>
                          <td style={{ padding: "var(--s-2)" }}>
                            <span style={{ 
                              padding: "2px 6px", 
                              borderRadius: "4px", 
                              fontSize: "12px", 
                              background: m.is_active ? "rgba(46, 204, 113, 0.2)" : "rgba(108, 117, 125, 0.2)",
                              color: m.is_active ? theme.growth : theme.textMuted 
                            }}>
                              {m.is_active ? "Active" : "Diagnostic"}
                            </span>
                          </td>
                          <td style={{ padding: "var(--s-2)", color: isHighAcc ? theme.growth : theme.textPrimary }}>
                            {fmtNum(m.accuracy, 4)}
                          </td>
                          <td style={{ padding: "var(--s-2)" }}>{fmtNum(m.auc, 4)}</td>
                          <td style={{ padding: "var(--s-2)" }}>{m.n_train}</td>
                          <td style={{ padding: "var(--s-2)" }}>{m.n_test}</td>
                          <td style={{ padding: "var(--s-2)", color: theme.textMuted }}>{m.split_date}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="card card-pad">
            <div style={{ fontWeight: 700, fontSize: "var(--t-subhead)", marginBottom: "var(--s-2)" }}>
              How This Research Engine Works
            </div>
            <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", lineHeight: 1.6 }}>
              Primary models generate raw directional signals (+1 for Buy, -1 for Sell).
              Secondary Meta-Labelers learn market environment conditions (volatility regime, RSI, MACD, volume ratio)
              under which primary signals succeed or fail at each horizon, and report out-of-sample accuracy/AUC per
              model above. This is a research &amp; backfill diagnostic — the models it trains and saves here are
              a separate artifact from the ones that actually gate live position sizing. A model only reaches the
              live signal aggregator's confidence gate via <code>scripts/train_meta_labelers.py</code> followed by
              the deployability-gated <code>bootstrap_meta_registry()</code> startup step (PBO/DSR-checked against
              <code>ml/registry.yaml</code>), which this screen's runs do not feed.
            </p>
          </section>
        </>
      )}
    </div>
  );
}
export default ForecastBackfillScreen;
