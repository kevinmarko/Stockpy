import { useState } from 'react';
import { Activity, Loader2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { useLiveTick } from '../hooks/useLiveTick';
import { api } from '../api/client';
import DemoDataBadge from './DemoDataBadge';
import { Button } from './ui';
import { CopyCommandBlock } from './CopyCommandBlock';

export default function ActiveTraderLadder({
  symbol = 'SPY',
  currentPrice = null,
}: {
  symbol?: string;
  currentPrice?: number | null;
}) {
  const tick = useLiveTick(symbol);
  const { data: ladder, loading, error } = useApi(() => api.getOrderBookLadder(symbol), [symbol]);

  const bestAsk = ladder?.asks?.[0]?.price ?? null;
  const bestBid = ladder?.bids?.[0]?.price ?? null;
  const spread = bestAsk !== null && bestBid !== null ? bestAsk - bestBid : null;
  const effectivePrice = tick.price ?? ladder?.current_price ?? currentPrice ?? null;

  const [quantity, setQuantity] = useState("100");
  const [orderType, setOrderType] = useState<"MARKET" | "LIMIT">("MARKET");
  const [limitPrice, setLimitPrice] = useState("");
  const [tradeAction, setTradeAction] = useState<"BUY" | "SELL" | null>(null);

  const handlePriceClick = (price: number) => {
    setLimitPrice(price.toFixed(2));
    setOrderType("LIMIT");
  };

  let command = "";
  if (tradeAction) {
    const actionStr = tradeAction.toLowerCase();
    const typeStr = orderType === "MARKET" ? "market order" : `limit order at $${limitPrice}`;
    command = `Run robinhood-execution to place a ${actionStr} ${typeStr} for ${quantity} shares of ${symbol}.`;
  }

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
                  <div 
                    className="ladder-cell" 
                    style={{ fontWeight: 600, color: "var(--decline)", cursor: "pointer" }}
                    onClick={() => handlePriceClick(ask.price)}
                    title="Click to set limit price"
                  >
                    ${ask.price.toFixed(2)}
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
                  <div 
                    className="ladder-cell" 
                    style={{ fontWeight: 600, color: "var(--growth)", cursor: "pointer" }}
                    onClick={() => handlePriceClick(bid.price)}
                    title="Click to set limit price"
                  >
                    ${bid.price.toFixed(2)}
                  </div>
                  <div className="ladder-cell" style={{ color: "var(--text-muted)" }}>-</div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Trade Controls */}
      <div style={{ padding: "var(--s-4)", borderTop: "1px solid var(--border)", background: "var(--surface)" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--s-3)", marginBottom: "var(--s-3)" }}>
           <div>
             <label style={{ fontSize: "var(--t-caption)", fontWeight: 600, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>Quantity</label>
             <input
               type="number"
               value={quantity}
               onChange={e => setQuantity(e.target.value)}
               style={{ width: "100%", background: "var(--base)", border: "1px solid var(--border)", borderRadius: "var(--r-sm)", padding: "6px 8px", color: "var(--text-primary)" }}
             />
           </div>
           <div>
             <label style={{ fontSize: "var(--t-caption)", fontWeight: 600, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>Order Type</label>
             <select
               value={orderType}
               onChange={e => setOrderType(e.target.value as "MARKET" | "LIMIT")}
               style={{ width: "100%", background: "var(--base)", border: "1px solid var(--border)", borderRadius: "var(--r-sm)", padding: "6px 8px", color: "var(--text-primary)" }}
             >
               <option value="MARKET">Market</option>
               <option value="LIMIT">Limit</option>
             </select>
           </div>
        </div>
        {orderType === "LIMIT" && (
          <div style={{ marginBottom: "var(--s-3)" }}>
             <label style={{ fontSize: "var(--t-caption)", fontWeight: 600, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>Limit Price</label>
             <input
               type="number"
               value={limitPrice}
               onChange={e => setLimitPrice(e.target.value)}
               placeholder="Click a price on the ladder"
               style={{ width: "100%", background: "var(--base)", border: "1px solid var(--border)", borderRadius: "var(--r-sm)", padding: "6px 8px", color: "var(--text-primary)" }}
             />
          </div>
        )}
        
        <div style={{ display: "flex", gap: "var(--s-2)", marginBottom: tradeAction ? "var(--s-3)" : 0 }}>
          <Button 
            variant="neutral"
            onClick={() => setTradeAction("BUY")} 
            style={{ flex: 1, backgroundColor: tradeAction === "BUY" ? "var(--growth)" : "var(--surface-2)", color: tradeAction === "BUY" ? "#fff" : undefined, borderColor: tradeAction === "BUY" ? "var(--growth)" : undefined }}
          >
            Buy
          </Button>
          <Button 
            variant="neutral"
            onClick={() => setTradeAction("SELL")}
            style={{ flex: 1, backgroundColor: tradeAction === "SELL" ? "var(--decline)" : "var(--surface-2)", color: tradeAction === "SELL" ? "#fff" : undefined, borderColor: tradeAction === "SELL" ? "var(--decline)" : undefined }}
          >
            Sell
          </Button>
          <Button
            variant="neutral"
            onClick={() => {
              setTradeAction(null);
              setOrderType("MARKET");
              setLimitPrice("");
              setQuantity("100");
            }}
            title="Reset"
          >
            Reset
          </Button>
        </div>

        {tradeAction && (
          <div style={{ animation: "fadeIn 0.2s ease-in-out" }}>
            <CopyCommandBlock 
              command={command} 
              label="Agent Command (Paste in Claude)" 
              resetKey={command} 
            />
          </div>
        )}
      </div>
    </div>
  );
}
