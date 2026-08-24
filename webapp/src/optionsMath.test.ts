import { describe, it, expect } from "vitest";
import {
  cumulativeNormalDistribution,
  normalProbabilityDensity,
  computePayoff,
  computeExpectedMove,
  computeProbabilityZones,
  computeBreakevenPoints,
  computeProbabilityOfProfit,
  evaluatePayoffAt,
  OptionLeg,
} from "./optionsMath";

describe("optionsMath", () => {
  describe("cumulativeNormalDistribution", () => {
    it("returns correct CDF values for standard normal", () => {
      // Mean = 0, SD = 1 values
      expect(cumulativeNormalDistribution(0)).toBeCloseTo(0.5, 4);
      expect(cumulativeNormalDistribution(1.96)).toBeCloseTo(0.975, 3);
      expect(cumulativeNormalDistribution(-1.96)).toBeCloseTo(0.025, 3);
      expect(cumulativeNormalDistribution(1.0)).toBeCloseTo(0.8413, 3);
    });

    it("handles degenerate inputs and NaN", () => {
      expect(cumulativeNormalDistribution(NaN)).toBeNaN();
      expect(cumulativeNormalDistribution(Infinity)).toBeNaN();
    });
  });

  describe("normalProbabilityDensity", () => {
    it("returns correct PDF values", () => {
      // Mean = 0, SD = 1 values
      expect(normalProbabilityDensity(0, 0, 1)).toBeCloseTo(0.3989, 4);
      // Mean = 100, SD = 10
      expect(normalProbabilityDensity(100, 100, 10)).toBeCloseTo(0.03989, 4);
    });

    it("handles degenerate inputs, zero or negative SD", () => {
      expect(normalProbabilityDensity(NaN, 0, 1)).toBeNaN();
      expect(normalProbabilityDensity(0, NaN, 1)).toBeNaN();
      expect(normalProbabilityDensity(0, 0, NaN)).toBeNaN();
      expect(normalProbabilityDensity(0, 0, 0)).toBeNaN();
      expect(normalProbabilityDensity(0, 0, -5)).toBeNaN();
    });
  });

  describe("computePayoff", () => {
    it("correctly computes payoff for a Bull Put Spread (credit)", () => {
      // Bull Put Spread: Sell 100 Put @ $5.00, Buy 95 Put @ $2.00
      // Net Credit = $3.00. Multiplier = 100. Max gain = +$300. Max loss = -$200.
      const legs: OptionLeg[] = [
        { Side: "Short", Type: "Put", Strike: 100, Price: 5 },
        { Side: "Long", Type: "Put", Strike: 95, Price: 2 },
      ];

      // At spot = 110 (all options expire worthless) -> Payoff should be +$300
      const payoffHigh = computePayoff(legs, 100, 10).find(p => p.price >= 105);
      expect(payoffHigh?.payoff).toBeCloseTo(300, 2);

      // At spot = 90 (both puts ITM)
      // Long 95 Put: 5 - 2 = +3. Short 100 Put: -10 + 5 = -5. Total = -2 -> Payoff should be -$200
      const payoffLow = computePayoff(legs, 100, 10).find(p => p.price <= 90);
      if (payoffLow) {
        expect(payoffLow.payoff).toBeCloseTo(-200, 2);
      }
    });

    it("correctly computes payoff for a Bull Call Spread (debit)", () => {
      // Buy 100 Call @ $5.00, Sell 105 Call @ $2.00. Net Debit = $3.00.
      const legs: OptionLeg[] = [
        { Side: "Long", Type: "Call", Strike: 100, Price: 5 },
        { Side: "Short", Type: "Call", Strike: 105, Price: 2 },
      ];

      const payoffs = computePayoff(legs, 100, 100);
      // Below 100 (both OTM): payoff = -3.00 * 100 = -300
      const payoffLow = payoffs.find(p => p.price < 95);
      if (payoffLow) {
        expect(payoffLow.payoff).toBeCloseTo(-300, 2);
      }

      // Above 105 (both ITM): payoff = (5 - 3) * 100 = +200
      const payoffHigh = payoffs.find(p => p.price > 110);
      if (payoffHigh) {
        expect(payoffHigh.payoff).toBeCloseTo(200, 2);
      }
    });

    it("handles empty or degenerate legs gracefully", () => {
      expect(computePayoff([], 100)).toEqual([]);
      expect(computePayoff([{ Side: "Short", Type: "Put", Strike: null, Price: null }], 100)).toEqual([]);
      expect(computePayoff([{ Side: "Short", Type: "Put", Strike: NaN, Price: 5 }], 100)).toEqual([]);
    });
  });

  describe("computeExpectedMove", () => {
    it("calculates expected move value correctly", () => {
      // spot = 100, sigma = 0.20, dte = 252 (Math.sqrt(252/252) = 1)
      // expected move = 100 * 0.20 * 1 = 20
      expect(computeExpectedMove(100, 0.20, 252)).toBeCloseTo(20, 4);
    });

    it("returns 0 for missing or non-positive inputs", () => {
      expect(computeExpectedMove(0, 0.2, 30)).toBe(0);
      expect(computeExpectedMove(100, -0.2, 30)).toBe(0);
      expect(computeExpectedMove(100, 0.2, NaN)).toBe(0);
    });
  });

  describe("computeProbabilityZones", () => {
    it("returns correct 3 log-normal zones", () => {
      const spot = 100;
      const sigma = 0.20;
      const dte = 252;
      const zones = computeProbabilityZones(spot, sigma, dte);

      expect(zones).toHaveLength(3);
      expect(zones[0].label).toBe("±1σ");
      expect(zones[0].sigmaLevel).toBe(1);

      // Period sigma for 252 dte: (0.2 / Math.sqrt(252)) * Math.sqrt(252) = 0.2
      // 1σ lower: 100 * exp(-0.2) = 81.87
      // 1σ upper: 100 * exp(0.2) = 122.14
      expect(zones[0].lower).toBeCloseTo(81.873, 2);
      expect(zones[0].upper).toBeCloseTo(122.140, 2);
    });

    it("returns empty array for invalid inputs", () => {
      expect(computeProbabilityZones(-10, 0.2, 10)).toEqual([]);
    });
  });

  describe("computeBreakevenPoints", () => {
    it("calculates breakeven point for Bull Put Spread", () => {
      // Sell 100 Put @ $5.00, Buy 95 Put @ $2.00. Net Credit = $3.00.
      // Breakeven is Strike - Credit = 100 - 3 = 97.
      const legs: OptionLeg[] = [
        { Side: "Short", Type: "Put", Strike: 100, Price: 5 },
        { Side: "Long", Type: "Put", Strike: 95, Price: 2 },
      ];
      const be = computeBreakevenPoints(legs);
      expect(be).toHaveLength(1);
      expect(be[0]).toBeCloseTo(97, 2);
    });

    it("calculates breakeven point for Bull Call Spread", () => {
      // Buy 100 Call @ $5.00, Sell 105 Call @ $2.00. Net Debit = $3.00.
      // Breakeven is Strike + Debit = 100 + 3 = 103.
      const legs: OptionLeg[] = [
        { Side: "Long", Type: "Call", Strike: 100, Price: 5 },
        { Side: "Short", Type: "Call", Strike: 105, Price: 2 },
      ];
      const be = computeBreakevenPoints(legs);
      expect(be).toHaveLength(1);
      expect(be[0]).toBeCloseTo(103, 2);
    });

    it("returns empty array when no legs or invalid legs are provided", () => {
      expect(computeBreakevenPoints([])).toEqual([]);
      expect(computeBreakevenPoints([{ Side: "Long", Type: "Call", Strike: null, Price: null }])).toEqual([]);
    });
  });

  describe("evaluatePayoffAt", () => {
    it("matches computePayoff's own values at the same price points", () => {
      const legs: OptionLeg[] = [
        { Side: "Short", Type: "Put", Strike: 100, Price: 5 },
        { Side: "Long", Type: "Put", Strike: 95, Price: 2 },
      ];
      const points = computePayoff(legs, 100, 20);
      for (const pt of points) {
        expect(evaluatePayoffAt(legs, pt.price)).toBeCloseTo(pt.payoff, 6);
      }
    });
  });

  describe("computeProbabilityOfProfit", () => {
    it("fixes the confirmed truncation bug: a longer-dated, higher-vol credit " +
      "spread's flat profitable plateau extends well past the finite chart " +
      "grid computePayoff builds -- summing a PDF over that truncated grid " +
      "(the old, buggy approach) silently drops the tail and badly " +
      "understates POP; the closed-form fix must match the true value " +
      "within a tight tolerance, not just 'be higher'", () => {
      // Bull Put Credit Spread: Short 70 Put @ $3.00, Long 60 Put @ $1.00.
      // Net credit = $2.00 -> single breakeven at 70 - 2 = 68.
      // Spot 100, sigma 40%, dte 252 (1 year) -- a realistic longer-dated,
      // higher-vol case, exactly where the old truncated-grid sum diverges
      // most from the true value (confirmed old/buggy result ~48%, vs the
      // true closed-form value below, computed independently in Node before
      // this fix landed: an understatement on this exact scale of order).
      const legs: OptionLeg[] = [
        { Side: "Short", Type: "Put", Strike: 70, Price: 3 },
        { Side: "Long", Type: "Put", Strike: 60, Price: 1 },
      ];
      const spot = 100;
      const sigma = 0.40;
      const dte = 252;

      const breakevens = computeBreakevenPoints(legs);
      expect(breakevens).toHaveLength(1);
      expect(breakevens[0]).toBeCloseTo(68, 2);

      // True closed-form: P(S_T > 68) under the same zero-drift log-normal
      // model this module already uses elsewhere (computeProbabilityZones).
      const periodSigma = sigma * Math.sqrt(dte / 252);
      const trueClosedForm =
        (1 - cumulativeNormalDistribution(Math.log(68 / spot) / periodSigma)) * 100;
      expect(trueClosedForm).toBeGreaterThan(78); // ~83.2% -- sanity-check the reference value itself

      const pop = computeProbabilityOfProfit(legs, spot, sigma, dte);
      expect(pop).not.toBeNull();
      expect(pop as number).toBeCloseTo(trueClosedForm, 1);

      // The old (buggy) truncated-grid approach this fix replaces landed
      // around 48% for an equivalent setup -- a 30+ point understatement.
      // Guard that the fix is not merely "a bit better" but actually close
      // to correct.
      expect(pop as number).toBeGreaterThan(75);
    });

    it("matches the true closed-form CDF for a simple single-breakeven position " +
      "(near-the-money long call)", () => {
      const legs: OptionLeg[] = [{ Side: "Long", Type: "Call", Strike: 100, Price: 5 }];
      const spot = 100;
      const sigma = 0.20;
      const dte = 45;

      // Breakeven = strike + premium = 105
      const periodSigma = sigma * Math.sqrt(dte / 252);
      const trueClosedForm =
        (1 - cumulativeNormalDistribution(Math.log(105 / spot) / periodSigma)) * 100;

      const pop = computeProbabilityOfProfit(legs, spot, sigma, dte);
      expect(pop as number).toBeCloseTo(trueClosedForm, 2);
    });

    it("correctly handles multiple breakevens (Iron Condor -- two profit-region " +
      "boundaries, not a single one)", () => {
      const legs: OptionLeg[] = [
        { Side: "Short", Type: "Put", Strike: 90, Price: 2 },
        { Side: "Long", Type: "Put", Strike: 85, Price: 1 },
        { Side: "Short", Type: "Call", Strike: 110, Price: 2 },
        { Side: "Long", Type: "Call", Strike: 115, Price: 1 },
      ];
      const spot = 100;
      const sigma = 0.25;
      const dte = 30;

      const breakevens = computeBreakevenPoints(legs);
      expect(breakevens).toHaveLength(2);
      expect(breakevens[0]).toBeCloseTo(88, 1); // 90 - net credit (2)
      expect(breakevens[1]).toBeCloseTo(112, 1); // 110 + net credit (2)

      const periodSigma = sigma * Math.sqrt(dte / 252);
      const trueClosedForm =
        (cumulativeNormalDistribution(Math.log(112 / spot) / periodSigma) -
          cumulativeNormalDistribution(Math.log(88 / spot) / periodSigma)) * 100;

      const pop = computeProbabilityOfProfit(legs, spot, sigma, dte);
      expect(pop as number).toBeCloseTo(trueClosedForm, 1);
    });

    it("returns null for degenerate or missing inputs", () => {
      const legs: OptionLeg[] = [{ Side: "Long", Type: "Call", Strike: 100, Price: 5 }];
      expect(computeProbabilityOfProfit([], 100, 0.2, 30)).toBeNull();
      expect(computeProbabilityOfProfit(legs, 0, 0.2, 30)).toBeNull();
      expect(computeProbabilityOfProfit(legs, 100, 0, 30)).toBeNull();
      expect(computeProbabilityOfProfit(legs, 100, 0.2, 0)).toBeNull();
      expect(computeProbabilityOfProfit(legs, 100, NaN, 30)).toBeNull();
    });

    it("stays within [0, 100]", () => {
      const legs: OptionLeg[] = [
        { Side: "Short", Type: "Put", Strike: 100, Price: 5 },
        { Side: "Long", Type: "Put", Strike: 95, Price: 2 },
      ];
      const pop = computeProbabilityOfProfit(legs, 100, 0.6, 400);
      expect(pop).not.toBeNull();
      expect(pop as number).toBeGreaterThanOrEqual(0);
      expect(pop as number).toBeLessThanOrEqual(100);
    });
  });
});
