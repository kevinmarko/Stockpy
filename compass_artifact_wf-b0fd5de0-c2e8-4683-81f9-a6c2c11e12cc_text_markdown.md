# Adding Modern Systematic Strategies to InvestYo/Stockpy: Strategies, Backtesting, Execution & Risk

## TL;DR
- **Build a layered strategy library, not one monolith.** The highest-leverage additions to your modular platform are (1) a small set of *robust classics* you can validate quickly — time-series momentum, dual momentum, cross-sectional ranking, RSI-2/Bollinger mean reversion, cointegration pairs — plus (2) *portfolio-construction overlays* (volatility targeting, risk parity, HMM regime gating) and (3) *one carefully-built ML layer* (LightGBM cross-sectional ranker + Lopez de Prado meta-labeling/triple-barrier). These map cleanly onto your existing engines and feed your 0–100 scoring kernel as additive signal modules.
- **Your biggest risk is not strategy choice — it's validation.** The codebase's VectorBT parameter sweeps plus yfinance data are a textbook recipe for backtest overfitting and survivorship bias. Adopt purged/embargoed k-fold and combinatorial purged cross-validation (CPCV), the deflated Sharpe ratio, walk-forward analysis, and realistic cost/slippage modeling before trusting any result.
- **Go paper-first with Alpaca, then optionally IB.** Alpaca (alpaca-py) now supports Level 3 multi-leg options in both paper and live, fits your async orchestrator, and is the lowest-friction path; Interactive Brokers via ib_async (the maintained successor to ib_insync) is the upgrade for breadth. Pair every strategy with fractional (half/quarter) Kelly sized from *estimated* win rates, volatility targeting, and your existing 6% portfolio-heat halt.

---

## Key Findings

1. **A complexity-spanning set of ~11 strategies integrates naturally with your engine layout.** Most are signal generators that should emit a normalized score into `strategy_engine.py`; a few (vol targeting, risk parity, HMM gating) are portfolio/risk overlays that belong in `evaluation_engine.py`/`strategy_engine.py` sizing logic; options-selling belongs in `technical_options_engine.py`.

2. **Each classic strategy has documented decay or failure modes.** Momentum suffers rare but catastrophic crashes; mean reversion breaks in trends and bear markets; pairs trading dies when cointegration breaks; the factor "zoo" is rife with non-replicable factors. Honest reporting of these is part of the deliverable.

3. **The volatility risk premium is real and persistent** — implied volatility has exceeded subsequent realized volatility ~84% of the time, by an average of 3.82 percentage points (SPX/VIX data since 1996, per OptionStrat: "implied volatility exceeds the market's realized results almost 84% of the time and by an average of 3.82 percentage points"; Barclays and the CFA Institute report ~4.1–4.2 vol points over 1990–2024). This is the economic basis for your options-selling matrix — but the payoff is negatively skewed (frequent small wins, rare large losses), so sizing and regime-gating matter more than win rate.

4. **Validation tooling exists in Python** but the gold-standard methods (CPCV, deflated Sharpe, purged CV) now live in paid/closed or fragmented libraries (mlfinlab is commercial; mlfinpy/skfolio and hand-rolled code are the open alternatives). You will likely implement these yourself.

5. **Execution is a solved problem for retail quant** in 2024–2026: Alpaca and IB both offer paper environments, multi-leg options, and mature Python SDKs; the key engineering work is state persistence, reconciliation, and kill switches — which dovetails with your existing macro kill switch.

---

## Details

### Part 1 — The Strategy Library (classic → modern)

For each: rationale, signal logic, parameters, strengths/failure modes, and engine integration.

---

#### 1. Time-Series (Absolute) Momentum / Trend Following
**Rationale.** A security's own past return predicts its near-future return. Moskowitz, Ooi & Pedersen (2012, *Journal of Financial Economics* 104:228–250) documented significant time-series momentum across 58 liquid futures contracts (equities, bonds, commodities, FX) over 1985–2009: all 58 instruments showed positive time-series momentum, and 52 of 58 were "statistically different from zero at the 5% significance" level. A diversified composite delivered substantial abnormal returns with little exposure to standard factors and performed *best* in extreme markets ("crisis alpha"). Hurst, Ooi & Pedersen (2017, *Journal of Portfolio Management*) extended the evidence back to 1880.

**Signal logic.** Core rule: if trailing 12-month excess return > 0 → long; if < 0 → flat/short. The 12-month lookback with 1-month holding is canonical; the *sign* of the past return is the predictor. Position is volatility-scaled (e.g., target constant ex-ante vol per position).

