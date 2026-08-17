# Giant Master Plan Audit — Was Phases 1–30 Actually Built Correctly?

**Date:** 2026-08-17
**Scope:** `.claude/giant_master_plan.md` (all 30 phases), cross-referenced against `pr_755_body.md`,
`pr_bodyTD.md`, `pr_753_body.md`, `tdtask.md`, and the actual state of `origin/main`.
**Method:** PR/merge history review, full commit timeline review, direct file reads on the
highest-risk modules, six parallel Explore-agent deep-dives (one per phase tier + two cross-cutting
checks), and independent re-runs of the full pytest/vitest/tsc suites. No claim below is a restatement
of the original PR bodies — every finding traces to a specific file:line, test run, or command output
gathered during this audit.

## Bottom line

**The code is real.** All 37 backend modules and ~15 webapp components across all 30 phases exist,
are substantively implemented (genuine Black-Scholes/HRP/Almgren-Chriss/Corsi-HAR-RV/Kalman-filter/
copula math — not stubs), and are wired into `api/pilots_api.py`. This was not a "PR body describes
work that was never done" situation.

**But "built" and "correctly built" are not the same claim, and several load-bearing pieces of this
build-out do not do what they claim to do once you look past the file existing:**

1. A **systemic mock/live API parity failure** across nearly every Phase 20–24 (and part of 28/30)
   webapp screen — wrong URLs, mismatched field names, and invented fields that will crash or 404 the
   moment the frontend points at the real backend instead of its own mock. The "1,721 tests passed, 0
   failed" vitest number is real, but every one of the affected test files mocks `api.<method>()`
   directly, so the test suite has never once touched the real contract it claims to validate.
2. The **live HRP/CVaR backend endpoint hardcodes a fabricated `cvar_95` value** (`# placeholder` in
   the code itself) instead of computing it — a genuine violation of this repo's own CONSTRAINT #4, in
   shipped backend code, not a test artifact.
3. The **Stage 4 ML meta-labeler (Phase 5) is a permanent no-op in the live auto-execution path** —
   the model is never loaded before scoring, so it silently always approves at full size — and its
   retrain endpoint trains on fabricated, near-constant features.
4. **Five live, user-executable options-selling pilot modules never went through this platform's own
   mandatory deployability gate** (PBO/DSR/Sharpe/MaxDD + tail-stress), despite submitting real paper
   trades.
5. Phase 16's **mandatory 15:45 ET 0DTE liquidation safety gate is coded and tested but never wired
   into any live/scheduled path** — the webapp UI displays it as active regardless.
6. Two headline phases were **mislabeled relative to their own spec**: Phase 22 claims "Deep RL / PPO"
   but ships a closed-form quoter (the RL bit is an orphaned heuristic, never called); Phase 29 claims
   "WebGL / Three.js" but ships Canvas 2D with hand-rolled 3D projection math.
7. **Documentation was not kept in sync as work landed.** `.claude/giant_master_plan.md` has been
   edited by exactly one commit since 2026-08-14 despite ~50 subsequent commits building out
   everything it still lists as future work; `CLAUDE.md`/`AGENTS.md` had zero mentions of any of the
   ~13 phase 19–30 modules. (Closed as part of this audit — see §7.)

None of this means the build-out was faked. It means a large amount of genuinely good engineering work
shipped without the closing 10% — the parity check, the wiring verification, the "does the safety gate
actually run in production" check — that turns "the code exists and compiles" into "the feature works."

---

## 1. Test-suite reconciliation

