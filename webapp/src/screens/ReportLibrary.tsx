/**
 * ReportLibrary.tsx — the webapp port of Streamlit's "Report Library" tab
 * (gui/panels/reports_library.py). Four sections, matching that panel's own
 * layout: the daily report, the two orchestrator dashboards, daily
 * briefings, and validation reports (summary JSON + per-strategy HTML).
 *
 * Backed by GET /reports (the manifest — name/kind/size/mtime, no content)
 * and GET /reports/{name} (content, fetched on demand). Every HTML report
 * (daily report / dashboards / validation HTML) is DOWNLOAD-ONLY by default
 * with an explicit opt-in "View inline" toggle, mirroring
 * gui/panels/reports_library.py::_html_file_block's own deliberate choice
 * not to auto-render a multi-MB HTML blob on every screen visit. Briefings
 * (small markdown text) render inline unconditionally, matching the
 * Streamlit panel. A validation summary renders as a collapsed-by-default
 * `<details>` JSON view, matching `st.expander`.
 *
 * Not registered in App.tsx / NAV_ITEMS by this PR (Agent A owns App.tsx
 * exclusively) — recommended placement: `/operations/reports`, carded on
 * OperationsHub next to Console, per this repo's "read-only research/
 * analytics screen" nav-placement convention (see
 * .claude/skills/new-pwa-screen/SKILL.md).
 */
import { useRef, useState, type SyntheticEvent } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { CommandJobParams, JobRecord, ReportContent, ReportFile, ReportManifest } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { usePoll } from "../hooks/usePoll";
import { ErrorState, Loading, Notice, Button, EmptyState, StaleDataNotice } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { LogStream } from "../components/LogStream";
import { downloadBlob } from "../utils/csv";
import { timeAgo } from "../format";
import { theme } from "../theme";
import { fmtBytes, mimeFor, textFor, MiniMarkdown } from "../reportRender";

/** One HTML-kind report (daily report / dashboard / validation HTML):
 * mtime + size caption, an opt-in "View inline" toggle (content is fetched
 * ONLY once the operator opts in — never prefetched), and a Download button
 * that fetches content on demand if not already loaded. */
function HtmlReportBlock({ file }: { file: ReportFile }) {
  const [viewing, setViewing] = useState(false);
  const [content, setContent] = useState<ReportContent | null>(null);
  const fetchContent = useMutation(() => api.getReport(file.name));

  const ensureContent = async (): Promise<ReportContent | undefined> => {
    if (content) return content;
    const result = await fetchContent.run();
    if (result) setContent(result);
    return result;
  };

  const handleToggleView = async () => {
    if (viewing) {
      setViewing(false);
      return;
    }
    await ensureContent();
    setViewing(true);
  };

  const handleDownload = async () => {
    const result = await ensureContent();
    if (!result) return;
    downloadBlob(textFor(result), file.name, mimeFor(result));
  };

  return (
    <div className="card card-pad" style={{ marginTop: "var(--s-3)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "var(--s-2)", flexWrap: "wrap" }}>
        <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontWeight: 700 }}>{file.name}</span>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
          {fmtBytes(file.size)} · {timeAgo(file.mtime)}
        </span>
      </div>

      <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-3)" }}>
        <Button
          variant="neutral"
          onClick={handleToggleView}
          pending={fetchContent.pending && !viewing}
          data-testid={`view-inline-${file.name}`}
        >
          {viewing ? "Hide" : "🔎 View inline"}
        </Button>
        <Button variant="neutral" onClick={handleDownload} data-testid={`download-${file.name}`}>
          ⬇️ Download
        </Button>
      </div>

      {fetchContent.error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-3)" }}>
          <span aria-hidden>⚠️</span>
          <span>{fetchContent.error}</span>
        </Notice>
      )}

      {viewing && content && (
        <div style={{ marginTop: "var(--s-3)" }}>
          {content.reason ? (
            <Notice variant="warn">
              <span aria-hidden>⚠️</span>
              <span>{content.reason}</span>
            </Notice>
          ) : (
            <iframe
              title={file.name}
              srcDoc={content.text ?? ""}
              // Same-origin content this platform itself generated; scripts
              // allowed so an inline-Plotly dashboard actually renders its
              // charts (mirrors Streamlit's components.html default), but no
              // top-navigation/popup/form permissions -- the iframe can't
              // navigate or pop anything out of this page.
              sandbox="allow-scripts allow-same-origin"
              style={{ width: "100%", height: 640, border: `1px solid ${theme.border}`, borderRadius: "var(--r-sm)" }}
            />
          )}
        </div>
      )}
    </div>
  );
}

/** A validation summary (parsed JSON): collapsed-by-default `<details>`,
 * matching Streamlit's `st.expander`. Content is fetched once, on first
 * expand. */
