import React from 'react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, ReferenceLine } from 'recharts';
import { Clock, TrendingUp, AlertTriangle, Loader2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import api from '../api/client';

export default function OptionsAnalyticsDashboard({ symbol = 'SPY' }: { symbol?: string }) {
  const { data, loading, error } = useApi<any>(() => api.getOptionsAnalytics(symbol), [symbol]);

  const intradayData = data?.intraday_series || [];
  const netDealerPremium = data?.net_dealer_premium || 0;
  const regime = data?.regime || 'Unknown';

  return (
    <div className="bg-white dark:bg-[#1a1a1a] rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm p-5 flex flex-col gap-6">
      <div className="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-4">
        <div className="flex items-center gap-2">
          <Clock className="w-5 h-5 text-indigo-500" />
          <h3 className="font-semibold text-slate-900 dark:text-white">0DTE Options Analytics</h3>
        </div>
        <div className="text-xs font-medium bg-indigo-50 dark:bg-indigo-900/20 text-indigo-700 dark:text-indigo-300 px-2 py-1 rounded">
          Live Intraday
        </div>
      </div>

      {loading ? (
        <div className="flex-1 flex items-center justify-center min-h-[300px]">
          <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
        </div>
      ) : error ? (
        <div className="flex-1 flex items-center justify-center text-red-500 min-h-[300px]">Failed to load analytics</div>
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="p-4 rounded-lg bg-slate-50 dark:bg-slate-800/30 border border-slate-200 dark:border-slate-800">
          <div className="text-sm font-medium text-slate-500 dark:text-slate-400 mb-1">Net Dealer Premium</div>
          <div className={`text-2xl font-bold ${netDealerPremium < 0 ? 'text-red-500' : 'text-green-500'}`}>
            ${Math.abs(netDealerPremium)}M {netDealerPremium < 0 ? 'Short' : 'Long'}
          </div>
          <div className="text-xs mt-2 flex items-center gap-1 text-slate-600 dark:text-slate-400">
            {netDealerPremium < 0 ? <AlertTriangle className="w-3 h-3 text-red-500" /> : <TrendingUp className="w-3 h-3 text-green-500" />}
            Regime: {regime}
          </div>
        </div>

        <div className="p-4 rounded-lg bg-slate-50 dark:bg-slate-800/30 border border-slate-200 dark:border-slate-800">
          <div className="text-sm font-medium text-slate-500 dark:text-slate-400 mb-1">Pin Risk Detection</div>
          <div className="text-2xl font-bold text-amber-500">
            Elevated
          </div>
          <div className="text-xs mt-2 text-slate-600 dark:text-slate-400">
            Large OI clusters detected near current ATM strikes.
          </div>
        </div>
      </div>

      <div>
        <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-200 mb-4">Intraday Theta Decay & Gamma Acceleration</h4>
        <div className="h-[250px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={intradayData} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="colorTheta" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="#ef4444" stopOpacity={0}/>
                </linearGradient>
                <linearGradient id="colorGamma" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <XAxis dataKey="time" tick={{ fontSize: 11, fill: '#64748b' }} tickLine={false} axisLine={{ stroke: '#334155', opacity: 0.5 }} minTickGap={30} />
              <YAxis yAxisId="left" tick={{ fontSize: 11, fill: '#64748b' }} tickLine={false} axisLine={false} />
              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11, fill: '#64748b' }} tickLine={false} axisLine={false} />
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#334155" opacity={0.2} />
              <RechartsTooltip 
                contentStyle={{ backgroundColor: 'rgba(15, 23, 42, 0.9)', borderColor: '#334155', borderRadius: '8px', color: '#f8fafc' }}
                itemStyle={{ color: '#f8fafc' }}
              />
              <ReferenceLine x="2:00 PM" stroke="#f59e0b" strokeDasharray="3 3" label={{ position: 'top', value: 'Decay Acceleration', fill: '#f59e0b', fontSize: 10 }} yAxisId="left" />
              <Area yAxisId="left" type="monotone" dataKey="theta" name="Theta Decay" stroke="#ef4444" fillOpacity={1} fill="url(#colorTheta)" />
              <Area yAxisId="right" type="monotone" dataKey="gamma" name="Gamma" stroke="#8b5cf6" fillOpacity={1} fill="url(#colorGamma)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
      </>
      )}
    </div>
  );
}
