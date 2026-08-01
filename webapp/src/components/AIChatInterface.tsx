import React, { useState, useRef, useEffect, Component } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Send, Bot, User, Loader2, BrainCircuit, ChevronDown, ChevronRight, X } from 'lucide-react';
import { getEffectiveToken } from '../auth/apiToken';
import { chatUrl } from '../api/client';


// Required component to catch ReactMarkdown v10+ crash parsing errors
class ErrorBoundary extends Component<{children: React.ReactNode}, {hasError: boolean, error: Error | null}> {
  constructor(props: {children: React.ReactNode}) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }
  render() {
    if (this.state.hasError) {
      return <div style={{ color: "var(--decline)", fontSize: "var(--t-caption)", padding: "var(--s-2)" }}>MD Err: {this.state.error?.message}</div>;
    }
    return this.props.children;
  }
}

interface AIChatInterfaceProps {
  isOpen: boolean;
  onClose: () => void;
  contextText?: string;
}

export default function AIChatInterface({ isOpen, onClose, contextText }: AIChatInterfaceProps) {
  const [messages, setMessages] = useState<any[]>([
    { role: 'model', content: "Hi! Ask me any questions about the data." }
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [expandedThoughts, setExpandedThoughts] = useState<Record<number, boolean>>({});
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    // Optional chaining on the call itself, not just the ref: jsdom (used by
    // the test suite) doesn't implement scrollIntoView at all, and this
    // component is always mounted (App.tsx renders it unconditionally, just
    // CSS-hidden), so an unguarded call here broke every test that mounts
    // <App />.
    bottomRef.current?.scrollIntoView?.({ behavior: 'smooth' });
  }, [messages, isLoading]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      const scrollHeight = textareaRef.current.scrollHeight;
      textareaRef.current.style.height = Math.min(scrollHeight, 112) + 'px';
      textareaRef.current.style.overflowY = scrollHeight > 112 ? 'auto' : 'hidden';
    }
  }, [input]);

  const toggleThought = (idx: number) => {
    setExpandedThoughts(prev => ({ ...prev, [idx]: !prev[idx] }));
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend(e);
    }
  };

  const handleSend = async (e?: React.FormEvent | React.KeyboardEvent, overrideText: string | null = null) => {
    e?.preventDefault();
    const textToSend = overrideText || input;
    if (!textToSend.trim() || isLoading) return;

    if (!overrideText) setInput('');
    setIsLoading(true);

    const currentMessages = [...messages, { role: 'user', content: textToSend.trim() }];

    const nextIdx = currentMessages.length;
    let thinking = '';
    let reply = '';
    let suggestions: string[] = [];

    setMessages([...currentMessages, { role: 'model', content: '', thoughts: '', suggestions: [] }]);

    try {
      const token = getEffectiveToken();
      const response = await fetch(chatUrl(), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          message: contextText ? `[Context: ${contextText}] ${textToSend.trim()}` : textToSend.trim(),
          history: currentMessages.slice(0, -1)
        })
      });


      if (!response.body) throw new Error('No response body');

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        setIsLoading(false);
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const dataStr = line.replace('data: ', '').trim();
            if (dataStr === '[DONE]') break;
            if (dataStr) {
              try {
                const parsed = JSON.parse(dataStr);
                if (parsed.type === 'THOUGHT') {
                  thinking += parsed.content + '\n';
                } else if (parsed.type === 'SUGGESTION') {
                  suggestions.push(parsed.content);
                } else {
                  reply += parsed.content;
                }

                setMessages(prev => {
                  const updated = [...prev];
                  updated[nextIdx] = {
                    role: 'model',
                    content: reply,
                    thoughts: thinking,
                    suggestions: suggestions
                  };
                  return updated;
                });
              } catch (e) {
                console.error('JSON parse fail:', dataStr);
              }
            }
          }
        }
      }
    } catch (error) {
      console.error(error);
      setMessages([...currentMessages, { role: 'model', content: `⚠️ Error connecting to server.` }]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div
      className={`ai-chat-panel${isOpen ? ' is-open' : ''}`}
      // The panel is only translated off-screen (for the slide transition),
      // not unmounted, so its buttons/textarea/suggestions stay in the DOM
      // while closed. `inert` (supported in all current major browsers)
      // removes the whole subtree from the tab order and assistive-tech
      // exposure without breaking the CSS transition the way conditionally
      // unmounting would. aria-hidden is redundant with inert in modern
      // browsers but kept as a defensive fallback for older AT/browser pairs.
      inert={!isOpen}
      aria-hidden={!isOpen}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "var(--s-4)", borderBottom: "1px solid var(--border)" }}>
        <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>AI Chat Interface</div>
        <button onClick={onClose} className="ai-chat-icon-btn"><X style={{ width: 20, height: 20 }} /></button>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "var(--s-4)", display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
        {messages.map((m, i) => (
          <div key={i} style={{ display: "flex", gap: "var(--s-3)", flexDirection: m.role === 'user' ? 'row-reverse' : 'row' }}>
            <div style={{ flexShrink: 0, width: 32, height: 32, borderRadius: "50%", background: "var(--surface-3)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              {m.role === 'user' ? <User style={{ width: 16, height: 16, color: "var(--text-secondary)" }} /> : <Bot style={{ width: 16, height: 16, color: "var(--text-secondary)" }} />}
            </div>
            <div style={{ maxWidth: "85%", display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>

              {m.role === 'model' && m.thoughts && m.thoughts.trim().length > 0 && (
                <div style={{ background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", overflow: "hidden" }}>
                  <button
                    onClick={() => toggleThought(i)}
                    className="ai-chat-thought-toggle"
                  >
                    {expandedThoughts[i] ? <ChevronDown style={{ width: 14, height: 14, flexShrink: 0 }} /> : <ChevronRight style={{ width: 14, height: 14, flexShrink: 0 }} />}
                    <BrainCircuit style={{ width: 14, height: 14, color: "var(--accent)", flexShrink: 0 }} />
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", textAlign: "left" }}>
                      {(() => {
                        if (m.content && m.content.length > 0) return "View reasoning process";
                        const lines = m.thoughts.split('\n').filter((l: string) => l.trim().length > 0);
                        return lines.length > 0 ? lines[lines.length - 1] : "Analyzing context...";
                      })()}
                    </span>
                  </button>
                  {expandedThoughts[i] && (
                    <div
                      style={{
                        padding: "var(--s-3) var(--s-3) var(--s-3)",
                        borderTop: "1px solid var(--border)",
                        maxHeight: 250,
                        overflowY: "auto",
                        background: "rgba(0, 0, 0, 0.2)",
                        fontSize: "var(--t-micro)",
                        fontFamily: "var(--font-mono, monospace)",
                        lineHeight: 1.6,
                        color: "var(--text-secondary)",
                        display: "flex",
                        flexDirection: "column",
                        gap: "var(--s-2)",
                      }}
                    >
                      {m.thoughts.split('\n').filter((l: string) => l.trim().length > 0).map((line: string, idx: number) => (
                        <div key={idx} style={{ display: "flex", gap: "var(--s-2)", alignItems: "flex-start" }}>
                          <div style={{ color: "var(--text-muted)", marginTop: 2 }}>›</div>
                          <div style={{ wordBreak: "break-word", whiteSpace: "pre-wrap" }}>{line}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {m.role === 'model' && !m.content && !expandedThoughts[i] && (
                <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", fontSize: "var(--t-caption)", color: "var(--text-secondary)", fontStyle: "italic", marginLeft: "var(--s-1)", marginBottom: "var(--s-1)", padding: "var(--s-1)" }}>
                  <Loader2 style={{ width: 12, height: 12, animation: "spin 0.7s linear infinite" }} /> {m.thoughts ? 'Thinking...' : 'Gathering insights...'}
                </div>
              )}

              {m.content && (
                <div
                  style={{
                    padding: "var(--s-3)",
                    borderRadius: "var(--r-lg)",
                    fontSize: "var(--t-body)",
                    lineHeight: 1.6,
                    ...(m.role === 'user'
                      ? { background: "var(--accent)", color: "#06222f", borderTopRightRadius: "var(--r-2xs)" }
                      : { background: "var(--surface-2)", color: "var(--text-primary)", borderTopLeftRadius: "var(--r-2xs)", border: "1px solid var(--border)" }),
                  }}
                >
                  {m.role === 'model' ? (
                    <ErrorBoundary>
                      <div className="md-content">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {m.content}
                        </ReactMarkdown>
                      </div>
                    </ErrorBoundary>
                  ) : (
                    <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
                  )}
                </div>
              )}

              {m.role === 'model' && m.suggestions && m.suggestions.length > 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-1-5)", marginTop: "var(--s-2)" }}>
                  {m.suggestions.slice(0, 3).map((s: string, idx: number) => (
                    <button
                      key={idx} onClick={() => handleSend(undefined, s)} disabled={isLoading}
                      className="ai-chat-suggestion"
                    >{s}</button>
                  ))}
                </div>
              )}

            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={handleSend} style={{ padding: "var(--s-4)", borderTop: "1px solid var(--border)", display: "flex", alignItems: "flex-end", background: "var(--surface-2)", position: "relative" }}>
        <textarea
          ref={textareaRef}
          value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={handleKeyDown} disabled={isLoading}
          placeholder="Ask a question about your portfolio..."
          rows={1}
          className="ai-chat-textarea"
        />
        <button type="submit" disabled={!input.trim() || isLoading} className="ai-chat-send-btn">
          <Send style={{ width: 16, height: 16 }} />
        </button>
      </form>
    </div>
  );
}
