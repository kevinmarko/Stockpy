import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, apiMeta } from "../api/client";
import type { PilotSummary } from "../api/types";
import { useApi } from "../hooks/useApi";
import { completeOnboarding } from "../onboarding";
import { CategoryChip, DeployableBadge, Loading } from "../components/ui";
import { RobinhoodConnectForm } from "../components/RobinhoodConnectForm";
import { fmtNum } from "../format";
import { theme } from "../theme";

/**
 * 3-step onboarding: Choose a Pilot -> Connect brokerage (paper-first) -> Set amount.
 * Completion is persisted client-side; the app then routes to the marketplace.
 */
export function Onboarding({ onDone }: { onDone: () => void }) {
  const nav = useNavigate();
  const [step, setStep] = useState(0);
  const [pilotId, setPilotId] = useState<string | null>(null);
  const [brokerage, setBrokerage] = useState<"paper" | "robinhood" | "skip" | null>(
    null
  );
  const [amount, setAmount] = useState<number>(500);
  const [rhConnected, setRhConnected] = useState(false);

  const { data: pilots, loading } = useApi<PilotSummary[]>(
    () => api.listPilots(),
    []
  );

  const deployable = useMemo(
    () => (pilots ?? []).filter((p) => p.headline.deployable),
    [pilots]
  );

  const canContinueStep1 =
    brokerage === "paper" || brokerage === "skip" ||
    (brokerage === "robinhood" && rhConnected);

  const finish = () => {
    completeOnboarding({
      pilotId: pilotId ?? undefined,
      brokerage: brokerage ?? "skip",
      amount,
    });
    onDone();
    if (pilotId) nav(`/pilots/${pilotId}`);
    else nav("/");
  };

  return (
    <div className="screen" style={{ paddingBottom: 24 }}>
      {/* progress dots */}
      <div style={{ display: "flex", gap: 6, marginBottom: 20 }}>
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            style={{
              height: 4,
              flex: 1,
              borderRadius: 2,
              background: i <= step ? theme.growth : theme.surface2,
            }}
          />
        ))}
      </div>

      {step === 0 && (
        <>
          <h1 className="screen-title">Choose a Pilot</h1>
          <p className="screen-sub">
            Pilots are Stockpy's own quant strategies, ranked by honest,
            overfitting-gated backtests. Pick one to follow.
          </p>
          {loading ? (
            <Loading lines={4} />
          ) : (
            <div className="list">
              {(pilots ?? []).map((p) => (
                <button
                  key={p.id}
                  onClick={() => setPilotId(p.id)}
                  className="card card-pad"
                  style={{
                    textAlign: "left",
                    marginBottom: 10,
                    border:
                      pilotId === p.id
                        ? `1.5px solid ${theme.growth}`
                        : "1px solid var(--border)",
                    background:
                      pilotId === p.id ? "rgba(16,185,129,0.06)" : "var(--surface)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <span style={{ fontWeight: 700, fontSize: 16 }}>{p.name}</span>
                    <CategoryChip category={p.category} />
                  </div>
                  <div
                    style={{
                      display: "flex",
                      gap: 10,
                      marginTop: 8,
                      alignItems: "center",
                    }}
                  >
                    <span
                      className="num"
                      style={{ color: theme.growth, fontWeight: 700 }}
                    >
                      {p.headline.sharpe == null
                        ? "—"
                        : `${fmtNum(p.headline.sharpe, 2)} Sharpe`}
                    </span>
                    <DeployableBadge deployable={p.headline.deployable} />
                  </div>
                </button>
              ))}
            </div>
          )}
          <button
            className="btn btn-primary btn-block"
            style={{ marginTop: 16 }}
            disabled={!pilotId}
            onClick={() => setStep(1)}
          >
            Continue
          </button>
        </>
      )}

      {step === 1 && (
        <>
          <h1 className="screen-title">Connect brokerage</h1>
          <p className="screen-sub">
            Stockpy is advisory and <strong>paper-first</strong>. Following a Pilot
            builds a gated order queue you confirm yourself — no live order is ever
            placed automatically.
          </p>

          <button
            onClick={() => setBrokerage("paper")}
            className="card card-pad"
            style={{
              textAlign: "left",
              width: "100%",
              marginBottom: 10,
              border:
                brokerage === "paper"
                  ? `1.5px solid ${theme.growth}`
                  : "1px solid var(--border)",
            }}
          >
            <div style={{ fontWeight: 700, fontSize: 16 }}>
              📝 Paper trading (recommended)
            </div>
            <div style={{ color: theme.textSecondary, fontSize: 13, marginTop: 4 }}>
              Simulated fills against your watchlist. Zero real money at risk.
            </div>
          </button>

          <button
            onClick={() => setBrokerage("robinhood")}
            className="card card-pad"
            style={{
              textAlign: "left",
              width: "100%",
              marginBottom: 10,
              border:
                brokerage === "robinhood"
                  ? `1.5px solid ${theme.growth}`
                  : "1px solid var(--border)",
            }}
          >
            <div style={{ fontWeight: 700, fontSize: 16 }}>
              🔗 Connect Robinhood{rhConnected ? " — connected" : ""}
            </div>
            <div style={{ color: theme.textSecondary, fontSize: 13, marginTop: 4 }}>
              Credentials go only to your local backend and are verified with a
              read-only login before anything is saved — never sent anywhere else.
            </div>
          </button>

          {brokerage === "robinhood" && !rhConnected && (
            <RobinhoodConnectForm onConnected={() => setRhConnected(true)} />
          )}

          <button
            onClick={() => setBrokerage("skip")}
            className="card card-pad"
            style={{
              textAlign: "left",
              width: "100%",
              border:
                brokerage === "skip"
                  ? `1.5px solid ${theme.growth}`
                  : "1px solid var(--border)",
            }}
          >
            <div style={{ fontWeight: 700, fontSize: 16 }}>Browse only for now</div>
            <div style={{ color: theme.textSecondary, fontSize: 13, marginTop: 4 }}>
              Explore Pilots without connecting. You can link a brokerage later.
            </div>
          </button>

          <div className="notice notice-info" style={{ marginTop: 16 }}>
            <span>ℹ️</span>
            <span>
              Execution mode is currently <strong>{apiMeta.mockMode ?? "review"}</strong>.
              The broker path stays quarantined until you explicitly confirm each queue.
            </span>
          </div>

          <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
            <button className="btn" style={{ flex: 1 }} onClick={() => setStep(0)}>
              Back
            </button>
            <button
              className="btn btn-primary"
              style={{ flex: 2 }}
              disabled={!canContinueStep1}
              onClick={() => setStep(2)}
            >
              Continue
            </button>
          </div>
        </>
      )}

      {step === 2 && (
        <>
          <h1 className="screen-title">Set amount</h1>
          <p className="screen-sub">
            How much would you allocate to{" "}
            <strong>
              {deployable.find((p) => p.id === pilotId)?.name ??
                pilots?.find((p) => p.id === pilotId)?.name ??
                "this Pilot"}
            </strong>
            ? This sizes the preview queue — you confirm before anything runs.
          </p>

          <label className="tile-label" htmlFor="ob-amount">
            Allocation (USD)
          </label>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 22, fontWeight: 700, color: theme.textMuted }}>
              $
            </span>
            <input
              id="ob-amount"
              className="field"
              type="number"
              inputMode="decimal"
              min={100}
              step={0.01}
              value={amount}
              onChange={(e) => setAmount(Math.max(0, Number(e.target.value)))}
            />
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            {[250, 500, 1000, 2500].map((a) => (
              <button
                key={a}
                className="chip"
                style={{ flex: 1, justifyContent: "center", minHeight: 38 }}
                onClick={() => setAmount(a)}
              >
                ${a}
              </button>
            ))}
          </div>

          <div style={{ display: "flex", gap: 10, marginTop: 24 }}>
            <button className="btn" style={{ flex: 1 }} onClick={() => setStep(1)}>
              Back
            </button>
            <button
              className="btn btn-primary"
              style={{ flex: 2 }}
              onClick={finish}
            >
              Get started
            </button>
          </div>

          <button
            onClick={() => {
              completeOnboarding({ brokerage: "skip" });
              onDone();
              nav("/");
            }}
            style={{
              background: "none",
              border: "none",
              color: theme.textMuted,
              width: "100%",
              marginTop: 16,
              fontSize: 13,
            }}
          >
            Skip for now
          </button>
        </>
      )}
    </div>
  );
}
