import React from "react";
import { useApi } from "../hooks/useApi";
import { api } from "../api/client";
import { TrendsStitchDemoResponse } from "../api/types";
import { TrendsStitchChart } from "../components/charts/TrendsStitchChart";
import { Loading, ErrorState } from "../components/ui";

export const TrendsVisualizer: React.FC = () => {
  const { data, loading, error, status, reload } = useApi<TrendsStitchDemoResponse>(
    () => api.getTrendsStitchDemo(),
    []
  );

  return (
    <div className="screen">
      <h1 className="screen-title">SVI Stitching Algorithm Demo</h1>
      <p className="screen-sub">
        Demonstrates the overlapping-window stitching algorithm used to reconstruct a continuous
        long-term Google Trends Search Volume Index (SVI) series from adjacent 90-day intervals.
        When real, already-ingested Google Trends data is available (opt-in, off by default),
        this demo uses it directly, labeled by its real query term below. Otherwise it falls
        back to running the algorithm against real SPY trading volume as an honestly-labeled
        stand-in ("SPY Volume Proxy" in the chart below) — a substitution always disclosed by
        name, never presented as real search-volume data.
      </p>

      {loading && !data && <Loading lines={4} />}

      {error && <ErrorState message={error} status={status} onRetry={reload} />}

      {data && (
        <div style={{ marginTop: "var(--s-4)" }}>
          <TrendsStitchChart
            rawCurves={data.raw_curves}
            stitchedCurve={data.stitched_curve}
          />
        </div>
      )}
    </div>
  );
};
