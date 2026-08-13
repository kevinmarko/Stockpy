import React, { useState, useEffect } from 'react';
import { OptionChainResponse, OptionContract } from '../../api/types';
import { theme } from '../../theme';
import { OptionsPayoffChart } from './OptionsPayoffChart';

export type StrategyCategory = 'Verticals' | 'Straddles & Strangles' | 'Calendars';
export type StrategyName = 
  | 'Custom'
  | 'Bull Call Spread' | 'Bear Put Spread' | 'Bull Put Spread' | 'Bear Call Spread'
  | 'Long Straddle' | 'Long Strangle'
  | 'Long Call Calendar' | 'Long Put Calendar' | 'Short Put Calendar';

interface SelectedLeg {
  contract: OptionContract;
  type: 'call' | 'put';
  action: 'Buy' | 'Sell';
}

interface Props {
  chain: OptionChainResponse | null;
  selectedLegs: SelectedLeg[];
  onUpdateLegs: (legs: SelectedLeg[]) => void;
}

const STRATEGIES: { name: StrategyName, category: StrategyCategory, outlook: string, desc: string, shape: 'call-spread' | 'put-spread' | 'straddle' | 'strangle' | 'calendar' }[] = [
  { name: 'Bull Call Spread', category: 'Verticals', outlook: 'Bullish', desc: 'Buy a call and sell a higher strike call. Limited risk, limited reward.', shape: 'call-spread' },
  { name: 'Bear Put Spread', category: 'Verticals', outlook: 'Bearish', desc: 'Buy a put and sell a lower strike put. Limited risk, limited reward.', shape: 'put-spread' },
  { name: 'Bull Put Spread', category: 'Verticals', outlook: 'Bullish', desc: 'Sell a put and buy a lower strike put for a net credit.', shape: 'call-spread' },
  { name: 'Bear Call Spread', category: 'Verticals', outlook: 'Bearish', desc: 'Sell a call and buy a higher strike call for a net credit.', shape: 'put-spread' },
  
  { name: 'Long Straddle', category: 'Straddles & Strangles', outlook: 'Volatile', desc: 'Buy a call and put at the same strike. Profits from a large move in either direction.', shape: 'straddle' },
  { name: 'Long Strangle', category: 'Straddles & Strangles', outlook: 'Volatile', desc: 'Buy an out-of-the-money call and put. Requires a larger move than a straddle but costs less.', shape: 'strangle' },
  
  { name: 'Long Call Calendar', category: 'Calendars', outlook: 'Neutral', desc: 'Sell a near-term call and buy a longer-term call at the same strike.', shape: 'calendar' },
  { name: 'Long Put Calendar', category: 'Calendars', outlook: 'Neutral', desc: 'Sell a near-term put and buy a longer-term put at the same strike.', shape: 'calendar' },
  { name: 'Short Put Calendar', category: 'Calendars', outlook: 'Volatile', desc: 'Buy a near-term put and sell a longer-term put at the same strike.', shape: 'calendar' },
];

export const OptionsStrategyBuilder: React.FC<Props> = ({ chain, onUpdateLegs }) => {
  const [activeCategory, setActiveCategory] = useState<StrategyCategory>('Verticals');
  const [activeStrategy, setActiveStrategy] = useState<StrategyName>('Custom');

  useEffect(() => {
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
      // Calendars require two chains, so we only populate the near-term leg for now
      case 'Long Call Calendar': {
        const shortCall = findClosestStrikeByDelta(calls, 0.50);
        if (shortCall) newLegs.push({ contract: shortCall, type: 'call', action: 'Sell' });
        break;
      }
      case 'Long Put Calendar':
      case 'Short Put Calendar': {
        const shortPut = findClosestStrikeByDelta(puts, -0.50);
        if (shortPut) newLegs.push({ contract: shortPut, type: 'put', action: 'Sell' });
        break;
      }
    }

    onUpdateLegs(newLegs);
  }, [activeStrategy, chain, onUpdateLegs]);

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
              <h4 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>{strat.name}</h4>
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
