import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { UsageLine } from "./UsageLine";

const usage = {
  prompt_tokens: 684,
  completion_tokens: 100,
  total_tokens: 784,
  thinking_tokens: 40,
  answer_tokens: 60,
  split_is_estimated: true,
};

describe("UsageLine", () => {
  it("shows the breakdown", () => {
    render(<UsageLine usage={usage} />);
    expect(screen.getByText(/684 in/)).toBeInTheDocument();
    expect(screen.getByText(/784 total/)).toBeInTheDocument();
  });

  it("marks the estimated parts with a tilde", () => {
    // The provider bills one figure for everything the model wrote, so the
    // split is ours. Presenting it as exact would be a lie.
    render(<UsageLine usage={usage} />);
    const line = screen.getByText(/thinking/);
    expect(line.textContent).toContain("~40 thinking");
    expect(line.textContent).toContain("~60 answer");
  });

  it("puts the billed figures where they can be checked", () => {
    render(<UsageLine usage={usage} />);
    expect(screen.getByText(/684 in/)).toHaveAttribute(
      "title",
      expect.stringContaining("684 input + 100 output"),
    );
  });
});
