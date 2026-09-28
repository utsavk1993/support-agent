import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageBubble } from "./MessageBubble";

describe("MessageBubble", () => {
  it("renders an assistant reply as markdown", () => {
    render(<MessageBubble message={{ role: "assistant", content: "a **15%** fee" }} />);
    expect(screen.getByText("15%").tagName).toBe("STRONG");
  });

  it("never renders what the customer typed as markup", () => {
    // Their message is plain text. Treating it as markdown, or as HTML,
    // would let them format — or inject — into our page.
    const typed = "is <b>this</b> **bold**?";
    render(<MessageBubble message={{ role: "user", content: typed }} />);
    expect(screen.getByText(typed)).toBeInTheDocument();
  });

  it("sanitises an assistant reply", () => {
    const { container } = render(
      <MessageBubble message={{ role: "assistant", content: '<img src=x onerror="alert(1)">' }} />,
    );
    expect(container.innerHTML).not.toContain("onerror");
  });

  it("shows the thinking block only when there is reasoning", () => {
    const { container, rerender } = render(
      <MessageBubble message={{ role: "assistant", content: "hi" }} />,
    );
    expect(container.querySelector("details")).toBeNull();

    rerender(<MessageBubble message={{ role: "assistant", content: "hi", thinking: "hmm" }} />);
    expect(container.querySelector("details")).not.toBeNull();
  });

  it("shows token counts only once they have arrived", () => {
    render(<MessageBubble message={{ role: "assistant", content: "hi" }} />);
    expect(screen.queryByText(/total/)).toBeNull();
  });

  it("shows them when they have", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "hi",
          usage: {
            prompt_tokens: 684,
            completion_tokens: 100,
            total_tokens: 784,
            thinking_tokens: 40,
            answer_tokens: 60,
          },
        }}
      />,
    );
    expect(screen.getByText(/784 total/)).toBeInTheDocument();
  });
});
