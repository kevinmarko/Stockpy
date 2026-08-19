import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { RealTimeRiskRadar } from './RealTimeRiskRadar';

describe('RealTimeRiskRadar', () => {
  it('renders risk title and status badge', () => {
    render(<RealTimeRiskRadar />);
    expect(screen.getByText('Real-Time Portfolio Risk & Greeks Streamer')).toBeInTheDocument();
    expect(screen.getByText('1 Hz Live Stream')).toBeInTheDocument();
  });

  it('renders Greeks KPI values in mock mode', () => {
    render(<RealTimeRiskRadar />);
    expect(screen.getByText('Net Delta (Δ)')).toBeInTheDocument();
    expect(screen.getByText('Beta-SPY Delta (βΔ)')).toBeInTheDocument();
    expect(screen.getByText('Net Gamma (Γ)')).toBeInTheDocument();
    expect(screen.getByText('Net Theta (Θ)')).toBeInTheDocument();
    expect(screen.getByText('Net Vega (𝒱)')).toBeInTheDocument();
  });

  it('renders positions breakdown table', () => {
    render(<RealTimeRiskRadar />);
    expect(screen.getByText('AAPL')).toBeInTheDocument();
    expect(screen.getByText(/AAPL 2026-09-18 \$185\.00 CALL/)).toBeInTheDocument();
  });

  it('cleans up interval on unmount without errors', () => {
    const { unmount } = render(<RealTimeRiskRadar />);
    expect(() => unmount()).not.toThrow();
  });
});
