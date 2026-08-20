import React from 'react';
import { OptionChainResponse, OptionContract } from '../../api/types';
import { DataTable, Column } from '../DataTable';
import { MetricColumn } from './OptionsMetricSelector';
import { theme } from '../../theme';
import { fmtNum, fmtUsd } from '../../format';

interface Props {
  data: OptionChainResponse;
  activeTab?: 'calls' | 'puts';
  onSelectContract?: (contract: OptionContract, type: 'call' | 'put') => void;
  selectedMetrics: MetricColumn[];
}

export const OptionsChain: React.FC<Props> = ({ data, activeTab, onSelectContract, selectedMetrics }) => {
  if (!data.calls && !data.puts) {
    return <div style={{ padding: 16, textAlign: 'center', color: theme.textMuted, fontStyle: 'italic' }}>No options data available for this expiration.</div>;
  }

  const columns = (type: 'call' | 'put'): Column<OptionContract>[] => {
    const baseCols: Column<OptionContract>[] = [
      {
        header: 'Strike',
        key: 'strike',
        render: (c: OptionContract) => (
          <span style={{ fontWeight: 500, color: c.inTheMoney ? theme.accent : 'inherit' }}>
            {fmtUsd(c.strike)}
          </span>
        )
      },
      { header: 'Bid', key: 'bid', render: (c: OptionContract) => fmtUsd(c.bid) },
      { header: 'Ask', key: 'ask', render: (c: OptionContract) => fmtUsd(c.ask) },
    ];

    const metricCols: Column<OptionContract>[] = [];
    
    if (selectedMetrics.includes('volume')) {
      metricCols.push({ header: 'Vol', key: 'volume', render: (c: OptionContract) => fmtNum(c.volume) });
    }
    if (selectedMetrics.includes('openInterest')) {
      metricCols.push({ header: 'OI', key: 'openInterest', render: (c: OptionContract) => fmtNum(c.openInterest) });
    }
    if (selectedMetrics.includes('impliedVolatility')) {
      metricCols.push({ header: 'IV', key: 'impliedVolatility', render: (c: OptionContract) => c.impliedVolatility != null ? `${fmtNum(c.impliedVolatility * 100, 1)}%` : '—' });
    }
    if (selectedMetrics.includes('chanceOfProfit')) {
      metricCols.push({ header: 'PoP', key: 'chanceOfProfit', render: (c: OptionContract) => `${fmtNum(c.greeks.chanceOfProfit * 100, 1)}%` });
    }
    if (selectedMetrics.includes('delta')) {
      metricCols.push({ header: 'Delta', key: 'delta', render: (c: OptionContract) => fmtNum(c.greeks.delta, 3) });
    }
    if (selectedMetrics.includes('gamma')) {
      metricCols.push({ header: 'Gamma', key: 'gamma', render: (c: OptionContract) => fmtNum(c.greeks.gamma, 3) });
    }
    if (selectedMetrics.includes('theta')) {
      metricCols.push({ header: 'Theta', key: 'theta', render: (c: OptionContract) => fmtNum(c.greeks.theta, 3) });
    }
    if (selectedMetrics.includes('vega')) {
      metricCols.push({ header: 'Vega', key: 'vega', render: (c: OptionContract) => fmtNum(c.greeks.vega, 3) });
    }
    if (selectedMetrics.includes('rho')) {
      metricCols.push({ header: 'Rho', key: 'rho', render: (c: OptionContract) => fmtNum(c.greeks.rho, 3) });
    }

    const actionCol: Column<OptionContract> = {
      header: '',
      key: 'contractSymbol',
      render: (c: OptionContract) => (
        <button
          onClick={(e) => { e.stopPropagation(); onSelectContract?.(c, type); }}
          style={{
            padding: '4px 8px',
            background: theme.surface3,
            color: theme.textPrimary,
            border: 'none',
            borderRadius: 4,
            fontSize: 12,
            cursor: 'pointer'
          }}
        >
          Select
        </button>
      )
    };

    return [...baseCols, ...metricCols, actionCol];
  };

  const showCalls = !activeTab || activeTab === 'calls';
  const showPuts = !activeTab || activeTab === 'puts';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, width: '100%' }}>
      {showCalls && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <h3 style={{ margin: 0, fontWeight: 600, fontSize: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
            Calls
            <span style={{ fontSize: 12, fontWeight: 400, color: theme.textSecondary, padding: '2px 8px', background: theme.surface3, borderRadius: 12 }}>
              {data.calls?.length || 0} contracts
            </span>
          </h3>
          <div style={{ background: theme.surface, borderRadius: 12, border: `1px solid ${theme.border}`, overflow: 'hidden' }}>
            <DataTable
              columns={columns('call')}
              data={data.calls || []}
              copyableJson={false}
            />
          </div>
        </div>
      )}

      {showPuts && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <h3 style={{ margin: 0, fontWeight: 600, fontSize: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
            Puts
            <span style={{ fontSize: 12, fontWeight: 400, color: theme.textSecondary, padding: '2px 8px', background: theme.surface3, borderRadius: 12 }}>
              {data.puts?.length || 0} contracts
            </span>
          </h3>
          <div style={{ background: theme.surface, borderRadius: 12, border: `1px solid ${theme.border}`, overflow: 'hidden' }}>
            <DataTable
              columns={columns('put')}
              data={data.puts || []}
              copyableJson={false}
            />
          </div>
        </div>
      )}
    </div>
  );
};
