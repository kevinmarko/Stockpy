import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import type {
  TradeJournalBridgeStatus,
  TradeJournalCohort,
  TradeJournalEntriesResponse,
  TradeJournalEntry,
  TradeJournalInsights,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { Button, Chip, EmptyState, ErrorState, Input, Loading, Select, Tile } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { ReliabilityDiagram } from "./Calibration";
import { fmtDateTime, fmtNum, fmtPct, fmtSignedUsd } from "../format";
import { theme } from "../theme";

/**
 * Trade Journal — the Retrospective Learning Loop's primary read surface.
 * Composes every closed paper trade into a full retrospective (what
 * happened, the real MFE/MAE/Edge Ratio recomputed from price history, and
 * decision provenance) plus a plain-text (no-LLM, fully templated)
 * narrative sentence, and surfaces a manual/signal-driven/unknown cohort
 * breakdown alongside the existing conviction-calibration reliability
 * diagram (reused verbatim from Calibration.tsx — the SAME underlying
 * `pilots.calibration.calibration_view()` data).
 *
 * `decision.state` is NEVER inferred from `strategy_id`/`pilot_id` — a trade
 * with no captured decision snapshot honestly reports "unknown", even for a
 * strategy that is, in fact, signal-driven (three of this platform's
 * automated options writers — `dispersion_trading.py`, `copula_stat_arb.py`,
 * `zero_dte_engine.py` — do not yet capture one; see CLAUDE.md's
 * Retrospective Learning Loop bullet). The three cohorts below are
 * STRUCTURALLY separate — there is no combined/"overall" figure anywhere on
 * this screen (CONSTRAINT #4 — merging manual and signal-driven outcomes
 * would blur a real signal).
 */

const LIMIT_OPTIONS = [25, 50, 100, 200] as const;

// ---------------------------------------------------------------------------
// Decision-state / evaluation-availability badges
// ---------------------------------------------------------------------------

function DecisionBadge({ state }: { state: TradeJournalEntry["decision"]["state"] }) {
  if (state === "signal_driven") {
    return <Chip label="🧠 Signal-driven" tone="growth" />;
  }
  if (state === "manual") {
    return <Chip label="🖐️ Manual" tone="caution" />;
  }
  return <Chip label="❓ Unknown" tone="muted" />;
}

function EvaluationUnavailableBadge({ reason }: { reason: string | null }) {
  return (
    <Chip
      label={`⚠️ Evaluation data unavailable — ${reason ?? "no reason given"}`}
      tone="decline"
    />
  );
}

// ---------------------------------------------------------------------------
// One entry
// ---------------------------------------------------------------------------

function fmtQty(qty: number | null): string {
  if (qty == null) return "—";
  return Number.isInteger(qty) ? String(qty) : qty.toFixed(2);
}

function OutcomeLine({ entry }: { entry: TradeJournalEntry }) {
  const positive = entry.realized_pnl != null && entry.realized_pnl >= 0;
  return (
    <span
      data-testid="trade-journal-outcome"
      style={{
        fontWeight: 700,
        color: entry.realized_pnl == null ? undefined : positive ? theme.growth : theme.decline,
      }}
    >
      {fmtSignedUsd(entry.realized_pnl)}
      {entry.realized_pnl_pct != null && (
        <> ({fmtPct(entry.realized_pnl_pct, 2, { fromFraction: true, signed: true })})</>
      )}
    </span>
  );
}

function EntryCard({ entry }: { entry: TradeJournalEntry }) {
  return (
    <div
      className="card card-pad"
      data-testid="trade-journal-entry"
      style={{ display: "flex", flexDirection: "column", gap: "var(--s-2-5)" }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: "var(--s-2)",
          flexWrap: "wrap",
        }}
      >
        <div>
          <div style={{ fontSize: "var(--t-callout)", fontWeight: 700 }}>
            {entry.symbol ? <Link to={`/symbol/${entry.symbol}`}>{entry.symbol}</Link> : "—"}
            <span style={{ color: theme.textMuted, fontWeight: 400, marginLeft: "var(--s-1-5)" }}>
              {entry.side ?? "—"} {fmtQty(entry.qty)}
            </span>
          </div>
          <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
            {fmtDateTime(entry.entry_ts)} @ {fmtNum(entry.entry_price, 2)} → {fmtDateTime(entry.exit_ts)} @{" "}
            {fmtNum(entry.exit_price, 2)} · {entry.close_reason ?? "unknown reason"}
          </div>
        </div>
        <div style={{ display: "flex", gap: "var(--s-1-5)", flexWrap: "wrap", justifyContent: "flex-end" }}>
          <DecisionBadge state={entry.decision.state} />
        </div>
      </div>

      <div>
        Realized P&amp;L: <OutcomeLine entry={entry} />
        {entry.holding_period_days != null && (
          <span style={{ color: theme.textMuted }}> · held {fmtNum(entry.holding_period_days, 1)}d</span>
        )}
      </div>

      <p style={{ margin: 0, color: theme.textSecondary, fontSize: "var(--t-label)", lineHeight: 1.5 }}>
        {entry.narrative}
      </p>

      {!entry.evaluation.available && <EvaluationUnavailableBadge reason={entry.evaluation.reason} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Patterns panel — calibration (reused) + three structurally-separate cohorts
// ---------------------------------------------------------------------------

function CohortCard({ title, cohort }: { title: string; cohort: TradeJournalCohort }) {
  return (
    <div className="card card-pad" data-testid={`trade-journal-cohort-${title.toLowerCase().replace(/\s+/g, "-")}`}>
      <div style={{ fontWeight: 700, marginBottom: "var(--s-2)" }}>{title}</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-3)" }}>
        <Tile label="Trades" value={String(cohort.n_trades)} />
        <Tile label="Win rate" value={fmtPct(cohort.win_rate, 0, { fromFraction: true })} />
        <Tile
          label="Mean P&L"
          value={fmtPct(cohort.mean_realized_pnl_pct, 1, { fromFraction: true, signed: true })}
          tone={
            cohort.mean_realized_pnl_pct == null
              ? undefined
              : cohort.mean_realized_pnl_pct >= 0
                ? "pos"
                : "neg"
          }
        />
      </div>
    </div>
  );
}

function CalibrationMini({ cal }: { cal: TradeJournalInsights["calibration"] }) {
  if (cal.total === 0) {
    return (
      <EmptyState
        title="No conviction data yet"
        hint={cal.reason ?? "Conviction scores appear here once trades close with a conviction annotation."}
      />
    );
  }
  if (cal.n_scored_bins === 0) {
    return (
      <EmptyState
        title="Not enough trades per bin yet"
        hint={`Every conviction bin has fewer than ${cal.min_trades_per_bin} trades, so no win rate is shown (never fabricated).`}
      />
    );
  }
  return (
    <>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-3)", marginBottom: "var(--s-3)" }}>
        <Tile label="Trades w/ conviction" value={String(cal.total)} />
        <Tile label="Overall win rate" value={fmtPct(cal.overall_win_rate, 1, { fromFraction: true })} />
        <Tile label="Calibration error" value={fmtNum(cal.calibration_error, 3)} />
      </div>
      <ReliabilityDiagram bins={cal.bins} />
    </>
  );
}

function PatternsPanel({ insights }: { insights: TradeJournalInsights }) {
  return (
    <>
      <div className="card card-pad" style={{ marginBottom: "var(--s-4)" }}>
        <h3 style={{ marginTop: 0, fontSize: "var(--t-callout)" }}>Conviction calibration</h3>
        <CalibrationMini cal={insights.calibration} />
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          gap: "var(--s-3)",
        }}
      >
        <CohortCard title="Signal-driven" cohort={insights.cohorts.signal_driven} />
        <CohortCard title="Manual" cohort={insights.cohorts.manual} />
        <CohortCard title="Unknown" cohort={insights.cohorts.unknown} />
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Bridge status — small, secondary diagnostic
// ---------------------------------------------------------------------------

function BridgeStatusNote({ status }: { status: TradeJournalBridgeStatus }) {
  if (status.completeness_pct == null) {
    return (
      <p style={{ color: theme.textMuted, fontSize: "var(--t-footnote)" }}>
        Evaluation bridge: {status.reason ?? "nothing to check yet."}
      </p>
    );
  }
  return (
    <p style={{ color: theme.textMuted, fontSize: "var(--t-footnote)" }}>
      Evaluation bridge{status.enabled ? "" : " (disabled)"}: {fmtPct(status.completeness_pct, 0)} of the last{" "}
      {status.window} closed trades matched a transactions_store row ({status.n_bridged}/{status.n_trades_checked}
      ).
    </p>
  );
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

export function TradeJournal() {
  const [symbol, setSymbol] = useState("");
  const [limit, setLimit] = useState<number>(50);

  const entriesState = useApi<TradeJournalEntriesResponse>(
    () => api.getTradeJournalEntries({ symbol: symbol || undefined, limit }),
    [symbol, limit]
  );
  const insightsState = useApi<TradeJournalInsights>(() => api.getTradeJournalInsights(), []);
  const bridgeState = useApi<TradeJournalBridgeStatus>(() => api.getTradeJournalBridgeStatus(), []);

  return (
    <div className="screen">
      <h1 className="screen-title">Trade Journal</h1>
      <p className="screen-sub">
        Every closed paper trade, composed into what happened, the real post-trade move (MFE/MAE/Edge
        Ratio), and — when captured — why the model made the call. Nothing here is inferred: a trade
        with no captured decision context honestly reads "unknown", never a guess.
      </p>

      <TabGuide tabKey="trade-journal" />

      <div
        style={{
          display: "flex",
          gap: "var(--s-3)",
          flexWrap: "wrap",
          alignItems: "flex-end",
          marginBottom: "var(--s-4)",
        }}
      >
        <Input
          label="Symbol"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          placeholder="All symbols"
        />
        <Select
          label="Show"
          value={String(limit)}
          onChange={(e) => setLimit(Number(e.target.value))}
          options={LIMIT_OPTIONS.map((n) => ({ value: String(n), label: `Most recent ${n}` }))}
        />
        <Button variant="neutral" onClick={entriesState.reload} disabled={entriesState.loading}>
          Refresh
        </Button>
      </div>

      {entriesState.loading && !entriesState.data ? (
        <Loading lines={4} />
      ) : entriesState.error && !entriesState.data ? (
        <ErrorState message={entriesState.error} status={entriesState.status} onRetry={entriesState.reload} />
      ) : !entriesState.data || entriesState.data.count === 0 ? (
        <EmptyState
          title="No trades yet"
          hint="Once a paper trade closes, its retrospective — what happened, the post-trade move, and (when captured) why — will appear here."
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
          {entriesState.data.entries.map((entry) => (
            <EntryCard key={entry.trade_id} entry={entry} />
          ))}
        </div>
      )}

      <div style={{ margin: "var(--s-6) 0 var(--s-3)" }}>
        <h2 style={{ margin: 0, fontSize: "var(--t-title)" }}>Patterns</h2>
        <p style={{ color: theme.textMuted, fontSize: "var(--t-label)", margin: "var(--s-0-5) 0 0" }}>
          Conviction calibration plus a manual / signal-driven / unknown cohort breakdown — always shown
          separately, never blended into one overall figure.
        </p>
      </div>

      {insightsState.loading && !insightsState.data ? (
        <Loading lines={3} />
      ) : insightsState.error && !insightsState.data ? (
        <ErrorState message={insightsState.error} status={insightsState.status} onRetry={insightsState.reload} />
      ) : insightsState.data ? (
        <PatternsPanel insights={insightsState.data} />
      ) : null}

      {bridgeState.data && (
        <div style={{ marginTop: "var(--s-5)" }}>
          <BridgeStatusNote status={bridgeState.data} />
        </div>
      )}
    </div>
  );
}
