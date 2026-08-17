# Multi-Model & Open Source AI Chat Architecture Plan

Enable the Stockpy AI Chat system to use **any model requested by the operator**:
1. **Google Gemini**: `gemini-2.5-flash`, `gemini-2.5-pro`, `gemini-1.5-pro`, `gemini-3.1-flash-live-preview`
2. **Anthropic Claude**: `claude-3-5-sonnet`, `claude-3-5-haiku`, `claude-3-opus`
3. **OpenAI ChatGPT**: `gpt-4o`, `gpt-4o-mini`, `o1`, `o3-mini`
4. **Local & Open Source LLMs**: Ollama, vLLM, LM Studio, DeepSeek, Llama-3, Qwen, Mistral, OpenRouter (via standard OpenAI-compatible API endpoint).

---

## User Review Required

> [!IMPORTANT]
> - **Provider Selection**: The user will be able to switch models directly from the chat UI (dropdown menu in the drawer header) or specify a default in settings.
> - **Local/Open Source Support**: Local open-source models connect via standard OpenAI-compatible HTTP endpoints (e.g. `http://localhost:11434/v1` for Ollama, `http://localhost:8000/v1` for vLLM, or OpenRouter).
> - **Grounding & Tools**: 
>   - Gemini utilizes native function calling with the 5 read-only platform tools (`list_all_pilots`, `get_pilot_holdings`, `get_pilot_recent_trades`, `get_current_portfolio`, `get_platform_status`).
>   - Claude, OpenAI, and Local LLMs receive pre-formatted system prompt context and platform state grounding.
> - **Voice vs Text Mode**:
>   - **Text Chat**: Supports 100% of providers (Gemini, Claude, OpenAI, Ollama, DeepSeek, Llama, etc.).
>   - **Live Voice WebSocket**: Powered by Gemini Live API (`gemini-3.1-flash-live-preview`) for real-time 16kHz/24kHz bidirectional PCM streaming.

---

## Open Questions

1. **Default Model Preferences**:
   - Would you like the default model to be **Auto** (auto-detect based on available API keys) or **Gemini 2.5 Flash**?
2. **Local LLM Default Endpoint**:
   - Default `LOCAL_LLM_BASE_URL` will be set to `http://localhost:11434/v1` (standard Ollama endpoint). Is that preferred, or do you have a specific local runner (e.g., vLLM / LM Studio / OpenRouter)?

---

## Proposed Changes

Grouped by component:

### Settings & Configuration Layer

#### [MODIFY] [settings.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_gemini_live_audio/settings.py)
- Add settings for multi-model configuration:
  - `LOCAL_LLM_BASE_URL: Optional[str] = Field(default="http://localhost:11434/v1", description="Base URL for OpenAI-compatible local open-source LLM server (Ollama, vLLM, LM Studio).")`
  - `LOCAL_LLM_MODEL: str = Field(default="llama3.3", description="Default model name for local LLM requests.")`
  - `LOCAL_LLM_API_KEY: Optional[str] = Field(default=None, description="Optional API key for local or self-hosted LLM server (e.g. OpenRouter / vLLM token).")`
  - `AI_CHAT_DEFAULT_PROVIDER: str = Field(default="auto", description="Default AI chat provider: 'auto', 'gemini', 'anthropic', 'openai', 'local'.")`
  - `AI_CHAT_DEFAULT_MODEL: Optional[str] = Field(default=None, description="Optional explicit override for default chat model across all providers.")`
