import React, { useEffect, useRef, useState } from 'react';
import { Activity, AlertTriangle } from 'lucide-react';
import { portfolioRiskWsUrl, USE_MOCK } from '../../api/client';
import { getMockPortfolioRiskStreamEvent } from '../../api/mock';
import type { PortfolioRiskStreamEvent } from '../../api/types';
import { theme } from '../../theme';

interface RealTimeRiskRadarProps {
  className?: string;
}

export const RealTimeRiskRadar: React.FC<RealTimeRiskRadarProps> = ({ className = '' }) => {
  const [riskData, setRiskData] = useState<PortfolioRiskStreamEvent | null>(
    USE_MOCK ? getMockPortfolioRiskStreamEvent() : null
  );
  const [connectionStatus, setConnectionStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting');
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let isMounted = true;

    if (USE_MOCK) {
      setConnectionStatus('connected');
      const interval = setInterval(() => {
        if (!isMounted) return;
        const mockEv = getMockPortfolioRiskStreamEvent();
        // Add tiny micro-fluctuations to simulate live ticking
        const jitter = (Math.random() - 0.5) * 0.4;
        setRiskData({
          ...mockEv,
          spy_price: Number((mockEv.spy_price + jitter).toFixed(2)),
          net_dollar_delta: Number((mockEv.net_dollar_delta + jitter * 50).toFixed(2)),
          timestamp: new Date().toISOString(),
        });
      }, 1000);

      return () => {
        isMounted = false;
        clearInterval(interval);
      };
    }

    // Live WebSocket connection
    const wsUrl = portfolioRiskWsUrl();
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (isMounted) setConnectionStatus('connected');
    };

    ws.onmessage = (event) => {
      if (!isMounted) return;
      try {
        const parsed: PortfolioRiskStreamEvent = JSON.parse(event.data);
        setRiskData(parsed);
      } catch (err) {
        console.warn('Failed to parse portfolio risk WebSocket frame:', err);
      }
    };

    ws.onerror = () => {
      if (isMounted) setConnectionStatus('disconnected');
    };

    ws.onclose = () => {
      if (isMounted) setConnectionStatus('disconnected');
    };

    return () => {
      isMounted = false;
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
      wsRef.current = null;
    };
  }, []);

  const formatDollar = (val: number) => {
    const prefix = val < 0 ? '-' : '+';
    return `${prefix}$${Math.abs(val).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };

  return (
    <div
      className={`card ${className}`}
      data-testid="realtime-risk-radar"
      style={{
        background: theme.surface,
        border: `1px solid ${theme.border}`,
        borderRadius: 'var(--r-md)',
        padding: 'var(--s-4)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--s-4)',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 'var(--s-2)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)' }}>
          <Activity size={20} color={theme.accent} />
          <h3 style={{ margin: 0, fontSize: 'var(--t-body)', fontWeight: 600, color: theme.textPrimary }}>
            Real-Time Portfolio Risk & Greeks Streamer
          </h3>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-3)' }}>
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 'var(--s-1-5)',
              padding: 'var(--s-1) var(--s-2-5)',
              borderRadius: 'var(--r-pill)',
              fontSize: 'var(--t-caption)',
              fontWeight: 500,
              background: connectionStatus === 'connected' ? 'rgba(16, 185, 129, 0.12)' : 'rgba(239, 68, 68, 0.12)',
              color: connectionStatus === 'connected' ? theme.growth : theme.decline,
            }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: connectionStatus === 'connected' ? theme.growth : theme.decline,
              }}
            />
            {connectionStatus === 'connected' ? '1 Hz Live Stream' : connectionStatus === 'connecting' ? 'Connecting...' : 'Disconnected'}
          </div>

          {riskData && (
            <span style={{ fontSize: 'var(--t-caption)', color: theme.textSecondary, fontFamily: 'monospace' }}>
              SPY: ${riskData.spy_price.toFixed(2)}
            </span>
          )}
        </div>
      </div>

      {/* Missing Data Banner */}
      {riskData && riskData.missing_data_count > 0 && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--s-2)',
            padding: 'var(--s-2) var(--s-3)',
            borderRadius: 'var(--r-sm)',
            background: 'rgba(245, 158, 11, 0.12)',
            color: theme.caution,
            fontSize: 'var(--t-caption)',
          }}
        >
          <AlertTriangle size={16} />
          <span>
            {riskData.missing_data_count} unresolvable position(s) excluded from totals ({riskData.missing_positions.join(', ')}).
          </span>
        </div>
      )}

      {/* Greeks KPI Grid */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
          gap: 'var(--s-3)',
        }}
      >
        {/* Net Delta */}
        <div
          style={{
            background: theme.surface2,
            border: `1px solid ${theme.border}`,
            borderRadius: 'var(--r-sm)',
            padding: 'var(--s-3)',
          }}
        >
          <div style={{ fontSize: 'var(--t-caption)', color: theme.textSecondary, marginBottom: 'var(--s-1)' }}>
            Net Delta (Δ)
          </div>
          <div style={{ fontSize: 'var(--t-body)', fontWeight: 600, color: theme.textPrimary, fontFamily: 'monospace' }}>
            {riskData ? `${riskData.net_delta > 0 ? '+' : ''}${riskData.net_delta.toFixed(1)} sh` : '--'}
          </div>
          <div style={{ fontSize: 'var(--t-caption)', color: riskData && riskData.net_dollar_delta >= 0 ? theme.growth : theme.decline, marginTop: 'var(--s-1)' }}>
            {riskData ? formatDollar(riskData.net_dollar_delta) : '--'}
          </div>
        </div>

        {/* Beta-Weighted SPY Delta */}
        <div
          style={{
            background: theme.surface2,
            border: `1px solid ${theme.border}`,
            borderRadius: 'var(--r-sm)',
            padding: 'var(--s-3)',
          }}
        >
          <div style={{ fontSize: 'var(--t-caption)', color: theme.textSecondary, marginBottom: 'var(--s-1)' }}>
            Beta-SPY Delta (βΔ)
          </div>
          <div style={{ fontSize: 'var(--t-body)', fontWeight: 600, color: theme.textPrimary, fontFamily: 'monospace' }}>
            {riskData ? `${riskData.beta_weighted_delta_spy > 0 ? '+' : ''}${riskData.beta_weighted_delta_spy.toFixed(1)} SPY` : '--'}
          </div>
          <div style={{ fontSize: 'var(--t-caption)', color: theme.textSecondary, marginTop: 'var(--s-1)' }}>
            Weighted exposure
          </div>
        </div>

        {/* Net Gamma */}
        <div
          style={{
            background: theme.surface2,
            border: `1px solid ${theme.border}`,
            borderRadius: 'var(--r-sm)',
            padding: 'var(--s-3)',
          }}
        >
          <div style={{ fontSize: 'var(--t-caption)', color: theme.textSecondary, marginBottom: 'var(--s-1)' }}>
            Net Gamma (Γ)
          </div>
          <div style={{ fontSize: 'var(--t-body)', fontWeight: 600, color: theme.textPrimary, fontFamily: 'monospace' }}>
            {riskData ? `${riskData.net_gamma > 0 ? '+' : ''}${riskData.net_gamma.toFixed(4)}` : '--'}
          </div>
          <div style={{ fontSize: 'var(--t-caption)', color: theme.textSecondary, marginTop: 'var(--s-1)' }}>
            {riskData ? `+$${riskData.net_dollar_gamma_1pct.toFixed(1)} / 1%` : '--'}
          </div>
        </div>

        {/* Net Theta */}
        <div
          style={{
            background: theme.surface2,
            border: `1px solid ${theme.border}`,
            borderRadius: 'var(--r-sm)',
            padding: 'var(--s-3)',
          }}
        >
          <div style={{ fontSize: 'var(--t-caption)', color: theme.textSecondary, marginBottom: 'var(--s-1)' }}>
            Net Theta (Θ)
          </div>
          <div
            style={{
              fontSize: 'var(--t-body)',
              fontWeight: 600,
              color: riskData && riskData.net_theta >= 0 ? theme.growth : theme.decline,
              fontFamily: 'monospace',
            }}
          >
            {riskData ? `${formatDollar(riskData.net_theta)}/day` : '--'}
          </div>
          <div style={{ fontSize: 'var(--t-caption)', color: theme.textSecondary, marginTop: 'var(--s-1)' }}>
            Decay velocity
          </div>
        </div>

        {/* Net Vega */}
        <div
          style={{
            background: theme.surface2,
            border: `1px solid ${theme.border}`,
            borderRadius: 'var(--r-sm)',
            padding: 'var(--s-3)',
          }}
        >
          <div style={{ fontSize: 'var(--t-caption)', color: theme.textSecondary, marginBottom: 'var(--s-1)' }}>
            Net Vega (𝒱)
          </div>
          <div style={{ fontSize: 'var(--t-body)', fontWeight: 600, color: theme.textPrimary, fontFamily: 'monospace' }}>
            {riskData ? `${formatDollar(riskData.net_vega)} / 1% IV` : '--'}
          </div>
          <div style={{ fontSize: 'var(--t-caption)', color: theme.textSecondary, marginTop: 'var(--s-1)' }}>
            Vol sensitivity
          </div>
        </div>
      </div>

      {/* Positions Breakdown Table */}
      {riskData && riskData.positions.length > 0 && (
        <div style={{ overflowX: 'auto', marginTop: 'var(--s-2)' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--t-caption)', textAlign: 'left' }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${theme.border}`, color: theme.textSecondary }}>
                <th style={{ padding: 'var(--s-2)' }}>Position</th>
                <th style={{ padding: 'var(--s-2)' }}>Qty</th>
                <th style={{ padding: 'var(--s-2)' }}>Spot</th>
                <th style={{ padding: 'var(--s-2)' }}>Delta (Δ)</th>
                <th style={{ padding: 'var(--s-2)' }}>$ Delta</th>
                <th style={{ padding: 'var(--s-2)' }}>Theta (Θ)</th>
                <th style={{ padding: 'var(--s-2)' }}>Vega (𝒱)</th>
              </tr>
            </thead>
            <tbody>
              {riskData.positions.map((pos, idx) => (
                <tr key={`${pos.symbol}-${idx}`} style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 'var(--s-2)', fontWeight: 500, color: theme.textPrimary }}>
                    {pos.symbol}
                    {pos.position_type === 'option' && pos.dte !== undefined && (
                      <span style={{ marginLeft: 'var(--s-1)', color: theme.textSecondary }}>({pos.dte}d)</span>
                    )}
                  </td>
                  <td style={{ padding: 'var(--s-2)', fontFamily: 'monospace' }}>{pos.qty}</td>
                  <td style={{ padding: 'var(--s-2)', fontFamily: 'monospace' }}>${pos.spot_price.toFixed(2)}</td>
                  <td style={{ padding: 'var(--s-2)', fontFamily: 'monospace' }}>{pos.delta.toFixed(1)}</td>
                  <td style={{ padding: 'var(--s-2)', fontFamily: 'monospace' }}>{formatDollar(pos.dollar_delta)}</td>
                  <td style={{ padding: 'var(--s-2)', fontFamily: 'monospace', color: pos.theta_daily >= 0 ? theme.growth : theme.decline }}>
                    {pos.theta_daily !== 0 ? `${formatDollar(pos.theta_daily)}/d` : '0.00'}
                  </td>
                  <td style={{ padding: 'var(--s-2)', fontFamily: 'monospace' }}>
                    {pos.vega_1pct !== 0 ? formatDollar(pos.vega_1pct) : '0.00'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