| Suite | Claimed (PR #753/#755) | Actual (this audit, re-run 2026-08-17) | Verdict |
|---|---|---|---|
| pytest (`-m "not network" -q -n 4`) | 11,147 passed, 0 failed, 32 skipped, 163s | **11,261 passed, 5 failed, 13 skipped, 199s** | See below |
| vitest | 160 files, 1,708 passed | **161 files, 1,721 passed, 0 failed**, 44s | Clean; growth explained by later commits (PR #757 etc.) |
| `tsc --noEmit` | 0 errors | **0 errors** | Confirmed exactly |

Of the 5 pytest failures found on first run:
- **4 were stale-artifact drift, not code bugs**: `docs/settings_liveness.json` and
  `docs/settings_field_census.json`/`.md` had fallen out of sync with `settings.py` (413 vs 415 files
  scanned) — a known, recurring maintenance chore this repo has hit and fixed repeatedly during this
  exact build-out (`ca57c756`, `94713ede`, `53def9d0`, `c753bc14` are all prior "regenerate stale
  settings census" commits). **Fixed in this audit** by re-running `scripts/settings_liveness.py
  --write` and `scripts/measure_settings_census.py --write`; all 4 tests now pass (diff: 782+104
  lines of accumulated drift removed). Committed alongside this report.
- **1 is a real, currently-failing test unrelated to phases 1–30**:
  `tests/test_forecast_backfill.py::test_step_3_skips_cross_sectional_modules_with_no_meta_label_features`
  — `assert call_log == ["cross_sectional_momentum"]` but the actual call log is
  `["cross_sectional_momentum", "sector_quality_rank", ...options_flow_sentiment...]`. A newer
  cross-sectional module (`sector_quality_rank`) now participates in the same gating path this test
  pins, and the test wasn't updated. **Not fixed here** — closing it may require touching
  `pipeline/production_steps.py` or the forecast-backfill engine, which needs a branch + PR per this
  repo's workflow rules, not a docs-only audit pass.

## 2. Findings, ranked by severity

### 🔴 Critical

**F1 — Systemic mock/live API parity failure, Phase 20–24 (+ parts of 28/30).** Confirmed by a
dedicated parity agent building a field-by-field matrix against the real Pydantic/FastAPI responses:

| Endpoint | Screen | Failure mode |
|---|---|---|
| `/pilots/options/gex/profile` | `GexProfileView.tsx` | Field mismatch (`net_gex` vs `net_gex_dollars`, `call_wall_strike` vs `call_gamma_wall`, ...) — screen calls `.call_gamma_wall.toFixed(2)` on a field the API never sends. **Throws live.** |
| `/pilots/options/lob/simulate-queue` | `LobDepthView.tsx` | Shape mismatch (`bids`/`asks` vs real `price_level`/`depth_ahead`/`fill_probability`). **Crashes live.** |
| `/pilots/options/copula/pairs` | `CopulaSpreadView.tsx` | Field mismatch (`asset_x/asset_y` vs real `symbol_y/symbol_x`, `kalman_beta` vs `current_beta`). Reads wrong fields live. |
| `/pilots/options/market-maker/simulate` | `MarketMakerAgentView.tsx` | Type/mock invent `max_drawdown`/`fill_rate`/`avg_spread`/`steps` — **none exist in the real response.** All undefined live. |
| transformer-forecast | `TransformerVolForecastView.tsx` | `client.ts` calls `/data/ai/transformer-vol/{symbol}` — **route does not exist.** Real route is `/pilots/options/ai/transformer-forecast`. 404 live, plus shape mismatch even if fixed. |
| diffusion-stress-test | `GenerativeDiffusionStressView.tsx` | `client.ts` calls `/data/ai/diffusion-stress` — **route does not exist.** Real route needs a required `spot_price` the request body never sends. 404 or 422 live. |
| `/pilots/execution/brokers/status`, `.../failover` | `MultiBrokerGatewayView.tsx` | `client.ts` calls `.../multi-broker/status`/`.../multi-broker/failover` — **wrong URL prefix**, real routes are `.../brokers/status`/`.../brokers/failover`. 404 live. |
| `/pilots/ai/research/synthesize` | `ResearchCopilotView.tsx` | Near-zero field overlap between mock/type and real response (`synthesis_id`/`ast_safety_passed`/... vs real `success`/`code`/`validation_passed`/...). Broken live. |
| multi-leg pricing (`/price`, `/validate`) | — | **Zero frontend wiring at all** — not attempted, not mocked, not typed. |
| FIX venues (`GET .../fix/venues`) | — | Zero frontend wiring. |
| FIX route (`POST .../fix/route`) | — | Shape matches, but **no component calls it and no test exercises it** — dead from the frontend side. |

Root cause, confirmed: every affected `*.test.tsx` mocks `api.<method>()` directly via `vi.fn()`,
so the vitest suite exercises the mock's own (wrong) shape and never once contacts a schema derived
from the real backend. Nothing in CI currently catches any of the breaks above. Two endpoints are
**fully clean**: `/pilots/ai/backtest/autonomous` and `/pilots/execution/sec-606/report`.

**F2 — Live HRP/CVaR backend hardcodes a fabricated result.** `api/pilots_api.py:6667` returns
`"cvar_95": float(0.05), # placeholder` instead of the value `constrain_cvar()` actually computed — a
CONSTRAINT #4 violation ("never fabricate a number") in shipped, non-mock backend code. The mock
compounds this by also hardcoding `expected_return: 0.12, cvar_95: 0.08, sharpe_ratio: 1.5` regardless
of input. The Almgren-Chriss mock has the same static-value pattern (`expected_shortfall: 15.42,
variance: 2.15, half_life: 3.4` for every request) but its live backend is genuinely computed —
only the mock is dishonest there.

**F3 — Phase 5's ML meta-labeler is a permanent no-op in the live auto-execution path.**
`execution/options_paper_executor.py` never calls `global_options_meta_labeler.load_model()` before
scoring directives — only the `/status` endpoint's path does. In a fresh daemon process, `self.model`
stays `None`, so `predict_probability()` always returns the hardcoded fallback `0.65`, and
`get_sizing_multiplier(0.65)` always evaluates to exactly `1.0` (the `edge=0.15` term exactly cancels
the offset) — the "Stage 4 ML gate" silently becomes an always-approve-at-full-size no-op. Separately:
`scripts/train_meta_labelers.py` (listed against Phase 5 in the plan) trains a *completely unrelated*
system (AFML momentum meta-labelers); the actual options-model retrain endpoint
(`api/pilots_api.py:5968-5979`) feeds every training sample the same hardcoded
`ivr=50.0, vrp=0.02, vix=20.0, credit_to_width_ratio=0.30, short_delta=0.30` regardless of real entry
conditions — training on ~3 near-identical feature vectors, not real regime-conditioned data.

**F4 — Five live, executable options-selling pilots bypass the mandatory deployability gate
entirely.** `pilots/earnings_crush.py`, `pilots/vol_mispricing.py`, `pilots/dispersion_trading.py`,
`pilots/zero_dte_engine.py`, and `pilots/gamma_scalper.py` are none of them in `STRATEGY_REGISTRY`,
have no `docs/VALIDATION_STRATEGY_FIX_LOG.md` entry, no `docs/signals/<name>.md`, and get no tail-shock
stress-test coverage from `validation/stress_scenarios.py` — yet all five are wired to real
`POST .../execute` endpoints in `api/pilots_api.py` that submit trades into `PaperAccountStore`. The
harness (`validation/options_selling_backtest.py`) exists, works, and IS invoked — but only by 6
generic SPY-only archetype proxies in `scripts/refresh_validations.py`, never by the actual named
pilot modules the platform lets an operator execute. `pilots/copula_stat_arb.py` (Phase 21) is
similarly unregistered/unvalidated. One item is fully compliant and worth naming as the model to
replicate: `signals/options_flow_sentiment.py` is registered, gated, and *honestly* failed
(DSR 0.906 < 0.95, documented in both the fix log and `docs/signals/options_flow_sentiment.md`).

### 🟠 High

**F5 — Phase 16's mandatory 15:45 ET liquidation gate is orphaned.** `evaluate_0dte_exits`/
`execute_0dte_exits` in `pilots/zero_dte_engine.py` correctly implement the hard-stop priority logic
and are unit-tested with exact `pytest.approx` P&L checks — but are **never called from
`api/pilots_api.py`, any daemon, or the webapp**. Only entry (`get_0dte_signals`,
`execute_0dte_trade`) is wired; the exit/safety path is dead code relative to the running system.
Meanwhile `webapp/src/components/options/ZeroDteDesk.tsx` displays "15:45 ET Auto-Close" as if it's
live and automatic.

**F6 — Phase 22's "Deep RL (PPO) Market Maker" claim is false.** The genuine, well-tested part —
Avellaneda-Stoikov closed-form quoting (reservation price, optimal spread, Poisson fill intensities) —
matches the spec exactly. But `train_market_maker_policy`'s own docstring calls it a "lightweight
heuristic and policy optimizer"; the body is a 2-parameter (γ, κ) stochastic hill-climb, not a neural
network, not policy-gradient/PPO. Worse: that function is **never imported or called by
`api/pilots_api.py` or any webapp component** — the only reachable live path is the deterministic
closed-form quoter. `MarketMakerAgentView.tsx` is not showing a trained RL agent.

**F7 — Phases 23/24 forecasters: confirmed missing lookahead tests, plus live endpoints feed them
random noise.** `test_transformer_vol_forecaster.py` (40L) and `test_synthetic_diffusion_engine.py`
(55L) have zero lookahead/causal-perturbation coverage, violating CLAUDE.md's explicit rule for "every
indicator and forecaster." Both underlying engines are genuine (real multi-head attention + GLU gating
trained via ridge regression; real OU-diffusion forward process + Euler-Maruyama reverse SDE) — but
`api/pilots_api.py` feeds both `np.random.randn(...)` synthetic tensors as "market history" instead of
real data. Even a passing test suite on the isolated function wouldn't validate what the live endpoint
actually returns to a user today.

### 🟡 Medium

**F8 — `execution/fix_recovery.py` is dead code.** Confirmed independently by two separate agents:
the module implements correct FIX gap-fill sequence-reset semantics and has its own passing test file,
but `execution/fix_gateway.py` never imports it — the gateway reimplements its own separate (also
correct) resend/gap-fill logic inline instead. `fix_recovery.py` is only ever exercised by its own
isolated test.

**F9 — Phase 29's "WebGL 3D" claim is a rendering-technology mismatch.** `VolSurface3D.tsx`/
`LobDepth3D.tsx` use plain Canvas 2D with genuinely-implemented hand-rolled 3D rotation/perspective
math — no `three` package dependency, zero WebGL draw calls anywhere in either file. `hasWebGL` only
toggles a cosmetic "WebGL 3D Active" vs "Canvas 3D Fallback Mode" label; both modes render identically
through the same 2D canvas path. The interactive 3D functionality itself works; it's just not on the
claimed technology, and the UI label actively misrepresents which mode is active.

**F10 — AST import-boundary guard covers 3 of 18 new `pilots/*.py` modules.** The real per-file guard
(`tests/test_pilots_strategy_matrix.py`) is a hardcoded allowlist, not an auto-discovered glob over
`pilots/*.py`. Only `lob_simulator`, `options_gex`, and `copula_stat_arb` are parametrized; the other
14 phase-19-30 pilots modules (`multi_leg_pricing`, `earnings_crush`, `har_volatility`,
`vol_mispricing`, `gamma_scalper`, `options_alerts`, `dispersion_trading`, `zero_dte_engine`,
`options_vpin`, `options_sor`, `paper_broker_options_order`, `options_risk`, `options_hedging`,
`volatility_surface`) are never checked by any guard. Manual verification (this audit) confirms none
of the 14 currently import a forbidden heavy engine — but that's true by inspection today, not by
enforced test coverage; a future regression in any of these 14 files would go undetected.

**F11 — Phase 3/7 "β-SPY" delta hedging assumes β=1 for every underlying.**
`beta_weighted_delta_spy` is computed as `net_dollar_delta / spy_spot`, not using the real per-symbol
regression beta that already exists elsewhere in the codebase (`data/fmp_fundamentals.py:compute_beta`,
`pilots/rolling_beta.py`). Not fabricated — an honest simplification — but the phase name overclaims
relative to what's implemented.

**F12 — `ml/drl_market_maker.py` has no documented gate exemption.** PBO/DSR doesn't map cleanly onto
an RL-shaped policy, which is a legitimate reason to not apply the standard gate as-is — but no
documented reasoned alternative or exemption exists anywhere in `docs/`, `CLAUDE.md`, or `.claude/`.
The gap is the missing writeup, not the absence of the standard gate.

### 🟢 Low / informational

- **F13**: `pilots/multi_leg_pricing.py` is real, well-tested, and wired — but does not correspond to
  any phase number in `.claude/giant_master_plan.md`. Its introducing PR bundled it under a
  "Phase 25-27" title alongside unrelated HRP/Almgren/FIX work.
- **F14**: Phase 13's "5-factor Greek PnL attribution" (per the informal planning transcript) overstates
  what shipped — the actual, internally-documented scope is a 3–4 term attribution (gamma rent, theta
  decay, transaction costs, net edge), with no delta/vega/rho P&L terms. The repo's own walkthrough doc
  correctly describes the smaller scope; the mismatch is between two planning artifacts, not a code gap.
- **F15**: `tests/test_options_risk.py` (Phase 3) has zero `pytest.approx`/hand-computed-reference
  checks for the core Black-Scholes Greeks — only loose range bounds — unlike the much stronger pattern
  in `tests/test_volatility_surface.py` (put-call parity + IV round-trip to 1e-4).
- **F16**: `tests/test_dispersion_trading.py` (Phase 15) only checks the Driessen-Maenhout-Vilkov
  implied-correlation formula at the trivial ρ=1.0 boundary; no non-trivial reference value is verified.
- **F17**: Phase 18's routing (`pilots/options_sor.py`) does not model named exchanges
  (CBOE/MIAX/BOX/PHLX) — that detail belongs to Phase 27's `fix_gateway.py`. `options_sor.py`'s scope
  is genuinely abstract "venue books," narrower than how it was informally described going into this
  audit.
- **F18**: Phase 9's actual deliverables are `pilots/scenario_matrix.py` + `ScenarioHeatmap.tsx`
  (wired into `PaperBroker.tsx`), not solely `volatility_surface.py`/`OptionsChain.tsx` as the plan
  doc's module list implies — confirmed present and real, but not individually deep-audited in this
  pass; worth a dedicated follow-up check.
- **F19**: `signals/aggregator.py` defaults meta-labeler confidence to `1.0` on failure (a documented,
  intentional fail-open per CONSTRAINT #6) — not concerning alone, but combined with F3 (the ML gate
  rarely loads in the first place) means the options desk's ML risk-gating may in practice be inactive
  far more often than the "Stage 4 ML Meta-Labeler" branding implies.

## 3. What's genuinely solid

Phases 1, 2, 4, 6, 8, 10, 11, 12, 14, 17, 18 (core), 19, 20 (backend), 21 (backend), 25, 26 (backend),
27 (`fix_gateway.py` itself), 28 (backend), 30 (backend) are real, substantively correct, honestly
degrade-on-failure (no fabricated zeros found outside F2), and backed by meaningful tests — several
(Phase 8's put-call-parity + IV round-trip, Phase 19's exact Poisson-tail closed forms, Phase 21's
lookahead-perturbation test, Phase 26's re-derived κ formula, Phase 30's exact NBBO price-improvement
checks) are genuinely strong, reference-value-checked test suites. `llm/research_copilot.py`'s
AST-safety sandbox has real adversarial tests (dunder-access rejection, forbidden-import rejection,
forbidden-call rejection) — independently spot-checked in this audit, not just inspected. PR #755's
core solver files were confirmed **byte-identical** to current `main`, so its "superseded by #753"
resolution holds exactly, not just by the repo owner's word.

## 4. Phase-by-phase status

| Phase | Name | Status | Key evidence |
|---|---|---|---|
| 1 | Multi-leg paper trading primitives | Fully Built | — |
| 2 | Auto-execution & dedup | Fully Built | F19 (minor) |
| 3 | Portfolio risk greeks | Built with caveats | F11, F15 |
| 4 | Validation harness & stress shocks | Fully Built | — |
| 5 | Stage 4 ML meta-labeler | **Partially Built** | F3 (critical) |
| 6 | Expiration cash settlement | Fully Built | — |
| 7 | Position lifecycle & SPY hedging | Built with caveats | F11 |
| 8 | PCHIP vol surface | Fully Built | — |
| 9 | 2D/3D scenario matrix | Fully Built | F18 (follow-up recommended) |
| 10 | Earnings vol crush scanner | Fully Built | minor proxy-pricing note |
| 11 | UOA & flow sentiment | Fully Built | model example for gate compliance |
| 12 | Corsi HAR-RV & mispricing | Fully Built | — |
| 13 | Gamma scalping & attribution | Built with caveats | F14 |
| 14 | Webhook alerts | Fully Built | — |
| 15 | Dispersion & correlation arb | Built with caveats | F4, F16 |
| 16 | 0DTE momentum & TTM squeeze | **Partially Built** | F5 (high) |
| 17 | Options VPIN | Fully Built | — |
| 18 | Smart order router | Built with caveats | F17 |
| 19 | LOB queue simulator | Fully Built (backend); Partially Built (frontend) | F1 |
| 20 | Options GEX | Fully Built (backend); Partially Built (frontend) | F1 |
| 21 | Copula stat arb | Fully Built (backend, ungated); Partially Built (frontend) | F1, F4 |
| 22 | "Deep RL" market maker | **Partially Built** | F6 (high), F1, F12 |
| 23 | Transformer vol forecaster | **Partially Built** | F7 (high), F1 |
| 24 | Generative diffusion stress | **Partially Built** | F7 (high), F1 |
| 25 | HRP/CVaR optimizer | Built with caveats (backend real, mock dishonest) | F2 (critical) |
| 26 | Almgren-Chriss execution | Fully Built (backend); mock dishonest | F2 |
| 27 | Cross-exchange FIX engine | Built with caveats | F8 |
| 28 | LLM research copilot & CPCV backtester | Fully Built (backend); Partially Built (one endpoint's frontend) | F1 |
| 29 | WebGL 3D vol surface & LOB | Built with caveats (tech mismatch) | F9 |
| 30 | Multi-broker gateway & SEC 606 | Fully Built (backend); Partially Built (frontend URLs) | F1 |
| — | `multi_leg_pricing.py` (unnumbered) | Fully Built | F13 (numbering only) |

## 5. Documentation fixes applied in this audit pass (docs-only, direct to `main`)

- `CLAUDE.md`/`AGENTS.md`: added changelog bullets for the ~13 phase 19–30 modules that had none.
- `.claude/giant_master_plan.md`: refreshed the "Next Quantitative Horizons" roadmap/gantt section to
  reflect phases 19–30 as actually complete (with real PR references), replacing the stale future-dated
  timeline.
- `docs/architecture/execution.md`: added the missing `fix_recovery.py` bullet (noting F8's dead-code
  finding).
- `docs/architecture/signal-engines.md` / `simulation-eval-reporting.md` / `validation-and-signals.md`:
  added entries for `hrp_cvar_optimizer.py`, `copula_stat_arb.py`, `drl_market_maker.py`,
  `transformer_vol_forecaster.py`, `synthetic_diffusion_engine.py`, `lob_simulator.py`,
  `options_gex.py`, `multi_leg_pricing.py` per each doc's existing scope.
- `docs/settings_liveness.json`, `docs/settings_field_census.json`/`.md`: regenerated (see §1).

**Not fixed in this pass** (would require source/engine changes, needs a branch + PR per this repo's
workflow rules): F1–F12 above. This audit's job was to find and document them precisely enough that a
follow-up task can act on each one without re-deriving the evidence.
