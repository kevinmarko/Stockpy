# Gemini Live API & Real-Time AI Chat System

Build out the **Gemini Live API** system in the Stockpy platform, enabling real-time, low-latency, bidirectional voice and text streaming over WebSockets between the Pilots PWA frontend and Google Gemini (`gemini-3.1-flash-live-preview`), fully grounded in platform portfolio and strategy state.

## User Review Required

> [!IMPORTANT]
> - **SDK & Model**: Uses the official `google-genai` Python SDK (`client.aio.live.connect`) targeting `gemini-3.1-flash-live-preview`.
> - **Audio Streaming Standards**: 
>   - Frontend input: Microphone captured via Web Audio API, downsampled/converted to 16 kHz 16-bit linear PCM mono.
>   - Gemini output: 24 kHz 16-bit linear PCM mono streamed over WebSocket to browser and played via Web Audio `AudioContext`.
> - **Safety & Capability Gating**: Gated by `settings.AI_GENERATION_API_ENABLED` and `settings.GEMINI_LIVE_CHAT_ENABLED` with token verification via `STATE_API_TOKEN`.
> - **Grounding Tools**: The Live session is equipped with the same 5 read-only platform tools (`list_all_pilots`, `get_pilot_holdings`, `get_pilot_recent_trades`, `get_current_portfolio`, `get_platform_status`).

## Open Questions

- None blocking. Sensible defaults are chosen (`gemini-3.1-flash-live-preview`, voice preset `Aoede`, automatic VAD interruption handling).

---

## Proposed Changes

### Configuration & Settings

#### [MODIFY] [settings.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/build_gemini_chat_system/settings.py)
- Add `GEMINI_LIVE_CHAT_ENABLED: bool = Field(default=True, description="Enable Gemini Live bidirectional WebSocket voice/audio streaming")`
- Add `GEMINI_LIVE_CHAT_MODEL: str = Field(default="gemini-3.1-flash-live-preview", description="Gemini model for real-time live streaming")`
- Add `GEMINI_LIVE_VOICE_NAME: str = Field(default="Aoede", description="Voice preset for Gemini Live audio output (Aoede, Puck, Charon, Fenrir, Kore)")`
- Add `GEMINI_CHAT_MODEL: str = Field(default="gemini-2.5-flash", description="Default model for REST SSE text chat")`

---

### Backend WebSocket Live Streaming

#### [MODIFY] [api/ws_api.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/build_gemini_chat_system/api/ws_api.py)
- Create `live_chat_router = APIRouter()` and add `@live_chat_router.websocket("/ws/chat/live")`.
- Implement `ws_live_chat_endpoint(websocket: WebSocket, token: Optional[str] = Query(None))`
  - Authenticate via `_check_ws_token(token, auth_header)` and ensure `settings.AI_GENERATION_API_ENABLED` is `True`.
  - Connect to Gemini Live session using `client.aio.live.connect(model=settings.GEMINI_LIVE_CHAT_MODEL, config=...)` with modalities `[types.Modality.AUDIO]`, system instruction for Stockpy Quant Assistant, and `_CHAT_TOOLS` function declarations.
  - Run concurrent receive/send loops:
    - **Client → Gemini**: Handle client JSON packets containing `realtime_input` (base64 PCM audio chunk, text, or context) and forward via `session.send_realtime_input(...)`.
    - **Gemini → Client**: Stream `server_content` (audio chunks base64, `input_transcription`, `output_transcription`, `interrupted` signals) to the client WebSocket.
    - **Tool Calling**: Handle tool calls requested by Gemini, execute read-only handlers safely (never raising), and send tool responses back to Gemini Live session.
  - Safe error handling & connection teardown.

#### [MODIFY] [api/data_api.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/build_gemini_chat_system/api/data_api.py)
- Mount `live_chat_router` in `api/data_api.py` alongside `tick_router`.
- Update `chat_endpoint` to use `settings.GEMINI_CHAT_MODEL` instead of hardcoded string.

---

### Frontend PWA (Pilots Webapp)

#### [NEW] [webapp/src/chat/audioStreamer.ts](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/build_gemini_chat_system/webapp/src/chat/audioStreamer.ts)
- Web Audio capture & downsampling utility (resampling to 16 kHz 16-bit PCM).
- 24 kHz PCM audio playback queue with smooth scheduling on `AudioContext` and instantaneous interruption flushing (`stop()`).

#### [NEW] [webapp/src/chat/useGeminiLive.ts](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/build_gemini_chat_system/webapp/src/chat/useGeminiLive.ts)
- React hook managing WebSocket connection to `/ws/chat/live`.
- Handles mic toggle, streaming audio, receiving transcripts, audio playback, connection state (`disconnected`, `connecting`, `connected`, `speaking`, `listening`), and error recovery.

#### [MODIFY] [webapp/src/components/AIChatInterface.tsx](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/build_gemini_chat_system/webapp/src/components/AIChatInterface.tsx)
- Add Voice / Live Mode controls (microphone toggle, live visualizer pulse / audio waves).
- Support live audio transcript display in real-time alongside text chat.
- Seamless fallback between Live Audio Mode and standard REST SSE Chat.

#### [MODIFY] [webapp/src/api/client.ts](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/build_gemini_chat_system/webapp/src/api/client.ts)
- Add `liveChatWsUrl(token?: string): string` helper constructing `ws://` / `wss://` URL to `/ws/chat/live`.

---

### Documentation Updates

#### [MODIFY] [CLAUDE.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/build_gemini_chat_system/CLAUDE.md)
- Document the Gemini Live API WebSocket endpoint (`/ws/chat/live`), voice capabilities, audio protocols, and settings flags.

#### [MODIFY] [docs/architecture/webapp-and-gui.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/build_gemini_chat_system/docs/architecture/webapp-and-gui.md)
- Document the Live AI Chat drawer architecture and Web Audio streaming pipeline.

---

## Verification Plan

### Automated Tests
1. **Python Unit Tests**:
   - Create `tests/test_gemini_live_chat.py` covering:
     - WebSocket auth & gating (`4003` close when unauthorized or `AI_GENERATION_API_ENABLED=False`).
     - Gemini Live session connection, audio & text forwarding.
     - Live tool execution and response routing.
     - Interruption and error handling.
   - Run `pytest tests/test_gemini_live_chat.py tests/test_data_api_chat.py`.
2. **Frontend Typecheck & Tests**:
   - `npm run --prefix webapp typecheck`
   - `npm run --prefix webapp test`

### Manual Verification
- Launch webapp dev server and Data API.
- Verify text chat continues functioning seamlessly.
- Connect to `/ws/chat/live` and test audio / microphone streaming and voice response.
