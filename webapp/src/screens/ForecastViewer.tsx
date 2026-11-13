/**
 * ForecastViewer.tsx — the Forecasts screen (forecasting rebuild F6b).
 *
 * Reads only what the pipeline PERSISTED (forecasting rebuild F6a routes):
 *   - GET /forecasts — the universe table: per symbol and horizon the published
 *     forecast, its Monte-Carlo band, the "published naive" / "fallback"
 *     disclosures, forecast z (F5; null until built), and what the forecast
 *     changed this cycle.
 *   - GET /symbols/{t}/forecast-summary — the symbol's ledger: skill vs naive
 *     per model, the naive gate's last decision and its evaluation now, and MC
 *     band coverage.
 * It never refits a model (the old per-request GET /metrics/forecast/{symbol}
 * refit is no longer called). Gate numbers come from the API response, which
 * reads the live settings, never from a literal here. Every null renders as
 * "—" or "unavailable" with the backend's reason (CONSTRAINT #4), never 0.
 *
 * `?symbol=` selects the symbol panel (links in from Symbol Detail and the
 * Model Health panel); without it the first universe row is shown.
 */
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api } from "../api/client";
import type {
  Bar,
  ForecastGateSettings,
  ForecastHorizonCell,
  ForecastRow,
  ForecastsView,
  ForecastSummary,
  ForecastSummaryHorizon,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { EmptyState, ErrorState, Loading, Notice, StaleDataNotice, Table } from "../components/ui";
import { ForecastCandleChart } from "../components/charts";
import { SymbolInput } from "../components/SymbolInput";
import { TabGuide } from "../components/TabGuide";
import { fmtNum, fmtPct, fmtUsd, timeAgo } from "../format";
import { theme } from "../theme";

const DASH = "—";
const HORIZONS = [10, 30, 60, 90] as const;

const LOOKBACK_RANGES: { label: string; days: number }[] = [
  { label: "1M", days: 21 },
  { label: "3M", days: 63 },
  { label: "6M", days: 126 },
  { label: "1Y", days: 252 },
];
const DEFAULT_LOOKBACK_DAYS = 63; // 3M

/** Plain-English labels for engine/advisory.py's Recommendation.forecast_effects tags. */
export const ADVISORY_EFFECT_LABELS: Record<string, string> = {
  case_a_exit: "Case A: a loss plus a bearish 30-day forecast escalated the SELL to a full exit",
  case_c_hold: "Case C: a gain plus a forecast that is neither bullish nor bearish turned a BUY into a HOLD",
  bullish_conviction_boost: "A bullish forecast raised the BUY's conviction",
};

const captionStyle = { color: theme.textMuted, fontSize: "var(--t-caption)" } as const;

function pctVsPrice(value: number | null, price: number | null): number | null {
  if (value == null || price == null || price <= 0) return null;
  return ((value - price) / price) * 100;
}

function HorizonBadges({ cell }: { cell: ForecastHorizonCell }) {
  return (
    <>
      {cell.gated_naive === true && (
        <span
          className="badge badge-warn"
          title="The naive gate admitted no model, so the published forecast is the naive one (price stays flat)."
        >
          naive
        </span>
      )}
      {cell.is_fallback === true && cell.gated_naive !== true && (
        <span className="badge badge-warn" title="The forecasting models did not converge; this is a fallback forecast.">
          fallback
        </span>
      )}
    </>
  );
}

function bandText(cell: ForecastHorizonCell): string {
  return cell.lower != null && cell.upper != null
    ? `${fmtNum(cell.lower, 2)} – ${fmtNum(cell.upper, 2)}`
    : "no band";
}

function GateLine({ gate }: { gate: ForecastGateSettings }) {
  return (
    <div style={captionStyle} data-testid="forecast-gate-line">
      <strong style={{ color: theme.textSecondary }}>Naive gate: {gate.enabled ? "on" : "shadow (off)"}</strong>
      {" — "}
      {gate.enabled
        ? "the published forecast is the gated blend: only models that beat naive are blended, else naive is published."
        : "the published forecast is the plain blend; the gate's decisions are recorded but not used."}{" "}
      Admits a model whose median log error is at least{" "}
      {fmtPct(gate.min_improvement, 1, { fromFraction: true })} lower than naive's, on at least {fmtNum(gate.min_obs, 0)} scored
      pairs over{" "}
      {gate.window_days == null ? DASH : `${gate.window_days} days`}.
    </div>
  );
}

function UniverseTable({
  view,
  selected,
  onSelect,
}: {
  view: ForecastsView;
  selected: string | null;
  onSelect: (sym: string) => void;
}) {
  if (view.rows.length === 0) {
    return <EmptyState title="No published forecasts" hint={view.reason ?? "The latest snapshot has no symbols."} />;
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <Table aria-label="Published forecasts">
        <thead>
          <tr>
            <th>Symbol</th>
            <th className="num">Price</th>
            {HORIZONS.map((h) => (
              <th key={h} className="num">
                {h}d
              </th>
            ))}
            <th className="num" title="forecast_alignment points in the composite score">
              Forecast pts
            </th>
            <th>Tier (without forecast)</th>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((r) => (
            <tr
              key={r.symbol}
              onClick={() => onSelect(r.symbol)}
              style={{ cursor: "pointer", background: r.symbol === selected ? "var(--surface-2)" : undefined }}
              aria-selected={r.symbol === selected}
            >
              <td>
                <button
                  type="button"
                  style={{ background: "none", border: "none", padding: 0, color: "var(--accent)", cursor: "pointer", fontWeight: 600 }}
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelect(r.symbol);
                  }}
                >
                  {r.symbol}
                </button>
                {r.held && <span style={{ ...captionStyle, marginLeft: 6 }}>held</span>}
              </td>
              <td className="num">{fmtUsd(r.price)}</td>
              {HORIZONS.map((h) => {
                const cell = r.horizons[String(h)];
                if (!cell || cell.forecast == null) {
                  return (
                    <td key={h} className="num" title="not recorded on this snapshot">
                      {DASH}
                    </td>
                  );
                }
                const pct = pctVsPrice(cell.forecast, r.price);
                return (
                  <td key={h} className="num" title={`band ${bandText(cell)}`}>
                    <div>{fmtNum(cell.forecast, 2)}</div>
                    <div style={captionStyle}>
                      {pct == null ? DASH : fmtPct(pct, 1, { signed: true })} <HorizonBadges cell={cell} />
                    </div>
                  </td>
                );
              })}
              <td className="num">
                {r.impact.alignment_points == null ? (
                  <span title="not recorded on this snapshot">{DASH}</span>
                ) : (
                  fmtNum(r.impact.alignment_points, 1)
                )}
              </td>
              <td>
                {r.impact.action_tier ?? DASH}
                {r.impact.action_tier_without_forecast != null && (
                  <span style={{ ...captionStyle, marginLeft: 4 }}>
                    ({r.impact.action_tier_without_forecast}
                    {r.impact.tier_changed_by_forecast ? ", changed" : ""})
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  );
}

function HorizonCards({ row }: { row: ForecastRow }) {
  const zReason = HORIZONS.map((h) => row.horizons[String(h)]?.z_reason).find((r) => r != null) ?? null;
  return (
    <>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: "var(--s-2)",
          marginBottom: "var(--s-2)",
        }}
      >
        {HORIZONS.map((h) => {
          const cell = row.horizons[String(h)];
          const pct = cell ? pctVsPrice(cell.forecast, row.price) : null;
          return (
            <div key={h} className="tile" data-testid={`forecast-horizon-${h}`}>
              <div className="tile-label">{h}d published</div>
              <div className="tile-value num">{cell?.forecast == null ? DASH : fmtNum(cell.forecast, 2)}</div>
              <div style={captionStyle}>
                {cell?.forecast == null ? (
                  "not recorded"
                ) : (
                  <>
                    {pct == null ? DASH : fmtPct(pct, 1, { signed: true })} · {bandText(cell)}
                  </>
                )}
              </div>
              {cell && (
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 4 }}>
                  <HorizonBadges cell={cell} />
                </div>
              )}
              <div style={captionStyle} title={cell?.z_reason ?? undefined}>
                z {cell?.z == null ? "unavailable" : fmtNum(cell.z, 2)} · E {cell?.err == null ? DASH : fmtNum(cell.err, 3)} · n{" "}
                {cell?.err_n == null ? DASH : cell.err_n}
              </div>
            </div>
          );
        })}
      </div>
      {zReason && (
        <p style={{ ...captionStyle, margin: "0 0 var(--s-3)" }} data-testid="forecast-z-reason">
          Forecast z, E and n: unavailable — {zReason}.
        </p>
      )}
    </>
  );
}

