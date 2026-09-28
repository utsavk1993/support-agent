/**
 * The hook holds the conversation and drives the stream, so these cover the
 * behaviours that would be worst to break: losing a reply, wedging the chat
 * on a dead conversation id, or forgetting which conversation we are in.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../../shared/api";
import { HttpError } from "../../shared/api";
import type { StreamEvent } from "../../shared/types";

import { useChat } from "./useChat";

/** Turn a list of events into the async stream the hook consumes. */
async function* streamOf(events: StreamEvent[]) {
  for (const event of events) yield event;
}

const REPLY: StreamEvent[] = [
  { type: "conversation", id: "conv-1" },
  { type: "thinking", text: "working it out" },
  { type: "text", text: "A 15% " },
  { type: "text", text: "restocking fee." },
  {
    type: "usage",
    prompt_tokens: 684,
    completion_tokens: 100,
    total_tokens: 784,
    thinking_tokens: 40,
    answer_tokens: 60,
  },
];

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(api, "loadConversation").mockResolvedValue(null);
});

afterEach(() => vi.restoreAllMocks());

describe("sending a message", () => {
  it("shows the question and the reply", async () => {
    vi.spyOn(api, "sendMessage").mockImplementation(() => streamOf(REPLY));
    const { result } = renderHook(() => useChat());

    await act(() => result.current.send("restocking fee?"));

    const [question, reply] = result.current.messages;
    expect(question).toMatchObject({ role: "user", content: "restocking fee?" });
    // Every text event joined, not only the last.
    expect(reply.content).toBe("A 15% restocking fee.");
  });

  it("keeps reasoning apart from the answer", async () => {
    vi.spyOn(api, "sendMessage").mockImplementation(() => streamOf(REPLY));
    const { result } = renderHook(() => useChat());

    await act(() => result.current.send("hi"));

    const reply = result.current.messages[1];
    expect(reply.thinking).toBe("working it out");
    expect(reply.content).not.toContain("working it out");
    // Timed, so the block can say how long it took.
    expect(reply.thoughtFor).toBeGreaterThanOrEqual(0);
  });

  it("records the token counts", async () => {
    vi.spyOn(api, "sendMessage").mockImplementation(() => streamOf(REPLY));
    const { result } = renderHook(() => useChat());

    await act(() => result.current.send("hi"));
    expect(result.current.messages[1].usage?.total_tokens).toBe(784);
  });

  it("remembers the conversation so a refresh can restore it", async () => {
    vi.spyOn(api, "sendMessage").mockImplementation(() => streamOf(REPLY));
    const { result } = renderHook(() => useChat());

    await act(() => result.current.send("hi"));
    expect(localStorage.getItem("conversationId")).toBe("conv-1");
  });
});

describe("when the conversation has vanished", () => {
  it("forgets the id and sends again as a new conversation", async () => {
    // The server no longer recognises it: deleted, or the cookie changed.
    // Before this, every later message failed the same way and the chat was
    // stuck showing "Not found." for good.
    localStorage.setItem("conversationId", "stale");
    // Restore has to SUCCEED here, or the hook drops the id on mount and
    // there is no stale id left for send to fail with. An empty array is a
    // conversation that exists and has no messages yet.
    vi.spyOn(api, "loadConversation").mockResolvedValue([]);

    const send = vi.spyOn(api, "sendMessage").mockImplementation((_text, id) => {
      if (id === "stale") throw new HttpError(404, "Not found.");
      return streamOf([
        { type: "conversation", id: "fresh" },
        { type: "text", text: "ok" },
      ]);
    });

    const { result } = renderHook(() => useChat());
    await waitFor(() => expect(result.current.restoring).toBe(false));

    await act(() => result.current.send("hello"));

    expect(send).toHaveBeenCalledTimes(2);
    expect(send).toHaveBeenLastCalledWith("hello", null); // retried without it
    expect(result.current.messages[1].content).toBe("ok");
    expect(localStorage.getItem("conversationId")).toBe("fresh");
  });

  it("drops a stored id the server rejects on load", async () => {
    localStorage.setItem("conversationId", "gone");
    vi.spyOn(api, "loadConversation").mockResolvedValue(null);

    const { result } = renderHook(() => useChat());

    await waitFor(() => expect(result.current.restoring).toBe(false));
    expect(localStorage.getItem("conversationId")).toBeNull();
  });
});

describe("when something else goes wrong", () => {
  it("shows the failure instead of leaving an empty bubble", async () => {
    vi.spyOn(api, "sendMessage").mockImplementation(() => {
      throw new Error("the assistant is unavailable");
    });

    const { result } = renderHook(() => useChat());
    await act(() => result.current.send("hi"));

    expect(result.current.messages[1].content).toContain("unavailable");
  });

  it("unlocks the composer even after a failure", async () => {
    vi.spyOn(api, "sendMessage").mockImplementation(() => {
      throw new Error("boom");
    });

    const { result } = renderHook(() => useChat());
    await act(() => result.current.send("hi"));

    // Otherwise one failure locks the person out of the chat entirely.
    expect(result.current.sending).toBe(false);
  });
});

describe("restoring on load", () => {
  it("brings back a stored conversation", async () => {
    localStorage.setItem("conversationId", "conv-1");
    vi.spyOn(api, "loadConversation").mockResolvedValue([
      { role: "user", content: "earlier question" },
      { role: "assistant", content: "earlier answer" },
    ]);

    const { result } = renderHook(() => useChat());

    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect(result.current.messages[0].content).toBe("earlier question");
  });

  it("stays empty when the server cannot be reached", async () => {
    // Offline is not the customer's problem to see an error about.
    localStorage.setItem("conversationId", "conv-1");
    vi.spyOn(api, "loadConversation").mockRejectedValue(new Error("offline"));

    const { result } = renderHook(() => useChat());

    await waitFor(() => expect(result.current.restoring).toBe(false));
    expect(result.current.messages).toEqual([]);
  });
});

describe("when the reply is cut short", () => {
  it("keeps the text that arrived and appends the reason", async () => {
    // A stream that fails after it has started cannot use a status code —
    // the response already promised 200 — so the server sends an error
    // event instead, and the customer keeps what they had already read.
    vi.spyOn(api, "sendMessage").mockImplementation(() =>
      streamOf([
        { type: "conversation", id: "conv-1" },
        { type: "text", text: "Used power tools " },
        { type: "error", message: "The reply was cut short." },
      ]),
    );

    const { result } = renderHook(() => useChat());
    await act(() => result.current.send("hi"));

    const reply = result.current.messages[1];
    expect(reply.content).toContain("Used power tools");
    expect(reply.content).toContain("cut short");
  });
});

describe("when something that is not an Error is thrown", () => {
  it("still says something useful", async () => {
    // A library can throw anything at all. Reading `.message` off a string
    // gives undefined, and the bubble would read "⚠️ undefined".
    vi.spyOn(api, "sendMessage").mockImplementation(() => {
      throw "a bare string";
    });

    const { result } = renderHook(() => useChat());
    await act(() => result.current.send("hi"));

    expect(result.current.messages[1].content).toBe("⚠️ Something went wrong.");
  });
});
