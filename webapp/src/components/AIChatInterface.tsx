import React, { useState, useRef, useEffect, Component, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Send,
  Bot,
  User,
  Loader2,
  BrainCircuit,
  ChevronDown,
  ChevronRight,
  X,
  Mic,
  MicOff,
  Radio,
  Volume2,
  AlertCircle
} from 'lucide-react';
import { getEffectiveToken } from '../auth/apiToken';
import { chatUrl } from '../api/client';
import { theme } from '../theme';
import { useGeminiLive } from '../chat/useGeminiLive';

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
      return <div style={{ color: theme.decline, fontSize: 'var(--t-caption)', padding: 'var(--s-2)' }}>MD Err: {this.state.error?.message}</div>;
    }
    return this.props.children;
  }
}

interface MessageItem {
  role: 'user' | 'model';
  content: string;
  thoughts?: string;
  suggestions?: string[];
  isLive?: boolean;
}

interface AIChatInterfaceProps {
  isOpen: boolean;
  onClose: () => void;
  contextText?: string;
}

export default function AIChatInterface({ isOpen, onClose, contextText }: AIChatInterfaceProps) {
  const [messages, setMessages] = useState<MessageItem[]>([
    { role: 'model', content: "Hi! Ask me any questions about your portfolio, strategy pilots, or market data." }
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isLiveMode, setIsLiveMode] = useState(false);
  const [expandedThoughts, setExpandedThoughts] = useState<Record<number, boolean>>({});
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Gemini Live hook
  const {
    status: liveStatus,
    isMicActive,
    isSpeaking,
    liveModel,
    errorMessage: liveError,
    connectLive,
    disconnectLive,
    toggleMic,
    sendTextMessage: sendLiveTextMessage,
  } = useGeminiLive({
    onUserTranscript: (text) => {
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.role === 'user' && last.isLive) {
          const updated = [...prev];
          updated[prev.length - 1] = { ...last, content: text };
          return updated;
        }
        return [...prev, { role: 'user', content: text, isLive: true }];
      });
    },
    onModelTranscript: (text, isPartial) => {
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.role === 'model' && last.isLive) {
          const updated = [...prev];
          updated[prev.length - 1] = {
            ...last,
            content: isPartial ? (last.content + text) : text,
          };
          return updated;
        }
        return [...prev, { role: 'model', content: text, isLive: true }];
      });
    },
    onTurnComplete: () => {
      // Completed current live dialogue turn
    },
    onThought: (thought) => {
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.role === 'model') {
          const updated = [...prev];
          updated[prev.length - 1] = {
            ...last,
            thoughts: (last.thoughts || '') + thought + '\n',
          };
          return updated;
        }
        return prev;
      });
    },
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: 'smooth' });
  }, [messages, isLoading, isSpeaking]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      const scrollHeight = textareaRef.current.scrollHeight;
      textareaRef.current.style.height = Math.min(scrollHeight, 112) + 'px';
      textareaRef.current.style.overflowY = scrollHeight > 112 ? 'auto' : 'hidden';
    }
  }, [input]);

  // Clean up live connection and mic when chat panel is closed
  useEffect(() => {
    if (!isOpen && isLiveMode) {
      disconnectLive();
      setIsLiveMode(false);
    }
  }, [isOpen, isLiveMode, disconnectLive]);

  const toggleThought = (idx: number) => {
    setExpandedThoughts(prev => ({ ...prev, [idx]: !prev[idx] }));
  };

  const handleToggleLiveMode = useCallback(async () => {
    if (isLiveMode) {
      disconnectLive();
      setIsLiveMode(false);
    } else {
      setIsLiveMode(true);
      await connectLive(contextText);
    }
  }, [isLiveMode, connectLive, disconnectLive, contextText]);

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

    // If live mode is connected, route text via WebSocket
    if (isLiveMode && liveStatus === 'connected') {
      const userMsg: MessageItem = { role: 'user', content: textToSend.trim(), isLive: true };
      setMessages((prev) => [...prev, userMsg]);
      sendLiveTextMessage(textToSend.trim());
      return;
    }

    setIsLoading(true);
    const userMsg: MessageItem = { role: 'user', content: textToSend.trim() };
    const currentMessages: MessageItem[] = [...messages, userMsg];
    const nextIdx = currentMessages.length;
    let thinking = '';
    let reply = '';
    let suggestions: string[] = [];

    const modelPlaceholder: MessageItem = { role: 'model', content: '', thoughts: '', suggestions: [] };
    setMessages([...currentMessages, modelPlaceholder]);

    try {
      const token = getEffectiveToken();
      const response = await fetch(chatUrl(), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          message: textToSend.trim(),
          history: currentMessages.slice(0, -1),
          ...(contextText ? { context: contextText } : {}),
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
              } catch (err) {
                console.error('JSON parse fail:', dataStr, err);
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
      className={`chat-drawer${isOpen ? ' open' : ''}`}
      data-testid="ai-chat-panel"
      style={{
        background: theme.surface,
        borderLeft: `1px solid ${theme.borderStrong}`,
        boxShadow: '-8px 0 30px rgba(0,0,0,0.35)',
        display: 'flex',
        flexDirection: 'column',
      }}
      inert={!isOpen}
      aria-hidden={!isOpen}
    >
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: 'var(--s-3) var(--s-4)',
          borderBottom: `1px solid ${theme.border}`,
          background: theme.surface2,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)' }}>
          <div style={{ fontWeight: 700, color: theme.textPrimary, fontSize: 'var(--t-body)' }}>AI Assistant</div>
          {isLiveMode && (
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                fontSize: 11,
                fontWeight: 600,
                padding: '2px 8px',
                borderRadius: 'var(--r-full)',
                background: liveStatus === 'connected' ? 'rgba(16, 185, 129, 0.15)' : 'rgba(245, 158, 11, 0.15)',
                color: liveStatus === 'connected' ? theme.growth : theme.caution,
                border: `1px solid ${liveStatus === 'connected' ? theme.growth : theme.caution}`,
              }}
            >
              <Radio size={10} className={liveStatus === 'connected' ? 'icon-pulse' : ''} />
              {liveStatus === 'connected' ? 'Live Voice' : 'Connecting...'}
            </span>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)' }}>
          {/* Toggle Live Mode Button */}
          <button
            type="button"
            onClick={handleToggleLiveMode}
            aria-label={isLiveMode ? "Switch to Text Mode" : "Switch to Gemini Live Voice Mode"}
            title={isLiveMode ? "Switch to standard text chat" : "Switch to Gemini Live voice chat"}
            style={{
              background: isLiveMode ? theme.accent : 'transparent',
              color: isLiveMode ? '#fff' : theme.textSecondary,
              border: `1px solid ${isLiveMode ? theme.accent : theme.border}`,
              borderRadius: 'var(--r-sm)',
              padding: '4px 8px',
              fontSize: 'var(--t-caption)',
              fontWeight: 600,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              transition: 'all 0.15s ease',
            }}
          >
            <Radio size={12} />
            {isLiveMode ? 'Live Mode' : 'Go Live'}
          </button>

          <button
            onClick={onClose}
            aria-label="Close AI chat"
            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 'var(--s-1)', display: 'flex' }}
          >
            <X size={18} color={theme.textSecondary} />
          </button>
        </div>
      </div>

      {/* Live Error Banner */}
      {liveError && isLiveMode && (
        <div
          style={{
            padding: 'var(--s-2) var(--s-3)',
            background: 'rgba(239, 68, 68, 0.1)',
            borderBottom: `1px solid ${theme.decline}`,
            color: theme.decline,
            fontSize: 'var(--t-caption)',
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--s-2)',
          }}
        >
          <AlertCircle size={14} />
          <span>{liveError}</span>
        </div>
      )}

      {/* Live Voice Status Indicator Bar */}
      {isLiveMode && liveStatus === 'connected' && (
        <div
          style={{
            padding: 'var(--s-2) var(--s-4)',
            background: theme.base,
            borderBottom: `1px solid ${theme.border}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            fontSize: 'var(--t-caption)',
            color: theme.textSecondary,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)' }}>
            {isSpeaking ? (
              <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: theme.accent, fontWeight: 600 }}>
                <Volume2 size={14} className="icon-pulse" /> Speaking...
              </span>
            ) : isMicActive ? (
              <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: theme.growth, fontWeight: 600 }}>
                <Mic size={14} className="icon-pulse" /> Listening to you...
              </span>
            ) : (
              <span>Mic paused • Tap mic below to speak</span>
            )}
          </div>
          <div style={{ fontSize: 11, color: theme.textMuted }}>{liveModel}</div>
        </div>
      )}

      {/* Message List */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: 'var(--s-4)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--s-4)',
        }}
      >
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              gap: 'var(--s-3)',
              flexDirection: m.role === 'user' ? 'row-reverse' : 'row',
            }}
          >
            <div
              style={{
                flexShrink: 0,
                width: 32,
                height: 32,
                borderRadius: '50%',
                background: theme.surface2,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              {m.role === 'user' ? <User size={16} color={theme.textSecondary} /> : <Bot size={16} color={theme.textSecondary} />}
            </div>
            <div style={{ maxWidth: '85%', display: 'flex', flexDirection: 'column', gap: 'var(--s-2)' }}>
              {m.role === 'model' && m.thoughts && m.thoughts.trim().length > 0 && (
                <div
                  style={{
                    background: theme.surface2,
                    border: `1px solid ${theme.border}`,
                    borderRadius: 'var(--r-md)',
                    overflow: 'hidden',
                  }}
                >
                  <button
                    onClick={() => toggleThought(i)}
                    className="btn"
                    style={{
                      width: '100%',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 'var(--s-2)',
                      padding: 'var(--s-2) var(--s-3)',
                      fontSize: 'var(--t-caption)',
                      fontWeight: 600,
                      color: theme.textSecondary,
                      background: 'transparent',
                      border: 'none',
                      borderRadius: 0,
                      justifyContent: 'flex-start',
                    }}
                  >
                    {expandedThoughts[i] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    <BrainCircuit size={14} color={theme.accent} />
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textAlign: 'left' }}>
                      {(() => {
                        if (m.content && m.content.length > 0) return "View reasoning process";
                        const lines = (m.thoughts || '').split('\n').filter((l: string) => l.trim().length > 0);
                        return lines.length > 0 ? lines[lines.length - 1] : "Analyzing context...";
                      })()}
                    </span>
                  </button>
                  {expandedThoughts[i] && (
                    <div
                      style={{
                        padding: 'var(--s-3)',
                        borderTop: `1px solid ${theme.border}`,
                        maxHeight: 250,
                        overflowY: 'auto',
                        background: theme.base,
                        fontSize: 11,
                        fontFamily: 'var(--font-mono, ui-monospace, monospace)',
                        color: theme.textSecondary,
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 'var(--s-2)',
                      }}
                    >
                      {(m.thoughts || '').split('\n').filter((l: string) => l.trim().length > 0).map((line: string, idx: number) => (
                        <div key={idx} style={{ display: 'flex', gap: 'var(--s-2)', alignItems: 'flex-start' }}>
                          <div style={{ color: theme.textMuted }}>&rsaquo;</div>
                          <div style={{ wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>{line}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {m.role === 'model' && !m.content && !expandedThoughts[i] && (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 'var(--s-2)',
                    fontSize: 'var(--t-caption)',
                    color: theme.textMuted,
                    fontStyle: 'italic',
                    padding: 'var(--s-1)',
                  }}
                >
                  <Loader2 size={12} className="icon-spin" /> {m.thoughts ? 'Thinking...' : 'Gathering insights...'}
                </div>
              )}

              {m.content && (
                <div
                  style={{
                    padding: 'var(--s-3)',
                    borderRadius: 'var(--r-lg)',
                    fontSize: 'var(--t-body)',
                    lineHeight: 1.5,
                    ...(m.role === 'user'
                      ? { background: theme.accent, color: '#fff', borderTopRightRadius: 'var(--r-2xs)' }
                      : {
                          background: theme.surface2,
                          color: theme.textPrimary,
                          border: `1px solid ${theme.border}`,
                          borderTopLeftRadius: 'var(--r-2xs)',
                        }),
                  }}
                >
                  {m.role === 'model' ? (
                    <ErrorBoundary>
                      <div className="chat-markdown">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {m.content}
                        </ReactMarkdown>
                      </div>
                    </ErrorBoundary>
                  ) : (
                    <div style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div>
                  )}
                </div>
              )}

              {m.role === 'model' && m.suggestions && m.suggestions.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--s-1-5)', marginTop: 'var(--s-2)' }}>
                  {m.suggestions.slice(0, 3).map((s: string, idx: number) => (
                    <button
                      key={idx}
                      onClick={() => handleSend(undefined, s)}
                      disabled={isLoading}
                      className="btn"
                      style={{
                        textAlign: 'left',
                        fontSize: 'var(--t-caption)',
                        background: theme.surface2,
                        color: theme.accent,
                        padding: 'var(--s-2)',
                        borderRadius: 'var(--r-sm)',
                        border: `1px solid ${theme.border}`,
                        justifyContent: 'flex-start',
                      }}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input / Control Footer */}
      <form
        onSubmit={handleSend}
        style={{
          padding: 'var(--s-3) var(--s-4)',
          borderTop: `1px solid ${theme.border}`,
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--s-2)',
          background: theme.surface2,
        }}
      >
        {/* Live Microphone Button when in Live Mode */}
        {isLiveMode && (
          <button
            type="button"
            onClick={toggleMic}
            aria-label={isMicActive ? "Mute Microphone" : "Unmute Microphone"}
            title={isMicActive ? "Mute Microphone" : "Speak to Gemini Live"}
            style={{
              width: 36,
              height: 36,
              borderRadius: '50%',
              background: isMicActive ? theme.decline : theme.surface,
              color: isMicActive ? '#fff' : theme.textPrimary,
              border: `1px solid ${isMicActive ? theme.decline : theme.border}`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              flexShrink: 0,
              transition: 'all 0.15s ease',
            }}
          >
            {isMicActive ? <Mic size={18} /> : <MicOff size={18} />}
          </button>
        )}

        <div style={{ flex: 1, position: 'relative', display: 'flex', alignItems: 'center' }}>
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
            placeholder={isLiveMode ? "Type or speak to Gemini Live..." : "Ask a question about your portfolio..."}
            rows={1}
            className="textarea"
            style={{
              width: '100%',
              paddingRight: 40,
              resize: 'none',
              borderRadius: 'var(--r-md)',
            }}
          />
          <button
            type="submit"
            disabled={!input.trim() || isLoading}
            aria-label="Send message"
            style={{
              position: 'absolute',
              right: 6,
              width: 28,
              height: 28,
              borderRadius: '50%',
              background: theme.accent,
              color: '#fff',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              border: 'none',
              cursor: 'pointer',
              opacity: (!input.trim() || isLoading) ? 0.4 : 1,
            }}
          >
            <Send size={14} />
          </button>
        </div>
      </form>
    </div>
  );
}
