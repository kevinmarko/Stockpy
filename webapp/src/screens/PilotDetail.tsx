import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { PerfRange, PilotDetail as PilotDetailT, PerformanceResponse } from "../api/types";
import { useApi } from "../hooks/useApi";
import { RangeToggle } from "../components/RangeToggle";
import { PerfLine, SectorDonut } from "../components/charts";
import { ErrorState, HonestyRow, Loading } from "../components/ui";
import { FollowModal } from "./FollowModal";
import { fmtNum, fmtUsd, timeAgo } from "../format";
import { theme } from "../theme";

const SIDE_STYLE: Record<string, string> = {
  ENTER: "badge-good",
  EXIT: "badge-bad",
  REWEIGHT: "badge-neutral",
};

export function PilotDetail() {
  const { id = "" } = useParams();
  const [range, setRange] = useState<PerfRange>("1M");
  const [showFollow, setShowFollow] = useState(false);

  const {
    data: pilot,
    loading,
    error,
    status,
    reload,
  } = useApi<PilotDetailT>(() => api.getPilot(id), [id]);

  const perf = useApi<PerformanceResponse>(
    () => api.getPerformance(id, range),
    [id, range]
  );

  if (loading) {
    return (
      <div className="screen">
        <BackLink />
        <Loading lines={5} />
      </div>
    );
  }
  if (error || !pilot) {
    return (
      <div className="screen">
        <BackLink />
        <ErrorState message={error ?? "Not found"} status={status} onRetry={reload} />
      </div>
    );
  }

  return (
    <div className="screen">
      <BackLink />

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="screen-title" style={{ marginBottom: 2 }}>
            {pilot.name}
          </h1>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span className="chip">{pilot.category}</span>
            {pilot.long_only && <span className="chip">Long-only</span>}
            <span style={{ fontSize: 12, color: theme.textMuted }}>
              as of {timeAgo(pilot.as_of)}
            </span>
          </div>
        </div>
      </div>

      <p style={{ color: theme.textSecondary, fontSize: 14, lineHeight: 1.5, marginTop: 12 }}>
        {pilot.description}
      </p>

      {/* Honesty badges */}
      <div style={{ margin: "6px 0 18px" }}>
        <HonestyRow h={pilot.headline} />
      </div>

      {/* Performance */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            marginBottom: 12,
          }}
        >
          <h2 style={{ fontSize: 16, margin: 0 }}>Performance</h2>
          {perf.data?.curve && perf.data.curve.length > 1 && (
            <PerfDelta curve={perf.data.curve} />
          )}
        </div>

        {perf.loading ? (
          <div className="skeleton" style={{ height: 200 }} />
        ) : perf.data?.curve ? (
          <PerfLine
            data={perf.data.curve}
            benchmark={perf.data.benchmark}
            macroBenchmark={perf.data.macro_benchmark}
          />
        ) : (
          <div
            className="empty"
            style={{ padding: "44px 8px", background: "var(--surface-2)", borderRadius: 12 }}
          >
            <div style={{ fontWeight: 600, color: theme.textSecondary }}>
              No backtest series yet
            </div>
            <div style={{ marginTop: 6, fontSize: 13 }}>
              {perf.data?.reason ??
                "This Pilot's validation report has no persisted return curve."}
            </div>
          </div>
        )}

        <div style={{ marginTop: 12 }}>
          <RangeToggle value={range} onChange={setRange} />
        </div>
        {perf.data?.curve &&
          (perf.data.benchmark || perf.data.macro_benchmark) && (
            <div style={{ display: "flex", gap: 16, marginTop: 10, fontSize: 11.5 }}>
              <LegendDot color={theme.growth} label="Pilot" />
              {perf.data.benchmark && (
                <LegendDot color={theme.textMuted} label="Benchmark" dashed />
              )}
              {perf.data.macro_benchmark && (
                <LegendDot color={theme.accent} label="S&P 500" dashed />
              )}
            </div>
          )}
      </section>

      {/* Sector allocation donut */}
      {pilot.sector_allocation.length > 0 && (
        <section className="card card-pad" style={{ marginBottom: 16 }}>
          <h2 style={{ fontSize: 16, margin: "0 0 12px" }}>Sector allocation</h2>
          <SectorDonut slices={pilot.sector_allocation} />
        </section>
      )}

      {/* Holdings */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>
          Holdings <span style={{ color: theme.textMuted }}>({pilot.holdings.length})</span>
        </h2>
        <div className="list">
          {pilot.holdings.map((hd) => (
            <div className="row" key={hd.symbol}>
              <div className="row-main">
                <span className="row-title">{hd.symbol}</span>
                <span className="row-sub">
                  {hd.name} · {hd.sector}
                </span>
              </div>
              <div className="row-end">
                <div className="num" style={{ fontWeight: 700 }}>
                  {(hd.weight * 100).toFixed(1)}%
                </div>
                <div className="row-sub num">
                  {hd.price == null ? "no quote" : fmtUsd(hd.price)}
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Recent trades */}
      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>Recent signal changes</h2>
        {pilot.recent_trades.length === 0 ? (
          <div className="empty" style={{ padding: 20 }}>
            No signal changes in the recent window.
          </div>
        ) : (
          <div className="list">
            {pilot.recent_trades.map((t, i) => (
              <div className="row" key={`${t.symbol}-${i}`}>
                <div className="row-main">
                  <span className="row-title">
                    <span
                      className={`badge ${SIDE_STYLE[t.side] ?? "badge-neutral"}`}
                      style={{ marginRight: 6, padding: "2px 7px" }}
                    >
                      {t.side}
                    </span>
                    {t.symbol}
                  </span>
                  <span className="row-sub">{t.sector ?? ""}</span>
                </div>
                <div className="row-end">
                  <div
                    className="num"
                    style={{ color: t.weight_delta >= 0 ? theme.growth : theme.decline }}
                  >
                    {t.weight_delta >= 0 ? "+" : ""}
                    {(t.weight_delta * 100).toFixed(1)}%
                  </div>
                  <div className="row-sub">{t.date}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Sticky Follow CTA */}
      <div
        style={{
          position: "sticky",
          bottom: "calc(76px + var(--safe-bottom))",
          marginTop: 8,
        }}
      >
        <button
          className="btn btn-primary btn-block"
          style={{ minHeight: 52, fontSize: 16, boxShadow: theme.growth + "33 0 8px 24px" }}
          onClick={() => setShowFollow(true)}
        >
          Follow · allocate {pilot.headline.sharpe != null ? `${fmtNum(pilot.headline.sharpe, 2)} Sharpe` : ""}
        </button>
      </div>

      {showFollow && (
        <FollowModal pilot={pilot} onClose={() => setShowFollow(false)} />
      )}
    </div>
  );
}

function PerfDelta({ curve }: { curve: { value: number }[] }) {
  const first = curve[0].value;
  const last = curve[curve.length - 1].value;
  const pct = ((last - first) / first) * 100;
  const up = pct >= 0;
  return (
    <span
      className="num"
      style={{ color: up ? theme.growth : theme.decline, fontWeight: 700, fontSize: 15 }}
    >
      {up ? "▲" : "▼"} {up ? "+" : ""}
      {pct.toFixed(2)}%
    </span>
  );
}

function LegendDot({ color, label, dashed }: { color: string; label: string; dashed?: boolean }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: theme.textSecondary }}>
      <span
        style={{
          width: 14,
          height: 0,
          borderTop: `2px ${dashed ? "dashed" : "solid"} ${color}`,
          display: "inline-block",
        }}
      />
      {label}
    </span>
  );
}

function BackLink() {
  return (
    <Link
      to="/"
      style={{ color: theme.textSecondary, fontSize: 14, display: "inline-block", marginBottom: 8 }}
    >
      ← Pilots
    </Link>
  );
}
