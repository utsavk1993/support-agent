import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import * as api from "./shared/api";
import type { StreamEvent } from "./shared/types";

async function* streamOf(events: StreamEvent[]) {
  for (const event of events) yield event;
}

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(api, "loadConversation").mockResolvedValue(null);
  // jsdom has no layout, so scrolling is not implemented on elements.
  Element.prototype.scrollTo = vi.fn();
});

afterEach(() => vi.restoreAllMocks());

describe("App", () => {
  it("invites a first question when there is nothing to show", async () => {
    render(<App />);
    expect(await screen.findByText(/Ask about returns/)).toBeInTheDocument();
  });

  it("shows a restored conversation instead of the invitation", async () => {
    localStorage.setItem("conversationId", "conv-1");
    vi.spyOn(api, "loadConversation").mockResolvedValue([
      { role: "user", content: "earlier question" },
      { role: "assistant", content: "earlier answer" },
    ]);

    render(<App />);

    expect(await screen.findByText("earlier question")).toBeInTheDocument();
    expect(screen.queryByText(/Ask about returns/)).toBeNull();
  });

  it("renders a reply that arrives while it is open", async () => {
    vi.spyOn(api, "sendMessage").mockImplementation(() =>
      streamOf([
        { type: "conversation", id: "conv-1" },
        { type: "text", text: "A 15% fee." },
      ]),
    );

    render(<App />);
    await screen.findByText(/Ask about returns/);

    await userEvent.type(screen.getByRole("textbox"), "fee?{Enter}");

    await waitFor(() => expect(screen.getByText("A 15% fee.")).toBeInTheDocument());
  });
});
