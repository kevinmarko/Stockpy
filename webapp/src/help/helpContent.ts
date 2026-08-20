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
  "live trade proposal":
    "A real, unfilled order an MCP tool proposed against your live brokerage account. It sits in `pending_approval` until a human explicitly approves or rejects it here — this screen is the ONLY way to act on one; there is no MCP-callable approve/reject, so a proposal can never be filled without an operator looking at it first.",
  "paper broker":
    "A simulated trading environment that tracks mock cash and positions without risking real money, allowing strategies to be tested against real-time market data.",
  "tiered cost model":
    "A fee estimation model that applies different slippage and commission rates based on trade size and asset liquidity, rather than a flat fixed rate.",
  "slippage":
    "The difference between the expected price of a trade and the actual price at which it executes, simulated to reflect real-world execution costs.",
  "advisory only":
    "The platform recommends; you decide. It is in advisory mode by default — no order is ever sent to a broker automatically. Every action signal, size, and options directive is informational.",
  "action signal":
    "The system's recommendation for each ticker: STRONG BUY, BUY, HOLD, RISK REDUCE, or AVOID. Purely informational — act on your own judgment.",
  conviction:
    "A score between 0 and 1 for how confident the system is in a recommendation. A conviction of 0.80 is NOT a promise of an 80% win rate — it reflects certainty, which the Calibration screen lets you verify empirically.",
  calibration:
    "A reliability check: 'when the system says conviction 0.80, does it actually win 80% of the time?' The reliability diagram compares stated conviction to the realized win rate per bin.",
  "buy range":
    "The technical zone where purchasing the asset is considered favorable, below which momentum may be failing, and above which it is overextended. Sourced from the technical options engine; purely informational.",
  "sell/stop range":
    "The zone to take profits, paired with a hard stop-loss level. Sourced from the technical options engine; purely informational.",
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
  "signal driver weight":
    "The mean absolute contribution (score × weight) a signal module carries, averaged across every currently-tracked symbol — a quick 'which signals matter most right now' view. This is a linear, configured-weight breakdown, not SHAP or a machine-learned feature-importance measure: it uses the exact same fixed weights as 'signal weight' above, and captures no interaction effects between modules. A module with no data for a symbol this cycle is left out of that symbol's average entirely, never counted as a zero.",
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
  "attention weight":
    "How much the BERT-LLA forecaster's self-attention layer weighted each day in its lookback window when forming a forecast — shaded on the price chart, darker means higher weight. This reflects which days the model itself found most informative, not a buy/sell signal or a claim about future importance. Only appears when BERT-LLA actually ran for that request; absent otherwise, never a fabricated overlay.",
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
  "prompt registry":
    "Version control for every AI-facing instruction the platform sends to an LLM. Pinning a version freezes it against future auto-updates; a pin only changes what the AI is TOLD, never what the platform is PERMITTED to do — order submission, the advisory quarantine, the risk gate, and the kill switch are enforced in Python regardless of registry content.",
  "circuit breaker":
    // The 24h dedup window is a documented literal default (gui/circuit_breakers.py
    // ::collect_circuit_breaker_trips's `window` parameter), not a Thresholds API
    // field — same "documented literal" precedent as "half-life"/"iv rank" above.
    "The kill switch plus every risk-gate block, merged into one severity-classified view: CRITICAL (halts everything, e.g. the kill switch or a daily loss limit) or WARNING (a single order blocked). Deduped to the most recent trip per breaker within a rolling 24h window so a chatty block log doesn't bury the signal — an unresolved trip stays visible until a newer one for that same breaker supersedes it.",
  "orchestrator daemon":
    "The always-on background process that keeps the platform's heavy engines warm between cycles instead of paying full startup cost on every run. Its own internal timer can run cycles on a schedule independent of a manual trigger from the Pipeline screen.",
  "sizing cap":
    "A durable log of every time the platform's automatic position-sizing guardrails shrank a trade below what the raw signal called for. The 'Constraint' column names which limit did it — kelly_cap (the per-position Kelly ceiling), vol_target_leverage, max_position_weight, portfolio_gross (the whole-book exposure cap), or escalation (a symbol capped for several cycles running gets derated further). This is capacity management, not a sign anything is wrong: a symbol showing up here often is being sized smaller than its signal alone would suggest, on purpose.",
  "etf transmission":
    "A non-fundamental risk source: a stock heavily owned by ETFs can absorb a shock to one of its basket-mates purely through ETF arbitrage, even when nothing about the stock itself changed (Ben-David, Franzoni & Moussawi, 2018). Shown per symbol as ownership %, and comovement R² with its primary wrapper after removing the shared market-factor effect — a high reading can mean its position size is being quietly derated to compensate.",
  "symbol rating":
    // SYMBOL_RATING_AUTO_DROP_ENABLED / SYMBOL_RATING_DROP_THRESHOLD_CYCLES
    // are settings.py fields, not Thresholds API fields (GET /thresholds
    // doesn't surface them) -- same "documented literal" precedent as
    // "iv rank"/"half-life"/"circuit breaker" above.
    "Every tracked symbol gets a GOOD/BAD rating from the platform's scoring engine each cycle. After enough consecutive BAD-rated cycles (5 by default), a symbol CAN be automatically excluded from tracking and buying — but this auto-drop behavior is off by default, and even when it's on, it never applies to anything you currently hold. An excluded symbol shows an 'Excluded' badge on Tracked Universe; 'Re-include' immediately undoes the exclusion by hand.",
  "analyst note":
    "An on-demand Claude-written narrative for one symbol — a one-sentence headline, a why-now catalyst paragraph, 1-3 key-risk bullets, and an invalidation condition that would void the thesis. Grounded in the platform's own deterministic numbers, never inventing new ones, and only generated when you click Generate — nothing here runs automatically.",
  "chart-pattern read":
    "An on-demand Gemini Vision interpretation of a symbol's recent price chart — a pattern label (e.g. 'ascending triangle'), qualitative support/resistance levels, and a short narrative. Advisory only; it never feeds back into the deterministic pipeline, and the chart image itself can render even when the AI read fails.",
  "research brief":
    "An on-demand grounded research summary (Opal) synthesized from real retrieved news, earnings, and macro context for one symbol — thesis context, catalysts, risk factors, and recent developments. Qualitative only by construction: no price target or score is ever fabricated, and a list is left empty rather than filled with an invented item.",
  "semantic similarity":
    "How closely a target stock's business description matches a candidate sector's description, via a local sentence-embedding model (SBERT) and cosine similarity — a number from -1 (opposite) to 1 (identical meaning), NOT keyword overlap. Unavailable ('—') when either description is missing or no embedding backend is configured.",
  "sector heat factor":
    // A DIFFERENT construct from the platform's other "Sector Heat Factor"
    // dashboard column (GDELT news-volume smoothing) -- see
    // docs/signals/sector_heat_factor.md's "Two features, one name" section
    // for the full disambiguation. This entry describes the Sector
    // Selection screen's own SHF specifically.
    "A Gaussian response to how much news + investor-forum volume a candidate sector is seeing, normalized against every OTHER candidate sector over a trailing 22-trading-day window — not a raw volume count. When investor-forum (Reddit/StockTwits) volume has never been observed, this degrades honestly to news-volume-only, flagged 'Investor-forum volume unavailable' rather than showing a fabricated number.",
  "sector correlation coefficient":
    "Semantic Related Sector Selection's ranking score: cosine similarity × Sector Heat Factor. Sectors are ranked by this number and the top N are selected as the most relevant related sectors for a target stock. '—' whenever either input side is unavailable — never computed from a partial or guessed value.",
  "sentiment score":
    "A -1 to 1 read of financial-news/social sentiment for a symbol, from FinBERT (or a keyword-lexicon fallback) scored headlines. Positive means bullish tone, negative bearish. Blank ('—') whenever the sourcing agent is unavailable for that request, never guessed.",
  "sentiment intensity":
    "0.1 to 1: how emotionally extreme or high-volume the sourced news/social commentary is right now, independent of its direction (sentiment score).",
  "credibility score":
    "0.1 to 1: a filter for 'rumor mill' spikes — low credibility means the sentiment reading is likely noise rather than a durable signal.",
  "volatility persistence":
    "A GJR-GARCH measure of how long a volatility shock takes to decay back toward baseline for a symbol. Computed independently from price history, not from the sentiment agent — it stays populated even when the sentiment agent itself is unavailable.",
  "earnings catalyst":
    "How close a symbol is to its next scheduled earnings print, and whether that proximity is currently dampening its live news-sentiment score. Fully zeroed out inside the pre-earnings blackout window (the read is considered too unreliable to use at all), halved in the days just before that window and again for about a day after the print (the reaction is still settling), and full-strength otherwise — including when no earnings date is currently scheduled.",
  "finbert classification":
    "The 3-class (positive / neutral / negative) sentiment read a headline gets from FinBERT, a language model tuned on financial text — falling back to a simple keyword lexicon when the model isn't available. The probability bar shown next to a headline is this classification, not a single collapsed score.",
  "news provider":
    "Which live source actually supplied a symbol's scored headlines this request — a primary provider with a fallback behind it. Shown honestly as unset when neither is configured, rather than showing an empty feed with no explanation.",
  // The three entries below back the Settings screen's Data Auto-Refresh
  // card. Deliberately written with ZERO numeric literals: the interval,
  // floors, and defaults they describe are user-set local device
  // preferences (localStorage), not values sourced from GET /thresholds --
  // quoting a current number here would describe something that can drift
  // out from under the text with no update mechanism to catch it. Covered
  // by helpContent.test.ts's "contains no digit" assertion.
  "auto-refresh":
    "A master switch, off by default, that lets each screen reload its own data in the background on a timer you control, plus a toggle for each data category and a shared refresh interval. Polling pauses automatically while this browser tab is in the background, and — if you turn that option on — while the market is closed, so nothing refreshes uselessly off-hours.",
  "market session":
    "Whether the primary US equity market is open right now, computed locally from the current time against the exchange's trading calendar rather than fetched from the server. Auto-refresh's 'pause when market closed' option reads this to decide whether background polling should pause or keep running.",
  "safety telemetry":
    "A switch separate from the auto-refresh master above, governing only the kill-switch and heartbeat readout in the top bar. It keeps polling on its own schedule even when auto-refresh is turned off or the market is closed — a stale safety reading is treated as a risk here, not as something worth pausing to save a background request.",
  "tax loss harvesting":
    "Selling securities at a loss to offset a capital gains tax liability. The Cache Long/Short strategy flags these opportunities automatically based on settings, holding them in a 'tax bank' tally.",
  "proxy hedge":
    "A highly correlated alternative security (like a sector ETF) bought when selling the original asset for tax-loss harvesting to maintain market exposure while waiting out the wash-sale rule window.",
  "correlation drift":
    "When a proxy security stops tracking its target asset closely enough. A background process continually monitors this correlation and flags if the proxy relationship weakens below a safety threshold.",
  "options delta":
    "The rate of change of an option's price per $1 move in the underlying stock. Calls have positive delta (0 to 1), puts have negative delta (-1 to 0). A delta of 0.30 means the option price moves ~$0.30 for each $1 stock move.",
  "options theta":
    "The daily time-decay of an option's price — how much value the option loses each day just from the passage of time, all else equal. Always negative for long positions.",
  "options gamma":
    "The rate of change of delta per $1 move in the underlying. High gamma means delta changes rapidly, making the position more sensitive to large stock moves.",
  "implied volatility":
    "The market's forward-looking expectation of the underlying's annualized volatility, backed out of the option's current market price via the Black-Scholes model. Higher IV means pricier options.",
  "chance of profit":
    "The estimated probability that an option position is profitable at expiration, accounting for the premium paid. Derived from Black-Scholes: for a call, it is N(d2) where d2 uses the break-even price (strike + premium) instead of the strike alone.",
};