function ImpactCard({ row }: { row: ForecastRow }) {
  const i = row.impact;
  const effects =
    i.advisory_effects == null
      ? "No advisory recommendation recorded for this symbol this cycle (the advisory overlay writes these in the daemon's primary queue mode)."
      : i.advisory_effects.length === 0
      ? "None fired."
      : null;
  const sellText =
    i.sell_hi_from_forecast === true
      ? "The sell-zone top came from the 30-day forecast."
      : i.sell_hi_from_forecast === false
      ? "The sell-zone top came from the ATR leg; the forecast did not raise it."
      : "Not derivable: no active sell zone, or no price.";
  return (
    <section className="card card-pad" data-testid="forecast-impact">
      <h2 style={{ fontSize: "var(--t-subhead)", margin: "0 0 var(--s-2)" }}>What the forecast changed this cycle</h2>
      <div className="list">
        <div className="row" style={{ display: "flex", justifyContent: "space-between", gap: "var(--s-2)", flexWrap: "wrap" }}>
          <span>Forecast points in the score</span>
          <span className="num">
            {i.alignment_points == null ? "not recorded" : `${i.alignment_points > 0 ? "+" : ""}${fmtNum(i.alignment_points, 1)}`}
            {i.score != null && <span style={captionStyle}> of {fmtNum(i.score, 1)}</span>}
          </span>
        </div>
        <div className="row" style={{ display: "flex", justifyContent: "space-between", gap: "var(--s-2)", flexWrap: "wrap" }}>
          <span>Action tier</span>
          <span>
            {i.action_tier ?? DASH} · without the forecast: {i.action_tier_without_forecast ?? "unavailable"}
          </span>
        </div>
      </div>
      <p style={{ ...captionStyle, margin: "var(--s-1-5) 0 var(--s-3)" }}>Tier without the forecast is {i.tier_note}.</p>
      <div style={{ fontWeight: 600, fontSize: "var(--t-callout)" }}>Advisory rules the forecast fired</div>
      {effects ? (
        <p style={{ ...captionStyle, margin: "var(--s-1) 0 var(--s-3)" }}>{effects}</p>
      ) : (
        <ul style={{ margin: "var(--s-1) 0 var(--s-3)", paddingLeft: "var(--s-4)", fontSize: "var(--t-callout)" }}>
          {(i.advisory_effects ?? []).map((e) => (
            <li key={e}>{ADVISORY_EFFECT_LABELS[e] ?? e}</li>
          ))}
        </ul>
      )}
      <div style={{ fontWeight: 600, fontSize: "var(--t-callout)" }}>Sell zone</div>
      <p style={{ ...captionStyle, margin: "var(--s-1) 0 0" }}>
        {sellText}
        {i.sell_range && <> ({i.sell_range})</>}
      </p>
    </section>
  );
}

