import { Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer } from 'recharts';
import { Globe, TrendingDown, TrendingUp, Minus, Loader2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api } from '../api/client';
import type { MacroSentimentResponse } from '../api/types';
import DemoDataBadge from './DemoDataBadge';

export default function MacroSentimentDashboard() {
  const { data, loading, error } = useApi<MacroSentimentResponse>(() => api.getMacroSentiment(), []);

  const macroData = data?.macro_data || [];

  const renderTrendIcon = (trend: string) => {
    switch (trend) {
      case 'up': return <TrendingUp className="w-4 h-4 text-green-500" />;
      case 'down': return <TrendingDown className="w-4 h-4 text-red-500" />;
      default: return <Minus className="w-4 h-4 text-slate-400" />;
    }
  };

  return (
    <div className="bg-white dark:bg-[#1a1a1a] rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm p-5 h-full flex flex-col">
      <div className="flex items-center gap-2 mb-6 border-b border-slate-200 dark:border-slate-800 pb-4">
        <Globe className="w-5 h-5 text-teal-500" />
        <h3 className="font-semibold text-slate-900 dark:text-white">Macroeconomic Sentiment</h3>
        {data?.is_synthetic && <DemoDataBadge />}
      </div>
      
      <div className="flex-1 flex flex-col lg:flex-row gap-6">
        {loading ? (
          <div className="w-full flex items-center justify-center p-8">
            <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
          </div>
        ) : error ? (
          <div className="w-full text-center text-red-500 p-8">Failed to load macro sentiment</div>
        ) : macroData.length === 0 ? (
          <div className="w-full text-center text-slate-500 dark:text-slate-400 p-8">
            {data?.reason || 'No macro data available yet.'}
          </div>
        ) : (
          <>
            <div className="w-full lg:w-1/2 h-[300px]">
          <ResponsiveContainer width="100%" height="100%">
            <RadarChart cx="50%" cy="50%" outerRadius="80%" data={macroData}>
              <PolarGrid stroke="#334155" opacity={0.3} />
              <PolarAngleAxis dataKey="subject" tick={{ fill: '#64748b', fontSize: 11 }} />
              <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} axisLine={false} />
              <Radar name="Current Sentiment" dataKey="value" stroke="#14b8a6" fill="#14b8a6" fillOpacity={0.3} />
            </RadarChart>
          </ResponsiveContainer>
        </div>
        
        <div className="w-full lg:w-1/2 flex flex-col justify-center">
          <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-200 mb-4">Key Drivers</h4>
          <div className="space-y-3">
            {macroData.map((item, idx) => (
              <div key={idx} className="flex items-center justify-between p-2 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors">
                <span className="text-sm text-slate-700 dark:text-slate-300 font-medium">{item.subject}</span>
                <div className="flex items-center gap-3">
                  <div className="w-24 h-1.5 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
                    <div 
                      className="h-full bg-teal-500 rounded-full"
                      style={{ width: `${item.value}%` }}
                    />
                  </div>
                  {renderTrendIcon(item.trend)}
                </div>
              </div>
            ))}
          </div>
        </div>
      </>
        )}
      </div>
    </div>
  );
}
