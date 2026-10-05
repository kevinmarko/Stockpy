import { useState, useEffect, useRef, useCallback } from 'react';
import { liveTickWsUrl, USE_MOCK } from '../api/client';
import {
  WS_INITIAL_RETRY_DELAY_MS,
  isAuthRejection,
  nextRetryDelay,
} from './wsReconnect';

export interface LiveTick {
  symbol: string;
  price: number | null;
  bid: number | null;
  ask: number | null;
  source: string;
  isStale: boolean;
  isConnected: boolean;
  error: string | null;
}

const DEFAULT_TICK = (symbol: string): LiveTick => ({
  symbol,
  price: null,
  bid: null,
  ask: null,
  source: 'connecting',
  isStale: true,
  isConnected: false,
  error: null,
});

/**
 * useLiveTick — subscribe to live price ticks for a symbol via WebSocket.
 *
 * Falls back to REST polling every 5 s if the WebSocket fails or if the
 * server reports the tick stream as unavailable (ws-unavailable). The
 * stream pushes REST quotes from the configured provider (no separate real-time feed).
 *
 * Reconnects follow wsReconnect.ts: 1 s -> 30 s exponential backoff, reset
 * only once a socket opens, and no retry at all after an auth rejection
 * (the tick then reports `source: 'unauthorized'` with an error).
 *
 * Mock mode opens no socket and returns MOCK_TICK: no price (callers fall
 * back to the mock ladder's own quote), `source: 'mock'`, never a made-up
 * live price.
 *
 * Usage:
 *   const { price, bid, ask, isConnected } = useLiveTick('AAPL');
 */
export const MOCK_TICK = (symbol: string): LiveTick => ({
  ...DEFAULT_TICK(symbol),
  source: 'mock',
});

export function useLiveTick(symbol: string): LiveTick {
  const [tick, setTick] = useState<LiveTick>(
    USE_MOCK ? MOCK_TICK(symbol) : DEFAULT_TICK(symbol)
  );
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryDelay = useRef(WS_INITIAL_RETRY_DELAY_MS);

  const connect = useCallback(() => {
    if (!symbol || USE_MOCK) return;

    // Defensive only -- wsRef.current is always already null by the time
    // connect() actually runs today (both call sites below, the mount
    // effect and the retry timeout in onclose, only ever reach connect()
    // after wsRef.current has already been nulled). Kept in case a future
    // call site is added that doesn't hold that invariant; nulling every
    // handler (not just onclose) before close() guarantees none of them
    // fire again for this socket, even for an event already in flight.
    if (wsRef.current) {
      wsRef.current.onopen = null;
      wsRef.current.onmessage = null;
      wsRef.current.onerror = null;
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }

    const url = liveTickWsUrl(symbol);

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      retryDelay.current = WS_INITIAL_RETRY_DELAY_MS; // reset backoff on success
      setTick(prev => ({ ...prev, isConnected: true, error: null }));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.error) {
          setTick(prev => ({ ...prev, error: data.error }));
          return;
        }
        setTick({
          symbol: data.symbol ?? symbol,
          price: data.price ?? null,
          bid: data.bid ?? null,
          ask: data.ask ?? null,
          source: data.source ?? 'ws',
          isStale: data.is_stale ?? false,
          isConnected: true,
          error: null,
        });
      } catch {
        // Ignore malformed frames
      }
    };

    ws.onerror = () => {
      setTick(prev => ({ ...prev, error: 'WebSocket error', isConnected: false }));
    };

    ws.onclose = (event: CloseEvent) => {
      wsRef.current = null;
      if (retryRef.current) clearTimeout(retryRef.current);
      if (isAuthRejection(event?.code)) {
        // A wrong/missing token won't fix itself -- stop instead of
        // hammering the server (and its log) with rejected handshakes.
        setTick(prev => ({
          ...prev,
          isConnected: false,
          source: 'unauthorized',
          error: `Live tick stream rejected the API token (close ${event.code})`,
        }));
        return;
      }
      setTick(prev => ({ ...prev, isConnected: false }));
      const delay = retryDelay.current;
      retryDelay.current = nextRetryDelay(delay);
      retryRef.current = setTimeout(connect, delay);
    };
  }, [symbol]);

  useEffect(() => {
    // A new symbol starts from a fresh backoff and a fresh tick.
    retryDelay.current = WS_INITIAL_RETRY_DELAY_MS;
    setTick(USE_MOCK ? MOCK_TICK(symbol) : DEFAULT_TICK(symbol));
    connect();
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current);
      if (wsRef.current) {
        // Null every handler, not just onclose -- prevents onmessage/
        // onopen/onerror from firing on an event already in flight too,
        // in addition to suppressing the reconnect onclose would trigger.
        wsRef.current.onopen = null;
        wsRef.current.onmessage = null;
        wsRef.current.onerror = null;
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect, symbol]);

  return tick;
}
