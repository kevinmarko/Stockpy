import { useState } from "react";
import { api } from "../api/client";
import type {
  TrendsBatch,
  TrendsSviResponse,
  TrendsTermRow,
  TrendsTermsResponse,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { EmptyState, ErrorState, Loading, Notice } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { TrendsRawWindowsChart } from "../components/TrendsRawWindowsChart";
import { TrendsSeamsTable } from "../components/TrendsSeamsTable";
import { TrendsStitchChart } from "../components/TrendsStitchChart";
import { TrendsAsviChart } from "../components/TrendsAsviChart";

const DASH = "—";

/** Hours -> "3 h ago" / "2.9 d ago". null/NaN -> "—" (never "0 h ago"). */
function fmtAge(hours: number | null | undefined): string {
  if (hours === null || hours === undefined || !Number.isFinite(hours)) return DASH;
  if (hours < 48) return `${hours.toFixed(hours < 10 ? 1 : 0)} h ago`;
  return `${(hours / 24).toFixed(1)} d ago`;
}

function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

const fmtNum2 = (v: number | null | undefined): string =>
  v === null || v === undefined || !Number.isFinite(v) ? DASH : v.toFixed(2);

const fmtInt = (v: number | null | undefined): string =>
  v === null || v === undefined || !Number.isFinite(v) ? DASH : String(v);

/** Freshest complete, non-stale row; else the first row. */
function pickDefaultTerm(rows: TrendsTermRow[]): string | null {
  if (rows.length === 0) return null;
  const fresh = rows
    .filter((r) => r.has_complete_batch && r.stale === false)
    .sort((a, b) => (a.age_hours ?? Infinity) - (b.age_hours ?? Infinity));
  return (fresh[0] ?? rows[0]).term;
}

function optionLabel(r: TrendsTermRow): string {
  const hints: string[] = [];
  if (!r.has_complete_batch) hints.push("no complete download");
  if (r.stale) hints.push("stale");
  return hints.length ? `${r.term} (${hints.join(", ")})` : r.term;
}

function Pair({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: "var(--t-caption)", color: "var(--text-secondary)" }}>{label}</div>
      <div className="num" style={{ fontSize: "var(--t-callout)", overflowWrap: "anywhere" }}>
        {children}
      </div>
    </div>
  );
}

function attemptDiffers(att: TrendsBatch | null, complete: TrendsBatch | null): boolean {
  if (!att) return false;
  if (!complete) return true;
  return att.batch_id !== complete.batch_id;
}

function HonestyStrip({
  svi,
  terms,
}: {
  svi: TrendsSviResponse;
  terms: TrendsTermsResponse;
}) {
  const b = svi.batch;
  const att = svi.latest_attempt;
  const coverage =
    terms.tracked_with_data === null || terms.tracked_universe_size === null
      ? DASH
      : `Trends data for ${terms.tracked_with_data} of ${terms.tracked_universe_size} tracked symbols`;
  return (
    <section className="card card-pad" data-testid="trends-honesty-strip" aria-label="Data honesty">
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: "var(--s-3)",
        }}
      >
        <Pair label="Last complete download">
          {b ? `${fmtWhen(b.finished_at ?? b.started_at)} (${fmtAge(b.age_hours)})` : DASH}
        </Pair>
        <Pair label="Batch status">
          {b ? (
            <>
              {b.status} ({fmtInt(b.windows_fetched)}/{fmtInt(b.windows_expected)} windows)
              {b.stale ? (
                <span className="badge badge-warn" style={{ marginLeft: "var(--s-1-5)" }}>
                  stale
                </span>
              ) : null}
            </>
          ) : (
            DASH
          )}
        </Pair>
        {attemptDiffers(att, b) && att ? (
          <Pair label="Latest attempt">
            {att.status} ({fmtInt(att.windows_fetched)}/{fmtInt(att.windows_expected)} windows)
            {att.reason ? ` — ${att.reason}` : ""}
          </Pair>
        ) : null}
        <Pair label="Coverage">{coverage}</Pair>
        <Pair label="Refresh">{svi.enabled ? "on" : "off"}</Pair>
        <Pair label="Window / overlap">
          {fmtInt(svi.window_days)} d / {fmtInt(svi.overlap_days)} d
        </Pair>
      </div>
    </section>
  );
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <section className="card card-pad" style={{ minWidth: 0 }}>
      <h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>{title}</h2>
      <p className="screen-sub" style={{ margin: "var(--s-1) 0 var(--s-3)" }}>
        {hint}
      </p>
      {children}
    </section>
  );
}

