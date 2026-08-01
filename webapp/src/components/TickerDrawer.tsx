import { Link } from "react-router";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import type { RiskGateBlockEntry, SymbolDetail } from "../api/types";
import { Loading, Tile } from "./ui";
import { fmtNum, fmtPct, timeAgo } from "../format";

interface TickerDrawerProps {
  symbol: string;
  onClose: () => void;
}

/**
 * TickerDrawer — a fast, real-data slide-over for inspecting one symbol from
 * anywhere (omni-search, Forecast Viewer). Sources GET /symbols/{ticker} for
 * sizing/signal/risk fields and the observability summary's risk-gate-block
 * log (filtered client-side by symbol) for recent rejections. Every field is
 * `null`/absent when the backend didn't compute it this cycle -- never a
 * fabricated placeholder (CONSTRAINT #4). There is no per-symbol price-history
 * endpoint reachable from the client today, so this intentionally has no
 * trend sparkline rather than fake one.
 */
export function TickerDrawer({ symbol, onClose }: TickerDrawerProps) {
  const { data, loading, error } = useApi<SymbolDetail>(() => api.getSymbol(symbol), [symbol]);
  const blocks = useApi(() => api.getObservabilitySummary("1M", 30), [symbol]);

  const notionalValue =
    data?.identity.price != null && data?.identity.shares != null
      ? data.identity.price * data.identity.shares
      : null;
  const weight = data?.advisory.position_pct;
  const scoreComponents = data?.factors.score_components;
  const rejections: RiskGateBlockEntry[] =
    blocks.data?.risk_gate_blocks.entries.filter((e) => e.symbol === symbol) ?? [];

  return (
    <div
      role="dialog"
      aria-label={`${symbol} inspection drawer`}
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: "min(100vw, 480px)",
        background: "var(--surface)",
        borderLeft: "1px solid var(--border)",
        boxShadow: "-10px 0 30px rgba(0, 0, 0, 0.5)",
        zIndex: 9000,
        display: "flex",
        flexDirection: "column",
        animation: "slideLeft 0.2s ease-out",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "var(--s-4)",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          background: "var(--surface-2)",
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
            <h2 style={{ margin: 0, fontSize: "var(--t-title)", fontWeight: 700, color: "var(--text-primary)" }}>
              {symbol}
            </h2>
            {data?.identity.sector && (
              <span style={{ fontSize: "var(--t-caption)", padding: "2px 6px", borderRadius: "var(--r-xs)", background: "var(--surface-3)", color: "var(--accent)" }}>
                {data.identity.sector}
              </span>
            )}
          </div>
          <div style={{ color: "var(--text-muted)", fontSize: "var(--t-caption)", marginTop: "2px" }}>
            Inspection & Signal Breakdown
          </div>
        </div>
        <button
          onClick={onClose}
          aria-label="Close"
          style={{
            background: "none",
            border: "none",
            color: "var(--text-muted)",
            fontSize: "24px",
            cursor: "pointer",
            padding: "0 var(--s-2)",
          }}
        >
          ×
        </button>
      </div>

      {/* Content body */}
      <div style={{ flex: 1, overflowY: "auto", padding: "var(--s-4)", display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
        {loading && <Loading lines={6} />}
        {!loading && error && (
          <div className="empty" style={{ padding: "var(--s-4)" }}>
            {error}
          </div>
        )}
        {!loading && !error && data && (
          <>
            {data.reason && (
              <p style={{ color: "var(--text-muted)", fontSize: "var(--t-caption)", margin: 0 }}>{data.reason}</p>
            )}
            {data.as_of && (
              <p style={{ color: "var(--text-muted)", fontSize: "var(--t-micro)", margin: 0 }}>
                As of {timeAgo(data.as_of)}
              </p>
            )}

            {/* Advisory & pricing snapshot */}
            <div>
              <h3 style={{ fontSize: "var(--t-callout)", margin: "0 0 var(--s-2)", color: "var(--text-primary)" }}>
                Advisory Snapshot
              </h3>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--s-2)" }}>
                <Tile label="Action" value={data.advisory.action ?? "—"} />
                <Tile label="Score" value={data.advisory.score == null ? "—" : fmtNum(data.advisory.score, 2)} />
                <Tile label="Price" value={data.identity.price == null ? "—" : `$${fmtNum(data.identity.price, 2)}`} />
                <Tile label="Conviction" value={data.advisory.conviction == null ? "—" : fmtNum(data.advisory.conviction, 2)} />
              </div>
            </div>

            {/* Portfolio Sizing */}
            <div>
              <h3 style={{ fontSize: "var(--t-callout)", margin: "0 0 var(--s-2)", color: "var(--text-primary)" }}>
                Portfolio Sizing & Cap
              </h3>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--s-2)" }}>
                <Tile label="Notional Value" value={notionalValue == null ? "—" : `$${fmtNum(notionalValue, 0)}`} />
                <Tile
                  label="Portfolio Weight"
                  value={weight == null ? "—" : fmtPct(weight, 1, { fromFraction: true })}
                />
                <Tile
                  label="Kelly Target"
                  value={data.advisory.kelly_target == null ? "—" : fmtPct(data.advisory.kelly_target, 1, { fromFraction: true })}
                />
                <Tile
                  label="Max Position Weight"
                  value={fmtPct(data.sizing.max_position_weight, 1, { fromFraction: true })}
                />
              </div>
            </div>

            {/* Signal Score Breakdown */}
            <div>
              <h3 style={{ fontSize: "var(--t-callout)", margin: "0 0 var(--s-2)", color: "var(--text-primary)" }}>
                Signal Score Breakdown
              </h3>
              {!scoreComponents || Object.keys(scoreComponents).length === 0 ? (
                <div className="empty" style={{ padding: "var(--s-3)" }}>
                  No per-module score breakdown for this symbol this cycle.
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
                  {Object.entries(scoreComponents).map(([name, score]) => (
                    <div
                      key={name}
                      style={{
                        background: "var(--surface-2)",
                        padding: "var(--s-2-5) var(--s-3)",
                        borderRadius: "var(--r-xs)",
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                      }}
                    >
                      <div style={{ fontSize: "var(--t-callout)", fontWeight: 600 }}>{name}</div>
                      <span
                        style={{
                          fontWeight: 700,
                          fontSize: "var(--t-callout)",
                          color: score > 0 ? "var(--growth)" : score < 0 ? "var(--decline)" : "var(--text-muted)",
                        }}
                      >
                        {score > 0 ? `+${fmtNum(score, 2)}` : fmtNum(score, 2)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Recent Block / Rejection History */}
            <div>
              <h3 style={{ fontSize: "var(--t-callout)", margin: "0 0 var(--s-2)", color: "var(--text-primary)" }}>
                Recent Risk Block History
              </h3>
              {blocks.loading ? (
                <Loading lines={2} />
              ) : rejections.length === 0 ? (
                <div className="empty" style={{ padding: "var(--s-3)" }}>
                  No risk-gate blocks recorded for {symbol} in the current window.
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
                  {rejections.map((rej, i) => (
                    <div
                      key={i}
                      style={{
                        background: "var(--surface-2)",
                        borderLeft: "3px solid var(--caution)",
                        padding: "var(--s-2-5) var(--s-3)",
                        borderRadius: "0 var(--r-xs) var(--r-xs) 0",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--t-caption)" }}>
                        <span style={{ fontWeight: 700, color: "var(--caution)" }}>{rej.check ?? "risk_gate"}</span>
                        <span style={{ color: "var(--text-muted)" }}>{rej.ts ? timeAgo(rej.ts) : "—"}</span>
                      </div>
                      <div style={{ fontSize: "var(--t-caption)", color: "var(--text-secondary)", marginTop: "2px" }}>
                        {rej.reason ?? "No reason recorded."}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <Link to={`/symbol/${symbol}`} className="btn" onClick={onClose} style={{ textAlign: "center" }}>
              Open full symbol detail →
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
