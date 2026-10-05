/**
 * Shared reconnect policy for the PWA's backend WebSocket hooks
 * (useLiveTick, useTrainingStatus).
 *
 * Two rules, both from a 2026-10-02 incident where a tab whose token the
 * server rejected re-opened /ws/training/status several times a second and
 * flooded investyo.log with "rejected unauthenticated connection" lines:
 *
 *  1. Back off exponentially, 1 s doubling to a 30 s cap, and only reset the
 *     delay once a socket has actually opened.
 *  2. Never retry an auth rejection. api/ws_api.py accepts and then closes an
 *     unauthenticated socket with 4003 (a pre-accept close reaches the
 *     browser only as 1006, indistinguishable from "server down"). 1008 is
 *     the standard "policy violation" code, treated the same way. Retrying
 *     cannot fix a wrong token; a page reload after fixing it does.
 */

export const WS_INITIAL_RETRY_DELAY_MS = 1000;
export const WS_MAX_RETRY_DELAY_MS = 30_000;

/** Close codes that mean "the server refused you" -- never retried. */
export const WS_AUTH_REJECTED_CODES: ReadonlySet<number> = new Set([4001, 4003, 1008]);

export function isAuthRejection(code: number | undefined): boolean {
  return code !== undefined && WS_AUTH_REJECTED_CODES.has(code);
}

/** The delay after `current`, doubled and capped. */
export function nextRetryDelay(current: number): number {
  return Math.min(current * 2, WS_MAX_RETRY_DELAY_MS);
}
