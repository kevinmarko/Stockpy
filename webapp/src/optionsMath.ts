/**
 * optionsMath.ts
 * Pure TypeScript module for options P/L payoff, expected move, probability zones, and breakeven calculations.
 */

export interface OptionLeg {
  Side: "Short" | "Long";
  Type: "Put" | "Call";
  Strike: number | null;
  Price: number | null;
}

/**
 * Approximation of the normal cumulative distribution function (CDF).
 * Using the high-accuracy Abramowitz and Stegun approximation.
 */
export function cumulativeNormalDistribution(x: number): number {
  if (typeof x !== "number" || isNaN(x) || !isFinite(x)) {
    return NaN;
  }
  const negate = x < 0 ? 1 : 0;
  if (negate) {
    x = -x;
  }
  const k = 1.0 / (1.0 + 0.2316419 * x);
  const d = 0.3989422804014327; // 1 / Math.sqrt(2 * Math.PI)
  const cdf = 1.0 - d * Math.exp(-0.5 * x * x) * (
    k * (0.319381530 + k * (-0.356563782 + k * (1.781477937 + k * (-1.821255978 + k * 1.330274429))))
  );
  return negate ? 1.0 - cdf : cdf;
}

/**
 * Normal probability density function (PDF).
 */
export function normalProbabilityDensity(x: number, mean: number, sd: number): number {
  if (
    typeof x !== "number" || isNaN(x) || !isFinite(x) ||
    typeof mean !== "number" || isNaN(mean) || !isFinite(mean) ||
    typeof sd !== "number" || isNaN(sd) || !isFinite(sd) ||
    sd <= 0
  ) {
    return NaN;
  }
  const exponent = -Math.pow(x - mean, 2) / (2 * sd * sd);
  return (1.0 / (sd * Math.sqrt(2 * Math.PI))) * Math.exp(exponent);
}

/**
 * Filters a leg list down to structurally valid legs (non-null/non-NaN
 * strike and price, a recognized Side/Type). Shared by every function below
 * so "what counts as a valid leg" can't drift between them.
 */
function filterValidLegs(legs: OptionLeg[]): OptionLeg[] {
  return (legs || []).filter(
    (leg) =>
      leg &&
      (leg.Side === "Short" || leg.Side === "Long") &&
      (leg.Type === "Put" || leg.Type === "Call") &&
      leg.Strike !== null &&
      !isNaN(leg.Strike) &&
      leg.Strike > 0 &&
      leg.Price !== null &&
      !isNaN(leg.Price) &&
      leg.Price >= 0
  );
}

/**
 * Computes the total net payoff (dollars, contract multiplier of 100
 * applied) of a set of already-validated option legs at underlying price S.
 * Short = collect premium now, pay off intrinsic value at expiry (+p, -payoff);
 * Long = pay premium now, receive intrinsic value at expiry (-p, +payoff).
 * Shared by computePayoff, computeBreakevenPoints, and
 * computeProbabilityOfProfit so all three price the exact same position
 * identically -- this used to be copy-pasted per function.
 */
export function evaluatePayoffAt(legs: OptionLeg[], S: number): number {
  let total = 0;
  for (const leg of legs) {
    const K = leg.Strike as number;
    const p = leg.Price as number;
    const legPayoff = leg.Type === "Call" ? Math.max(0, S - K) : Math.max(0, K - S);
    total += leg.Side === "Short" ? (-legPayoff + p) * 100 : (legPayoff - p) * 100;
  }
  return total;
}

/**
 * Computes the payoff at expiry across a price range.
 * Range is from 0.8 * spot to 1.2 * spot, or wider if leg strikes are outside this range.
 * Returns an array of { price, payoff } objects.
 * Leg premium is factored in (Short = credit/+, Long = debit/-).
 * Contract multiplier is 100.
 *
 * NOTE: this grid is sized for charting a P/L curve's x-axis, not for
 * integrating probability -- it does NOT extend to the true unbounded price
 * domain. Use computeProbabilityOfProfit (closed-form, unbounded), not a sum
 * over this grid's points, for any probability calculation.
 */
