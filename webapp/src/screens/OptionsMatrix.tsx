import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import type {
  OptionsDirective,
  OptionsMatrix as OptionsMatrixT,
  OptionsRecomputeResult,
  Portfolio,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { useMutation } from "../hooks/useMutation";
import { Button, ErrorState, Input, InfoTip, Loading, Notice, Select, StaleDataNotice } from "../components/ui";
import { Modal } from "../components/Modal";
import { TabGuide } from "../components/TabGuide";
import { DynamicGrid, resetGridLayout } from "../components/DynamicGrid";
import { chartAxisLine, chartAxisTick, chartGridProps } from "../components/charts";
import { fmtNum, fmtPct, fmtUsd, timeAgo } from "../format";
import { theme } from "../theme";
import { realizableTheta, effectiveIvr } from "../optionsHonesty";
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
import OptionsAnalyticsDashboard from "../components/OptionsAnalyticsDashboard";
import { useChat } from "../chat/ChatContext";
import { buildOptionsContextText } from "../chat/formatOptionsContext";


function heatmapStyle(val: number | null | undefined, min: number, max: number, invert = false) {
  if (val == null || isNaN(val)) return {};
  const ratio = Math.max(0, Math.min(1, (val - min) / (max - min)));
  const hot = invert ? "var(--decline)" : "var(--growth)";
  const cold = invert ? "var(--growth)" : "var(--decline)";
  // If > 0.5, mix towards hot. If < 0.5, mix towards cold.
  if (ratio >= 0.5) {
    const p = Math.round((ratio - 0.5) * 2 * 100);
    return { backgroundColor: `color-mix(in srgb, var(--surface-3) ${100 - p}%, ${hot} 20%)` };
  } else {
    const p = Math.round((0.5 - ratio) * 2 * 100);
    return { backgroundColor: `color-mix(in srgb, var(--surface-3) ${100 - p}%, ${cold} 20%)` };
  }
}



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

const SORT_OPTIONS: { value: Sort; label: string }[] = [
  { value: "premium", label: "Net premium ↓" },
  { value: "ivr", label: "IVR ↓" },
  { value: "sigma", label: "σ ↓" },
  { value: "symbol", label: "Symbol A–Z" },
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

/**
 * Note: this card is a `role="button"` `div`, not a real `<button>` --
 * the "stale" badge below needs its own tap-to-open InfoTip trigger, and
 * nesting a focusable trigger inside a native `<button>` is invalid HTML
 * (see components/ui.tsx's InfoTip docstring). `role="button"` + `tabIndex`
 * + a matching `onKeyDown` reproduces the same click/Enter/Space affordance
 * a real `<button>` gets for free, mirroring TradingHub.tsx's HubCardRow.
 */
function DirectiveCard({ d, onOpen }: { d: OptionsDirective; onOpen: () => void }) {
  const ivr = effectiveIvr(d);
  return (
    <div
      className="glass-card"
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      style={{
        display: "flex",
        flexDirection: "column",
        background: "none",
        width: "100%",
        height: "100%",
        textAlign: "left",
        border: "1px solid var(--border)",
        padding: 0,
        cursor: "pointer",
      }}
    >
      <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid ${theme.border}`, display: "flex", justifyContent: "space-between", alignItems: "baseline", cursor: "grab" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <span style={{ fontWeight: 700, fontSize: "var(--t-input)" }}>{d.Symbol}</span>
          {d.Stale === true && (
            <InfoTip triggerClassName="badge badge-warn" content="Quote is stale">
              stale
            </InfoTip>
          )}
        </div>
        <span className="num" style={{ color: theme.textSecondary }}>
          {fmtUsd(d.Price ?? null)}
        </span>
      </div>
      <div
        style={{
          flex: 1,
          padding: "var(--s-3)",
          overflow: "auto",
        }}
      >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span style={{ fontWeight: 600 }}>{d.Strategy ?? "—"}</span>
        <span className={`badge ${actionBadgeClass(d.Action)}`}>{d.Action ?? "—"}</span>
      </div>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "var(--s-3-5)",
          marginTop: "var(--s-2-5)",
          alignItems: "baseline",
        }}
      >
        <PremiumLabel d={d} />
        <span 
          style={{ 
            fontSize: "var(--t-body)", 
            color: theme.textSecondary,
            padding: "2px 6px",
            borderRadius: "var(--r-sm)",
            ...heatmapStyle(ivr.value, 0, 100)
          }}
        >
          IVR <span className="num" style={{ fontWeight: 600, color: theme.textPrimary }}>{fmtNum(ivr.value, 0)}</span>
          {ivr.value != null && (
            <span
              style={{ fontSize: "var(--t-micro)", color: theme.textMuted, marginLeft: 3 }}
              title={
                ivr.isTrue
                  ? "Real options-chain-derived 30-day ATM IV rank."
                  : "Realized-volatility proxy — no options chain fetched for this symbol; not true implied-vol rank."
              }
            >
              {ivr.isTrue ? "chain" : "proxy"}
            </span>
          )}
        </span>
        <span style={{ fontSize: "var(--t-body)", color: theme.textSecondary }}>{d.Trend_Bias ?? "—"}</span>
        {d.Days_To_Earnings != null && (
          <span
            className={`badge ${d.Earnings_Risk ? "badge-warn" : "badge-neutral"}`}
            title="Scheduled earnings announcement"
          >
            ⚠️ Earnings in {d.Days_To_Earnings}d
          </span>
        )}
        {typeof d.Altman_Z_Score === "number" && (
          <span
            className={`badge ${d.Altman_Z_Score >= 2.6 ? "badge-good" : d.Altman_Z_Score < 1.8 ? "badge-bad" : "badge-warn"}`}
            title="Altman Z-Score Solvency (>= 2.6 Safe, < 1.8 Distress)"
          >
            Altman Z: {fmtNum(d.Altman_Z_Score, 1)} {d.Altman_Z_Score >= 2.6 ? "(Safe)" : d.Altman_Z_Score < 1.8 ? "(Distress)" : "(Grey)"}
          </span>
        )}
        {typeof d.Net_Debt_EBITDA === "number" && (
          <span style={{ fontSize: "var(--t-micro)", color: theme.textSecondary }}>
            Net Debt/EBITDA: <span className="num">{fmtNum(d.Net_Debt_EBITDA, 1)}x</span>
          </span>
        )}
        {typeof d.Piotroski_F_Score === "number" && (
          <span
            className={`badge ${d.Piotroski_F_Score >= 7 ? "badge-good" : d.Piotroski_F_Score <= 3 ? "badge-bad" : "badge-warn"}`}
            title="Piotroski F-Score (0-9): >= 7 Strong, <= 3 Weak"
          >
            F-Score: {d.Piotroski_F_Score}/9
          </span>
        )}
        {typeof d.FCF_Yield === "number" && (
          <span style={{ fontSize: "var(--t-micro)", color: theme.textSecondary }}>
            FCF Yield: <span className="num">{fmtPct(d.FCF_Yield, 1, { fromFraction: true, signed: true })}</span>
          </span>
        )}
        {isFlagged(d) && (
          <span className="badge badge-bad" style={{ marginLeft: "auto" }}>
            ⚠ Integrity
          </span>
        )}
      </div>
      </div>
    </div>
  );
}




function DetailSheet({ d, dte, asOf, onClose }: { d: OptionsDirective; dte: number; asOf?: string | null; onClose: () => void }) {
  const theta = realizableTheta(d);
  const ivr = effectiveIvr(d);
  const legs = Array.isArray(d.Legs) ? d.Legs : [];
  const spotPrice = d.Price ?? 0;
  const sigma = d.Sigma_GARCH ?? 0;

  const expiryDate = useMemo(() => {
    const base = asOf || d.as_of || d.AsOf; // support fallback properties
    if (typeof base !== "string") return null;
    try {
      const date = new Date(base);
      date.setDate(date.getDate() + dte);
      return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    } catch {
      return null;
    }
  }, [asOf, d.as_of, d.AsOf, dte]);

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
      <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", marginTop: "var(--s-1-5)", flexWrap: "wrap" }}>
        <span style={{ fontWeight: 600 }}>{d.Strategy ?? "—"}</span>
        <span className={`badge ${actionBadgeClass(d.Action)}`}>{d.Action ?? "—"}</span>
        {expiryDate && <span className="badge badge-neutral">Expiry: {expiryDate} ({dte} DTE)</span>}
        {d.Stale === true && <span className="badge badge-warn">stale</span>}
      </div>

      {/* Volatility & expected move context panel */}
      <div className="options-vol-panel-vis">
        <div className="options-vol-item">
          <div style={{ fontSize: 10, color: theme.textMuted, fontWeight: 700, textTransform: "uppercase" }}>Expected Move</div>
          <div className="num" style={{ fontSize: "var(--t-input)", fontWeight: 700 }}>
            {expectedMove > 0 ? `± ${fmtUsd(expectedMove)}` : "—"}
          </div>
        </div>
        <div className="options-vol-item" style={{ textAlign: "center" }}>
          <div style={{ fontSize: 10, color: theme.textMuted, fontWeight: 700, textTransform: "uppercase" }}>Prob of Profit (POP)</div>
          <div className="num" style={{ fontSize: "var(--t-input)", fontWeight: 700, color: theme.growth }}>
            {popPercent !== null ? `${fmtNum(popPercent, 1)}%` : "—"}
          </div>
        </div>
        <div className="options-vol-item" style={{ textAlign: "right" }}>
          <div style={{ fontSize: 10, color: theme.textMuted, fontWeight: 700, textTransform: "uppercase" }}>
            {ivr.isTrue ? "IVR (chain)" : "IVR Proxy"}
          </div>
          <div className="num" style={{ fontSize: "var(--t-input)", fontWeight: 700 }}>
            {fmtNum(ivr.value, 0)}
          </div>
        </div>
      </div>

      {/* Visual legs view */}
      {legs.length > 0 && (
        <section style={{ marginTop: "var(--s-4)" }}>
          <h3 style={{ fontSize: "var(--t-body)", color: theme.textMuted, margin: "0 0 var(--s-1-5)" }}>Visual Structure</h3>
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
        <section style={{ marginTop: "var(--s-4)" }}>
          <h3 style={{ fontSize: "var(--t-body)", color: theme.textMuted, margin: "0 0 var(--s-1-5)" }}>P/L Payoff Curve</h3>
          <div style={{ width: "100%", height: 180, background: "var(--surface-2)", borderRadius: "var(--r-md)", padding: "var(--s-2-5) var(--s-2-5) 0 0" }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid {...chartGridProps} />
                <XAxis
                  dataKey="price"
                  tickFormatter={(val) => fmtUsd(val)}
                  tick={chartAxisTick}
                  {...chartAxisLine}
                  type="number"
                  domain={["dataMin", "dataMax"]}
                />
                <YAxis tick={chartAxisTick} {...chartAxisLine} tickFormatter={(val) => fmtUsd(val)} />
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

      {/* Technical Profile */}
      {(d.Trend_Bias || d.Aroon_Oscillator != null || d.Coppock_Curve != null) && (
        <section style={{ marginTop: "var(--s-4)" }}>
          <h3 style={{ fontSize: "var(--t-body)", color: theme.textMuted, margin: "0 0 var(--s-1-5)" }}>Indicators & Bias</h3>
          <div className="options-greeks-grid">
            {d.Trend_Bias && (
              <div className="options-greek-card-vis">
                <div className="options-greek-label-vis">Trend Bias</div>
                <div className="options-greek-value-vis" style={{ color: d.Trend_Bias.toLowerCase().includes("bull") ? theme.growth : d.Trend_Bias.toLowerCase().includes("bear") ? theme.decline : theme.textSecondary, fontWeight: 700 }}>
                  {d.Trend_Bias}
                </div>
              </div>
            )}
            {d.Aroon_Oscillator != null && (
              <div className="options-greek-card-vis">
                <div className="options-greek-label-vis">Aroon Osc</div>
                <div className="options-greek-value-vis" style={{ fontWeight: 700 }}>{fmtNum(d.Aroon_Oscillator, 1)}</div>
              </div>
            )}
            {d.Coppock_Curve != null && (
              <div className="options-greek-card-vis">
                <div className="options-greek-label-vis">Coppock</div>
                <div className="options-greek-value-vis" style={{ fontWeight: 700 }}>{fmtNum(d.Coppock_Curve, 2)}</div>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Greeks Grid */}
      <section style={{ marginTop: "var(--s-4)" }}>
        <h3 style={{ fontSize: "var(--t-body)", color: theme.textMuted, margin: "0 0 var(--s-1)" }}>Greeks</h3>
        <div className="options-greeks-grid">
          <div className="options-greek-card-vis" style={heatmapStyle(d.ATM_Delta, -1, 1)}>
            <div className="options-greek-label-vis">Delta</div>
            <div className="options-greek-value-vis">{fmtNum(d.ATM_Delta ?? null, 3)}</div>
          </div>
          <div className="options-greek-card-vis" style={heatmapStyle(d.ATM_Gamma, 0, 0.2)}>
            <div className="options-greek-label-vis">Gamma</div>
            <div className="options-greek-value-vis">{fmtNum(d.ATM_Gamma ?? null, 3)}</div>
          </div>
          <div className="options-greek-card-vis" style={heatmapStyle(d.ATM_Vega, 0, 2)}>
            <div className="options-greek-label-vis">Vega</div>
            <div className="options-greek-value-vis">{fmtNum(d.ATM_Vega ?? null, 3)}</div>
          </div>
          <div className="options-greek-card-vis" style={heatmapStyle(d.ATM_Theta_Daily, -0.5, 0.5)}>
            <div className="options-greek-label-vis">Theta</div>
            <div className="options-greek-value-vis">{fmtNum(d.ATM_Theta_Daily ?? null, 3)}</div>
          </div>
        </div>
        <p style={{ fontSize: "var(--t-micro)", color: theme.textMuted, marginTop: "var(--s-1)", lineHeight: 1.4 }}>
          ATM Greeks reflect sensitivity per option contract at spot & σ, not structure exposure.
        </p>
      </section>

      {/* Realizable Theta details */}
      <section style={{ marginTop: "var(--s-4)" }}>
        <h3 style={{ fontSize: "var(--t-body)", color: theme.textMuted, margin: "0 0 var(--s-1)" }}>Realizable Theta</h3>
        {theta.note ? (
          <>
            <div className="num muted" style={{ fontSize: "var(--t-subhead)" }}>—</div>
            <p style={{ fontSize: "var(--t-footnote)", color: theme.textMuted, marginTop: "var(--s-1)", lineHeight: 1.45 }}>
              {theta.note}
            </p>
          </>
        ) : (
          <div className="num" style={{ fontSize: "var(--t-subhead)", fontWeight: 700 }}>
            {fmtNum(theta.value, 3)}
            <span style={{ fontSize: "var(--t-caption)", fontWeight: 400, color: theme.textMuted }}> /day</span>
          </div>
        )}
      </section>

      {/* Integrity */}
      <section style={{ marginTop: "var(--s-4)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <h3 style={{ fontSize: "var(--t-body)", color: theme.textMuted, margin: 0 }}>Integrity</h3>
          <span className={`badge ${d.Integrity_OK === true ? "badge-good" : "badge-bad"}`}>
            {d.Integrity_OK === true ? "✓ clean" : "✗ flagged"}
          </span>
        </div>
        {Array.isArray(d.Integrity_Issues) && d.Integrity_Issues.length > 0 && (
          <ul style={{ margin: "var(--s-2) 0 0", paddingLeft: 18, fontSize: "var(--t-label)", color: theme.textSecondary, lineHeight: 1.5 }}>
            {d.Integrity_Issues.map((issue, i) => (
              <li key={i}>{issue}</li>
            ))}
          </ul>
        )}
      </section>

      {/* Analyst Consensus -- CONSTRAINT #4: no placeholder/empty state when
          absent, matching every other conditional section on this screen. */}
      {d.Analyst_Target_Consensus != null && (
        <section style={{ marginTop: "var(--s-4)" }}>
          <h3 style={{ fontSize: "var(--t-body)", color: theme.textMuted, margin: "0 0 var(--s-1-5)" }}>Analyst Consensus</h3>
          <div style={{ display: "flex", alignItems: "baseline", gap: "var(--s-3)", flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 10, color: theme.textMuted, fontWeight: 700, textTransform: "uppercase" }}>Price Target</div>
              <div className="num" style={{ fontSize: "var(--t-input)", fontWeight: 700 }}>
                {fmtUsd(d.Analyst_Target_Consensus)}
              </div>
            </div>
            {(() => {
              const upside =
                d.Analyst_Target_Upside != null
                  ? d.Analyst_Target_Upside
                  : d.Price != null && d.Price > 0
                    ? d.Analyst_Target_Consensus! / d.Price - 1
                    : null;
              if (upside == null) return null;
              const color = upside >= 0 ? theme.growth : theme.decline;
              return (
                <div>
                  <div style={{ fontSize: 10, color: theme.textMuted, fontWeight: 700, textTransform: "uppercase" }}>
                    {upside >= 0 ? "Upside" : "Downside"}
                  </div>
                  <div className="num" style={{ fontSize: "var(--t-input)", fontWeight: 700, color }}>
                    {fmtPct(upside, 1, { fromFraction: true, signed: true })}
                  </div>
                </div>
              );
            })()}
            {typeof d.Analyst_Grade_Score === "number" && (
              <span
                className={`badge ${d.Analyst_Grade_Score > 0.15 ? "badge-good" : d.Analyst_Grade_Score < -0.15 ? "badge-bad" : "badge-warn"}`}
                title="Analyst grade score: net buy-rated tilt, roughly in [-1, 1]"
              >
                Grade {fmtNum(d.Analyst_Grade_Score, 2)}
              </span>
            )}
          </div>
        </section>
      )}

      {/* News & Peers -- two independently-conditional sub-blocks. */}
      {((Array.isArray(d.News_Snippets) && d.News_Snippets.length > 0) ||
        (Array.isArray(d.Peers) && d.Peers.length > 0)) && (
        <section style={{ marginTop: "var(--s-4)" }}>
          <h3 style={{ fontSize: "var(--t-body)", color: theme.textMuted, margin: "0 0 var(--s-1-5)" }}>News & Peers</h3>
          {Array.isArray(d.News_Snippets) && d.News_Snippets.length > 0 && (
            <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
              {d.News_Snippets.map((n, i) => (
                <li key={i} style={{ marginBottom: "var(--s-1-5)" }}>
                  <a
                    href={n.url}
                    target="_blank"
                    rel="noreferrer"
                    style={{ color: theme.accent, fontSize: "var(--t-label)", fontWeight: 600, textDecoration: "none" }}
                  >
                    {n.title}
                  </a>
                  <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted, marginTop: 2 }}>
                    {[n.site, n.published_date].filter(Boolean).join(" · ")}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {Array.isArray(d.Peers) && d.Peers.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-1-5)", marginTop: "var(--s-2)" }}>
              {d.Peers.map((p) => (
                <span key={p} className="chip">{p}</span>
              ))}
            </div>
          )}
        </section>
      )}

      <div style={{ marginTop: "var(--s-4-5)" }}>
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
    <section className="glass-panel" style={{ marginTop: "var(--s-4)", padding: "var(--s-4)", borderRadius: "var(--r-md)" }}>
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
        <span style={{ fontWeight: 700, fontSize: "var(--t-subhead)" }}>ATM Greeks roll-up (held, actionable)</span>
        <span style={{ color: theme.textMuted }}>{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div style={{ marginTop: "var(--s-3)" }}>
          {portfolio.loading ? (
            <Loading lines={1} />
          ) : !held ? (
            <div className="empty" style={{ padding: "var(--s-4-5)" }}>
              No account snapshot — connect a brokerage or run the pipeline to populate holdings.
            </div>
          ) : included.length === 0 ? (
            <div className="empty" style={{ padding: "var(--s-4-5)" }}>
              None of your held symbols has an actionable directive with ATM Greeks.
            </div>
          ) : (
            <>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-4-5)" }}>
                <RollupStat label="Σ Δ delta" value={fmtNum(sums.delta, 3)} />
                <RollupStat label="Σ Γ gamma" value={fmtNum(sums.gamma, 3)} />
                <RollupStat label="Σ V vega" value={fmtNum(sums.vega, 3)} />
                <RollupStat label="Σ Θ theta/day" value={fmtNum(sums.theta, 3)} />
                <RollupStat
                  label="30d Θ carry"
                  value={fmtNum(sums.theta * 30, 2)}
                />
              </div>
              <p style={{ fontSize: "var(--t-footnote)", color: theme.textMuted, marginTop: "var(--s-2-5)", lineHeight: 1.5 }}>
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
      <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted }}>{label}</div>
      <div className="num" style={{ fontSize: "var(--t-subhead)", fontWeight: 700 }}>{value}</div>
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
    <section className="glass-panel" style={{ marginTop: "var(--s-4)", padding: "var(--s-4)", borderRadius: "var(--r-md)" }}>
      <h2 style={{ fontSize: "var(--t-subhead)", margin: "0 0 var(--s-1)" }}>Recompute with custom parameters</h2>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-label)", marginTop: 0, marginBottom: "var(--s-3)" }}>
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

      <div style={{ display: "flex", gap: "var(--s-2-5)", flexWrap: "wrap", marginTop: "var(--s-2-5)" }}>
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
      <div style={{ display: "flex", gap: "var(--s-2-5)", flexWrap: "wrap", marginTop: "var(--s-2-5)" }}>
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

      <div style={{ marginTop: "var(--s-3-5)" }}>
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
        <Notice variant="warn" style={{ marginTop: "var(--s-3)" }}>
          <span>{mutation.error}</span>
        </Notice>
      )}

      {result && (
        <div style={{ marginTop: "var(--s-4)" }}>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--s-2)",
              marginBottom: "var(--s-3)",
              fontSize: "var(--t-label)",
              color: theme.textSecondary,
            }}
          >
            <span className="chip">Target DTE {result.target_dte}</span>
            <span className="chip">VIX {fmtNum(result.vix, 1)}</span>
            <span className="chip">{result.market_regime ?? "—"}</span>
          </div>

          {result.errors.length > 0 && (
            <Notice variant="warn" style={{ marginBottom: "var(--s-3)" }}>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {result.errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </Notice>
          )}

          {directives.length === 0 ? (
            <div className="empty" style={{ padding: "var(--s-4-5)" }}>
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
          asOf={new Date().toISOString()}
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
  useAutoPoll(reload, "options", { hasError: error != null });
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("premium");
  const [openSymbol, setOpenSymbol] = useState<string | null>(null);
  const [showRecompute, setShowRecompute] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const { openChat } = useChat();

  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  const directives = data?.directives ?? [];

  const cleanCount = directives.filter((d) => d.Integrity_OK === true).length;
  const flaggedCount = directives.length - cleanCount;
  const trueIvrCount = directives.filter((d) => effectiveIvr(d).isTrue).length;

  const visible = useMemo(() => {
    const activeFilter = FILTERS.find((f) => f.key === filter)!;
    const rows = directives.filter(
      (d) => activeFilter.test(d) && d.Symbol.toLowerCase().includes(searchQuery.toLowerCase())
    );
    const sorted = [...rows];
    if (sort === "premium") sorted.sort(byNum((d) => d.Net_Premium));
    else if (sort === "ivr") sorted.sort(byNum((d) => effectiveIvr(d).value));
    else if (sort === "sigma") sorted.sort(byNum((d) => d.Sigma_GARCH));
    else sorted.sort((a, b) => a.Symbol.localeCompare(b.Symbol));
    return sorted;
  }, [directives, filter, sort, searchQuery]);

  const openDirective = openSymbol
    ? directives.find((d) => d.Symbol === openSymbol) ?? null
    : null;

  return (
    <div className="screen" style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--s-3)" }}>
        <div>
          <button
            onClick={back}
            style={{
              background: "none",
              border: "none",
              padding: 0,
              cursor: "pointer",
              color: theme.textSecondary,
              fontSize: "var(--t-callout)",
              marginBottom: "var(--s-2)",
            }}
          >
            ← Back
          </button>
          <div style={{ display: "flex", alignItems: "baseline", gap: "var(--s-2)" }}>
            <h1 className="screen-title" style={{ margin: 0 }}>Options premium</h1>
          </div>
        </div>
        <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-4)", alignItems: "center" }}>
          <Button variant="neutral" onClick={() => resetGridLayout("options-matrix")}>Reset Layout</Button>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <Button
            onClick={() => openChat(buildOptionsContextText(directives))}
            style={{
              background: "var(--surface-3)",
              color: "var(--growth)",
              border: "1px solid var(--border)",
              fontSize: "var(--t-body)",
              fontWeight: 600,
              padding: "4px 12px",
              borderRadius: "var(--r-md)",
            }}
          >
            🤖 Ask Gemini
          </Button>
          {data?.as_of && (
            <span style={{ fontSize: "var(--t-caption)", color: theme.textMuted }}>{timeAgo(data.as_of)}</span>
          )}
        </div>
      </div>

      <TabGuide tabKey="options" />

      <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column" }}>
        {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}

      {!loading && !error && data && directives.length === 0 && (
        <div className="empty" style={{ padding: "var(--s-7-5)" }}>
          {data.reason ?? "No options directives generated yet."}
        </div>
      )}

      {!loading && !error && data && directives.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
          {/* Read-only context row */}
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--s-2)",
              margin: "var(--s-2) 0 var(--s-3)",
              fontSize: "var(--t-label)",
              color: theme.textSecondary,
            }}
          >
            <span className="chip">
              Target DTE {data.target_dte ?? "—"}
            </span>
            <span className="chip">VIX {fmtNum(data.vix ?? null, 1)}</span>
            <span className="chip">{data.market_regime ?? "—"}</span>
          </div>

          {/* Persistent honesty banner — reflects what THIS cycle's data actually
              is, not a static claim. When settings.OPTIONS_TRUE_IVR_ENABLED is on
              and at least one symbol resolved a real options-chain-derived IV
              rank, say so — but the fallback is per-row (see `effectiveIvr`), so
              even here the banner names the fallback explicitly rather than
              implying every row is chain-derived. */}
          {trueIvrCount === 0 ? (
            <Notice variant="warn" style={{ marginBottom: "var(--s-3)" }}>
              <span>
                <strong>IVR here is a realized-volatility rank</strong> (IVR_Proxy) — no options
                chain is fetched, so this is <em>not</em> true implied-vol rank. Advisory only; no
                orders are placed.
              </span>
            </Notice>
          ) : (
            <Notice variant="info" style={{ marginBottom: "var(--s-3)" }}>
              <span>
                <strong>IVR</strong> is a real, options-chain-derived 30-day ATM IV rank for{" "}
                {trueIvrCount} of {directives.length} symbols this cycle (marked "chain" below),
                falling back to a realized-volatility proxy (marked "proxy") where chain or
                history data wasn't available. Advisory only; no orders are placed.
              </span>
            </Notice>
          )}

          {/* Summary Metrics Banner */}
          <div className="glass-panel" style={{ display: "flex", gap: "var(--s-4)", padding: "var(--s-3) var(--s-4)", borderRadius: "var(--r-md)", marginBottom: "var(--s-4)", alignItems: "center" }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted, textTransform: "uppercase", letterSpacing: "0.5px" }}>Actionable Premium</div>
              <div className="num" style={{ fontSize: "var(--t-display)", fontWeight: 700, color: theme.growth }}>
                {fmtUsd(directives.filter(isActionable).reduce((sum, d) => sum + (d.Net_Premium || 0), 0))}
              </div>
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted, textTransform: "uppercase", letterSpacing: "0.5px" }}>Integrity</div>
              <div style={{ fontSize: "var(--t-subhead)", fontWeight: 700 }}>
                {flaggedCount === 0 ? (
                  <span style={{ color: theme.growth }}>✅ All Clean</span>
                ) : (
                  <span style={{ color: theme.caution }}>⚠️ {flaggedCount} Flagged</span>
                )}
              </div>
            </div>
          </div>

          <div style={{ display: "flex", gap: "var(--s-3)", marginBottom: "var(--s-3)", flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: "200px" }}>
              <Input
                label="Search Symbol"
                type="text"
                placeholder="e.g. AAPL"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="glass-input"
              />
            </div>
            <div style={{ flex: 1, minWidth: "200px" }}>
              <Select
                label="Sort"
                value={sort}
                onChange={(e) => setSort(e.target.value as Sort)}
                options={SORT_OPTIONS}
                className="glass-input"
              />
            </div>
          </div>

          {/* Filter Segmented Control */}
          <div className="segmented" style={{ marginBottom: "var(--s-4)", overflowX: "auto" }}>
            {FILTERS.map((f) => {
              const count = directives.filter(f.test).length;
              const active = filter === f.key;
              return (
                <button
                  key={f.key}
                  type="button"
                  onClick={() => setFilter(f.key)}
                  className={active ? "on" : ""}
                  style={{ padding: "0 var(--s-2)", whiteSpace: "nowrap" }}
                >
                  {f.label} <span className="num" style={{ fontSize: "0.85em", opacity: 0.7 }}>{count}</span>
                </button>
              );
            })}
          </div>



          {visible.length === 0 ? (
            <div className="empty" style={{ padding: "var(--s-6)" }}>
              No directives match this filter.
            </div>
          ) : (
            <div style={{ flex: 1, minHeight: 0, marginTop: "var(--s-3)" }}>
              <DynamicGrid
                layoutKey="options-matrix"
                defaultLayouts={{
                  lg: visible.map((d, i) => ({
                    i: d.Symbol,
                    x: (i % 3) * 4,
                    y: Math.floor(i / 3) * 4,
                    w: 4,
                    h: 4,
                    minW: 3,
                    minH: 3,
                  })),
                }}
              >
                {visible.map((d) => (
                  <div key={d.Symbol}>
                    <DirectiveCard d={d} onOpen={() => setOpenSymbol(d.Symbol)} />
                  </div>
                ))}
              </DynamicGrid>
            </div>
          )}

          <div style={{ marginTop: "var(--s-4)" }}>
            <GreeksRollup directives={directives} />
          </div>
        </div>
      )}

      {openDirective && (
        <DetailSheet
          d={openDirective}
          dte={data?.target_dte ?? 30}
          asOf={data?.as_of}
          onClose={() => setOpenSymbol(null)}
        />
      )}

      <button
        type="button"
        onClick={() => setShowRecompute((v) => !v)}
        aria-expanded={showRecompute}
        className="btn btn-neutral"
        style={{ marginTop: "var(--s-5)", width: "100%" }}
      >
        {showRecompute ? "▲ Hide" : "▼"} Recompute with custom parameters
      </button>

      {showRecompute && <OptionsRecomputeSection />}

      <div style={{ marginTop: "var(--s-8)", marginBottom: "var(--s-4)" }}>
        <OptionsAnalyticsDashboard />
      </div>
      </div>
    </div>
  );
    </div>
  );
}
