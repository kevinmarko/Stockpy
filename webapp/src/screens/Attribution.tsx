import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type {
  CorrelationCluster,
  EdgeRatioByStrategyRow,
  FactorExposure,
  PortfolioAttribution as PortfolioAttributionT,
  PortfolioTradeQuality as PortfolioTradeQualityT,
  TradeQualityPoint,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { EmptyState, ErrorState, Loading, StaleDataNotice } from "../components/ui";
import { fmtNum, fmtPct, timeAgo } from "../format";
import { theme } from "../theme";

/** A single cluster is "heavy" when it's a real (>1 symbol) grouping making up
 * more than 30% of held market value -- a hidden-concentration warning, not a
 * hard rule. Mirrors the old Streamlit Report Viewer's cluster-concentration
 * banner threshold. */
const HEAVY_CONCENTRATION_THRESHOLD = 0.3;

const FACTOR_LABELS: Record<keyof FactorExposure, string> = {
  value_z: "Value",
  quality_z: "Quality",
  lowvol_z: "Low volatility",
  size_z: "Size",
  multifactor_composite: "Composite tilt",
};

/** Zero-centered horizontal bar for one factor's z-score, clamped to [-3, 3]
 * for display (z-scores are winsorized at +/-3 upstream anyway --
 * signals/multifactor.py). `null` renders an honest empty track, never 0. */
function FactorTiltBar({ label, value }: { label: string; value: number | null }) {
  const clamped = value == null ? 0 : Math.max(-3, Math.min(3, value));
  const halfWidthPct = value == null ? 0 : (Math.abs(clamped) / 3) * 50;
  const positive = clamped >= 0;
  const color = value == null ? theme.textMuted : positive ? theme.growth : theme.decline;

  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
        <span style={{ color: theme.textSecondary }}>{label}</span>
        <span className="num" style={{ color, fontWeight: 700 }}>
          {value == null ? "—" : fmtNum(value, 2)}
        </span>
      </div>
      <div
        style={{
          position: "relative",
          height: 8,
          borderRadius: 4,
          background: theme.surface2,
          marginTop: 4,
          overflow: "hidden",
        }}
      >
        <div
          aria-hidden
          style={{
            position: "absolute",
            left: "50%",
            top: 0,
            bottom: 0,
            width: 1,
            background: theme.borderStrong,
          }}
        />
        {value != null && (
          <div
            style={{
              position: "absolute",
              top: 0,
              bottom: 0,
              left: positive ? "50%" : `${50 - halfWidthPct}%`,
              width: `${halfWidthPct}%`,
              borderRadius: 4,
              background: color,
            }}
          />
        )}
      </div>
    </div>
  );
}

function ClusterCard({ c }: { c: CorrelationCluster }) {
  return (
    <section className="card card-pad" style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 15, wordBreak: "break-word" }}>
          {c.symbols.join(" + ")}
        </div>
        {c.weight_pct != null && (
          <span className="badge badge-neutral" style={{ fontWeight: 700, whiteSpace: "nowrap" }}>
            {fmtPct(c.weight_pct, 0, { fromFraction: true })} of book
          </span>
        )}
      </div>
      {c.insufficient_history ? (
        <p style={{ color: theme.textMuted, fontSize: 12.5, marginTop: 8 }}>
          Not enough price history yet to correlate{" "}
          {c.n_symbols === 1 ? "this holding" : "these holdings"}.
        </p>
      ) : (
        <div style={{ display: "flex", gap: 20, marginTop: 10 }}>
          <div>
            <div style={{ fontSize: 11, color: theme.textMuted }}>Holdings</div>
            <div className="num" style={{ fontSize: 14 }}>{c.n_symbols}</div>
          </div>
          <div>
            <div style={{ fontSize: 11, color: theme.textMuted }}>Avg correlation</div>
            <div className="num" style={{ fontSize: 14 }}>
              {c.avg_intra_corr == null ? "—" : fmtNum(c.avg_intra_corr, 2)}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Trade Quality — MFE/MAE scatter (current signals) + Edge Ratio by strategy
// (closed trades). A separate section fed by its own endpoint
// (GET /portfolio/trade-quality), independent of the factor-exposure /
// correlation-cluster sections above -- a failure or cold-start here never
// blocks those.
// ---------------------------------------------------------------------------

type ScatterSort = "edge_ratio" | "mfe" | "mae" | "symbol";

/** Nulls always sort last, regardless of direction -- mirrors OptionsMatrix's
 * `byNum` convention (never treat "unknown" as "zero"). */
function byNumDesc<T>(sel: (row: T) => number | null | undefined) {
  return (a: T, b: T) => {
    const av = sel(a);
    const bv = sel(b);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return bv - av;
  };
}

const SCATTER_HEADERS: { key: ScatterSort; label: string }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "mfe", label: "MFE" },
  { key: "mae", label: "MAE" },
  { key: "edge_ratio", label: "Edge ratio" },
];

