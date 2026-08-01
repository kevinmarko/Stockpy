import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, ReferenceLine } from 'recharts';
import { Clock, TrendingUp, AlertTriangle, Loader2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api } from '../api/client';
import type { OptionsAnalyticsSummaryResponse } from '../api/types';
import DemoDataBadge from './DemoDataBadge';

export default function OptionsAnalyticsDashboard({ symbol = 'SPY' }: { symbol?: string }) {
  const { data, loading, error } = useApi<OptionsAnalyticsSummaryResponse>(() => api.getOptionsAnalytics(symbol), [symbol]);

  const intradayData = data?.intraday_series || [];
  // ?? not || : an absent/invalid premium must render as "unavailable", not
  // be silently converted into a real-looking $0M "Long" reading.
  const netDealerPremium = data?.net_dealer_premium ?? null;
  const regime = data?.regime ?? null;

  return (
    <div className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: "var(--s-6)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", borderBottom: "1px solid var(--border)", paddingBottom: "var(--s-4)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <Clock style={{ width: 20, height: 20, color: "#6366f1" }} />
          <h3 style={{ margin: 0, fontWeight: 600, color: "var(--text-primary)" }}>0DTE Options Analytics</h3>
        </div>
        {data?.is_synthetic ? (
          <DemoDataBadge />
        ) : (
          <div
            style={{
              fontSize: "var(--t-caption)",
              fontWeight: 600,
              background: "rgba(99, 102, 241, 0.12)",
              color: "#a5b4fc",
              border: "1px solid rgba(99, 102, 241, 0.28)",
              padding: "var(--s-1) var(--s-2)",
              borderRadius: "var(--r-xs)",
            }}
          >
            Live Intraday
          </div>
        )}
      </div>

      {loading ? (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", minHeight: 300 }}>
          <Loader2 className="icon-spin" style={{ width: 24, height: 24, color: "var(--text-muted)" }} />
        </div>
      ) : error ? (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--decline)", minHeight: 300 }}>Failed to load analytics</div>
      ) : (
        <>
          <div className="tile">
          <div className="tile-label">Net Dealer Premium</div>
          {netDealerPremium === null ? (
            <div style={{ fontSize: "var(--t-subhead)", fontWeight: 600, color: "var(--text-muted)" }}>Unavailable</div>
          ) : (
            <>
              <div className="tile-value" style={{ color: netDealerPremium < 0 ? "var(--decline)" : "var(--growth)" }}>
                ${Math.abs(netDealerPremium)}M {netDealerPremium < 0 ? 'Short' : 'Long'}
              </div>
              <div style={{ fontSize: "var(--t-caption)", marginTop: "var(--s-2)", display: "flex", alignItems: "center", gap: "var(--s-1)", color: "var(--text-secondary)" }}>
                {netDealerPremium < 0 ? <AlertTriangle style={{ width: 12, height: 12, color: "var(--decline)" }} /> : <TrendingUp style={{ width: 12, height: 12, color: "var(--growth)" }} />}
                Regime: {regime ?? 'Unavailable'}
              </div>
            </>
          )}
        </div>

      <div>
        <h4 style={{ fontSize: "var(--t-body)", fontWeight: 600, color: "var(--text-primary)", marginBottom: "var(--s-4)" }}>Intraday Theta Decay &amp; Gamma Acceleration</h4>
        <div style={{ height: 250, width: "100%" }}>
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
