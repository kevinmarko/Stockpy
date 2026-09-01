// webapp/src/utils/trendsStitch.ts
//
// Pure, dependency-free TypeScript port of `data/trends_stitcher.py`'s
// `GoogleTrendsStitcher.stitch_intervals` / `stitch_multiple_intervals`.
//
// This exists so the Trends Stitching demo (`webapp/src/api/mock.ts`'s
// `getTrendsStitchDemo()`) can run the REAL stitching algorithm client-side
// against genuinely overlapping-but-differently-scaled mock windows, instead
// of fabricating an unrelated "stitched" curve. Kept free of any charting
// library / pandas-equivalent so it stays trivially unit-testable and usable
// anywhere else a client-side stitch is needed.
//
// Algorithm (mirrors the Python implementation's overlapping-window
// empirical scaling-factor approach, Da/Engelberg/Gao-style SVI stitching):
//   1. Find the calendar (timestamp) overlap between period A and period B.
//   2. Within the overlap ONLY, replace any zero value with 0.1 (prevents a
//      div-by-zero / degenerate scaling factor) and sum both sides.
//   3. scaling_factor = sum(overlap_a) / sum(overlap_b), or 1.0 if
//      sum(overlap_b) <= 1e-9 (guards a degenerate/all-zero overlap).
//   4. Scale ALL of period B (not just the overlap) by that factor.
//   5. Merge: prefer period A's own points; fill in scaled period B
//      elsewhere.
//   6. At the overlap timestamps, smooth the boundary by averaging period
//      A's (original) value and period B's (scaled) value.

export type TrendsPoint = [number, number]; // [timestamp_ms, value]

const ZERO_REPLACEMENT = 0.1;
const MIN_DENOMINATOR = 1e-9;

/**
 * Stitch two adjacent, overlapping daily SVI-style series into one
 * continuous series, rescaling period B onto period A's scale using the
 * empirical sum-ratio over their overlapping timestamps.
 *
 * Throws if the two periods share no overlapping timestamps (exact
 * millisecond match), mirroring the Python implementation's `ValueError`.
 */
export function stitchIntervals(
  periodA: TrendsPoint[],
  periodB: TrendsPoint[],
): TrendsPoint[] {
  if (periodA.length === 0) {
    return periodB.slice().sort((a, b) => a[0] - b[0]);
  }
  if (periodB.length === 0) {
    return periodA.slice().sort((a, b) => a[0] - b[0]);
  }

  const mapA = new Map<number, number>(periodA);
  const mapB = new Map<number, number>(periodB);

  const overlapTimestamps: number[] = [];
  for (const ts of mapA.keys()) {
    if (mapB.has(ts)) {
      overlapTimestamps.push(ts);
    }
  }

  if (overlapTimestamps.length === 0) {
    throw new Error(
      "Stitching aborted: No overlapping dates found between windows.",
    );
  }

  // Step 2-3: empirical scaling factor from the (zero-guarded) overlap sums.
  let sumA = 0;
  let sumB = 0;
  for (const ts of overlapTimestamps) {
    const a = mapA.get(ts) as number;
    const b = mapB.get(ts) as number;
    sumA += a === 0 ? ZERO_REPLACEMENT : a;
    sumB += b === 0 ? ZERO_REPLACEMENT : b;
  }
  const scalingFactor = sumB > MIN_DENOMINATOR ? sumA / sumB : 1.0;

  // Step 4: scale ALL of period B (original, unreplaced values) by the factor.
  const scaledB = new Map<number, number>();
  for (const [ts, value] of mapB) {
    scaledB.set(ts, value * scalingFactor);
  }

  // Step 5: merge, preferring period A's own points; fill in scaled B
  // elsewhere (pandas `combine_first` semantics).
  const combined = new Map<number, number>();
  for (const [ts, value] of mapA) {
    combined.set(ts, value);
  }
  for (const [ts, value] of scaledB) {
    if (!combined.has(ts)) {
      combined.set(ts, value);
    }
  }

  // Step 6: smooth the overlap boundary by averaging A's original value and
  // B's scaled value.
  for (const ts of overlapTimestamps) {
    const a = mapA.get(ts) as number;
    const scaledBValue = scaledB.get(ts) as number;
    combined.set(ts, (a + scaledBValue) / 2.0);
  }

  return Array.from(combined.entries())
    .sort((x, y) => x[0] - y[0])
    .map(([ts, value]) => [ts, value] as TrendsPoint);
}

/**
 * Left-folds `stitchIntervals` across an ordered list of periods:
 * stitch(periods[0], periods[1]) -> stitch(result, periods[2]) -> ...
 */
export function stitchMultipleIntervals(
  periods: TrendsPoint[][],
): TrendsPoint[] {
  if (periods.length === 0) {
    return [];
  }
  if (periods.length === 1) {
    return periods[0].slice().sort((a, b) => a[0] - b[0]);
  }

  let stitched = periods[0];
  for (let i = 1; i < periods.length; i++) {
    stitched = stitchIntervals(stitched, periods[i]);
  }
  return stitched;
}
