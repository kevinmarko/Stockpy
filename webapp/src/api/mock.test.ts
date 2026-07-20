/**
 * mock.test.ts — offline, deterministic contract tests for the mock API layer.
 *
 * These pin two things:
 *  1. Shape parity — every object the mock returns carries the fields its
 *     `types.ts` interface requires, so `VITE_USE_MOCK=false` (the live client,
 *     which is shape-identical) can't silently diverge from what screens read.
 *  2. Honesty (CONSTRAINT #4 / webapp/README.md) — the two deliberately-honest
 *     fixtures stay honest: `momentum-burst` reports `deployable=false`, and
 *     `value-quality` reports `curve: null` with a `reason`. We assert the
 *     honest states; we never loosen them.
 *
 * Mock layer only — no network, no live API.
 */

import { afterEach, beforeEach, describe, it, expect } from "vitest";
import { mockApi } from "./mock";
import { ApiError } from "./types";
import type {
  CurvePoint,
  Headline,
  Holding,
  PerfRange,
  PilotSummary,
  PortfolioPositionView,
} from "./types";

const CATEGORIES = [
  "Momentum",
  "Mean Reversion",
  "Factor",
  "Blend",
  "Macro",
  "Risk",
  "Sentiment",
  "Forecast",
];

const RANGES: PerfRange[] = ["1W", "1M", "3M", "6M", "1Y", "2Y"];

function expectHeadline(hd: Headline) {
  // Numeric-or-null metrics — honesty means null, never a fabricated 0.
  for (const k of ["sharpe", "dsr", "pbo", "max_drawdown"] as const) {
    expect(hd, `headline.${k}`).toHaveProperty(k);
    expect(hd[k] === null || typeof hd[k] === "number").toBe(true);
  }
  expect(typeof hd.deployable).toBe("boolean");
}

function expectCurvePoint(pt: CurvePoint) {
  expect(typeof pt.date).toBe("string");
  expect(typeof pt.value).toBe("number");
  expect(Number.isFinite(pt.value)).toBe(true);
}

function expectHolding(h: Holding) {
  expect(typeof h.symbol).toBe("string");
  expect(typeof h.name).toBe("string");
  expect(typeof h.sector).toBe("string");
  expect(typeof h.weight).toBe("number");
  expect(typeof h.score).toBe("number");
  expect(h.price === null || typeof h.price === "number").toBe(true);
}

describe("mock API — /pilots list contract", () => {
  it("every PilotSummary carries all PilotSummary fields", async () => {
    const pilots = await mockApi.listPilots();
    expect(pilots.length).toBeGreaterThan(0);
    for (const p of pilots as PilotSummary[]) {
      expect(typeof p.id).toBe("string");
      expect(typeof p.name).toBe("string");
      expect(CATEGORIES).toContain(p.category);
      expect(typeof p.description).toBe("string");
      expect(typeof p.holdings_count).toBe("number");
      expect(typeof p.aum_proxy).toBe("number");
      expect(typeof p.followers_proxy).toBe("number");
      expect(typeof p.long_only).toBe("boolean");
      expectHeadline(p.headline);
    }
  });
});

describe("mock API — /pilots/{id} detail contract", () => {
  it("detail extends the summary and adds holdings/sector/trades/as_of", async () => {
    const detail = await mockApi.getPilot("trend-following");
    // Inherited PilotSummary fields (PilotDetail extends PilotSummary).
    expect(detail.id).toBe("trend-following");
    expect(typeof detail.holdings_count).toBe("number");
    expect(typeof detail.aum_proxy).toBe("number");
    expect(typeof detail.followers_proxy).toBe("number");
    expect(typeof detail.long_only).toBe("boolean");
    expectHeadline(detail.headline);
    // Detail-only fields.
    expect(Array.isArray(detail.holdings)).toBe(true);
    expect(detail.holdings.length).toBeGreaterThan(0);
    detail.holdings.forEach(expectHolding);
    expect(Array.isArray(detail.sector_allocation)).toBe(true);
    for (const s of detail.sector_allocation) {
      expect(typeof s.sector).toBe("string");
      expect(typeof s.weight).toBe("number");
    }
    expect(Array.isArray(detail.recent_trades)).toBe(true);
    for (const t of detail.recent_trades) {
      expect(["ENTER", "EXIT", "REWEIGHT"]).toContain(t.side);
      expect(typeof t.weight_delta).toBe("number");
    }
    expect(detail.as_of === null || typeof detail.as_of === "string").toBe(true);
  });

  it("holdings_count matches the actual holdings array length", async () => {
    const detail = await mockApi.getPilot("multifactor");
    expect(detail.holdings_count).toBe(detail.holdings.length);
  });

  it("throws an ApiError(404) for an unknown pilot id", async () => {
    await expect(mockApi.getPilot("does-not-exist")).rejects.toBeInstanceOf(
      ApiError
    );
    await expect(mockApi.getPilot("does-not-exist")).rejects.toMatchObject({
      status: 404,
    });
  });
});

