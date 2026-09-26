import { useEffect, useRef } from "react";

import { Composer } from "./components/Composer";
import { MessageBubble } from "./components/MessageBubble";
import { useChat } from "./hooks/useChat";

export default function App() {
  const { messages, send, sending, restoring } = useChat();
  const scroller = useRef<HTMLDivElement>(null);

  // Follow the reply as it grows.
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [messages]);

  return (
    <>
      <header>
        <h1>Northwind Tools — Support</h1>
        <span>Returns · Warranty · Shipping · Price matching</span>
      </header>

      <div id="chat" ref={scroller}>
        <div className="wrap">
          {!restoring && messages.length === 0 && (
            <div className="empty">
              Ask about returns, warranties, shipping or price matching.
              <br />
              Try: <code>What's the restocking fee on a used power tool?</code>
            </div>
          )}

          {messages.map((message, index) => (
            <MessageBubble key={index} message={message} />
          ))}
        </div>
      </div>

      <footer>
        <Composer onSend={send} disabled={sending} />
      </footer>
    </>
  );
}
