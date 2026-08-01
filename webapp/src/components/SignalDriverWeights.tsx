import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { Activity } from 'lucide-react';
import DemoDataBadge from './DemoDataBadge';

interface SHAPFeature {
  name: string;
  value: number;
}

// No caller currently passes `data` (see screens/Models.tsx) -- this always
// renders as the synthetic fallback below. Kept as fixed illustrative
// numbers, not wired to signals/multifactor.py's real weights, since doing
// that is a real integration (api/metrics_api.py::get_symbol_signals
// already exposes a per-module breakdown, but it's per-symbol, not the
// universe-wide aggregate this component's own copy claims to show) rather
// than a one-line fix. The DemoDataBadge below exists so this is never
// mistaken for real telemetry (CONSTRAINT #4) until that wiring lands.
const PLACEHOLDER_DATA: SHAPFeature[] = [
  { name: 'FinBERT Sentiment', value: 0.286 },
  { name: 'GARCH Volatility', value: 0.214 },
  { name: 'RSI(14)', value: 0.173 },
  { name: 'MACD', value: -0.125 },
  { name: 'Sector Heat', value: 0.082 },
  { name: 'Retail Flow (Emoji)', value: 0.055 },
  { name: 'SMA(50) Cross', value: -0.045 },
  { name: 'Housing Starts', value: 0.020 },
];

export default function SignalDriverWeights({ data }: { data?: SHAPFeature[] }) {
  const isSynthetic = !data;
  const plotData = data || PLACEHOLDER_DATA;

  // Sort by absolute value for standard SHAP display
  const sortedData = [...plotData].sort((a, b) => Math.abs(b.value) - Math.abs(a.value));

  const CustomTooltip = ({ active, payload }: any) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;
      const isPositive = data.value >= 0;
      return (
        <div className="bg-white dark:bg-slate-800 p-3 border border-slate-200 dark:border-slate-700 rounded-lg shadow-lg">
          <p className="font-semibold text-slate-900 dark:text-slate-100">{data.name}</p>
          <p className={`text-sm ${isPositive ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}>
            Marginal Impact: {(data.value * 100).toFixed(1)}%
          </p>
        </div>
      );
    }
    return null;
  };

  return (
    <div className="bg-white dark:bg-[#1a1a1a] rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm p-5 h-full flex flex-col">
      <div className="flex items-center gap-2 mb-4">
        <Activity className="w-5 h-5 text-purple-500" />
        <h3 className="font-semibold text-slate-900 dark:text-white">Signal Driver Weights</h3>
        {isSynthetic && <DemoDataBadge />}
      </div>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-6">
        {isSynthetic
          ? 'Illustrative example only — not wired to live per-symbol signal weights yet.'
          : 'Displays mean absolute contribution (score × weight) per signal module across the universe.'}
        {' '}Linear weight decomposition — not Shapley marginal contributions.
      </p>
      
      <div className="flex-1 min-h-[300px]">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            layout="vertical"
            data={sortedData}
            margin={{ top: 5, right: 30, left: 100, bottom: 5 }}
          >
            <CartesianGrid strokeDasharray="3 3" horizontal={true} vertical={true} stroke="#334155" opacity={0.2} />
            <XAxis type="number" tickFormatter={(val) => `${(val * 100).toFixed(0)}%`} tick={{ fill: '#64748b', fontSize: 12 }} />
            <YAxis dataKey="name" type="category" width={120} tick={{ fill: '#64748b', fontSize: 12 }} />
            <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(148, 163, 184, 0.1)' }} />
            <Bar dataKey="value" radius={[0, 4, 4, 0]}>
              {sortedData.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={entry.value >= 0 ? '#10b981' : '#ef4444'} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
