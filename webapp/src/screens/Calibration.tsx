import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type {
  CalibrationBin,
  CalibrationSummary,
  EdgeByStrategy,
  MfeMaePoint,
  RecTrackingRow,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { Button, EmptyState, ErrorState, Loading, Tile } from "../components/ui";
import { DecisionModal } from "../components/DecisionModal";
import { fmtNum, fmtPct } from "../format";
import { theme } from "../theme";

/**
 * Calibration — the "did our actual calls work?" evaluation surface, ported
 * from the retired Streamlit Report Viewer (gui/panels/report_viewer.py's
 * calibration / recommendation-tracking / trade-quality / decision-journal
 * sections). It is the honesty complement to Strategy Health (which shows
 * whether a strategy is *statistically* sound): this shows whether the
 * platform's real, live recommendations actually panned out.
 *
 * One composite GET (`/calibration/summary`) drives four read sections; the
 * heavier edge-by-strategy recompute lazy-loads behind a button
 * (`/calibration/edge-by-strategy`); and the decision journal writes via
 * `POST /decisions`. Every null/empty is rendered honestly — an under-min
 * calibration bin is "insufficient data", not a fabricated win rate; a null
 * return is "—", not 0.0.
 */

const HORIZONS = [10, 30, 60, 90] as const;

// ---------------------------------------------------------------------------
// Reliability diagram (inline SVG — recharts has no y=x-referenced bar chart)
// ---------------------------------------------------------------------------

function ReliabilityDiagram({ bins }: { bins: CalibrationBin[] }) {
  const W = 320,
    H = 240,
    padL = 34,
    padR = 12,
    padT = 12,
    padB = 34;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const x = (conv: number) => padL + conv * plotW;
  const y = (rate: number) => padT + (1 - rate) * plotH;

  const scored = bins.filter(
    (b): b is CalibrationBin & { win_rate: number; bin_center: number } =>
      b.win_rate != null && b.bin_center != null
  );
  const barW = Math.max(6, (plotW / Math.max(bins.length, 4)) * 0.55);
  const baseY = y(0);

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      role="img"
      aria-label="Reliability diagram: model conviction vs. realized win rate"
      style={{ maxWidth: 440, display: "block" }}
    >
      {/* horizontal gridlines + y ticks (win rate %) */}
      {[0, 0.25, 0.5, 0.75, 1].map((t) => (
        <g key={`y${t}`}>
          <line x1={padL} y1={y(t)} x2={W - padR} y2={y(t)} stroke="rgba(255,255,255,0.06)" />
          <text x={padL - 5} y={y(t) + 3} textAnchor="end" fontSize="9" fill={theme.textMuted}>
            {(t * 100).toFixed(0)}
          </text>
        </g>
      ))}
      {/* x ticks (conviction) */}
      {[0, 0.5, 1].map((t) => (
        <text
          key={`x${t}`}
          x={x(t)}
          y={H - padB + 14}
          textAnchor="middle"
          fontSize="9"
          fill={theme.textMuted}
        >
          {t.toFixed(1)}
        </text>
      ))}
      {/* perfect-calibration y=x diagonal */}
      <line
        x1={x(0)}
        y1={y(0)}
        x2={x(1)}
        y2={y(1)}
        stroke={theme.textMuted}
        strokeWidth={1}
        strokeDasharray="4 4"
      />
      {/* actual win-rate bars at each scored bin center */}
      {scored.map((b) => {
        const cx = x(b.bin_center);
        const top = y(b.win_rate);
        return (
          <rect
            key={b.bin_center}
            x={cx - barW / 2}
            y={top}
            width={barW}
            height={Math.max(0, baseY - top)}
            rx={2}
            fill={theme.accent}
            fillOpacity={0.8}
          >
            <title>
              {`Conviction ${(b.bin_center * 100).toFixed(0)}% → won ${(b.win_rate * 100).toFixed(
                0
              )}% (${b.count} trades)`}
            </title>
          </rect>
        );
      })}
      {/* axis captions */}
      <text
        x={padL + plotW / 2}
        y={H - 3}
        textAnchor="middle"
        fontSize="9.5"
        fill={theme.textSecondary}
      >
        Conviction (model output)
      </text>
      <text
        x={11}
        y={padT + plotH / 2}
        textAnchor="middle"
        fontSize="9.5"
        fill={theme.textSecondary}
        transform={`rotate(-90 11 ${padT + plotH / 2})`}
      >
        Win rate (actual)
      </text>
    </svg>
  );
}