**Parameters.** Lookback 3–12 months (252-day or 12-month ROC); rebalance monthly; optional SMA(200) or 10-month MA filter (Clenow/Faber style).

**Strengths / failure modes.** Strong diversifier, convex in crises. Fails in choppy, mean-reverting, range-bound markets (whipsaw); suffers turnover and tax drag; effect reverses at horizons beyond ~12 months.

**Integration.** New function in `processing_engine.py` computing 12M ROC and a trend-state flag (you already have SMA50/200, Aroon, Coppock — natural home). Emit a momentum sub-score into the `strategy_engine.py` kernel (you already have a "momentum/trend" input — formalize it as signed, vol-scaled 12M return). ATR (already present) drives the vol scaling.

---

#### 2. Cross-Sectional Momentum (Relative Strength Ranking)
**Rationale.** Jegadeesh & Titman (1993): stocks that outperformed peers over 3–12 months continue to outperform over the next 1–3 months. This is *relative* ranking, distinct from #1.

**Signal logic.** Rank universe by trailing 12-1 month return (12-month return skipping the most recent month to avoid 1-month reversal). Long top decile/quintile, optionally short bottom. Rebalance monthly.

**Parameters.** Formation 6–12 months, skip 1 month, hold 1–3 months, top/bottom 10–30%.

**Strengths / failure modes.** Robust across markets and asset classes; but **momentum crashes** are severe. Per Daniel & Moskowitz, "Momentum Crashes" (2016, *Journal of Financial Economics* 122:221–247), the two worst months were consecutive — July/August 1932, when "the past-loser decile portfolio returned 232% and the past-winner decile portfolio had a gain of only 32%"; and in March–May 2009 "the past-loser decile rose by 163% and the decile portfolio of past winners gained only 8%." Single-month winner-minus-loser (WML) losses reached roughly −74% (July 1932) and the factor experienced sustained drawdowns June 1932–December 1939 and March 2009–March 2013. The crashes are partly forecastable — they "occur in 'panic' states – following market declines and when market volatility is high – and are contemporaneous with market rebounds," driven by loser-decile betas rising above 3 while winner betas fall below 0.5. High turnover; crowding has compressed returns since publication.

**Integration.** This requires a *cross-sectional* step your per-ticker pipeline lacks. Add a portfolio-level ranking pass in `main_orchestrator.py` after per-ticker scores are computed, or a dedicated `ranking` function. Your existing "relative strength vs SPY" in `processing_engine.py` is a single-name proxy; generalize to a universe rank percentile and feed it as a cross-sectional momentum score. A dynamic momentum strategy that scales by forecast mean/variance "approximately doubles the alpha and Sharpe Ratio of a static momentum strategy" (Daniel & Moskowitz 2016) — worth implementing the volatility scaling.

---

#### 3. Dual Momentum (Antonacci)
**Rationale.** Gary Antonacci's *Dual Momentum Investing* (2014) combines relative momentum (pick the strongest asset) with absolute momentum (only hold it if it has positive trailing return / beat T-bills), else rotate to bonds. Antonacci's Global Equity Momentum (GEM) reported long-run outperformance with materially lower drawdowns than buy-and-hold (third-party backtests cite ~17.4%/yr vs ~8.9% for a global index with ~22.7% vs ~60% max drawdown over ~39 years — practitioner figures, treat as illustrative).

**Signal logic (GEM).** Monthly: compare 12-month return of US equities (SPY) vs ex-US (e.g., ACWI ex-US / VEU). Pick the higher. Then apply absolute momentum: if that asset's 12-month excess return > 0, hold it; else hold aggregate bonds (AGG).

**Parameters.** 12-month lookback, monthly rebalance, ~1.5 trades/year historically.

**Strengths / failure modes.** Simple, low turnover, strong drawdown control via the absolute-momentum "off switch." Underperforms in strong bull markets and sharp V-shaped rebounds (whipsaw on the bond switch); sensitive to the single lookback parameter.

**Integration.** A natural *allocation overlay* sitting above your per-ticker scoring. Implement as a small module consuming the 12M returns (from `processing_engine.py`) of a handful of ETFs, outputting a target asset. Couples well with your `macro_engine.py` regime/kill-switch — dual momentum's absolute leg is itself a market-timing risk control.

---

