import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router";
import App from "./App";
import { ChatProvider } from "./chat/ChatContext";
import { EnvError } from "./components/EnvError";
import { envIssues, isEnvValid } from "./config/env";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {/* Env validation runs as an import-time side effect of ./config/env --
        necessarily so, since ESM evaluates the whole import graph (including
        api/client.ts's module-scope base-URL and USE_MOCK consts) before this
        file's body runs. All we can do here is branch on the result: render
        the app, or render the error screen instead of it. */}
    {isEnvValid ? (
      <BrowserRouter>
        {/* Wraps the router root (not just App's own JSX) so App itself can
            call useChat() to wire up the floating chat button/panel, while
            every routed screen (e.g. Options Matrix) can also call it to open
            the same global panel pre-loaded with screen-specific context. */}
        <ChatProvider>
          <App />
        </ChatProvider>
      </BrowserRouter>
    ) : (
      <EnvError issues={envIssues} />
    )}
  </React.StrictMode>
);
