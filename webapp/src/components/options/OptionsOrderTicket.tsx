import React, { useState } from 'react';
import { OptionContract } from '../../api/types';
import { Toggle } from '../Toggle';
import { theme } from '../../theme';
import { fmtNum, fmtUsd } from '../../format';

interface SelectedLeg {
  contract: OptionContract;
  type: 'call' | 'put';
  action: 'Buy' | 'Sell';
}

interface Props {
  symbol: string;
  expiration: string;
  legs: SelectedLeg[];
  onClear: () => void;
}

export const OptionsOrderTicket: React.FC<Props> = ({ symbol, expiration, legs, onClear }) => {
  const [isLive, setIsLive] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  if (legs.length === 0) return null;

  // Currently we handle single-leg for the exact layout match, or aggregate for multi-leg
  const isMultiLeg = legs.length > 1;
  const primaryLeg = legs[0];
  const { contract, action, type } = primaryLeg;

  // Title formatting: "Sell AGNC $11 Call 8/14"
  const formattedExp = expiration; // Assuming it's already a short date format, might need parsing otherwise
  const title = isMultiLeg 
    ? `${legs.length}-Leg Strategy on ${symbol}`
    : `${action} ${symbol} ${fmtUsd(contract.strike)} ${type === 'call' ? 'Call' : 'Put'} ${formattedExp}`;

  const handleSubmit = async () => {
    setIsSubmitting(true);
    // Simulate network request
    await new Promise(resolve => setTimeout(resolve, 800));
    console.log(`[Options Order] ${isLive ? 'LIVE' : 'PAPER'} Execution for ${symbol}`, {
      expiration, legs
    });
    setIsSubmitting(false);
    setSubmitted(true);
    setTimeout(() => {
      setSubmitted(false);
      onClear();
    }, 2000);
  };

  const DataRow = ({ label, value }: { label: string, value: string | React.ReactNode }) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 13, color: theme.textSecondary }}>{label}</span>
      <span style={{ fontSize: 15, fontWeight: 500, color: theme.textPrimary }}>{value}</span>
    </div>
  );

  return (
    <div style={{
      background: theme.base,
      borderTop: `1px solid ${theme.border}`,
      padding: '24px 20px',
      display: 'flex',
      flexDirection: 'column',
      gap: 24,
      maxWidth: 500,
      margin: '0 auto'
    }}>
      {/* Header and Toggle */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>{title}</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 500, color: isLive ? theme.textSecondary : theme.accent }}>Paper</span>
          <Toggle checked={isLive} onChange={setIsLive} label="Toggle Live" />
          <span style={{ fontSize: 13, fontWeight: 500, color: isLive ? theme.decline : theme.textSecondary }}>Live</span>
        </div>
      </div>

      {/* Bid / Ask Depth Chart (Mocked) */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', paddingBottom: 16, borderBottom: `1px solid ${theme.border}` }}>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontSize: 13, color: theme.textSecondary }}>Bid</span>
          <span style={{ fontSize: 20, fontWeight: 700 }}>{fmtUsd(contract.bid)}</span>
          <span style={{ fontSize: 12, color: theme.textSecondary }}>x 100</span>
        </div>
        
        {/* Mock Depth Bars */}
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8, height: 40, paddingBottom: 8 }}>
          <div style={{ width: 32, height: 40, border: `1px solid ${theme.growth}`, background: `${theme.growth}20` }} />
          <div style={{ width: 32, height: 4, background: theme.caution }} />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
          <span style={{ fontSize: 13, color: theme.textSecondary }}>Ask</span>
          <span style={{ fontSize: 20, fontWeight: 700 }}>{fmtUsd(contract.ask)}</span>
          <span style={{ fontSize: 12, color: theme.textSecondary }}>x 1</span>
        </div>
      </div>

      {/* Contract Details */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
        <DataRow label="Mark" value={fmtUsd(contract.lastPrice > 0 ? contract.lastPrice : (contract.bid + contract.ask)/2)} />
        <DataRow label="Last trade" value={fmtUsd(contract.lastPrice)} />
        <DataRow label="IV" value={`${fmtNum((contract.impliedVolatility || 0) * 100, 2)}%`} />
        
        <DataRow label="Prev close" value={fmtUsd(contract.lastPrice)} />
        <DataRow label="High" value={fmtUsd(contract.lastPrice * 1.05)} />
        <DataRow label="Low" value={fmtUsd(contract.lastPrice * 0.95)} />
        
        <DataRow label="Chance of profit" value={`${fmtNum((contract.greeks?.chanceOfProfit || 0) * 100, 2)}%`} />
        <DataRow label="Volume" value={fmtNum(contract.volume)} />
        <DataRow label="Open interest" value={fmtNum(contract.openInterest)} />
      </div>

      {/* The Greeks */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 8 }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>The Greeks</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
          <DataRow label="Delta" value={fmtNum(contract.greeks?.delta || 0, 4)} />
          <DataRow label="Gamma" value={fmtNum(contract.greeks?.gamma || 0, 4)} />
          <DataRow label="Theta" value={fmtNum(contract.greeks?.theta || 0, 4)} />
          <DataRow label="Vega" value={fmtNum(contract.greeks?.vega || 0, 4)} />
          <DataRow label="Rho" value={fmtNum(contract.greeks?.rho || 0, 4)} />
        </div>
      </div>

      {/* Action Buttons */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 16 }}>
        <button
          onClick={handleSubmit}
          disabled={isSubmitting || submitted}
          style={{
            background: submitted ? theme.growth : (isLive ? theme.decline : theme.growth),
            color: '#000',
            border: 'none',
            borderRadius: 24,
            padding: '16px',
            fontSize: 16,
            fontWeight: 700,
            cursor: (isSubmitting || submitted) ? 'not-allowed' : 'pointer',
            opacity: isSubmitting ? 0.7 : 1,
            transition: 'background 0.2s',
            width: '100%',
          }}
        >
          {submitted ? 'Order Submitted' : isSubmitting ? 'Processing...' : (isLive ? `Live ${action}` : `Paper ${action}`)}
        </button>
        
        <div style={{ display: 'flex', justifyContent: 'center', gap: 24 }}>
          <button 
            onClick={() => console.log('Added to watchlist')}
            style={{
              background: 'transparent',
              border: 'none',
              color: theme.growth,
              fontSize: 14,
              fontWeight: 600,
              cursor: 'pointer'
            }}
          >
            Add to Watchlist
          </button>
          <button 
            onClick={onClear}
            style={{
              background: 'transparent',
              border: 'none',
              color: theme.textSecondary,
              fontSize: 14,
              fontWeight: 600,
              cursor: 'pointer'
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
};
