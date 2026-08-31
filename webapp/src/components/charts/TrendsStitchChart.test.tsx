import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { TrendsStitchChart, TrendsCurve } from './TrendsStitchChart';

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
});
