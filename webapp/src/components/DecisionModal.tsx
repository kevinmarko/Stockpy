import { useState } from "react";
import { api } from "../api/client";
import type { DecisionCreateRequest } from "../api/types";
import { useMutation } from "../hooks/useMutation";
import { Button } from "./ui";
import { Modal } from "./Modal";
import { fmtNum } from "../format";
import { theme } from "../theme";

/**
 * Shared decision-journal confirm modal — log Acted/Passed/Modified against
 * one signal via POST /decisions. Used by both the Calibration screen's
 * current-signals scatter and SymbolDetail's per-symbol journal section, so
 * the logging UX (notes field, three action buttons, trade-link result
 * message) stays in exactly one place.
 *
 * `signal` is intentionally a minimal structural subset (not `MfeMaePoint`)
 * so any screen with just a symbol + the system's action/conviction can open
 * this without constructing an unrelated type.
 */
export interface DecisionSignal {
  symbol: string;
  action: string | null;
  conviction: number | null;
}

export function DecisionModal({
  signal,
  onClose,
  onLogged,
}: {
  signal: DecisionSignal;
  onClose: () => void;
  onLogged: () => void;
}) {
  const [notes, setNotes] = useState("");
  const { run, pending, error, result } = useMutation((body: DecisionCreateRequest) =>
    api.logDecision(body)
  );

  const submit = (action: DecisionCreateRequest["action_taken"]) => {
    void run({
      symbol: signal.symbol,
      action_taken: action,
      signal_action: signal.action ?? "—",
      conviction: signal.conviction,
      notes: notes.trim(),
      signal_ts: "",
    });
  };

  return (
    <Modal ariaLabel={`Log decision for ${signal.symbol}`} onClose={onClose}>
      <h2 style={{ margin: "0 0 2px", fontSize: "var(--t-title)" }}>Log decision — {signal.symbol}</h2>
      <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0 }}>
        System recommendation: <strong>{signal.action ?? "—"}</strong>
        {signal.conviction != null && <> · conviction {fmtNum(signal.conviction, 2)}</>}
      </p>

      {result ? (
        <div style={{ marginTop: 8 }}>
          <div
            className="notice"
            data-testid="decision-result"
            style={{
              background: "rgba(16, 185, 129, 0.1)",
              border: "1px solid rgba(16, 185, 129, 0.28)",
              color: "#a7f3d0",
            }}
          >
            <span>✅</span>
            <span>
              Logged: <strong>{result.symbol}</strong> → {result.action_taken}
              {result.action_taken === "acted" &&
                (result.trade_linked
                  ? ` · linked to trade #${result.trade_id}`
                  : " · no trade match within 24h")}
            </span>
          </div>
          <div style={{ display: "flex", marginTop: 16 }}>
            <Button variant="primary" block onClick={() => { onLogged(); onClose(); }}>
              Done
            </Button>
          </div>
        </div>
      ) : (
        <>
          <label htmlFor="dj-notes" className="tile-label" style={{ display: "block", margin: "10px 0 6px" }}>
            Notes {`(required when modifying)`}
          </label>
          <textarea
            id="dj-notes"
            className="input"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            placeholder="e.g. 'Halved size — position already large', 'Used a limit, not market'"
            style={{ width: "100%", resize: "vertical", minHeight: 68 }}
          />
          {error && (
            <div className="notice notice-warn" style={{ marginTop: 10 }}>
              <span>⚠️</span>
              <span>{error}</span>
            </div>
          )}
          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <Button variant="primary" onClick={() => submit("acted")} pending={pending} style={{ flex: 1 }}>
              ✅ Acted
            </Button>
            <Button variant="neutral" onClick={() => submit("passed")} pending={pending} style={{ flex: 1 }}>
              ⏭ Passed
            </Button>
            <Button
              variant="neutral"
              onClick={() => submit("modified")}
              pending={pending}
              disabled={!notes.trim()}
              style={{ flex: 1 }}
            >
              🔁 Modified
            </Button>
          </div>
          <p style={{ color: theme.textMuted, fontSize: 11, marginTop: 8 }}>
            "Modified" needs a note describing what you changed.
          </p>
        </>
      )}
    </Modal>
  );
}
