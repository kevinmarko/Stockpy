import { useEffect, useState } from 'react';
import { Activity, Loader2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { useLiveTick } from '../hooks/useLiveTick';
import { api } from '../api/client';
import DemoDataBadge from './DemoDataBadge';
import { Button, Input, Select } from './ui';
import { CopyCommandBlock } from './CopyCommandBlock';

/**
 * The exact phrasing for a Claude Code invocation that places THIS specific
 * ad hoc order. Deliberately does NOT say "run the robinhood-execution
 * skill" -- that skill's documented procedure
 * (.claude/skills/robinhood-execution/SKILL.md) is strictly queue-driven: it
 * reads output/execution_queue.json, walks only the intents
 * execution/queue_builder.py already gated into that file, and explicitly
 * "never edits execution_queue.json -- the platform owns it". There is no
 * queue entry for an order the operator just composed by hand on this
 * ladder, so a "run robinhood-execution" command would send a future agent
 * session down a workflow that structurally cannot fulfill it (see
 * .claude/commands/rh-execute.md -- same queue-only contract). Instead this
 * asks the agent to use the robinhood-trading MCP directly, restating the
 * skill's own non-negotiable invariants (preview before place, confirm the
 * Agentic account, one explicit per-order human confirmation, honor the
 * kill switch) so an ad hoc order is held to the same bar as a queued one.
 */
function buildOrderCommand(
  action: 'BUY' | 'SELL',
  orderType: 'MARKET' | 'LIMIT',
  quantity: string,
  limitPrice: string,
  symbol: string
): string {
  const actionStr = action.toLowerCase();
  const typeStr = orderType === 'MARKET' ? 'market order' : `limit order at $${limitPrice}`;
  return (
    `Using the robinhood-trading MCP, preview a ${actionStr} ${typeStr} for ${quantity} shares of ${symbol} ` +
    `in my Agentic account (never the main account) -- honor the kill switch, show me the preview, and place it ` +
    `only after I explicitly confirm.`
  );
}

export default function ActiveTraderLadder({
  symbol = 'SPY',
  currentPrice = null,
}: {
  symbol?: string;
  currentPrice?: number | null;
}) {
  // Live top-of-book price/bid/ask over WebSocket (falls back to REST
  // polling server-side -- see api/ws_api.py -- and reconnects with
  // exponential backoff on drop).
  const tick = useLiveTick(symbol);

  // Depth ladder (bid/ask SIZES at each price level) via the real
  // GET /data/ladder/{symbol} endpoint. current_price there is a real quote
  // when available; the depth itself is synthetic (is_synthetic: true) --
  // this platform has no Level 2 / consolidated order book feed to compute
  // real depth from (Alpaca's free IEX feed and yfinance are both
  // top-of-book only). Never presented as real liquidity: see DemoDataBadge
  // below.
  const { data: ladder, loading, error } = useApi(() => api.getOrderBookLadder(symbol), [symbol]);

  // Prefer the live tick price (updates in real time); fall back to the
  // ladder response's own quote, then the caller-supplied snapshot price.
  const bestAsk = ladder?.asks?.[0]?.price ?? null;
  const bestBid = ladder?.bids?.[0]?.price ?? null;
  const spread = bestAsk !== null && bestBid !== null ? bestAsk - bestBid : null;
  const effectivePrice = tick.price ?? ladder?.current_price ?? currentPrice ?? null;

  const [quantity, setQuantity] = useState('100');
  const [orderType, setOrderType] = useState<'MARKET' | 'LIMIT'>('MARKET');
  const [limitPrice, setLimitPrice] = useState('');
  const [tradeAction, setTradeAction] = useState<'BUY' | 'SELL' | null>(null);

  // A fresh symbol means any staged order/limit price was composed against
  // a completely different price range (e.g. a $520 SPY limit surviving
  // verbatim onto a $12 name) -- drop the staged side/type/price. Quantity
  // is left alone: a share-count preference isn't symbol-specific.
  useEffect(() => {
    setTradeAction(null);
    setOrderType('MARKET');
    setLimitPrice('');
  }, [symbol]);

  const handlePriceClick = (price: number) => {
    setLimitPrice(price.toFixed(2));
    setOrderType('LIMIT');
  };

  const quantityNum = Number(quantity);
  const quantityValid = quantity.trim() !== '' && Number.isFinite(quantityNum) && quantityNum > 0;
  const limitPriceNum = Number(limitPrice);
  const limitPriceValid =
    orderType === 'MARKET' || (limitPrice.trim() !== '' && Number.isFinite(limitPriceNum) && limitPriceNum > 0);
  const canGenerateCommand = quantityValid && limitPriceValid;

  const command =
    tradeAction && canGenerateCommand ? buildOrderCommand(tradeAction, orderType, quantity, limitPrice, symbol) : '';

  const resetTrade = () => {
    setTradeAction(null);
    setOrderType('MARKET');
    setLimitPrice('');
    setQuantity('100');
  };

  return (
    <div className="card" style={{ overflow: "hidden", display: "flex", flexDirection: "column", height: "100%" }}>
      <div
        style={{
          padding: "var(--s-4)",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          background: "var(--surface-2)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <Activity style={{ width: 20, height: 20, color: "var(--accent)" }} />
          <h3 style={{ margin: 0, fontWeight: 600, color: "var(--text-primary)" }}>Active Trader Ladder</h3>
          {ladder?.is_synthetic && <DemoDataBadge />}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          {tick.isConnected && (
            <span
              title={`Live tick source: ${tick.source}`}
              style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--growth)", display: "inline-block" }}
            />
          )}
          <div
            style={{
              fontSize: "var(--t-body)",
              fontWeight: 600,
              color: "var(--text-secondary)",
              background: "var(--surface)",
              padding: "var(--s-1) var(--s-3)",
              borderRadius: "var(--r-sm)",
              border: "1px solid var(--border)",
            }}
          >
            {symbol}
          </div>
        </div>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "var(--s-4)" }}>
        {loading ? (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "var(--s-8)" }}>
            <Loader2 className="icon-spin" style={{ width: 24, height: 24, color: "var(--text-muted)" }} />
          </div>
        ) : error || !ladder || effectivePrice === null ? (
          <div style={{ textAlign: "center", color: "var(--text-secondary)", padding: "var(--s-8)", fontSize: "var(--t-body)" }}>
            Ladder unavailable for {symbol}.
          </div>
        ) : (
          <>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(3, 1fr)",
                gap: "var(--s-4)",
                marginBottom: "var(--s-2)",
                fontSize: "var(--t-caption)",
                fontWeight: 600,
                color: "var(--text-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.04em",
                textAlign: "center",
                borderBottom: "1px solid var(--border)",
                paddingBottom: "var(--s-2)",
              }}
            >
              <div>Bid Size</div>
              <div>Price</div>
              <div>Ask Size</div>
            </div>

            <div style={{ display: "flex", flexDirection: "column" }}>
              {/* Asks (descending price) */}
              {[...ladder.asks].reverse().map((ask, i) => (
                <div key={`ask-${i}`} className="ladder-row">
                  <div className="ladder-cell" style={{ color: "var(--text-muted)" }}>-</div>
                  <div className="ladder-cell">
                    <button
                      type="button"
                      data-testid={`ladder-ask-price-${i}`}
                      onClick={() => handlePriceClick(ask.price)}
                      title="Click to set limit price"
                      aria-label={`Set limit price to $${ask.price.toFixed(2)} (best ask)`}
                      style={{
                        width: "100%",
                        background: "none",
                        border: "none",
                        borderBottom: "1px dashed var(--decline)",
                        padding: 0,
                        font: "inherit",
                        fontWeight: 600,
                        color: "var(--decline)",
                        cursor: "pointer",
                      }}
                    >
                      ${ask.price.toFixed(2)}
                    </button>
                  </div>
                  <div className="ladder-cell">
                    <span style={{ position: "relative", zIndex: 1, color: "var(--text-secondary)" }}>{ask.size}</span>
                    <div
                      className="ladder-size-bar"
                      style={{
                        position: "absolute",
                        top: 0,
                        bottom: 0,
                        right: 0,
                        background: "rgba(239, 68, 68, 0.15)",
                        borderRadius: "var(--r-2xs)",
                        width: `${Math.min(100, (ask.size / 2000) * 100)}%`
                      }}
                    />
                  </div>
                </div>
              ))}

              {/* Current Price */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, 1fr)",
                  gap: "var(--s-4)",
                  padding: "var(--s-3) 0",
                  margin: "var(--s-2) 0",
                  fontSize: "var(--t-body)",
                  background: "rgba(56, 189, 248, 0.08)",
                  borderTop: "1px solid rgba(56, 189, 248, 0.22)",
                  borderBottom: "1px solid rgba(56, 189, 248, 0.22)",
                }}
              >
                <div />
                <div style={{ textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
                  <div style={{ fontWeight: 700, fontSize: "var(--t-subhead)", color: "var(--text-primary)" }}>
                    ${effectivePrice.toFixed(2)}
                  </div>
                  {spread !== null && (
                    <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginTop: 2 }}>
                      Spread: ${spread.toFixed(2)}
                    </div>
                  )}
                </div>
                <div />
              </div>

              {/* Bids (descending price) */}
              {ladder.bids.map((bid, i) => (
                <div key={`bid-${i}`} className="ladder-row">
                  <div className="ladder-cell">
                    <span style={{ position: "relative", zIndex: 1, color: "var(--text-secondary)" }}>{bid.size}</span>
                    <div
                      className="ladder-size-bar ladder-size-bar-bid"
                      style={{ width: `${Math.min(100, (bid.size / 2000) * 100)}%` }}
                    />
                  </div>
                  <div className="ladder-cell">
                    <button
                      type="button"
                      data-testid={`ladder-bid-price-${i}`}
                      onClick={() => handlePriceClick(bid.price)}
                      title="Click to set limit price"
                      aria-label={`Set limit price to $${bid.price.toFixed(2)} (best bid)`}
                      style={{
                        width: "100%",
                        background: "none",
                        border: "none",
                        borderBottom: "1px dashed var(--growth)",
                        padding: 0,
                        font: "inherit",
                        fontWeight: 600,
                        color: "var(--growth)",
                        cursor: "pointer",
                      }}
                    >
                      ${bid.price.toFixed(2)}
                    </button>
                  </div>
                  <div className="ladder-cell" style={{ color: "var(--text-muted)" }}>-</div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Trade Controls -- composes a paste-in-Claude-Code order command
          (via CopyCommandBlock); nothing here ever calls a broker directly.
          See buildOrderCommand's docstring for why the generated text does
          not claim to invoke the (queue-only) robinhood-execution skill. */}
      <div style={{ padding: "var(--s-4)", borderTop: "1px solid var(--border)", background: "var(--surface)" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--s-3)", marginBottom: "var(--s-3)" }}>
          <Input
            id="ladder-quantity-input"
            label="Quantity"
            type="number"
            inputMode="decimal"
            min={0}
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            invalid={!quantityValid}
            hint={quantityValid ? undefined : "Enter a quantity greater than 0."}
          />
          <Select
            id="ladder-order-type-select"
            label="Order Type"
            value={orderType}
            onChange={(e) => setOrderType(e.target.value as 'MARKET' | 'LIMIT')}
            options={[
              { value: 'MARKET', label: 'Market' },
              { value: 'LIMIT', label: 'Limit' },
            ]}
          />
        </div>
        {orderType === 'LIMIT' && (
          <div style={{ marginBottom: "var(--s-3)" }}>
            <Input
              id="ladder-limit-price-input"
              label="Limit Price"
              type="number"
              inputMode="decimal"
              min={0}
              step={0.01}
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
              placeholder="Click a price on the ladder"
              invalid={tradeAction !== null && !limitPriceValid}
              hint={tradeAction !== null && !limitPriceValid ? "Enter a limit price greater than 0." : undefined}
            />
          </div>
        )}

        <div style={{ display: "flex", gap: "var(--s-2)", marginBottom: tradeAction ? "var(--s-3)" : 0 }}>
          <Button
            variant="neutral"
            onClick={() => setTradeAction('BUY')}
            disabled={!quantityValid}
            aria-pressed={tradeAction === 'BUY'}
            data-testid="ladder-buy-button"
            style={{
              flex: 1,
              backgroundColor: tradeAction === 'BUY' ? "var(--growth)" : "var(--surface-2)",
              color: tradeAction === 'BUY' ? "#fff" : undefined,
              borderColor: tradeAction === 'BUY' ? "var(--growth)" : undefined,
            }}
          >
            Buy
          </Button>
          <Button
            variant="neutral"
            onClick={() => setTradeAction('SELL')}
            disabled={!quantityValid}
            aria-pressed={tradeAction === 'SELL'}
            data-testid="ladder-sell-button"
            style={{
              flex: 1,
              backgroundColor: tradeAction === 'SELL' ? "var(--decline)" : "var(--surface-2)",
              color: tradeAction === 'SELL' ? "#fff" : undefined,
              borderColor: tradeAction === 'SELL' ? "var(--decline)" : undefined,
            }}
          >
            Sell
          </Button>
          <Button variant="neutral" onClick={resetTrade} title="Reset" data-testid="ladder-reset-button">
            Reset
          </Button>
        </div>

        {tradeAction && (
          <div style={{ animation: "fadeIn 0.2s ease-in-out" }}>
            {canGenerateCommand ? (
              <CopyCommandBlock
                command={command}
                label="Agent Command (paste in Claude Code)"
                resetKey={command}
                testIdPrefix="ladder-order-command"
              />
            ) : (
              <div style={{ fontSize: "var(--t-caption)", color: "var(--decline)" }}>
                {!quantityValid
                  ? "Enter a valid quantity to generate the agent command."
                  : "Enter a valid limit price to generate the agent command."}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
