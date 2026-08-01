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
  const effectivePrice = tick.price ?? ladder?.current_price ?? currentPrice ?? null;

  return (
    <div className="bg-white dark:bg-[#1a1a1a] rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden flex flex-col h-full">
      <div className="p-4 border-b border-slate-200 dark:border-slate-800 flex justify-between items-center bg-slate-50 dark:bg-[#121212]">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-blue-500" />
          <h3 className="font-semibold text-slate-900 dark:text-white">Active Trader Ladder</h3>
          {ladder?.is_synthetic && <DemoDataBadge />}
        </div>
        <div className="flex items-center gap-2">
          {tick.isConnected && (
            <span
              title={`Live tick source: ${tick.source}`}
              className="w-1.5 h-1.5 rounded-full bg-green-500"
            />
          )}
          <div className="text-sm font-medium text-slate-500 bg-white dark:bg-black px-3 py-1 rounded-md border border-slate-200 dark:border-slate-800">
            {symbol}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-4">
        {loading ? (
          <div className="flex items-center justify-center p-8">
            <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
          </div>
        ) : error || !ladder || effectivePrice === null ? (
          <div className="text-center text-slate-500 dark:text-slate-400 p-8 text-sm">
            Ladder unavailable for {symbol}.
          </div>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-4 mb-2 text-xs font-semibold text-slate-500 uppercase tracking-wider text-center border-b border-slate-200 dark:border-slate-800 pb-2">
              <div>Bid Size</div>
              <div>Price</div>
              <div>Ask Size</div>
            </div>

            <div className="flex flex-col">
              {/* Asks (descending price) */}
              {[...ladder.asks].reverse().map((ask, i) => (
                <div key={`ask-${i}`} className="grid grid-cols-3 gap-4 py-1.5 text-sm hover:bg-slate-50 dark:hover:bg-slate-800/50 rounded group cursor-pointer">
                  <div className="text-center text-slate-400">-</div>
                  <div className="text-center font-medium text-red-500 dark:text-red-400">${ask.price.toFixed(2)}</div>
                  <div className="text-center relative">
                    <span className="relative z-10 text-slate-700 dark:text-slate-300">{ask.size}</span>
                    <div
                      className="absolute inset-y-0 right-0 bg-red-100 dark:bg-red-900/30 rounded-sm"
                      style={{ width: `${Math.min(100, (ask.size / 2000) * 100)}%` }}
                    />
                  </div>
                </div>
              ))}

              {/* Current Price */}
              <div className="grid grid-cols-3 gap-4 py-3 my-2 text-sm bg-blue-50 dark:bg-blue-900/20 border-y border-blue-100 dark:border-blue-800/30">
                <div className="text-center"></div>
                <div className="text-center font-bold text-lg text-slate-900 dark:text-white">${effectivePrice.toFixed(2)}</div>
                <div className="text-center"></div>
              </div>

              {/* Bids (descending price) */}
              {ladder.bids.map((bid, i) => (
                <div key={`bid-${i}`} className="grid grid-cols-3 gap-4 py-1.5 text-sm hover:bg-slate-50 dark:hover:bg-slate-800/50 rounded group cursor-pointer">
                  <div className="text-center relative">
                    <span className="relative z-10 text-slate-700 dark:text-slate-300">{bid.size}</span>
                    <div
                      className="absolute inset-y-0 left-0 bg-green-100 dark:bg-green-900/30 rounded-sm"
                      style={{ width: `${Math.min(100, (bid.size / 2000) * 100)}%` }}
                    />
                  </div>
                  <div className="text-center font-medium text-green-500 dark:text-green-400">${bid.price.toFixed(2)}</div>
                  <div className="text-center text-slate-400">-</div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
