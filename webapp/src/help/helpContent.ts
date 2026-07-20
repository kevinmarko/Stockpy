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
import { fmtNum, fmtPct, fmtUsd } from "../format";
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
    `The suggested fraction of your capital for one position, from the fractional (half-) Kelly formula using your real trade history, capped at ${fmtPct(t?.kelly_cap, 0, { fromFraction: true })} and then by a per-name advisory ceiling. 0.14 means 'up to 14% of capital' — still advisory only. The final number is 'post-regime': the pre-regime Kelly figure gets multiplied by the HMM regime multiplier and the meta-label composite before the cap is applied — see 'regime multiplier' and 'meta-label composite' for the breakdown.`,
  "regime multiplier":
    "A 0-1 multiplier on Kelly Target driven by the HMM's risk-on probability — it shrinks suggested position size in a bearish regime and defaults to 1.0 (no effect) when the HMM hasn't run. It carries zero directional score of its own; it only adjusts sizing.",
  "meta-label composite":
    "The geometric mean of every active signal module's confidence that a signal is correct (P(signal correct)), multiplied into Kelly Target alongside the regime multiplier. A value of exactly 1.0 for every symbol is expected, not a bug, until real MetaLabelers are trained and registered — it's the honest 'no-op' default. A hard 0.0 means a registered MetaLabeler gated the signal below the platform's minimum confidence.",
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
  "execution mode":
    "The Robinhood order queue's posture: 'off' builds nothing, 'review' builds a dry-run queue for you to confirm, 'live' still requires the same per-trade human confirmation before any order reaches the broker — no mode ever places an order automatically.",
  "kill switch":
    "A global, file-based safety switch. While active, the execution queue adds no new orders and Pilot follows are paused. Pausing does not stop the pipeline schedule — cycles keep running, they just produce no actionable output.",
  "notional cap": (t) => {
    const cap = t?.robinhood_max_notional_per_order;
    const rendered = cap != null && cap > 0 ? fmtUsd(cap) : "not configured";
    return `The hard per-order USD ceiling the execution queue enforces before an intent is marked placeable: ${rendered}. An intent above the cap is blocked, never silently resized.`;
  },
  "follow minimum": (t) =>
    `The smallest dollar amount the Follow modal accepts for a Pilot allocation: ${fmtUsd(t?.follow_min_amount)}. A UX floor, not a broker constraint — the gated queue itself is bounded by the per-order notional cap.`,
  "opportunity scan": (t) =>
    `A Robinhood broker scan run by the agentic-discovery skill, cross-referenced against this platform's own advisory engine — never run automatically. Results are capped at ${fmtNum(t?.agentic_max_candidates, 0)} candidates regardless of how many the scan matches; a candidate with no computed action shows '—', never a guessed one.`,
  cointegration:
    "Two symbols whose price spread is stationary — it mean-reverts instead of wandering — tested via the Engle-Granger method. The basis for every pair on the Pairs radar screen; a broken cointegration (rolling ADF p-value > 0.10) exits the trade.",
  "half-life":
    // Fixed algorithm parameter (signals/pairs_trading.py-equivalent), not a
    // Thresholds API field — same "documented literal" precedent as "iv rank"/vrp below.
    "How many trading days a pair's spread takes to close half the distance back to its rolling mean, from an Ornstein-Uhlenbeck fit. Pairs radar only surfaces pairs with a half-life between 5 and 60 days — too fast is noise, too slow ties up capital.",
  "z-score":
    "How many standard deviations the current spread sits from its rolling mean. Pairs radar enters at |z| > 2, exits on a 0-cross, and stops out at |z| > 4.",
  "correlation cluster":
    // 30% concentration flag is a local frontend constant (Attribution.tsx's
    // HEAVY_CONCENTRATION_THRESHOLD), not a Thresholds API field.
    "A group of your holdings that move together, from realized return correlation — not sector labels. A cluster making up more than 30% of book value is flagged as a hidden-concentration risk even if it looks diversified by sector.",
  "risk gate":
    // Sahm/VIX/HY-OAS trigger levels are fixed constants in the macro kill-switch
    // check, not Thresholds API fields — same documented-literal precedent as above.
    "The pre-trade check that vetoes a new BUY when the macro regime looks dangerous (Sahm Rule ≥ 0.5, VIX > 30, or HY OAS > 6%). Mission Control's block log lists every order it actually stopped, and why. Operators can switch it off for hybrid mode, in which technical signals run without the macro override.",
  "circuit breaker":
    // The 24h dedup window is a documented literal default (gui/circuit_breakers.py
    // ::collect_circuit_breaker_trips's `window` parameter), not a Thresholds API
    // field — same "documented literal" precedent as "half-life"/"iv rank" above.
    "The kill switch plus every risk-gate block, merged into one severity-classified view: CRITICAL (halts everything, e.g. the kill switch or a daily loss limit) or WARNING (a single order blocked). Deduped to the most recent trip per breaker within a rolling 24h window so a chatty block log doesn't bury the signal — an unresolved trip stays visible until a newer one for that same breaker supersedes it.",
  "orchestrator daemon":
    "The always-on background process that keeps the platform's heavy engines warm between cycles instead of paying full startup cost on every run. Its own internal timer can run cycles on a schedule independent of a manual trigger from the Pipeline screen.",
  "analyst note":
    "An on-demand Claude-written narrative for one symbol — a one-sentence headline, a why-now catalyst paragraph, 1-3 key-risk bullets, and an invalidation condition that would void the thesis. Grounded in the platform's own deterministic numbers, never inventing new ones, and only generated when you click Generate — nothing here runs automatically.",
  "chart-pattern read":
    "An on-demand Gemini Vision interpretation of a symbol's recent price chart — a pattern label (e.g. 'ascending triangle'), qualitative support/resistance levels, and a short narrative. Advisory only; it never feeds back into the deterministic pipeline, and the chart image itself can render even when the AI read fails.",
  "research brief":
    "An on-demand grounded research summary (Opal) synthesized from real retrieved news, earnings, and macro context for one symbol — thesis context, catalysts, risk factors, and recent developments. Qualitative only by construction: no price target or score is ever fabricated, and a list is left empty rather than filled with an invented item.",
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
    keyConcepts: ["deployable", "pbo", "dsr", "sharpe ratio", "max drawdown", "follow minimum"],
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
  agentic: {
    title: "Agentic Trading",
    description:
      "The consolidated command center for the platform's Robinhood-backed loop: Pilot follows, the gated dry-run order queue, scan-based candidate discovery, and the decision journal. Every control here is advisory-only or paper-first — placing a real order always requires a separate, human-confirmed step outside this screen.",
    keyConcepts: [
      "advisory only",
      "execution mode",
      "kill switch",
      "notional cap",
      "opportunity scan",
      "follow minimum",
    ],
  },
  activity: {
    title: "Activity",
    description:
      "A chronological feed of the pipeline's own alerts — Info, Warning, and Critical severities — read straight from the structured alert log. An unrecognized severity is shown as-is, never upgraded to a fabricated level.",
    keyConcepts: [],
  },
  compare: {
    title: "Pilot Strategy Comparison",
    description:
      "Pick up to 5 Pilots to overlay their performance curves and compare Sharpe, PBO, DSR, and follower count side by side — the same honesty metrics as the Pilots screen, just side by side. Also surfaces the platform's current recommended-stock picks.",
    keyConcepts: ["sharpe ratio", "pbo", "dsr", "follow minimum"],
  },
  models: {
    title: "The models",
    description:
      "The ML model registry behind the platform's forecasts — each model's honest CPCV-validated DSR and PBO, training date, and sample size. A model that fails a gate is shown as not deployable, never loosened to force a green badge.",
    keyConcepts: ["deployable", "dsr", "pbo"],
  },
  pairs: {
    title: "Pairs radar",
    description:
      "Cointegrated stat-arb pairs and their current spread state — z-score, half-life, hedge ratio, and cointegration p-value per pair. A cointegration break (rolling ADF p-value > 0.10) exits the trade even without a stop. Advisory only.",
    keyConcepts: ["cointegration", "z-score", "half-life"],
  },
  "data-explorer": {
    title: "Data explorer",
    description:
      "The platform's recommended-stock picks, plus the raw data layer for any symbol — daily price bars, current fundamentals, and the macro snapshot (VIX, 10y-2y curve, Sahm Rule, HY OAS). Manage which tickers are tracked from Settings.",
    keyConcepts: [],
  },
  attribution: {
    title: "Portfolio attribution",
    description:
      "Decomposes your book's return versus a benchmark into Allocation, Selection, and Interaction (Brinson-Fachler), plus multifactor exposure (Value/Quality/Low-Vol/Size) and correlation clusters that flag hidden concentration — a cluster over 30% of book value gets called out even if it looks diversified by sector.",
    keyConcepts: ["brinson-fachler", "multifactor", "correlation cluster"],
  },
  commands: {
    title: "Commands",
    description:
      "An autocomplete composer over the platform's full CLI manifest — type or pick a command, resolve its options, and copy the exact string to run in your own terminal. This screen never executes anything itself, by design: running platform CLIs from a browser would bypass the advisory quarantine. Also hosts the read-only Robinhood execution queue below it.",
    keyConcepts: ["advisory only", "kill switch", "notional cap"],
  },
  observability: {
    title: "Mission Control",
    description:
      "The platform's single risk-telemetry surface: macro regime (VIX, Sahm Rule, HY OAS, yield curve, HMM risk-on probability), portfolio-wide equity/drawdown history, forecast-model skill weights, the circuit-breaker dashboard (kill switch + risk-gate blocks, deduped and classified CRITICAL/WARNING within a rolling window), and the raw risk-gate block log — every order the pre-trade gate actually vetoed, and why.",
    keyConcepts: ["hmm regime", "risk gate", "circuit breaker"],
  },
  pipeline: {
    title: "Pipeline",
    description:
      "The orchestrator daemon's live status and manual run triggers — full pipeline, data-only, or metrics-only — plus run history. Distinct from Settings' automation summary: this is the raw daemon the trigger buttons act directly against. A run with no recorded outcome shows '—', never a fabricated success.",
    keyConcepts: ["orchestrator daemon"],
  },
  settings: {
    title: "Data & Automation",
    description:
      "Operate the platform without SSHing into the host: pipeline status and manual triggers, the automated run schedule, the kill switch and execution mode, which tickers are tracked, your brokerage connection, active Pilot follows, and app/update status — all in one place.",
    keyConcepts: ["kill switch", "execution mode", "advisory only"],
  },
  "symbol-detail": {
    title: "Symbol Detail",
    description:
      "Deep dive on one symbol: the advisory recommendation, the regime-multiplier sizing breakdown behind that Kelly Target, factor exposure, risk & regime, rolling beta, forecast skill, and the persisted options directive — plus three on-demand AI generation cards you can trigger yourself: a Claude analyst note, a Gemini chart-pattern read, and an Opal research brief. Each AI card is independent and generated only when you click its Generate button; an honest, provider-specific message explains why a card has nothing to show (e.g. a disabled capability or a missing API key) rather than a generic error.",
    keyConcepts: [
      "advisory only",
      "kelly target",
      "regime multiplier",
      "meta-label composite",
      "analyst note",
      "chart-pattern read",
      "research brief",
    ],
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
