import { useEffect, useRef, useState } from "react";
import { trainingStatusWsUrl, USE_MOCK } from "../api/client";
import {
  WS_INITIAL_RETRY_DELAY_MS,
  isAuthRejection,
  nextRetryDelay,
} from "./wsReconnect";

export interface TrainingJobStatus {
  status: string;
  exit_code?: number | null;
}

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
 *
 * Reconnects follow wsReconnect.ts (1 s -> 30 s backoff, no retry after an
 * auth rejection). Mock mode opens no socket: there is no backend to
 * broadcast, and Models.tsx's GET /jobs/{id} poll is the authoritative
 * completion signal in both modes anyway.
 */
export function useTrainingStatus(): Record<string, TrainingJobStatus> {
  const [statuses, setStatuses] = useState<Record<string, TrainingJobStatus>>({});
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryDelayRef = useRef(WS_INITIAL_RETRY_DELAY_MS);
  const aliveRef = useRef(true);

  useEffect(() => {
    if (USE_MOCK) return;
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
        retryDelayRef.current = WS_INITIAL_RETRY_DELAY_MS;
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

      ws.onclose = (event: CloseEvent) => {
        wsRef.current = null;
        if (!aliveRef.current) return;
        if (retryRef.current) clearTimeout(retryRef.current);
        if (isAuthRejection(event?.code)) {
          console.warn(
            `useTrainingStatus: /ws/training/status rejected the API token (close ${event.code}); not retrying`
          );
          return;
        }
        const delay = retryDelayRef.current;
        retryDelayRef.current = nextRetryDelay(delay);
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
