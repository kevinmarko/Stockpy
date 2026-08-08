import type { ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import { api } from "../api/client";
import type {
  AiChartResponse,
  AiCommentaryResponse,
  AiResearchResponse,
  ForecastModelError,
  ForecastSkill,
  OptionsDirective,
  RollingBeta,
  SymbolDetail as SymbolDetailT,
  SymbolOptions,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { useMutation } from "../hooks/useMutation";
import { Button, ErrorState, Loading, MetricBadge, Notice } from "../components/ui";
import { PerfLine, chartAxisLine, chartAxisTick, chartGridProps, chartTooltipStyle } from "../components/charts";
import { DecisionModal } from "../components/DecisionModal";
import { TabGuide } from "../components/TabGuide";
import { fmtNum, fmtPct, fmtUsd, timeAgo } from "../format";
import { seriesColor, theme } from "../theme";
import { realizableTheta, effectiveIvr } from "../optionsHonesty";
import { useState } from "react";
import type { DecisionEntry } from "../api/types";
import ActiveTraderLadder from "../components/ActiveTraderLadder";
import { DynamicGrid, resetGridLayout } from "../components/DynamicGrid";

/** News sentiment (FinBERT, ~[-1,1]) → colored bullish/neutral/bearish badge. */
function NewsBadge({ value }: { value: number | null }) {
  if (value == null) return <span style={{ color: theme.textMuted }}>—</span>;
  const bullish = value > 0.15;
  const bearish = value < -0.15;
  const color = bullish ? theme.growth : bearish ? theme.decline : theme.textMuted;
  const label = bullish ? "Bullish" : bearish ? "Bearish" : "Neutral";
  return (
    <span style={{ color, fontWeight: 700 }}>
      {label} <span className="num">{fmtNum(value, 2)}</span>
    </span>
  );
}

const ACTION_STYLE: Record<string, string> = {
  BUY: "badge-good",
  SELL: "badge-bad",
  HOLD: "badge-neutral",
};

/** BUY/SELL/HOLD → colored badge; anything else (incl. null) → plain "—". */
function ActionBadge({ action }: { action: string | null }) {
  if (!action) return <span style={{ color: theme.textMuted }}>—</span>;
  return (
    <span className={`badge ${ACTION_STYLE[action] ?? "badge-neutral"}`}>
      {action}
    </span>
  );
}

/** A label / value row inside a card (value already formatted, "—" for null). */
function StatRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="row">
      <div className="row-main">
        <span className="row-title" style={{ fontWeight: 500 }}>
          {label}
        </span>
      </div>
      <div className="row-end">
        <div className="num" style={{ fontWeight: 600 }}>
          {value}
        </div>
      </div>
    </div>
  );
}

/**
 * Regime-multiplier sizing impact — Kelly Target before vs. after the HMM
 * regime multiplier + meta-label composite were applied. Ports
 * `gui/panels/strategy_matrix.py::_render_regime_multiplier_impact`, sitting
 * next to the Advisory section's "Kelly target" row: this card explains that
 * number rather than duplicating a symbol picker (the legacy panel's
 * selectbox is redundant here — we're already on one symbol's page).
 *
 * Gates on BOTH pre AND post being non-null (a fix over the legacy panel,
 * which only NaN-checked pre and could render a literal "nan%" for post) and
 * treats every leaf independently — `0` is a real, meaningful value here
 * (e.g. a MetaLabeler hard-gating `meta_label_composite` to 0), never
 * coerced via `??` into looking like "not computed".
 */
