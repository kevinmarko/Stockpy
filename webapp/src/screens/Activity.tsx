import { useState } from "react";
import { apiMeta } from "../api/client";
import { ActivityFeed } from "../components/ActivityFeed";
import { TabGuide } from "../components/TabGuide";
import { InfoTip } from "../components/ui";
import { theme } from "../theme";

const CATEGORIES = ["ALL", "SYSTEM", "EXECUTION", "RISK", "REGIME"];

export function Activity() {
  const [activeCategory, setActiveCategory] = useState("ALL");

  return (
    <div className="screen">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="screen-title">Activity</h1>
          <p className="screen-sub">Recent alerts from the Stockpy pipeline.</p>
        </div>
        {apiMeta.useMock && (
          <InfoTip triggerClassName="chip" triggerStyle={{ marginTop: "var(--s-2-5)" }} content="Running on mock data">
            demo
          </InfoTip>
        )}
      </div>

      <TabGuide tabKey="activity" />

      {/* Filter Ribbon */}
      <div
        style={{
          display: "flex",
          gap: "var(--s-2)",
          overflowX: "auto",
          paddingBottom: "var(--s-2)",
          marginTop: "var(--s-4)",
          scrollbarWidth: "none", // hide scrollbar Firefox
          msOverflowStyle: "none", // hide scrollbar IE/Edge
        }}
        className="hide-scrollbar"
      >
        {CATEGORIES.map((cat) => (
          <button
            key={cat}
            className={`btn ${activeCategory === cat ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setActiveCategory(cat)}
            style={{
              padding: "var(--s-1) var(--s-3)",
              fontSize: "var(--t-small)",
              borderRadius: "999px",
              whiteSpace: "nowrap",
            }}
          >
            {cat}
          </button>
        ))}
      </div>

      {/*
        The feed component owns loading / error / honest empty-state (reason)
        and the level-labeled alert cards — the screen just frames it.
      */}
      <div style={{ marginTop: "var(--s-2)" }}>
        <ActivityFeed limit={50} categoryFilter={activeCategory} />
      </div>

      <p
        style={{
          color: theme.textMuted,
          fontSize: "var(--t-footnote)",
          marginTop: "var(--s-5)",
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