/** tabKey → help. Keyed by a stable per-screen slug (see each screen's usage). */
export const TAB_HELP: Record<string, TabHelp> = {
  "live-trade-approvals": {
    title: "Live Trade Approvals",
    description:
      "The one place an operator can approve or reject a REAL live-trade proposal an MCP tool has queued against your actual brokerage account. There is no MCP-callable equivalent — this screen IS the human-in-the-loop enforcement surface, not just a convenience view. A proposal expires on its own if nobody acts on it in time.",
    keyConcepts: ["live trade proposal", "advisory only"],
  },
  "paper-broker": {
    title: "Paper Broker",
    description:
      "A local simulated brokerage for end-to-end execution testing. Orders are executed against real-time market data with simulated slippage.",
    keyConcepts: ["paper broker", "tiered cost model", "slippage"],
  },

  dashboard: {
    title: "Dashboard",
    description:
      "Your at-a-glance advisory home: today's action signals, holdings, and conviction per name. Everything here is advisory-only — the platform recommends and you decide; no orders are ever placed for you.",
    keyConcepts: ["advisory only", "action signal", "conviction", "kelly target"],
  },
  pilots: {
    title: "Pilots",
    description:
      "Browse strategy 'Pilots' you can follow. Each Pilot now shows its actual current BUY/SELL/HOLD call per holding, with buy and sell/stop ranges. The honesty badges (Deployable / Not deployable) and the PBO · DSR · Sharpe · Max-DD row show whether a Pilot actually cleared its backtest gates — never a marketing number.",
    keyConcepts: [
      "deployable",
      "pbo",
      "dsr",
      "sharpe ratio",
      "max drawdown",
      "follow minimum",
      "buy range",
      "sell/stop range",
    ],
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
      "For one symbol, how each pluggable signal module scored it and how those weighted scores combined into the composite that drives the recommendation. Type or pick a ticker to load it. Below that, a universe-wide 'signal driver weights' panel shows which modules carry the most weight on average across every tracked symbol.",
    keyConcepts: [
      "signal weight",
      "signal driver weight",
      "multifactor",
      "cross-sectional momentum",
      "conviction",
    ],
  },
  forecast: {
    title: "Forecast Viewer",
    description:
      "Multi-horizon, probabilistic price forecasts for one symbol, with the model's volatility (GJR-GARCH) and regime inputs. Forecasts are not guarantees — an input that can't be computed shows '—', never a fabricated number.",
    keyConcepts: ["forecast", "garch vol", "hmm regime", "attention weight"],
  },
  sentiment: {
    title: "Sentiment Dynamics",
    description:
      "Live sentiment analysis from financial news and social media for one symbol, driven by the Antigravity Agent, alongside a GJR-GARCH volatility-persistence read, a real scored-headline feed, and an archived sentiment-vs-VIX trend chart. Every value degrades honestly to '—' when the sourcing agent is unavailable — never a guessed number.",
    keyConcepts: [
      "sentiment score",
      "sentiment intensity",
      "credibility score",
      "volatility persistence",
      "earnings catalyst",
      "finbert classification",
      "news provider",
    ],
  },
  "strategy-insights": {
    title: "Strategy Insights",
    description:
      "A per-Pilot deep dive: real holdings coverage, archived news-sentiment coverage for Pilots that weight the news-catalyst signal, and a 'what if I allocated $X' allocation simulator. Every projection is computed fresh per Pilot and per allocation size — never a single reused number across Pilots.",
    keyConcepts: ["deployable", "kelly target", "news provider"],
  },
  "create-data-app": {
    title: "Create Data App",
    description:
      "Name a custom view built from the same real, live widgets Strategy Insights uses, and save it as a working sidebar shortcut — never a decorative form. Saved to this browser's storage (survives a reload, syncs across its other open tabs); if your browser blocks or fills that storage you'll see an honest warning instead of a false 'Saved'.",
    keyConcepts: ["edge ratio"],
  },
  "custom-view": {
    title: "Data App",
    description:
      "An operator-saved custom view. Every widget here is the same live component used elsewhere in the app (Strategy Insights' charts, the platform's grounded chat) — a Data App is a saved combination of those, not a separate implementation.",
    keyConcepts: ["edge ratio"],
  },
  "sector-selection": {
    title: "Sector Selection",
    description:
      "Ranks candidate upstream/downstream industry sectors by how relevant they are to a target stock — combining semantic similarity (SBERT, cosine similarity) with each sector's Sector Heat Factor (recent news + investor-forum volume). The top N ranked sectors are the ones this platform's research treats as most relevant for supplementing a thin single-stock signal. Every field is '—' when it honestly couldn't be computed, never a fabricated number — and a persistent banner explains when investor-forum volume specifically is unavailable.",
    keyConcepts: ["semantic similarity", "sector heat factor", "sector correlation coefficient"],
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
  "symbol-screener": {
    title: "Symbol screener",
    description:
      "Search FMP's full symbol universe by name or ticker, or filter it by sector, industry, market cap, price, beta, or dividend yield — independent of your tracked watchlist. Send a discovered symbol straight to Paper Broker's Quick Trade, or a whole selection to its Strategy Scan.",
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
      "An autocomplete composer over the platform's full CLI manifest — type or pick a command, resolve its options, then copy the exact string to run in your own terminal or run it here directly (gated behind an operator-enabled flag, off by default). High-stakes commands — the kill switch, a forced broker re-login — require an extra confirmation before they execute. Also hosts the read-only Robinhood execution queue below it.",
    keyConcepts: ["advisory only", "kill switch", "notional cap"],
  },
  observability: {
    title: "Mission Control",
    description:
      "An attention summary up top flags anything that actually needs a look — circuit-breaker trips, risk-gate blocks, a sizing-cap escalation, a stale heartbeat, the macro gate being off — with an honest 'All clear' when nothing does. Below that, always visible: the macro regime gate control (VIX, Sahm Rule, HY OAS, yield curve, HMM risk-on probability, and the toggle that vetoes new BUYs during a bad regime) and portfolio risk/equity history. Everything else — forecast-model skill (portfolio-wide and per-symbol), the circuit-breaker dashboard, the raw risk-gate block log, system telemetry, per-symbol quote latency, the sizing-cap audit trail, ETF volatility transmission, heartbeat, strategy P&L, and the log tail — lives in a collapsed 'Background telemetry' section below, expandable on demand.",
    keyConcepts: ["hmm regime", "risk gate", "circuit breaker", "sizing cap", "etf transmission"],
  },
  pipeline: {
    title: "Pipeline",
    description:
      "The orchestrator daemon's live status and manual run triggers — full pipeline, data-only, or metrics-only — plus run history. Distinct from Settings' automation summary: this is the raw daemon the trigger buttons act directly against. A run with no recorded outcome shows '—', never a fabricated success.",
    keyConcepts: ["orchestrator daemon"],
  },
  console: {
    title: "Console",
    description:
      "One-click launchers for the platform's background jobs — a preflight check, the pytest suite, an advisory pipeline cycle, full verification, a Gravity AI Review Suite audit, and the validation harness backtest — each streamed live via the log panel below. Runs the same gated job-execution infrastructure Commands' Run control uses; nothing here places an order with a broker.",
    keyConcepts: ["advisory only", "deployable"],
  },
  reports: {
    title: "Report Library",
    description:
      "Every generated report artifact in one place: the daily HTML report, the orchestrator's dashboards, generated daily briefings, and per-strategy validation reports (with the same Deployable / PBO / DSR gates Strategy Health shows). Large dashboards are download-only by default — you opt in to viewing one inline rather than it loading automatically.",
    keyConcepts: ["deployable", "pbo", "dsr"],
  },
  prompts: {
    title: "Prompt Registry",
    description:
      "Version control for every AI-facing instruction: the resolved version, source, and pin state for each registered prompt, plus a per-prompt diff viewer. Pinning only changes what the AI is told, never what the platform is permitted to do — order submission, the advisory quarantine, the risk gate, and the kill switch stay enforced in Python regardless of registry content.",
    keyConcepts: ["prompt registry", "advisory only"],
  },
  "settings-general": {
    title: "General & Execution Mode",
    description:
      "Global orchestrator behavior in one place: the kill switch, live-trading execution mode (advisory-only / dry-run / paper / live), app & update status, and a reset for the first-run onboarding flow.",
    keyConcepts: ["kill switch", "execution mode", "advisory only"],
  },
  "settings-data": {
    title: "Data & Automation",
    description:
      "Operate the pipeline without SSHing into the host: the orchestrator daemon's live run status and manual triggers, the automated run schedule, and how fresh your brokerage snapshot is.",
    keyConcepts: ["orchestrator daemon", "auto-refresh"],
  },
  "settings-universe": {
    title: "Tracked Universe",
    description:
      "Add or remove the symbols the pipeline processes on every run, and see per-symbol data coverage — including each symbol's rating history and whether it's currently excluded by the platform's automated rating system.",
    keyConcepts: ["symbol rating"],
  },
  "settings-brokers": {
    title: "Brokerage Connections",
    description:
      "Connect, disconnect, or force-refresh your Robinhood link. Login now uses a device-approval push (tap 'approve' in the Robinhood app) instead of a typed authenticator code — credential values are never echoed back by the API.",
    keyConcepts: [],
  },
  "settings-modules": {
    title: "Modules & Integrations",
    description:
      "Entry points to every `.env`-write surface and sub-system: the Strategy Matrix (per-module signal weights), general runtime tunables, the scoped sentiment/sector-selection/FMP/ETF-transmission editors, the Prompt Registry, the AI Control Center, and your active Pilot follows.",
    keyConcepts: ["signal weight", "prompt registry"],
  },
  "settings-feature-flags": {
    title: "Feature Flags",
    description:
      "Admin and execution capabilities, grouped into two kinds. Write & Execution Gates enable or disable writes, live trading execution, AI generation APIs, and critical automation loops — some require typed confirmation before toggling because they can fundamentally change platform behavior from 'advisory-only' to 'live trading execution'. Diagnostic & Data Features are read-only measurement/data-source switches with no execution risk of their own.",
    keyConcepts: ["advisory only", "kill switch"],
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
  "cache-long-short": {
    title: "Cache Long/Short",
    description:
      "A systematic tax-loss harvesting (TLH) overlay. It monitors concentrated equity positions for TLH opportunities, generating a proxy hedge (like a highly-correlated sector ETF) to maintain beta exposure while avoiding wash-sale rules. Pending trades are routed here for approval before taking effect.",
    keyConcepts: ["tax loss harvesting", "proxy hedge", "correlation drift"],
  },
  "options-chain": {
    title: "Options Chain Explorer",
    description:
      "Interactive options chain for a single symbol — browse available expirations, inspect bid/ask/IV/Greeks per strike, and see the statistically-grounded Chance of Profit for each contract. Chain data comes from yfinance; the underlying spot price for Greek calculations comes from the FMP quote endpoint for reliability. Use Builder mode to construct common multi-leg strategies (spreads, straddles, calendars) from the chain, then review the combined order in the ticket — Paper orders simulate a fill, and Live orders require an explicit confirmation and remain subject to advisory-only constraints (no order is actually routed to a broker yet).",
    keyConcepts: ["options delta", "options theta", "implied volatility", "chance of profit"],
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