function CalibrationSection({ cal }: { cal: CalibrationSummary["calibration"] }) {
  if (cal.total === 0) {
    return (
      <EmptyState
        title="No conviction data yet"
        hint={cal.reason ?? "Conviction scores appear here once trades close with a conviction annotation."}
      />
    );
  }
  const insufficient = cal.bins.filter((b) => b.win_rate == null && b.count > 0);
  return (
    <>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 12 }}>
        <Tile label="Trades w/ conviction" value={String(cal.total)} />
        <Tile label="Overall win rate" value={fmtPct(cal.overall_win_rate, 1, { fromFraction: true })} />
        <Tile label="Calibration error" value={fmtNum(cal.calibration_error, 3)} />
        <Tile label="Bins w/ data" value={String(cal.n_scored_bins)} />
      </div>
      {cal.n_scored_bins > 0 ? (
        <div className="card card-pad">
          <ReliabilityDiagram bins={cal.bins} />
          <p style={{ color: theme.textMuted, fontSize: 11.5, marginTop: 8, lineHeight: 1.5 }}>
            Bars are the realized win rate per conviction bucket; the dashed line is perfect
            calibration (say 0.70 → win 70%). Calibration error is the mean gap between the two.
          </p>
        </div>
      ) : (
        <EmptyState
          title="Not enough trades per bin yet"
          hint={`Every conviction bin has fewer than ${cal.min_trades_per_bin} trades, so no win rate is shown (never fabricated).`}
        />
      )}
      {insufficient.length > 0 && (
        <p style={{ color: theme.textMuted, fontSize: 11.5, marginTop: 8 }}>
          {insufficient.length} bin{insufficient.length === 1 ? "" : "s"} had fewer than{" "}
          {cal.min_trades_per_bin} trades — shown as insufficient data, not a fabricated rate.
        </p>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Recommendation tracking (model vs. operator return)
// ---------------------------------------------------------------------------

function RecTrackingSection({
  tracking,
  horizon,
  onHorizon,
}: {
  tracking: CalibrationSummary["recommendation_tracking"];
  horizon: number;
  onHorizon: (h: number) => void;
}) {
  const pct = (v: number | null) => fmtPct(v, 2, { fromFraction: true, signed: true });
  const deltaTone =
    tracking.delta == null ? undefined : tracking.delta >= 0 ? "pos" : "neg";
  return (
    <>
      <div
        role="group"
        aria-label="Return horizon"
        style={{ display: "flex", gap: 6, marginBottom: 12, flexWrap: "wrap" }}
      >
        {HORIZONS.map((h) => (
          <button
            key={h}
            className="chip"
            onClick={() => onHorizon(h)}
            style={{
              cursor: "pointer",
              background: h === horizon ? theme.surface3 : undefined,
              borderColor: h === horizon ? theme.borderStrong : undefined,
              color: h === horizon ? theme.textPrimary : theme.textSecondary,
            }}
          >
            {h}d
          </button>
        ))}
      </div>
      {tracking.n_signals === 0 ? (
        <EmptyState
          title="No BUY signals logged yet"
          hint={tracking.reason ?? "Log decisions in the journal below; the tracking report populates after the horizon elapses."}
        />
      ) : (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
            <Tile label={`Model ${tracking.horizon_days}d return`} value={pct(tracking.model_return)} />
            <Tile label="Operator return" value={pct(tracking.operator_return)} />
            <Tile label="Delta (op − model)" value={pct(tracking.delta)} tone={deltaTone} />
            <Tile label="BUY signals" value={String(tracking.n_signals)} />
            <Tile label="Acted" value={String(tracking.n_acted)} />
            <Tile label="Completed" value={String(tracking.n_completed)} />
          </div>
          {tracking.model_return == null && (
            <p style={{ color: theme.textMuted, fontSize: 12, marginTop: 8 }}>
              No logged BUY signal has reached the {tracking.horizon_days}-day horizon yet — check
              back once it elapses.
            </p>
          )}
          {tracking.rows.length > 0 && <RecTrackingTable rows={tracking.rows} />}
        </>
      )}
    </>
  );
}

function RecTrackingTable({ rows }: { rows: RecTrackingRow[] }) {
  const pct = (v: number | null) => fmtPct(v, 2, { fromFraction: true, signed: true });
  return (
    <div style={{ overflowX: "auto", marginTop: 12 }}>
      <table style={{ width: "100%", fontSize: 12.5, minWidth: 460, borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th style={thL}>Symbol</th>
            <th style={thL}>Signal</th>
            <th style={thR}>Conv.</th>
            <th style={thL}>Decision</th>
            <th style={thR}>Model</th>
            <th style={thR}>Actual</th>
            <th style={thR}>Held</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.symbol}-${r.signal_ts}-${i}`}>
              <td style={tdL}>{r.symbol}</td>
              <td style={tdL}>{r.signal_action ?? "—"}</td>
              <td style={tdR}>{fmtNum(r.conviction, 2)}</td>
              <td style={tdL}>
                {r.action_taken ?? "—"}
                {!r.completed && (
                  <span style={{ color: theme.textMuted }}> (pending)</span>
                )}
              </td>
              <td style={tdR}>{pct(r.model_return)}</td>
              <td style={tdR}>{pct(r.actual_return)}</td>
              <td style={tdR}>{r.days_held == null ? "—" : `${r.days_held}d`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const thL: React.CSSProperties = { textAlign: "left", padding: "6px 8px", color: theme.textMuted, fontWeight: 600, borderBottom: `1px solid ${theme.border}` };
const thR: React.CSSProperties = { ...thL, textAlign: "right" };
const tdL: React.CSSProperties = { textAlign: "left", padding: "6px 8px", borderBottom: `1px solid ${theme.border}` };
const tdR: React.CSSProperties = { ...tdL, textAlign: "right" };

// ---------------------------------------------------------------------------
// MFE / MAE scatter (inline SVG)
// ---------------------------------------------------------------------------

function MfeMaeScatter({ points }: { points: MfeMaePoint[] }) {
  const W = 320,
    H = 240,
    padL = 40,
    padR = 12,
    padT = 12,
    padB = 34;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const maxV = Math.max(0.02, ...points.flatMap((p) => [p.mfe, p.mae])) * 1.12;
  const x = (v: number) => padL + (v / maxV) * plotW;
  const y = (v: number) => padT + (1 - v / maxV) * plotH;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      role="img"
      aria-label="MFE vs MAE scatter for current signals"
      style={{ maxWidth: 440, display: "block" }}
    >
      {[0, 0.5, 1].map((f) => {
        const v = maxV * f;
        return (
          <g key={f}>
            <line x1={padL} y1={y(v)} x2={W - padR} y2={y(v)} stroke="rgba(255,255,255,0.06)" />
            <text x={padL - 5} y={y(v) + 3} textAnchor="end" fontSize="9" fill={theme.textMuted}>
              {(v * 100).toFixed(0)}%
            </text>
            <text x={x(v)} y={H - padB + 14} textAnchor="middle" fontSize="9" fill={theme.textMuted}>
              {(v * 100).toFixed(0)}%
            </text>
          </g>
        );
      })}
      {/* MFE=MAE reference (edge ratio = 1) */}
      <line
        x1={x(0)}
        y1={y(0)}
        x2={x(maxV)}
        y2={y(maxV)}
        stroke={theme.textMuted}
        strokeWidth={1}
        strokeDasharray="4 4"
      />
      {points.map((p) => {
        const favorable = p.edge_ratio != null ? p.edge_ratio >= 1 : p.mfe >= p.mae;
        return (
          <circle
            key={p.symbol}
            cx={x(p.mae)}
            cy={y(p.mfe)}
            r={5}
            fill={favorable ? theme.growth : theme.decline}
            fillOpacity={0.8}
            stroke={theme.base}
            strokeWidth={1}
          >
            <title>
              {`${p.symbol} · MFE ${(p.mfe * 100).toFixed(1)}% · MAE ${(p.mae * 100).toFixed(1)}% · edge ${
                p.edge_ratio == null ? "—" : p.edge_ratio.toFixed(2)
              }`}
            </title>
          </circle>
        );
      })}
      <text x={padL + plotW / 2} y={H - 3} textAnchor="middle" fontSize="9.5" fill={theme.textSecondary}>
        MAE — adverse excursion
      </text>
      <text
        x={11}
        y={padT + plotH / 2}
        textAnchor="middle"
        fontSize="9.5"
        fill={theme.textSecondary}
        transform={`rotate(-90 11 ${padT + plotH / 2})`}
      >
        MFE — favorable
      </text>
    </svg>
  );
}

function MfeMaeSection({ mfeMae }: { mfeMae: CalibrationSummary["mfe_mae"] }) {
  if (mfeMae.points.length === 0) {
    return (
      <EmptyState
        title="No excursion data yet"
        hint={mfeMae.reason ?? "MFE/MAE populate once symbols have trade history."}
      />
    );
  }
  return (
    <div className="card card-pad">
      <MfeMaeScatter points={mfeMae.points} />
      <p style={{ color: theme.textMuted, fontSize: 11.5, marginTop: 8, lineHeight: 1.5 }}>
        Each dot is a current signal. Above the dashed line (green) the favorable move exceeded the
        adverse one (edge ratio &gt; 1); below it (red) the trade was underwater more than it ran up.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Edge ratio by strategy (lazy-loaded, heavier recompute)
// ---------------------------------------------------------------------------

function EdgeByStrategySection() {
  const { data, loading, error, status, reload } = useApi<EdgeByStrategy>(
    () => api.getEdgeByStrategy(),
    []
  );
  if (loading) return <Loading lines={2} />;
  if (error) return <ErrorState message={error} status={status} onRetry={reload} />;
  if (!data || data.rows.length === 0) {
    return (
      <EmptyState
        title="No closed trades to score yet"
        hint={data?.reason ?? "Edge ratio by strategy populates once trades close."}
      />
    );
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", fontSize: 12.5, minWidth: 460, borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th style={thL}>Strategy</th>
            <th style={thR}>Trades</th>
            <th style={thR}>Mean edge</th>
            <th style={thR}>Median edge</th>
            <th style={thR}>Mean MFE</th>
            <th style={thR}>Mean MAE</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((r) => (
            <tr key={r.strategy}>
              <td style={tdL}>{r.strategy}</td>
              <td style={tdR}>{r.n_trades}</td>
              <td style={tdR}>{fmtNum(r.mean_edge_ratio, 2)}</td>
              <td style={tdR}>{fmtNum(r.median_edge_ratio, 2)}</td>
              <td style={tdR}>{fmtPct(r.mean_mfe, 1, { fromFraction: true })}</td>
              <td style={tdR}>{fmtPct(r.mean_mae, 1, { fromFraction: true })}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Decision journal — read list + per-current-signal write via confirm Modal
// (DecisionModal itself lives in ../components/DecisionModal -- shared with
// SymbolDetail's per-symbol journal section).
// ---------------------------------------------------------------------------

function DecisionJournalSection({
  signals,
  recent,
  onLogged,
}: {
  signals: MfeMaePoint[];
  recent: CalibrationSummary["recent_decisions"];
  onLogged: () => void;
}) {
  const [journaling, setJournaling] = useState<MfeMaePoint | null>(null);
  return (
    <>
      <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0, lineHeight: 1.5 }}>
        Record whether you acted on, passed, or modified each current signal. "Acted" decisions are
        best-effort linked to a real trade within 24h so the tracking report above can measure your
        judgment against the model.
      </p>

      {signals.length === 0 ? (
        <EmptyState
          title="No current signals to journal"
          hint="Run the pipeline to produce signals with excursion data, then log your decisions here."
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {signals.map((s) => (
            <div
              key={s.symbol}
              className="card card-pad"
              style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}
            >
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 700 }}>{s.symbol}</div>
                <div style={{ color: theme.textMuted, fontSize: 12 }}>
                  {s.action}
                  {s.conviction != null && <> · conv {fmtNum(s.conviction, 2)}</>}
                </div>
              </div>
              <Button variant="neutral" onClick={() => setJournaling(s)}>
                Log decision
              </Button>
            </div>
          ))}
        </div>
      )}

      <h3 style={{ fontSize: 14, margin: "20px 0 8px", color: theme.textSecondary }}>Recent decisions</h3>
      {recent.decisions.length === 0 ? (
        <p style={{ color: theme.textMuted, fontSize: 12.5 }}>
          {recent.reason ?? "No decisions logged yet."}
        </p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", fontSize: 12.5, minWidth: 420, borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={thL}>Symbol</th>
                <th style={thL}>Decision</th>
                <th style={thL}>Signal</th>
                <th style={thL}>When</th>
                <th style={thR}>Trade</th>
              </tr>
            </thead>
            <tbody>
              {recent.decisions.map((d, i) => (
                <tr key={`${d.symbol}-${d.timestamp}-${i}`}>
                  <td style={tdL}>{d.symbol ?? "—"}</td>
                  <td style={tdL}>{d.action_taken ?? "—"}</td>
                  <td style={tdL}>{d.signal_action ?? "—"}</td>
                  <td style={tdL}>{d.timestamp ? d.timestamp.slice(0, 10) : "—"}</td>
                  <td style={tdR}>{d.trade_id == null ? "—" : `#${d.trade_id}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {journaling && (
        <DecisionModal
          signal={journaling}
          onClose={() => setJournaling(null)}
          onLogged={onLogged}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

function SectionHead({ title, sub }: { title: string; sub?: string }) {
  return (
    <div style={{ margin: "24px 0 10px" }}>
      <h2 style={{ margin: 0, fontSize: "var(--t-title)" }}>{title}</h2>
      {sub && <p style={{ color: theme.textMuted, fontSize: 12.5, margin: "2px 0 0" }}>{sub}</p>}
    </div>
  );
}

export function Calibration() {
  const nav = useNavigate();
  const [horizon, setHorizon] = useState(30);
  const [showEdge, setShowEdge] = useState(false);
  const { data, loading, error, status, reload } = useApi<CalibrationSummary>(
    () => api.getCalibrationSummary(horizon),
    [horizon]
  );
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/marketplace"));

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
      <h1 className="screen-title">Calibration</h1>
      <p className="screen-sub">
        Did our actual calls work? Model confidence vs. real outcomes, your decisions vs. the
        model's baseline, and post-trade excursion quality — never a fabricated number.
      </p>

      {loading && <Loading lines={4} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}

      {!loading && !error && data && (
        <>
          <SectionHead
            title="Conviction calibration"
            sub="When the model says 0.70, does it actually win 70%?"
          />
          <CalibrationSection cal={data.calibration} />

          <SectionHead title="Recommendation tracking" sub="Your decisions vs. the model's paper baseline" />
          <RecTrackingSection
            tracking={data.recommendation_tracking}
            horizon={horizon}
            onHorizon={setHorizon}
          />

          <SectionHead title="Trade quality — MFE / MAE" sub="Excursion quality across current signals" />
          <MfeMaeSection mfeMae={data.mfe_mae} />

          <SectionHead title="Edge ratio by strategy" sub="Recomputed from closed-trade history — loads on demand" />
          {showEdge ? (
            <EdgeByStrategySection />
          ) : (
            <Button variant="neutral" onClick={() => setShowEdge(true)}>
              📐 Compute edge ratio by strategy
            </Button>
          )}

          <SectionHead title="Decision journal" sub="Log what you did with each signal" />
          <DecisionJournalSection
            signals={data.mfe_mae.points}
            recent={data.recent_decisions}
            onLogged={reload}
          />
        </>
      )}

      <p
        style={{
          color: theme.textMuted,
          fontSize: 11.5,
          marginTop: 24,
          textAlign: "center",
          lineHeight: 1.5,
        }}
      >
        Calibration bins under the minimum trade count show "insufficient data", never a fabricated
        win rate; a null return renders "—", never 0.0.
      </p>
    </div>
  );
}
