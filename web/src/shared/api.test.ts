/**
 * The awkward part of reading a stream is that events do not arrive in
 * whole pieces. The network delivers whatever sized lumps it feels like,
 * so a single read might hold two events, or half of one.
 *
 * These tests feed the parser deliberately awkward splits.
 */

import { describe, expect, it, vi } from "vitest";

import { HttpError, loadConversation, sendMessage } from "./api";
import type { StreamEvent } from "./types";

/** A fetch whose body arrives in exactly the chunks given. */
function streamingFetch(chunks: string[]) {
  const encoder = new TextEncoder();
  return vi.fn().mockResolvedValue({
    ok: true,
    body: new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
  });
}

async function collect(chunks: string[]): Promise<StreamEvent[]> {
  vi.stubGlobal("fetch", streamingFetch(chunks));
  const events: StreamEvent[] = [];
  for await (const event of sendMessage("hi", null)) events.push(event);
  vi.unstubAllGlobals();
  return events;
}

const TEXT = (t: string) => `data: {"type":"text","text":"${t}"}\n\n`;

describe("reading the event stream", () => {
  it("reads events that arrive one per chunk", async () => {
    const events = await collect([TEXT("one"), TEXT("two")]);
    expect(events).toEqual([
      { type: "text", text: "one" },
      { type: "text", text: "two" },
    ]);
  });

  it("reads two events delivered in a single chunk", async () => {
    const events = await collect([TEXT("one") + TEXT("two")]);
    expect(events).toHaveLength(2);
  });

  it("waits for an event split across chunks", async () => {
    // The split falls in the middle of the JSON.
    const whole = TEXT("split");
    const events = await collect([whole.slice(0, 14), whole.slice(14)]);
    expect(events).toEqual([{ type: "text", text: "split" }]);
  });

  it("waits when only part of the blank-line separator has arrived", async () => {
    // One newline is not the end of an event. Treating it as one would
    // parse a fragment and throw.
    const whole = TEXT("edge");
    const cut = whole.length - 1;
    const events = await collect([whole.slice(0, cut), whole.slice(cut)]);
    expect(events).toEqual([{ type: "text", text: "edge" }]);
  });

  it("ignores anything that is not a data line", async () => {
    const events = await collect([`: keep-alive\n\n${TEXT("real")}`]);
    expect(events).toEqual([{ type: "text", text: "real" }]);
  });
});

describe("failures before the reply starts", () => {
  it("raises an HttpError carrying the status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: async () => ({ detail: "Not found." }),
      }),
    );

    // The 404 matters: the hook uses it to recover from a stale id.
    await expect(async () => {
      for await (const _ of sendMessage("hi", "gone")) {
        /* drain */
      }
    }).rejects.toThrow(HttpError);

    vi.unstubAllGlobals();
  });
});

describe("loadConversation", () => {
  it("returns null on 404 rather than throwing", async () => {
    // The caller treats null as "forget this conversation", so a missing
    // one must not be an exception.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 404, ok: false }));
    expect(await loadConversation("gone")).toBeNull();
    vi.unstubAllGlobals();
  });

  it("returns the stored messages", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        json: async () => ({ messages: [{ role: "user", content: "hi" }] }),
      }),
    );
    expect(await loadConversation("abc")).toEqual([{ role: "user", content: "hi" }]);
    vi.unstubAllGlobals();
  });
});

describe("edge cases in the parser", () => {
  it("copes with a body that ends exactly on an event boundary", async () => {
    // `blocks.pop()` returns undefined when the split leaves nothing
    // trailing, which the `?? ""` fallback exists for.
    const events = await collect([TEXT("only")]);
    expect(events).toHaveLength(1);
  });

  it("falls back to the status when the server sends no detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => {
          throw new Error("not json");
        },
      }),
    );

    await expect(async () => {
      for await (const _ of sendMessage("hi", null)) {
        /* drain */
      }
    }).rejects.toThrow("Server returned 502");

    vi.unstubAllGlobals();
  });

  it("raises when a response has no body at all", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, body: null }));

    await expect(async () => {
      for await (const _ of sendMessage("hi", null)) {
        /* drain */
      }
    }).rejects.toThrow("no body");

    vi.unstubAllGlobals();
  });
});

describe("loadConversation failures", () => {
  it("raises on anything other than 404", async () => {
    // A 500 is not "this conversation is gone" — treating it as such would
    // silently discard a conversation that still exists.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 500, ok: false }));
    await expect(loadConversation("abc")).rejects.toThrow(HttpError);
    vi.unstubAllGlobals();
  });
});
