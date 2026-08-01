import { Modal } from "./Modal";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import type { ReportContent } from "../api/types";
import { Loading, Notice } from "./ui";
import { MiniMarkdown } from "../reportRender";
import { theme } from "../theme";

interface ReportPreviewModalProps {
  /** A real `ReportFile.name` from GET /reports -- never an arbitrary
   *  operator-facing title. Resolved server-side against the same manifest
   *  GET /reports/{name} already enforces (no client-built path). */
  name: string;
  onClose: () => void;
}

export function ReportPreviewModal({ name, onClose }: ReportPreviewModalProps) {
  const { data, loading, error } = useApi<ReportContent>(() => api.getReport(name), [name]);

  return (
    <Modal ariaLabel={name} onClose={onClose}>
      <div style={{ width: "min(90vw, 720px)", maxHeight: "70vh", overflowY: "auto", padding: "var(--s-2)" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            marginBottom: "var(--s-3)",
            borderBottom: `1px solid ${theme.border}`,
            paddingBottom: "var(--s-2)",
          }}
        >
          <span style={{ fontWeight: 700, fontSize: "var(--t-subhead)", fontFamily: "var(--font-mono, ui-monospace, monospace)" }}>
            {name}
          </span>
          {data?.mtime && (
            <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>{data.mtime}</span>
          )}
        </div>

        {loading && <Loading lines={4} />}
        {!loading && error && (
          <Notice variant="warn">
            <span aria-hidden>⚠️</span>
            <span>{error}</span>
          </Notice>
        )}
        {!loading && !error && data && data.reason && (
          <Notice variant="warn">
            <span aria-hidden>⚠️</span>
            <span>{data.reason}</span>
          </Notice>
        )}
        {!loading && !error && data && !data.reason && (
          <div
            style={{
              background: theme.surface,
              border: `1px solid ${theme.border}`,
              borderRadius: "var(--r-md)",
              padding: "var(--s-4)",
              fontSize: "var(--t-body)",
              lineHeight: 1.6,
              color: theme.textPrimary,
            }}
          >
            {data.content_type === "markdown" && <MiniMarkdown text={data.text ?? ""} />}
            {data.content_type === "json" && (
              <pre
                style={{
                  margin: 0,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  fontFamily: "var(--font-mono, ui-monospace, monospace)",
                  fontSize: "var(--t-caption)",
                }}
              >
                {JSON.stringify(data.json, null, 2)}
              </pre>
            )}
            {data.content_type === "html" && (
              <iframe
                title={name}
                srcDoc={data.text ?? ""}
                // Same-origin content this platform itself generated; scripts
                // allowed so an inline-Plotly dashboard actually renders its
                // charts, matching ReportLibrary's own inline-view iframe. No
                // top-navigation/popup/form permissions.
                sandbox="allow-scripts allow-same-origin"
                style={{ width: "100%", height: 480, border: `1px solid ${theme.border}`, borderRadius: "var(--r-sm)" }}
              />
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
