import React, { createContext, useCallback, useContext, useState } from "react";

/**
 * Global chat-panel open/close + context-injection state.
 *
 * `AIChatInterface` (webapp/src/components/AIChatInterface.tsx) is mounted
 * once, globally, in App.tsx -- individual screens (e.g. Options Matrix)
 * live under React Router and have no direct access to App.tsx's local
 * state. This context is the seam that lets any screen under
 * `<ChatProvider>` open the one global chat panel pre-loaded with a
 * screen-specific context string (e.g. a summary of the options directives
 * currently on screen), without threading callbacks through the router.
 *
 * Mirrors this codebase's existing ToastContext.tsx pattern: a Provider
 * holding the state, a `useX()` hook with a safe no-op fallback so it can
 * never throw when called outside the Provider (defense in depth -- in
 * practice `<ChatProvider>` wraps the whole app, see main.tsx).
 */

interface ChatContextValue {
  isOpen: boolean;
  /** Pre-formatted text block threaded into the next outgoing chat request
   * as `context` (see api/data_api.py::ChatMessageRequest.context).
   * `undefined` when the panel was opened with no context (e.g. via the
   * floating chat button), matching AIChatInterface's existing optional
   * `contextText` prop. */
  contextText: string | undefined;
  /** Opens the global chat panel. Passing no argument (or an empty string)
   * opens it with no context, same as today's floating chat button. */
  openChat: (contextText?: string) => void;
  closeChat: () => void;
}

const ChatContext = createContext<ChatContextValue | undefined>(undefined);

export const ChatProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [contextText, setContextText] = useState<string | undefined>(undefined);

  const openChat = useCallback((text?: string) => {
    setContextText(text && text.length > 0 ? text : undefined);
    setIsOpen(true);
  }, []);

  const closeChat = useCallback(() => {
    setIsOpen(false);
  }, []);

  return (
    <ChatContext.Provider value={{ isOpen, contextText, openChat, closeChat }}>
      {children}
    </ChatContext.Provider>
  );
};

const dummyChatContext: ChatContextValue = {
  isOpen: false,
  contextText: undefined,
  openChat: () => {},
  closeChat: () => {},
};

/** Safe to call from any component -- returns a no-op fallback outside
 * `<ChatProvider>` rather than throwing, matching useToast's convention. */
export function useChat(): ChatContextValue {
  const ctx = useContext(ChatContext);
  return ctx || dummyChatContext;
}
