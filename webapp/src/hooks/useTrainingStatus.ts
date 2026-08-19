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
const INITIAL_RETRY_DELAY_MS = 1000;
const MAX_RETRY_DELAY_MS = 30000;

/**
 * useTrainingStatus — subscribes to the Control API's `/ws/training/status`
 * broadcast so a "Retrain Now" button (Models.tsx) can reflect a training
 * job's real lifecycle instead of flipping back the instant the
 * `POST /jobs` call resolves.
 *
 * One shared WebSocket connection for every in-flight job (not one per
 * job/symbol like useLiveTick) -- messages are `{job_id, status, ...}`
 * frames that merge into a `job_id`-keyed map rather than replacing a
 * single value.
 */
export function useTrainingStatus(): Record<string, TrainingJobStatus> {
  const [statuses, setStatuses] = useState<Record<string, TrainingJobStatus>>({});
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryDelayRef = useRef(INITIAL_RETRY_DELAY_MS);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;

    const connect = () => {
      if (!aliveRef.current) return;

      if (wsRef.current) {
        wsRef.current.onopen = null;
        wsRef.current.onmessage = null;
        wsRef.current.onerror = null;
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }

      const ws = new WebSocket(trainingStatusWsUrl());
      wsRef.current = ws;

      ws.onopen = () => {
        retryDelayRef.current = INITIAL_RETRY_DELAY_MS;
      };

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
        const delay = retryDelayRef.current;
        retryDelayRef.current = Math.min(delay * 2, MAX_RETRY_DELAY_MS);
        retryRef.current = setTimeout(connect, delay);
      };

      // onerror is always immediately followed by onclose in browser WebSocket implementations
      ws.onerror = () => {};
    };

    connect();

    return () => {
      aliveRef.current = false;
      if (retryRef.current) clearTimeout(retryRef.current);
      if (wsRef.current) {
        wsRef.current.onopen = null;
        wsRef.current.onmessage = null;
        wsRef.current.onerror = null;
        wsRef.current.onclose = null; // prevent reconnect on intentional unmount
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  return statuses;
}