describe("mock API — /pilots/{id}/performance contract", () => {
  it("a deployable pilot returns range/metrics/curve/benchmark", async () => {
    const perf = await mockApi.getPerformance("trend-following", "1M");
    expect(perf.range).toBe("1M");
    // trend-following is a deployable mock pilot with a real curve, so
    // metrics is genuinely non-null here (see the honesty-fixtures block
    // below for the null-metrics case).
    expectHeadline(perf.metrics!);
    expect(Array.isArray(perf.curve)).toBe(true);
    perf.curve!.forEach(expectCurvePoint);
    // benchmark is present (either a curve or null) — the field must exist.
    expect(
      perf.benchmark === null || Array.isArray(perf.benchmark)
    ).toBe(true);
    // macro_benchmark (the SEPARATE SPY overlay) field must exist too; for a
    // multi-name pilot it's a real distinct curve.
    expect(
      perf.macro_benchmark === null || Array.isArray(perf.macro_benchmark)
    ).toBe(true);
    expect(Array.isArray(perf.macro_benchmark)).toBe(true);
    perf.macro_benchmark!.forEach(expectCurvePoint);
  });

  it("macro_benchmark is a SEPARATE series from benchmark (not an alias)", async () => {
    const perf = await mockApi.getPerformance("trend-following", "1Y");
    expect(Array.isArray(perf.benchmark)).toBe(true);
    expect(Array.isArray(perf.macro_benchmark)).toBe(true);
    // Distinct object identity and at least one differing value.
    expect(perf.macro_benchmark).not.toBe(perf.benchmark);
    const b = perf.benchmark!;
    const m = perf.macro_benchmark!;
    const anyDifferent = m.some(
      (pt, i) => b[i] === undefined || pt.value !== b[i].value
    );
    expect(anyDifferent).toBe(true);
  });

  it("macro_benchmark is null when the underlying already IS SPY (redundant)", async () => {
    // macd-trend models the honest redundancy case — no fabricated SPY overlay.
    const perf = await mockApi.getPerformance("macd-trend", "1Y");
    expect(Array.isArray(perf.curve)).toBe(true);
    expect(perf.macro_benchmark).toBeNull();
  });

  it("returns the requested range for every PerfRange", async () => {
    for (const r of RANGES) {
      const perf = await mockApi.getPerformance("balanced-blend", r);
      expect(perf.range).toBe(r);
    }
  });
});

describe("mock API — honesty fixtures (must not be loosened)", () => {
  it("momentum-burst is NOT deployable and surfaces it honestly", async () => {
    const detail = await mockApi.getPilot("momentum-burst");
    expect(detail.headline.deployable).toBe(false);
    const perf = await mockApi.getPerformance("momentum-burst", "1Y");
    // momentum-burst has a real (failing) backtest, so metrics is non-null —
    // distinct from value-quality below, which has no backtest at all.
    expect(perf.metrics!.deployable).toBe(false);
  });

  it("value-quality has curve:null with a reason — never a fabricated line", async () => {
    const perf = await mockApi.getPerformance("value-quality", "1Y");
    expect(perf.curve).toBeNull();
    expect(perf.benchmark).toBeNull();
    expect(perf.macro_benchmark).toBeNull();
    expect(typeof perf.reason).toBe("string");
    expect(perf.reason!.length).toBeGreaterThan(0);
  });

  it("value-quality's headline reports null metrics, not fabricated zeros", async () => {
    const detail = await mockApi.getPilot("value-quality");
    expect(detail.headline.deployable).toBe(false);
    expect(detail.headline.sharpe).toBeNull();
    expect(detail.headline.dsr).toBeNull();
    expect(detail.headline.pbo).toBeNull();
    expect(detail.headline.max_drawdown).toBeNull();
  });
});

