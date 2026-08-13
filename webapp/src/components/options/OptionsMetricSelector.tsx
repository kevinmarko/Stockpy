import React from 'react';
import { theme } from '../../theme';
import { Toggle } from '../Toggle';
import { X } from 'lucide-react';

export type MetricColumn = 'volume' | 'openInterest' | 'impliedVolatility' | 'delta' | 'gamma' | 'theta' | 'vega' | 'rho' | 'chanceOfProfit';

interface Props {
  selectedMetrics: MetricColumn[];
  onChange: (metrics: MetricColumn[]) => void;
  onClose: () => void;
}

const AVAILABLE_METRICS: { id: MetricColumn, label: string }[] = [
  { id: 'volume', label: 'Volume' },
  { id: 'openInterest', label: 'Open Interest' },
  { id: 'impliedVolatility', label: 'Implied Volatility (IV)' },
  { id: 'chanceOfProfit', label: 'Chance of Profit (PoP)' },
  { id: 'delta', label: 'Delta' },
  { id: 'gamma', label: 'Gamma' },
  { id: 'theta', label: 'Theta' },
  { id: 'vega', label: 'Vega' },
  { id: 'rho', label: 'Rho' },
];

export const OptionsMetricSelector: React.FC<Props> = ({ selectedMetrics, onChange, onClose }) => {
  const toggleMetric = (metric: MetricColumn, isSelected: boolean) => {
    if (isSelected) {
      // Add if not present
      if (!selectedMetrics.includes(metric)) {
        onChange([...selectedMetrics, metric]);
      }
    } else {
      // Remove
      onChange(selectedMetrics.filter(m => m !== metric));
    }
  };

  return (
    <div style={{
      position: 'fixed',
      top: 0, left: 0, right: 0, bottom: 0,
      background: 'rgba(0,0,0,0.6)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 1000
    }}>
      <div style={{
        background: theme.surface,
        borderRadius: 16,
        border: `1px solid ${theme.border}`,
        width: '90%',
        maxWidth: 400,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden'
      }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: 16, borderBottom: `1px solid ${theme.border}` }}>
          <h3 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>Customize Columns</h3>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: theme.textSecondary, cursor: 'pointer' }}>
            <X size={20} />
          </button>
        </div>

        {/* List */}
        <div style={{ display: 'flex', flexDirection: 'column', padding: 16, gap: 16, maxHeight: '60vh', overflowY: 'auto' }}>
          {AVAILABLE_METRICS.map(metric => (
            <div key={metric.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 15, color: theme.textPrimary }}>{metric.label}</span>
              <Toggle 
                checked={selectedMetrics.includes(metric.id)} 
                onChange={(c) => toggleMetric(metric.id, c)} 
                label={`Toggle ${metric.label}`} 
              />
            </div>
          ))}
        </div>

        {/* Footer */}
        <div style={{ padding: 16, borderTop: `1px solid ${theme.border}`, background: theme.surface2 }}>
          <button
            onClick={onClose}
            style={{
              width: '100%',
              background: theme.accent,
              color: '#000',
              border: 'none',
              borderRadius: 8,
              padding: '12px',
              fontSize: 16,
              fontWeight: 600,
              cursor: 'pointer'
            }}
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
};
