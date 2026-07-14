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

import { describe, it, expect } from "vitest";
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
  "Trend",
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
