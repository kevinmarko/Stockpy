/**
 * helpContent.ts — the webapp's in-app education store.
 * ====================================================
 *
 * A curated TypeScript port of the core of the Streamlit Command Center's
 * `gui/help_content.py` (`TAB_HELP` + `GLOSSARY`), scoped to the PWA's core
 * screens. Rendered by `<TabGuide tabKey=… />` as a dismissible "How this works"
 * panel.
 *
 * Content is authored (not machine-generated). Every glossary entry that
 * quotes a deployability-gate or sizing threshold (PBO, DSR, net Sharpe, Max
 * Drawdown, the stress-gate limit, the Kelly cap) is a FUNCTION over the live
 * `Thresholds` fetched from `GET /thresholds` (`help/thresholds.ts`), never a
 * hard-coded literal — mirroring `gui/help_content.py`'s own rule ("Never
 * hard-code numeric thresholds here"). Every other entry is static prose. A
 * function entry degrades to "—" per number (via `fmtNum`/`fmtPct`'s existing
 * null-handling) if thresholds haven't loaded yet or the fetch failed — never
 * a guessed value.
 */
import { fmtNum, fmtPct } from "../format";
import type { Thresholds } from "../api/types";

export interface TabHelp {
  /** Short screen title shown in the panel header. */
  title: string;
  /** One-paragraph plain-English explanation of what the screen is for. */
  description: string;
  /** Glossary keys (into GLOSSARY) surfaced as expandable term chips. */
  keyConcepts: string[];
}

/** A glossary definition: static prose, or a live-threshold template. */
export type GlossaryValue = string | ((t: Thresholds | null) => string);

/** term key (lower-case) → plain-English definition. */
export const GLOSSARY: Record<string, GlossaryValue> = {
  "advisory only":
    "The platform recommends; you decide. It is in advisory mode by default — no order is ever sent to a broker automatically. Every action signal, size, and options directive is informational.",
  "action signal":
    "The system's recommendation for each ticker: STRONG BUY, BUY, HOLD, RISK REDUCE, or AVOID. Purely informational — act on your own judgment.",
  conviction:
    "A score between 0 and 1 for how confident the system is in a recommendation. A conviction of 0.80 is NOT a promise of an 80% win rate — it reflects certainty, which the Calibration screen lets you verify empirically.",
  calibration:
    "A reliability check: 'when the system says conviction 0.80, does it actually win 80% of the time?' The reliability diagram compares stated conviction to the realized win rate per bin.",
  "reliability diagram":
    "The chart on the Calibration screen. Points on the diagonal are perfectly calibrated; above the line = underconfident, below = overconfident. Bins with too little data read 'insufficient', never a fabricated win rate.",
  "kelly target": (t) =>
    `The suggested fraction of your capital for one position, from the fractional (half-) Kelly formula using your real trade history, capped at ${fmtPct(t?.kelly_cap, 0, { fromFraction: true })} and then by a per-name advisory ceiling. 0.14 means 'up to 14% of capital' — still advisory only.`,
  "edge ratio":
    "Post-trade quality: how far a trade ran in your favor (MFE) versus against you (MAE). An edge ratio ≥ 1 means favorable excursion dominated adverse excursion.",
  "mfe / mae":
    "Maximum Favorable Excursion and Maximum Adverse Excursion — the best and worst unrealized moves during a trade's life. Together they measure trade quality independent of the final exit.",
  deployable: (t) =>
    `An honesty badge. A strategy is 'deployable' only if it clears every validation gate — PBO < ${fmtNum(t?.pbo_max, 1)}, DSR > ${fmtNum(t?.dsr_min, 2)}, net-of-cost Sharpe > ${fmtNum(t?.net_sharpe_min, 1)}, Max Drawdown < ${fmtPct(t?.max_drawdown_max, 0, { fromFraction: true })}. A strategy that fails any gate reads 'not deployable', never softened.`,
  pbo: (t) =>
    `Probability of Backtest Overfitting — how likely a backtest's edge is luck rather than real, via Combinatorial Purged Cross-Validation. Lower is better; must be < ${fmtNum(t?.pbo_max, 1)} (${fmtNum(t?.pbo_max, 1)} is coin-flip) to deploy.`,
  dsr: (t) =>
    `Deflated Sharpe Ratio — the Sharpe adjusted for how many parameter combinations were tried, since testing many inflates the best in-sample Sharpe by chance. Must be > ${fmtNum(t?.dsr_min, 2)} to deploy.`,
  "sharpe ratio": (t) =>
    `Average return divided by the standard deviation of returns — risk-adjusted performance. Deployment requires a net-of-costs Sharpe > ${fmtNum(t?.net_sharpe_min, 1)}.`,
  "max drawdown": (t) =>
    `The largest peak-to-trough drop in the equity curve, as a fraction of peak equity. Must be < ${fmtPct(t?.max_drawdown_max, 0, { fromFraction: true })} for standard strategies; options-selling strategies must also stay < ${fmtPct(t?.stress_max_drawdown, 0, { fromFraction: true })} in every dated shock window (2008, 2018, 2020, 2024).`,
  "signal weight":
    "How much each signal module contributes to the final composite score: total = sum of (module_score × weight) across active modules. Weights are tunable in the Strategy Matrix.",
  multifactor:
    "A cross-sectional blend of Value, Quality, Low-Volatility, and Size z-scores into one composite, ranking each name against the rest of the universe.",
  "cross-sectional momentum":
    "Ranks the universe by 12-1 month return (12-month lookback, skipping the most recent month to avoid reversal bias). Top-half names score positive. Based on Jegadeesh-Titman (1993).",
  "hmm regime":
    "A Hidden Markov Model's probability (0-1) that the market is in a risk-on regime. It multiplies the Kelly Target, so bearish readings shrink suggested sizes; when it can't run it defaults to 1.0 (no effect).",
  forecast:
    "A multi-horizon, probabilistic price projection — never a guarantee. An input that can't be computed shows '—', never a fabricated number.",
  "garch vol":
    "A GJR-GARCH volatility estimate that weights recent bad days more than good ones (the leverage effect) — more accurate than a plain moving standard deviation. It's the primary vol input for sizing and options.",
  "put credit spread":
    "Sells a put and buys a lower-strike protective put, collecting premium if the stock stays above the short put; max loss is the spread width minus premium. Suggested only when IVR, VRP, and macro are all favorable. Advisory only.",
  "iron condor":
    "A put credit spread below the market plus a call credit spread above it, profiting if the stock stays in a range until expiry. Requires favorable IV. Advisory only.",
  "iv rank":
    // IVR/VRP/VIX gate values here are literal constants inside
    // technical_options_engine.py (not settings-derived) — gui/help_content.py
    // hard-codes them too for the same reason, so this matches its precedent
    // rather than being an inconsistency with the live-threshold entries above.
    "Implied Volatility Rank — where current IV sits in its past-year range. IVR 80 = top 20% of the year, historically a good time to sell premium. Credit spreads require IVR > 50.",
  vrp:
    "Volatility Risk Premium — implied volatility in excess of realized. When options charge more than the stock actually moves, there's premium to collect. A VRP > 0.02 is required before recommending a premium-selling strategy.",
  "brinson-fachler":
    "Attribution that splits benchmark out-/under-performance into Allocation (right sectors?), Selection (right stocks within a sector?), and Interaction (the combined effect).",
};

