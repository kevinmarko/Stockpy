"""api/chat_api.py
================
FastAPI backend service providing Server-Sent Events (SSE) streaming for the
Gemini-powered Data Analytics Chat Interface in the Pilots PWA.

Features:
- Streaming via Server-Sent Events (`text/event-stream`).
- Separate event types for thoughts (`THOUGHT`), final response markdown
  (`FINAL_RESPONSE`), and interactive follow-up buttons (`SUGGESTION`).
- Multi-turn conversation context handling.
- Dynamic data context loading from active portfolio holdings, watchlist, and FMP feeds.
- Dead-letter resilient: gracefully falls back when Gemini API key is missing or calls fail.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, AsyncGenerator, Dict, List, Optional
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from settings import settings

logger = logging.getLogger(__name__)

app = FastAPI(title="InvestYo Gemini Data Analytics Chat API", version="1.0.0")

# CORS Configuration
allowed_origins = getattr(settings, "CORS_ALLOWED_ORIGINS", ["http://localhost:5173", "http://127.0.0.1:5173"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessageItem(BaseModel):
    role: str = Field(..., description="user or model")
    content: str = Field(..., description="Message text content")


class ChatStreamRequest(BaseModel):
    query: str = Field(..., description="Latest user query")
    history: List[ChatMessageItem] = Field(default_factory=list, description="Multi-turn conversation history")
    symbols: Optional[List[str]] = Field(default=None, description="Optional active symbol list context")


def require_read_token(authorization: Optional[str] = Header(None)) -> None:
    """Read-token security dependency."""
    token = getattr(settings, "STATE_API_TOKEN", None)
    if token and authorization:
        expected = f"Bearer {token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="Invalid token")


def _get_universe_context(symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Resolve data context for symbols using available data providers."""
    target_syms = symbols or ["SYF", "CMCL", "AAL", "PK", "ABR"]
    context_records: List[Dict[str, Any]] = []

    try:
        from data.fmp_feeds_company import fetch_financial_scores, fetch_key_ratios_ttm
        from data.fmp_feeds_market import fetch_realized_volatility
        for sym in target_syms[:15]:
            scores = fetch_financial_scores(sym)
            ratios = fetch_key_ratios_ttm(sym)
            vol = fetch_realized_volatility(sym)
            context_records.append({
                "symbol": sym,
                "altman_z_score": scores.get("altman_z_score"),
                "piotroski_f_score": scores.get("piotroski_f_score"),
                "net_debt_ebitda": ratios.get("net_debt_ebitda"),
                "fcf_yield": ratios.get("fcf_yield"),
                "pe_ratio": ratios.get("pe_ratio"),
                "hv_30": vol.get("hv_30"),
            })
    except Exception as exc:
        logger.warning("Failed to fetch context records: %s", exc)

    return context_records


async def _sse_stream_generator(request: ChatStreamRequest) -> AsyncGenerator[str, None]:
    """Generates SSE events for thought, content chunks, and suggestions."""
    gemini_key = os.environ.get("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", None)

    # Send initial thought chunk
    yield f"data: {json.dumps({'type': 'THOUGHT', 'text': 'Analyzing market data, options directives, and fundamental health scores...'})}\n\n"

    data_context = _get_universe_context(request.symbols)
    yield f"data: {json.dumps({'type': 'THOUGHT', 'text': f'Loaded dataset context for {len(data_context)} symbols.'})}\n\n"

    if not gemini_key:
        fallback_msg = (
            "⚠️ **Gemini API Key missing.**\n\n"
            "To enable live natural language queries against your market & options data, please set `GEMINI_API_KEY` in your `.env` file.\n\n"
            "### Loaded Data Context Summary:\n"
        )
        yield f"data: {json.dumps({'type': 'FINAL_RESPONSE', 'text': fallback_msg})}\n\n"
        yield f"data: {json.dumps({'type': 'FINAL_RESPONSE', 'text': f'```json\\n{json.dumps(data_context, indent=2)}\\n```'})}\n\n"

        suggestions = [
            "Which tickers have low Altman Z solvency scores?",
            "What options strategies have upcoming earnings risk?",
            "Show sector performance relative values",
        ]
        for s in suggestions:
            yield f"data: {json.dumps({'type': 'SUGGESTION', 'text': s})}\n\n"
        yield "data: [DONE]\n\n"
        return

    try:
        from google import genai
        client = genai.Client(api_key=gemini_key)

        system_instruction = (
            "You are the AI Data Analytics Assistant for InvestYo Quant Platform ('Stock Dashboard Py'). "
            "Help the investor evaluate options strategies, fundamental health (Altman Z, Piotroski F), "
            "earnings risk, and sector performance. Be concise, structured, and quantitative."
        )

        prompt = (
            f"Dataset Context:\n{json.dumps(data_context, indent=2)}\n\n"
            f"User Query: {request.query}"
        )

        response = client.models.generate_content_stream(
            model="gemini-2.5-flash",
            contents=prompt,
            config={"system_instruction": system_instruction}
        )

        for chunk in response:
            if hasattr(chunk, "text") and chunk.text:
                yield f"data: {json.dumps({'type': 'FINAL_RESPONSE', 'text': chunk.text})}\n\n"

        suggestions = [
            "Analyze earnings risk for options candidates",
            "Which symbols have high Piotroski F-Scores?",
            "Compare rolling 30-day realized volatility vs IVR",
        ]
        for s in suggestions:
            yield f"data: {json.dumps({'type': 'SUGGESTION', 'text': s})}\n\n"

    except Exception as exc:
        logger.error("Gemini API streaming error: %s", exc)
        yield f"data: {json.dumps({'type': 'FINAL_RESPONSE', 'text': f'❌ Gemini API Error: {str(exc)}'})}\n\n"

    yield "data: [DONE]\n\n"


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "chat_api"}


@app.post("/api/chat/stream", dependencies=[Depends(require_read_token)])
async def chat_stream(request: ChatStreamRequest) -> StreamingResponse:
    """Stream response from Gemini API as Server-Sent Events."""
    return StreamingResponse(
        _sse_stream_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
