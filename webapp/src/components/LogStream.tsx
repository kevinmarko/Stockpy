import React, { useEffect, useState } from "react";

interface LogStreamProps {
  jobId?: string;
  isStreaming?: boolean;
}

export const LogStream: React.FC<LogStreamProps> = ({ jobId, isStreaming }) => {
  const [logs, setLogs] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);

  useEffect(() => {
    if (!jobId || !isStreaming) return;

    const eventSource = new EventSource(`http://localhost:8601/jobs/${jobId}/stream`);

    eventSource.onmessage = (event) => {
      if (event.data) {
        setLogs((prev) => [...prev, event.data]);
      }
    };

    eventSource.addEventListener("end", () => {
      eventSource.close();
    });

    eventSource.onerror = () => {
      eventSource.close();
    };

    return () => {
      eventSource.close();
    };
  }, [jobId, isStreaming]);

  const filteredLogs = logs.filter((line) =>
    line.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <div className="bg-zinc-950 border border-zinc-800 rounded-lg p-4 font-mono text-sm text-zinc-100 flex flex-col h-96">
      <div className="flex justify-between items-center mb-2 pb-2 border-b border-zinc-800">
        <span className="font-semibold text-zinc-300">Live Console Output</span>
        <div className="flex items-center space-x-2">
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
      <div className="flex-1 overflow-y-auto space-y-1">
        {filteredLogs.length === 0 ? (
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
