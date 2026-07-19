import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ObservabilitySummary, PerfRange, RiskGateBlockEntry } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, Tile } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { RangeToggle } from "../components/RangeToggle";
import { DrawdownArea, PerfLine } from "../components/charts";
import { fmtNum, fmtPct, timeAgo } from "../format";
import { theme } from "../theme";

const HORIZONS: readonly number[] = [10, 30, 60, 90];

/** Local horizon toggle — mirrors RangeToggle's segmented-control look, but
 * for the four forecast horizons the pipeline actually forecasts (not worth
 * generalizing RangeToggle, which is typed specifically to PerfRange). */
function HorizonToggle({
  value,
  onChange,
}: {
  value: number;
  onChange: (h: number) => void;
}) {
  return (
    <div className="segmented" role="tablist" aria-label="Forecast horizon">
      {HORIZONS.map((h) => (
        <button
          key={h}
          role="tab"
          aria-selected={h === value}
          className={h === value ? "on" : ""}
          onClick={() => onChange(h)}
        >
          {h}d
        </button>
      ))}
    </div>
  );
}

/** RISK ON -> growth, RECESSION/CREDIT EVENT -> decline, everything else
 * (NEUTRAL, UNKNOWN, ...) -> caution. Never guesses at a regime that wasn't
 * actually persisted. */
function regimeColor(regime: string | null): string {
  if (!regime) return theme.textMuted;
  const r = regime.toUpperCase();
  if (r.includes("RISK ON")) return theme.growth;
  if (r.includes("RECESSION") || r.includes("CREDIT EVENT")) return theme.decline;
  return theme.caution;
}

function SectionHeading({ title, sub }: { title: string; sub?: string }) {
  return (
    <div style={{ marginTop: 24, marginBottom: 10 }}>
      <h2 style={{ margin: 0, fontSize: "var(--t-title)" }}>{title}</h2>
      {sub && (
        <p style={{ margin: "4px 0 0", color: theme.textMuted, fontSize: 12.5 }}>{sub}</p>
      )}
    </div>
  );
}

function RegimeBadgeRow({ regime }: { regime: ObservabilitySummary["regime"] }) {
  if (regime.reason) {
    return <div className="empty" style={{ padding: 16 }}>{regime.reason}</div>;
  }
  const badges: { label: string; value: string }[] = [
    { label: "As of", value: timeAgo(regime.as_of) },
    { label: "Regime", value: regime.market_regime ?? "—" },
    { label: "VIX", value: fmtNum(regime.vix, 1) },
    { label: "Sahm Rule", value: fmtNum(regime.sahm_rule, 3) },
    { label: "HY OAS", value: regime.high_yield_oas == null ? "—" : `${fmtNum(regime.high_yield_oas, 2)}%` },
    { label: "10Y-2Y", value: regime.yield_curve == null ? "—" : `${fmtNum(regime.yield_curve, 2)}%` },
    {
      label: "HMM risk-on",
      value: regime.hmm_risk_on_probability == null ? "—" : fmtPct(regime.hmm_risk_on_probability, 0, { fromFraction: true }),
    },
  ];
  return (
    <div
      data-testid="regime-badges"
      style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}
    >
      {badges.map((b) => (
        <span
          key={b.label}
          className="chip"
          style={
            b.label === "Regime"
              ? { color: regimeColor(regime.market_regime), fontWeight: 700 }
              : undefined
          }
        >
          {b.label}: {b.value}
        </span>
      ))}
      {regime.kill_switch_active && (
        <span className="badge badge-bad">Kill switch ACTIVE</span>
      )}
      {regime.macro_regime_gate_enabled === false && (
        <span className="badge badge-warn">Macro regime gate OFF</span>
      )}
    </div>
  );
}

