import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router";
import App from "./App";
import { ChatProvider } from "./chat/ChatContext";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      {/* Wraps the router root (not just App's own JSX) so App itself can
          call useChat() to wire up the floating chat button/panel, while
          every routed screen (e.g. Options Matrix) can also call it to open
          the same global panel pre-loaded with screen-specific context. */}
      <ChatProvider>
        <App />
      </ChatProvider>
    </BrowserRouter>
  </React.StrictMode>
);
