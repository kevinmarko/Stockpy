import React, { useEffect, useRef, useState } from "react";
import { jobStreamUrl, USE_MOCK } from "../api/client";
import { Button } from "./ui";
import { Toggle } from "./Toggle";
import { theme } from "../theme";

interface LogStreamProps {
  jobId?: string;
  isStreaming?: boolean;
}

export const LogStream: React.FC<LogStreamProps> = ({ jobId, isStreaming }) => {
  const [logs, setLogs] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    // A new job id means a genuinely different job -- starting it from the
    // previous job's log lines still on screen would read as one garbled
    // transcript instead of two separate runs.
    setLogs([]);
    if (!jobId || !isStreaming || USE_MOCK) return;

    const eventSource = new EventSource(jobStreamUrl(jobId, 0));

    eventSource.onmessage = (event) => {
      if (event.data) {
        setLogs((prev) => [...prev, event.data]);
      }
    };

    eventSource.addEventListener("end", () => {
      // The job actually finished — no reason to let the browser reconnect.
      eventSource.close();
    });

    // Deliberately NOT closing here: EventSource reconnects automatically on
    // a transient error (network blip, backgrounded tab) and resends the
    // last `id:` it saw as a `Last-Event-ID` header, which the backend uses
    // to resume from the right offset instead of replaying from the start.
    eventSource.onerror = () => {};

    return () => {
      eventSource.close();
    };
  }, [jobId, isStreaming]);

  useEffect(() => {
    // Scroll only this panel's own list, never scrollIntoView() -- that
    // bubbles up through every scrollable ancestor, including the outer
    // page, and can carry the whole viewport away from the Console screen.
    if (autoScroll && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  const filteredLogs = logs.filter((line) =>
    line.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
      <div
        className="drag-handle"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "var(--s-2)",
          padding: "var(--s-3)",
          borderBottom: `1px solid ${theme.border}`,
          cursor: "grab",
        }}
      >
        <span style={{ fontWeight: 700, color: theme.textSecondary, fontSize: "var(--t-callout)" }}>
          Live Console Output
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2-5)" }}>
          <Toggle
            label="Auto-scroll"
            checked={autoScroll}
            onChange={setAutoScroll}
          />
          <input
            type="text"
            placeholder="Filter logs..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="input"
            style={{ width: 160, fontSize: "var(--t-caption)", padding: "var(--s-1) var(--s-2)", minHeight: "auto" }}
          />
          <Button onClick={() => setLogs([])}>Clear</Button>
        </div>
      </div>
      <div
        ref={listRef}
        style={{
          flex: 1,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: "var(--s-1)",
          fontFamily: "var(--font-mono, ui-monospace, monospace)",
          fontSize: "var(--t-caption)",
          color: "#10b981",
          background: "#0b0e11",
          padding: "var(--s-3)",
          borderRadius: "var(--r-sm)",
          border: `1px solid ${theme.borderStrong}`,
          scrollBehavior: "smooth",
        }}
      >
        {USE_MOCK ? (
          <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", fontStyle: "italic" }}>
            Log streaming is only available in live mode.
          </div>
        ) : filteredLogs.length === 0 ? (
          <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", fontStyle: "italic" }}>
            No logs received yet...
          </div>
        ) : (
          filteredLogs.map((line, idx) => (
            <div key={idx} style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", lineHeight: 1.4 }}>
              {line}
            </div>
          ))
        )}
      </div>
    </section>
  );
};
