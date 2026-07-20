import type { ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  AiChartResponse,
  AiCommentaryResponse,
  AiResearchResponse,
  ForecastSkill,
  OptionsDirective,
  RollingBeta,
  SymbolDetail as SymbolDetailT,
  SymbolOptions,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, ErrorState, Loading, MetricBadge } from "../components/ui";
import { PerfLine } from "../components/charts";
import { DecisionModal } from "../components/DecisionModal";
import { TabGuide } from "../components/TabGuide";
import { fmtNum, fmtPct, fmtUsd, timeAgo } from "../format";
import { theme } from "../theme";
import { realizableTheta } from "../optionsHonesty";
import { useState } from "react";
import type { DecisionEntry } from "../api/types";

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

/**
 * Kelly Target before vs. after the HMM regime multiplier + meta-label
 * composite were multiplied in and re-clamped — the per-symbol port of
 * `gui/panels/strategy_matrix.py::_render_regime_multiplier_impact`. Either
 * leg can legitimately be `null` (only the advisory snapshot writer persists
 * these, not the richer main_orchestrator one) — renders "—", never a
 * fabricated delta.
 */
function RegimeSizingDelta({ pre, post }: { pre: number | null; post: number | null }) {
  if (pre == null || post == null) return <span style={{ color: theme.textMuted }}>—</span>;
  const deltaPp = (post - pre) * 100;
  const positive = deltaPp >= 0;
  return (
    <span>
      {fmtPct(pre, 2, { fromFraction: true })} → {fmtPct(post, 2, { fromFraction: true })}{" "}
      <span style={{ color: positive ? theme.growth : theme.decline, fontSize: 11 }}>
        ({positive ? "+" : ""}
        {deltaPp.toFixed(2)}pp)
      </span>
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

export function SymbolDetail() {
  const { ticker = "" } = useParams();
  const nav = useNavigate();

  const { data, loading, error, status, reload } = useApi<SymbolDetailT>(
    () => api.getSymbol(ticker),
    [ticker]
  );
  const forecast = useApi<ForecastSkill>(() => api.getForecast(ticker, 30), [ticker]);
  const options = useApi<SymbolOptions>(() => api.getSymbolOptions(ticker), [ticker]);
  const rollingBeta = useApi<RollingBeta>(() => api.getRollingBeta(ticker, 60), [ticker]);
  const decisions = useApi<DecisionEntry[]>(
    () => api.getDecisions({ symbol: ticker, limit: 10 }),
    [ticker]
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

  const { identity, advisory, factors, ranges, risk, held_by_pilots } = data;
  const sc = factors.score_components;
  const hasComponents = sc != null && Object.keys(sc).length > 0;

  return (
    <div className="screen">
      <BackButton onClick={back} />

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="screen-title" style={{ marginBottom: 2 }}>
            {data.symbol}
          </h1>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {identity.sector && <span className="chip">{identity.sector}</span>}
            <ActionBadge action={identity.action} />
            <span style={{ fontSize: 12, color: theme.textMuted }}>
              as of {timeAgo(data.as_of)}
            </span>
          </div>
        </div>
        <div className="num" style={{ fontSize: 26, fontWeight: 800, letterSpacing: "-0.02em" }}>
          {fmtUsd(identity.price)}
        </div>
      </div>

      {data.reason && (
        <p style={{ color: theme.textMuted, fontSize: 13, marginTop: 10 }}>{data.reason}</p>
      )}

      <TabGuide tabKey="symbol-detail" />

      {/* Advisory */}
      <section className="card card-pad" style={{ margin: "16px 0" }}>
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>Advisory</h2>
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
          <p style={{ color: theme.textSecondary, fontSize: 13.5, lineHeight: 1.5, marginTop: 12 }}>
            {advisory.rationale}
          </p>
        )}
      </section>

      {/* Decision journal — per-symbol log of what the operator actually did
          with this signal. Shared DecisionModal with the Calibration screen's
          portfolio-wide journal (../components/DecisionModal); this section
          is the standalone, symbol-scoped read (GET /decisions?symbol=...)
          Calibration's bundled recent-decisions preview doesn't offer. */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ fontSize: 16, margin: 0 }}>Decision journal</h2>
          <Button variant="neutral" onClick={() => setJournaling(true)}>
            Log decision
          </Button>
        </div>
        {decisions.loading && <Loading lines={2} />}
        {!decisions.loading && (!decisions.data || decisions.data.length === 0) && (
          <p style={{ color: theme.textMuted, fontSize: 13, marginTop: 10 }}>
            No decisions logged yet for {data.symbol}.
          </p>
        )}
        {!decisions.loading && decisions.data && decisions.data.length > 0 && (
          <div className="list" style={{ marginTop: 8 }}>
            {decisions.data.map((d, i) => (
              <div key={`${d.timestamp}-${i}`} className="row">
                <div className="row-main">
                  <span className="row-title" style={{ fontWeight: 500 }}>
                    {d.action_taken === "acted" ? "✅ Acted" : d.action_taken === "passed" ? "⏭ Passed" : "🔁 Modified"}
                  </span>
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
            ))}
          </div>
        )}
      </section>

      {journaling && (
        <DecisionModal
          signal={{ symbol: data.symbol, action: advisory.action, conviction: advisory.conviction }}
          onClose={() => setJournaling(false)}
          onLogged={decisions.reload}
        />
      )}

      {/* Identity */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>Snapshot</h2>
        <div className="list">
          <StatRow label="Sector" value={identity.sector ?? "—"} />
          <StatRow label="Price" value={fmtUsd(identity.price)} />
          <StatRow label="Signal action" value={<ActionBadge action={identity.action} />} />
          <StatRow
            label="Shares held"
            value={identity.shares == null ? "—" : fmtNum(identity.shares, 0)}
          />
        </div>
      </section>

      {/* Tactical ranges (pre-formatted strings, NOT tuples) */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>Tactical ranges</h2>
        <div className="list">
          <StatRow label="Buy" value={ranges.buy_range ?? "—"} />
          <StatRow label="Sell" value={ranges.sell_range ?? "—"} />
        </div>
      </section>

      {/* Factors */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>Factor exposure</h2>
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
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
            {Object.entries(sc!).map(([k, v]) => (
              <MetricBadge key={k} label={k} value={fmtNum(v, 2)} />
            ))}
          </div>
        )}
      </section>

      {/* Risk */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>Risk & regime</h2>
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
          <StatRow label="Meta-label composite" value={fmtNum(risk.meta_label_composite, 2)} />
          <StatRow label="HMM regime multiplier" value={fmtNum(risk.regime_multiplier, 3)} />
          <StatRow
            label="Kelly target (pre → post regime)"
            value={<RegimeSizingDelta pre={risk.kelly_target_pre_regime} post={risk.kelly_target_post_regime} />}
          />
        </div>
      </section>

      {/* Rolling beta vs SPY — time-varying, distinct from the static point-in-time beta */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>Rolling beta vs SPY</h2>
        {rollingBeta.loading ? (
          <Loading lines={2} />
        ) : !rollingBeta.data || rollingBeta.data.series.length === 0 ? (
          <div className="empty" style={{ padding: 18 }}>
            {rollingBeta.data?.reason ?? "No cached price history yet."}
          </div>
        ) : (
          <>
            <PerfLine
              data={rollingBeta.data.series.map((p) => ({ date: p.date, value: p.beta }))}
              valueLabel="Beta"
              yTickDecimals={1}
            />
            <p style={{ color: theme.textMuted, fontSize: 12, marginTop: 8 }}>
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
      </section>

      {/* Forecast reliability + model skill weights */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>Forecast skill</h2>
        {forecast.loading ? (
          <Loading lines={1} />
        ) : !forecast.data || forecast.data.reason ? (
          <div className="empty" style={{ padding: 18 }}>
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
                <div style={{ color: theme.textMuted, fontSize: 12, margin: "12px 0 6px" }}>
                  Model skill weights (inverse-RMSE)
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
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
          </>
        )}
      </section>

      {/* Options premium directive (persisted matrix; advisory) */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>Options premium</h2>
        {options.loading ? (
          <Loading lines={1} />
        ) : !options.data || !options.data.directive ? (
          <div className="empty" style={{ padding: 18 }}>
            {options.data?.reason ?? "No options directive for this symbol yet."}
          </div>
        ) : (
          <OptionsDirectiveView d={options.data.directive} />
        )}
      </section>

      {/* On-demand AI generation — Claude analyst note, Gemini chart-pattern
          read, Opal research brief. Each is operator-triggered only (never
          generated automatically) and fully independent: one card failing or
          being disabled never blocks the other two. */}
      <CommentaryCard symbol={data.symbol} />
      <ChartReadCard symbol={data.symbol} />
      <ResearchBriefCard symbol={data.symbol} />

      {/* Held by Pilots — the Stockpy reverse cross-link */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>
          Held by Pilots{" "}
          <span style={{ color: theme.textMuted }}>({held_by_pilots.length})</span>
        </h2>
        {held_by_pilots.length === 0 ? (
          <div className="empty" style={{ padding: 20 }}>
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
      </section>
    </div>
  );
}

/** Renders one persisted options premium directive (advisory, read-only). */
function OptionsDirectiveView({ d }: { d: OptionsDirective }) {
  const legOk = d.Integrity_OK === true;
  const theta = realizableTheta(d);
  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
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
        <StatRow label="IVR proxy" value={fmtNum(d.IVR_Proxy ?? null, 1)} />
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
    <div className="empty" style={{ padding: 18, marginTop: 12 }}>
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
    <div style={{ marginTop: 10 }}>
      <div style={{ color: theme.textMuted, fontSize: 12, marginBottom: 4 }}>{title}</div>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {items.map((it, i) => (
          <li
            key={i}
            style={{ fontSize: 13.5, lineHeight: 1.5, color: theme.textSecondary, marginBottom: 2 }}
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
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <h2 style={{ fontSize: 16, margin: 0 }}>{title}</h2>
        <Button variant="neutral" pending={pending} onClick={onGenerate}>
          Generate
        </Button>
      </div>
      <p style={{ color: theme.textMuted, fontSize: 12, margin: "4px 0 0" }}>{subtitle}</p>
    </>
  );
}

/** Claude analyst-grade narrative: headline / why-now / key risks / invalidation. */
function CommentaryCard({ symbol }: { symbol: string }) {
  const mutation = useMutation(() => api.generateCommentary(symbol));
  const data = mutation.result;

  return (
    <section className="card card-pad" style={{ marginBottom: 16 }}>
      <AiCardHeader
        title="Claude analyst note"
        subtitle={`On-demand Claude narrative for ${symbol} — not generated automatically.`}
        pending={mutation.pending}
        onGenerate={() => mutation.run()}
      />
      {mutation.error && (
        <div className="notice notice-warn" style={{ marginTop: 12 }}>
          <span>{mutation.error}</span>
        </div>
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
        <div style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 700, fontSize: 14.5 }}>{data.payload.headline}</div>
          <p style={{ color: theme.textSecondary, fontSize: 13.5, lineHeight: 1.5, marginTop: 8 }}>
            {data.payload.why_now}
          </p>
          <BulletList title="Key risks" items={data.payload.key_risks} />
          <p style={{ color: theme.textMuted, fontSize: 12.5, marginTop: 10, lineHeight: 1.5 }}>
            <strong style={{ color: theme.textSecondary }}>Invalidation:</strong>{" "}
            {data.payload.invalidation}
          </p>
        </div>
      )}
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
    <section className="card card-pad" style={{ marginBottom: 16 }}>
      <AiCardHeader
        title="Gemini chart read"
        subtitle={`On-demand chart-pattern read for ${symbol} — not generated automatically.`}
        pending={mutation.pending}
        onGenerate={() => mutation.run()}
      />
      {mutation.error && (
        <div className="notice notice-warn" style={{ marginTop: 12 }}>
          <span>{mutation.error}</span>
        </div>
      )}
      {data?.chart_png_base64 && (
        <img
          src={`data:image/png;base64,${data.chart_png_base64}`}
          alt={`${symbol} price chart`}
          style={{ width: "100%", borderRadius: "var(--r-md)", marginTop: 12, display: "block" }}
        />
      )}
      {data?.available && data.payload && (
        <div style={{ marginTop: 12 }}>
          <div className="list">
            <StatRow label="Pattern" value={data.payload.pattern_name} />
            <StatRow label="Trend" value={data.payload.trend_direction} />
            <StatRow label="Confidence" value={data.payload.confidence} />
          </div>
          <BulletList title="Support" items={data.payload.support_levels} />
          <BulletList title="Resistance" items={data.payload.resistance_levels} />
          <p style={{ color: theme.textSecondary, fontSize: 13.5, lineHeight: 1.5, marginTop: 10 }}>
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
    </section>
  );
}

/** Opal (OpenAI/Gemini) grounded research brief — qualitative-only, sourced
 * from real retrieved news/earnings. */
function ResearchBriefCard({ symbol }: { symbol: string }) {
  const mutation = useMutation(() => api.generateResearch(symbol));
  const data = mutation.result;

  return (
    <section className="card card-pad" style={{ marginBottom: 16 }}>
      <AiCardHeader
        title="Opal research brief"
        subtitle={`On-demand grounded research brief for ${symbol} — not generated automatically.`}
        pending={mutation.pending}
        onGenerate={() => mutation.run()}
      />
      {mutation.error && (
        <div className="notice notice-warn" style={{ marginTop: 12 }}>
          <span>{mutation.error}</span>
        </div>
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
        <div style={{ marginTop: 12 }}>
          <p style={{ color: theme.textSecondary, fontSize: 13.5, lineHeight: 1.5 }}>
            {data.payload.thesis_context}
          </p>
          <BulletList title="Catalysts" items={data.payload.catalysts} />
          <BulletList title="Risk factors" items={data.payload.risk_factors} />
          <BulletList title="Recent developments" items={data.payload.recent_developments} />
          <p style={{ color: theme.textMuted, fontSize: 12, marginTop: 10 }}>
            {data.payload.sources_note}
          </p>
        </div>
      )}
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
        fontSize: 14,
        display: "inline-block",
        marginBottom: 8,
      }}
    >
      ← Back
    </button>
  );
}
