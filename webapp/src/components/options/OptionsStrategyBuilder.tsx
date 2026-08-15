import React, { useState, useEffect } from 'react';
import { OptionChainResponse, OptionContract } from '../../api/types';
import { api } from '../../api/client';
import { theme } from '../../theme';
import { OptionsPayoffChart } from './OptionsPayoffChart';

export type StrategyCategory = 'Verticals' | 'Straddles & Strangles' | 'Calendars';
export type StrategyName = 
  | 'Custom'
  | 'Bull Call Spread' | 'Bear Put Spread' | 'Bull Put Spread' | 'Bear Call Spread'
  | 'Iron Condor'
  | 'Long Straddle' | 'Long Strangle'
  | 'Long Call Calendar' | 'Long Put Calendar' | 'Short Put Calendar';

interface SelectedLeg {
  contract: OptionContract;
  type: 'call' | 'put';
  action: 'Buy' | 'Sell';
}

interface Props {
  symbol: string;
  chain: OptionChainResponse | null;
  /**
   * The full list of available expiration dates for this symbol (from the
   * no-`expiration`-param chain response). `chain` itself is the response for
   * ONE already-selected expiration and never carries its own `expirations`
   * array (both the mock and live backend omit it once `expiration` is
   * passed) -- Calendar-spread legs must resolve "the next expiration" from
   * this prop, not from `chain.expirations`.
   */
  expirations: string[];
  selectedLegs: SelectedLeg[];
  onUpdateLegs: (legs: SelectedLeg[]) => void;
}

const STRATEGIES: { name: StrategyName, category: StrategyCategory, outlook: string, desc: string, shape: 'call-spread' | 'put-spread' | 'straddle' | 'strangle' | 'calendar' }[] = [
  { name: 'Bull Call Spread', category: 'Verticals', outlook: 'Bullish', desc: 'Buy a call and sell a higher strike call. Limited risk, limited reward.', shape: 'call-spread' },
  { name: 'Bear Put Spread', category: 'Verticals', outlook: 'Bearish', desc: 'Buy a put and sell a lower strike put. Limited risk, limited reward.', shape: 'put-spread' },
  { name: 'Bull Put Spread', category: 'Verticals', outlook: 'Bullish', desc: 'Sell a put and buy a lower strike put for a net credit.', shape: 'call-spread' },
  { name: 'Bear Call Spread', category: 'Verticals', outlook: 'Bearish', desc: 'Sell a call and buy a higher strike call for a net credit.', shape: 'put-spread' },
  { name: 'Iron Condor', category: 'Verticals', outlook: 'Neutral', desc: 'Sell an out-of-the-money put spread and call spread for a net credit. Limited risk, limited reward.', shape: 'put-spread' },
  
  { name: 'Long Straddle', category: 'Straddles & Strangles', outlook: 'Volatile', desc: 'Buy a call and put at the same strike. Profits from a large move in either direction.', shape: 'straddle' },
  { name: 'Long Strangle', category: 'Straddles & Strangles', outlook: 'Volatile', desc: 'Buy an out-of-the-money call and put. Requires a larger move than a straddle but costs less.', shape: 'strangle' },
  
  { name: 'Long Call Calendar', category: 'Calendars', outlook: 'Neutral', desc: 'Sell a near-term call and buy a longer-term call at the same strike.', shape: 'calendar' },
  { name: 'Long Put Calendar', category: 'Calendars', outlook: 'Neutral', desc: 'Sell a near-term put and buy a longer-term put at the same strike.', shape: 'calendar' },
  { name: 'Short Put Calendar', category: 'Calendars', outlook: 'Volatile', desc: 'Buy a near-term put and sell a longer-term put at the same strike.', shape: 'calendar' },
];

