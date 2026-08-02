import React, { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage, ChatStreamEvent } from "../api/types";


interface GeminiChatPanelProps {
  isOpen: boolean;
  onClose: () => void;
  symbols?: string[];
}

export const GeminiChatPanel: React.FC<GeminiChatPanelProps> = ({
  isOpen,
  onClose,
  symbols,
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [thoughtsExpanded, setThoughtsExpanded] = useState<Record<string, boolean>>({});
  const chatBottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Dynamic textarea height adjustment
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`;
    }
  }, [input]);

  if (!isOpen) return null;

  const handleSend = async (customQuery?: string) => {
    const query = (customQuery || input).trim();
    if (!query || isStreaming) return;

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: query,
    };

    const modelMessageId = `model-${Date.now()}`;
    const modelMessage: ChatMessage = {
      id: modelMessageId,
      role: "model",
      content: "",
      thoughts: [],
      suggestions: [],
      isStreaming: true,
    };

    setMessages((prev) => [...prev, userMessage, modelMessage]);
    if (!customQuery) setInput("");
    setIsStreaming(true);

    try {
      const response = await fetch("http://localhost:8602/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          history: messages.map((m) => ({ role: m.role, content: m.content })),
          symbols,
        }),
      });

      if (!response.ok || !response.body) {
        throw new Error(`HTTP error ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed.startsWith("data: ")) {
            const dataStr = trimmed.slice(6);
            if (dataStr === "[DONE]") break;

            try {
              const event: ChatStreamEvent = JSON.parse(dataStr);
              setMessages((prev) =>
                prev.map((msg) => {
                  if (msg.id !== modelMessageId) return msg;
                  if (event.type === "THOUGHT") {
                    return {
                      ...msg,
                      thoughts: [...(msg.thoughts || []), event.text],
                    };
                  } else if (event.type === "FINAL_RESPONSE") {
                    return {
                      ...msg,
                      content: msg.content + event.text,
                    };
                  } else if (event.type === "SUGGESTION") {
                    return {
                      ...msg,
                      suggestions: [...(msg.suggestions || []), event.text],
                    };
                  }
                  return msg;
                })
              );
            } catch (err) {
              console.warn("Failed to parse SSE payload:", dataStr, err);
            }
          }
        }
      }
    } catch (error) {
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === modelMessageId
            ? {
                ...msg,
                content: `⚠️ Failed to connect to Gemini Chat backend: ${error instanceof Error ? error.message : "Unknown error"}. Verify backend is running on port 8602 with GEMINI_API_KEY.`,
              }
            : msg
        )
      );
    } finally {
      setIsStreaming(false);
      setMessages((prev) =>
        prev.map((msg) => (msg.id === modelMessageId ? { ...msg, isStreaming: false } : msg))
      );
    }
  };

  const toggleThoughts = (id: string) => {
    setThoughtsExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  return (
    <div className="fixed inset-y-0 right-0 z-50 w-full max-w-md bg-zinc-900 border-l border-zinc-800 shadow-2xl flex flex-col font-sans text-zinc-100">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800 bg-zinc-950">
        <div className="flex items-center gap-2">
          <span className="text-emerald-400 font-bold text-lg">🤖 Gemini Analytics</span>
          <span className="text-xs bg-emerald-950 text-emerald-300 px-2 py-0.5 rounded border border-emerald-800">
            Data Chat
          </span>
        </div>
        <button
          onClick={onClose}
          className="text-zinc-400 hover:text-zinc-100 text-xl font-bold px-2"
          aria-label="Close Gemini Chat"
        >
          ✕
        </button>
      </div>

      {/* Message List */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="text-center text-zinc-400 my-8 space-y-3">
            <p className="text-sm font-medium">Ask questions about market, options, & fundamentals:</p>
            <div className="flex flex-col gap-2">
              {[
                "Which tickers have upcoming earnings risk?",
                "Which candidate plays have Altman Z > 2.5?",
                "Compare 30-day realized volatility vs IVR",
              ].map((suggestion, idx) => (
                <button
                  key={idx}
                  onClick={() => handleSend(suggestion)}
                  className="text-xs text-emerald-400 bg-zinc-800 hover:bg-zinc-700 p-2.5 rounded-lg border border-zinc-700 text-left transition-colors"
                >
                  💬 {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}
          >
            <div className="text-[10px] text-zinc-400 mb-1 px-1">
              {msg.role === "user" ? "You" : "Gemini Analytics"}
            </div>

            {/* Model Thoughts Accordion */}
            {msg.role === "model" && msg.thoughts && msg.thoughts.length > 0 && (
              <div className="w-full mb-2 bg-zinc-950/60 border border-zinc-800 rounded-md overflow-hidden text-xs">
                <button
                  onClick={() => toggleThoughts(msg.id)}
                  className="w-full flex items-center justify-between px-3 py-1.5 text-zinc-400 hover:text-zinc-200 bg-zinc-900/50"
                >
                  <span className="flex items-center gap-1.5">
                    🧠 {msg.isStreaming ? "Thinking..." : "System Reasoning"} ({msg.thoughts.length} steps)
                  </span>
                  <span>{thoughtsExpanded[msg.id] ? "▲" : "▼"}</span>
                </button>
                {thoughtsExpanded[msg.id] && (
                  <div className="p-2.5 space-y-1 font-mono text-[11px] text-zinc-400 border-t border-zinc-800 bg-zinc-950/80">
                    {msg.thoughts.map((t, idx) => (
                      <div key={idx} className="flex gap-2">
                        <span className="text-zinc-400">›</span>
                        <span>{t}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Message Body */}
            <div
              className={`p-3 rounded-lg max-w-[92%] text-sm ${
                msg.role === "user"
                  ? "bg-emerald-700 text-white rounded-br-none"
                  : "bg-zinc-800 text-zinc-100 border border-zinc-700 rounded-bl-none"
              }`}
            >
              {msg.role === "model" ? (
                <div className="prose prose-invert max-w-none text-xs leading-relaxed space-y-2 [&>p]:mb-2 [&>ul]:list-disc [&>ul]:pl-4 [&>ol]:list-decimal [&>ol]:pl-4 [&>h3]:text-emerald-400 [&>h3]:font-bold [&>h3]:mt-2">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {msg.content || (msg.isStreaming ? "⏳ Generating response..." : "")}
                  </ReactMarkdown>
                </div>
              ) : (
                <span>{msg.content}</span>
              )}
            </div>

            {/* Follow-up Interactive Suggestions */}
            {msg.role === "model" && msg.suggestions && msg.suggestions.length > 0 && !msg.isStreaming && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {msg.suggestions.map((s, sIdx) => (
                  <button
                    key={sIdx}
                    onClick={() => handleSend(s)}
                    className="text-[11px] bg-zinc-800 hover:bg-zinc-700 text-emerald-400 px-2.5 py-1 rounded-full border border-zinc-700 transition-colors"
                  >
                    💡 {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
        <div ref={chatBottomRef} />
      </div>

      {/* Input Area */}
      <div className="p-3 border-t border-zinc-800 bg-zinc-950">
        <div className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder="Ask a question about your market data..."
            rows={1}
            disabled={isStreaming}
            className="flex-1 bg-zinc-900 border border-zinc-700 rounded-lg p-2.5 text-xs text-zinc-100 focus:outline-none focus:border-emerald-500 resize-none max-h-32 font-sans"
          />
          <button
            onClick={() => handleSend()}
            disabled={!input.trim() || isStreaming}
            className="bg-emerald-600 hover:bg-emerald-500 text-white font-medium px-4 py-2.5 rounded-lg text-xs transition-colors"
          >
            {isStreaming ? "..." : "Send"}
          </button>

        </div>
      </div>
    </div>
  );
};

export default GeminiChatPanel;
