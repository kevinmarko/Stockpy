import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type {
  OptionsDirective,
  OptionsMatrix as OptionsMatrixT,
  OptionsLeg,
  Portfolio,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, StaleDataNotice } from "../components/ui";
import { Modal } from "../components/Modal";
import { fmtNum, fmtUsd, timeAgo } from "../format";
import { theme } from "../theme";
import { realizableTheta } from "../optionsHonesty";

function isCredit(d: OptionsDirective): boolean {
  return typeof d.Net_Premium === "number" && d.Net_Premium > 0;
}
function isDebit(d: OptionsDirective): boolean {
  return typeof d.Net_Premium === "number" && d.Net_Premium < 0;
}
function isActionable(d: OptionsDirective): boolean {
  return !!d.Action && d.Action !== "Wait";
}
function isFlagged(d: OptionsDirective): boolean {
  return d.Integrity_OK !== true;
}

type Filter = "all" | "actionable" | "credit" | "debit" | "flagged";
type Sort = "premium" | "ivr" | "sigma" | "symbol";

const FILTERS: { key: Filter; label: string; test: (d: OptionsDirective) => boolean }[] = [
  { key: "all", label: "All", test: () => true },
  { key: "actionable", label: "Actionable", test: isActionable },
  { key: "credit", label: "Credit", test: isCredit },
  { key: "debit", label: "Debit", test: isDebit },
  { key: "flagged", label: "Flagged", test: isFlagged },
];

/** Nulls always sort last, regardless of direction. */
function byNum(sel: (d: OptionsDirective) => number | null | undefined) {
  return (a: OptionsDirective, b: OptionsDirective) => {
    const av = sel(a);
    const bv = sel(b);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return bv - av;
  };
}

function actionBadgeClass(action: string | null | undefined): string {
  if (action === "Sell to Open") return "badge-good";
  if (action === "Buy to Open") return "badge-warn";
  return "badge-neutral";
}

/** Signed net premium, colored + worded (never color-alone). */
function PremiumLabel({ d }: { d: OptionsDirective }) {
  const v = d.Net_Premium;
  if (v == null || Number.isNaN(v)) return <span className="num muted">—</span>;
  const credit = v > 0;
  const debit = v < 0;
  const color = credit ? theme.growth : debit ? theme.decline : theme.textSecondary;
  const word = credit ? " credit" : debit ? " debit" : "";
  return (
    <span className="num" style={{ color, fontWeight: 700 }}>
      {fmtUsd(v)}
      {word}
    </span>
  );
}

function DirectiveCard({ d, onOpen }: { d: OptionsDirective; onOpen: () => void }) {
  return (
    <button
      type="button"
      className="card card-pad"
      onClick={onOpen}
      style={{
        display: "block",
        width: "100%",
        textAlign: "left",
        marginBottom: 10,
        cursor: "pointer",
        border: "1px solid var(--border)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontWeight: 700, fontSize: 16 }}>{d.Symbol}</span>
          {d.Stale === true && (
            <span className="badge badge-warn" title="Quote is stale">
              stale
            </span>
          )}
        </div>
        <span className="num" style={{ color: theme.textSecondary }}>
          {fmtUsd(d.Price ?? null)}
        </span>
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginTop: 8,
        }}
      >
        <span style={{ fontWeight: 600 }}>{d.Strategy ?? "—"}</span>
        <span className={`badge ${actionBadgeClass(d.Action)}`}>{d.Action ?? "—"}</span>
      </div>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 14,
          marginTop: 10,
          alignItems: "baseline",
        }}
      >
        <PremiumLabel d={d} />
        <span style={{ fontSize: 13, color: theme.textSecondary }}>
          IVR <span className="num">{fmtNum(d.IVR_Proxy ?? null, 0)}</span>
        </span>
        <span style={{ fontSize: 13, color: theme.textSecondary }}>{d.Trend_Bias ?? "—"}</span>
        {isFlagged(d) && (
          <span className="badge badge-bad" style={{ marginLeft: "auto" }}>
            ⚠ Integrity
          </span>
        )}
      </div>
    </button>
  );
}

function StatRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="row">
      <div className="row-main">
        <span className="row-title">{label}</span>
      </div>
      <div className="row-end num">{value}</div>
    </div>
  );
}

