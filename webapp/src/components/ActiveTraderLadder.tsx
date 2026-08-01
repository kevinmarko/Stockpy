import React, { useState } from 'react';
import { ArrowUp, ArrowDown, Activity } from 'lucide-react';

interface OrderBookLevel {
  price: number;
  size: number;
  type: 'bid' | 'ask';
}

export default function ActiveTraderLadder({
  symbol = 'SPY',
  currentPrice = 450.00,
}: {
  symbol?: string;
  currentPrice?: number | null;
}) {
  const effectivePrice = currentPrice ?? 450.00;

  // Order book ladder around currentPrice
  const bids: OrderBookLevel[] = [
    { price: effectivePrice - 0.05, size: 1200, type: 'bid' },
    { price: effectivePrice - 0.10, size: 850, type: 'bid' },
    { price: effectivePrice - 0.15, size: 2100, type: 'bid' },
    { price: effectivePrice - 0.20, size: 500, type: 'bid' },
    { price: effectivePrice - 0.25, size: 300, type: 'bid' },
  ];

  const asks: OrderBookLevel[] = [
    { price: effectivePrice + 0.05, size: 900, type: 'ask' },
    { price: effectivePrice + 0.10, size: 1500, type: 'ask' },
    { price: effectivePrice + 0.15, size: 600, type: 'ask' },
    { price: effectivePrice + 0.20, size: 2000, type: 'ask' },
    { price: effectivePrice + 0.25, size: 1100, type: 'ask' },
  ];


  return (
    <div className="bg-white dark:bg-[#1a1a1a] rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden flex flex-col h-full">
      <div className="p-4 border-b border-slate-200 dark:border-slate-800 flex justify-between items-center bg-slate-50 dark:bg-[#121212]">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-blue-500" />
          <h3 className="font-semibold text-slate-900 dark:text-white">Active Trader Ladder</h3>
        </div>
        <div className="text-sm font-medium text-slate-500 bg-white dark:bg-black px-3 py-1 rounded-md border border-slate-200 dark:border-slate-800">
          {symbol}
        </div>
      </div>
      
      <div className="flex-1 overflow-auto p-4">
        <div className="grid grid-cols-3 gap-4 mb-2 text-xs font-semibold text-slate-500 uppercase tracking-wider text-center border-b border-slate-200 dark:border-slate-800 pb-2">
          <div>Bid Size</div>
          <div>Price</div>
          <div>Ask Size</div>
        </div>
        
        <div className="flex flex-col">
          {/* Asks (descending price) */}
          {[...asks].reverse().map((ask, i) => (
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
          {bids.map((bid, i) => (
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
      </div>
    </div>
  );
}
