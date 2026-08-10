import { useMemo, useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import type {
  Follow,
  PerfRange,
  Portfolio as PortfolioT,
  PilotSummary,
  EquityCurveResponse,
  RealizedPerformance,
  UniverseResponse,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { DynamicGrid, resetGridLayout } from "../components/DynamicGrid";
import type { ResponsiveLayouts } from "react-grid-layout";
import { PerfLine } from "../components/charts";
import { RangeToggle } from "../components/RangeToggle";
import { TabGuide } from "../components/TabGuide";
import { ErrorState, Loading, Tile, InfoTip, Button } from "../components/ui";
import { Toggle } from "../components/Toggle";
import { fmtNum, fmtPct, fmtSignedUsd, fmtUsd, timeAgo } from "../format";
import { theme } from "../theme";

/**
 * ReconciliationSection — "held vs. signal" reconciliation, the webapp port
 * of the legacy Streamlit Paper Monitor tab's two reconciliation metrics.
 * Entirely client-side (no backend change): derived from the SAME
 * `GET /portfolio` + `GET /universe` fetches this screen already makes.
 *
 * "Held, no signal" mirrors the legacy computation exactly: a held position
 * whose symbol doesn't appear AT ALL in the tracked universe (`GET /universe`
 * is `pilots/symbols.py::list_universe(snapshot)`'s output -- built from the
 * same `state_snapshot.json` "signals" list Streamlit's `projected` set read).
 *
 * "Signalled, not held" is a DELIBERATE refinement, not a literal port: the
 * legacy panel's `projected - held` includes every action (HOLD/SELL too),
 * which floods the list with every non-held tracked symbol regardless of
 * whether there's anything actionable about it. This filters to symbols
 * whose latest action contains "BUY" (covers "BUY"/"STRONG BUY", matching
 * `pilots/symbols.py::list_recommendations`'s own convention) -- the only
 * case where "not held" is actually actionable.
 */
function ReconciliationSection({
  positions,
  universe,
}: {
  positions: PortfolioT["positions"];
  universe: UniverseResponse | null;
}) {
  const { heldNoSignal, signalledNotHeld } = useMemo(() => {
    const heldSymbols = new Set(positions.map((p) => p.symbol));
    const symbols = universe?.symbols ?? [];
    const universeSymbols = new Set(symbols.map((s) => s.symbol));
    const heldNoSignal = [...heldSymbols].filter((s) => !universeSymbols.has(s)).sort();
    const signalledNotHeld = symbols
      .filter((s) => !heldSymbols.has(s.symbol) && s.action != null && s.action.toUpperCase().includes("BUY"))
      .map((s) => s.symbol)
      .sort();
    return { heldNoSignal, signalledNotHeld };
  }, [positions, universe]);

  if (!universe) return null;

  return (
    <section style={{ marginBottom: "var(--s-4)" }}>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-footnote)", margin: "0 0 var(--s-3)" }}>
        Held positions with no tracked signal, and BUY-signalled symbols you don't hold.
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-3)", marginBottom: "var(--s-3)" }}>
        <Tile label="Held, no signal" value={heldNoSignal.length} tone={heldNoSignal.length > 0 ? "neg" : undefined} />
        <Tile label="Signalled, not held" value={signalledNotHeld.length} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--s-3)" }}>
        <div data-testid="held-no-signal-list">
          <div style={{ fontSize: "var(--t-label)", fontWeight: 700, marginBottom: "var(--s-1)" }}>
            Held, no signal
          </div>
          {heldNoSignal.length === 0 ? (
            <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>—</p>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-1)" }}>
              {heldNoSignal.map((s) => (
                <Link key={s} to={`/symbol/${s}`} className="chip">
                  {s}
                </Link>
              ))}
            </div>
          )}
        </div>
        <div data-testid="signalled-not-held-list">
          <div style={{ fontSize: "var(--t-label)", fontWeight: 700, marginBottom: "var(--s-1)" }}>
            Signalled, not held
          </div>
          {signalledNotHeld.length === 0 ? (
            <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>—</p>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-1)" }}>
              {signalledNotHeld.map((s) => (
                <Link key={s} to={`/symbol/${s}`} className="chip">
                  {s}
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

const PORTFOLIO_LAYOUTS: ResponsiveLayouts = {
  lg: [
    { i: "summary", x: 0, y: 0, w: 12, h: 5 },
    { i: "equity", x: 0, y: 5, w: 12, h: 12 },
    { i: "reconciliation", x: 0, y: 17, w: 12, h: 7 },
    { i: "realized", x: 0, y: 24, w: 7, h: 10 },
    { i: "follows", x: 7, y: 24, w: 5, h: 10 },
    { i: "positions", x: 0, y: 34, w: 12, h: 10 },
  ],
  md: [
    { i: "summary", x: 0, y: 0, w: 10, h: 5 },
    { i: "equity", x: 0, y: 5, w: 10, h: 12 },
    { i: "reconciliation", x: 0, y: 17, w: 10, h: 7 },
    { i: "realized", x: 0, y: 24, w: 5, h: 10 },
    { i: "follows", x: 5, y: 24, w: 5, h: 10 },
    { i: "positions", x: 0, y: 34, w: 10, h: 10 },
  ],
  sm: [
    { i: "summary", x: 0, y: 0, w: 6, h: 7 },
    { i: "equity", x: 0, y: 7, w: 6, h: 12 },
    { i: "reconciliation", x: 0, y: 19, w: 6, h: 8 },
    { i: "realized", x: 0, y: 27, w: 6, h: 10 },
    { i: "follows", x: 0, y: 37, w: 6, h: 10 },
    { i: "positions", x: 0, y: 47, w: 6, h: 10 },
  ],
};

export function Portfolio() {
  const [range, setRange] = useState<PerfRange>("3M");
  const [showBuyingPower, setShowBuyingPower] = useState(false);

  const port = useApi<PortfolioT>(() => api.getPortfolio(), []);
  const equity = useApi<EquityCurveResponse>(() => api.getEquityCurve(range), [range]);
  const follows = useApi<Follow[]>(() => api.getFollows(), []);
  const pilots = useApi<PilotSummary[]>(() => api.listPilots(), []);
  const realized = useApi<RealizedPerformance>(() => api.getRealized(), []);
  const universe = useApi<UniverseResponse>(() => api.getUniverse(), []);

  useAutoPoll(
    () => {
      port.reload();
      equity.reload();
      follows.reload();
      pilots.reload();
      realized.reload();
      universe.reload();
    },
    "portfolio",
    { hasError: port.error != null }
  );

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
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 className="screen-title">Portfolio</h1>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-3)" }}>
          <span style={{ fontSize: "var(--t-caption)", color: theme.textMuted, display: "flex", alignItems: "center", gap: "var(--s-1-5)" }}>
            {p.source} · {timeAgo(p.fetched_at)}
            {p.is_stale === true &&
              (p.age_hours != null ? (
                <InfoTip triggerClassName="badge badge-warn" content={`${fmtNum(p.age_hours, 1)}h old`}>
                  stale
                </InfoTip>
              ) : (
                <span className="badge badge-warn">stale</span>
              ))}
          </span>
          <Button variant="neutral" onClick={() => resetGridLayout("portfolio")}>
            Reset Layout
          </Button>
        </div>
      </div>

      <TabGuide tabKey="portfolio" />

      <div style={{ flex: 1, minHeight: 0 }}>
        <DynamicGrid layoutKey="portfolio" defaultLayouts={PORTFOLIO_LAYOUTS}>
          {/* Summary */}
          <div key="summary" className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <div className="drag-handle" style={{ cursor: 'grab', marginBottom: 'var(--s-2)', paddingBottom: 'var(--s-1)', borderBottom: `1px solid ${theme.border}` }}>
              <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Summary</h2>
            </div>
            <div style={{ display: "flex", gap: "var(--s-4)", alignItems: "center", marginBottom: "var(--s-3)" }}>
              <div>
                <div className="tile-label">Total equity</div>
                <div
                  className="num"
                  style={{ fontSize: 30, fontWeight: 800, letterSpacing: "-0.02em" }}
                >
                  {fmtUsd(p.total_equity)}
                </div>
                <div
                  className="num"
                  style={{
                    color: p.total_unrealized_pl >= 0 ? theme.growth : theme.decline,
                    fontWeight: 700,
                    fontSize: "var(--t-body)",
                  }}
                >
                  {fmtSignedUsd(p.total_unrealized_pl)} unrealized
                </div>
              </div>
            </div>
            <div className="tiles" style={{ marginTop: "auto" }}>
              <Tile label="Buying power" value={fmtUsd(p.buying_power)} />
              <Tile
                label="Unrealized P&L"
                value={fmtSignedUsd(p.total_unrealized_pl)}
                tone={p.total_unrealized_pl >= 0 ? "pos" : "neg"}
              />
              <Tile label="Dividends" value={fmtUsd(p.total_dividends)} />
              <Tile label="Positions" value={p.position_count} />
            </div>
          </div>

          {/* Equity curve */}
          <section key="equity" className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <div className="drag-handle" style={{ cursor: 'grab', marginBottom: 'var(--s-3)', paddingBottom: 'var(--s-1)', borderBottom: `1px solid ${theme.border}` }}>
              <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Account value</h2>
            </div>
            <div style={{ flex: 1, minHeight: 0 }}>
              {equity.loading ? (
                <div className="skeleton" style={{ height: "100%" }} />
              ) : equity.data?.curve && equity.data.curve.length > 1 ? (
                <PerfLine
                  data={equity.data.curve}
                  macroBenchmark={showBuyingPower ? equity.data.buying_power_curve : null}
                  macroLabel="Buying power"
                  macroSecondaryAxis
                />
              ) : (
                <div className="empty" style={{ padding: "var(--s-7-5)", height: "100%" }}>
                  Not enough account history yet.
                </div>
              )}
            </div>
        <div style={{ marginTop: "var(--s-3)", display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: "var(--s-3)" }}>
          <RangeToggle value={range} onChange={setRange} />
          <Toggle
            label="Overlay buying power"
            checked={showBuyingPower}
            onChange={setShowBuyingPower}
            disabled={!equity.data?.buying_power_curve || equity.data.buying_power_curve.length === 0}
            dataTestId="buying-power-overlay-checkbox"
          />
        </div>
        {showBuyingPower && (!equity.data?.buying_power_curve || equity.data.buying_power_curve.length === 0) && (
          <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-1-5)" }}>
            No buying-power history in the selected range.
          </p>
        )}
      </section>

      {/* Reconciliation */}
      <div key="reconciliation" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <div className="drag-handle" style={{ cursor: 'grab', marginBottom: 'var(--s-2)', paddingBottom: 'var(--s-1)', borderBottom: `1px solid ${theme.border}` }}>
          <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Reconciliation</h2>
        </div>
        <ReconciliationSection positions={p.positions} universe={universe.data ?? null} />
      </div>

      {/* Realized performance (broker order history, FIFO round-trips) */}
      <section key="realized" className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <div className="drag-handle" style={{ cursor: 'grab', marginBottom: 'var(--s-1)', paddingBottom: 'var(--s-1)', borderBottom: `1px solid ${theme.border}` }}>
          <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Realized performance</h2>
        </div>
        <p style={{ color: theme.textMuted, fontSize: "var(--t-footnote)", margin: "0 0 var(--s-3)" }}>
          Reconstructed from your Robinhood filled-order history (closed round-trips).
        </p>
        {realized.loading ? (
          <Loading lines={2} />
        ) : !realized.data || !realized.data.available ? (
          <div className="empty" style={{ padding: 22 }}>
            No realized trades cached yet.
          </div>
        ) : (
          <>
            <div className="tiles">
              <Tile
                label="Realized P&L"
                value={fmtSignedUsd(realized.data.summary.total_realized_pnl)}
                tone={realized.data.summary.total_realized_pnl >= 0 ? "pos" : "neg"}
              />
              <Tile
                label="Win rate"
                value={fmtPct(realized.data.summary.win_rate, 0, { fromFraction: true })}
              />
              <Tile
                label="Profit factor"
                value={fmtNum(realized.data.summary.profit_factor, 2)}
              />
              <Tile label="Trades" value={realized.data.summary.n_trades} />
            </div>
            <div className="tiles" style={{ marginTop: "var(--s-2)" }}>
              <Tile 
                label="Avg return" 
                value={fmtPct(realized.data.summary.avg_return_pct, 1, { signed: true })}
                tone={(realized.data.summary.avg_return_pct ?? 0) >= 0 ? "pos" : "neg"}
              />
              <Tile 
                label="Avg win" 
                value={fmtSignedUsd(realized.data.summary.avg_win)} 
                tone="pos"
              />
              <Tile 
                label="Avg loss" 
                value={fmtSignedUsd(realized.data.summary.avg_loss)} 
                tone="neg"
              />
              <Tile 
                label="Avg hold" 
                value={realized.data.summary.avg_holding_days != null ? `${fmtNum(realized.data.summary.avg_holding_days, 0)}d` : "—"} 
              />
            </div>
            {realized.data.trades.length > 0 && (
              <div className="list" style={{ marginTop: "var(--s-3)" }}>
                {realized.data.trades.slice(0, 8).map((t, i) => (
                  <Link className="row" key={`${t.symbol}-${i}`} to={`/symbol/${t.symbol}`}>
                    <div className="row-main">
                      <span className="row-title">{t.symbol}</span>
                      <span className="row-sub">
                        {t.quantity == null ? "—" : fmtNum(t.quantity, 0)} sh ·{" "}
                        {t.holding_days == null ? "—" : `${fmtNum(t.holding_days, 0)}d`}
                      </span>
                    </div>
                    <div className="row-end">
                      <div
                        className="num"
                        style={{
                          fontWeight: 700,
                          color: (t.realized_pnl ?? 0) >= 0 ? theme.growth : theme.decline,
                        }}
                      >
                        {fmtSignedUsd(t.realized_pnl)}
                      </div>
                      <div className="num row-sub">
                        {fmtPct(t.return_pct, 1, { signed: true })}
                      </div>
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      {/* Active follows */}
      <section key="follows" className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <div className="drag-handle" style={{ cursor: 'grab', marginBottom: 'var(--s-1)', paddingBottom: 'var(--s-1)', borderBottom: `1px solid ${theme.border}` }}>
          <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Active follows</h2>
        </div>
        {follows.loading ? (
          <Loading lines={2} />
        ) : (follows.data ?? []).length === 0 ? (
          <div className="empty" style={{ padding: 22 }}>
            You aren't following any Pilots yet.
            <div style={{ marginTop: "var(--s-2-5)" }}>
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
        <p style={{ color: theme.textMuted, fontSize: "var(--t-footnote)", marginTop: "var(--s-3)" }}>
          Follows build a gated, paper-first order queue. Confirm each queue in the
          robinhood-execution flow — nothing is placed automatically.
        </p>
      </section>

      {/* Positions */}
      <section key="positions" className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <div className="drag-handle" style={{ cursor: 'grab', marginBottom: 'var(--s-1)', paddingBottom: 'var(--s-1)', borderBottom: `1px solid ${theme.border}` }}>
          <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Positions</h2>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gridAutoRows: "max-content", gap: "var(--s-3)" }}>
          {p.positions.map((pos) => {
            const isPositive = (pos.unrealized_pl ?? 0) >= 0;
            const bgColor = isPositive ? "rgba(16, 185, 129, 0.05)" : "rgba(239, 68, 68, 0.05)";
            const borderColor = isPositive ? "rgba(16, 185, 129, 0.2)" : "rgba(239, 68, 68, 0.2)";
            return (
              <Link
                key={pos.symbol}
                to={`/symbol/${pos.symbol}`}
                className="card card-pad"
                style={{
                  backgroundColor: bgColor,
                  borderColor: borderColor,
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--s-2)",
                  textDecoration: "none"
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <span style={{ fontSize: "var(--t-title)", fontWeight: 700, color: theme.textPrimary }}>{pos.symbol}</span>
                  <span className="num" style={{ fontWeight: 700, fontSize: "var(--t-subhead)", color: theme.textPrimary }}>
                    {fmtUsd(pos.market_value)}
                  </span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", color: theme.textMuted, fontSize: "var(--t-body)" }}>
                  <span>
                    {pos.qty} sh @ {fmtUsd(pos.avg_cost)}
                    {pos.current_price != null ? ` (now ${fmtUsd(pos.current_price)})` : ""}
                  </span>
                  <div
                    className="num"
                    style={{
                      color: isPositive ? theme.growth : theme.decline,
                      fontWeight: 600
                    }}
                  >
                    {fmtSignedUsd(pos.unrealized_pl)} ({fmtPct(pos.unrealized_pl_pct, 1, { signed: true })})
                  </div>
                </div>
              </Link>
            );
          })}
            </div>
          </section>
        </DynamicGrid>
      </div>
    </div>
  );
}