#### 4. Mean Reversion — RSI-2 / Bollinger / Z-score
**Rationale.** Short-term overreactions revert. Larry Connors' 2-period RSI (RSI-2) is the canonical systematic version: buy short-term oversold dips within a longer uptrend.

**Signal logic (RSI-2, Connors).** Trade only with the trend: price > SMA(200). Buy when RSI(2) < 10 (more aggressive: < 5); exit when price closes above SMA(5) or RSI(2) > ~70. Bollinger/z-score variant: enter when z-score of price vs SMA(20) < −2, exit at the mean.

**Parameters.** RSI period 2; entry threshold 5–10; trend filter SMA(200); z-score ±2 on a 20-period window.

**Strengths / failure modes.** High win rate in range-bound markets. **Degrades sharply in bear markets / strong trends** — "buy the dip" repeatedly fails; per QuantifiedStrategies the 2008 and March 2020 episodes saw RSI-2 win rates drop below 60%, and out-of-sample 2015–2025 shows decay from HFT competition. A VIX < 25 regime filter helps. Connors found stops *hurt* performance statistically, but that conflicts with prudent risk control — use a time-stop or regime exit instead.

**Integration.** Drop-in for `processing_engine.py` (you already compute RSI; add the 2-period variant and z-score). Emit a mean-reversion sub-score. Critically, gate it with `macro_engine.py` RISK-OFF and a volatility filter so it doesn't fire into crashes.

---

#### 5. Statistical Arbitrage / Pairs Trading (Cointegration)
**Rationale.** Two economically linked securities share a common stochastic trend; their spread is stationary and mean-reverts. Trade deviations from equilibrium.

