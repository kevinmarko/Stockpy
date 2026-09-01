import React from "react";
import { useApi } from "../hooks/useApi";
import { api } from "../api/client";
import { TrendsStitchDemoResponse } from "../api/types";
import { TrendsStitchChart } from "../components/charts/TrendsStitchChart";
import {
  AlertCircle,
  Activity
} from "lucide-react";

export const TrendsVisualizer: React.FC = () => {
  const { data, loading, error, reload } = useApi<TrendsStitchDemoResponse>(
    () => api.getTrendsStitchDemo(),
    []
  );

  return (
    <div className="flex flex-col h-full bg-zinc-950 p-6 overflow-y-auto">
      <div className="flex justify-between items-start mb-6">
        <div>
          <div className="flex items-center space-x-2">
            <h1 className="text-2xl font-semibold text-zinc-100 flex items-center">
              <Activity className="w-6 h-6 mr-2 text-zinc-400" />
              Google Trends SVI Stitching
            </h1>
          </div>
          <p className="text-zinc-400 mt-1 text-sm max-w-3xl">
            Demonstrates the stitching of multiple overlapping 90-day Google Trends Search Volume Index (SVI) queries into a single continuous time series.
          </p>
        </div>
      </div>

      {loading && !data && (
        <div className="flex justify-center items-center h-64 border border-zinc-800 rounded-lg bg-zinc-900/50">
          <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-500"></div>
        </div>
      )}

      {error && (
        <div className="bg-red-900/20 border border-red-900/50 rounded-lg p-4 flex flex-col items-center justify-center h-64 text-red-400 space-y-2">
          <AlertCircle className="w-8 h-8 mb-2" />
          <p className="font-medium text-lg">Failed to load trends data</p>
          <p className="text-sm opacity-80">{error}</p>
          <button 
            onClick={reload}
            className="mt-4 px-4 py-2 bg-red-900/40 hover:bg-red-900/60 transition-colors rounded text-sm text-red-200"
          >
            Retry
          </button>
        </div>
      )}

      {data && (
        <div className="space-y-6">
          <TrendsStitchChart 
            rawCurves={data.raw_curves} 
            stitchedCurve={data.stitched_curve} 
          />
        </div>
      )}
    </div>
  );
};