function RegimeSizingCard({
  sizing,
  symbol,
}: {
  sizing: SymbolDetailT["sizing"];
  symbol: string;
}) {
  const { kelly_target_pre_regime: pre, kelly_target_post_regime: post, regime_multiplier, meta_label_composite } = sizing;

  if (pre == null || post == null) {
    return (
      <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }} data-testid="regime-sizing-card">
        <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
          <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Regime sizing impact</h2>
        </div>
        <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
          <p style={{ color: theme.textMuted, fontSize: "var(--t-body)", marginTop: "var(--s-2-5)" }}>
            Pre/post-regime Kelly Target breakdown is not available for {symbol}{" "}
            (missing from the latest snapshot, or the strategy engine didn't
            run for this symbol this cycle).
          </p>
        </div>
      </section>
    );
  }

  const deltaPp = (post - pre) * 100;
  const chartData = [
    { label: "Pre-regime", value: +(pre * 100).toFixed(2) },
    { label: "Post-regime", value: +(post * 100).toFixed(2) },
  ];

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }} data-testid="regime-sizing-card">
      <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
        <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Regime sizing impact</h2>
        <p style={{ margin: "var(--s-1) 0 0", fontSize: "var(--t-body)", color: theme.textMuted }}>
          Kelly Target before vs. after the HMM regime multiplier + meta-label
          composite were applied.
        </p>
      </div>
      <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
        <div className="list">
          <StatRow label="Kelly Target (pre-regime)" value={fmtPct(pre, 2, { fromFraction: true })} />
          <StatRow
            label="Kelly Target (post-regime)"
            value={
              <span>
                {fmtPct(post, 2, { fromFraction: true })}{" "}
                <span style={{ color: deltaPp >= 0 ? theme.growth : theme.decline, fontSize: "var(--t-caption)" }}>
                  ({deltaPp >= 0 ? "+" : ""}
                  {deltaPp.toFixed(2)}pp)
                </span>
              </span>
            }
          />
          <StatRow label="HMM regime multiplier" value={regime_multiplier == null ? "—" : regime_multiplier.toFixed(3)} />
        </div>

        <div style={{ height: 160, marginTop: "var(--s-3)" }} data-testid="regime-sizing-chart">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
              <CartesianGrid {...chartGridProps} />
              <XAxis dataKey="label" tick={{ ...chartAxisTick, fontSize: "var(--t-micro)" }} {...chartAxisLine} />
              <YAxis tick={chartAxisTick} {...chartAxisLine} unit="%" />
              <Tooltip
                contentStyle={chartTooltipStyle}
                labelStyle={{ color: theme.textSecondary, fontSize: "var(--t-micro)" }}
                itemStyle={{ fontSize: "var(--t-micro)" }}
                formatter={(v) => `${Number(v).toFixed(2)}%`}
              />
              <Bar dataKey="value" fill={theme.accent} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <p style={{ margin: "var(--s-2-5) 0 0", fontSize: "var(--t-caption)", color: theme.textMuted }} data-testid="regime-sizing-meta-label">
          Meta-label composite currently{" "}
          {meta_label_composite == null ? "—" : meta_label_composite.toFixed(3)}{" "}
          (multiplied in alongside the regime multiplier, then re-clamped to{" "}
          {fmtPct(sizing.max_position_weight, 0, { fromFraction: true })} max
          position weight).
        </p>
      </div>
    </section>
  );
}

/**
 * ForecastErrorChart — per-model RMSE + mean absolute error, grouped bars.
 *
 * Deliberately never renders the bare acronym "MAE" — this codebase already
 * uses "MAE" for a DIFFERENT metric (Maximum Adverse Excursion, a trade-
 * quality figure — see Calibration.tsx and the "MAE" StatRow elsewhere on
 * this same screen). "Mean absolute error" is always spelled out here to
 * avoid colliding with that meaning on the same page.
 *
 * Two series (RMSE, mean absolute error) get distinct SERIES_PALETTE hues —
 * this is categorical identity (which metric is which bar), not a good/bad
 * judgment, so it correctly uses seriesColor rather than growth/decline.
 * Models are pre-sorted ascending by RMSE by the API (best forecaster
 * first); rendered in that order rather than re-sorted here.
 */
