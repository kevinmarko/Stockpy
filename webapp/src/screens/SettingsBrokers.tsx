import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BrokerageStatus } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { useBrokerageLoginJob } from "../hooks/useBrokerageLoginJob";
import { PHASE_LABEL, formatLoginCountdown, loginFailureMessage } from "../brokerageLoginCopy";
import {
  Button,
  ErrorState,
  Loading,
  MetricBadge,
  Notice,
} from "../components/ui";
import { Modal } from "../components/Modal";
import { theme } from "../theme";
import { SectionCard } from "../components/SectionCard";
import { RobinhoodConnectForm } from "../components/RobinhoodConnectForm";
import { TabGuide } from "../components/TabGuide";

export function SettingsBrokers() {
  const {
    data: brokerageData,
    loading: brokerageLoading,
    error: brokerageError,
    status: brokerageHttpStatus,
    reload: reloadBrokerage,
  } = useApi<BrokerageStatus>(() => api.getBrokerageStatus(), []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
      <div>
        <h2 style={{ margin: "0 0 var(--s-1)", fontSize: "var(--t-title)" }}>Brokerage Connections</h2>
        <p style={{ color: "var(--text-secondary)", margin: 0, fontSize: "var(--t-body)" }}>
          Manage your connections to external brokers like Robinhood.
        </p>
      </div>

      <TabGuide tabKey="settings-brokers" />

      <BrokerageSection
        data={brokerageData}
        loading={brokerageLoading}
        error={brokerageError}
        httpStatus={brokerageHttpStatus}
        reload={reloadBrokerage}
      />
    </div>
  );
}


/**
 * Brokerage connection — view status and connect/disconnect Robinhood AFTER
 * onboarding, over GET /brokerage/status + POST /brokerage/{connect,disconnect}.
 * Before this, connectBrokerage was reachable only during onboarding and
 * disconnect/status had no UI at all. connect/disconnect fail closed
 * server-side when their gates aren't set (BROKERAGE_CONNECT_ENABLED +
 * FOLLOW_API_TOKEN + loopback-only -- see api/pilots_api.py); this UI renders
 * whatever the server actually returned and never echoes credentials. Reuses
 * the SAME RobinhoodConnectForm as onboarding so the intake path can't drift.
 *
 * The refreshBrokerage() button (POST /brokerage/refresh) is deliberately
 * NOT gated on `data.connected` -- unlike connect/disconnect, it never reads
 * a request body; it just calls fetch_account_snapshot(force=True)
 * server-side, which logs in with whatever RH_USERNAME/RH_PASSWORD is
 * already configured in THIS MACHINE's .env via the device-approval push
 * flow, no typed input needed. That works identically regardless of what `data.connected` (a
 * client-side read of the SAME env vars, which can be stale relative to a
 * just-restarted backend, or simply not yet reflect a hand-edited .env)
 * currently reports -- so the button is offered in both branches, as a
 * faster alternative to typing credentials into RobinhoodConnectForm below.
 * An honest failure (including a job that times out) surfaces the same way
 * either way, never a fabricated success.
 */
