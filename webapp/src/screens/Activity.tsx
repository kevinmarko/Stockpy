import { apiMeta } from "../api/client";
import { ActivityFeed } from "../components/ActivityFeed";
import { theme } from "../theme";

export function Activity() {
  return (
    <div className="screen">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="screen-title">Activity</h1>
          <p className="screen-sub">Recent alerts from the Stockpy pipeline.</p>
        </div>
        {apiMeta.useMock && (
          <span className="chip" style={{ marginTop: 10 }} title="Running on mock data">
            demo
          </span>
        )}
      </div>

      {/*
        The feed component owns loading / error / honest empty-state (reason)
        and the level-labeled alert cards — the screen just frames it.
      */}
      <div style={{ marginTop: 8 }}>
        <ActivityFeed limit={50} />
      </div>

      <p
        style={{
          color: theme.textMuted,
          fontSize: 11.5,
          marginTop: 20,
          textAlign: "center",
          lineHeight: 1.5,
        }}
      >
        Alerts are read from the structured alert log. Configure ALERT_FILE_PATH
        to enable the feed on a live backend.
      </p>
    </div>
  );
}
