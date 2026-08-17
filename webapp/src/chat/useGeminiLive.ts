/**
 * useGeminiLive.ts — React hook for managing Gemini Live WebSocket connection and audio streams.
 */

import { useState, useRef, useCallback, useEffect } from "react";
import { liveChatWsUrl, USE_MOCK } from "../api/client";
import { getEffectiveToken } from "../auth/apiToken";
import { AudioRecorder, AudioPlayer } from "./audioStreamer";

export type LiveConnectionStatus = "disconnected" | "connecting" | "connected" | "error";

export interface LiveMessageEvent {
  type: "user" | "model";
  text: string;
  isPartial?: boolean;
}

interface UseGeminiLiveOptions {
  onUserTranscript?: (text: string) => void;
  onModelTranscript?: (text: string, isPartial?: boolean) => void;
  onTurnComplete?: () => void;
  onThought?: (thought: string) => void;
  onError?: (error: string) => void;
}

export function useGeminiLive(options: UseGeminiLiveOptions = {}) {
  const [status, setStatus] = useState<LiveConnectionStatus>("disconnected");
  const [isMicActive, setIsMicActive] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [liveModel, setLiveModel] = useState<string>("gemini-3.1-flash-live-preview");
  const [liveVoice, setLiveVoice] = useState<string>("Aoede");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const recorderRef = useRef<AudioRecorder | null>(null);
  const playerRef = useRef<AudioPlayer | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  // Initialize audio player
  useEffect(() => {
    playerRef.current = new AudioPlayer((playing) => {
      setIsSpeaking(playing);
    });
    recorderRef.current = new AudioRecorder();

    return () => {
      playerRef.current?.close();
      recorderRef.current?.stop();
      wsRef.current?.close();
    };
  }, []);

  const disconnectLive = useCallback(() => {
    recorderRef.current?.stop();
    playerRef.current?.interrupt();
    setIsMicActive(false);
    setIsSpeaking(false);

    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.onerror = null;
      wsRef.current.onmessage = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    setStatus("disconnected");
  }, []);

  const connectLive = useCallback(
    async (initialContext?: string): Promise<void> => {
      disconnectLive();
      setStatus("connecting");
      setErrorMessage(null);

      if (USE_MOCK) {
        // Offline mock mode handler
        setStatus("connected");
        setLiveModel("gemini-3.1-flash-live-preview (mock)");
        return;
      }

      return new Promise<void>((resolve, reject) => {
        try {
          const token = getEffectiveToken();
          const url = liveChatWsUrl(token || undefined);
          const ws = new WebSocket(url);
          wsRef.current = ws;

          let isResolved = false;

          ws.onopen = () => {
            setStatus("connected");
            if (initialContext) {
              ws.send(JSON.stringify({ type: "context", text: initialContext }));
            }
            if (!isResolved) {
              isResolved = true;
              resolve();
            }
          };

          ws.onmessage = (event) => {
            try {
              const data = JSON.parse(event.data);
              switch (data.type) {
                case "connected":
                  if (data.model) setLiveModel(data.model);
                  if (data.voice) setLiveVoice(data.voice);
                  break;
                case "audio":
                  if (data.data) {
                    playerRef.current?.playChunk(data.data);
                  }
                  break;
                case "text":
                  if (data.content) {
                    optionsRef.current.onModelTranscript?.(data.content, true);
                  }
                  break;
                case "input_transcription":
                  if (data.text) {
                    optionsRef.current.onUserTranscript?.(data.text);
                  }
                  break;
                case "output_transcription":
                  if (data.text) {
                    optionsRef.current.onModelTranscript?.(data.text, false);
                  }
                  break;
                case "interrupted":
                  playerRef.current?.interrupt();
                  break;
                case "turn_complete":
                  optionsRef.current.onTurnComplete?.();
                  break;
                case "thought":
                  if (data.content) {
                    optionsRef.current.onThought?.(data.content);
                  }
                  break;
                case "error":
                  setErrorMessage(data.message || "Live API error");
                  optionsRef.current.onError?.(data.message || "Live API error");
                  break;
                default:
                  break;
              }
            } catch (e) {
              console.error("Failed to parse Live WS message:", e);
            }
          };

          ws.onerror = () => {
            setErrorMessage("WebSocket connection error");
            setStatus("error");
            if (!isResolved) {
              isResolved = true;
              reject(new Error("WebSocket connection error"));
            }
          };

          ws.onclose = (e) => {
            if (e.code === 4003) {
              setErrorMessage("Authentication failed or Live Chat is disabled.");
            }
            if (!isResolved) {
              isResolved = true;
              reject(new Error(`WebSocket closed with code ${e.code}`));
            }
            disconnectLive();
          };
        } catch (err: unknown) {
          const msg = err instanceof Error ? err.message : "Failed to connect to Live API";
          setErrorMessage(msg);
          setStatus("error");
          reject(err);
        }
      });
    },
    [disconnectLive]
  );

  const startMic = useCallback(async () => {
    if (USE_MOCK) {
      setIsMicActive(true);
      optionsRef.current.onUserTranscript?.("Simulated live speech (Mock Mode)");
      setTimeout(() => {
        optionsRef.current.onModelTranscript?.(
          "Mock Live Audio response: Running in offline mock mode. Connect to live backend to stream real Gemini Live audio."
        );
        optionsRef.current.onTurnComplete?.();
      }, 600);
      return;
    }

    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      await connectLive();
    }

    try {
      await recorderRef.current?.start((pcmChunkBase64) => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(
            JSON.stringify({
              type: "realtime_input",
              audio: pcmChunkBase64,
            })
          );
        }
      });
      setIsMicActive(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Could not access microphone";
      setErrorMessage(msg);
      setIsMicActive(false);
    }
  }, [connectLive]);

  const stopMic = useCallback(() => {
    recorderRef.current?.stop();
    setIsMicActive(false);
    if (!USE_MOCK && wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: "realtime_input",
          audio_stream_end: true,
        })
      );
    }
  }, []);

  const toggleMic = useCallback(async () => {
    if (isMicActive) {
      stopMic();
    } else {
      await startMic();
    }
  }, [isMicActive, startMic, stopMic]);

  const sendTextMessage = useCallback(
    (text: string) => {
      if (!text.trim()) return;
      if (USE_MOCK) {
        setTimeout(() => {
          optionsRef.current.onModelTranscript?.(
            `Mock Live response to "${text}": Backend is in mock mode.`
          );
          optionsRef.current.onTurnComplete?.();
        }, 300);
        return;
      }
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(
          JSON.stringify({
            type: "realtime_input",
            text: text.trim(),
          })
        );
      }
    },
    []
  );

  /**
   * Push an updated grounding-context string over the ALREADY-OPEN
   * connection (server-side handled identically to the "context" message
   * connectLive's onopen sends once at connect time — see
   * api/ws_api.py::ws_live_chat_endpoint's "context" branch). Callers should
   * use this instead of tearing down and reconnecting the live session
   * whenever their context text changes mid-conversation -- reconnecting
   * would lose the Gemini Live session's turn state and any buffered audio.
   * No-ops on an empty string or when there's no open connection (mock mode
   * has nothing to relay context to, so it's a no-op there too).
   */
  const sendContext = useCallback((text: string) => {
    if (!text || !text.trim()) return;
    if (USE_MOCK) return;
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "context", text }));
    }
  }, []);

  const interruptPlayback = useCallback(() => {
    playerRef.current?.interrupt();
  }, []);

  return {
    status,
    isMicActive,
    isSpeaking,
    liveModel,
    liveVoice,
    errorMessage,
    connectLive,
    disconnectLive,
    startMic,
    stopMic,
    toggleMic,
    sendTextMessage,
    sendContext,
    interruptPlayback,
  };
}
