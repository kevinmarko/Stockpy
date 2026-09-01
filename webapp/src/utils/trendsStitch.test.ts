import { describe, expect, it } from "vitest";
import {
  stitchIntervals,
  stitchMultipleIntervals,
  type TrendsPoint,
} from "./trendsStitch";

const DAY_MS = 86_400_000;
const t = (day: number) => day * DAY_MS;

describe("stitchIntervals", () => {
  it("recovers the true scaling factor from a known-multiple overlap and applies it to B's non-overlap tail", () => {
    // Period A: days 0-5, on a "small" scale.
    const periodA: TrendsPoint[] = [
      [t(0), 10],
      [t(1), 20],
      [t(2), 30],
      [t(3), 40],
      [t(4), 50],
      [t(5), 60],
    ];

    // Period B: days 3-8, overlapping A on days 3-5. B's overlap values are
    // EXACTLY 3x A's corresponding values -- a known ground-truth relationship
    // so the recovered scaling factor can be checked precisely (should be
    // ~1/3, to bring B back down onto A's scale).
    const periodB: TrendsPoint[] = [
      [t(3), 40 * 3], // 120
      [t(4), 50 * 3], // 150
      [t(5), 60 * 3], // 180
      [t(6), 70 * 3], // 210 (non-overlap tail)
      [t(7), 80 * 3], // 240 (non-overlap tail)
      [t(8), 90 * 3], // 270 (non-overlap tail)
    ];

    const stitched = stitchIntervals(periodA, periodB);
    const byTs = new Map(stitched);

    // Sorted ascending by timestamp.
    expect(stitched.map(([ts]) => ts)).toEqual(
      [...stitched].map(([ts]) => ts).sort((a, b) => a - b),
    );

    // Union of both periods' timestamps (6 + 6 - 3 overlap = 9 points).
    expect(stitched.length).toBe(9);

    // Non-overlap A-only days pass through untouched.
    expect(byTs.get(t(0))).toBeCloseTo(10, 6);
    expect(byTs.get(t(1))).toBeCloseTo(20, 6);
    expect(byTs.get(t(2))).toBeCloseTo(30, 6);

    // Overlap days are the average of A's original value and B's *scaled*
    // value; since B's overlap values are exactly 3x A's, the recovered
    // scaling factor (~1/3) brings scaled-B back to ~A, so the average
    // stays close to A's own value.
    expect(byTs.get(t(3))).toBeCloseTo(40, 4);
    expect(byTs.get(t(4))).toBeCloseTo(50, 4);
    expect(byTs.get(t(5))).toBeCloseTo(60, 4);

    // Non-overlap B-only tail is genuinely DERIVED: it must be B's raw
    // value scaled by the SAME recovered factor (~1/3), not passed through
    // raw and not independently random. Recover the implied factor from one
    // tail point and confirm it's consistent across the rest of the tail.
    const impliedFactor = (byTs.get(t(6)) as number) / 210;
    expect(impliedFactor).toBeCloseTo(1 / 3, 4);
    expect(byTs.get(t(7))).toBeCloseTo(240 * impliedFactor, 6);
    expect(byTs.get(t(8))).toBeCloseTo(270 * impliedFactor, 6);
  });

  it("throws when the two periods share no overlapping timestamps", () => {
    const periodA: TrendsPoint[] = [
      [t(0), 10],
      [t(1), 20],
    ];
    const periodB: TrendsPoint[] = [
      [t(10), 100],
      [t(11), 110],
    ];

    expect(() => stitchIntervals(periodA, periodB)).toThrow(
      /No overlapping/,
    );
  });

  it("treats an all-zero overlap as scaling factor 1.0 (epsilon guard), passing B's tail through unscaled", () => {
    const periodA: TrendsPoint[] = [
      [t(0), 5],
      [t(1), 0],
      [t(2), 0],
    ];
    const periodB: TrendsPoint[] = [
      [t(1), 0],
      [t(2), 0],
      [t(3), 42],
      [t(4), 84],
    ];

    const stitched = stitchIntervals(periodA, periodB);
    const byTs = new Map(stitched);

    // Both sides replace 0 -> 0.1 for the ratio, so sum_a == sum_b == 0.2
    // over the two overlap days -> factor == 1.0 exactly.
    expect(byTs.get(t(3))).toBeCloseTo(42, 6);
    expect(byTs.get(t(4))).toBeCloseTo(84, 6);

    // Overlap days: A's original value (0) averaged with B's scaled (still
    // 0, since factor==1) value -> 0.
    expect(byTs.get(t(1))).toBeCloseTo(0, 6);
    expect(byTs.get(t(2))).toBeCloseTo(0, 6);
  });
});

describe("stitchMultipleIntervals", () => {
  it("chains three overlapping windows left-to-right onto a single continuous scale", () => {
    // Window 1: days 0-4, scale ~[100-140].
    const window1: TrendsPoint[] = [
      [t(0), 100],
      [t(1), 110],
      [t(2), 120],
      [t(3), 130],
      [t(4), 140],
    ];
    // Window 2: days 3-7, overlapping window1 on days 3-4, on DOUBLE the
    // scale (Google Trends' own window-relative renormalization).
    const window2: TrendsPoint[] = [
      [t(3), 130 * 2],
      [t(4), 140 * 2],
      [t(5), 150 * 2],
      [t(6), 160 * 2],
      [t(7), 170 * 2],
    ];
    // Window 3: days 6-10, overlapping window2 on days 6-7, on HALF window2's
    // raw scale (i.e. 1/4 of window1's original scale).
    const window3: TrendsPoint[] = [
      [t(6), 160 * 2 * 0.5],
      [t(7), 170 * 2 * 0.5],
      [t(8), 180 * 2 * 0.5],
      [t(9), 190 * 2 * 0.5],
      [t(10), 200 * 2 * 0.5],
    ];

    const stitched = stitchMultipleIntervals([window1, window2, window3]);
    const byTs = new Map(stitched);

    // Union of days 0-10 -> 11 points, no duplicates, sorted ascending.
    expect(stitched.length).toBe(11);
    expect(stitched.map(([ts]) => ts)).toEqual(
      [...stitched].map(([ts]) => ts).sort((a, b) => a - b),
    );
    expect(new Set(stitched.map(([ts]) => ts)).size).toBe(11);

    // No NaNs/undefined anywhere -- every value genuinely derived.
    for (const [, value] of stitched) {
      expect(Number.isFinite(value)).toBe(true);
    }

    // window1-only days pass through untouched.
    expect(byTs.get(t(0))).toBeCloseTo(100, 6);
    expect(byTs.get(t(2))).toBeCloseTo(120, 6);

    // The final tail (window3-only, day 10) should land back on window1's
    // original linear scale (continuing 100,110,120,...  -> 200 at day 10),
    // since each pairwise stitch recovers the prior window's scale --
    // proving the chain actually propagates a consistent scale through 3
    // windows rather than emitting independent noise.
    expect(byTs.get(t(10))).toBeCloseTo(200, 3);
  });

  it("returns [] for empty input and the single period unchanged (sorted) for one input", () => {
    expect(stitchMultipleIntervals([])).toEqual([]);

    const single: TrendsPoint[] = [
      [t(2), 30],
      [t(0), 10],
      [t(1), 20],
    ];
    expect(stitchMultipleIntervals([single])).toEqual([
      [t(0), 10],
      [t(1), 20],
      [t(2), 30],
    ]);
  });
});
