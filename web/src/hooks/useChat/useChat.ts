/**
 * useChat — the conversation, and everything that happens to it.
 *
 * All the state and all the stream handling live here, so the components
 * below are left doing nothing but drawing. That separation is the main
 * reason for the rewrite: in the old client, parsing the stream and building
 * DOM nodes were interleaved in the same function.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { HttpError, loadConversation, sendMessage } from "../../shared/api";
import type { Message } from "../../shared/types";

const STORAGE_KEY = "conversationId";

/** A stable key for a message, unique within this page's lifetime. */
let nextMessageId = 0;
const messageId = () => `m${nextMessageId++}`;

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [sending, setSending] = useState(false);
  const [restoring, setRestoring] = useState(true);

  // The id lives in a ref as well as storage because the send function reads
  // it, and a ref does not go stale between renders the way a captured
  // state value would.
  const conversationId = useRef<string | null>(localStorage.getItem(STORAGE_KEY));

  const rememberConversation = useCallback((id: string | null) => {
    conversationId.current = id;
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  }, []);

  // Bring back the conversation from last time. This is the whole point of
  // the history living on the server: closing the tab no longer destroys it.
  useEffect(() => {
    const id = conversationId.current;
    if (!id) {
      setRestoring(false);
      return;
    }

    loadConversation(id)
      .then((restored) => {
        // null means the server does not recognise it. Forget it and start
        // fresh rather than retrying something that will never work.
        if (restored) setMessages(restored.map((m) => ({ ...m, id: messageId() })));
        else rememberConversation(null);
      })
      // Offline, or the server is down. Leave the page empty rather than
      // showing an error for something the user did not ask for.
      .catch(() => undefined)
      .finally(() => setRestoring(false));
  }, [rememberConversation]);

  const send = useCallback(
    async (text: string) => {
      setSending(true);

      // Show the question immediately, and an empty reply to fill in.
      setMessages((current) => [
        ...current,
        { role: "user", content: text },
        { role: "assistant", content: "", streaming: true },
      ]);

      // Everything below writes to the LAST message, which is the reply we
      // just added.
      const updateReply = (change: (reply: Message) => Message) =>
        setMessages((current) => {
          const next = [...current];
          next[next.length - 1] = change(next[next.length - 1]);
          return next;
        });

      const startedAt = performance.now();
      let thinkingDone = false;

      async function stream(id: string | null) {
        for await (const event of sendMessage(text, id)) {
          switch (event.type) {
            case "conversation":
              rememberConversation(event.id);
              break;

            case "thinking":
              updateReply((reply) => ({
                ...reply,
                thinking: (reply.thinking ?? "") + event.text,
              }));
              break;

            case "text":
              // The first real text means the reasoning is over.
              if (!thinkingDone) {
                thinkingDone = true;
                const seconds = (performance.now() - startedAt) / 1000;
                updateReply((reply) => ({ ...reply, thoughtFor: seconds }));
              }
              updateReply((reply) => ({ ...reply, content: reply.content + event.text }));
              break;

            case "usage":
              updateReply((reply) => ({ ...reply, usage: event }));
              break;

            case "error":
              // Too late for a status code — the server told us in the stream.
              updateReply((reply) => ({
                ...reply,
                content: `${reply.content}\n\n⚠️ ${event.message}`,
              }));
              break;
          }
        }
      }

      try {
        try {
          await stream(conversationId.current);
        } catch (error) {
          // The server does not recognise this conversation: deleted, or the
          // session cookie changed. The id we hold is useless.
          //
          // Without this, every later message failed the same way and the chat
          // was stuck for good. Forget it and send again as a new conversation,
          // so a vanished conversation costs one message and not the session.
          if (error instanceof HttpError && error.status === 404 && conversationId.current) {
            rememberConversation(null);
            await stream(null);
          } else {
            throw error;
          }
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : "Something went wrong.";
        updateReply((reply) => ({ ...reply, content: `⚠️ ${message}` }));
      } finally {
        updateReply((reply) => ({ ...reply, streaming: false }));
        setSending(false);
      }
    },
    [rememberConversation],
  );

  return { messages, send, sending, restoring };
}