**Signal logic.**
- **Test cointegration:** Engle-Granger (regress A on B via OLS, ADF-test the residual for stationarity) or Johansen (multivariate, handles >2 assets).
- **Hedge ratio:** static OLS β, or a **Kalman filter** for a dynamic, time-varying hedge ratio (Ernie Chan's EWA/EWC example) — avoids the lookback-window free parameter and updates online (reducing lookahead bias). Chan notes that if you use a Kalman filter, "there is no expectation that the stocks A and B are cointegrating at all… [it] is typically used in a mean reversion strategy where there is no true cointegration."
- **Spread dynamics:** model the spread as an Ornstein-Uhlenbeck process; estimate the **half-life of mean reversion** (= −ln(2)/θ, or via the AR(1) coefficient) to set holding period and Bollinger lookback.
- **Entry/exit:** enter when spread z-score > +2 (short spread) or < −2 (long spread); exit at 0 (mean). Chan caution: ±2σ is a convention, not optimal — calibrate to the half-life.

**Parameters.** Cointegration p-value < 0.05; z entry ±2, exit 0, stop ±3–4; half-life sets the rolling window.

**Strengths / failure modes.** Market-neutral, low beta. **Cointegration breaks down** — Chan's rule: for static mean-reversion strategies, loss of cointegration (failed ADF) compels immediate liquidation; "short-term mean-reversion does not require cointegration/stationarity, so you can continue trading as long as the time series mean-reverts." Crowded; spreads have thinned. Requires constant rebalancing if hedge ratio is dynamic.

**Integration.** New `pairs` capability. Cointegration tests via `statsmodels` (you already use it for ARIMA). The Kalman filter belongs in `forecasting_engine.py` (sits beside your other models) or a new stat-arb module; emit a spread z-score and a "cointegration-valid" flag. Pairs P&L is portfolio-level, so reconcile in `evaluation_engine.py`. Note: the open-source mlfinlab pairs/`arbitragelab` tooling is now commercial (Hudson & Thames) — plan to hand-roll with statsmodels + pykalman.

---

#### 6. Volatility Risk Premium / Systematic Options Selling
**Rationale.** Implied volatility systematically exceeds subsequent realized volatility (the volatility/variance risk premium), so selling options is compensated insurance. As above, IV has exceeded realized vol ~84% of the time by ~3.8 vol points (OptionStrat; ~4.1–4.2 points per Barclays/CFA Institute over 1990–2024). This is the economic engine behind your existing options matrix.

**Signal logic.** Sell premium when it's "expensive": gate entries on **IV Rank** (IVR = (current IV − 52w low)/(52w high − 52w low)) or IV percentile. Typical rule: only sell when IVR > 50 (one backtest set found filtering iron condors to IVR > 50 lifted returns ~4%/yr net of brokerage). Structures: cash-secured puts, covered calls, short strangles, and defined-risk iron condors. For condors, sell ~15–20 delta short strikes, 30–45 DTE; manage at 50% of max profit; stop near 200% of credit received.

**Parameters.** IVR > 50 entry; 16–30 delta shorts (POP ~65–85%); 30–45 DTE; 50% profit target; defined-risk wings.

**Strengths / failure modes.** Persistent premium, high win rate. But payoff is **negatively skewed**: negative gamma and negative vega mean a vol spike can erase many wins — a "90% POP" position can still have a negative Sharpe once tail losses and multi-leg slippage are included. A strategy capturing the S&P 500 volatility premium "lost more than 48 percent in October 2008" (Alpha Architect, "The Variance Risk Premium is Pervasive"), and crowded short-vol blew up in February 2018 (XIV). Regime-gate hard.

**Integration.** This is precisely your `technical_options_engine.py` (you already have Black-Scholes, Greeks, delta-to-strike via Brentq, GJR-GARCH vol, an IVR proxy, and the strategy matrix). Two concrete upgrades: (a) make the IVR proxy more faithful (your realized-vol proxy understates the true implied-vs-realized gap of ~3.8 vol points); (b) feed a "VRP regime" flag (IVR + macro VIX from `macro_engine.py`) so the matrix only sells premium when the premium is both high *and* not in a tail-risk regime.

---

#### 7. Factor Investing / Multi-Factor Models
**Rationale.** Cross-sectional return differences are explained by exposures to compensated factors: market, size (SMB), value (HML), profitability (RMW), investment (CMA) — the Fama-French five-factor model — plus momentum (UMD/WML) and low-volatility.

**Signal logic.** Build factor scores per stock (value = book/market or earnings yield; quality = ROE/profitability; momentum = 12-1; low-vol = inverse trailing vol; size = market cap). Z-score each factor cross-sectionally, combine into a composite, rank, and long the top.

**Parameters.** Monthly/quarterly rebalance; equal-weight or factor-risk-weighted composite; winsorize outliers.

**Strengths / failure modes.** Decades of evidence, but the **"factor zoo" is a replication minefield.** Hou, Xue & Zhang (2020, *Review of Financial Studies* 33(5):2019–2133) found that, with microcaps mitigated via NYSE breakpoints and value-weighted returns, **65% of 452 anomalies** cannot clear a single-test t > 1.96 (96% of trading-frictions anomalies fail); imposing a multiple-testing hurdle of 2.78 "raises the failure rate to 82%." Stick to the handful of factors with strong economic priors (value, momentum, quality, low-vol, size) and treat exotic factors with deep skepticism. Factors have long underperformance stretches (e.g., value 2017–2020).

**Integration.** You already compute fundamentals (Graham Number, DDM) and risk metrics — extend `processing_engine.py` with value/quality/low-vol factor scores. The cross-sectional combination happens at the orchestrator level (same ranking pass as #2). Emit a multi-factor composite sub-score to the kernel.

---

#### 8. Risk Parity & Volatility Targeting (Portfolio Construction Overlay)
**Rationale.** Equal *risk* contribution, not equal capital. Naive (inverse-vol) risk parity weights each asset ∝ 1/σ; equal-risk-contribution (ERC) accounts for the covariance matrix. Volatility targeting scales total exposure to hold portfolio vol at a constant target (e.g., 10% annualized), de-levering when vol spikes.

**Signal logic.** Inverse-vol weights: wᵢ = (1/σᵢ) / Σ(1/σₖ). Vol targeting: leverage = target_vol / realized_vol (e.g., EWMA σ with λ≈0.94). ERC requires numerical optimization. Hierarchical Risk Parity (Lopez de Prado) clusters by correlation then allocates — more robust than mean-variance to estimation error.

**Parameters.** Target vol 8–15%; vol estimation window 20–60 days or EWMA; rebalance when weights drift beyond a band.

**Strengths / failure modes.** Smoother equity curve, smaller drawdowns; vol targeting demonstrably improves Sharpe and tames tail risk for risk assets. But assumes similar Sharpe across assets; can over-lever low-vol assets (bonds); breaks when correlations jump to 1 in crises.

**Integration.** A sizing overlay in `strategy_engine.py` (alongside your Kelly sizing) and `evaluation_engine.py` (which already tracks portfolio heat). You already compute GARCH vol — feed it directly into the vol-targeting leverage scalar. This is the cleanest, highest-value risk upgrade you can make.

---

#### 9. Regime-Switching Allocation (HMM / Macro Gating)
**Rationale.** Markets exhibit persistent regimes (bull/low-vol, bear/high-vol, sideways). Conditioning strategy selection and exposure on the regime improves risk-adjusted returns.

**Signal logic.** Fit a Gaussian Hidden Markov Model (e.g., `hmmlearn`) to returns + realized vol (optionally macro inputs), inferring 2–3 latent states. Map states to target allocations (risk-on → equities; risk-off → bonds/cash). Use the smoothed state probability as a continuous gate rather than a hard switch. Statistical jump models (Bulla et al. lineage) are a more-robust recent alternative to HMMs for persistent regimes.

**Parameters.** 2–3 states; features = returns, rolling vol; refit on expanding window; gate exposure by P(risk-on).

**Strengths / failure modes.** Reduces drawdowns by avoiding high-vol regimes; published research found rotating factor models on HMM regime predictions outperformed any single factor model, and avoiding trades in HMM-identified high-vol regimes improved Sharpe. But HMMs are prone to overfitting, lag at turning points, and can "flicker" — use probability smoothing and require persistence.

**Integration.** This is a sibling to your existing `macro_engine.py` regime detection (yield curve, HY OAS, Sahm, VIX). Add an HMM-based statistical regime as a second opinion; combine with the macro rules into a single regime state that gates the kernel's risk posture (your kill switch is the hard version of this). Emit a regime-conditioned multiplier on position sizing.

---

#### 10. ML Cross-Sectional Ranker (LightGBM / XGBoost) — done correctly
**Rationale.** Gradient-boosted trees capture nonlinear interactions among many features for cross-sectional return ranking, naturally down-weighting irrelevant factors. They consistently beat linear baselines when tuned, and are the workhorse of modern quant factor research.

**Signal logic.** Features: PIT-safe momentum (1/3/12M), realized vol (20/60d), volume/liquidity proxies, fundamental factor scores, cross-sectional rank. Target: forward excess return vs benchmark (regression) or triple-barrier label (classification, see #11). Train in **expanding-window walk-forward** with purged folds and an embargo (e.g., 90 trading days) between train and predict to prevent leakage from overlapping forward-return labels. Use the model's predicted rank to select longs.

**Parameters.** Depth 3–8, learning rate 0.01–0.05, 800–3000 trees, early stopping, L1/L2 regularization, feature/row subsampling. Evaluate by cross-sectional Information Coefficient (IC), not just accuracy.

**Strengths / failure modes.** Strong predictive power, handles many features. But extreme overfitting risk; non-stationary markets degrade models; needs careful PIT data and leakage control. Feature importance ≠ causation.

**Integration.** A new model in `forecasting_engine.py` (sits beside your CNN-LSTM/ARIMA/Prophet) but used for *cross-sectional ranking*, not single-name price forecasting. Emit the predicted rank/score as one more input into the kernel — do **not** let it override the economic signals; treat it as an ensemble member. Consider Microsoft's **qlib** as a reference architecture for the ML factor pipeline (data server, LightGBM/LSTM built-ins, walk-forward).

---

#### 11. Meta-Labeling + Triple-Barrier Method (Lopez de Prado)
**Rationale.** From *Advances in Financial Machine Learning* (2018). Separate the *side* decision (your existing primary signal) from the *size/act* decision. A secondary ML model ("meta-label") learns whether to *act* on each primary signal and how confidently to size it — improving precision and reducing false positives.

**Signal logic.**
- **Triple-barrier labeling:** for each signal, set an upper barrier (profit-take, e.g., 2σ), lower barrier (stop, e.g., 1σ, σ from rolling vol *known at entry*), and a vertical barrier (max holding period). Label by which barrier is touched first (+1/−1/0). Critically, σ uses data strictly before entry (no lookahead).
- **Meta-labeling:** your primary model proposes long/short; the triple-barrier outcomes become the training target; a classifier (often LightGBM) predicts P(primary signal is correct). Use that probability for position sizing (e.g., scale Kelly fraction by it) and to filter low-probability trades.

**Parameters.** Barriers in units of rolling σ; vertical barrier = max hold; CUSUM filter for event sampling.

**Strengths / failure modes.** Improves Sharpe and precision; gives a principled, probabilistic sizing input. But adds complexity and another layer to overfit; requires the same purged-CV discipline. (Hudson & Thames' own study confirms that combining event-based sampling, triple-barrier, and meta-labeling improves strategy performance.)

**Integration.** This is the *connective tissue* between your signal engines and sizing. The triple-barrier labeler and meta-model belong in a new `labeling`/`meta` module (or `forecasting_engine.py`); the output P(correct) feeds `strategy_engine.py`'s Kelly sizing directly — replacing the arbitrary 0–100 score with an estimated probability. The open reference implementation is now fragmented: mlfinlab is commercial (£100/mo via QuantConnect); **mlfinpy** and **skfolio** are open alternatives, or hand-roll from the book.

---

### Part 2 — Robust Backtesting & Validation

Your codebase has two structural risks: **VectorBT parameter sweeps** (overfitting via multiple testing) and **yfinance data** (survivorship bias, non–point-in-time, corporate-action quirks). Address both.

**Biases to defend against:**
- **Lookahead / data-snooping bias.** Compute every indicator and label using only data available at decision time; σ for triple-barrier and vol-targeting must be lagged. Kalman/online methods (pairs) naturally avoid this.
- **Survivorship bias.** yfinance gives you *current* constituents and back-adjusted prices, not the universe as it existed historically — delisted/bankrupt names are missing, inflating backtest returns. Elton, Gruber & Blake (1996, *RFS*) estimated ~0.9% per year of survivorship bias in mutual-fund returns; the effect is larger for small-caps, and one 2026 emerging-market study found backtesting on *current* constituents "systematically excluded 1,185 stocks—82.5% of all companies that were ever in the index," inflating Sharpe to 1.16 and returns to 26.17%. Mitigation: use a point-in-time constituency dataset (academic standard is CRSP, which includes delisted stocks) or at minimum acknowledge the bias explicitly.
- **Overfitting from parameter sweeps.** Each parameter combination is a "trial"; the best of N trials has an inflated Sharpe even with zero true edge. Bailey, Borwein, Lopez de Prado & Zhu (2014, *Notices of the AMS*) show the expected maximum Sharpe from N independent trials grows with √(2·ln N) — testing ~1,000 combinations yields an expected best Sharpe of ~3.7 from pure noise.

**Validation methods (in order of rigor):**
1. **In-sample / out-of-sample split** — minimum bar; reserve a final hold-out you touch once.
2. **Walk-forward analysis** — expanding/rolling train→test, preserving time order; closest to live behavior but high variance (single path).
3. **Purged & embargoed k-fold CV** (Lopez de Prado) — purge training observations whose labels overlap the test window; add an embargo gap after each test fold. Standard k-fold is *invalid* in finance because labels overlap and data is autocorrelated (it violates the IID assumption).
4. **Combinatorial Purged Cross-Validation (CPCV)** — generates *many* backtest paths from combinations of purged train/test blocks (e.g., 10 train + 8 test folds → 36 paths), producing a *distribution* of out-of-sample Sharpe rather than a single number. A synthetic-environment study (ScienceDirect, 2024) found CPCV superior at preventing overfitting, with lower probability of backtest overfitting and higher deflated Sharpe than walk-forward. The current best-practice defense.
5. **Deflated Sharpe Ratio (DSR) & Probability of Backtest Overfitting (PBO)** — DSR (Bailey & Lopez de Prado 2014, *Journal of Portfolio Management* 40(5):94–107) corrects the observed Sharpe for the number of trials, non-normality (skew/kurtosis), and sample length, returning the probability the Sharpe is genuinely > 0. Report DSR and PBO alongside any headline Sharpe.

**Realistic costs.** Model commissions + bid/ask + slippage explicitly; for illiquid names add a slippage buffer of 1–2 ticks. Underestimating costs is the most common way a "profitable" backtest dies live (Chan's blunt framing: if edge ≈ cost, "you don't have a strategy. You have a hobby"). Trend strategies suffer most from slippage (chasing moves already in motion); mean-reversion suffers least.

**Python tooling tradeoffs:**
- **VectorBT** (you have it): unmatched speed for parameter sweeps via Numba; weak on realistic fills/slippage/partial fills — use for *research and robustness scans*, not as the final word. Pair sweeps with DSR/CPCV to discount the best result.
- **Backtrader** (you have it): event-driven, realistic order/commission/slippage modeling, broker bridges (IB/Alpaca/OANDA) — use for *final validation and the path to live*.
- **backtesting.py**: simplest, fast prototyping; no live trading or multi-asset.
- **zipline-reloaded**: best Pipeline API for long/short equity factor research with dynamic universes; setup friction (data-bundle ingestion); maintained Quantopian successor.
- **qlib** (Microsoft): AI-oriented; built-in LightGBM/LSTM, fast time-series data server, walk-forward — best for the ML factor pipeline (#10/#11).
- **mlfinlab**: implements triple-barrier, meta-labeling, purged CV, DSR — but is now **commercial**; **mlfinpy**/**skfolio** are open substitutes.

Recommended split: **VectorBT for sweeps → CPCV + DSR for honest significance → Backtrader for event-driven validation with costs → paper trade.**

### Part 3 — Live / Paper Trading Execution

**Brokerage options (2024–2026):**
- **Alpaca** (`alpaca-py`): API-first, commission-free US equities + ETFs + options. **Level 3 multi-leg options** (spreads, straddles, strangles, iron condors/butterflies) are live in **both paper and live** as of 2025; covered calls and cash-secured puts supported with collateral checks; index options were noted as "coming soon." Simple API key/secret auth, async/WebSocket support, paper environment mirrors production, up to 1,000 API calls/min for data. **Best starting point** given your async orchestrator. Note multi-leg constraint: from day zero of Level 3, an MLeg order is accepted only if all legs are covered within the same order (no uncovered short legs), which complicates rolling a short contract or calendar spread.
- **Interactive Brokers** via **ib_async** (the maintained fork of ib_insync; original author Ewald de Wit passed away early 2024, project continued by Matt Stancliff as `ib_async`, release line 2.x as of late 2025): broadest instrument/market coverage, 100+ order types, but requires a running TWS/IB Gateway desktop session (port 7497 paper / 7496 live) — a common stumbling block. Best for breadth and non-US assets. **Migrate any ib_insync code to ib_async** (it's a near drop-in and fixes order-stack desync bugs — e.g., warning/error code handling that previously left the order stack out of sync with IB).
- **Tradier**: RESTful, OAuth 2.0, equities + options focused; good for US options apps; separate sandbox and production endpoints.

**Execution & operational concerns:**
- **Paper-trade first, always** — sandbox fidelity is high (commonly cited at ~98–99%) but not perfect; it surfaces bugs in order logic, state, and reconciliation before real capital.
- **Order types.** Prefer limit orders (and marketable limits) over market orders to control slippage; use bracket/OCO for stops + targets; multi-leg orders for options so legs fill together (avoids leg risk/partial fills).
- **State persistence & reconciliation.** Persist intended positions/orders to a database; on every cycle reconcile broker truth vs your internal state (orders can fill, partially fill, or be rejected at the broker while your state drifts — a documented ib_insync/IB failure mode). Use idempotent order submission with client order IDs.
- **Risk controls / kill switches.** Implement: max position size, max portfolio heat (you have the 6% halt — wire it to *block new orders*), per-day loss limit, max order rate, and a global kill switch (extend `macro_engine.py`'s RISK-OFF to also flatten/halt execution). Add connectivity/heartbeat monitoring with alerting for crashes, disconnects, or anomalous (errant/duplicate) orders.

### Part 4 — Position Sizing & Risk Management

- **Fractional Kelly with *estimated* inputs.** Your kernel currently sizes from a 0–100 score — replace that with Kelly computed from *estimated* win rate p and payoff ratio b: f* = (p·b − (1−p))/b. Then use **half- or quarter-Kelly**: per MacLean, Ziemba & Blazenko (1992, *Management Science*), half-Kelly delivers ~75% of full-Kelly's growth rate with ~50% of the volatility (Ed Thorp: "if you bet half the Kelly amount, you get about three-quarters of the return with half the volatility"). Fractional Kelly is the mathematically appropriate response to estimation error — full Kelly on an overestimated edge can drive long-run growth negative. Estimate p and b from a statistically adequate trade sample: industry guidance sets "a minimum of 50 closed trades… A sample of 100 or more trades is preferable. Using Kelly with fewer than 50 data points is risky" (Altrady). Cap any single Kelly output at ~20%. The meta-labeling P(correct) from #11 is the principled source for p.
- **Volatility targeting** (see #8): scale exposure so portfolio vol ≈ target; de-lever when GARCH/EWMA vol rises. Single highest-value risk add.
- **Risk parity** for multi-strategy capital allocation: weight strategies by inverse vol / ERC so no one strategy dominates risk.
- **Portfolio heat / max open risk** (you have 6%): keep it, and make it correlation-aware — Kelly applied per-position ignores shared risk; apply Kelly to *total correlated exposure*, and reduce size when open positions are correlated (e.g., all long-equity beta).
- **Correlation-aware sizing.** Before adding a position, compute its correlation to the existing book; haircut size for high correlation. This prevents the classic failure of stacking individually-attractive but jointly-redundant bets.

---

## Recommendations (staged)

**Stage 0 — Validation foundation (do this first, before any new strategy).**
Implement purged k-fold + embargo, a CPCV path generator, and a deflated-Sharpe/PBO calculator (hand-rolled or via mlfinpy/skfolio). Add realistic cost/slippage to your Backtrader path. Document the survivorship-bias limitation of yfinance and, if feasible, source a point-in-time universe. *Benchmark to change course:* if a candidate strategy's DSR implies <95% probability its Sharpe > 0, or PBO > ~50%, do not deploy it.

**Stage 1 — Robust classics (fast wins).**
Add time-series momentum (#1), dual momentum (#3), and RSI-2/Bollinger mean reversion (#4) — all reuse existing `processing_engine.py` primitives and emit sub-scores into the kernel. Add volatility targeting (#8) as a sizing overlay using your existing GARCH vol. These are low-complexity, well-understood, and individually validatable.

**Stage 2 — Cross-sectional & portfolio layer.**
Add the universe-ranking pass for cross-sectional momentum (#2) and multi-factor scores (#7); add HMM regime gating (#9) as a second opinion to your macro engine; formalize risk parity (#8) for multi-strategy allocation. Replace the 0–100→Kelly mapping with estimated-p fractional Kelly (Part 4).

**Stage 3 — Options VRP & stat-arb.**
Harden the options-selling matrix (#6) with a faithful IVR and VRP-regime gate in `technical_options_engine.py`; add cointegration pairs (#5) with Kalman hedge ratios. Validate options strategies on tail scenarios (October 2008, February 2018, March 2020), not just average outcomes.

**Stage 4 — ML, done correctly.**
Build the LightGBM cross-sectional ranker (#10) and meta-labeling/triple-barrier layer (#11) under strict purged-CV discipline; use qlib as the reference pipeline. Treat ML outputs as ensemble inputs, never as overrides of economically-grounded signals.

**Stage 5 — Execution.**
Paper-trade the full stack on Alpaca (`alpaca-py`) for ≥3 months; build state persistence, reconciliation, and kill switches; only then consider live capital, starting at minimal size. Add IB via ib_async if you need breadth. *Benchmark to go live:* paper results within tolerance of backtest expectations *after costs*, and all risk controls verified to actually halt trading.

---

## Caveats
- **This is educational/informational content about strategy design and software architecture, not personalized financial advice.** Trading involves substantial risk of loss; options selling has uncapped or large tail risk.
- **Backtested and hypothetical results do not guarantee future performance**, and published anomalies decay after discovery (momentum crowding, RSI-2 HFT erosion, short-vol blowups).
- **Source-quality flags:** several strategy "win rate" and return figures (iron-condor POPs, dual-momentum CAGRs, RSI-2 win rates) come from practitioner blogs (QuantifiedStrategies, tastytrade-derived stats, ApexVol, Quant Investing) and should be treated as illustrative, not peer-reviewed; the academic anchors (Moskowitz/Ooi/Pedersen, Daniel & Moskowitz, Fama-French/Hou-Xue-Zhang, Bailey & Lopez de Prado, Jegadeesh & Titman, Elton/Gruber/Blake) are stronger. Exact momentum-crash magnitudes vary by window/methodology across sources (e.g., July 1932 single-month WML ≈ −74%, two-month Jul–Aug 1932 ≈ −88%, three-month Jun–Aug 1932 ≈ −91%).
- **VRP magnitude** estimates cluster around 3.8–4.2 vol points but vary by sample window and whether measured as VIX-minus-realized or variance-based; the post-2020 average has been higher (~6.5 points per CAIA).
- **Tooling is in flux:** mlfinlab is now commercial; ib_insync is unmaintained (use ib_async); zipline requires the maintained zipline-reloaded fork; vectorbt's actively-developed tier is the paid VectorBT PRO. Verify current API capabilities and library versions before building.
- **Coverage note:** the primary web-search budget was exhausted before three planned confirmatory searches (momentum-crash magnitudes, precise VRP figure, survivorship/cost best practices); those datapoints were sourced via a dedicated research subagent and an enrichment pass, and are attributed to their named sources above.