- Register `LOCAL_LLM_BASE_URL`, `LOCAL_LLM_MODEL`, `AI_CHAT_DEFAULT_PROVIDER`, and `AI_CHAT_DEFAULT_MODEL` in `ALLOWED_KEYS` in [`gui/env_io.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_gemini_live_audio/gui/env_io.py).

---

### Backend Data API & Provider Router

#### [MODIFY] [api/data_api.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_gemini_live_audio/api/data_api.py)
- Update `ChatMessageRequest`:
  ```python
  class ChatMessageRequest(BaseModel):
      message: str
      history: Optional[List[Dict[str, str]]] = None
      context: Optional[str] = None
      provider: Optional[str] = None  # "gemini" | "anthropic" | "openai" | "local" | "auto"
      model: Optional[str] = None     # Specific model slug (e.g., "claude-3-5-sonnet-20241022", "gpt-4o", "deepseek-r1", "llama3.3")
      custom_base_url: Optional[str] = None
  ```
- Implement dedicated streaming handlers in `chat_endpoint`:
  1. **`_stream_gemini(model, contents, tools)`**: Google GenAI SDK stream with tool calling.
  2. **`_stream_anthropic(model, messages, system_prompt)`**: Anthropic Messages API stream.
  3. **`_stream_openai(model, messages, system_prompt, base_url, api_key)`**: OpenAI / Local OpenAI-compatible API streaming (supports both OpenAI official endpoints and local Ollama/vLLM endpoints).
- Add `GET /data/ai/models` endpoint returning available providers, models, and availability status (configured vs missing API key).

---

### Frontend PWA (Pilots Webapp)

#### [MODIFY] [webapp/src/api/client.ts](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_gemini_live_audio/webapp/src/api/client.ts)
- Add `getAiModels(): Promise<AiModelsResponse>` and types in [`webapp/src/api/types.ts`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_gemini_live_audio/webapp/src/api/types.ts).

#### [MODIFY] [webapp/src/components/AIChatInterface.tsx](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_gemini_live_audio/webapp/src/components/AIChatInterface.tsx)
- Add a **Model & Provider Selector** in the header:
  - Provider grouping: Google Gemini, Anthropic Claude, OpenAI, Local / Open Source.
  - Quick model presets + custom model input.
  - Active provider badge & model indicator.
- Pass `provider` and `model` to `POST /api/chat`.
- Live Voice mode keeps the fast toggle to Gemini Live WebSocket.

---

### Testing & Documentation

#### [MODIFY] [tests/test_data_api_chat.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_gemini_live_audio/tests/test_data_api_chat.py)
- Add tests for:
  - Claude / Anthropic streaming with custom model parameters.
  - OpenAI streaming (`gpt-4o`, `gpt-4o-mini`).
  - Local / Open-source OpenAI-compatible streaming (`http://localhost:11434/v1`, `llama3.3`, `deepseek-r1`).
  - Provider auto-detection when no provider is explicitly passed.
  - Available models endpoint `GET /data/ai/models`.

#### [MODIFY] [CLAUDE.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_gemini_live_audio/CLAUDE.md) & [AGENTS.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_gemini_live_audio/AGENTS.md)
- Document the multi-model architecture, supported providers, local open-source LLM configuration, and API contracts.

---

## Verification Plan

### Automated Tests
1. **Python Unit Tests**:
   - `pytest tests/test_data_api_chat.py tests/test_gemini_live_chat.py tests/test_gui_env_io.py`
2. **Frontend Typecheck & Tests**:
   - `npm run --prefix webapp typecheck`
   - `npm run --prefix webapp test -- --run src/components/AIChatInterface.test.tsx`

### Independent Audits
- Run `custom-parity-auditor` and `custom-honesty-auditor` to audit the multi-provider implementation.

---

## Post-review security fix (2026-08-17)

A code review of this PR (before merge) found that the `custom_base_url` field
on `ChatMessageRequest` — accepted from the raw request body with no
validation and passed straight into `openai.AsyncOpenAI(base_url=..., ...)`
in the `"local"` provider branch — made `POST /api/chat` an open SSRF /
credential-relay: any caller able to reach the endpoint could redirect the
server's outbound request to an attacker-controlled host and receive the
forwarded chat content (including portfolio/grounding context) plus the
operator's `LOCAL_LLM_API_KEY` as a bearer token.

**Fixed by removing `custom_base_url` entirely** rather than
allowlisting/validating it — the webapp never actually sent this field (no
UI exposed it), so dropping it cost no real functionality. The `"local"`
provider's outbound base URL is now unconditionally
`settings.LOCAL_LLM_BASE_URL` (operator-set, server-side only, falling back
to `http://localhost:11434/v1`). See `tests/test_data_api_chat.py`'s
`test_local_routing_ignores_client_supplied_base_url` for the regression
test, and `docs/architecture/webapp-and-gui.md`'s "Multi-Model & Open Source
AI Chat" entry for the documented contract.