function ForecastErrorChart({ rows }: { rows: ForecastModelError[] }) {
  if (rows.length === 0) return null;
  const chartData = rows.map((r) => ({
    label: r.model_name,
    rmse: r.rmse,
    mae: r.mae,
    n: r.n,
  }));
  return (
    <div style={{ marginTop: "var(--s-4)" }} data-testid="forecast-error-chart">
      <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginBottom: "var(--s-1-5)" }}>
        Forecast error by model (lower is better)
      </div>
      <div style={{ height: 200 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
            <CartesianGrid {...chartGridProps} />
            <XAxis dataKey="label" tick={{ ...chartAxisTick, fontSize: "var(--t-micro)" }} {...chartAxisLine} />
            <YAxis tick={chartAxisTick} {...chartAxisLine} tickFormatter={(v: number) => fmtUsd(v)} />
            <Tooltip
              contentStyle={chartTooltipStyle}
              labelStyle={{ color: theme.textSecondary, fontSize: "var(--t-micro)" }}
              itemStyle={{ fontSize: "var(--t-micro)" }}
              formatter={(value, name, entry: { payload?: { n: number } }) => {
                const label = name === "rmse" ? "RMSE" : "Mean absolute error";
                if (typeof value !== "number") return ["—", label];
                const n = entry?.payload?.n;
                return [`${fmtUsd(value)}${n != null ? ` (n=${n})` : ""}`, label];
              }}
            />
            <Legend
              wrapperStyle={{ fontSize: "var(--t-micro)" }}
              formatter={(value: string) => (value === "rmse" ? "RMSE" : "Mean absolute error")}
            />
            <Bar dataKey="rmse" name="rmse" fill={seriesColor(0)} />
            <Bar dataKey="mae" name="mae" fill={seriesColor(1)} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function SymbolDetail() {
  const { ticker = "" } = useParams();
  const nav = useNavigate();

  const { data, loading, error, status, reload } = useApi<SymbolDetailT>(
    () => api.getSymbol(ticker),
    [ticker]
  );
  const [forecastHorizon, setForecastHorizon] = useState(30);
  const forecast = useApi<ForecastSkill>(
    () => api.getForecast(ticker, forecastHorizon),
    [ticker, forecastHorizon]
  );
  const options = useApi<SymbolOptions>(() => api.getSymbolOptions(ticker), [ticker]);
  const rollingBeta = useApi<RollingBeta>(() => api.getRollingBeta(ticker, 60), [ticker]);
  const decisions = useApi<DecisionEntry[]>(
    () => api.getDecisions({ symbol: ticker, limit: 10 }),
    [ticker]
  );

  useAutoPoll(
    () => {
      reload();
      forecast.reload();
      options.reload();
      rollingBeta.reload();
      decisions.reload();
    },
    "signals",
    { hasError: error != null }
  );
  const [journaling, setJournaling] = useState(false);

  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  if (loading) {
    return (
      <div className="screen">
        <BackButton onClick={back} />
        <Loading lines={6} />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="screen">
        <BackButton onClick={back} />
        <ErrorState
          message={error ?? "Not found"}
          status={status}
          onRetry={reload}
        />
      </div>
    );
  }

  const { identity, advisory, factors, ranges, risk, sizing, held_by_pilots } = data;
  const sc = factors.score_components;
  const hasComponents = sc != null && Object.keys(sc).length > 0;

  return (
    <div className="screen">
      <BackButton onClick={back} />

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="screen-title" style={{ marginBottom: "var(--s-0-5)" }}>
            {data.symbol}
          </h1>
          <div style={{ display: "flex", gap: "var(--s-2)", alignItems: "center" }}>
            {identity.sector && <span className="chip">{identity.sector}</span>}
            <ActionBadge action={identity.action} />
            <span style={{ fontSize: "var(--t-caption)", color: theme.textMuted }}>
              as of {timeAgo(data.as_of)}
            </span>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "var(--s-2)" }}>
          <div className="num" style={{ fontSize: 26, fontWeight: 800, letterSpacing: "-0.02em" }}>
            {fmtUsd(identity.price)}
          </div>
          <Button variant="neutral" onClick={() => resetGridLayout("symbolDetail")}>Reset Layout</Button>
        </div>
      </div>

      {data.reason && (
        <p style={{ color: theme.textMuted, fontSize: "var(--t-body)", marginTop: "var(--s-2-5)" }}>{data.reason}</p>
      )}

      <TabGuide tabKey="symbol-detail" />

      {/* Advisory */}
      <DynamicGrid
        layoutKey="symbolDetail"
        defaultLayouts={{
          lg: [
            { i: "advisory", x: 0, y: 0, w: 4, h: 4, minW: 3, minH: 3 },
            { i: "regime", x: 4, y: 0, w: 4, h: 4, minW: 3, minH: 3 },
            { i: "snapshot", x: 8, y: 0, w: 4, h: 4, minW: 3, minH: 3 },
            { i: "factors", x: 0, y: 4, w: 4, h: 4, minW: 3, minH: 3 },
            { i: "risk", x: 4, y: 4, w: 4, h: 4, minW: 3, minH: 3 },
            { i: "tactical", x: 8, y: 4, w: 4, h: 4, minW: 3, minH: 3 },
            { i: "rolling_beta", x: 0, y: 8, w: 6, h: 4, minW: 4, minH: 3 },
            { i: "forecast", x: 6, y: 8, w: 6, h: 4, minW: 4, minH: 3 },
            { i: "options", x: 0, y: 12, w: 4, h: 4, minW: 3, minH: 3 },
            { i: "decision", x: 4, y: 12, w: 8, h: 4, minW: 4, minH: 3 },
            { i: "claude", x: 0, y: 16, w: 4, h: 5, minW: 3, minH: 4 },
            { i: "gemini", x: 4, y: 16, w: 4, h: 5, minW: 3, minH: 4 },
            { i: "opal", x: 8, y: 16, w: 4, h: 5, minW: 3, minH: 4 },
            { i: "pilots", x: 0, y: 21, w: 4, h: 4, minW: 3, minH: 3 },
          ],
        }}
      >
        <div key="advisory">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
            <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
              <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Advisory</h2>
            </div>
            <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
        <div className="list">
          <StatRow label="Recommendation" value={<ActionBadge action={advisory.action} />} />
          <StatRow
            label="Conviction"
            value={fmtPct(advisory.conviction, 0, { fromFraction: true })}
          />
          <StatRow
            label="Suggested position"
            value={fmtPct(advisory.position_pct, 1, { fromFraction: true })}
          />
          <StatRow
            label="Kelly target"
            value={fmtPct(advisory.kelly_target, 1, { fromFraction: true })}
          />
          <StatRow label="Score" value={fmtNum(advisory.score, 1)} />
        </div>
            {advisory.rationale && (
              <p style={{ color: theme.textSecondary, fontSize: 13.5, lineHeight: 1.5, marginTop: "var(--s-3)" }}>
                {advisory.rationale}
              </p>
            )}
            </div>
          </section>
        </div>

        <div key="regime">
          <RegimeSizingCard sizing={sizing} symbol={data.symbol} />
        </div>

        <div key="decision">
          {/* Decision journal — per-symbol log of what the operator actually did
              with this signal. Shared DecisionModal with the Calibration screen's
              portfolio-wide journal (../components/DecisionModal); this section
              is the standalone, symbol-scoped read (GET /decisions?symbol=...)
              Calibration's bundled recent-decisions preview doesn't offer. */}
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
            <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Decision journal</h2>
              <Button
                variant="neutral"
                onClick={() => setJournaling(true)}
                onMouseDown={(e) => e.stopPropagation()}
                onTouchStart={(e) => e.stopPropagation()}
              >
                Log decision
              </Button>
            </div>
            <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
        {decisions.loading && <Loading lines={2} />}
        {!decisions.loading && (!decisions.data || decisions.data.length === 0) && (
          <p style={{ color: theme.textMuted, fontSize: "var(--t-body)", marginTop: "var(--s-2-5)" }}>
            No decisions logged yet for {data.symbol}.
          </p>
        )}
        {!decisions.loading && decisions.data && decisions.data.length > 0 && (
          <div className="list" style={{ marginTop: "var(--s-2)" }}>
            {decisions.data.map((d, i) => (
              <div key={`${d.timestamp}-${i}`} className="row">
                <div className="row-main">
                  <span className="row-title" style={{ fontWeight: 500 }}>
                    {d.action_taken === "acted" ? "✅ Acted" : d.action_taken === "passed" ? "⏭ Passed" : "🔁 Modified"}
                  </span>
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
            ))}
          </div>
            )}
            </div>
          </section>
        </div>

      {journaling && (
        <DecisionModal
          signal={{ symbol: data.symbol, action: advisory.action, conviction: advisory.conviction }}
          onClose={() => setJournaling(false)}
          onLogged={decisions.reload}
        />
      )}

        <div key="snapshot">
          {/* Identity */}
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
            <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
              <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Snapshot</h2>
            </div>
            <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
              <div className="list">
                <StatRow label="Sector" value={identity.sector ?? "—"} />
                <StatRow label="Price" value={fmtUsd(identity.price)} />
                <StatRow label="Signal action" value={<ActionBadge action={identity.action} />} />
                <StatRow
                  label="Shares held"
                  value={identity.shares == null ? "—" : fmtNum(identity.shares, 0)}
                />
              </div>
            </div>
          </section>
        </div>

        <div key="tactical">
          {/* Tactical ranges (pre-formatted strings, NOT tuples) */}
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
            <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
              <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Tactical ranges</h2>
            </div>
            <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
              <div className="list">
                <StatRow label="Buy" value={ranges.buy_range ?? "—"} />
                <StatRow label="Sell" value={ranges.sell_range ?? "—"} />
              </div>
            </div>
          </section>
        </div>

        <div key="factors">
          {/* Factors */}
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
            <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
              <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Factor exposure</h2>
            </div>
            <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
              <div className="list">
                <StatRow label="Value (z)" value={fmtNum(factors.value_z, 2)} />
                <StatRow label="Quality (z)" value={fmtNum(factors.quality_z, 2)} />
                <StatRow label="Low-vol (z)" value={fmtNum(factors.lowvol_z, 2)} />
                <StatRow label="Size (z)" value={fmtNum(factors.size_z, 2)} />
                <StatRow
                  label="Multifactor composite"
                  value={fmtNum(factors.multifactor_composite, 2)}
                />
                <StatRow label="12-1m momentum" value={fmtNum(factors.xsec_12_1m, 2)} />
                <StatRow
                  label="Momentum rank"
                  value={fmtPct(factors.xsec_momentum_rank, 0, { fromFraction: true })}
                />
              </div>
              {hasComponents && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", marginTop: "var(--s-3)" }}>
                  {Object.entries(sc!).map(([k, v]) => (
                    <MetricBadge key={k} label={k} value={fmtNum(v, 2)} />
                  ))}
                </div>
              )}
            </div>
          </section>
        </div>

        <div key="risk">
          {/* Risk */}
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
            <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
              <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Risk & regime</h2>
            </div>
            <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
              <div className="list">
                <StatRow
                  label="Regime"
                  value={
                    risk.hmm_risk_on == null ? (
                      <span style={{ color: theme.textMuted }}>—</span>
                    ) : (
                      <span
                        className={`badge ${risk.hmm_risk_on >= 0.5 ? "badge-good" : "badge-bad"}`}
                      >
                        {risk.hmm_risk_on >= 0.5 ? "Risk-on" : "Risk-off"}{" "}
                        {fmtPct(risk.hmm_risk_on, 0, { fromFraction: true })}
                      </span>
                    )
                  }
                />
                <StatRow label="Macro status" value={risk.macro_status ?? "—"} />
                <StatRow label="News sentiment" value={<NewsBadge value={risk.news_sentiment} />} />
                <StatRow label="CoVaR proxy" value={fmtNum(risk.covar_proxy, 2)} />
                <StatRow label="Realized slippage" value={fmtNum(risk.realized_slippage, 4)} />
                <StatRow label="MFE" value={fmtNum(risk.mfe, 2)} />
                <StatRow label="MAE" value={fmtNum(risk.mae, 2)} />
                <StatRow label="Edge ratio" value={fmtNum(risk.edge_ratio, 2)} />
              </div>
            </div>
          </section>
        </div>

        <div key="rolling_beta">
          {/* Rolling beta vs SPY — time-varying, distinct from the static point-in-time beta */}
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
            <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
              <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Rolling beta vs SPY</h2>
            </div>
            <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
              {rollingBeta.loading ? (
                <Loading lines={2} />
              ) : !rollingBeta.data || rollingBeta.data.series.length === 0 ? (
                <div className="empty" style={{ padding: "var(--s-4-5)" }}>
                  {rollingBeta.data?.reason ?? "No cached price history yet."}
                </div>
              ) : (
                <>
                  <PerfLine
                    data={rollingBeta.data.series.map((p) => ({ date: p.date, value: p.beta }))}
                    valueLabel="Beta"
                    yTickDecimals={1}
                  />
                  <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-2)" }}>
                    {rollingBeta.data.window}-day rolling beta — latest:{" "}
                    <span className="num" style={{ fontWeight: 700, color: theme.textSecondary }}>
                      {fmtNum(
                        rollingBeta.data.series[rollingBeta.data.series.length - 1].beta,
                        2
                      )}
                    </span>
                  </p>
                </>
              )}
            </div>
          </section>
        </div>

        <div key="forecast">
          {/* Forecast reliability + model skill weights */}
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
            <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "baseline",
                  flexWrap: "wrap",
                  gap: "var(--s-2)",
                }}
              >
                <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Forecast skill</h2>
                <div
                  className="segmented"
                  role="tablist"
                  aria-label="Forecast horizon"
                  onMouseDown={(e) => e.stopPropagation()}
                  onTouchStart={(e) => e.stopPropagation()}
                >
                  {[10, 30, 60, 90].map((h) => (
                    <button
                      key={h}
                      role="tab"
                      aria-selected={h === forecastHorizon}
                      className={h === forecastHorizon ? "on" : ""}
                      onClick={() => setForecastHorizon(h)}
                    >
                      {h}d
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
              {forecast.loading ? (
                <Loading lines={1} />
              ) : !forecast.data || forecast.data.reason ? (
                <div className="empty" style={{ padding: "var(--s-4-5)" }}>
                  {forecast.data?.reason ?? "No forecast history yet."}
                </div>
              ) : (
                <>
                  <div className="list">
                    <StatRow label="Completed forecasts" value={forecast.data.completed} />
                    <StatRow label="Pending" value={forecast.data.pending} />
                  </div>
                  {Object.keys(forecast.data.skill_weights).length > 0 && (
                    <>
                      <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", margin: "var(--s-3) 0 var(--s-1-5)" }}>
                        Model skill weights (inverse-RMSE)
                      </div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)" }}>
                        {Object.entries(forecast.data.skill_weights).map(([m, w]) => (
                          <MetricBadge
                            key={m}
                            label={m}
                            value={fmtPct(w, 0, { fromFraction: true })}
                          />
                        ))}
                      </div>
                    </>
                  )}
                  <ForecastErrorChart rows={forecast.data.error_by_model} />
                  {forecast.data.error_by_model.some((r) => r.model_name.startsWith("lstm_") || r.model_name === "bert_lla") && (
                    <p style={{ color: theme.textMuted, fontSize: "var(--t-micro)", marginTop: "var(--s-1-5)", lineHeight: 1.5 }}>
                      lstm_baseline / lstm_attention / bert_lla are three ablations
                      of one architecture (dual-layer LSTM, with and without
                      self-attention and sentiment) — a direct comparison, not
                      three unrelated models.
                    </p>
                  )}
                </>
              )}
            </div>
          </section>
        </div>

        <div key="options">
          {/* Options premium directive (persisted matrix; advisory) */}
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
            <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
              <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Options premium</h2>
            </div>
            <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
              {options.loading ? (
                <Loading lines={1} />
              ) : !options.data || !options.data.directive ? (
                <div className="empty" style={{ padding: "var(--s-4-5)" }}>
                  {options.data?.reason ?? "No options directive for this symbol yet."}
                </div>
              ) : (
                <OptionsDirectiveView d={options.data.directive} />
              )}
            </div>
          </section>
        </div>

        {/* On-demand AI generation — Claude analyst note, Gemini chart-pattern
            read, Opal research brief. Each is operator-triggered only (never
            generated automatically) and fully independent: one card failing or
            being disabled never blocks the other two. */}
        <div key="claude">
          <CommentaryCard symbol={data.symbol} />
        </div>
        <div key="gemini">
          <ChartReadCard symbol={data.symbol} />
        </div>
        <div key="opal">
          <ResearchBriefCard symbol={data.symbol} />
        </div>

        <div key="pilots">
          {/* Held by Pilots — the Stockpy reverse cross-link */}
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
            <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
              <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>
                Held by Pilots{" "}
                <span style={{ color: theme.textMuted }}>({held_by_pilots.length})</span>
              </h2>
            </div>
            <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
              {held_by_pilots.length === 0 ? (
                <div className="empty" style={{ padding: "var(--s-5)" }}>
                  No Pilots currently hold {data.symbol}.
                </div>
              ) : (
                <div className="list">
                  {held_by_pilots.map((hp) => (
                    <Link className="row" key={hp.pilot_id} to={`/pilots/${hp.pilot_id}`}>
                      <div className="row-main">
                        <span className="row-title">{hp.name}</span>
                        <span className="row-sub">{hp.pilot_id}</span>
                      </div>
                      <div className="row-end">
                        <div className="num" style={{ fontWeight: 700 }}>
                          {fmtPct(hp.weight, 1, { fromFraction: true })}
                        </div>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          </section>
        </div>
      </DynamicGrid>

      <div style={{ marginTop: "var(--s-8)", marginBottom: "var(--s-4)" }}>
        <ActiveTraderLadder symbol={data.symbol} currentPrice={identity.price} />
      </div>
    </div>
  );
}

/** Renders one persisted options premium directive (advisory, read-only). */
function OptionsDirectiveView({ d }: { d: OptionsDirective }) {
  const legOk = d.Integrity_OK === true;
  const theta = realizableTheta(d);
  const ivr = effectiveIvr(d);
  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-2)" }}>
        <div style={{ fontWeight: 700 }}>{d.Strategy ?? "—"}</div>
        <span className={`badge ${legOk ? "badge-good" : "badge-bad"}`}>
          {legOk ? "Integrity ✓" : "Integrity ✗"}
        </span>
      </div>
      <div className="list">
        <StatRow label="Action" value={d.Action ?? "—"} />
        <StatRow label="Trend bias" value={d.Trend_Bias ?? "—"} />
        <StatRow label="Net premium" value={fmtUsd(d.Net_Premium ?? null)} />
        <StatRow
          label="Realizable θ/day"
          value={theta.note ? "—" : fmtUsd(theta.value)}
        />
        <StatRow
          label="Short strike / Δ"
          value={`${fmtUsd(d.Short_Strike ?? null)} / ${fmtNum(d.Short_Delta ?? null, 2)}`}
        />
        <StatRow
          label="Long strike / Δ"
          value={`${fmtUsd(d.Long_Strike ?? null)} / ${fmtNum(d.Long_Delta ?? null, 2)}`}
        />
        <StatRow label="GARCH σ" value={fmtNum(d.Sigma_GARCH ?? null, 3)} />
        <StatRow
          label={ivr.isTrue ? "IVR (chain)" : "IVR (proxy)"}
          value={fmtNum(ivr.value, 1)}
        />
      </div>
    </>
  );
}

// ---- On-demand AI generation cards -----------------------------------------
// Operator-facing copy per honest `reason` — never a generic "error"; each
// message names the specific env var / condition the backend reported so an
// operator knows exactly what to do next.

const COMMENTARY_REASON_COPY: Record<NonNullable<AiCommentaryResponse["reason"]>, string> = {
  disabled: "Claude commentary is off. An operator can enable it via LLM_COMMENTARY_ENABLED in .env.",
  missing_key: "Claude commentary is enabled, but ANTHROPIC_API_KEY is not configured.",
  generation_failed: "Claude couldn't generate a note for this symbol right now — try again.",
};

const CHART_REASON_COPY: Record<NonNullable<AiChartResponse["reason"]>, string> = {
  disabled: "Gemini chart reads are off. An operator can enable it via LLM_COMMENTARY_ENABLED in .env.",
  missing_key: "Gemini chart reads are enabled, but GEMINI_API_KEY is not configured.",
  no_bars: "Not enough cached price history to render a chart for this symbol yet.",
  chart_render_failed: "The chart couldn't be rendered for this symbol right now — try again.",
  generation_failed: "The chart rendered, but Gemini couldn't generate a pattern read for it right now — try again.",
};

const RESEARCH_REASON_COPY: Record<NonNullable<AiResearchResponse["reason"]>, string> = {
  disabled: "Opal research briefs are off. An operator can enable it via OPAL_RESEARCH_ENABLED in .env.",
  generation_failed: "Opal couldn't generate a research brief for this symbol right now — try again.",
};

/** Honest empty/disabled-state box — reused by all three AI cards. */
function ReasonNotice({ text }: { text: string }) {
  return (
    <div className="empty" style={{ padding: "var(--s-4-5)", marginTop: "var(--s-3)" }}>
      {text}
    </div>
  );
}

/** Small labelled bullet list — reused for key_risks / support / resistance /
 * catalysts / risk_factors / recent_developments. Renders nothing for an
 * empty list rather than an empty heading (CONSTRAINT #4 — several of these
 * fields may legitimately be empty, not every empty case is an error). */
function BulletList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div style={{ marginTop: "var(--s-2-5)" }}>
      <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginBottom: "var(--s-1)" }}>{title}</div>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {items.map((it, i) => (
          <li
            key={i}
            style={{ fontSize: 13.5, lineHeight: 1.5, color: theme.textSecondary, marginBottom: "var(--s-0-5)" }}
          >
            {it}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Shared header row: title + Generate button (disabled/spinner while pending). */
function AiCardHeader({
  title,
  subtitle,
  pending,
  onGenerate,
}: {
  title: string;
  subtitle: string;
  pending: boolean;
  onGenerate: () => void;
}) {
  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--s-3)" }}>
        <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>{title}</h2>
        <Button
          variant="neutral"
          pending={pending}
          onClick={onGenerate}
          onMouseDown={(e) => e.stopPropagation()}
          onTouchStart={(e) => e.stopPropagation()}
        >
          Generate
        </Button>
      </div>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", margin: "var(--s-1) 0 0" }}>{subtitle}</p>
    </>
  );
}

/** Claude analyst-grade narrative: headline / why-now / key risks / invalidation. */
function CommentaryCard({ symbol }: { symbol: string }) {
  const mutation = useMutation(() => api.generateCommentary(symbol));
  const data = mutation.result;

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
      <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
        <AiCardHeader
          title="Claude analyst note"
          subtitle={`On-demand Claude narrative for ${symbol} — not generated automatically.`}
          pending={mutation.pending}
          onGenerate={() => mutation.run()}
        />
      </div>
      <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
      {mutation.error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-3)" }}>
          <span>{mutation.error}</span>
        </Notice>
      )}
      {data && !data.available && (
        <ReasonNotice
          text={
            data.reason
              ? COMMENTARY_REASON_COPY[data.reason]
              : "Claude couldn't generate a note for this symbol right now — try again."
          }
        />
      )}
      {data?.available && data.payload && (
        <div style={{ marginTop: "var(--s-3)" }}>
          <div style={{ fontWeight: 700, fontSize: 14.5 }}>{data.payload.headline}</div>
          <p style={{ color: theme.textSecondary, fontSize: 13.5, lineHeight: 1.5, marginTop: "var(--s-2)" }}>
            {data.payload.why_now}
          </p>
          <BulletList title="Key risks" items={data.payload.key_risks} />
          <p style={{ color: theme.textMuted, fontSize: "var(--t-label)", marginTop: "var(--s-2-5)", lineHeight: 1.5 }}>
            <strong style={{ color: theme.textSecondary }}>Invalidation:</strong>{" "}
            {data.payload.invalidation}
          </p>
        </div>
      )}
      </div>
    </section>
  );
}