function ValidationSummaryBlock({ file }: { file: ReportFile }) {
  const [content, setContent] = useState<ReportContent | null>(null);
  const fetchContent = useMutation(() => api.getReport(file.name));

  const handleToggle = async (e: SyntheticEvent<HTMLDetailsElement>) => {
    if (e.currentTarget.open && !content) {
      const result = await fetchContent.run();
      if (result) setContent(result);
    }
  };

  return (
    <details
      className="card card-pad"
      style={{ marginTop: "var(--s-3)" }}
      onToggle={handleToggle}
      data-testid={`validation-summary-${file.name}`}
    >
      <summary style={{ cursor: "pointer", fontFamily: "var(--font-mono, ui-monospace, monospace)", fontWeight: 700 }}>
        🧾 {file.name}
        <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)", fontWeight: 400, marginLeft: "var(--s-2)" }}>
          {fmtBytes(file.size)} · {timeAgo(file.mtime)}
        </span>
      </summary>

      {fetchContent.pending && <Loading lines={2} />}
      {fetchContent.error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-2)" }}>
          <span aria-hidden>⚠️</span>
          <span>{fetchContent.error}</span>
        </Notice>
      )}
      {content?.reason && (
        <Notice variant="warn" style={{ marginTop: "var(--s-2)" }}>
          <span aria-hidden>⚠️</span>
          <span>{content.reason}</span>
        </Notice>
      )}
      {content && content.json != null && (
        <pre
          style={{
            marginTop: "var(--s-2)",
            padding: "var(--s-3)",
            background: theme.surface2,
            borderRadius: "var(--r-sm)",
            overflowX: "auto",
            fontSize: "var(--t-caption)",
          }}
        >
          {JSON.stringify(content.json, null, 2)}
        </pre>
      )}
    </details>
  );
}

const TERMINAL_STATUSES = new Set(["success", "failed", "cancelled", "unknown"]);

/** "Generate today's briefing" — posts the SAME job-creation flow
 * Commands.tsx's RunCommandControl uses (POST /jobs, job_type "command",
 * command "daily_briefing.py"), not a bespoke trigger. Not high-stakes
 * (daily_briefing.py carries no HIGH_STAKES_COMMANDS entry), so no
 * confirmation dialog. On success, calls `onGenerated` so the manifest
 * (which now has a new/updated briefing_*.md row) refetches. */
function GenerateBriefingButton({ onGenerated }: { onGenerated: () => void }) {
  const [activeJob, setActiveJob] = useState<JobRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const notifiedRef = useRef(false);

  usePoll(
    async () => {
      if (!activeJob) return;
      try {
        const updated = await api.getJobStatus(activeJob.job_id);
        setActiveJob(updated);
        if (updated.status === "success" && !notifiedRef.current) {
          notifiedRef.current = true;
          onGenerated();
        }
      } catch {
        // Transient poll failure -- try again next tick.
      }
    },
    1500,
    Boolean(activeJob) && !TERMINAL_STATUSES.has(activeJob?.status ?? "")
  );

  const run = async () => {
    setError(null);
    notifiedRef.current = false;
    try {
      const params: CommandJobParams = {
        command: "daily_briefing.py",
        subcommand: null,
        args: [],
        confirm: false,
      };
      const job = await api.createJob("command", { ...params });
      setActiveJob(job);
    } catch (err: any) {
      setError(err?.message ?? String(err));
    }
  };

  return (
    <div style={{ marginBottom: "var(--s-3)" }}>
      <Button
        variant="primary"
        onClick={run}
        pending={Boolean(activeJob) && !TERMINAL_STATUSES.has(activeJob?.status ?? "")}
        data-testid="generate-briefing-button"
      >
        📝 Generate today's briefing
      </Button>
      {error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-2)" }}>
          <span aria-hidden>⚠️</span>
          <span>{error}</span>
        </Notice>
      )}
      {activeJob && (
        <div style={{ marginTop: "var(--s-2-5)" }}>
          <span style={{ color: theme.textSecondary, fontSize: "var(--t-caption)" }} data-testid="generate-briefing-status">
            Job {activeJob.job_id} — {activeJob.status}
          </span>
          <div style={{ marginTop: "var(--s-2)" }}>
            <LogStream jobId={activeJob.job_id} isStreaming={Boolean(activeJob)} />
          </div>
        </div>
      )}
    </div>
  );
}

