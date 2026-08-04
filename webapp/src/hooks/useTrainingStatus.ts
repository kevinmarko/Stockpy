import { useEffect, useRef, useState } from "react";
import { trainingStatusWsUrl } from "../api/client";

export interface TrainingJobStatus {
  status: string;
  exit_code?: number | null;
}

// A few seconds is plenty for this hook -- unlike useLiveTick (a per-symbol
// price feed an operator stares at), a training job's lifecycle is measured
// in minutes, so there's no need for useLiveTick's fuller exponential-backoff
// convention. A fixed delay keeps this hook much simpler, per the "Retrain
// Now" feature's own scope.
const RECONNECT_DELAY_MS = 5000;

/**
 * useTrainingStatus — subscribes to the Control API's `/ws/training/status`
 * broadcast so a "Retrain Now" button (Models.tsx) can reflect a training
 * job's real lifecycle instead of flipping back the instant the
 * `POST /jobs` call resolves.
 *
 * One shared WebSocket connection for every in-flight job (not one per
 * job/symbol like useLiveTick) -- messages are `{job_id, status, ...}`
 * frames that merge into a `job_id`-keyed map rather than replacing a
 * single value. No REST-polling fallback: if the socket never connects (or
 * the server never sends a "finished" frame for a given job), the caller is
 * expected to apply its own client-side timeout rather than this hook
 * inventing one.
 */
export function useTrainingStatus(): Record<string, TrainingJobStatus> {
  const [statuses, setStatuses] = useState<Record<string, TrainingJobStatus>>({});
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;

    const connect = () => {
      if (!aliveRef.current) return;

      const ws = new WebSocket(trainingStatusWsUrl());
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (!msg || typeof msg.job_id !== "string" || typeof msg.status !== "string") return;
          setStatuses((prev) => ({
            ...prev,
            [msg.job_id]: { status: msg.status, exit_code: msg.exit_code ?? null },
          }));
        } catch {
          // Ignore malformed frames.
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        if (!aliveRef.current) return;
        if (retryRef.current) clearTimeout(retryRef.current);
        retryRef.current = setTimeout(connect, RECONNECT_DELAY_MS);
      };

      // onerror is always immediately followed by onclose in every browser
      // implementation -- reconnect is handled there, this is a no-op.
      ws.onerror = () => {};
    };

    connect();

    return () => {
      aliveRef.current = false;
      if (retryRef.current) clearTimeout(retryRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null; // prevent reconnect on intentional unmount
        wsRef.current.close();
      }
    };
  }, []);

  return statuses;
}