export const OptionsStrategyBuilder: React.FC<Props> = ({ symbol, chain, expirations, onUpdateLegs }) => {
  const [activeCategory, setActiveCategory] = useState<StrategyCategory>('Verticals');
  const [activeStrategy, setActiveStrategy] = useState<StrategyName>('Custom');
  const [isFetchingLegs, setIsFetchingLegs] = useState(false);

  useEffect(() => {
    let active = true;
    // Reset immediately on every new invocation (strategy/chain/symbol change) --
    // not gated on `active`, so switching away from a calendar strategy while
    // its secondary-chain fetch is still in flight can't leave this stuck
    // `true` forever (the stale fetch's own cleanup-gated reset would never run).
    setIsFetchingLegs(false);

    const buildLegs = async () => {
      if (activeStrategy === 'Custom' || !chain) return;

      const findClosestStrikeByDelta = (contracts: OptionContract[], targetDelta: number) => {
        if (!contracts || contracts.length === 0) return null;
        return contracts.reduce((prev, curr) => 
          Math.abs(curr.greeks.delta - targetDelta) < Math.abs(prev.greeks.delta - targetDelta) ? curr : prev
        );
      };

      const newLegs: SelectedLeg[] = [];
      const calls = chain.calls || [];
      const puts = chain.puts || [];

      switch (activeStrategy) {
        case 'Bull Call Spread': {
          const longCall = findClosestStrikeByDelta(calls, 0.50);
          const shortCall = findClosestStrikeByDelta(calls, 0.30);
          if (longCall) newLegs.push({ contract: longCall, type: 'call', action: 'Buy' });
          if (shortCall && shortCall.strike !== longCall?.strike) newLegs.push({ contract: shortCall, type: 'call', action: 'Sell' });
          break;
        }
        case 'Bear Put Spread': {
          const longPut = findClosestStrikeByDelta(puts, -0.50);
          const shortPut = findClosestStrikeByDelta(puts, -0.30);
          if (longPut) newLegs.push({ contract: longPut, type: 'put', action: 'Buy' });
          if (shortPut && shortPut.strike !== longPut?.strike) newLegs.push({ contract: shortPut, type: 'put', action: 'Sell' });
          break;
        }
        case 'Bull Put Spread': {
          const shortPut = findClosestStrikeByDelta(puts, -0.30);
          const longPut = findClosestStrikeByDelta(puts, -0.15);
          if (shortPut) newLegs.push({ contract: shortPut, type: 'put', action: 'Sell' });
          if (longPut && longPut.strike !== shortPut?.strike) newLegs.push({ contract: longPut, type: 'put', action: 'Buy' });
          break;
        }
        case 'Bear Call Spread': {
          const shortCall = findClosestStrikeByDelta(calls, 0.30);
          const longCall = findClosestStrikeByDelta(calls, 0.15);
          if (shortCall) newLegs.push({ contract: shortCall, type: 'call', action: 'Sell' });
          if (longCall && longCall.strike !== shortCall?.strike) newLegs.push({ contract: longCall, type: 'call', action: 'Buy' });
          break;
        }
        case 'Iron Condor': {
          const longPut = findClosestStrikeByDelta(puts, -0.15);
          const shortPut = findClosestStrikeByDelta(puts, -0.30);
          const shortCall = findClosestStrikeByDelta(calls, 0.30);
          const longCall = findClosestStrikeByDelta(calls, 0.15);
          if (longPut) newLegs.push({ contract: longPut, type: 'put', action: 'Buy' });
          if (shortPut && shortPut.strike !== longPut?.strike) newLegs.push({ contract: shortPut, type: 'put', action: 'Sell' });
          if (shortCall) newLegs.push({ contract: shortCall, type: 'call', action: 'Sell' });
          if (longCall && longCall.strike !== shortCall?.strike) newLegs.push({ contract: longCall, type: 'call', action: 'Buy' });
          break;
        }
        case 'Long Straddle': {
          const call = findClosestStrikeByDelta(calls, 0.50);
          const put = puts.find(p => p.strike === call?.strike);
          if (call) newLegs.push({ contract: call, type: 'call', action: 'Buy' });
          if (put) newLegs.push({ contract: put, type: 'put', action: 'Buy' });
          break;
        }
        case 'Long Strangle': {
          const call = findClosestStrikeByDelta(calls, 0.16);
          const put = findClosestStrikeByDelta(puts, -0.16);
          if (call) newLegs.push({ contract: call, type: 'call', action: 'Buy' });
          if (put) newLegs.push({ contract: put, type: 'put', action: 'Buy' });
          break;
        }
        case 'Long Call Calendar': {
          const shortCall = findClosestStrikeByDelta(calls, 0.50);
          if (shortCall) {
            // Hide the (possibly stale, previously-selected) Order Ticket while the
            // far-term leg is being resolved, rather than leaving whatever legs were
            // selected before the user picked this strategy submittable mid-fetch.
            if (active) onUpdateLegs([]);
            setIsFetchingLegs(true);
            try {
              const currIdx = expirations.indexOf(chain.expiration || '');
              const nextExp = (currIdx !== -1 && currIdx + 1 < expirations.length) ? expirations[currIdx + 1] : null;

              if (nextExp) {
                const nextChain = await api.getOptionsChain(symbol, nextExp);
                const nextCalls = nextChain.calls || [];
                const longCall = nextCalls.find(c => c.strike === shortCall.strike);
                newLegs.push({ contract: shortCall, type: 'call', action: 'Sell' });
                if (longCall) newLegs.push({ contract: longCall, type: 'call', action: 'Buy' });
              } else {
                // No later expiration available -- fall back to the single near-term
                // leg rather than silently claiming a calendar spread was built.
                newLegs.push({ contract: shortCall, type: 'call', action: 'Sell' });
              }
            } catch (e) {
              console.error("Failed to fetch next chain for calendar spread:", e);
              newLegs.push({ contract: shortCall, type: 'call', action: 'Sell' });
            }
            setIsFetchingLegs(false);
          }
          break;
        }
        case 'Long Put Calendar':
        case 'Short Put Calendar': {
          const isLong = activeStrategy === 'Long Put Calendar';
          const shortPut = findClosestStrikeByDelta(puts, isLong ? -0.50 : -0.20); // rough approximation
          if (shortPut) {
            if (active) onUpdateLegs([]);
            setIsFetchingLegs(true);
            try {
              const currIdx = expirations.indexOf(chain.expiration || '');
              const nextExp = (currIdx !== -1 && currIdx + 1 < expirations.length) ? expirations[currIdx + 1] : null;

              if (nextExp) {
                const nextChain = await api.getOptionsChain(symbol, nextExp);
                const nextPuts = nextChain.puts || [];
                const longPut = nextPuts.find(p => p.strike === shortPut.strike);
                newLegs.push({ contract: shortPut, type: 'put', action: isLong ? 'Sell' : 'Buy' });
                if (longPut) newLegs.push({ contract: longPut, type: 'put', action: isLong ? 'Buy' : 'Sell' });
              } else {
                newLegs.push({ contract: shortPut, type: 'put', action: isLong ? 'Sell' : 'Buy' });
              }
            } catch (e) {
              console.error("Failed to fetch next chain for calendar spread:", e);
              newLegs.push({ contract: shortPut, type: 'put', action: isLong ? 'Sell' : 'Buy' });
            }
            setIsFetchingLegs(false);
          }
          break;
        }
      }

      if (active) {
        onUpdateLegs(newLegs);
      }
    };

    buildLegs();

    return () => { active = false; };
  }, [activeStrategy, chain, symbol, onUpdateLegs]);

  const categories: StrategyCategory[] = ['Verticals', 'Straddles & Strangles', 'Calendars'];
  const activeStrategies = STRATEGIES.filter(s => s.category === activeCategory);

  const getOutlookColor = (outlook: string) => {
    switch (outlook) {
      case 'Bullish': return theme.growth;
      case 'Bearish': return theme.decline;
      case 'Volatile': return theme.accent;
      default: return theme.textSecondary;
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 16 }}>
      
      {/* Categories */}
      <div style={{ display: 'flex', gap: 8, borderBottom: `1px solid ${theme.border}`, paddingBottom: 16 }}>
        {categories.map(cat => (
          <button
            key={cat}
            onClick={() => setActiveCategory(cat)}
            style={{
              padding: '8px 16px',
              borderRadius: 20,
              background: activeCategory === cat ? theme.surface3 : 'transparent',
              color: activeCategory === cat ? theme.textPrimary : theme.textSecondary,
              border: `1px solid ${activeCategory === cat ? theme.borderStrong : 'transparent'}`,
              cursor: 'pointer',
              fontWeight: 500,
              transition: 'all 0.2s'
            }}
          >
            {cat}
          </button>
        ))}
      </div>

      {/* Strategy Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
        {activeStrategies.map(strat => (
          <div
            key={strat.name}
            onClick={() => setActiveStrategy(strat.name)}
            style={{
              background: theme.surface,
              border: `1px solid ${activeStrategy === strat.name ? theme.accent : theme.border}`,
              borderRadius: 12,
              padding: 16,
              cursor: 'pointer',
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
              transition: 'all 0.2s'
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <h4 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>{strat.name}</h4>
                {isFetchingLegs && activeStrategy === strat.name && (
                  <span style={{ fontSize: 12, color: theme.textSecondary }}>Loading legs...</span>
                )}
              </div>
              <span style={{ 
                fontSize: 11, 
                padding: '2px 8px', 
                borderRadius: 12, 
                background: `${getOutlookColor(strat.outlook)}20`,
                color: getOutlookColor(strat.outlook),
                fontWeight: 600
              }}>
                {strat.outlook}
              </span>
            </div>
            
            <p style={{ margin: 0, fontSize: 13, color: theme.textSecondary, lineHeight: 1.4, flex: 1 }}>
              {strat.desc}
            </p>

            <div style={{ height: 60, marginTop: 8 }}>
              <OptionsPayoffChart type={strat.shape} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
