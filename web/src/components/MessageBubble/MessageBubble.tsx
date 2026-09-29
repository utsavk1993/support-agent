import { renderMarkdown } from "../../shared/markdown";
import type { Message } from "../../shared/types";
import { ConfirmReturn } from "../ConfirmReturn/ConfirmReturn";
import { ThinkingBlock } from "../ThinkingBlock/ThinkingBlock";
import { ToolTrace } from "../ToolTrace/ToolTrace";
import { UsageLine } from "../UsageLine/UsageLine";

interface Props {
  message: Message;
  /** Present only on a reply proposing a return. */
  onConfirm?: () => void;
  busy?: boolean;
}

export function MessageBubble({ message, onConfirm, busy = false }: Props) {
  const isAssistant = message.role === "assistant";

  return (
    <div className={`msg ${message.role}`}>
      <div className="bubble">
        {message.tools?.length ? <ToolTrace tools={message.tools} /> : null}

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

        {message.confirm && onConfirm && (
          <ConfirmReturn
            item={message.confirm.item}
            orderNumber={message.confirm.order_number}
            reason={message.confirm.reason}
            confirmed={message.confirmed}
            onConfirm={onConfirm}
            disabled={busy}
          />
        )}

        {message.usage && <UsageLine usage={message.usage} />}
      </div>
    </div>
  );
}
