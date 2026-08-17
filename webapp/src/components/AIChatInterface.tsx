import React, { useState, useRef, useEffect, Component } from 'react';
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
  Volume2,
  Radio,
  MessageSquare,
  Sparkles,
} from 'lucide-react';
import { getEffectiveToken } from '../auth/apiToken';
import { chatUrl, api } from '../api/client';
import type { AiModelsResponse } from '../api/types';
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

interface AIChatInterfaceProps {
  isOpen: boolean;
  onClose: () => void;
  contextText?: string;
}

interface ChatMessage {
  role: 'user' | 'model';
  content: string;
  thoughts?: string;
  suggestions?: string[];
  isLive?: boolean;
  // Set once this live message's turn has finished (Gemini's
  // "turn_complete" event). A sealed message is never appended to again --
  // the next transcript/thought fragment for that role starts a fresh
  // bubble instead, so multi-turn conversations don't run all their text
  // together into one ever-growing message.
  turnComplete?: boolean;
}

export default function AIChatInterface({ isOpen, onClose, contextText }: AIChatInterfaceProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: 'model', content: "Hi! Ask me any questions about the data." }
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isLiveMode, setIsLiveMode] = useState(false);
  const [expandedThoughts, setExpandedThoughts] = useState<Record<number, boolean>>({});
  const [modelCatalog, setModelCatalog] = useState<AiModelsResponse | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<string>('gemini');
  const [selectedModel, setSelectedModel] = useState<string>('gemini-2.5-flash');
  const [customModel, setCustomModel] = useState<string>('');
  const [showCustomInput, setShowCustomInput] = useState<boolean>(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (isOpen) {
      api.getAiModels().then((data) => {
        setModelCatalog(data);
        if (data?.default_provider && data.default_provider !== 'auto') {
          setSelectedProvider(data.default_provider);
          if (data.default_model) {
            setSelectedModel(data.default_model);
          }
        }
      }).catch((err) => {
        console.warn('Failed to load AI model catalog:', err);
      });
    }
  }, [isOpen]);

  // Gemini Live Hook
  const {
    status: liveStatus,
    isMicActive,
    isSpeaking,
    liveModel,
    connectLive,
    disconnectLive,
    toggleMic,
    sendTextMessage,
    sendContext,
  } = useGeminiLive({
    // input_transcription events stream the user's speech as INCREMENTAL
    // fragments (mirroring how Gemini's own audio-transcription samples
    // print each chunk with no separator/newline) -- each fragment is
    // concatenated directly onto the still-open live user message, not
    // used to replace it, so a multi-fragment utterance renders as the
    // full sentence instead of only its last fragment. `!last.turnComplete`
    // keeps this from appending into an already-finished prior turn's
    // message once a new one starts.
    onUserTranscript: (text) => {
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.role === 'user' && last.isLive && !last.turnComplete) {
          const updated = [...prev];
          updated[updated.length - 1] = { ...last, content: `${last.content}${text}` };
          return updated;
        }
        return [...prev, { role: 'user', content: text, isLive: true }];
      });
    },
    // isPartial=true is the streamed `part.text` path (space-joined, as
    // before); isPartial=false is output_transcription, which -- like
    // input_transcription above -- streams incremental fragments rather
    // than the full cumulative text each time, so it's now appended
    // (direct concat, matching the ASR-fragment convention) instead of
    // replacing the message outright.
    onModelTranscript: (text, isPartial) => {
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.role === 'model' && last.isLive && !last.turnComplete) {
          const updated = [...prev];
          const appended = isPartial
            ? (last.content ? `${last.content} ${text}` : text)
            : `${last.content}${text}`;
          updated[updated.length - 1] = { ...last, content: appended };
          return updated;
        }
        return [...prev, { role: 'model', content: text, isLive: true }];
      });
    },
    onThought: (thought) => {
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.role === 'model' && last.isLive && !last.turnComplete) {
          const updated = [...prev];
          const prevThoughts = last.thoughts || '';
          updated[updated.length - 1] = {
            ...last,
            thoughts: prevThoughts ? `${prevThoughts}\n${thought}` : thought,
          };
          return updated;
        }
        return [
          ...prev,
          { role: 'model', content: '', thoughts: thought, isLive: true },
        ];
      });
    },
    // Seals the trailing live message so the NEXT turn's transcript/thought
    // fragments open a fresh bubble instead of appending into this
    // now-finished one.
    onTurnComplete: () => {
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.isLive && !last.turnComplete) {
          const updated = [...prev];
          updated[updated.length - 1] = { ...last, turnComplete: true };
          return updated;
        }
        return prev;
      });
    },
    onError: (err) => {
      setMessages((prev) => [
        ...prev,
        { role: 'model', content: `⚠️ Live Error: ${err}`, isLive: true },
      ]);
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

  // Latest contextText, read (not subscribed to) by the connect effect
  // below -- mirrors useGeminiLive.ts's own optionsRef pattern.
  const contextTextRef = useRef(contextText);
  useEffect(() => {
    contextTextRef.current = contextText;
  }, [contextText]);

  // Connect/disconnect Live ONLY when the drawer opens/closes or Live Mode
  // is toggled -- deliberately NOT when contextText changes. contextText
  // can legitimately update while a live voice session is already open
  // (e.g. the operator is mid-conversation as the viewed symbol/page
  // changes), and connectLive() tears down any existing connection before
  // opening a new one -- if this effect depended on contextText directly,
  // every such update would silently disconnect and reconnect the live
  // session, losing Gemini's turn state and any buffered audio. The most
  // recent contextText is still threaded in as the initial connect-time
  // context via contextTextRef; updates that happen AFTER connecting are
  // sent over the already-open connection by the effect below instead.
  useEffect(() => {
    if (isOpen && isLiveMode) {
      connectLive(contextTextRef.current);
    } else {
      disconnectLive();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, isLiveMode, connectLive, disconnectLive]);

  // Push a context UPDATE over the already-open connection (instead of
  // reconnecting) whenever contextText changes while live and connected.
  // lastSentContextRef tracks what's already been sent for the CURRENT
  // session so this doesn't re-send the same initial value connectLive
  // already sent once on open; it's cleared whenever the session isn't
  // live-connected, so the next connection starts the tracking over.
  const lastSentContextRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!isLiveMode || liveStatus !== 'connected') {
      lastSentContextRef.current = undefined;
      return;
    }
    if (lastSentContextRef.current === undefined) {
      // First render of this connected session -- connectLive already sent
      // this exact value as the initial context; nothing new to push.
      lastSentContextRef.current = contextText;
      return;
    }
    if (contextText && contextText !== lastSentContextRef.current) {
      lastSentContextRef.current = contextText;
      sendContext(contextText);
    }
  }, [contextText, isLiveMode, liveStatus, sendContext]);

  const toggleThought = (idx: number) => {
    setExpandedThoughts((prev) => ({ ...prev, [idx]: !prev[idx] }));
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend(e);
    }
  };

  const handleSend = async (
    e?: React.FormEvent | React.KeyboardEvent,
    overrideText: string | null = null
  ) => {
    e?.preventDefault();
    const textToSend = overrideText || input;
    if (!textToSend.trim() || isLoading) return;

    if (!overrideText) setInput('');

    if (isLiveMode) {
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: textToSend.trim(), isLive: true },
      ]);
      sendTextMessage(textToSend.trim());
      return;
    }

    setIsLoading(true);

    const currentMessages = [...messages, { role: 'user' as const, content: textToSend.trim() }];
    const nextIdx = currentMessages.length;
    let thinking = '';
    let reply = '';
    const suggestions: string[] = [];

    setMessages([...currentMessages, { role: 'model', content: '', thoughts: '', suggestions: [] }]);

    try {
      const token = getEffectiveToken();
      const response = await fetch(chatUrl(), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          message: textToSend.trim(),
          history: currentMessages.slice(0, -1),
          ...(contextText ? { context: contextText } : {}),
          ...(selectedProvider !== 'auto' ? { provider: selectedProvider } : {}),
          ...((showCustomInput && customModel.trim())
            ? { model: customModel.trim() }
            : (selectedModel ? { model: selectedModel } : {})),
        }),
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

                setMessages((prev) => {
                  const updated = [...prev];
                  updated[nextIdx] = {
                    role: 'model',
                    content: reply,
                    thoughts: thinking,
                    suggestions: suggestions,
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
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)' }}>
          <div style={{ fontWeight: 700, color: theme.textPrimary, fontSize: 'var(--t-body)' }}>
            AI Assistant
          </div>
          {isLiveMode && (
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                padding: '2px 8px',
                borderRadius: 'var(--r-pill)',
                background: liveStatus === 'connected' ? 'rgba(16, 185, 129, 0.15)' : 'rgba(245, 158, 11, 0.15)',
                color: liveStatus === 'connected' ? theme.growth : theme.caution,
                fontSize: 11,
                fontWeight: 600,
              }}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: liveStatus === 'connected' ? theme.growth : theme.caution,
                  display: 'inline-block',
                }}
              />
              Live Audio
            </div>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)' }}>
          {/* Toggle Live Mode Button */}
          <button
            type="button"
            onClick={() => setIsLiveMode(!isLiveMode)}
            className="btn"
            title={isLiveMode ? "Switch to Text Chat" : "Switch to Live Voice Chat"}
            style={{
              padding: '4px 10px',
              fontSize: 12,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              background: isLiveMode ? 'var(--c-accent-subtle, rgba(99, 102, 241, 0.15))' : theme.surface2,
              color: isLiveMode ? theme.accent : theme.textSecondary,
              border: `1px solid ${isLiveMode ? theme.accent : theme.border}`,
              borderRadius: 'var(--r-pill)',
              cursor: 'pointer',
            }}
          >
            {isLiveMode ? <Radio size={13} className="icon-pulse" /> : <MessageSquare size={13} />}
            <span>{isLiveMode ? "Voice Live" : "Text Chat"}</span>
          </button>

          <button
            onClick={onClose}
            aria-label="Close AI chat"
            style={{
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              padding: 'var(--s-1)',
              display: 'flex',
            }}
          >
            <X size={18} color={theme.textSecondary} />
          </button>
        </div>
      </div>

      {/* Model Selection Bar (Text Chat Mode) */}
      {!isLiveMode && (
        <div
          data-testid="ai-model-selector-bar"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: 'var(--s-2) var(--s-4)',
            background: theme.surface2,
            borderBottom: `1px solid ${theme.border}`,
            fontSize: 'var(--t-caption)',
            gap: 'var(--s-2)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: theme.textSecondary }}>
            <Sparkles size={13} style={{ color: theme.accent }} />
            <span style={{ fontWeight: 500 }}>Model:</span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, justifyContent: 'flex-end' }}>
            {!showCustomInput ? (
              <select
                aria-label="AI Model Selector"
                value={selectedModel ? `${selectedProvider}:${selectedModel}` : (selectedProvider === 'auto' ? 'auto:' : `${selectedProvider}:default`)}
                onChange={(e) => {
                  const val = e.target.value;
                  if (val === 'custom') {
                    setShowCustomInput(true);
                    return;
                  }
                  if (val === 'auto:' || val === 'auto') {
                    setSelectedProvider('auto');
                    setSelectedModel('');
                    return;
                  }
                  const [p, ...m] = val.split(':');
                  setSelectedProvider(p);
                  setSelectedModel(m.join(':') === 'default' ? '' : m.join(':'));
                }}
                style={{
                  background: theme.surface,
                  color: theme.textPrimary,
                  border: `1px solid ${theme.border}`,
                  borderRadius: 'var(--r-sm)',
                  padding: '3px 8px',
                  fontSize: 12,
                  maxWidth: '230px',
                  cursor: 'pointer',
                }}
              >
                <option value="auto:">✨ Auto (Best Available)</option>
                {modelCatalog?.providers ? (
                  modelCatalog.providers.map((p) => (
                    <optgroup key={p.id} label={`${p.name}${p.available ? '' : ' (Key not set)'}`}>
                      {p.models.map((m) => (
                        <option key={`${p.id}:${m}`} value={`${p.id}:${m}`}>
                          {m}
                        </option>
                      ))}
                    </optgroup>
                  ))
                ) : (
                  <>
                    <optgroup label="Google Gemini">
                      <option value="gemini:gemini-2.5-flash">Gemini 2.5 Flash</option>
                      <option value="gemini:gemini-2.5-pro">Gemini 2.5 Pro</option>
                      <option value="gemini:gemini-1.5-pro">Gemini 1.5 Pro</option>
                    </optgroup>
                    <optgroup label="Anthropic Claude">
                      <option value="anthropic:claude-3-5-sonnet-20241022">Claude 3.5 Sonnet</option>
                      <option value="anthropic:claude-3-5-haiku-20241022">Claude 3.5 Haiku</option>
                      <option value="anthropic:claude-3-opus-20240229">Claude 3 Opus</option>
                    </optgroup>
                    <optgroup label="OpenAI ChatGPT">
                      <option value="openai:gpt-4o">GPT-4o</option>
                      <option value="openai:gpt-4o-mini">GPT-4o Mini</option>
                      <option value="openai:o3-mini">o3-mini</option>
                      <option value="openai:o1">o1</option>
                    </optgroup>
                    <optgroup label="Local / Open Source (Ollama, vLLM)">
                      <option value="local:llama3.3">Llama 3.3 (Local)</option>
                      <option value="local:deepseek-r1">DeepSeek R1 (Local)</option>
                      <option value="local:qwen2.5">Qwen 2.5 (Local)</option>
                      <option value="local:mistral">Mistral (Local)</option>
                    </optgroup>
                  </>
                )}
                <option value="custom">✏️ Custom Model...</option>
              </select>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, flex: 1, maxWidth: '240px' }}>
                <input
                  type="text"
                  placeholder="e.g. deepseek-ai/DeepSeek-V3"
                  value={customModel}
                  onChange={(e) => setCustomModel(e.target.value)}
                  style={{
                    flex: 1,
                    background: theme.surface,
                    color: theme.textPrimary,
                    border: `1px solid ${theme.border}`,
                    borderRadius: 'var(--r-sm)',
                    padding: '3px 8px',
                    fontSize: 12,
                  }}
                />
                <button
                  type="button"
                  onClick={() => setShowCustomInput(false)}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: theme.textSecondary,
                    cursor: 'pointer',
                    padding: 2,
                    fontSize: 11,
                  }}
                  title="Back to dropdown"
                >
                  ✕
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Live Audio Status Banner (when Live Mode is on) */}
      {isLiveMode && (
        <div
          style={{
            padding: 'var(--s-2) var(--s-4)',
            background: 'var(--c-surface-elevated, #181c24)',
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
