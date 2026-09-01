import { useMemo, useState } from "react";
import { Link } from "react-router";
import { api, apiMeta } from "../api/client";
import type { PilotSummary } from "../api/types";
import { useApi } from "../hooks/useApi";
import { PilotCard, PopularCard } from "../components/PilotCard";
import { ErrorState, Loading, StaleDataNotice, InfoTip } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
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

  const [activeCategory, setActiveCategory] = useState<string>("All");
  const categories = ["All", ...byCategory.map(([cat]) => cat)];

  const filteredPilots = useMemo(() => {
    if (activeCategory === "All") return pilots;
    return pilots.filter(p => p.category === activeCategory);
  }, [pilots, activeCategory]);

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
          <InfoTip triggerClassName="chip" triggerStyle={{ marginTop: "var(--s-2-5)" }} content="Running on mock data">
            demo
          </InfoTip>
        )}
      </div>

      <TabGuide tabKey="pilots" />

      {loading && <Loading lines={3} />}

      {!loading && error && (
        <ErrorState message={error} status={status} onRetry={reload} />
      )}

      {!loading && !error && (
        <>
          {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}
          <div style={{ display: "flex", gap: "var(--s-2)", overflowX: "auto", paddingBottom: "var(--s-2)", marginTop: "var(--s-4)", marginBottom: "var(--s-4)", scrollbarWidth: "none" }}>
            {categories.map(cat => (
              <button
                key={cat}
                className="chip"
                style={{
                  background: activeCategory === cat ? theme.accent : "transparent",
                  color: activeCategory === cat ? "#fff" : theme.textPrimary,
                  border: `1px solid ${activeCategory === cat ? theme.accent : theme.borderStrong}`,
                  cursor: "pointer",
                  fontSize: "var(--t-label)",
                  padding: "var(--s-1-5) var(--s-3)",
                  whiteSpace: "nowrap",
                }}
                onClick={() => setActiveCategory(cat)}
              >
                {cat}
              </button>
            ))}
          </div>

          {activeCategory === "All" ? (
            <>
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

              <div className="rail-head" style={{ marginTop: "var(--s-6)" }}>
                <h2>Browse by category</h2>
              </div>
              {byCategory.map(([cat, ps]) => (
                <Rail key={cat} title={cat} pilots={ps} />
              ))}
            </>
          ) : (
            <div className="rail" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "var(--s-4)", overflowX: "visible", whiteSpace: "normal" }}>
              {filteredPilots.map(p => (
                <PilotCard key={p.id} pilot={p} />
              ))}
            </div>
          )}

          {/* Explore: research surfaces that aren't a single Pilot */}
          <div className="rail-head" style={{ marginTop: "var(--s-6)" }}>
            <h2>Explore</h2>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "var(--s-3)" }}>
            <Link to="/models" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>🧠</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>The models</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                CPCV-gated ML registry
              </div>
            </Link>
            <Link to="/pairs" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>🔗</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>Pairs radar</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                Cointegrated stat-arb candidates
              </div>
            </Link>
            <Link to="/options" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>🎯</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>Options premium</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                Per-symbol premium directives
              </div>
            </Link>
            <Link to="/attribution" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>🧮</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>Attribution</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                Factor tilts & correlation clusters
              </div>
            </Link>
            <Link to="/observability" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>🛰️</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>Mission Control</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                Risk, equity curve, regime &amp; forecast skill
              </div>
            </Link>
            <Link to="/strategy-health" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>🛡️</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>Strategy health</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                Deployability gates, pilot by pilot
              </div>
            </Link>
            <Link to="/calibration" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>🎚️</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>Calibration</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                Did our actual calls work?
              </div>
            </Link>
            <Link to="/data-explorer" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>🗂️</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>Data explorer</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                Raw bars, fundamentals &amp; macro
              </div>
            </Link>
            <Link to="/symbol-screener" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>🔎</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>Symbol screener</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                Search &amp; filter beyond your watchlist
              </div>
            </Link>
            <Link to="/trade-history" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>💼</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>Trade history</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                Your full real, closed-trade ledger
              </div>
            </Link>
            <Link to="/signals" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>🧬</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>Signal breakdown</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                Per-module contributions by symbol
              </div>
            </Link>
            <Link to="/forecast" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>📈</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>Forecast viewer</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                Multi-horizon price forecast &amp; MC band
              </div>
            </Link>
            <Link to="/commands" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>⌨️</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>Commands</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                CLI autocomplete &amp; validation
              </div>
            </Link>
            <Link to="/sector-selection" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>🧩</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>Sector selection</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                Semantic related-sector ranking
              </div>
            </Link>
            <Link to="/research/trends-stitcher" className="card card-pad" style={{ textDecoration: "none" }}>
              <div style={{ fontSize: "var(--t-display)" }} aria-hidden>🧵</div>
              <div style={{ fontWeight: 700, marginTop: "var(--s-1-5)" }}>Trends stitcher</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                Overlapping-window SVI stitching demo
              </div>
            </Link>
          </div>

          <p
            style={{
              color: theme.textMuted,
              fontSize: "var(--t-footnote)",
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