/** Gemini Vision chart-pattern read. Renders the chart image whenever
 * `chart_png_base64` is present, independent of `available` — the chart can
 * render fine even when the AI narrative failed. */
function ChartReadCard({ symbol }: { symbol: string }) {
  const mutation = useMutation(() => api.generateChart(symbol));
  const data = mutation.result;

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
      <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
        <AiCardHeader
          title="Gemini chart read"
          subtitle={`On-demand chart-pattern read for ${symbol} — not generated automatically.`}
          pending={mutation.pending}
          onGenerate={() => mutation.run()}
        />
      </div>
      <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
      {mutation.error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-3)" }}>
          <span>{mutation.error}</span>
        </Notice>
      )}
      {data?.chart_png_base64 && (
        <img
          src={`data:image/png;base64,${data.chart_png_base64}`}
          alt={`${symbol} price chart`}
          style={{ width: "100%", borderRadius: "var(--r-md)", marginTop: "var(--s-3)", display: "block" }}
        />
      )}
      {data?.available && data.payload && (
        <div style={{ marginTop: "var(--s-3)" }}>
          <div className="list">
            <StatRow label="Pattern" value={data.payload.pattern_name} />
            <StatRow label="Trend" value={data.payload.trend_direction} />
            <StatRow label="Confidence" value={data.payload.confidence} />
          </div>
          <BulletList title="Support" items={data.payload.support_levels} />
          <BulletList title="Resistance" items={data.payload.resistance_levels} />
          <p style={{ color: theme.textSecondary, fontSize: 13.5, lineHeight: 1.5, marginTop: "var(--s-2-5)" }}>
            {data.payload.narrative}
          </p>
        </div>
      )}
      {data && !data.available && (
        <ReasonNotice
          text={
            data.reason
              ? CHART_REASON_COPY[data.reason]
              : "Gemini couldn't generate a chart read for this symbol right now — try again."
          }
        />
      )}
      </div>
    </section>
  );
}

