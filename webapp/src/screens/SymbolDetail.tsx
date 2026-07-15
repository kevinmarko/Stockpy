import type { ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { SymbolDetail as SymbolDetailT } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, MetricBadge } from "../components/ui";
import { fmtNum, fmtPct, fmtUsd, timeAgo } from "../format";
import { theme } from "../theme";

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
          <StatRow label="News sentiment" value={fmtNum(risk.news_sentiment, 2)} />
          <StatRow label="CoVaR proxy" value={fmtNum(risk.covar_proxy, 2)} />
          <StatRow label="Realized slippage" value={fmtNum(risk.realized_slippage, 4)} />
          <StatRow label="MFE" value={fmtNum(risk.mfe, 2)} />
          <StatRow label="MAE" value={fmtNum(risk.mae, 2)} />
          <StatRow label="Edge ratio" value={fmtNum(risk.edge_ratio, 2)} />
        </div>
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
