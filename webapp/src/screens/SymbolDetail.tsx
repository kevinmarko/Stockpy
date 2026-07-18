import { useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  Decision,
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
import { fmtNum, fmtPct, fmtUsd, timeAgo } from "../format";
import { theme } from "../theme";
import { realizableTheta } from "../optionsHonesty";

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

      {/* Decision journal — log + review whether the operator acted on,
          passed on, or modified this signal (ports gui/decision_log.py's
          Streamlit form). Reuses the advisory data already loaded above
          rather than re-fetching it. */}
      <DecisionJournalSection
        ticker={data.symbol}
        asOf={data.as_of}
        advisoryAction={advisory.action}
        advisoryConviction={advisory.conviction}
      />

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

type DecisionAction = "acted" | "passed" | "modified";

const DECISION_LABEL: Record<DecisionAction, string> = {
  acted: "✅ Acted",
  passed: "⏭ Passed",
  modified: "🔁 Modified",
};

/**
 * Log + review Decision Journal entries for one symbol — ports
 * gui/panels/report_viewer.py::_render_decision_journal_section's three
 * decision buttons + notes field + past-decisions list to the PWA. Reuses
 * the advisory action/conviction already loaded by the parent screen rather
 * than issuing a second fetch for the same data.
 */
function DecisionJournalSection({
  ticker,
  asOf,
  advisoryAction,
  advisoryConviction,
}: {
  ticker: string;
  asOf: string | null;
  advisoryAction: string | null;
  advisoryConviction: number | null;
}) {
  const [notes, setNotes] = useState("");
  const decisions = useApi<Decision[]>(() => api.getDecisions(ticker, 10), [ticker]);
  const logMutation = useMutation((action: DecisionAction) =>
    api.logDecision({
      symbol: ticker,
      action_taken: action,
      signal_action: advisoryAction ?? "",
      conviction: advisoryConviction,
      notes: notes.trim(),
      signal_ts: asOf ?? "",
    })
  );

  const handleLog = async (action: DecisionAction) => {
    const entry = await logMutation.run(action);
    if (entry) {
      setNotes("");
      decisions.reload();
    }
  };

  return (
    <section className="card card-pad" style={{ marginBottom: 16 }}>
      <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>Decision journal</h2>
      <p style={{ color: theme.textMuted, fontSize: 12.5, marginTop: 0, marginBottom: 12 }}>
        Log what you decided to do with this signal.
      </p>

      <div className="list" style={{ marginBottom: 12 }}>
        <StatRow label="System recommendation" value={<ActionBadge action={advisoryAction} />} />
        <StatRow
          label="Conviction"
          value={fmtPct(advisoryConviction, 0, { fromFraction: true })}
        />
      </div>

      <label
        htmlFor="decision-journal-notes"
        className="tile-label"
        style={{ display: "block", marginBottom: 6 }}
      >
        Notes (optional)
      </label>
      <textarea
        id="decision-journal-notes"
        className="input"
        rows={3}
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="e.g. 'Sized half — position already large', 'Used a limit instead of market'"
        style={{ resize: "vertical", fontFamily: "inherit", width: "100%" }}
      />

      {logMutation.error && (
        <div className="notice notice-warn" style={{ marginTop: 10 }}>
          <span>⚠️</span>
          <span>{logMutation.error}</span>
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        {(["acted", "passed", "modified"] as const).map((action) => (
          <Button
            key={action}
            variant="neutral"
            pending={logMutation.pending}
            onClick={() => handleLog(action)}
            style={{ flex: 1 }}
          >
            {DECISION_LABEL[action]}
          </Button>
        ))}
      </div>

      <h3 style={{ fontSize: 13, color: theme.textMuted, margin: "18px 0 8px" }}>
        Past decisions
      </h3>
      {decisions.loading ? (
        <Loading lines={2} />
      ) : !decisions.data || decisions.data.length === 0 ? (
        <div className="empty" style={{ padding: 18 }}>
          No decisions logged yet for {ticker}.
        </div>
      ) : (
        <div className="list">
          {decisions.data.map((d, i) => (
            <div className="row" key={`${d.timestamp}-${i}`}>
              <div className="row-main">
                <span className="row-title">{DECISION_LABEL[d.action_taken]}</span>
                {d.notes && (
                  <span className="row-sub" style={{ whiteSpace: "normal" }}>
                    {d.notes}
                  </span>
                )}
              </div>
              <div className="row-end">
                <div style={{ fontSize: 12, color: theme.textMuted }}>{timeAgo(d.timestamp)}</div>
                {d.trade_id != null && (
                  <div style={{ fontSize: 11, color: theme.textMuted }}>
                    trade #{d.trade_id}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
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