function BriefingsSection({ files, onReload }: { files: ReportFile[]; onReload: () => void }) {
  const [selected, setSelected] = useState<string | null>(files[0]?.name ?? null);
  const active = selected ?? files[0]?.name ?? null;
  const { data, loading, error, status, reload: reloadBriefing } = useApi<ReportContent>(
    () => (active ? api.getReport(active) : Promise.resolve(null as unknown as ReportContent)),
    [active]
  );

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
      <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)` }}>
        <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>📝 Daily briefings</h2>
      </div>
      <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>

      <GenerateBriefingButton onGenerated={onReload} />

      {files.length === 0 ? (
        <EmptyState title="No briefings yet" hint="Generate one with the button above." />
      ) : (
        <>
          <label className="tile-label" style={{ display: "block", marginBottom: "var(--s-1-5)" }}>
            Select a briefing (newest first)
          </label>
          <div className="select-wrap" style={{ marginBottom: "var(--s-3)" }}>
            <select
              className="select"
              value={active ?? ""}
              onChange={(e) => setSelected(e.target.value)}
              data-testid="briefing-select"
            >
              {files.map((f) => (
                <option key={f.name} value={f.name}>
                  {f.name} ({timeAgo(f.mtime)})
                </option>
              ))}
            </select>
          </div>

          {loading && <Loading lines={3} />}
          {!loading && error && <ErrorState message={error} status={status} onRetry={reloadBriefing} />}
          {!loading && !error && data && (
            data.reason ? (
              <Notice variant="warn">
                <span aria-hidden>⚠️</span>
                <span>{data.reason}</span>
              </Notice>
            ) : (
              <MiniMarkdown text={data.text ?? ""} />
            )
          )}
        </>
      )}
      </div>
    </section>
  );
}

export function ReportLibrary() {
  const nav = useNavigate();
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));
  const { data, loading, error, status, stale, cachedAt, reload } = useApi<ReportManifest>(
    () => api.getReports(),
    []
  );

  const byKind = (kind: ReportFile["kind"]) => (data?.reports ?? []).filter((r) => r.kind === kind);

  return (
    <div className="screen">
      <button
        onClick={back}
        style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: theme.textSecondary, fontSize: "var(--t-callout)", marginBottom: "var(--s-2)" }}
      >
        ← Back
      </button>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "var(--s-4)" }}>
        <div>
          <h1 className="screen-title" style={{ marginBottom: "var(--s-1)" }}>Report Library</h1>
          <p className="screen-sub">
            Every report the platform has generated — the daily report, orchestrator
            dashboards, daily briefings, and validation reports — in one place.
          </p>
        </div>
      </div>

      <TabGuide tabKey="reports" />

      {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}

      {loading && <Loading lines={4} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}

      {!loading && !error && data && (
        data.reports.length === 0 ? (
          <EmptyState
            title="No reports generated yet"
            hint={data.reason ?? "Run the pipeline, generate a briefing, or run the validation harness."}
          />
        ) : (
            <div className="dashboard-layout" style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
              <div key="daily-report">
                <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
                  <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)` }}>
                    <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>📰 Daily report</h2>
                  </div>
                  <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
                  {byKind("daily_report").length === 0 ? (
                    <p style={{ color: theme.textMuted, fontSize: "var(--t-body)", margin: 0 }}>
                      No daily report yet — generated every advisory cycle.
                    </p>
                  ) : (
                    byKind("daily_report").map((f) => <HtmlReportBlock key={f.name} file={f} />)
                  )}
                  </div>
                </section>
              </div>

              <div key="orchestrator-dashboards">
                <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
                  <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)` }}>
                    <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>📊 Orchestrator dashboards</h2>
                    <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-1)" }}>
                      Only refresh on a manual main_orchestrator.py run — their modified
                      time may lag the latest advisory cycle. Large files; inline view
                      is opt-in only.
                    </p>
                  </div>
                  <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
                  {byKind("dashboard").length === 0 ? (
                    <p style={{ color: theme.textMuted, fontSize: "var(--t-body)", margin: 0 }}>
                      No orchestrator dashboards yet — run main_orchestrator.py to generate them.
                    </p>
                  ) : (
                    byKind("dashboard").map((f) => <HtmlReportBlock key={f.name} file={f} />)
                  )}
                  </div>
                </section>
              </div>

              <div key="briefings">
                <BriefingsSection files={byKind("briefing")} onReload={reload} />
              </div>

              <div key="validation-reports">
                <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
                  <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)` }}>
                    <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>✅ Validation reports</h2>
                  </div>
                  <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
                  {byKind("validation_summary").length === 0 && byKind("validation_html").length === 0 ? (
                    <p style={{ color: theme.textMuted, fontSize: "var(--t-body)", margin: 0 }}>
                      No validation reports yet — none are generated until a strategy
                      runs through the validation harness.
                    </p>
                  ) : (
                    <>
                      {byKind("validation_summary").map((f) => (
                        <ValidationSummaryBlock key={f.name} file={f} />
                      ))}
                      {byKind("validation_html").map((f) => (
                        <HtmlReportBlock key={f.name} file={f} />
                      ))}
                    </>
                  )}
                  </div>
                </section>
              </div>
            </div>
        )
      )}
    </div>
  );
}
