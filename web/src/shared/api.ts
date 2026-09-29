/**
 * api.ts — talking to the server, and reading its stream.
 *
 * The only file that knows about HTTP. Everything above it deals in typed
 * events rather than bytes.
 */

import type { Message, StreamEvent } from "./types";

/**
 * Read a Server-Sent Events response, yielding one typed event at a time.
 *
 * The format is simple: each event is a line starting `data: `, followed by
 * a blank line. The awkward part is that they arrive in whatever sized lumps
 * the network feels like — a single read might contain two events, or half
 * of one. So we keep a buffer and only parse what is complete, leaving any
 * partial event for the next read.
 *
 * `async function*` is a generator: it yields values over time rather than
 * returning once, which is what lets the caller write `for await (...)`.
 */
async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<StreamEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // A blank line ends an event. Whatever follows the last one is
    // incomplete, so it stays in the buffer for the next read.
    //
    // Written as an index rather than `blocks.pop() ?? ""` because split()
    // always returns at least one element, so the fallback could never run
    // — and an unreachable branch is a branch nothing can ever test.
    const parts = buffer.split("\n\n");
    const blocks = parts.slice(0, -1);
    buffer = parts[parts.length - 1];

    for (const block of blocks) {
      if (!block.startsWith("data: ")) continue;
      yield JSON.parse(block.slice(6)) as StreamEvent;
    }
  }
}

export class HttpError extends Error {
  // Written out longhand rather than as a constructor parameter property,
  // because those are TypeScript-only syntax and this project builds with
  // `erasableSyntaxOnly`: types must vanish without changing the JavaScript.
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/**
 * Send a message and stream the reply.
 *
 * Only the new message goes up, plus which conversation it belongs to. The
 * server holds the history and loads it — the browser does not send it.
 */
export async function* sendMessage(
  message: string,
  conversationId: string | null,
): AsyncGenerator<StreamEvent> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });

  // Failures BEFORE the reply starts still arrive as an ordinary HTTP
  // error, because the server deliberately fetches the first piece before
  // it commits to a status code.
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new HttpError(response.status, detail.detail ?? `Server returned ${response.status}`);
  }

  if (!response.body) throw new Error("The response had no body.");
  yield* readEvents(response.body);
}

/** Fetch a stored conversation, or null if the server does not recognise it. */
export async function loadConversation(id: string): Promise<Message[] | null> {
  const response = await fetch(`/api/conversations/${id}`);

  // 404 means it is gone, or belongs to someone else — which is what you
  // get after clearing cookies, since identity lives in one.
  if (response.status === 404) return null;
  if (!response.ok) throw new HttpError(response.status, "Could not load the conversation.");

  const data = await response.json();
  return data.messages as Message[];
}

/** Open a return the assistant proposed. Nothing happens until this runs. */
export async function confirmReturn(
  conversationId: string,
  orderNumber: string,
  itemId: number,
  reason: string,
): Promise<{ return_id: string }> {
  const response = await fetch("/api/returns", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_id: conversationId,
      order_number: orderNumber,
      item_id: itemId,
      reason,
    }),
  });

  if (!response.ok) {
    throw new HttpError(response.status, "That return could not be opened.");
  }
  return response.json();
}
