import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TrendsVisualizer } from './TrendsVisualizer';
import { useApi } from '../hooks/useApi';

vi.mock('../hooks/useApi');
vi.mock('../api/client', () => ({
  api: {
    getTrendsStitchDemo: vi.fn(),
  }
}));

vi.mock('../components/charts/TrendsStitchChart', () => ({
  TrendsStitchChart: () => <div data-testid="trends-stitch-chart">Mock Chart</div>
}));

describe('TrendsVisualizer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the title and chart', () => {
    vi.mocked(useApi).mockReturnValue({
      data: {
        raw_curves: [],
        stitched_curve: { name: 'Test', data: [] }
      },
      loading: false,
      error: null,
      status: 200,
      stale: false,
      cachedAt: null,
      reload: vi.fn(),
    });

    render(<TrendsVisualizer />);
    
    expect(screen.getByText('Google Trends SVI Stitching')).toBeInTheDocument();
    expect(screen.getByTestId('trends-stitch-chart')).toBeInTheDocument();
  });
});
