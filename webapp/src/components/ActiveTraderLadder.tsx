import { Activity, Loader2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { useLiveTick } from '../hooks/useLiveTick';
import { api } from '../api/client';
import DemoDataBadge from './DemoDataBadge';

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
                  <div className="ladder-cell" style={{ fontWeight: 600, color: "var(--decline)" }}>${ask.price.toFixed(2)}</div>
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
                  <div className="ladder-cell" style={{ fontWeight: 600, color: "var(--growth)" }}>${bid.price.toFixed(2)}</div>
                  <div className="ladder-cell" style={{ color: "var(--text-muted)" }}>-</div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
