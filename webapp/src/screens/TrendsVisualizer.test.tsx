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

    expect(screen.getByText('SVI Stitching Algorithm Demo')).toBeInTheDocument();
    expect(screen.getByTestId('trends-stitch-chart')).toBeInTheDocument();
  });

  it('honestly discloses the SPY-volume-proxy nature of the demo data', () => {
    // Regression guard: the backend's own response labels every curve "SPY Volume
    // Proxy" specifically so this demo is never mistaken for real Google Trends
    // data (see api/data_api.py::get_trends_stitch_demo's docstring). The screen's
    // headline text must not contradict that by presenting it as unqualified real
    // search-volume data.
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

    expect(screen.getByText(/SPY Volume Proxy/)).toBeInTheDocument();
    expect(screen.getByText(/isn't wired up in this platform/)).toBeInTheDocument();
  });
});
