import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
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

  it('honestly discloses the SPY-volume-proxy nature of the fallback demo data', () => {
    // Regression guard: the backend's SPY-proxy fallback response labels every
    // curve "SPY Volume Proxy" specifically so it is never mistaken for real
    // Google Trends data (see api/data_api.py::get_trends_stitch_demo's
    // docstring). The screen's headline text must not contradict that by
    // presenting the fallback as unqualified real search-volume data. Since the
    // backend can also return REAL Google Trends data now (when TrendsStore has
    // some on file), the copy must not claim live data "isn't wired up" -- only
    // that a proxy substitution, when it happens, is always disclosed by name.
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
    expect(screen.getByText(/never presented as real search-volume data/)).toBeInTheDocument();
  });

  it('renders a loading indicator and withholds the chart while data is loading', () => {
    vi.mocked(useApi).mockReturnValue({
      data: null,
      loading: true,
      error: null,
      status: null,
      stale: false,
      cachedAt: null,
      reload: vi.fn(),
    });

    const { container } = render(<TrendsVisualizer />);

    // The heading renders regardless of load state.
    expect(screen.getByText('SVI Stitching Algorithm Demo')).toBeInTheDocument();
    // The real chart (and error state) must not appear while loading.
    expect(screen.queryByTestId('trends-stitch-chart')).not.toBeInTheDocument();
    expect(screen.queryByText(/Couldn't load/i)).not.toBeInTheDocument();
    // `Loading` (components/ui.tsx) renders `lines` skeleton placeholder divs
    // with no distinguishing testid/role/text -- assert on its stable
    // `.skeleton` class instead.
    expect(container.querySelectorAll('.skeleton').length).toBeGreaterThan(0);
  });

  it('renders the error message and calls reload when Retry is clicked', () => {
    const reload = vi.fn();
    vi.mocked(useApi).mockReturnValue({
      data: null,
      loading: false,
      error: 'Something broke',
      status: 500,
      stale: false,
      cachedAt: null,
      reload,
    });

    render(<TrendsVisualizer />);

    expect(screen.getByText('Something broke')).toBeInTheDocument();
    expect(screen.queryByTestId('trends-stitch-chart')).not.toBeInTheDocument();

    const retryButton = screen.getByRole('button', { name: /retry/i });
    fireEvent.click(retryButton);
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
