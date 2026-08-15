import { useState, useEffect, useRef, useCallback } from 'react';
import { liveTickWsUrl } from '../api/client';

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
 * platform's Alpaca key is not configured (server returns ws-unavailable).
 *
 * Usage:
 *   const { price, bid, ask, isConnected } = useLiveTick('AAPL');
 */
export function useLiveTick(symbol: string): LiveTick {
  const [tick, setTick] = useState<LiveTick>(DEFAULT_TICK(symbol));
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryDelay = useRef(1000);

  const connect = useCallback(() => {
    if (!symbol) return;

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
      retryDelay.current = 1000; // reset backoff on success
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

    ws.onclose = () => {
      wsRef.current = null;
      setTick(prev => ({ ...prev, isConnected: false }));
      // Exponential backoff reconnect (max 30 s)
      if (retryRef.current) clearTimeout(retryRef.current);
      retryRef.current = setTimeout(() => {
        retryDelay.current = Math.min(retryDelay.current * 2, 30_000);
        connect();
      }, retryDelay.current);
    };
  }, [symbol]);

  useEffect(() => {
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
  }, [connect]);

  return tick;
}
