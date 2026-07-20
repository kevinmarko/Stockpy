import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type {
  OptionsDirective,
  OptionsMatrix as OptionsMatrixT,
  OptionsRecomputeResult,
  Portfolio,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, ErrorState, Input, Loading, StaleDataNotice } from "../components/ui";
import { Modal } from "../components/Modal";
import { TabGuide } from "../components/TabGuide";
import { fmtNum, fmtUsd, timeAgo } from "../format";
import { theme } from "../theme";
import { realizableTheta } from "../optionsHonesty";
import {
  computePayoff,
  computeExpectedMove,
  computeBreakevenPoints,
  normalProbabilityDensity,
} from "../optionsMath";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as ChartTooltip,
  ResponsiveContainer,
  ReferenceLine,
  ReferenceArea,
} from "recharts";


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



function DetailSheet({ d, dte, onClose }: { d: OptionsDirective; dte: number; onClose: () => void }) {
  const theta = realizableTheta(d);
  const legs = Array.isArray(d.Legs) ? d.Legs : [];
  const spotPrice = d.Price ?? 0;
  const sigma = d.Sigma_GARCH ?? 0;

  // Compute options metrics
  const expectedMove = computeExpectedMove(spotPrice, sigma, dte);
  const breakevens = computeBreakevenPoints(legs);
  const payoffPoints = computePayoff(legs, spotPrice, 150);

  // Split payoff into profit/loss areas for visualization
  const chartData = useMemo(() => {
    return payoffPoints.map((p) => ({
      price: p.price,
      payoff: p.payoff,
      profit: p.payoff >= 0 ? p.payoff : 0,
      loss: p.payoff < 0 ? p.payoff : 0,
    }));
  }, [payoffPoints]);

  // Integrate PDF to calculate POP
  const popPercent = useMemo(() => {
    const sd = spotPrice * sigma * Math.sqrt(dte / 252);
    if (payoffPoints.length < 2 || sd <= 0) return null;
    let pop = 0;
    const step = payoffPoints[1].price - payoffPoints[0].price;
    payoffPoints.forEach((pt) => {
      if (pt.payoff > 0) {
        const pdfVal = normalProbabilityDensity(pt.price, spotPrice, sd);
        if (!isNaN(pdfVal)) pop += pdfVal * step;
      }
    });
    return Math.min(100, Math.max(0, pop * 100));
  }, [payoffPoints, spotPrice, sigma, dte]);

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

      {/* Volatility & expected move context panel */}
      <div className="options-vol-panel-vis">
        <div className="options-vol-item">
          <div style={{ fontSize: 10, color: theme.textMuted, fontWeight: 700, textTransform: "uppercase" }}>Expected Move</div>
          <div className="num" style={{ fontSize: 16, fontWeight: 700 }}>
            {expectedMove > 0 ? `± ${fmtUsd(expectedMove)}` : "—"}
          </div>
        </div>
        <div className="options-vol-item" style={{ textAlign: "center" }}>
          <div style={{ fontSize: 10, color: theme.textMuted, fontWeight: 700, textTransform: "uppercase" }}>Prob of Profit (POP)</div>
          <div className="num" style={{ fontSize: 16, fontWeight: 700, color: theme.growth }}>
            {popPercent !== null ? `${fmtNum(popPercent, 1)}%` : "—"}
          </div>
        </div>
        <div className="options-vol-item" style={{ textAlign: "right" }}>
          <div style={{ fontSize: 10, color: theme.textMuted, fontWeight: 700, textTransform: "uppercase" }}>IVR Proxy</div>
          <div className="num" style={{ fontSize: 16, fontWeight: 700 }}>
            {fmtNum(d.IVR_Proxy ?? null, 0)}
          </div>
        </div>
      </div>

      {/* Visual legs view */}
      {legs.length > 0 && (
        <section style={{ marginTop: 16 }}>
          <h3 style={{ fontSize: 13, color: theme.textMuted, margin: "0 0 6px" }}>Visual Structure</h3>
          <div className="options-legs-row">
            {legs.map((leg, i) => (
              <div
                key={i}
                className={`options-leg-card-vis options-leg-card-${leg.Side === "Short" ? "sell" : "buy"}`}
              >
                <div className="options-leg-label-vis">
                  {leg.Side} {leg.Type}
                </div>
                <div className="options-leg-strike-vis">{fmtUsd(leg.Strike)}</div>
                <div className="options-leg-detail-vis">
                  Price: {fmtUsd(leg.Price)} | Δ: {leg.Delta != null ? fmtNum(leg.Delta, 2) : "—"}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Interactive Payoff Curve */}
      {chartData.length > 0 && (
        <section style={{ marginTop: 16 }}>
          <h3 style={{ fontSize: 13, color: theme.textMuted, margin: "0 0 6px" }}>P/L Payoff Curve</h3>
          <div style={{ width: "100%", height: 180, background: "var(--surface-2)", borderRadius: "var(--r-md)", padding: "10px 10px 0 0" }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255, 255, 255, 0.05)" />
                <XAxis
                  dataKey="price"
                  tickFormatter={(val) => fmtUsd(val)}
                  stroke="rgba(255,255,255,0.4)"
                  style={{ fontSize: 10 }}
                  type="number"
                  domain={["dataMin", "dataMax"]}
                />
                <YAxis
                  stroke="rgba(255,255,255,0.4)"
                  style={{ fontSize: 10 }}
                  tickFormatter={(val) => fmtUsd(val)}
                />
                <ChartTooltip
                  formatter={(value: any) => [fmtUsd(value), "P/L"]}
                  labelFormatter={(label: any) => `Underlying: ${fmtUsd(label)}`}
                />
                {/* Expected move 1SD shading */}
                {expectedMove > 0 && (
                  <ReferenceArea
                    x1={spotPrice - expectedMove}
                    x2={spotPrice + expectedMove}
                    fill="rgba(56, 189, 248, 0.04)"
                    isFront={false}
                  />
                )}
                <ReferenceLine y={0} stroke="rgba(255, 255, 255, 0.2)" strokeWidth={1} />
                {/* Spot Price Line */}
                {spotPrice > 0 && (
                  <ReferenceLine
                    x={spotPrice}
                    stroke={theme.accent}
                    strokeDasharray="3 3"
                    label={{ value: "Spot", fill: theme.accent, fontSize: 9, position: "top" }}
                  />
                )}
                {/* Breakeven lines */}
                {breakevens.map((be, idx) => (
                  <ReferenceLine
                    key={idx}
                    x={be}
                    stroke={theme.caution}
                    strokeWidth={1}
                    label={{ value: "B/E", fill: theme.caution, fontSize: 9, position: "top" }}
                  />
                ))}
                <Area
                  type="monotone"
                  dataKey="profit"
                  stroke="none"
                  fill={theme.growth}
                  fillOpacity={0.15}
                  connectNulls
                />
                <Area
                  type="monotone"
                  dataKey="loss"
                  stroke="none"
                  fill={theme.decline}
                  fillOpacity={0.15}
                  connectNulls
                />
                <Area
                  type="monotone"
                  dataKey="payoff"
                  stroke="#ffffff"
                  strokeWidth={2}
                  fill="none"
                  connectNulls
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      {/* Greeks Grid */}
      <section style={{ marginTop: 16 }}>
        <h3 style={{ fontSize: 13, color: theme.textMuted, margin: "0 0 4px" }}>Greeks</h3>
        <div className="options-greeks-grid">
          <div className="options-greek-card-vis">
            <div className="options-greek-label-vis">Delta</div>
            <div className="options-greek-value-vis">{fmtNum(d.ATM_Delta ?? null, 3)}</div>
          </div>
          <div className="options-greek-card-vis">
            <div className="options-greek-label-vis">Gamma</div>
            <div className="options-greek-value-vis">{fmtNum(d.ATM_Gamma ?? null, 3)}</div>
          </div>
          <div className="options-greek-card-vis">
            <div className="options-greek-label-vis">Vega</div>
            <div className="options-greek-value-vis">{fmtNum(d.ATM_Vega ?? null, 3)}</div>
          </div>
          <div className="options-greek-card-vis">
            <div className="options-greek-label-vis">Theta</div>
            <div className="options-greek-value-vis">{fmtNum(d.ATM_Theta_Daily ?? null, 3)}</div>
          </div>
        </div>
        <p style={{ fontSize: 11, color: theme.textMuted, marginTop: 4, lineHeight: 1.4 }}>
          ATM Greeks reflect sensitivity per option contract at spot & σ, not structure exposure.
        </p>
      </section>

      {/* Realizable Theta details */}
      <section style={{ marginTop: 16 }}>
        <h3 style={{ fontSize: 13, color: theme.textMuted, margin: "0 0 4px" }}>Realizable Theta</h3>
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

      {/* Integrity */}
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

const RECOMPUTE_MIN_SYMBOLS = 1;
const RECOMPUTE_MAX_SYMBOLS = 8;

/**
 * "Recompute with custom parameters" — the on-demand action for backlog item
 * 8b. Ports gui/panels/options_matrix.py's controls form (delta-scale/IVR
 * thresholds/risk-free-rate/strike-grid/DTE + an arbitrary symbol list) to a
 * capped, operator-triggered HTTP call. The persisted matrix above stays the
 * default view; this computes live against parameters the operator chooses,
 * for a small symbol list (1-8), not the whole tracked universe.
 */
function OptionsRecomputeSection() {
  const [symbolsText, setSymbolsText] = useState("");
  const [targetDte, setTargetDte] = useState(30);
  const [deltaScale, setDeltaScale] = useState(1.0);
  const [ivrSell, setIvrSell] = useState(50);
  const [ivrBuy, setIvrBuy] = useState(30);
  const [riskFreeRatePct, setRiskFreeRatePct] = useState("");
  const [strikeGrid, setStrikeGrid] = useState(0.5);
  const [deltaTolerance, setDeltaTolerance] = useState(0.05);
  const [openSymbol, setOpenSymbol] = useState<string | null>(null);

  const mutation = useMutation(() => {
    const rfr = parseFloat(riskFreeRatePct);
    return api.recomputeOptions({
      symbols: parsedSymbols,
      target_dte: targetDte,
      delta_target_scale: deltaScale,
      ivr_sell_threshold: ivrSell,
      ivr_buy_threshold: ivrBuy,
      risk_free_rate_pct: Number.isFinite(rfr) ? rfr : null,
      strike_grid: strikeGrid,
      delta_tolerance: deltaTolerance,
    });
  });
  const result: OptionsRecomputeResult | null = mutation.result ?? null;

  const parsedSymbols = symbolsText
    .split(/[,\s]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
  const uniqueCount = new Set(parsedSymbols).size;
  const canSubmit = uniqueCount >= RECOMPUTE_MIN_SYMBOLS && uniqueCount <= RECOMPUTE_MAX_SYMBOLS;

  const directives = result?.directives ?? [];
  const openDirective = openSymbol
    ? directives.find((d) => d.Symbol === openSymbol) ?? null
    : null;

  return (
    <section className="card card-pad" style={{ marginTop: 16 }}>
      <h2 style={{ fontSize: 15, margin: "0 0 4px" }}>Recompute with custom parameters</h2>
      <p style={{ color: theme.textMuted, fontSize: 12.5, marginTop: 0, marginBottom: 12 }}>
        Compute a fresh premium-selling directive for up to {RECOMPUTE_MAX_SYMBOLS} symbols you
        pick, with your own delta-scale/IVR/risk-free-rate/strike-grid controls — computed live,
        not from the pipeline's last run.
      </p>

      <Input
        label={`Symbols (comma or space separated, ${RECOMPUTE_MIN_SYMBOLS}-${RECOMPUTE_MAX_SYMBOLS})`}
        value={symbolsText}
        onChange={(e) => setSymbolsText(e.target.value)}
        hint={`${uniqueCount} distinct symbol${uniqueCount === 1 ? "" : "s"} entered.`}
        invalid={uniqueCount > 0 && !canSubmit}
      />

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
        <div style={{ flex: "1 1 100px" }}>
          <Input
            label="Target DTE"
            type="number"
            min={1}
            max={120}
            value={targetDte}
            onChange={(e) => setTargetDte(Number(e.target.value) || 30)}
          />
        </div>
        <div style={{ flex: "1 1 100px" }}>
          <Input
            label="Delta ×"
            type="number"
            min={0.25}
            max={2.0}
            step={0.05}
            value={deltaScale}
            onChange={(e) => setDeltaScale(Number(e.target.value) || 1.0)}
          />
        </div>
        <div style={{ flex: "1 1 100px" }}>
          <Input
            label="IVR sell >"
            type="number"
            min={0}
            max={100}
            value={ivrSell}
            onChange={(e) => setIvrSell(Number(e.target.value) || 0)}
          />
        </div>
        <div style={{ flex: "1 1 100px" }}>
          <Input
            label="IVR buy <"
            type="number"
            min={0}
            max={100}
            value={ivrBuy}
            onChange={(e) => setIvrBuy(Number(e.target.value) || 0)}
          />
        </div>
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
        <div style={{ flex: "1 1 100px" }}>
          <Input
            label="Risk-free rate % (blank = default)"
            type="number"
            min={0}
            max={15}
            step={0.25}
            value={riskFreeRatePct}
            onChange={(e) => setRiskFreeRatePct(e.target.value)}
          />
        </div>
        <div style={{ flex: "1 1 100px" }}>
          <Input
            label="Strike grid $"
            type="number"
            min={0.5}
            max={10}
            step={0.5}
            value={strikeGrid}
            onChange={(e) => setStrikeGrid(Number(e.target.value) || 0.5)}
          />
        </div>
        <div style={{ flex: "1 1 100px" }}>
          <Input
            label="Delta tolerance"
            type="number"
            min={0.01}
            max={0.25}
            step={0.01}
            value={deltaTolerance}
            onChange={(e) => setDeltaTolerance(Number(e.target.value) || 0.05)}
          />
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        <Button
          variant="primary"
          pending={mutation.pending}
          disabled={!canSubmit}
          onClick={() => mutation.run()}
        >
          Recompute
        </Button>
      </div>

      {mutation.error && (
        <div className="notice notice-warn" style={{ marginTop: 12 }}>
          <span>{mutation.error}</span>
        </div>
      )}

      {result && (
        <div style={{ marginTop: 16 }}>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 8,
              marginBottom: 12,
              fontSize: 12.5,
              color: theme.textSecondary,
            }}
          >
            <span className="chip">Target DTE {result.target_dte}</span>
            <span className="chip">VIX {fmtNum(result.vix, 1)}</span>
            <span className="chip">{result.market_regime ?? "—"}</span>
          </div>

          {result.errors.length > 0 && (
            <div className="notice notice-warn" style={{ marginBottom: 12 }}>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {result.errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </div>
          )}

          {directives.length === 0 ? (
            <div className="empty" style={{ padding: 18 }}>
              No directives computed.
            </div>
          ) : (
            directives.map((d) => (
              <DirectiveCard key={d.Symbol} d={d} onOpen={() => setOpenSymbol(d.Symbol)} />
            ))
          )}
        </div>
      )}

      {openDirective && (
        <DetailSheet
          d={openDirective}
          dte={result?.target_dte ?? 30}
          onClose={() => setOpenSymbol(null)}
        />
      )}
    </section>
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
  const [showRecompute, setShowRecompute] = useState(false);

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

      <TabGuide tabKey="options" />

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
        <DetailSheet
          d={openDirective}
          dte={data?.target_dte ?? 30}
          onClose={() => setOpenSymbol(null)}
        />
      )}

      <button
        type="button"
        onClick={() => setShowRecompute((v) => !v)}
        aria-expanded={showRecompute}
        className="btn btn-neutral"
        style={{ marginTop: 20, width: "100%" }}
      >
        {showRecompute ? "▲ Hide" : "▼"} Recompute with custom parameters
      </button>

      {showRecompute && <OptionsRecomputeSection />}
    </div>
  );
}