export function computePayoff(
  legs: OptionLeg[],
  spotPrice: number,
  pointsCount: number = 100
): { price: number; payoff: number }[] {
  if (typeof spotPrice !== "number" || isNaN(spotPrice) || spotPrice <= 0) {
    return [];
  }

  const validLegs = filterValidLegs(legs);

  if (validLegs.length === 0) {
    return [];
  }

  let minPrice = 0.8 * spotPrice;
  let maxPrice = 1.2 * spotPrice;

  if (validLegs.length > 0) {
    const strikes = validLegs.map((leg) => leg.Strike as number);
    const minStrike = Math.min(...strikes);
    const maxStrike = Math.max(...strikes);
    if (minStrike * 0.9 < minPrice) {
      minPrice = minStrike * 0.9;
    }
    if (maxStrike * 1.1 > maxPrice) {
      maxPrice = maxStrike * 1.1;
    }
  }

  minPrice = Math.max(0, minPrice);

  const count = Math.max(2, pointsCount);
  const step = (maxPrice - minPrice) / (count - 1);
  const points: { price: number; payoff: number }[] = [];

  for (let i = 0; i < count; i++) {
    const S = minPrice + i * step;
    points.push({ price: S, payoff: evaluatePayoffAt(validLegs, S) });
  }

  return points;
}

/**
 * Computes the expected move.
 * expectedMove = spotPrice * sigma * Math.sqrt(dte / 252)
 * Note: if spotPrice, sigma or dte is missing or non-positive, return 0.
 */
export function computeExpectedMove(spotPrice: number, sigma: number, dte: number): number {
  if (
    typeof spotPrice !== "number" || isNaN(spotPrice) || spotPrice <= 0 ||
    typeof sigma !== "number" || isNaN(sigma) || sigma <= 0 ||
    typeof dte !== "number" || isNaN(dte) || dte <= 0
  ) {
    return 0;
  }
  return spotPrice * sigma * Math.sqrt(dte / 252);
}

/**
 * Computes probability zones (±1σ, ±2σ, ±3σ) using a log-normal model.
 */
export function computeProbabilityZones(
  spotPrice: number,
  sigma: number,
  dte: number
): { label: string; lower: number; upper: number; sigmaLevel: number }[] {
  if (
    typeof spotPrice !== "number" || isNaN(spotPrice) || spotPrice <= 0 ||
    typeof sigma !== "number" || isNaN(sigma) || sigma <= 0 ||
    typeof dte !== "number" || isNaN(dte) || dte <= 0
  ) {
    return [];
  }

  const periodSigma = (sigma / Math.sqrt(252)) * Math.sqrt(dte);
  const zones: { label: string; lower: number; upper: number; sigmaLevel: number }[] = [];

  for (let n = 1; n <= 3; n++) {
    const lower = spotPrice * Math.exp(-n * periodSigma);
    const upper = spotPrice * Math.exp(n * periodSigma);
    zones.push({
      label: `±${n}σ`,
      lower,
      upper,
      sigmaLevel: n,
    });
  }

  return zones;
}

/**
 * Returns strike prices where the strategy net payoff equals zero at expiry.
 * Solved by constructing a grid of points, evaluating payoffs, and finding exact root crossings.
 */
export function computeBreakevenPoints(legs: OptionLeg[]): number[] {
  const validLegs = filterValidLegs(legs);

  if (validLegs.length === 0) {
    return [];
  }

  const strikes = validLegs.map((leg) => leg.Strike as number);
  const minStrike = Math.min(...strikes);
  const maxStrike = Math.max(...strikes);

  // Set up boundary range
  const rangeMin = Math.max(0, minStrike - (maxStrike - minStrike) - 50);
  const rangeMax = maxStrike + (maxStrike - minStrike) + 50;

  // Build grid including strikes to capture payoff hinge points exactly
  const gridSet = new Set<number>();
  for (const strike of strikes) {
    gridSet.add(strike);
  }

  const pointsCount = 1000;
  const step = (rangeMax - rangeMin) / (pointsCount - 1);
  for (let i = 0; i < pointsCount; i++) {
    gridSet.add(rangeMin + i * step);
  }

  const grid = Array.from(gridSet).sort((a, b) => a - b);

  // Helper to compute payoff at specific price S
  const getPayoffAt = (S: number): number => evaluatePayoffAt(validLegs, S);

  const breakevens: number[] = [];
  const epsilon = 1e-6;

  // Scan grid for crossings
  for (let i = 0; i < grid.length; i++) {
    const s1 = grid[i];
    const y1 = getPayoffAt(s1);

    if (Math.abs(y1) < epsilon) {
      breakevens.push(s1);
    }

    if (i < grid.length - 1) {
      const s2 = grid[i + 1];
      const y2 = getPayoffAt(s2);

      // Check if sign change occurs between s1 and s2
      if (y1 * y2 < 0) {
        // Linear interpolation for exact zero crossing
        const zeroSpot = s1 - y1 * ((s2 - s1) / (y2 - y1));
        breakevens.push(zeroSpot);
      }
    }
  }

  // Deduplicate and round points close to each other
  const uniqueBreakevens: number[] = [];
  for (const val of breakevens) {
    if (!uniqueBreakevens.some((exist) => Math.abs(exist - val) < 1e-4)) {
      uniqueBreakevens.push(val);
    }
  }

  return uniqueBreakevens.sort((a, b) => a - b);
}

