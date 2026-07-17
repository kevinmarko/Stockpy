import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type {
  CorrelationCluster,
  FactorExposure,
  PortfolioAttribution as PortfolioAttributionT,
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
    </div>
  );
}