function LegsTable({ legs }: { legs: OptionsLeg[] }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr style={{ color: theme.textMuted, textAlign: "left" }}>
            <th style={{ padding: "4px 8px 4px 0", fontWeight: 600 }}>Side</th>
            <th style={{ padding: "4px 8px", fontWeight: 600 }}>Type</th>
            <th style={{ padding: "4px 8px", fontWeight: 600, textAlign: "right" }}>Strike</th>
            <th style={{ padding: "4px 8px", fontWeight: 600, textAlign: "right" }}>Price</th>
            <th style={{ padding: "4px 0 4px 8px", fontWeight: 600, textAlign: "right" }}>Δ</th>
          </tr>
        </thead>
        <tbody>
          {legs.map((leg, i) => (
            <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
              <td style={{ padding: "6px 8px 6px 0", color: leg.Side === "Short" ? theme.decline : theme.growth, fontWeight: 600 }}>
                {leg.Side}
              </td>
              <td style={{ padding: "6px 8px" }}>{leg.Type}</td>
              <td className="num" style={{ padding: "6px 8px", textAlign: "right" }}>
                {fmtUsd(leg.Strike)}
              </td>
              <td className="num" style={{ padding: "6px 8px", textAlign: "right" }}>
                {fmtUsd(leg.Price)}
              </td>
              <td className="num" style={{ padding: "6px 0 6px 8px", textAlign: "right" }}>
                {fmtNum(leg.Delta ?? null, 2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DetailSheet({ d, onClose }: { d: OptionsDirective; onClose: () => void }) {
  const theta = realizableTheta(d);
  const legs = Array.isArray(d.Legs) ? d.Legs : [];
  return (
    <Modal ariaLabel={`${d.Symbol} options directive`} onClose={onClose}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h2 style={{ fontSize: 18, margin: 0 }}>{d.Symbol}</h2>
        <span className="num" style={{ color: theme.textSecondary }}>{fmtUsd(d.Price ?? null)}</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}>
        <span style={{ fontWeight: 600 }}>{d.Strategy ?? "—"}</span>
        <span className={`badge ${actionBadgeClass(d.Action)}`}>{d.Action ?? "—"}</span>
        {d.Stale === true && <span className="badge badge-warn">stale</span>}
      </div>

      {legs.length > 0 && (
        <section style={{ marginTop: 16 }}>
          <h3 style={{ fontSize: 13, color: theme.textMuted, margin: "0 0 6px" }}>Legs</h3>
          <LegsTable legs={legs} />
        </section>
      )}

      <section style={{ marginTop: 16 }}>
        <h3 style={{ fontSize: 13, color: theme.textMuted, margin: "0 0 4px" }}>Signals</h3>
        <div className="list">
          <StatRow label="Net premium" value={<PremiumLabel d={d} />} />
          <StatRow label="GARCH σ (annual)" value={fmtNum(d.Sigma_GARCH ?? null, 3)} />
          <StatRow label="IVR (realized-vol rank)" value={fmtNum(d.IVR_Proxy ?? null, 1)} />
          <StatRow label="Aroon oscillator" value={fmtNum(d.Aroon_Oscillator ?? null, 1)} />
          <StatRow label="Coppock curve" value={fmtNum(d.Coppock_Curve ?? null, 1)} />
          <StatRow label="Trend bias" value={d.Trend_Bias ?? "—"} />
        </div>
      </section>

      <section style={{ marginTop: 16 }}>
        <h3 style={{ fontSize: 13, color: theme.textMuted, margin: "0 0 4px" }}>ATM Greeks</h3>
        <div className="list">
          <StatRow label="Δ delta" value={fmtNum(d.ATM_Delta ?? null, 3)} />
          <StatRow label="Γ gamma" value={fmtNum(d.ATM_Gamma ?? null, 3)} />
          <StatRow label="V vega" value={fmtNum(d.ATM_Vega ?? null, 3)} />
          <StatRow label="Θ theta/day" value={fmtNum(d.ATM_Theta_Daily ?? null, 3)} />
        </div>
        <p style={{ fontSize: 11.5, color: theme.textMuted, marginTop: 6, lineHeight: 1.45 }}>
          Computed for a hypothetical at-the-money <strong>call</strong> at this symbol's
          spot and σ — describes the symbol's ATM sensitivity, not this structure's exposure.
        </p>
      </section>

      <section style={{ marginTop: 16 }}>
        <h3 style={{ fontSize: 13, color: theme.textMuted, margin: "0 0 4px" }}>Realizable theta</h3>
        {theta.note ? (
          <>
            <div className="num muted" style={{ fontSize: 15 }}>—</div>
            <p style={{ fontSize: 11.5, color: theme.textMuted, marginTop: 4, lineHeight: 1.45 }}>
              {theta.note}
            </p>
          </>
        ) : (
          <div className="num" style={{ fontSize: 15, fontWeight: 700 }}>
            {fmtNum(theta.value, 3)}
            <span style={{ fontSize: 12, fontWeight: 400, color: theme.textMuted }}> /day</span>
          </div>
        )}
      </section>

      <section style={{ marginTop: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <h3 style={{ fontSize: 13, color: theme.textMuted, margin: 0 }}>Integrity</h3>
          <span className={`badge ${d.Integrity_OK === true ? "badge-good" : "badge-bad"}`}>
            {d.Integrity_OK === true ? "✓ clean" : "✗ flagged"}
          </span>
        </div>
        {Array.isArray(d.Integrity_Issues) && d.Integrity_Issues.length > 0 && (
          <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 12.5, color: theme.textSecondary, lineHeight: 1.5 }}>
            {d.Integrity_Issues.map((issue, i) => (
              <li key={i}>{issue}</li>
            ))}
          </ul>
        )}
      </section>

      <div style={{ marginTop: 18 }}>
        <Link to={`/symbol/${d.Symbol}`} className="btn" style={{ display: "inline-block" }}>
          View {d.Symbol} →
        </Link>
      </div>
    </Modal>
  );
}

/**
 * ATM Greeks roll-up (held, actionable). An UNWEIGHTED sum of per-contract ATM
 * Greeks across held symbols with an actionable directive — the same filter the
 * Streamlit panel's _render_portfolio_greeks_rollup applies. Gated on a REAL
 * held set from /portfolio: on a 404 (no account snapshot) it renders the honest
 * empty state, never a sum over the whole universe.
 */
function GreeksRollup({ directives }: { directives: OptionsDirective[] }) {
  const [open, setOpen] = useState(false);
  const portfolio = useApi<Portfolio>(() => api.getPortfolio(), []);

  const held = useMemo(() => {
    const p = portfolio.data;
    if (!p || !Array.isArray(p.positions)) return null;
    return new Set(p.positions.map((pos) => pos.symbol));
  }, [portfolio.data]);

  const included = useMemo(() => {
    if (!held) return [];
    return directives.filter(
      (d) =>
        held.has(d.Symbol) &&
        !(d.Strategy ?? "").toLowerCase().includes("cash") &&
        d.ATM_Delta != null &&
        d.ATM_Gamma != null &&
        d.ATM_Vega != null &&
        d.ATM_Theta_Daily != null,
    );
  }, [held, directives]);

  const sums = useMemo(() => {
    return included.reduce(
      (acc, d) => ({
        delta: acc.delta + (d.ATM_Delta as number),
        gamma: acc.gamma + (d.ATM_Gamma as number),
        vega: acc.vega + (d.ATM_Vega as number),
        theta: acc.theta + (d.ATM_Theta_Daily as number),
      }),
      { delta: 0, gamma: 0, vega: 0, theta: 0 },
    );
  }, [included]);

  return (
    <section className="card card-pad" style={{ marginTop: 16 }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          width: "100%",
          textAlign: "left",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          color: theme.textPrimary,
        }}
      >
        <span style={{ fontWeight: 700, fontSize: 15 }}>ATM Greeks roll-up (held, actionable)</span>
        <span style={{ color: theme.textMuted }}>{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div style={{ marginTop: 12 }}>
          {portfolio.loading ? (
            <Loading lines={1} />
          ) : !held ? (
            <div className="empty" style={{ padding: 18 }}>
              No account snapshot — connect a brokerage or run the pipeline to populate holdings.
            </div>
          ) : included.length === 0 ? (
            <div className="empty" style={{ padding: 18 }}>
              None of your held symbols has an actionable directive with ATM Greeks.
            </div>
          ) : (
            <>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 18 }}>
                <RollupStat label="Σ Δ delta" value={fmtNum(sums.delta, 3)} />
                <RollupStat label="Σ Γ gamma" value={fmtNum(sums.gamma, 3)} />
                <RollupStat label="Σ V vega" value={fmtNum(sums.vega, 3)} />
                <RollupStat label="Σ Θ theta/day" value={fmtNum(sums.theta, 3)} />
                <RollupStat
                  label="30d Θ carry"
                  value={fmtNum(sums.theta * 30, 2)}
                />
              </div>
              <p style={{ fontSize: 11.5, color: theme.textMuted, marginTop: 10, lineHeight: 1.5 }}>
                Unweighted sum of per-contract ATM Greeks across {included.length} held{" "}
                {included.length === 1 ? "symbol" : "symbols"} with an actionable directive.{" "}
                <strong>Not position-sized</strong> — this does not know your contract count
                (an equity share count is not a contract count). Greeks are for a hypothetical
                ATM <strong>call</strong> at each symbol's spot and σ, not the recommended
                structure. The 30-day theta carry assumes nothing moves — no price move, no vol
                move, no early assignment, no roll.
              </p>
            </>
          )}
        </div>
      )}
    </section>
  );
}

function RollupStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: theme.textMuted }}>{label}</div>
      <div className="num" style={{ fontSize: 15, fontWeight: 700 }}>{value}</div>
    </div>
  );
}