describe("mock API — /portfolio contract", () => {
  it("returns a Portfolio with source and PortfolioPositionView positions", async () => {
    const pf = await mockApi.getPortfolio();
    expect(typeof pf.total_equity).toBe("number");
    expect(typeof pf.buying_power).toBe("number");
    expect(typeof pf.total_unrealized_pl).toBe("number");
    expect(typeof pf.total_dividends).toBe("number");
    expect(typeof pf.position_count).toBe("number");
    expect(typeof pf.source).toBe("string");
    expect(
      pf.fetched_at === null || typeof pf.fetched_at === "string"
    ).toBe(true);
    expect(Array.isArray(pf.positions)).toBe(true);
    for (const p of pf.positions as PortfolioPositionView[]) {
      expect(typeof p.symbol).toBe("string");
      expect(typeof p.qty).toBe("number");
      expect(typeof p.avg_cost).toBe("number");
      expect(
        p.current_price === null || typeof p.current_price === "number"
      ).toBe(true);
      expect(
        p.market_value === null || typeof p.market_value === "number"
      ).toBe(true);
      expect(
        p.unrealized_pl === null || typeof p.unrealized_pl === "number"
      ).toBe(true);
    }
  });
});

describe("mock API — /portfolio/equity-curve contract", () => {
  it("returns { range, curve:[{date,value}] } — the client.ts envelope", async () => {
    const res = await mockApi.getEquityCurve("3M");
    expect(res.range).toBe("3M");
    expect(Array.isArray(res.curve)).toBe(true);
    expect(res.curve!.length).toBeGreaterThan(0);
    res.curve!.forEach(expectCurvePoint);
  });
});

describe("mock API — /symbols/{ticker} contract", () => {
  it("returns the grouped SymbolDetail shape for a known symbol", async () => {
    const d = await mockApi.getSymbol("NVDA");
    // Top-level keys mirror the backend §2 canonical contract.
    expect(Object.keys(d).sort()).toEqual(
      [
        "advisory",
        "as_of",
        "factors",
        "held_by_pilots",
        "identity",
        "ranges",
        "reason",
        "risk",
        "sizing",
        "symbol",
      ].sort()
    );
    expect(d.identity).toHaveProperty("sector");
    expect(d.identity).toHaveProperty("price");
    expect(d.advisory).toHaveProperty("score");
    expect(d.factors).toHaveProperty("score_components");
    expect(d.ranges).toHaveProperty("buy_range");
    expect(d.risk).toHaveProperty("hmm_risk_on");
    expect(d.sizing).toHaveProperty("kelly_target_pre_regime");
    expect(d.sizing).toHaveProperty("kelly_target_post_regime");
    expect(d.sizing).toHaveProperty("regime_multiplier");
    expect(d.sizing).toHaveProperty("meta_label_composite");
  });

  it("normalizes the ticker to uppercase", async () => {
    const d = await mockApi.getSymbol("nvda");
    expect(d.symbol).toBe("NVDA");
  });

  it("ranges are pre-formatted strings, not tuples", async () => {
    const d = await mockApi.getSymbol("NVDA");
    expect(typeof d.ranges.buy_range).toBe("string");
    expect(typeof d.ranges.sell_range).toBe("string");
    expect(d.ranges.buy_range).toContain("$");
  });

  it("held_by_pilots is a weight-desc array of {pilot_id, name, weight}", async () => {
    const d = await mockApi.getSymbol("NVDA");
    expect(Array.isArray(d.held_by_pilots)).toBe(true);
    expect(d.held_by_pilots.length).toBeGreaterThan(0);
    for (const hp of d.held_by_pilots) {
      expect(Object.keys(hp).sort()).toEqual(["name", "pilot_id", "weight"]);
      expect(typeof hp.pilot_id).toBe("string");
      expect(typeof hp.name).toBe("string");
      expect(typeof hp.weight).toBe("number");
    }
    const w = d.held_by_pilots.map((x) => x.weight);
    expect(w).toEqual([...w].sort((a, b) => b - a));
  });

  it("honesty (CONSTRAINT #4): unavailable factor/risk leaves are null, never 0", async () => {
    const d = await mockApi.getSymbol("NVDA");
    // The advisory snapshot writer genuinely lacks these — must be null.
    for (const k of ["value_z", "quality_z", "xsec_12_1m", "xsec_momentum_rank"] as const) {
      expect(d.factors[k]).toBeNull();
    }
    for (const k of ["news_sentiment", "realized_slippage", "mfe", "mae", "edge_ratio", "macro_status"] as const) {
      expect(d.risk[k]).toBeNull();
    }
  });

  it("throws an ApiError(404) for an unknown ticker", async () => {
    await expect(mockApi.getSymbol("ZZZZ")).rejects.toBeInstanceOf(ApiError);
    await expect(mockApi.getSymbol("ZZZZ")).rejects.toMatchObject({ status: 404 });
  });
});