/**
 * Computes probability-of-profit (POP): P(payoff(S_T) > 0) at expiration,
 * under the same zero-drift log-normal price model computeProbabilityZones
 * already uses (periodSigma = sigma * sqrt(dte / 252), S_T = spot * exp(X),
 * X ~ Normal(0, periodSigma^2)).
 *
 * This is a CLOSED-FORM integration over the true unbounded price domain
 * (0, +Infinity), not a numerical sum over computePayoff's finite charting
 * grid. That distinction matters: computePayoff's grid only spans roughly
 * [0.8, 1.2] x spot (widened modestly by strikes) for chart-axis purposes,
 * but a credit spread's profitable region is a flat plateau that extends to
 * +-infinity past the outermost strike -- summing a PDF over the truncated
 * grid silently drops that tail probability and systematically UNDERSTATES
 * POP, worse as IV/DTE grow -- confirmed to understate POP by 30+
 * percentage points on a realistic longer-dated, higher-vol credit spread
 * (see optionsMath.test.ts's computeProbabilityOfProfit tests).
 *
 * Approach: computeBreakevenPoints already finds every zero-crossing of the
 * (piecewise-linear) payoff curve. Those breakevens partition (0, +Infinity)
 * into a small number of intervals inside which the payoff sign cannot
 * change (a multi-leg vanilla-option position is asymptotically linear --
 * never oscillates -- beyond its outermost strike, and breakevens ARE its
 * only sign changes by construction), so evaluating the payoff at one
 * representative point per interval is sufficient to classify the whole
 * interval as profit or loss. The log-normal probability mass of every
 * profit interval, including the two open-ended tails, is then summed via
 * the closed-form CDF -- no truncation, no grid-resolution error.
 */
export function computeProbabilityOfProfit(
  legs: OptionLeg[],
  spotPrice: number,
  sigma: number,
  dte: number
): number | null {
  if (
    typeof spotPrice !== "number" || isNaN(spotPrice) || spotPrice <= 0 ||
    typeof sigma !== "number" || isNaN(sigma) || sigma <= 0 ||
    typeof dte !== "number" || isNaN(dte) || dte <= 0
  ) {
    return null;
  }

  const validLegs = filterValidLegs(legs);
  if (validLegs.length === 0) {
    return null;
  }

  const periodSigma = sigma * Math.sqrt(dte / 252);
  const breakevens = computeBreakevenPoints(legs); // sorted ascending
  const lognormalCdf = (x: number): number =>
    cumulativeNormalDistribution(Math.log(x / spotPrice) / periodSigma);

  const edges = [0, ...breakevens, Infinity];
  let pop = 0;

  for (let i = 0; i < edges.length - 1; i++) {
    const lo = edges[i];
    const hi = edges[i + 1];

    // A representative point strictly inside (lo, hi) to sample the
    // (sign-constant) payoff of this interval.
    let probe: number;
    if (lo === 0 && hi === Infinity) {
      probe = spotPrice; // no breakevens at all -- payoff sign is constant everywhere
    } else if (lo === 0) {
      probe = hi / 2;
    } else if (hi === Infinity) {
      probe = lo * 2 + 1; // safely beyond the last breakeven, inside the linear tail
    } else {
      probe = Math.sqrt(lo * hi); // geometric mean -- a price-scale-appropriate midpoint
    }

    if (evaluatePayoffAt(validLegs, probe) > 0) {
      const pLo = lo === 0 ? 0 : lognormalCdf(lo);
      const pHi = hi === Infinity ? 1 : lognormalCdf(hi);
      pop += Math.max(0, pHi - pLo);
    }
  }

  return Math.min(100, Math.max(0, pop * 100));
}
