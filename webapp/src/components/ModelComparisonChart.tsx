import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import { GitBranch, Loader2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api } from '../api/client';
import type { ModelComparisonResponse } from '../api/types';
import DemoDataBadge from './DemoDataBadge';

export default function ModelComparisonChart() {
  const { data: responseData, loading, error } = useApi<ModelComparisonResponse>(() => api.getModelComparison(), []);
  const data = responseData?.data || [];

  return (
    <div className="bg-white dark:bg-[#1a1a1a] rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm p-5 h-full flex flex-col">
      <div className="flex items-center justify-between gap-2 mb-6 border-b border-slate-200 dark:border-slate-800 pb-4">
        <div className="flex items-center gap-2">
          <GitBranch className="w-5 h-5 text-orange-500" />
          <h3 className="font-semibold text-slate-900 dark:text-white">Model Strategy Comparison</h3>
        </div>
        {responseData?.is_synthetic && <DemoDataBadge />}
      </div>

      <div className="flex-1 min-h-[300px]">
        {loading ? (
          <div className="w-full h-full flex items-center justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
          </div>
        ) : error ? (
          <div className="w-full h-full flex items-center justify-center text-red-500">Failed to load model comparison</div>
        ) : data.length === 0 ? (
          // "SF-GARCH-LSTM"/"Bond-BERT" are undeployed ridge-regression
          // stand-ins with no tracked real return history to compare (see
          // api/metrics_api.py::get_model_comparison docstring) -- an empty
          // chart with no explanation would look like a loading glitch, so
          // this states the honest reason instead.
          <div className="w-full h-full flex items-center justify-center text-center text-sm text-slate-400 dark:text-slate-500 px-6">
            Unavailable — no deployed model has tracked return history to compare yet.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 5, right: 30, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#334155" opacity={0.2} />
            <XAxis dataKey="name" tick={{ fill: '#64748b', fontSize: 12 }} tickLine={false} axisLine={{ stroke: '#334155', opacity: 0.5 }} />
            <YAxis tickFormatter={(val) => `${val}%`} tick={{ fill: '#64748b', fontSize: 12 }} tickLine={false} axisLine={false} />
            <Tooltip 
              contentStyle={{ backgroundColor: 'rgba(15, 23, 42, 0.9)', borderColor: '#334155', borderRadius: '8px', color: '#f8fafc' }}
              itemStyle={{ fontSize: 13 }}
            />
            <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '10px' }} />
            <Line type="monotone" dataKey="SF-GARCH-LSTM" stroke="#3b82f6" strokeWidth={3} dot={{ r: 4, strokeWidth: 2 }} activeDot={{ r: 6 }} />
            <Line type="monotone" dataKey="Bond-BERT" stroke="#10b981" strokeWidth={2} dot={{ r: 3 }} />
            <Line type="monotone" dataKey="Benchmark (SPY)" stroke="#64748b" strokeWidth={2} strokeDasharray="5 5" dot={false} />
          </LineChart>
        </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