describe("mock API — brokerage-connect contract", () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    localStorage.clear();
  });

  it("getBrokerageStatus() starts disconnected", async () => {
    const status = await mockApi.getBrokerageStatus();
    expect(status).toEqual({ connected: false, has_account_snapshot: false });
  });

  it("connectBrokerage() with all three fields succeeds and flips status", async () => {
    const result = await mockApi.connectBrokerage({
      username: "user@example.com",
      password: "hunter2",
      mfa_code: "123456",
    });
    expect(result).toEqual({ connected: true, verified: true, has_account_snapshot: false });

    const status = await mockApi.getBrokerageStatus();
    expect(status.connected).toBe(true);
  });

  it("connectBrokerage() never echoes the submitted credential values", async () => {
    const result = await mockApi.connectBrokerage({
      username: "user@example.com",
      password: "sUp3rS3cr3t!!",
      mfa_code: "123456",
    });
    expect(JSON.stringify(result)).not.toContain("sUp3rS3cr3t");
    expect(JSON.stringify(result)).not.toContain("123456");
  });

  it("connectBrokerage() rejects with ApiError(401) when a field is blank", async () => {
    await expect(
      mockApi.connectBrokerage({ username: "", password: "pw", mfa_code: "123456" })
    ).rejects.toBeInstanceOf(ApiError);
    await expect(
      mockApi.connectBrokerage({ username: "u", password: "pw", mfa_code: "" })
    ).rejects.toMatchObject({ status: 401 });
  });

  it("disconnectBrokerage() clears the connected state", async () => {
    await mockApi.connectBrokerage({
      username: "user@example.com",
      password: "hunter2",
      mfa_code: "123456",
    });
    expect((await mockApi.getBrokerageStatus()).connected).toBe(true);

    const result = await mockApi.disconnectBrokerage();
    expect(result).toEqual({ connected: false });
    expect((await mockApi.getBrokerageStatus()).connected).toBe(false);
  });

  it("never persists the raw credential strings to localStorage", async () => {
    await mockApi.connectBrokerage({
      username: "user@example.com",
      password: "sUp3rS3cr3t!!",
      mfa_code: "123456",
    });
    const dump: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)!;
      dump.push(`${key}=${localStorage.getItem(key)}`);
    }
    const joined = dump.join("\n");
    expect(joined).not.toContain("sUp3rS3cr3t");
    expect(joined).not.toContain("123456");
  });
});

describe("mock API — /llm/status contract", () => {
  it("returns the honest default posture (all off, no calls, no attention)", async () => {
    const s = await mockApi.getLlmStatus();
    expect(s.attention).toBe(false);
    expect(s.attention_reason).toBeNull();
    expect(typeof s.capabilities_source).toBe("string");
    expect(typeof s.providers_source).toBe("string");
    expect(typeof s.telemetry_note).toBe("string");
    // three providers, each a full-shape "none" record — null, never 0/false.
    expect(Object.keys(s.providers).sort()).toEqual(["claude", "gemini", "openai"]);
    for (const p of Object.values(s.providers)) {
      expect(p.source).toBe("none");
      expect(p.ok).toBeNull();
      expect(p.error_kind).toBeNull();
      expect(p.checked_at).toBeNull();
      expect(p.age_seconds).toBeNull();
    }
  });

  it("every capability row carries the full types.ts shape", async () => {
    const s = await mockApi.getLlmStatus();
    expect(s.capabilities.length).toBe(5);
    for (const c of s.capabilities) {
      expect(typeof c.key).toBe("string");
      expect(typeof c.label).toBe("string");
      expect(["on_demand", "scheduled"]).toContain(c.trigger);
      expect(Array.isArray(c.provider_keys)).toBe(true);
      // active_provider / invalid_provider are provider-name-or-null.
      expect(c.active_provider === null || typeof c.active_provider === "string").toBe(true);
      expect(c.invalid_provider === null || typeof c.invalid_provider === "string").toBe(true);
      expect(typeof c.enabled).toBe("boolean");
      expect(typeof c.built).toBe("boolean");
      expect(["ready", "disabled", "missing_key", "invalid_key", "not_built"]).toContain(c.status);
    }
  });
});

