import React, { useState, useEffect } from 'react';
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
  expiration?: string;
  legs?: SelectedLeg[];
  assetType?: 'option' | 'stock';
  spotPrice?: number;
  initialAction?: 'Buy' | 'Sell';
  onClear: () => void;
}

export const OptionsOrderTicket: React.FC<Props> = ({
  symbol,
  expiration = '',
  legs = [],
  assetType = 'option',
  spotPrice = 0,
  initialAction = 'Buy',
  onClear,
}) => {
  const isStock = assetType === 'stock' || legs.length === 0;
  const [stockAction, setStockAction] = useState<'Buy' | 'Sell'>(initialAction);
  const [sizingMode, setSizingMode] = useState<'dollar' | 'quantity'>('dollar');
  const [dollarAmount, setDollarAmount] = useState<number>(500);
  const [quantity, setQuantity] = useState<number>(1);
  const [orderType, setOrderType] = useState<'market' | 'limit'>('market');
  const [limitPrice, setLimitPrice] = useState<number>(0);
  
  const [isLive, setIsLive] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [showLiveModal, setShowLiveModal] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [availableCash, setAvailableCash] = useState<number | null>(null);
  const [watchlistAdded, setWatchlistAdded] = useState(false);
  const [watchlistLoading, setWatchlistLoading] = useState(false);

  // Fetch Paper Account Cash
  useEffect(() => {
    let active = true;
    api.getPaperBrokerAccount()
      .then(acc => {
        if (active && acc) {
          setAvailableCash(acc.cash);
        }
      })
      .catch(() => {
        // Ignore error in read-only / offline
      });
    return () => { active = false; };
  }, []);

  // Aggregate metrics for multi-leg option
  const isMultiLeg = !isStock && legs.length > 1;
  const primaryLeg = legs[0] || null;

  let netCost = 0;
  let netMark = 0;
  let aggDelta = 0, aggGamma = 0, aggTheta = 0, aggVega = 0, aggRho = 0;

  if (!isStock && legs.length > 0) {
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
  }

  // Determine base price for calculations
  const defaultOptionPrice = isMultiLeg 
    ? Math.abs(netCost) || 0.05
    : primaryLeg
      ? (primaryLeg.action === 'Buy' ? (primaryLeg.contract.ask || primaryLeg.contract.lastPrice || 0.05) : (primaryLeg.contract.bid || primaryLeg.contract.lastPrice || 0.05))
      : 0.05;

  const defaultPrice = isStock ? (spotPrice || 10.0) : defaultOptionPrice;

  // Initialize limit price when switching order type or price changes
  useEffect(() => {
    if (limitPrice === 0) {
      setLimitPrice(+(defaultPrice).toFixed(2));
    }
  }, [defaultPrice, limitPrice]);

  const effectivePrice = orderType === 'limit' && limitPrice > 0 ? limitPrice : defaultPrice;

  // Sizing and derived calculations
  let derivedQuantity = 1;
  let estimatedTotal = 0;
  let commission = 0;

  if (isStock) {
    if (sizingMode === 'dollar') {
      derivedQuantity = Math.max(1, +(dollarAmount / Math.max(0.01, effectivePrice)).toFixed(2));
      estimatedTotal = derivedQuantity * effectivePrice;
    } else {
      derivedQuantity = Math.max(1, quantity);
      estimatedTotal = derivedQuantity * effectivePrice;
    }
  } else {
    // Option: 1 contract = 100 shares
    const costPerContract = Math.max(0.01, effectivePrice) * 100;
    if (sizingMode === 'dollar') {
      derivedQuantity = Math.max(1, Math.floor(dollarAmount / Math.max(0.01, costPerContract)));
      commission = 0.65 * derivedQuantity * Math.max(1, legs.length);
      estimatedTotal = (derivedQuantity * costPerContract) + commission;
    } else {
      derivedQuantity = Math.max(1, quantity);
      commission = 0.65 * derivedQuantity * Math.max(1, legs.length);
      estimatedTotal = (derivedQuantity * costPerContract) + commission;
    }
  }

  const isInsufficientCash = !isLive && availableCash !== null && estimatedTotal > availableCash;

  // Title formatting
  const actionLabel = isStock ? stockAction : (primaryLeg ? primaryLeg.action : 'Buy');
  const title = isStock
    ? `${stockAction} ${symbol} Stock`
    : isMultiLeg 
      ? `${legs.length}-Leg Strategy on ${symbol}`
      : primaryLeg
        ? `${primaryLeg.action} ${symbol} ${fmtUsd(primaryLeg.contract.strike)} ${primaryLeg.type === 'call' ? 'Call' : 'Put'} ${expiration}`
        : `${actionLabel} ${symbol} Option`;

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
    setSubmitError(null);

    let ok = false;
    try {
      const res = await api.postOptionsOrder({
        symbol,
        asset_type: isStock ? 'stock' : 'option',
        side: isStock ? (stockAction.toLowerCase() as 'buy' | 'sell') : (primaryLeg?.action?.toLowerCase() as 'buy' | 'sell' || 'buy'),
        quantity: sizingMode === 'quantity' ? quantity : derivedQuantity,
        dollar_amount: sizingMode === 'dollar' ? dollarAmount : undefined,
        order_type: orderType,
        limit_price: orderType === 'limit' ? limitPrice : undefined,
        expiration: isStock ? undefined : expiration,
        legs: isStock ? undefined : legs,
        isLive,
      });
      console.log(`[Order Execution] ${isLive ? 'LIVE' : 'PAPER'} for ${symbol}:`, res);
      ok = res.ok;
      if (!ok) setSubmitError(res.message || "Order was rejected.");
    } catch (e) {
      console.error("Order failed:", e);
      setSubmitError(e instanceof Error ? e.message : "Order failed.");
    }

    setIsSubmitting(false);
    if (ok) {
      setSubmitted(true);
      // Refresh available cash
      api.getPaperBrokerAccount().then(acc => acc && setAvailableCash(acc.cash)).catch(() => {});
      setTimeout(() => {
        setSubmitted(false);
        onClear();
      }, 2000);
    }
  };

  const handleAddToWatchlist = async () => {
    if (watchlistAdded || watchlistLoading) return;
    setWatchlistLoading(true);
    try {
      const res = await api.watchCandidate(symbol);
      if (res && res.symbol) {
        setWatchlistAdded(true);
      }
    } catch (err) {
      console.error('Failed to add to watchlist:', err);
    } finally {
      setWatchlistLoading(false);
    }
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
      padding: '20px 20px',
      display: 'flex',
      flexDirection: 'column',
      gap: 16,
      maxWidth: 520,
      margin: '0 auto',
      maxHeight: '85vh',
      overflowY: 'auto'
    }}>
      {/* Header and Toggle */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>{title}</h2>
          <span style={{ fontSize: 12, color: theme.textSecondary }}>
            {isStock ? `Spot Price: ${fmtUsd(spotPrice)}` : `Expiration: ${expiration || 'N/A'}`}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 500, color: isLive ? theme.textSecondary : theme.accent }}>Paper</span>
          <Toggle checked={isLive} onChange={setIsLive} label="Toggle Live" />
          <span style={{ fontSize: 13, fontWeight: 500, color: isLive ? theme.decline : theme.textSecondary }}>Live</span>
        </div>
      </div>

      {/* Stock Buy / Sell Selector (if Stock Mode) */}
      {isStock && (
        <div style={{ display: 'flex', gap: 8 }}>
          {(['Buy', 'Sell'] as const).map(act => (
            <button
              key={act}
              onClick={() => setStockAction(act)}
              style={{
                flex: 1,
                padding: '8px 0',
                background: stockAction === act 
                  ? (act === 'Buy' ? `${theme.growth}25` : `${theme.decline}25`)
                  : theme.surface2,
                color: stockAction === act 
                  ? (act === 'Buy' ? theme.growth : theme.decline)
                  : theme.textSecondary,
                border: `1px solid ${stockAction === act ? (act === 'Buy' ? theme.growth : theme.decline) : 'transparent'}`,
                borderRadius: 8,
                fontWeight: 600,
                fontSize: 14,
                cursor: 'pointer',
                transition: 'all 0.15s'
              }}
            >
              {act} {symbol}
            </button>
          ))}
        </div>
      )}

      {/* Sizing Mode Selector (Dollar Amount vs Quantity) */}
      <div style={{
        background: theme.surface2,
        borderRadius: 12,
        padding: '12px 14px',
        display: 'flex',
        flexDirection: 'column',
        gap: 12
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>Order Sizing</span>
          <div style={{ display: 'flex', background: theme.base, borderRadius: 8, padding: 2, border: `1px solid ${theme.border}` }}>
            <button
              onClick={() => setSizingMode('dollar')}
              style={{
                padding: '4px 10px',
                borderRadius: 6,
                background: sizingMode === 'dollar' ? theme.accent : 'transparent',
                color: sizingMode === 'dollar' ? '#000' : theme.textSecondary,
                border: 'none',
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'all 0.15s'
              }}
            >
              By Dollar ($)
            </button>
            <button
              onClick={() => setSizingMode('quantity')}
              style={{
                padding: '4px 10px',
                borderRadius: 6,
                background: sizingMode === 'quantity' ? theme.accent : 'transparent',
                color: sizingMode === 'quantity' ? '#000' : theme.textSecondary,
                border: 'none',
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'all 0.15s'
              }}
            >
              {isStock ? 'By Shares' : 'By Contracts'}
            </button>
          </div>
        </div>

        {/* Sizing Input Controls */}
        {sizingMode === 'dollar' ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{
                display: 'flex',
                alignItems: 'center',
                flex: 1,
                background: theme.base,
                border: `1px solid ${theme.border}`,
                borderRadius: 8,
                padding: '0 12px'
              }}>
                <span style={{ fontSize: 18, fontWeight: 700, color: theme.textSecondary, marginRight: 4 }}>$</span>
                <input
                  type="number"
                  inputMode="decimal"
                  min={1}
                  step={10}
                  value={dollarAmount}
                  onChange={(e) => setDollarAmount(Math.max(0, Number(e.target.value)))}
                  style={{
                    flex: 1,
                    background: 'transparent',
                    border: 'none',
                    color: theme.textPrimary,
                    fontSize: 18,
                    fontWeight: 700,
                    padding: '8px 0',
                    outline: 'none',
                    width: '100%'
                  }}
                />
              </div>
            </div>

            {/* Preset Amount Chips */}
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {[100, 250, 500, 1000, 2500].map(val => (
                <button
                  key={val}
                  onClick={() => setDollarAmount(val)}
                  style={{
                    flex: 1,
                    minWidth: 50,
                    padding: '4px 6px',
                    background: dollarAmount === val ? `${theme.accent}30` : theme.base,
                    border: `1px solid ${dollarAmount === val ? theme.accent : theme.border}`,
                    color: dollarAmount === val ? theme.accent : theme.textSecondary,
                    borderRadius: 6,
                    fontSize: 12,
                    fontWeight: 500,
                    cursor: 'pointer'
                  }}
                >
                  ${val}
                </button>
              ))}
              {availableCash !== null && availableCash > 0 && (
                <button
                  onClick={() => setDollarAmount(Math.floor(availableCash * 0.75))}
                  style={{
                    padding: '4px 8px',
                    background: theme.base,
                    border: `1px solid ${theme.border}`,
                    color: theme.growth,
                    borderRadius: 6,
                    fontSize: 12,
                    fontWeight: 600,
                    cursor: 'pointer'
                  }}
                >
                  75% Cash
                </button>
              )}
            </div>

            {/* Derived Quantity Display */}
            <div style={{ fontSize: 12, color: theme.textSecondary, display: 'flex', justifyContent: 'space-between', marginTop: 2 }}>
              <span>
                Calculated Sizing: <strong style={{ color: theme.textPrimary }}>
                  {derivedQuantity} {isStock ? 'shares' : `contract${derivedQuantity > 1 ? 's' : ''}`}
                </strong>
              </span>
              <span>
                Est. Total: <strong style={{ color: theme.textPrimary }}>{fmtUsd(estimatedTotal)}</strong>
              </span>
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <button
                onClick={() => setQuantity(Math.max(1, quantity - 1))}
                style={{
                  width: 40,
                  height: 38,
                  borderRadius: 8,
                  border: `1px solid ${theme.border}`,
                  background: theme.base,
                  color: theme.textPrimary,
                  fontSize: 18,
                  fontWeight: 600,
                  cursor: 'pointer'
                }}
              >
                -
              </button>
              <input
                type="number"
                min={1}
                step={1}
                value={quantity}
                onChange={(e) => setQuantity(Math.max(1, parseInt(e.target.value) || 1))}
                style={{
                  flex: 1,
                  textAlign: 'center',
                  background: theme.base,
                  border: `1px solid ${theme.border}`,
                  color: theme.textPrimary,
                  fontSize: 18,
                  fontWeight: 700,
                  borderRadius: 8,
                  padding: '8px 0',
                  outline: 'none'
                }}
              />
              <button
                onClick={() => setQuantity(quantity + 1)}
                style={{
                  width: 40,
                  height: 38,
                  borderRadius: 8,
                  border: `1px solid ${theme.border}`,
                  background: theme.base,
                  color: theme.textPrimary,
                  fontSize: 18,
                  fontWeight: 600,
                  cursor: 'pointer'
                }}
              >
                +
              </button>
            </div>
            <div style={{ fontSize: 12, color: theme.textSecondary, display: 'flex', justifyContent: 'space-between' }}>
              <span>{isStock ? 'Unit: 1 Share' : 'Unit: 1 Contract = 100 Shares'}</span>
              <span>
                Est. Total: <strong style={{ color: theme.textPrimary }}>{fmtUsd(estimatedTotal)}</strong>
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Order Type & Price Controls */}
      <div style={{
        background: theme.surface2,
        borderRadius: 12,
        padding: '12px 14px',
        display: 'flex',
        flexDirection: 'column',
        gap: 10
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>Order Type</span>
          <div style={{ display: 'flex', background: theme.base, borderRadius: 8, padding: 2, border: `1px solid ${theme.border}` }}>
            <button
              onClick={() => setOrderType('market')}
              style={{
                padding: '4px 10px',
                borderRadius: 6,
                background: orderType === 'market' ? theme.accent : 'transparent',
                color: orderType === 'market' ? '#000' : theme.textSecondary,
                border: 'none',
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer'
              }}
            >
              Market
            </button>
            <button
              onClick={() => setOrderType('limit')}
              style={{
                padding: '4px 10px',
                borderRadius: 6,
                background: orderType === 'limit' ? theme.accent : 'transparent',
                color: orderType === 'limit' ? '#000' : theme.textSecondary,
                border: 'none',
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer'
              }}
            >
              Limit
            </button>
          </div>
        </div>

        {orderType === 'limit' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 13, color: theme.textSecondary, width: 80 }}>Limit Price:</span>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              flex: 1,
              background: theme.base,
              border: `1px solid ${theme.border}`,
              borderRadius: 8,
              padding: '0 10px'
            }}>
              <span style={{ fontSize: 14, color: theme.textSecondary, marginRight: 4 }}>$</span>
              <input
                type="number"
                step={0.01}
                value={limitPrice}
                onChange={(e) => setLimitPrice(Math.max(0.01, Number(e.target.value)))}
                style={{
                  flex: 1,
                  background: 'transparent',
                  border: 'none',
                  color: theme.textPrimary,
                  fontSize: 15,
                  fontWeight: 600,
                  padding: '6px 0',
                  outline: 'none'
                }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Contract / Stock Details (if option mode) */}
      {!isStock && primaryLeg && (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', paddingBottom: 8, borderBottom: `1px solid ${theme.border}` }}>
            {isMultiLeg ? (
              <div style={{ display: 'flex', width: '100%', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', flexDirection: 'column' }}>
                  <span style={{ fontSize: 13, color: theme.textSecondary }}>Net {netCost > 0 ? 'Debit' : 'Credit'}</span>
                  <span style={{ fontSize: 20, fontWeight: 700, color: netCost > 0 ? theme.decline : theme.growth }}>
                    {fmtUsd(Math.abs(netCost))}
                  </span>
                  <span style={{ fontSize: 11, color: theme.textSecondary }}>x 100 per contract</span>
                </div>
              </div>
            ) : (
              <>
                <div style={{ display: 'flex', flexDirection: 'column' }}>
                  <span style={{ fontSize: 12, color: theme.textSecondary }}>Bid</span>
                  <span style={{ fontSize: 17, fontWeight: 700 }}>{fmtUsd(primaryLeg.contract.bid)}</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                  <span style={{ fontSize: 12, color: theme.textSecondary }}>Mark</span>
                  <span style={{ fontSize: 17, fontWeight: 700 }}>{fmtUsd(primaryLeg.contract.lastPrice || (primaryLeg.contract.bid + primaryLeg.contract.ask) / 2)}</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
                  <span style={{ fontSize: 12, color: theme.textSecondary }}>Ask</span>
                  <span style={{ fontSize: 17, fontWeight: 700 }}>{fmtUsd(primaryLeg.contract.ask)}</span>
                </div>
              </>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
            <DataRow label="IV" value={isMultiLeg ? "N/A" : `${fmtNum((primaryLeg.contract.impliedVolatility || 0) * 100, 2)}%`} />
            <DataRow label="Chance of profit" value={isMultiLeg ? "N/A" : `${fmtNum((primaryLeg.contract.greeks?.chanceOfProfit || 0) * 100, 2)}%`} />
            <DataRow label="Open Interest" value={isMultiLeg ? "N/A" : fmtNum(primaryLeg.contract.openInterest)} />
          </div>

          {/* Combined Greeks */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: theme.textSecondary }}>
              {isMultiLeg ? "Combined Greeks" : "The Greeks"}
            </span>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
              <DataRow label="Delta" value={fmtNum(isMultiLeg ? aggDelta : (primaryLeg.contract.greeks?.delta || 0), 3)} />
              <DataRow label="Gamma" value={fmtNum(isMultiLeg ? aggGamma : (primaryLeg.contract.greeks?.gamma || 0), 3)} />
              <DataRow label="Theta" value={fmtNum(isMultiLeg ? aggTheta : (primaryLeg.contract.greeks?.theta || 0), 3)} />
              <DataRow label="Vega" value={fmtNum(isMultiLeg ? aggVega : (primaryLeg.contract.greeks?.vega || 0), 3)} />
            </div>
          </div>
        </>
      )}

      {/* Available Cash & Financial Summary */}
      <div style={{
        background: theme.surface,
        border: `1px solid ${theme.border}`,
        borderRadius: 10,
        padding: '10px 14px',
        display: 'flex',
        flexDirection: 'column',
        gap: 6
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
          <span style={{ color: theme.textSecondary }}>Available Paper Cash:</span>
          <span style={{ fontWeight: 600, color: theme.textPrimary }}>
            {availableCash !== null ? fmtUsd(availableCash) : '—'}
          </span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14 }}>
          <span style={{ fontWeight: 600, color: theme.textPrimary }}>Estimated Total Cost:</span>
          <span style={{ fontWeight: 700, color: isInsufficientCash ? theme.decline : theme.growth }}>
            {fmtUsd(estimatedTotal)}
          </span>
        </div>
      </div>

      {isInsufficientCash && (
        <div style={{
          padding: '8px 12px',
          borderRadius: 8,
          background: `${theme.decline}15`,
          border: `1px solid ${theme.decline}`,
          color: theme.decline,
          fontSize: 12,
          fontWeight: 500
        }}>
          ⚠️ Insufficient paper cash balance ({fmtUsd(availableCash)} available). Reduce order size.
        </div>
      )}

      {/* Action Buttons */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 4 }}>
        <button
          onClick={handleSubmitClick}
          disabled={isSubmitting || submitted || isInsufficientCash}
          style={{
            background: submitted ? theme.growth : (isLive ? theme.decline : theme.growth),
            color: '#000',
            border: 'none',
            borderRadius: 20,
            padding: '14px',
            fontSize: 15,
            fontWeight: 700,
            cursor: (isSubmitting || submitted || isInsufficientCash) ? 'not-allowed' : 'pointer',
            opacity: (isSubmitting || isInsufficientCash) ? 0.6 : 1,
            transition: 'background 0.2s',
            width: '100%',
          }}
        >
          {submitted
            ? '✓ Order Executed'
            : isSubmitting
              ? 'Processing Order...'
              : isLive
                ? `Live ${actionLabel} (Advisory Review)`
                : `Paper ${actionLabel} ${fmtUsd(estimatedTotal)} (${derivedQuantity} ${isStock ? 'shares' : 'contracts'})`}
        </button>

        {submitError && (
          <div style={{
            padding: '10px 12px',
            borderRadius: 8,
            background: `${theme.decline}15`,
            border: `1px solid ${theme.decline}`,
            color: theme.decline,
            fontSize: 13,
            fontWeight: 500,
          }}>
            Order failed: {submitError}
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'center', gap: 24 }}>
          <button 
            onClick={handleAddToWatchlist}
            disabled={watchlistAdded || watchlistLoading}
            style={{
              background: 'transparent',
              border: 'none',
              color: watchlistAdded ? theme.growth : theme.accent,
              fontSize: 13,
              fontWeight: 600,
              cursor: watchlistAdded ? 'default' : 'pointer'
            }}
          >
            {watchlistLoading ? 'Adding...' : watchlistAdded ? '✓ Added to Watchlist' : '+ Add to Watchlist'}
          </button>
          <button 
            onClick={onClear}
            style={{
              background: 'transparent',
              border: 'none',
              color: theme.textSecondary,
              fontSize: 13,
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
              You are about to place a <strong>LIVE</strong> order to{' '}
              {isStock
                ? <strong>{stockAction.toLowerCase()} {derivedQuantity} shares</strong>
                : isMultiLeg
                  ? <strong>execute a {legs.length}-leg strategy ({legs.map(l => l.action).join(' / ')}) with {derivedQuantity} contracts</strong>
                  : <strong>{actionLabel.toLowerCase()} {derivedQuantity} contract(s)</strong>} on {symbol}.
              <br/><br/>
              Total estimated notional: <strong>{fmtUsd(estimatedTotal)}</strong>.
              <br/><br/>
              This order will be sent to the brokerage integration for placement, subject to
              the advisory-only constraints noted below.
            </p>
            
            <div style={{ padding: '14px', background: `${theme.decline}15`, border: `1px solid ${theme.decline}`, borderRadius: 8 }}>
              <span style={{ fontSize: 13, color: theme.decline, fontWeight: 600 }}>WARNING: ADVISORY ONLY MODE</span>
              <p style={{ margin: '6px 0 0 0', fontSize: 12, color: theme.textSecondary }}>
                Live order placement is currently subject to advisory constraints and human approval.
              </p>
            </div>

            <div style={{ display: 'flex', gap: 12, marginTop: 8 }}>
              <button
                onClick={() => setShowLiveModal(false)}
                style={{
                  flex: 1,
                  padding: '10px',
                  background: 'transparent',
                  border: `1px solid ${theme.border}`,
                  color: theme.textPrimary,
                  borderRadius: 18,
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
                  padding: '10px',
                  background: theme.decline,
                  border: 'none',
                  color: '#000',
                  borderRadius: 18,
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
