# Gemini Live Audio Streaming & Multi-Model AI Chat System Walkthrough

## Overview

We designed and implemented a production-grade **Gemini Live Audio WebSocket Streaming** system and expanded the AI chat architecture into an extensible **Multi-Model & Open-Source AI Engine** for the Stockpy Pilots PWA and Data API.

---

## 1. Gemini Live Bidirectional Voice Streaming (`/ws/chat/live`)

- **Protocol**: Real-time WebSocket connection to `api/ws_api.py` (`/ws/chat/live`), mounted on `api/data_api.py`.
- **Audio Standards**:
  - **Input Capture**: Microphones captured via Web Audio API, downsampled and converted in-browser to **16 kHz 16-bit linear PCM mono** ([`audioStreamer.ts`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_gemini_live_audio/webapp/src/chat/audioStreamer.ts)).
  - **Output Playback**: Gemini Live audio emitted as **24 kHz 16-bit linear PCM mono**, streamed to the client and played with a low-latency Web Audio `AudioContext` queue.
  - **Instant Interruption**: User speech immediately stops active audio playback buffers and flushes playback queues.
- **Platform Grounding & Tool Execution**:
  - Automatically queries platform state via read-only tools: `list_all_pilots`, `get_pilot_holdings`, `get_pilot_recent_trades`, `get_current_portfolio`, and `get_platform_status`.
  - Blocking tool logic is safely executed asynchronously with `asyncio.to_thread`.
- **Safety & Gating**:
  - Protected by `settings.AI_GENERATION_API_ENABLED`, `settings.GEMINI_LIVE_CHAT_ENABLED`, and token authentication (`STATE_API_TOKEN`).

---

## 2. Multi-Model & Open-Source AI Chat (`POST /api/chat`, `GET /data/ai/models`)

- **Multi-Provider Backend Routing**:
  - **Google Gemini**: Uses the official `google-genai` SDK with auto tool execution and SSE streaming.
  - **Anthropic Claude**: Uses `anthropic.AsyncAnthropic` with system prompt grounding and role normalization.
  - **OpenAI ChatGPT**: Uses `openai.AsyncOpenAI` for `gpt-4o`, `gpt-4o-mini`, `o1`, and `o3-mini`.
  - **Local & Open Source LLMs**: Seamless support for Ollama, vLLM, LM Studio, DeepSeek-R1, Llama 3.3, Qwen 2.5, Mistral, and OpenRouter via standard OpenAI-compatible endpoints (`LOCAL_LLM_BASE_URL`).
- **Dynamic Model Catalog (`GET /data/ai/models`)**:
  - Returns real-time availability and model presets per provider based on configured environment variables and API keys.
- **PWA Model Selection Bar**:
  - Sleek model selector embedded directly in the chat drawer header.
  - Dropdown grouping (`Google Gemini`, `Anthropic Claude`, `OpenAI ChatGPT`, `Local / Open Source`) with quick preset selection.
  - Custom model input field enabling write-in model slugs for arbitrary local or hosted models.

---

## 3. Verification & Quality Gates

### Automated Test Results
- **Backend pytest**: **76 passed in 3.55s**
  - `pytest tests/test_data_api_chat.py tests/test_gemini_live_chat.py tests/test_gui_env_io.py`
- **Frontend vitest**: **162 test files passed, 1729 unit tests passed**
  - `npm run --prefix webapp test`
- **TypeScript Typecheck**: **0 errors**
  - `npm run --prefix webapp typecheck`

### Independent Audits
- **API Parity Auditor**: Confirmed 100% method signature and type parity between `liveApi` and `mockApi`. Added reverse proxy route in `scripts/Caddyfile` for `/ws/chat/*`.
- **Honesty Auditor**: Confirmed 0 data fabrication (CONSTRAINT #4), non-raising error handling with robust fallback (CONSTRAINT #6), and full secret classification in `SECRET_KEYS` (CONSTRAINT #3).