function BrokerageSection({
  data,
  loading,
  error,
  httpStatus: status,
  reload,
}: {
  data: BrokerageStatus | null;
  loading: boolean;
  error: string | null;
  httpStatus: number | null;
  reload: () => void;
}) {
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);
  const disconnect = useMutation(() => api.disconnectBrokerage());
  const refresh = useBrokerageLoginJob();
  const refreshRunning = refresh.starting || refresh.job?.state === "running";

  const doDisconnect = async () => {
    await disconnect.run();
    setConfirmingDisconnect(false);
    refresh.reset(); // clear a stale refresh notice from before the disconnect
    reload();
  };

  const doRefresh = () => {
    disconnect.reset(); // clear a stale disconnect notice from before this attempt
    void refresh.start("refresh");
  };

  // Reload the moment the refresh job reaches a terminal state (success or
  // otherwise) -- keyed on job_id + state so a retried refresh's own
  // terminal transition fires this again too.
  useEffect(() => {
    if (refresh.job && refresh.job.state !== "running") {
      reload(); // pick up the (possibly now-populated) connected/has_account_snapshot flags
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh.job?.job_id, refresh.job?.state]);

  return (
    <SectionCard
      title="Brokerage"
      sub="Connect Robinhood for read-only portfolio snapshots, or disconnect to clear the stored credentials."
    >
      {loading && <Loading lines={2} />}
      {!loading && error && (
        <ErrorState message={error} status={status} onRetry={reload} />
      )}
      {!loading && !error && data && (
        <div className="list">
          {data.connected && (
            <div className="row">
              <span className="row-title">Robinhood</span>
              <MetricBadge
                label="Connected"
                value={data.has_account_snapshot ? "snapshot ready" : "no snapshot yet"}
                good={true}
              />
            </div>
          )}

          <div
            style={{
              display: "flex",
              gap: "var(--s-2-5)",
              flexWrap: "wrap",
              alignItems: "center",
              marginTop: data.connected ? "var(--s-3)" : 0,
            }}
          >
            <Button variant="neutral" onClick={doRefresh} pending={refreshRunning}>
              {refreshRunning
                ? "Connecting…"
                : data.connected
                  ? "🔐 Force fresh login"
                  : "🔐 Connect using .env credentials"}
            </Button>
            {refreshRunning && refresh.job && (
              <Button variant="neutral" onClick={() => void refresh.cancel()}>
                Cancel
              </Button>
            )}
            {data.connected && (
              <Button
                variant="neutral"
                onClick={() => setConfirmingDisconnect(true)}
              >
                Disconnect
              </Button>
            )}
          </div>
          {refreshRunning && refresh.job && (
            <p
              style={{
                color: theme.textSecondary,
                fontSize: "var(--t-caption)",
                marginTop: "var(--s-1-5)",
                marginBottom: 0,
              }}
            >
              <span aria-hidden>📱</span> Open the Robinhood app and approve
              this login — {PHASE_LABEL[refresh.job.phase]}{" "}
              {formatLoginCountdown(refresh.job.seconds_remaining)} remaining
            </p>
          )}
          <p
            style={{
              color: theme.textMuted,
              fontSize: "var(--t-caption)",
              marginTop: "var(--s-1-5)",
              marginBottom: 0,
              lineHeight: 1.4,
            }}
          >
            {data.connected ? (
              <>
                Bypasses the daily cache and re-authenticates against Robinhood
                right now — equivalent to <code>python3 main.py --refresh-account</code>.
              </>
            ) : (
              <>
                Logs in with <code>RH_USERNAME</code>/<code>RH_PASSWORD</code> already in
                this machine's <code>.env</code> via the device-approval push flow — no
                typing required, just tap "approve" in the Robinhood app. Haven't set
                those yet? Use the form below instead.
              </>
            )}
          </p>
          {refresh.error && (
            <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
              <span>⚠️</span>
              <span>{refresh.error}</span>
            </Notice>
          )}
          {!refresh.error && refresh.job?.state === "succeeded" && (
            <Notice variant="success" style={{ marginTop: "var(--s-2-5)" }}>
              <span>✅</span>
              <span>Refreshed — Robinhood account snapshot updated.</span>
            </Notice>
          )}
          {!refresh.error &&
            refresh.job &&
            refresh.job.state !== "running" &&
            refresh.job.state !== "succeeded" && (
              <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
                <span>⚠️</span>
                <span>{loginFailureMessage(refresh.job)}</span>
              </Notice>
            )}
          {disconnect.error && (
            <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
              <span>⚠️</span>
              <span>{disconnect.error}</span>
            </Notice>
          )}

          {data.connected ? (
            <p
              style={{
                color: theme.textMuted,
                fontSize: "var(--t-caption)",
                marginTop: "var(--s-3)",
                lineHeight: 1.45,
              }}
            >
              Credentials are stored only on this local machine and are never
              shown here.
            </p>
          ) : (
            <>
              <p
                style={{
                  color: theme.textSecondary,
                  fontSize: "var(--t-body)",
                  marginTop: "var(--s-3)",
                  marginBottom: "var(--s-3)",
                }}
              >
                Not connected. Credentials go only to your local backend and are
                verified with a read-only login before anything is saved.
              </p>
              <RobinhoodConnectForm
                onConnected={() => {
                  refresh.reset(); // clear a stale refresh notice from before this connect
                  reload();
                }}
              />
            </>
          )}
        </div>
      )}

      {confirmingDisconnect && (
        <Modal
          ariaLabel="Disconnect brokerage"
          onClose={() => setConfirmingDisconnect(false)}
        >
          <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>
            Disconnect Robinhood?
          </h2>
          <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0 }}>
            Clears the stored Robinhood credentials from this machine. Portfolio
            snapshots stop refreshing until you reconnect.
          </p>
          <div style={{ display: "flex", gap: "var(--s-2-5)", marginTop: "var(--s-4-5)" }}>
            <Button
              variant="neutral"
              onClick={() => setConfirmingDisconnect(false)}
              style={{ flex: 1 }}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={doDisconnect}
              pending={disconnect.pending}
              style={{ flex: 2 }}
            >
              Disconnect
            </Button>
          </div>
        </Modal>
      )}
    </SectionCard>
  );
}