describe("mock API — Agentic Trading contract", () => {
  // putScanConfig / status read from localStorage-backed stores; isolate them.
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it("getAgenticStatus() returns the full composite AgenticStatus shape", async () => {
    const s = await mockApi.getAgenticStatus();
    expect(typeof s.mode).toBe("string");
    expect(typeof s.advisory_only).toBe("boolean");
    expect(typeof s.kill_switch.active).toBe("boolean");
    expect(
      s.kill_switch.reason === null || typeof s.kill_switch.reason === "string"
    ).toBe(true);
    // queue = summary sub-shape only (never the full intents list).
    expect(typeof s.queue.mode).toBe("string");
    expect(typeof s.queue.n_intents).toBe("number");
    expect(typeof s.queue.n_placeable).toBe("number");
    expect(typeof s.queue.stale).toBe("boolean");
    // follows summary.
    expect(typeof s.follows.n_active).toBe("number");
    expect(typeof s.follows.total_amount).toBe("number");
    // agent_loop mirrors engine/advisory_agent.py's persisted AgentState.
    expect(typeof s.agent_loop.cycle_count).toBe("number");
    expect(typeof s.agent_loop.backlog_count).toBe("number");
    expect(
      s.agent_loop.last_cycle_iso === null ||
        typeof s.agent_loop.last_cycle_iso === "string"
    ).toBe(true);
  });

  it("getAgenticDiscovery() surfaces a scored AND an honestly-unscored candidate (CONSTRAINT #4)", async () => {
    const d = await mockApi.getAgenticDiscovery();
    expect(Array.isArray(d.candidates)).toBe(true);
    expect(Array.isArray(d.scan_configs)).toBe(true);
    expect(typeof d.writable).toBe("boolean");
    expect(typeof d.note).toBe("string");
    // action/conviction are provider-null or populated together — never a
    // fabricated score for a symbol the advisory couldn't cross-reference.
    for (const c of d.candidates) {
      expect(typeof c.symbol).toBe("string");
      expect(c.action === null || typeof c.action === "string").toBe(true);
      expect(c.conviction === null || typeof c.conviction === "number").toBe(true);
    }
    const scored = d.candidates.find((c) => c.action !== null);
    const unscored = d.candidates.find((c) => c.action === null);
    expect(scored?.conviction).not.toBeNull();
    expect(unscored).toBeDefined();
    expect(unscored!.conviction).toBeNull();
  });

  it("putScanConfig() persists + echoes the stored row, preserving created_at on update", async () => {
    const first = await mockApi.putScanConfig({
      name: "my_scan",
      filters: { min_price: 5 },
      enabled: true,
    });
    expect(first.scan_config.name).toBe("my_scan");
    expect(first.scan_config.filters).toEqual({ min_price: 5 });
    expect(first.scan_config.enabled).toBe(true);
    expect(first.applies).toBe("next_discovery_run");
    expect(typeof first.note).toBe("string");

    // now readable back through the discovery read.
    const disc = await mockApi.getAgenticDiscovery();
    expect(disc.scan_configs.some((c) => c.name === "my_scan")).toBe(true);

    // an upsert to the same name preserves created_at (not a fresh row).
    const second = await mockApi.putScanConfig({
      name: "my_scan",
      filters: { min_price: 10 },
      enabled: false,
    });
    expect(second.scan_config.created_at).toBe(first.scan_config.created_at);
    expect(second.scan_config.enabled).toBe(false);
    expect(second.scan_config.filters).toEqual({ min_price: 10 });
  });
});
