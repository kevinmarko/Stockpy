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
 * Computes the payoff at expiry across a price range.
 * Range is from 0.8 * spot to 1.2 * spot, or wider if leg strikes are outside this range.
 * Returns an array of { price, payoff } objects.
 * Leg premium is factored in (Short = credit/+, Long = debit/-).
 * Contract multiplier is 100.
 */
export function computePayoff(
  legs: OptionLeg[],
  spotPrice: number,
  pointsCount: number = 100
): { price: number; payoff: number }[] {
  if (typeof spotPrice !== "number" || isNaN(spotPrice) || spotPrice <= 0) {
    return [];
  }

  // Filter out invalid/degenerate legs
  const validLegs = (legs || []).filter(
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
    let totalPayoff = 0;

    for (const leg of validLegs) {
      const K = leg.Strike!;
      const p = leg.Price!;
      let legPayoff = 0;

      if (leg.Type === "Call") {
        legPayoff = Math.max(0, S - K);
      } else {
        legPayoff = Math.max(0, K - S);
      }

      if (leg.Side === "Short") {
        totalPayoff += (-legPayoff + p) * 100;
      } else {
        totalPayoff += (legPayoff - p) * 100;
      }
    }

    points.push({ price: S, payoff: totalPayoff });
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
  const validLegs = (legs || []).filter(
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
  const getPayoffAt = (S: number): number => {
    let total = 0;
    for (const leg of validLegs) {
      const K = leg.Strike!;
      const p = leg.Price!;
      let legPayoff = 0;
      if (leg.Type === "Call") {
        legPayoff = Math.max(0, S - K);
      } else {
        legPayoff = Math.max(0, K - S);
      }

      if (leg.Side === "Short") {
        total += (-legPayoff + p) * 100;
      } else {
        total += (legPayoff - p) * 100;
      }
    }
    return total;
  };

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