/** tabKey → help. Keyed by a stable per-screen slug (see each screen's usage). */
export const TAB_HELP: Record<string, TabHelp> = {
  dashboard: {
    title: "Dashboard",
    description:
      "Your at-a-glance advisory home: today's action signals, holdings, and conviction per name. Everything here is advisory-only — the platform recommends and you decide; no orders are ever placed for you.",
    keyConcepts: ["advisory only", "action signal", "conviction", "kelly target"],
  },
  pilots: {
    title: "Pilots",
    description:
      "Browse strategy 'Pilots' you can follow. The honesty badges (Deployable / Not deployable) and the PBO · DSR · Sharpe · Max-DD row show whether a Pilot actually cleared its backtest gates — never a marketing number.",
    keyConcepts: ["deployable", "pbo", "dsr", "sharpe ratio", "max drawdown"],
  },
  portfolio: {
    title: "Portfolio",
    description:
      "Your real holdings with unrealized and realized P&L, plus Brinson-Fachler attribution that decomposes performance into allocation, selection, and interaction. Cost basis comes from your brokerage snapshot; prices and indicators from the pipeline — those roles never cross.",
    keyConcepts: ["brinson-fachler", "edge ratio", "advisory only"],
  },
  calibration: {
    title: "Calibration",
    description:
      "The 'did our actual calls work?' honesty surface: model confidence vs. real outcomes, your decisions vs. the model's baseline, and post-trade excursion quality. Under-populated bins read 'insufficient data' — never a fabricated win rate.",
    keyConcepts: [
      "conviction",
      "calibration",
      "reliability diagram",
      "edge ratio",
      "mfe / mae",
    ],
  },
  "strategy-health": {
    title: "Strategy Health",
    description:
      "The statistical-soundness view: each strategy's Deployable verdict against the four validation gates. Options-selling strategies add a tail-scenario stress gate. A failing gate honestly reads 'not deployable' — tap a term below for the exact live threshold.",
    keyConcepts: ["deployable", "pbo", "dsr", "sharpe ratio", "max drawdown"],
  },
  signals: {
    title: "Signal Breakdown",
    description:
      "For one symbol, how each pluggable signal module scored it and how those weighted scores combined into the composite that drives the recommendation. Type or pick a ticker to load it.",
    keyConcepts: [
      "signal weight",
      "multifactor",
      "cross-sectional momentum",
      "conviction",
    ],
  },
  forecast: {
    title: "Forecast Viewer",
    description:
      "Multi-horizon, probabilistic price forecasts for one symbol, with the model's volatility (GJR-GARCH) and regime inputs. Forecasts are not guarantees — an input that can't be computed shows '—', never a fabricated number.",
    keyConcepts: ["forecast", "garch vol", "hmm regime"],
  },
  options: {
    title: "Options Matrix",
    description:
      "Premium-selling strategy directives per active symbol: recommended structure (Put Credit Spread, Iron Condor, or Cash/Wait), strikes, net premium, and Greeks. Gated by IVR > 50, VRP > 0.02, VIX < 30, and no CREDIT EVENT — Cash/Wait is returned when any gate fails. All informational.",
    keyConcepts: ["put credit spread", "iron condor", "iv rank", "vrp", "garch vol"],
  },
};

/**
 * Look up a glossary definition by key; `undefined` when absent (never
 * throws). `thresholds` is only consulted by the small set of entries that
 * are functions — `null` (not yet loaded / fetch failed) renders "—" for each
 * live number rather than a stale or guessed value.
 */
export function glossaryDef(key: string, thresholds: Thresholds | null = null): string | undefined {
  const entry = GLOSSARY[key];
  if (entry === undefined) return undefined;
  return typeof entry === "function" ? entry(thresholds) : entry;
}