export function OptionsMatrix() {
  const nav = useNavigate();
  const { data, loading, error, status, stale, cachedAt, reload } = useApi<OptionsMatrixT>(
    () => api.getOptions(),
    [],
  );
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("premium");
  const [openSymbol, setOpenSymbol] = useState<string | null>(null);

  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  const directives = data?.directives ?? [];

  const cleanCount = directives.filter((d) => d.Integrity_OK === true).length;
  const flaggedCount = directives.length - cleanCount;

  const visible = useMemo(() => {
    const activeFilter = FILTERS.find((f) => f.key === filter)!;
    const rows = directives.filter(activeFilter.test);
    const sorted = [...rows];
    if (sort === "premium") sorted.sort(byNum((d) => d.Net_Premium));
    else if (sort === "ivr") sorted.sort(byNum((d) => d.IVR_Proxy));
    else if (sort === "sigma") sorted.sort(byNum((d) => d.Sigma_GARCH));
    else sorted.sort((a, b) => a.Symbol.localeCompare(b.Symbol));
    return sorted;
  }, [directives, filter, sort]);

  const openDirective = openSymbol
    ? directives.find((d) => d.Symbol === openSymbol) ?? null
    : null;

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
        ← Back
      </button>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h1 className="screen-title">Options premium</h1>
        {data?.as_of && (
          <span style={{ fontSize: 12, color: theme.textMuted }}>{timeAgo(data.as_of)}</span>
        )}
      </div>

      {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}

      {!loading && !error && data && directives.length === 0 && (
        <div className="empty" style={{ padding: 30 }}>
          {data.reason ?? "No options directives generated yet."}
        </div>
      )}

      {!loading && !error && data && directives.length > 0 && (
        <>
          {/* Read-only context row */}
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 8,
              margin: "8px 0 12px",
              fontSize: 12.5,
              color: theme.textSecondary,
            }}
          >
            <span className="chip">
              Target DTE {data.target_dte ?? "—"}
            </span>
            <span className="chip">VIX {fmtNum(data.vix ?? null, 1)}</span>
            <span className="chip">{data.market_regime ?? "—"}</span>
          </div>

          {/* Persistent honesty banner */}
          <div className="notice notice-warn" style={{ marginBottom: 12 }}>
            <span>
              <strong>IVR here is a realized-volatility rank</strong> (IVR_Proxy) — no options
              chain is fetched, so this is <em>not</em> true implied-vol rank. Advisory only; no
              orders are placed.
            </span>
          </div>

          {/* Integrity strip */}
          <div style={{ fontSize: 13, marginBottom: 12 }}>
            {flaggedCount === 0 ? (
              <span style={{ color: theme.growth }}>✅ {cleanCount}/{directives.length} legs clean</span>
            ) : (
              <span style={{ color: theme.caution }}>
                ⚠️ {flaggedCount} of {directives.length} flagged
              </span>
            )}
          </div>

          {/* Filter chips */}
          <div
            style={{
              display: "flex",
              gap: 8,
              overflowX: "auto",
              paddingBottom: 4,
              marginBottom: 10,
            }}
          >
            {FILTERS.map((f) => {
              const count = directives.filter(f.test).length;
              const active = filter === f.key;
              return (
                <button
                  key={f.key}
                  type="button"
                  onClick={() => setFilter(f.key)}
                  aria-pressed={active}
                  className="chip"
                  style={{
                    cursor: "pointer",
                    background: active ? theme.accent : "var(--surface-2)",
                    color: active ? "#04121e" : theme.textSecondary,
                    borderColor: active ? theme.accent : "var(--border)",
                    fontWeight: active ? 700 : 600,
                  }}
                >
                  {f.label} {count}
                </button>
              );
            })}
          </div>

          {/* Sort */}
          <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <span style={{ fontSize: 12.5, color: theme.textMuted }}>Sort</span>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as Sort)}
              style={{
                fontSize: 16,
                padding: "6px 10px",
                borderRadius: "var(--r-md)",
                background: "var(--surface-2)",
                color: theme.textPrimary,
                border: "1px solid var(--border)",
              }}
            >
              <option value="premium">Net premium ↓</option>
              <option value="ivr">IVR ↓</option>
              <option value="sigma">σ ↓</option>
              <option value="symbol">Symbol A–Z</option>
            </select>
          </label>

          {visible.length === 0 ? (
            <div className="empty" style={{ padding: 24 }}>
              No directives match this filter.
            </div>
          ) : (
            visible.map((d) => (
              <DirectiveCard key={d.Symbol} d={d} onOpen={() => setOpenSymbol(d.Symbol)} />
            ))
          )}

          <GreeksRollup directives={directives} />
        </>
      )}

      {openDirective && (
        <DetailSheet d={openDirective} onClose={() => setOpenSymbol(null)} />
      )}
    </div>
  );
}