function SkillTable({ cell }: { cell: ForecastSummaryHorizon }) {
  if (cell.skill.length === 0) {
    return <p style={captionStyle}>No skill rows: {cell.skill_reason ?? "unavailable"}.</p>;
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <Table aria-label="Skill vs naive">
        <thead>
          <tr>
            <th>Model</th>
            <th className="num">n</th>
            <th className="num" title="median |ln(forecast / actual)|">
              Median |log err|
            </th>
            <th className="num">Naive's</th>
            <th className="num">Beats naive</th>
            <th className="num" title="two-sided sign test on wins vs losses">
              Sign p
            </th>
            <th className="num">Direction hit</th>
          </tr>
        </thead>
        <tbody>
          {cell.skill.map((s) => (
            <tr key={s.model}>
              <td>{s.model}</td>
              <td className="num">{s.n == null ? DASH : s.n}</td>
              <td className="num">{fmtNum(s.median_abs_log_error, 4)}</td>
              <td className="num">{fmtNum(s.naive_median_abs_log_error, 4)}</td>
              <td className="num">{fmtPct(s.pct_beating_naive, 0, { fromFraction: true })}</td>
              <td className="num">{fmtNum(s.sign_test_p, 2)}</td>
              <td className="num">{fmtPct(s.direction_hit_rate, 0, { fromFraction: true })}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  );
}

function GateBlock({ cell }: { cell: ForecastSummaryHorizon }) {
  const { last_decision: last, evaluation: ev, reason } = cell.gate;
  const rejected = ev ? Object.entries(ev.rejected) : [];
  return (
    <div data-testid="forecast-gate-block">
      <div style={{ fontWeight: 600, fontSize: "var(--t-callout)" }}>Naive gate</div>
      <p style={{ margin: "var(--s-1) 0", fontSize: "var(--t-callout)" }}>
        <span style={captionStyle}>Last recorded decision: </span>
        {last == null
          ? `none recorded${reason ? ` (${reason})` : ""}`
          : last.fallback_to_naive === true
          ? `${last.forecast_day ?? DASH}: no model admitted (fallback to naive)`
          : last.admitted.length > 0
          ? `${last.forecast_day ?? DASH}: admitted ${last.admitted.join(", ")}`
          : `${last.forecast_day ?? DASH}: admitted none`}
      </p>
      <p style={{ margin: "var(--s-1) 0", fontSize: "var(--t-callout)" }}>
        <span style={captionStyle}>Evaluation now: </span>
        {ev == null
          ? `unavailable${reason ? ` (${reason})` : ""}`
          : ev.fallback_to_naive
          ? "admits no model (would fall back to naive)"
          : `admits ${ev.admitted.join(", ")}`}
      </p>
      {rejected.length > 0 && (
        <ul style={{ margin: "var(--s-1) 0", paddingLeft: "var(--s-4)", fontSize: "var(--t-callout)" }} aria-label="Rejected models">
          {rejected.map(([m, why]) => (
            <li key={m}>
              <strong>{m}</strong>: {why}
            </li>
          ))}
        </ul>
      )}
      {ev && <p style={{ ...captionStyle, margin: "var(--s-1) 0 0" }}>Evaluation is {cell.gate.evaluation_note}.</p>}
    </div>
  );
}

function LedgerCard({ symbol }: { symbol: string }) {
  const [horizon, setHorizon] = useState<number>(30);
  const { data, loading, error, status, reload } = useApi<ForecastSummary>(
    () => api.getForecastSummary(symbol),
    [symbol]
  );
  const cell = data?.horizons[String(horizon)];
  return (
    <section className="card card-pad" data-testid="forecast-ledger">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "var(--s-2)" }}>
        <h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>Forecast ledger</h2>
        <div className="segmented" role="tablist" aria-label="Ledger horizon">
          {HORIZONS.map((h) => (
            <button key={h} role="tab" aria-selected={h === horizon} className={h === horizon ? "on" : ""} onClick={() => setHorizon(h)}>
              {h}d
            </button>
          ))}
        </div>
      </div>
      <div style={{ marginTop: "var(--s-3)" }}>
        {loading && <Loading lines={2} />}
        {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
        {!loading && !error && data && (
          <>
            {data.reason && (
              <Notice variant="info" style={{ marginBottom: "var(--s-3)" }}>
                {data.reason}
              </Notice>
            )}
            {!cell ? (
              <EmptyState title="No ledger view" hint={data.reason ?? "This horizon is not in the ledger response."} />
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
                <p style={{ ...captionStyle, margin: 0 }}>
                  Published model: <strong>{cell.published_model}</strong> · window{" "}
                  {data.window_days == null ? DASH : `${data.window_days} days`}
                </p>
                <GateBlock cell={cell} />
                <div>
                  <div style={{ fontWeight: 600, fontSize: "var(--t-callout)", marginBottom: "var(--s-1)" }}>Skill vs naive</div>
                  <SkillTable cell={cell} />
                </div>
                <div data-testid="forecast-mc-coverage">
                  <div style={{ fontWeight: 600, fontSize: "var(--t-callout)" }}>Monte-Carlo band coverage</div>
                  <p style={{ margin: "var(--s-1) 0 0", fontSize: "var(--t-callout)" }}>
                    {cell.mc_coverage.empirical == null
                      ? `unavailable${cell.mc_coverage.reason ? ` (${cell.mc_coverage.reason})` : ""}`
                      : `${fmtPct(cell.mc_coverage.empirical, 0, { fromFraction: true })} of outcomes inside the band (nominal ${fmtPct(
                          cell.mc_coverage.nominal,
                          0,
                          { fromFraction: true }
                        )}, n = ${cell.mc_coverage.n})`}
                  </p>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function ChartCard({ symbol, row }: { symbol: string; row: ForecastRow | null }) {
  const [lookbackDays, setLookbackDays] = useState(DEFAULT_LOOKBACK_DAYS);
  const bars = useApi<Bar[]>(() => api.getDataBars(symbol, lookbackDays), [symbol, lookbackDays]);
  // A null horizon is skipped, never plotted as 0 (CONSTRAINT #4).
  const forecast = row
    ? HORIZONS.flatMap((h) => {
        const c = row.horizons[String(h)];
        return c && c.forecast != null ? [{ day: h, mid: c.forecast, lower: c.lower, upper: c.upper }] : [];
      })
    : [];
  const barList = bars.data ?? [];
  return (
    <section className="card card-pad">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "var(--s-2)" }}>
        <h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>Price & published forecast</h2>
        <div className="segmented" role="tablist" aria-label="Price history range">
          {LOOKBACK_RANGES.map((r) => (
            <button
              key={r.label}
              role="tab"
              aria-selected={r.days === lookbackDays}
              className={r.days === lookbackDays ? "on" : ""}
              onClick={() => setLookbackDays(r.days)}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>
      <div style={{ marginTop: "var(--s-3)" }}>
        {bars.loading ? (
          <Loading lines={1} />
        ) : barList.length === 0 && forecast.length === 0 ? (
          <div className="empty" style={{ padding: "var(--s-5)" }}>
            {bars.error ? `Price history unavailable (${bars.error}) and no published forecast.` : "No price history and no published forecast to chart."}
          </div>
        ) : (
          <>
            {barList.length === 0 && (
              <div className="empty" style={{ padding: "var(--s-3)", marginBottom: "var(--s-2-5)", fontSize: "var(--t-label)" }}>
                {bars.error
                  ? `Price history unavailable (${bars.error}) — showing the forecast projection only.`
                  : "No price history in the store for this symbol — showing the forecast projection only."}
              </div>
            )}
            <ForecastCandleChart bars={barList} forecast={forecast} />
          </>
        )}
      </div>
    </section>
  );
}

function exportCsv(symbol: string, row: ForecastRow) {
  const lines = HORIZONS.map((h) => {
    const c = row.horizons[String(h)];
    // Empty cells for anything not recorded — never a fabricated 0.
    return [
      `${h}d`,
      c?.forecast ?? "",
      c?.lower ?? "",
      c?.upper ?? "",
      c?.gated_naive == null ? "" : c.gated_naive,
      c?.is_fallback == null ? "" : c.is_fallback,
    ].join(",");
  });
  const csv = ["Horizon,Forecast,Lower,Upper,PublishedNaive,Fallback", ...lines].join("\n");
  const link = document.createElement("a");
  link.setAttribute("href", "data:text/csv;charset=utf-8," + encodeURIComponent(csv));
  link.setAttribute("download", `${symbol}_published_forecast.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

export function ForecastViewer() {
  const nav = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const view = useApi<ForecastsView>(() => api.getForecasts(), []);
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  const requested = searchParams.get("symbol")?.trim().toUpperCase() || null;
  const symbol = requested ?? view.data?.rows[0]?.symbol ?? null;
  const row = symbol ? view.data?.rows.find((r) => r.symbol === symbol) ?? null : null;
  const select = (sym: string) => setSearchParams({ symbol: sym.trim().toUpperCase() }, { replace: true });

  const zReason =
    view.data?.rows.flatMap((r) => HORIZONS.map((h) => r.horizons[String(h)]?.z_reason)).find((x) => x != null) ??
    null;

  return (
    <div className="screen">
      <button
        onClick={back}
        style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: theme.textSecondary, fontSize: "var(--t-callout)", marginBottom: "var(--s-2)" }}
      >
        ← Back
      </button>
      <h1 className="screen-title" style={{ marginTop: "var(--s-2)" }}>
        Forecasts
      </h1>
      <p className="screen-sub">
        The pipeline's published 10/30/60/90-day forecasts, how they compare with the naive "price stays flat" forecast, and what
        they changed this cycle. Read from the last pipeline run; nothing is refit here.
      </p>

      <TabGuide tabKey="forecast" />

      {view.stale && <StaleDataNotice cachedAt={view.cachedAt} onRetry={view.reload} />}

      <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }} data-testid="forecast-universe">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: "var(--s-2)" }}>
          <h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>Published forecasts</h2>
          {view.data && (
            <span style={captionStyle}>
              {view.data.as_of ? `as of ${timeAgo(view.data.as_of)}` : "no snapshot yet"} · {view.data.source}
            </span>
          )}
        </div>
        <div style={{ margin: "var(--s-2) 0 var(--s-3)", display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
          {view.data && <GateLine gate={view.data.gate} />}
          {view.data && view.data.z == null && (
            <div style={captionStyle}>
              <strong style={{ color: theme.textSecondary }}>Forecast z:</strong> unavailable —{" "}
              {zReason ?? "not reported by the backend"}.
            </div>
          )}
        </div>
        {view.loading && <Loading lines={2} />}
        {!view.loading && view.error && <ErrorState message={view.error} status={view.status} onRetry={view.reload} />}
        {!view.loading && !view.error && view.data && (
          <UniverseTable view={view.data} selected={symbol} onSelect={select} />
        )}
      </section>

      <div style={{ marginBottom: "var(--s-3)" }}>
        <SymbolInput key={symbol ?? ""} initial={symbol ?? ""} onSubmit={select} pending={view.loading} />
      </div>

      {symbol == null ? (
        !view.loading && <EmptyState title="Pick a symbol" hint="Choose a row above or type a symbol to see its forecast ledger." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }} data-testid="forecast-symbol-panel">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: "var(--s-2)" }}>
            <h2 style={{ fontSize: "var(--t-title)", margin: 0 }}>{symbol}</h2>
            <div style={{ display: "flex", gap: "var(--s-2)", flexWrap: "wrap" }}>
              <button className="btn" onClick={() => nav(`/symbol/${encodeURIComponent(symbol)}`)} style={{ fontSize: "var(--t-caption)" }}>
                Symbol detail →
              </button>
              {row && (
                <button className="btn" onClick={() => exportCsv(symbol, row)} style={{ fontSize: "var(--t-caption)" }}>
                  Export CSV
                </button>
              )}
            </div>
          </div>
          {!view.loading && !view.error && !row && (
            <Notice variant="info">
              {symbol} is not in the latest pipeline snapshot, so there is no published forecast for it. This screen never refits a
              model; the ledger below may still have history.
            </Notice>
          )}
          {row && <HorizonCards row={row} />}
          <ChartCard symbol={symbol} row={row} />
          {row && <ImpactCard row={row} />}
          <LedgerCard symbol={symbol} />
        </div>
      )}
    </div>
  );
}
