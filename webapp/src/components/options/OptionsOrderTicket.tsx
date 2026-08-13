import React, { useState } from 'react';
import { OptionContract } from '../../api/types';
import { Toggle } from '../Toggle';
import { theme } from '../../theme';
import { fmtNum, fmtUsd } from '../../format';
import { api } from '../../api/client';
import { Modal } from '../Modal';

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
  const [showLiveModal, setShowLiveModal] = useState(false);

  if (legs.length === 0) return null;

  // Aggregate metrics for multi-leg
  const isMultiLeg = legs.length > 1;
  const primaryLeg = legs[0];
  const { contract, action, type } = primaryLeg;

  let netCost = 0;
  let netMark = 0;
  let aggDelta = 0, aggGamma = 0, aggTheta = 0, aggVega = 0, aggRho = 0;

  legs.forEach(leg => {
    const mult = leg.action === 'Buy' ? 1 : -1;
    const c = leg.contract;
    netCost += (leg.action === 'Buy' ? c.ask : -c.bid);
    netMark += mult * (c.lastPrice > 0 ? c.lastPrice : (c.bid + c.ask) / 2);
    
    aggDelta += mult * (c.greeks?.delta || 0);
    aggGamma += mult * (c.greeks?.gamma || 0);
    aggTheta += mult * (c.greeks?.theta || 0);
    aggVega += mult * (c.greeks?.vega || 0);
    aggRho += mult * (c.greeks?.rho || 0);
  });

  // Title formatting: "Sell AGNC $11 Call 8/14"
  const formattedExp = expiration; 
  const title = isMultiLeg 
    ? `${legs.length}-Leg Strategy on ${symbol}`
    : `${action} ${symbol} ${fmtUsd(contract.strike)} ${type === 'call' ? 'Call' : 'Put'} ${formattedExp}`;

  const handleSubmitClick = () => {
    if (isLive) {
      setShowLiveModal(true);
    } else {
      executeOrder();
    }
  };

  const executeOrder = async () => {
    setIsSubmitting(true);
    setShowLiveModal(false);
    
    try {
      const res = await api.postOptionsOrder({
        symbol,
        expiration,
        legs,
        isLive
      });
      console.log(`[Options Order] ${isLive ? 'LIVE' : 'PAPER'} Execution for ${symbol}:`, res);
    } catch (e) {
      console.error("Order failed:", e);
    }

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

      {/* Bid / Ask Depth Chart or Net Cost */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', paddingBottom: 16, borderBottom: `1px solid ${theme.border}` }}>
        {isMultiLeg ? (
          <div style={{ display: 'flex', width: '100%', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              <span style={{ fontSize: 13, color: theme.textSecondary }}>Net {netCost > 0 ? 'Debit' : 'Credit'}</span>
              <span style={{ fontSize: 24, fontWeight: 700, color: netCost > 0 ? theme.decline : theme.growth }}>
                {fmtUsd(Math.abs(netCost))}
              </span>
              <span style={{ fontSize: 12, color: theme.textSecondary }}>x 100</span>
            </div>
          </div>
        ) : (
          <>
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
          </>
        )}
      </div>

      {/* Contract Details */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
        <DataRow label={isMultiLeg ? "Net mark" : "Mark"} value={fmtUsd(Math.abs(isMultiLeg ? netMark : (contract.lastPrice > 0 ? contract.lastPrice : (contract.bid + contract.ask)/2)))} />
        <DataRow label={isMultiLeg ? "Legs" : "Last trade"} value={isMultiLeg ? legs.length : fmtUsd(contract.lastPrice)} />
        <DataRow label="IV" value={isMultiLeg ? "N/A" : `${fmtNum((contract.impliedVolatility || 0) * 100, 2)}%`} />
        
        <DataRow label="Prev close" value={isMultiLeg ? "N/A" : fmtUsd(contract.lastPrice)} />
        <DataRow label="High" value={isMultiLeg ? "N/A" : fmtUsd(contract.lastPrice * 1.05)} />
        <DataRow label="Low" value={isMultiLeg ? "N/A" : fmtUsd(contract.lastPrice * 0.95)} />
        
        <DataRow label="Chance of profit" value={isMultiLeg ? "N/A" : `${fmtNum((contract.greeks?.chanceOfProfit || 0) * 100, 2)}%`} />
        <DataRow label="Volume" value={isMultiLeg ? "N/A" : fmtNum(contract.volume)} />
        <DataRow label="Open interest" value={isMultiLeg ? "N/A" : fmtNum(contract.openInterest)} />
      </div>

      {/* The Greeks */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 8 }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>{isMultiLeg ? "Combined Greeks" : "The Greeks"}</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
          <DataRow label="Delta" value={fmtNum(isMultiLeg ? aggDelta : (contract.greeks?.delta || 0), 4)} />
          <DataRow label="Gamma" value={fmtNum(isMultiLeg ? aggGamma : (contract.greeks?.gamma || 0), 4)} />
          <DataRow label="Theta" value={fmtNum(isMultiLeg ? aggTheta : (contract.greeks?.theta || 0), 4)} />
          <DataRow label="Vega" value={fmtNum(isMultiLeg ? aggVega : (contract.greeks?.vega || 0), 4)} />
          <DataRow label="Rho" value={fmtNum(isMultiLeg ? aggRho : (contract.greeks?.rho || 0), 4)} />
        </div>
      </div>

      {/* Action Buttons */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 16 }}>
        <button
          onClick={handleSubmitClick}
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
          {submitted ? 'Order Submitted' : isSubmitting ? 'Processing...' : (isMultiLeg ? `Execute Strategy` : (isLive ? `Live ${action}` : `Paper ${action}`))}
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

      {showLiveModal && (
        <Modal ariaLabel="Confirm Live Order" onClose={() => setShowLiveModal(false)}>
          <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <h2 style={{ margin: 0, fontSize: 20 }}>Confirm Live Order</h2>
            <p style={{ margin: 0, color: theme.textSecondary, lineHeight: 1.5 }}>
              You are about to place a <strong>LIVE</strong> order to {action.toLowerCase()} <strong>{isMultiLeg ? `${legs.length} legs` : `1 contract`}</strong> on {symbol}.
              <br/><br/>
              This will route to the execution queue for final brokerage submission.
            </p>
            
            <div style={{ padding: '16px', background: `${theme.decline}15`, border: `1px solid ${theme.decline}`, borderRadius: 8 }}>
              <span style={{ fontSize: 14, color: theme.decline, fontWeight: 600 }}>WARNING: ADVISORY ONLY MODE</span>
              <p style={{ margin: '8px 0 0 0', fontSize: 13, color: theme.textSecondary }}>
                Options order placement is currently subject to advisory constraints.
              </p>
            </div>

            <div style={{ display: 'flex', gap: 12, marginTop: 8 }}>
              <button
                onClick={() => setShowLiveModal(false)}
                style={{
                  flex: 1,
                  padding: '12px',
                  background: 'transparent',
                  border: `1px solid ${theme.border}`,
                  color: theme.textPrimary,
                  borderRadius: 20,
                  fontWeight: 600,
                  cursor: 'pointer'
                }}
              >
                Cancel
              </button>
              <button
                onClick={executeOrder}
                style={{
                  flex: 1,
                  padding: '12px',
                  background: theme.decline,
                  border: 'none',
                  color: '#000',
                  borderRadius: 20,
                  fontWeight: 700,
                  cursor: 'pointer'
                }}
              >
                Confirm Live Order
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
};
