import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  Follow,
  PerfRange,
  Portfolio as PortfolioT,
  PilotSummary,
  CurvePoint,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { PerfLine } from "../components/charts";
import { RangeToggle } from "../components/RangeToggle";
import { ErrorState, Loading, Tile } from "../components/ui";
import { fmtPct, fmtSignedUsd, fmtUsd, timeAgo } from "../format";
import { theme } from "../theme";

export function Portfolio() {
  const [range, setRange] = useState<PerfRange>("3M");

  const port = useApi<PortfolioT>(() => api.getPortfolio(), []);
  const equity = useApi<{ range: PerfRange; curve: CurvePoint[] | null }>(
    () => api.getEquityCurve(range),
    [range]
  );
  const follows = useApi<Follow[]>(() => api.getFollows(), []);
  const pilots = useApi<PilotSummary[]>(() => api.listPilots(), []);

  if (port.loading) {
    return (
      <div className="screen">
        <h1 className="screen-title">Portfolio</h1>
        <Loading lines={4} />
      </div>
    );
  }
  if (port.error || !port.data) {
    return (
      <div className="screen">
        <h1 className="screen-title">Portfolio</h1>
        <ErrorState
          message={port.error ?? "No account snapshot"}
          status={port.status}
          onRetry={port.reload}
        />
      </div>
    );
  }

  const p = port.data;
  const pilotName = (id: string) =>
    pilots.data?.find((x) => x.id === id)?.name ?? id;

  return (
    <div className="screen">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h1 className="screen-title">Portfolio</h1>
        <span style={{ fontSize: 12, color: theme.textMuted }}>
          {p.source} · {timeAgo(p.fetched_at)}
        </span>
      </div>

      <div style={{ marginBottom: 4 }}>
        <div className="tile-label">Total equity</div>
        <div
          className="num"
          style={{ fontSize: 34, fontWeight: 800, letterSpacing: "-0.02em" }}
        >
          {fmtUsd(p.total_equity)}
        </div>
        <div
          className="num"
          style={{
            color: p.total_unrealized_pl >= 0 ? theme.growth : theme.decline,
            fontWeight: 700,
            fontSize: 15,
            marginTop: 2,
          }}
        >
          {fmtSignedUsd(p.total_unrealized_pl)} unrealized
        </div>
      </div>

      <div className="tiles" style={{ margin: "16px 0" }}>
        <Tile label="Buying power" value={fmtUsd(p.buying_power)} />
        <Tile
          label="Unrealized P&L"
          value={fmtSignedUsd(p.total_unrealized_pl)}
          tone={p.total_unrealized_pl >= 0 ? "pos" : "neg"}
        />
        <Tile label="Dividends" value={fmtUsd(p.total_dividends)} />
        <Tile label="Positions" value={p.position_count} />
      </div>

      {/* Equity curve */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, margin: "0 0 12px" }}>Account value</h2>
        {equity.loading ? (
          <div className="skeleton" style={{ height: 200 }} />
        ) : equity.data?.curve && equity.data.curve.length > 1 ? (
          <PerfLine data={equity.data.curve} />
        ) : (
          <div className="empty" style={{ padding: 30 }}>
            Not enough account history yet.
          </div>
        )}
        <div style={{ marginTop: 12 }}>
          <RangeToggle value={range} onChange={setRange} />
        </div>
      </section>

      {/* Active follows */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>Active follows</h2>
        {follows.loading ? (
          <Loading lines={2} />
        ) : (follows.data ?? []).length === 0 ? (
          <div className="empty" style={{ padding: 22 }}>
            You aren't following any Pilots yet.
            <div style={{ marginTop: 10 }}>
              <Link to="/" className="btn" style={{ display: "inline-flex" }}>
                Browse Pilots
              </Link>
            </div>
          </div>
        ) : (
          <div className="list">
            {(follows.data ?? []).map((f) => (
              <Link className="row" key={f.pilot_id} to={`/pilots/${f.pilot_id}`}>
                <div className="row-main">
                  <span className="row-title">{pilotName(f.pilot_id)}</span>
                  <span className="row-sub">Updated {timeAgo(f.updated_at)}</span>
                </div>
                <div className="row-end">
                  <div className="num" style={{ fontWeight: 700 }}>
                    {fmtUsd(f.amount)}
                  </div>
                  <div>
                    <span
                      className={`badge ${
                        f.status === "active" ? "badge-warn" : "badge-neutral"
                      }`}
                    >
                      {f.status === "active" ? "gated queue" : f.status}
                    </span>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
        <p style={{ color: theme.textMuted, fontSize: 11.5, marginTop: 12 }}>
          Follows build a gated, paper-first order queue. Confirm each queue in the
          robinhood-execution flow — nothing is placed automatically.
        </p>
      </section>

      {/* Positions */}
      <section className="card card-pad">
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>Positions</h2>
        <div className="list">
          {p.positions.map((pos) => (
            <div className="row" key={pos.symbol}>
              <div className="row-main">
                <span className="row-title">{pos.symbol}</span>
                <span className="row-sub">
                  {pos.qty} sh @ {fmtUsd(pos.avg_cost)}
                </span>
              </div>
              <div className="row-end">
                <div className="num" style={{ fontWeight: 700 }}>
                  {fmtUsd(pos.market_value)}
                </div>
                <div
                  className="num row-sub"
                  style={{
                    color:
                      (pos.unrealized_pl ?? 0) >= 0 ? theme.growth : theme.decline,
                  }}
                >
                  {fmtSignedUsd(pos.unrealized_pl)} (
                  {fmtPct(pos.unrealized_pl_pct, 1, { signed: true })})
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
