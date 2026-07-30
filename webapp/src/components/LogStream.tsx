import React, { useEffect, useRef, useState } from "react";
import { jobStreamUrl, USE_MOCK } from "../api/client";

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
    <div className="bg-zinc-950 border border-zinc-800 rounded-lg p-4 font-mono text-sm text-zinc-100 flex flex-col h-96">
      <div className="flex justify-between items-center mb-2 pb-2 border-b border-zinc-800">
        <span className="font-semibold text-zinc-300">Live Console Output</span>
        <div className="flex items-center space-x-2">
          <label className="flex items-center gap-1 text-xs text-zinc-400">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
            />
            Auto-scroll
          </label>
          <input
            type="text"
            placeholder="Filter logs..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="bg-zinc-900 border border-zinc-700 text-xs text-zinc-200 px-2 py-1 rounded focus:outline-none focus:border-zinc-500"
          />
          <button
            onClick={() => setLogs([])}
            className="text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 px-2 py-1 rounded"
          >
            Clear
          </button>
        </div>
      </div>
      <div ref={listRef} className="flex-1 overflow-y-auto space-y-1">
        {USE_MOCK ? (
          <div className="text-zinc-500 text-xs italic">
            Log streaming is only available in live mode.
          </div>
        ) : filteredLogs.length === 0 ? (
          <div className="text-zinc-500 text-xs italic">No logs received yet...</div>
        ) : (
          filteredLogs.map((line, idx) => (
            <div key={idx} className="whitespace-pre-wrap break-all leading-snug">
              {line}
            </div>
          ))
        )}
      </div>
    </div>
  );
};