/** Opal (OpenAI/Gemini) grounded research brief — qualitative-only, sourced
 * from real retrieved news/earnings. */
function ResearchBriefCard({ symbol }: { symbol: string }) {
  const mutation = useMutation(() => api.generateResearch(symbol));
  const data = mutation.result;

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
      <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
        <AiCardHeader
          title="Opal research brief"
          subtitle={`On-demand grounded research brief for ${symbol} — not generated automatically.`}
          pending={mutation.pending}
          onGenerate={() => mutation.run()}
        />
      </div>
      <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
      {mutation.error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-3)" }}>
          <span>{mutation.error}</span>
        </Notice>
      )}
      {data && !data.available && (
        <ReasonNotice
          text={
            data.reason
              ? RESEARCH_REASON_COPY[data.reason]
              : "Opal couldn't generate a research brief for this symbol right now — try again."
          }
        />
      )}
      {data?.available && data.payload && (
        <div style={{ marginTop: "var(--s-3)" }}>
          <p style={{ color: theme.textSecondary, fontSize: 13.5, lineHeight: 1.5 }}>
            {data.payload.thesis_context}
          </p>
          <BulletList title="Catalysts" items={data.payload.catalysts} />
          <BulletList title="Risk factors" items={data.payload.risk_factors} />
          <BulletList title="Recent developments" items={data.payload.recent_developments} />
          <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-2-5)" }}>
            {data.payload.sources_note}
          </p>
        </div>
      )}
      </div>
    </section>
  );
}

function BackButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        background: "none",
        border: "none",
        padding: 0,
        cursor: "pointer",
        color: theme.textSecondary,
        fontSize: "var(--t-callout)",
        display: "inline-block",
        marginBottom: "var(--s-2)",
      }}
    >
      ← Back
    </button>
  );
}