function ForecastSkillSection({
  skill,
}: {
  skill: ObservabilitySummary["forecast_skill"];
}) {
  if (skill.reason) {
    return <div className="empty" style={{ padding: 20 }}>{skill.reason}</div>;
  }
  const weights = Object.entries(skill.skill_weights).sort((a, b) => b[1] - a[1]);
  return (
    <div>
      <div style={{ display: "flex", gap: 16, marginBottom: 12, flexWrap: "wrap" }}>
        <Tile label="Pending" value={skill.pending} />
        <Tile label="Completed" value={skill.completed} />
        <Tile label="Window" value={`${skill.window_days}d`} />
        <Tile label="Min obs" value={skill.min_obs} />
      </div>
      {weights.length === 0 ? (
        <div className="empty" style={{ padding: 16 }}>
          No skill weights yet — not enough completed forecasts in the window.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {weights.map(([model, weight]) => (
            <div key={model} style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ width: 96, fontSize: 12.5, color: theme.textSecondary, flex: "0 0 auto" }}>
                {model}
              </span>
              <div style={{ flex: 1, height: 8, borderRadius: 4, background: theme.surface2, overflow: "hidden" }}>
                <div
                  style={{
                    width: `${Math.max(0, Math.min(1, weight)) * 100}%`,
                    height: "100%",
                    background: theme.accent,
                  }}
                />
              </div>
              <span className="num" style={{ width: 46, textAlign: "right", fontSize: 12.5 }}>
                {fmtPct(weight, 0, { fromFraction: true })}
              </span>
            </div>
          ))}
        </div>
      )}
      {skill.reliability_curve.length > 0 && (
        <div style={{ marginTop: 16, overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: theme.textMuted, textAlign: "left" }}>
                <th style={{ padding: "4px 8px" }}>Model</th>
                <th style={{ padding: "4px 8px" }}>Bin</th>
                <th style={{ padding: "4px 8px" }}>Mean error</th>
                <th style={{ padding: "4px 8px" }}>Count</th>
              </tr>
            </thead>
            <tbody>
              {skill.reliability_curve.map((bin, i) => (
                <tr key={i} style={{ borderTop: `1px solid ${theme.border}` }}>
                  <td style={{ padding: "4px 8px" }}>{bin.model_name}</td>
                  <td className="num" style={{ padding: "4px 8px" }}>
                    {bin.bin_center == null ? "—" : fmtPct(bin.bin_center, 0, { fromFraction: true })}
                  </td>
                  <td className="num" style={{ padding: "4px 8px" }}>
                    {bin.mean_pct_error == null ? "—" : fmtPct(bin.mean_pct_error, 1, { fromFraction: true, signed: true })}
                  </td>
                  <td className="num" style={{ padding: "4px 8px" }}>{bin.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function BlockLogRow({ entry }: { entry: RiskGateBlockEntry }) {
  return (
    <div
      className="card card-pad"
      style={{ marginBottom: 8 }}
      data-testid="risk-gate-block-row"
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontWeight: 700, fontSize: 13.5 }}>
          {entry.symbol ?? "—"} {entry.side ? entry.side.toUpperCase() : ""}
          {entry.qty != null ? ` × ${fmtNum(entry.qty, 2)}` : ""}
        </span>
        <span style={{ fontSize: 11, color: theme.textMuted, whiteSpace: "nowrap" }}>
          {entry.ts ? timeAgo(entry.ts) : "—"}
        </span>
      </div>
      <div style={{ fontSize: 11.5, color: theme.caution, marginTop: 2 }}>
        {entry.check ?? "—"}
        {entry.strategy_id ? ` · ${entry.strategy_id}` : ""}
      </div>
      {entry.reason && (
        <div style={{ fontSize: 12.5, color: theme.textSecondary, marginTop: 4, lineHeight: 1.4 }}>
          {entry.reason}
        </div>
      )}
    </div>
  );
}

export function Observability() {
  const nav = useNavigate();
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  const [range, setRange] = useState<PerfRange>("1Y");
  const [horizon, setHorizon] = useState<number>(30);

  const { data, loading, error, status, reload } = useApi<ObservabilitySummary>(
    () => api.getObservabilitySummary(range, horizon),
    [range, horizon]
  );

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
          fontSize: 14,
          marginBottom: 8,
        }}
      >
        ← Pilots
      </button>
      <h1 className="screen-title">Mission Control</h1>
      <p className="screen-sub">
        Account risk stats, the equity curve, the macro regime, forecast
        skill, and blocked orders — one read-only view over what the engine
        already computed.
      </p>

      <TabGuide tabKey="observability" />

      {loading && <Loading lines={4} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}

      {!loading && !error && data && (
        <>
          {/* 1. Portfolio risk metrics */}
          <SectionHeading title="Portfolio risk" sub="Over the full account equity history" />
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
            <Tile label="Sharpe" value={fmtNum(data.portfolio_risk.sharpe_ratio, 2)} />
            <Tile label="Calmar" value={fmtNum(data.portfolio_risk.calmar_ratio, 2)} />
            <Tile
              label="Max drawdown"
              value={fmtPct(data.portfolio_risk.max_drawdown, 1, { fromFraction: true })}
              tone={
                data.portfolio_risk.max_drawdown != null && data.portfolio_risk.max_drawdown < 0
                  ? "neg"
                  : undefined
              }
            />
            <Tile
              label="Max DD duration"
              value={
                data.portfolio_risk.max_drawdown_duration_days == null
                  ? "—"
                  : `${fmtNum(data.portfolio_risk.max_drawdown_duration_days, 0)}d`
              }
            />
            <Tile label="CAGR" value={fmtPct(data.portfolio_risk.cagr, 1, { fromFraction: true })} />
          </div>
          {data.portfolio_risk.reason && (
            <p style={{ color: theme.textMuted, fontSize: 12, marginTop: 8 }}>
              {data.portfolio_risk.reason}
            </p>
          )}

          {/* 2. Equity + drawdown + regime overlay */}
          <SectionHeading title="Equity, drawdown &amp; regime" />
          <div style={{ marginBottom: 10 }}>
            <RangeToggle value={range} onChange={setRange} />
          </div>
          {data.equity_curve.points.length === 0 ? (
            <div className="empty" style={{ padding: 20 }}>
              {data.equity_curve.reason ?? "No account equity history yet."}
            </div>
          ) : (
            <>
              <PerfLine
                data={data.equity_curve.points.map((p) => ({ date: p.date, value: p.equity }))}
              />
              <DrawdownArea data={data.equity_curve.points} />
            </>
          )}
          <RegimeBadgeRow regime={data.regime} />

          {/* 3. Forecast skill (portfolio-wide) */}
          <SectionHeading
            title="Forecast skill"
            sub="Portfolio-wide reliability and inverse-RMSE model weights"
          />
          <div style={{ marginBottom: 10 }}>
            <HorizonToggle value={horizon} onChange={setHorizon} />
          </div>
          <ForecastSkillSection skill={data.forecast_skill} />

          {/* 4. Risk gate block log */}
          <SectionHeading
            title="Risk gate block log"
            sub={`Last ${data.risk_gate_blocks.count} blocked order(s)`}
          />
          {data.risk_gate_blocks.entries.length === 0 ? (
            <div className="empty" style={{ padding: 20 }}>
              {data.risk_gate_blocks.reason ?? "No blocked orders in the log."}
            </div>
          ) : (
            <div style={{ maxHeight: 340, overflowY: "auto" }}>
              {data.risk_gate_blocks.entries.map((e, i) => (
                <BlockLogRow key={`${e.ts ?? i}-${i}`} entry={e} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
