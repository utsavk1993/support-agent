import { renderMarkdown } from "../markdown";
import type { Message } from "../types";
import { ThinkingBlock } from "./ThinkingBlock";
import { UsageLine } from "./UsageLine";

export function MessageBubble({ message }: { message: Message }) {
  const isAssistant = message.role === "assistant";

  return (
    <div className={`msg ${message.role}`}>
      <div className="bubble">
        {message.thinking && (
          <ThinkingBlock text={message.thinking} thoughtFor={message.thoughtFor} />
        )}

        {isAssistant ? (
          // Replies are markdown, rendered through a sanitiser. See
          // markdown.ts for why that second step is not optional.
          <div className="body" dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }} />
        ) : (
          // What the customer typed is plain text and must never be treated
          // as markup. React escapes it for us; `white-space: pre-wrap` in
          // the stylesheet keeps their line breaks.
          <div className="body">{message.content}</div>
        )}

        {message.usage && <UsageLine usage={message.usage} />}
      </div>
    </div>
  );
}
