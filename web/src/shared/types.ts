/**
 * The shapes that travel over the chat stream.
 *
 * The server sends one JSON object per Server-Sent Event, and they are not
 * all alike. Describing them as a union means TypeScript forces every one
 * to be handled — the previous hand-written client distinguished them with
 * a chain of string comparisons, where a typo failed silently at runtime.
 */

/** Sent first. Tells the browser which conversation this belongs to. */
export interface ConversationEvent {
  type: "conversation";
  id: string;
}

/** The model working out what to say, before it says any of it. */
export interface ThinkingEvent {
  type: "thinking";
  text: string;
}

/** A piece of the reply. Joining every one of these gives the full answer. */
export interface TextEvent {
  type: "text";
  text: string;
}

/** The assistant reaching for a tool. Shown so a pause is explicable. */
export interface ToolEvent {
  type: "tool";
  name: string;
  arguments: Record<string, unknown>;
}

/**
 * A return the assistant is proposing.
 *
 * Nothing has happened yet. The customer has to press a button, which
 * sends a separate request — the model cannot open a return, because no
 * tool does. See app/routes.py:confirm_return.
 */
export interface ConfirmEvent {
  type: "confirm";
  awaiting_confirmation: true;
  order_number: string;
  item_id: number;
  item: string;
  reason: string;
}

/**
 * Token counts. Arrives once, at the very end.
 *
 * The provider bills a single figure for everything the model wrote,
 * reasoning included, so `thinking_tokens` and `answer_tokens` are our
 * estimate of how that splits — hence `split_is_estimated`.
 */
export interface UsageEvent {
  type: "usage";
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  thinking_tokens: number;
  answer_tokens: number;
  split_is_estimated?: boolean;
}

/**
 * Something broke after the reply had already started.
 *
 * A stream commits to "200 OK" the moment it opens, so a failure after that
 * cannot be reported with a status code. It travels as data instead.
 */
export interface ErrorEvent {
  type: "error";
  message: string;
}

export type StreamEvent = ConversationEvent | ThinkingEvent | TextEvent | UsageEvent | ErrorEvent;

export type Usage = Omit<UsageEvent, "type">;

/** One message on screen. */
export interface Message {
  /**
   * Stable identity for React's list rendering.
   *
   * Keying on array position instead looks fine for an append-only list,
   * but the last message here is rewritten on every streamed chunk, and a
   * positional key invites React to reuse the wrong node when that happens.
   *
   * Optional because a restored conversation arrives from the server
   * without one; the hook assigns them on load.
   */
  id?: string;
  role: "user" | "assistant";
  content: string;
  /** The model's scratchpad. Live only — it is not stored, so it does not survive a refresh. */
  thinking?: string;
  /** How long the model spent reasoning, in seconds. */
  thoughtFor?: number;
  usage?: Usage;
  /** Tools used while producing this reply, in order. */
  tools?: { name: string; arguments: Record<string, unknown> }[];
  /** A return awaiting the customer's confirmation. */
  confirm?: Omit<ConfirmEvent, "type" | "awaiting_confirmation">;
  /** Set once the customer has confirmed, so the button cannot be pressed twice. */
  confirmed?: string;
  /** True while this reply is still arriving. */
  streaming?: boolean;
}
