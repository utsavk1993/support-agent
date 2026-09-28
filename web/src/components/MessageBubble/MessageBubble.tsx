import { renderMarkdown } from "../../shared/markdown";
import type { Message } from "../../shared/types";
import { ThinkingBlock } from "../ThinkingBlock/ThinkingBlock";
import { UsageLine } from "../UsageLine/UsageLine";

export function MessageBubble({ message }: { message: Message }) {
  const isAssistant = message.role === "assistant";

  return (
    <div className={`msg ${message.role}`}>
      <div className="bubble">
        {message.thinking && (
          <ThinkingBlock text={message.thinking} thoughtFor={message.thoughtFor} />
        )}

        {isAssistant ? (
          // The reply is markdown and has to become HTML. renderMarkdown
          // runs it through DOMPurify first, which is the whole reason that
          // module exists — see shared/markdown.ts and its tests.
          <div
            className="body"
            // biome-ignore lint/security/noDangerouslySetInnerHtml: sanitised in renderMarkdown
            dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }}
          />
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
