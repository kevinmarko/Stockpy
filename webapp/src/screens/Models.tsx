import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ModelRow } from "../api/types";
import { useApi } from "../hooks/useApi";
import { DeployableBadge, ErrorState, Loading, MetricBadge } from "../components/ui";
import { fmtDate, fmtNum } from "../format";
import { theme } from "../theme";

function ModelCard({ m }: { m: ModelRow }) {
  return (
    <section className="card card-pad" style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 15, wordBreak: "break-word" }}>{m.name}</div>
          {m.role && (
            <div style={{ color: theme.textMuted, fontSize: 12, marginTop: 2 }}>{m.role}</div>
          )}
        </div>
        <DeployableBadge deployable={m.deployable} />
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
        <MetricBadge
          label="DSR"
          value={m.cpcv_dsr == null ? "—" : fmtNum(m.cpcv_dsr, 3)}
          good={m.cpcv_dsr == null ? null : m.cpcv_dsr > 0.95}
        />
        <MetricBadge
          label="PBO"
          value={m.pbo == null ? "—" : fmtNum(m.pbo, 2)}
          good={m.pbo == null ? null : m.pbo < 0.5}
        />
        <MetricBadge label="Trained" value={fmtDate(m.trained_date)} />
        <MetricBadge label="N" value={m.n_train == null ? "—" : String(m.n_train)} />
      </div>
      {m.notes && (
        <p style={{ color: theme.textSecondary, fontSize: 12.5, lineHeight: 1.5, marginTop: 12 }}>
          {m.notes}
        </p>
      )}
    </section>
  );
}

export function Models() {
  const nav = useNavigate();
  const { data, loading, error, status, reload } = useApi<ModelRow[]>(
    () => api.getModels(),
    []
  );
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  return (
    <div className="screen">
      <button
        onClick={back}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          color: theme.textSecondary,
          fontSize: 14,
          marginBottom: 8,
        }}
      >
        ← Pilots
      </button>
      <h1 className="screen-title">The models</h1>
      <p className="screen-sub">
        The ML models behind the platform, with their honest CPCV validation
        metrics. A model that fails a gate is shown as not deployable.
      </p>

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        data.length === 0 ? (
          <div className="empty" style={{ padding: 30 }}>
            No model registry available yet.
          </div>
        ) : (
          <div style={{ marginTop: 12 }}>
            {data.map((m) => (
              <ModelCard key={m.name} m={m} />
            ))}
          </div>
        )
      )}
      <p
        style={{
          color: theme.textMuted,
          fontSize: 11.5,
          marginTop: 20,
          textAlign: "center",
          lineHeight: 1.5,
        }}
      >
        Deployable = CPCV-DSR &gt; 0.95 AND PBO &lt; 0.50. Metrics are never loosened
        to force a green badge.
      </p>
    </div>
  );
}