function SviBody({ svi, terms }: { svi: TrendsSviResponse; terms: TrendsTermsResponse }) {
  const reason = svi.reason ?? DASH;
  if (svi.status === "error") {
    return (
      <ErrorState message={`Search attention could not be read: ${reason}`} status={null} />
    );
  }
  if (svi.status === "partial_only" || svi.status === "no_data" || svi.status === "disabled") {
    const title =
      svi.status === "disabled"
        ? "Trends refresh is disabled"
        : svi.status === "no_data"
          ? "No Trends data for this term"
          : "Only a partial download so far";
    return (
      <>
        <HonestyStrip svi={svi} terms={terms} />
        <EmptyState title={title} hint={reason} />
      </>
    );
  }

  const stitchedOnly = svi.status === "stitched_only";
  const rc = svi.recompute_check;
  const asvi = svi.asvi;
  return (
    <>
      <HonestyStrip svi={svi} terms={terms} />

      <section className="card card-pad" data-testid="trends-latest-asvi">
        <div style={{ fontSize: "var(--t-caption)", color: "var(--text-secondary)" }}>
          Latest ASVI
        </div>
        <div className="num" style={{ fontSize: "var(--t-title, 28px)", fontWeight: 700 }}>
          {fmtNum2(asvi?.latest)}
        </div>
        <div style={{ fontSize: "var(--t-caption)", color: "var(--text-secondary)" }}>
          {asvi?.latest_date ?? DASH}
        </div>
      </section>

      {rc && !rc.matches ? (
        <Notice variant="warn" data-testid="trends-mismatch-warning">
          The stored series does not match a fresh stitch of its own download (max difference{" "}
          {fmtNum2(rc.max_abs_diff)} over {fmtInt(rc.compared_days)} days)
        </Notice>
      ) : null}

      {stitchedOnly ? <Notice variant="warn">{reason}</Notice> : null}

      {!stitchedOnly ? (
        <Section
          title="Raw windows"
          hint="Each short window exactly as Google returned it, scaled so its own peak is 100."
        >
          <TrendsRawWindowsChart windows={svi.raw_windows} />
        </Section>
      ) : null}

      {!stitchedOnly ? (
        <Section
          title="Seams between windows"
          hint="Where two windows overlap, the later one is multiplied by a factor so the overlap lines up; a guard keeps the factor at 1 when an overlap is empty."
        >
          <TrendsSeamsTable seams={svi.seams} />
        </Section>
      ) : null}

      <Section
        title="Rescaled and stitched series"
        hint="The windows after rescaling, joined into one continuous daily line."
      >
        <TrendsStitchChart
          rescaled={svi.rescaled_windows}
          stitched={svi.stitched}
          seams={svi.seams}
        />
      </Section>

      <Section
        title="ASVI (abnormal search volume)"
        hint="Today's searches compared with the term's usual level over the lookback; above 0 means more attention than usual."
      >
        <TrendsAsviChart asvi={svi.asvi} />
      </Section>
    </>
  );
}

function TermView({
  term,
  terms,
}: {
  term: string;
  terms: TrendsTermsResponse;
}) {
  const { data, loading, error, status, reload } = useApi<TrendsSviResponse>(
    () => api.getTrendsSvi(term),
    [term]
  );
  if (loading) return <Loading lines={3} />;
  if (error) return <ErrorState message={error} status={status} onRetry={reload} />;
  if (!data) return <EmptyState title="No data" hint="The server returned nothing for this term." />;
  return <SviBody svi={data} terms={terms} />;
}

export function TrendsAttention() {
  const [picked, setPicked] = useState<string | null>(null);
  const terms = useApi<TrendsTermsResponse>(() => api.getTrendsTerms(), []);

  const header = (
    <>
      <h1 className="screen-title">Search Attention (Google Trends)</h1>
      <p className="screen-sub">
        Google only gives daily search values in short windows, and each window is scaled so its
        own peak is 100. The platform downloads overlapping windows and stitches them into one
        series, then computes ASVI: today's searches compared with the usual level. It is a
        diagnostic for context, advisory only, and never places an order.
      </p>
      <TabGuide tabKey="trends" />
    </>
  );

  if (terms.loading) {
    return (
      <div className="screen">
        {header}
        <Loading lines={3} />
      </div>
    );
  }
  if (terms.error) {
    return (
      <div className="screen">
        {header}
        <ErrorState message={terms.error} status={terms.status} onRetry={terms.reload} />
      </div>
    );
  }
  const t = terms.data;
  if (!t) {
    return (
      <div className="screen">
        {header}
        <EmptyState title="No data" hint="The server returned nothing." />
      </div>
    );
  }
  if (t.status !== "ok" || t.terms.length === 0) {
    const title =
      t.status === "error"
        ? "Trends terms could not be read"
        : t.status === "disabled"
          ? "Trends refresh is disabled"
          : "No Trends data yet";
    return (
      <div className="screen">
        {header}
        <EmptyState title={title} hint={t.reason ?? DASH} />
      </div>
    );
  }

  const term = picked && t.terms.some((r) => r.term === picked) ? picked : pickDefaultTerm(t.terms);

  return (
    <div className="screen">
      {header}
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)", minWidth: 0 }}>
        {terms.stale ? <Notice variant="warn">Showing cached data; the network is unreachable.</Notice> : null}
        <label style={{ display: "flex", flexDirection: "column", gap: "var(--s-1)", maxWidth: 320 }}>
          <span style={{ fontSize: "var(--t-caption)", color: "var(--text-secondary)" }}>Term</span>
          <select
            aria-label="Term"
            value={term ?? ""}
            onChange={(e) => setPicked(e.target.value)}
          >
            {t.terms.map((r) => (
              <option key={r.term} value={r.term}>
                {optionLabel(r)}
              </option>
            ))}
          </select>
        </label>
        {term ? <TermView term={term} terms={t} /> : null}
      </div>
    </div>
  );
}
