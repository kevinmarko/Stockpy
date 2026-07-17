import { useMemo } from "react";
import { Link } from "react-router-dom";
import { api, apiMeta } from "../api/client";
import type { PilotSummary } from "../api/types";
import { useApi } from "../hooks/useApi";
import { PilotCard, PopularCard } from "../components/PilotCard";
import { ErrorState, Loading, StaleDataNotice } from "../components/ui";
import { theme } from "../theme";

function Rail({
  title,
  sub,
  pilots,
  variant = "perf",
}: {
  title: string;
  sub?: string;
  pilots: PilotSummary[];
  variant?: "perf" | "popular";
}) {
  if (pilots.length === 0) return null;
  return (
    <section>
      <div className="rail-head">
        <h2>{title}</h2>
        {sub && <span className="rail-sub">{sub}</span>}
      </div>
      <div className="rail">
        {pilots.map((p) =>
          variant === "popular" ? (
            <PopularCard key={p.id} pilot={p} />
          ) : (
            <PilotCard key={p.id} pilot={p} />
          )
        )}
      </div>
    </section>
  );
}

/** Sort helper: nulls (missing metric) always sort last. */
function byDesc(sel: (p: PilotSummary) => number | null) {
  return (a: PilotSummary, b: PilotSummary) => {
    const av = sel(a);
    const bv = sel(b);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return bv - av;
  };
}

export function Marketplace() {
  const { data, loading, error, status, stale, cachedAt, reload } = useApi<
    PilotSummary[]
  >(() => api.listPilots(), []);

  const pilots = data ?? [];

  const topPerformers = useMemo(
    () =>
      [...pilots]
        .filter((p) => p.headline.deployable)
        .sort(byDesc((p) => p.headline.sharpe ?? p.headline.dsr)),
    [pilots]
  );

  const mostPopular = useMemo(
    () => [...pilots].sort(byDesc((p) => p.aum_proxy + p.followers_proxy)),
    [pilots]
  );

  const byCategory = useMemo(() => {
    const groups = new Map<string, PilotSummary[]>();
    for (const p of pilots) {
      if (!groups.has(p.category)) groups.set(p.category, []);
      groups.get(p.category)!.push(p);
    }
    return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [pilots]);

  return (
    <div className="screen">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
        }}
      >
        <div>
          <h1 className="screen-title">Pilots</h1>
          <p className="screen-sub">
            Copyable Stockpy strategies, ranked by honest backtests.
          </p>
        </div>
        {apiMeta.useMock && (
          <span className="chip" style={{ marginTop: 10 }} title="Running on mock data">
            demo
          </span>
        )}
      </div>

      {loading && <Loading lines={3} />}

      {!loading && error && (
        <ErrorState message={error} status={status} onRetry={reload} />
      )}

      {!loading && !error && (
        <>
          {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}
          <Rail
            title="Top Performers"
            sub="by Sharpe / DSR"
            pilots={topPerformers}
          />
          <Rail
            title="Most Popular"
            sub="by AUM & followers"
            pilots={mostPopular}
            variant="popular"
          />

          <div className="rail-head" style={{ marginTop: 24 }}>
            <h2>Browse by category</h2>
          </div>
          {byCategory.map(([cat, ps]) => (
            <Rail key={cat} title={cat} pilots={ps} />
          ))}

          {/* Explore: research surfaces that aren't a single Pilot */}
          <div className="rail-head" style={{ marginTop: 24 }}>
            <h2>Explore</h2>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 12 }}>
            <Link to="/models" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: 22 }} aria-hidden>🧠</div>
              <div style={{ fontWeight: 700, marginTop: 6 }}>The models</div>
              <div style={{ color: theme.textMuted, fontSize: 12, marginTop: 2 }}>
                CPCV-gated ML registry
              </div>
            </Link>
            <Link to="/pairs" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: 22 }} aria-hidden>🔗</div>
              <div style={{ fontWeight: 700, marginTop: 6 }}>Pairs radar</div>
              <div style={{ color: theme.textMuted, fontSize: 12, marginTop: 2 }}>
                Cointegrated stat-arb candidates
              </div>
            </Link>
            <Link to="/options" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: 22 }} aria-hidden>🎯</div>
              <div style={{ fontWeight: 700, marginTop: 6 }}>Options premium</div>
              <div style={{ color: theme.textMuted, fontSize: 12, marginTop: 2 }}>
                Per-symbol premium directives
              </div>
            </Link>
            <Link to="/attribution" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: 22 }} aria-hidden>🧮</div>
              <div style={{ fontWeight: 700, marginTop: 6 }}>Attribution</div>
              <div style={{ color: theme.textMuted, fontSize: 12, marginTop: 2 }}>
                Factor tilts & correlation clusters
              </div>
            </Link>
            <Link to="/observability" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: 22 }} aria-hidden>🛰️</div>
              <div style={{ fontWeight: 700, marginTop: 6 }}>Mission Control</div>
              <div style={{ color: theme.textMuted, fontSize: 12, marginTop: 2 }}>
                Risk, equity curve, regime &amp; forecast skill
              </div>
            </Link>
            <Link to="/strategy-health" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: 22 }} aria-hidden>🛡️</div>
              <div style={{ fontWeight: 700, marginTop: 6 }}>Strategy health</div>
              <div style={{ color: theme.textMuted, fontSize: 12, marginTop: 2 }}>
                Deployability gates, pilot by pilot
              </div>
            </Link>
          </div>

          <p
            style={{
              color: theme.textMuted,
              fontSize: 11.5,
              marginTop: 28,
              textAlign: "center",
              lineHeight: 1.5,
            }}
          >
            Metrics are read from PBO/DSR-gated validation reports. A Pilot that
            fails a gate is shown as not deployable — never hidden or inflated.
          </p>
        </>
      )}
    </div>
  );
}
