import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { TrendsStitchChart, TrendsCurve, formatUtcDate } from './TrendsStitchChart';

// Mock recharts in case it is currently used or after it gets rewritten
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
  LineChart: () => <div data-testid="recharts-line-chart">Chart</div>,
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  Legend: () => null,
}));

// Mock echarts-for-react in case it is still used
vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="echarts-mock">Chart</div>
}));

describe('TrendsStitchChart', () => {
  it('renders without crashing', () => {
    const rawCurves: TrendsCurve[] = [
      { name: 'Raw 1', data: [[1, 10], [2, 20]] }
    ];
    const stitchedCurve: TrendsCurve = { name: 'Stitched', data: [[1, 15], [2, 25]] };

    const { container } = render(
      <TrendsStitchChart rawCurves={rawCurves} stitchedCurve={stitchedCurve} />
    );
    expect(container).toBeTruthy();
  });

  describe('formatUtcDate', () => {
    it('never shifts a UTC-midnight-encoded bar date back a calendar day', () => {
      // Regression guard for the off-by-one-day bug: the backend encodes each real
      // trading date as UTC midnight (api/data_api.py's to_curve() does
      // ts.timestamp() * 1000 on a tz-naive pandas Timestamp, which pandas treats
      // as UTC). Formatting with the runtime's LOCAL timezone (the pre-fix
      // behavior) would render this as one day earlier in any timezone behind
      // UTC -- e.g. America/New_York. formatUtcDate must always report the real
      // UTC calendar date regardless of what timezone this test happens to run in.
      const utcMidnightJan2_2026 = Date.UTC(2026, 0, 2, 0, 0, 0); // 1767312000000
      expect(formatUtcDate(utcMidnightJan2_2026)).toBe('1/2/2026');

      const utcMidnightAug28_2026 = Date.UTC(2026, 7, 28, 0, 0, 0);
      expect(formatUtcDate(utcMidnightAug28_2026)).toBe('8/28/2026');
    });

    it('returns an empty string for a falsy tick', () => {
      expect(formatUtcDate(0)).toBe('');
      expect(formatUtcDate(null)).toBe('');
    });
  });
});
