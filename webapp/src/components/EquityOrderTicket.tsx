import React, { useState, useEffect } from 'react';
import { Toggle } from './Toggle';
import { theme, alpha } from '../theme';
import { fmtUsd } from '../format';
import { api } from '../api/client';
import { Modal } from './Modal';
import { loadUniverse } from './universeCache';

interface Props {
  symbol: string;
  // `null`/`undefined` while the caller's own spot-price fetch hasn't
  // resolved yet -- must never be silently treated as a verified $0.00 quote.
  spotPrice?: number | null;
  initialAction?: 'Buy' | 'Sell';
  onClear: () => void;
}

/**
 * EquityOrderTicket -- the options-free equity counterpart to
 * OptionsOrderTicket.tsx's `assetType="stock"` mode. Extracted so Quick Trade
 * (Paper Broker's "any symbol" panel) has no dependency on options code,
 * which is being archived. Posts to `POST /pilots/paper-broker/order` via
 * `api.postPaperEquityOrder` (`pilots/paper_equity_order.py`) instead of the
 * combined options-desk endpoint.
 */
export const EquityOrderTicket: React.FC<Props> = ({
  symbol,
  spotPrice = null,
  initialAction = 'Buy',
  onClear,
}) => {
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
  // Visible failure state for the "+ Add to Watchlist" flow -- this used to
  // be console.error-only, so a 409 watchlist_env_precedence or a missing
  // FOLLOW_API_TOKEN failed with zero on-screen feedback.
  const [watchlistError, setWatchlistError] = useState<string | null>(null);
  // Secondary, non-blocking note for the spot-data-download that fires
  // alongside a successful watchlist add -- a backfill hiccup never rolls
  // back the "Added to Watchlist" confirmation itself (CONSTRAINT #6).
  const [backfillNote, setBackfillNote] = useState<string | null>(null);
  // Inline "this symbol isn't tracked yet" prompt after a successful fill --
  // an explicit user action (Add / Not now), never a silent auto-add.
  const [notTracked, setNotTracked] = useState(false);
  const [addPromptDismissed, setAddPromptDismissed] = useState(false);

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

  const defaultPrice = spotPrice || 10.0;

  // Initialize limit price when switching order type or price changes
  useEffect(() => {
    if (limitPrice === 0) {
      setLimitPrice(+(defaultPrice).toFixed(2));
    }
  }, [defaultPrice, limitPrice]);

  const effectivePrice = orderType === 'limit' && limitPrice > 0 ? limitPrice : defaultPrice;

  // Sizing and derived calculations -- mirrors
  // pilots/paper_equity_order.py::execute_equity_order's fill math exactly
  // (dollar sizing -> fractional shares via calculate_stock_sizing;
  // commission $0.005/share, $1.00 minimum).
  let derivedQuantity: number;
  if (sizingMode === 'dollar') {
    derivedQuantity = Math.max(0.0001, +(dollarAmount / Math.max(0.01, effectivePrice)).toFixed(4));
  } else {
    // Fractional shares allowed: dollar-sized buys create fractional
    // positions, and selling one outright needs a fractional quantity.
    derivedQuantity = Math.max(0.0001, +quantity.toFixed(4));
  }
  const commission = Math.max(1.0, +(derivedQuantity * 0.005).toFixed(2));
  const isSell = stockAction === 'Sell';
  // Matches the backend: a buy costs notional + commission; a sell yields
  // notional - commission.
  const estimatedTotal = isSell
    ? (derivedQuantity * effectivePrice) - commission
    : (derivedQuantity * effectivePrice) + commission;

  // Cash only gates buys -- a sell raises cash.
  const isInsufficientCash = !isSell && !isLive && availableCash !== null && estimatedTotal > availableCash;

  const title = `${stockAction} ${symbol} Stock`;

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
      const res = await api.postPaperEquityOrder({
        symbol,
        side: stockAction.toLowerCase() as 'buy' | 'sell',
        quantity: sizingMode === 'quantity' ? quantity : undefined,
        dollar_amount: sizingMode === 'dollar' ? dollarAmount : undefined,
        order_type: orderType,
        limit_price: orderType === 'limit' ? limitPrice : undefined,
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
      // loadUniverse() never rejects -- it degrades to [] on a fetch failure
      // (see components/universeCache.ts) -- so no try/catch needed here.
      const universe = await loadUniverse();
      const isTracked = universe.some((u) => u.symbol === symbol.toUpperCase());
      if (isTracked) {
        setTimeout(() => {
          setSubmitted(false);
          onClear();
        }, 2000);
      } else {
        // Don't auto-clear: give the operator a chance to see and act on
        // the "not tracked yet" prompt below instead of it vanishing in 2s.
        setNotTracked(true);
      }
    }
  };

  const handleAddToWatchlist = async () => {
    if (watchlistAdded || watchlistLoading) return;
    setWatchlistLoading(true);
    setWatchlistError(null);
    try {
      const res = await api.watchCandidate(symbol);
      if (res && res.symbol) {
        setWatchlistAdded(true);
        // Non-blocking: a backfill hiccup never undoes the watchlist-add
        // confirmation above -- surfaced as its own secondary note
        // (CONSTRAINT #6), not rolled back into an error state.
        api.triggerSymbolBackfill(symbol)
          .then((backfill) => {
            setBackfillNote(
              backfill.status === 'ok'
                ? `Backfilled ${backfill.rows_persisted} bars of price history.`
                : `Added to your universe, but price history isn't available yet for ${backfill.symbol}.`
            );
          })
          .catch((err) => {
            setBackfillNote(
              `Added to your universe, but the price-history backfill failed: ${err instanceof Error ? err.message : 'unknown error'}`
            );
          });
      }
    } catch (err) {
      setWatchlistError(err instanceof Error ? err.message : 'Failed to add to watchlist.');
    } finally {
      setWatchlistLoading(false);
    }
  };

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
            Spot Price: {spotPrice != null ? fmtUsd(spotPrice) : '—'}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 500, color: isLive ? theme.textSecondary : theme.accent }}>Paper</span>
          <Toggle checked={isLive} onChange={setIsLive} label="Toggle Live" />
          <span style={{ fontSize: 13, fontWeight: 500, color: isLive ? theme.decline : theme.textSecondary }}>Live</span>
        </div>
      </div>

      {/* Stock Buy / Sell Selector */}
      <div style={{ display: 'flex', gap: 8 }}>
        {(['Buy', 'Sell'] as const).map(act => (
          <button
            key={act}
            onClick={() => setStockAction(act)}
            style={{
              flex: 1,
              padding: '8px 0',
              background: stockAction === act
                ? (act === 'Buy' ? alpha(theme.growth, "25") : alpha(theme.decline, "25"))
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

      {/* Sizing Mode Selector (Dollar Amount vs Shares) */}
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
              By Shares
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
                    background: dollarAmount === val ? alpha(theme.accent, "30") : theme.base,
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
                  {derivedQuantity} shares
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
                onClick={() => setQuantity(Math.max(1, +(quantity - 1).toFixed(4)))}
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
                min={0.0001}
                step="any"
                value={quantity}
                onChange={(e) => { const v = parseFloat(e.target.value); setQuantity(Number.isFinite(v) && v > 0 ? v : 1); }}
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
                onClick={() => setQuantity(+(quantity + 1).toFixed(4))}
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
              <span>Unit: 1 Share</span>
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
          <span style={{ fontWeight: 600, color: theme.textPrimary }}>{isSell ? 'Estimated Proceeds:' : 'Estimated Total Cost:'}</span>
          <span style={{ fontWeight: 700, color: isInsufficientCash ? theme.decline : theme.growth }}>
            {fmtUsd(estimatedTotal)}
          </span>
        </div>
      </div>

      {isInsufficientCash && (
        <div style={{
          padding: '8px 12px',
          borderRadius: 8,
          background: alpha(theme.decline, "15"),
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
                ? `Live ${stockAction} (Advisory Review)`
                : `Paper ${stockAction} ${fmtUsd(estimatedTotal)} (${derivedQuantity} shares)`}
        </button>

        {submitError && (
          <div style={{
            padding: '10px 12px',
            borderRadius: 8,
            background: alpha(theme.decline, "15"),
            border: `1px solid ${theme.decline}`,
            color: theme.decline,
            fontSize: 13,
            fontWeight: 500,
          }}>
            Order failed: {submitError}
          </div>
        )}

        {submitted && notTracked && !addPromptDismissed && !watchlistAdded && (
          <div
            data-testid="not-tracked-prompt"
            style={{
              padding: '10px 12px',
              borderRadius: 8,
              background: alpha(theme.accent, "15"),
              border: `1px solid ${theme.accent}`,
              color: theme.textPrimary,
              fontSize: 13,
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            <span>
              {symbol} isn't in your tracked universe — add it so future signals and models see it?
            </span>
            <div style={{ display: 'flex', gap: 16 }}>
              <button
                onClick={async () => {
                  await handleAddToWatchlist();
                  setNotTracked(false);
                  onClear();
                }}
                disabled={watchlistLoading}
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: theme.accent,
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: watchlistLoading ? 'default' : 'pointer',
                  padding: 0,
                }}
              >
                {watchlistLoading ? 'Adding...' : 'Add'}
              </button>
              <button
                onClick={() => {
                  setAddPromptDismissed(true);
                  setNotTracked(false);
                  onClear();
                }}
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: theme.textSecondary,
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: 'pointer',
                  padding: 0,
                }}
              >
                Not now
              </button>
            </div>
          </div>
        )}

        {watchlistError && (
          <div style={{
            padding: '10px 12px',
            borderRadius: 8,
            background: alpha(theme.decline, "15"),
            border: `1px solid ${theme.decline}`,
            color: theme.decline,
            fontSize: 13,
            fontWeight: 500,
          }}>
            Couldn't add {symbol} to your watchlist: {watchlistError}
          </div>
        )}

        {backfillNote && (
          <div style={{
            padding: '8px 12px',
            borderRadius: 8,
            fontSize: 12,
            color: theme.textSecondary,
          }}>
            {backfillNote}
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
              <strong>{stockAction.toLowerCase()} {derivedQuantity} shares</strong> on {symbol}.
              <br/><br/>
              Total estimated notional: <strong>{fmtUsd(estimatedTotal)}</strong>.
              <br/><br/>
              This order will be sent to the brokerage integration for placement, subject to
              the advisory-only constraints noted below.
            </p>

            <div style={{ padding: '14px', background: alpha(theme.decline, "15"), border: `1px solid ${theme.decline}`, borderRadius: 8 }}>
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