function MfeMaeScatterTable({ points }: { points: TradeQualityPoint[] }) {
  const [sort, setSort] = useState<ScatterSort>("edge_ratio");

  const sorted = useMemo(() => {
    const rows = [...points];
    if (sort === "symbol") rows.sort((a, b) => a.symbol.localeCompare(b.symbol));
    else if (sort === "mfe") rows.sort(byNumDesc((r: TradeQualityPoint) => r.mfe));
    else if (sort === "mae") rows.sort(byNumDesc((r: TradeQualityPoint) => r.mae));
    else rows.sort(byNumDesc((r: TradeQualityPoint) => r.edge_ratio));
    return rows;
  }, [points, sort]);

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
        <thead>
          <tr style={{ textAlign: "left" }}>
            {SCATTER_HEADERS.map((h) => (
              <th
                key={h.key}
                onClick={() => setSort(h.key)}
                style={{
                  padding: "4px 8px",
                  cursor: "pointer",
                  userSelect: "none",
                  fontWeight: sort === h.key ? 700 : 400,
                  color: sort === h.key ? theme.textPrimary : theme.textMuted,
                }}
              >
                {h.label}
                {sort === h.key ? " ▾" : ""}
              </th>
            ))}
            <th style={{ padding: "4px 8px", color: theme.textMuted }}>Action</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.symbol} style={{ borderTop: `1px solid ${theme.border}` }}>
              <td style={{ padding: "4px 8px", fontWeight: 600 }}>{r.symbol}</td>
              <td className="num" style={{ padding: "4px 8px" }}>
                {fmtPct(r.mfe, 1, { fromFraction: true })}
              </td>
              <td className="num" style={{ padding: "4px 8px" }}>
                {fmtPct(r.mae, 1, { fromFraction: true })}
              </td>
              <td className="num" style={{ padding: "4px 8px" }}>
                {r.edge_ratio == null ? "—" : fmtNum(r.edge_ratio, 2)}
              </td>
              <td style={{ padding: "4px 8px", color: theme.textSecondary }}>
                {r.action ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EdgeRatioByStrategyTable({ rows }: { rows: EdgeRatioByStrategyRow[] }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
        <thead>
          <tr style={{ color: theme.textMuted, textAlign: "left" }}>
            <th style={{ padding: "4px 8px" }}>Strategy</th>
            <th style={{ padding: "4px 8px" }}>Trades</th>
            <th style={{ padding: "4px 8px" }}>Avg MFE</th>
            <th style={{ padding: "4px 8px" }}>Avg MAE</th>
            <th style={{ padding: "4px 8px" }}>Avg edge ratio</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.strategy} style={{ borderTop: `1px solid ${theme.border}` }}>
              <td style={{ padding: "4px 8px", fontWeight: 600 }}>{r.strategy}</td>
              <td className="num" style={{ padding: "4px 8px" }}>{r.n_trades}</td>
              <td className="num" style={{ padding: "4px 8px" }}>
                {fmtPct(r.avg_mfe, 1, { fromFraction: true })}
              </td>
              <td className="num" style={{ padding: "4px 8px" }}>
                {fmtPct(r.avg_mae, 1, { fromFraction: true })}
              </td>
              <td className="num" style={{ padding: "4px 8px" }}>
                {r.avg_edge_ratio == null ? "—" : fmtNum(r.avg_edge_ratio, 2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TradeQualitySection({ data }: { data: PortfolioTradeQualityT }) {
  const byStrategy = data.edge_ratio_by_strategy;
  return (
    <>
      <h2 style={{ fontSize: 15, marginBottom: 4 }}>Trade quality</h2>
      <p className="screen-sub" style={{ marginTop: 0 }}>
        Maximum favorable/adverse excursion and edge ratio -- how much upside a
        position captured relative to the drawdown it survived along the way.
      </p>

      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h3 style={{ fontSize: 13, margin: "0 0 4px", color: theme.textSecondary }}>
          MFE vs. MAE — current signals
        </h3>
        {data.scatter.length === 0 ? (
          <EmptyState
            title="No excursion data yet"
            hint="These populate once a symbol has enough trade history to compute MFE/MAE."
          />
        ) : (
          <>
            <MfeMaeScatterTable points={data.scatter} />
            <p style={{ color: theme.textMuted, fontSize: 11, marginTop: 8 }}>
              Tap a column to sort. MFE/MAE are fractions of entry price.
            </p>
          </>
        )}
      </section>

      <section className="card card-pad" style={{ marginBottom: 16 }}>
        <h3 style={{ fontSize: 13, margin: "0 0 4px", color: theme.textSecondary }}>
          Edge ratio by strategy — closed trades
        </h3>
        {byStrategy.by_strategy.length === 0 ? (
          <EmptyState
            title="No closed trades yet"
            hint={byStrategy.reason ?? "This populates once trades close and price history is cached."}
          />
        ) : (
          <EdgeRatioByStrategyTable rows={byStrategy.by_strategy} />
        )}
      </section>

      {data.as_of && (
        <p style={{ color: theme.textMuted, fontSize: 11, marginTop: -8, marginBottom: 16 }}>
          As of {timeAgo(data.as_of)}
        </p>
      )}
    </>
  );
}

function AttributionBody({ data }: { data: PortfolioAttributionT }) {
  const fe = data.factor_exposure;
  const cc = data.correlation_clusters;
  const heavy = cc.clusters.filter(
    (c) => !c.insufficient_history && (c.weight_pct ?? 0) > HEAVY_CONCENTRATION_THRESHOLD
  );

  return (
    <>
      <h2 style={{ fontSize: 15, marginTop: 8, marginBottom: 8 }}>Factor exposure</h2>
      {fe.coverage.held_count === 0 ? (
        <EmptyState
          title="No holdings yet"
          hint="Connect a brokerage or run the pipeline to see your factor tilts."
        />
      ) : fe.coverage.matched_count === 0 ? (
        <EmptyState
          title="No factor data yet"
          hint={fe.reason ?? "Run the pipeline to score your holdings."}
        />
      ) : (
        <section className="card card-pad" style={{ marginBottom: 16 }}>
          {(Object.keys(FACTOR_LABELS) as (keyof FactorExposure)[]).map((key) => (
            <FactorTiltBar key={key} label={FACTOR_LABELS[key]} value={fe.exposures[key]} />
          ))}
          <p style={{ color: theme.textMuted, fontSize: 11.5, marginTop: 8 }}>
            Covers {fmtPct(fe.coverage.matched_value_pct, 0, { fromFraction: true })} of your
            book ({fe.coverage.matched_count} of {fe.coverage.held_count} holdings scored).
            {fe.coverage.unmatched_symbols.length > 0 && (
              <> Not yet scored: {fe.coverage.unmatched_symbols.join(", ")}.</>
            )}
          </p>
          {fe.as_of && (
            <p style={{ color: theme.textMuted, fontSize: 11, marginTop: 4 }}>
              As of {timeAgo(fe.as_of)}
            </p>
          )}
        </section>
      )}

      <h2 style={{ fontSize: 15, marginBottom: 4 }}>Correlation clusters</h2>
      <p className="screen-sub" style={{ marginTop: 0 }}>
        Holdings that tend to move together, over the last {cc.lookback_days} trading days.
      </p>

      {heavy.length > 0 && (
        <div className="notice notice-warn" style={{ marginBottom: 12 }}>
          <span>
            High concentration: {heavy.map((c) => c.symbols.join("+")).join(", ")} move together
            and make up a large share of your book. Consider diversifying.
          </span>
        </div>
      )}

      {cc.clusters.length === 0 ? (
        <EmptyState
          title="No clusters yet"
          hint={cc.reason ?? "Not enough price history to correlate your holdings."}
        />
      ) : (
        cc.clusters.map((c) => <ClusterCard key={c.cluster_id} c={c} />)
      )}
    </>
  );
}

export function Attribution() {
  const nav = useNavigate();
  const { data, loading, error, status, stale, cachedAt, reload } =
    useApi<PortfolioAttributionT>(() => api.getPortfolioAttribution(), []);
  // Independent fetch: a Trade Quality failure/cold-start never blocks the
  // factor exposure / correlation cluster sections above (own loading/error
  // handling, no shared ErrorState -- mirrors Portfolio.tsx's secondary
  // `realized`/`equity` sections).
  const tq = useApi<PortfolioTradeQualityT>(() => api.getPortfolioTradeQuality(), []);
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

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
      <h1 className="screen-title">Portfolio attribution</h1>
      <p className="screen-sub">
        What factor tilts and hidden concentration your actual holdings carry
        -- a read of your current book, not a backtest.
      </p>

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        <>
          {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}
          <AttributionBody data={data} />
        </>
      )}

      {tq.loading ? (
        <Loading lines={2} />
      ) : tq.data ? (
        <TradeQualitySection data={tq.data} />
      ) : (
        <EmptyState
          title="No trade quality data yet"
          hint={tq.error ?? "Run the pipeline and record a closed trade to see this section."}
        />
      )}
    </div>
  );
}
