import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ThinkingBlock } from "./ThinkingBlock";

describe("ThinkingBlock", () => {
  it("says it is thinking while the model still is", () => {
    render(<ThinkingBlock text="working it out" />);
    expect(screen.getByText("Thinking…")).toBeInTheDocument();
  });

  it("pulses only while unfinished", () => {
    const { rerender } = render(<ThinkingBlock text="x" />);
    expect(screen.getByText("Thinking…")).toHaveClass("pulse");

    rerender(<ThinkingBlock text="x" thoughtFor={1.5} />);
    expect(screen.getByText("Thought for 1.5s")).not.toHaveClass("pulse");
  });

  it("reports how long it took once the answer begins", () => {
    render(<ThinkingBlock text="x" thoughtFor={7.42} />);
    expect(screen.getByText("Thought for 7.4s")).toBeInTheDocument();
  });

  it("stays collapsed", () => {
    // A customer asking about a refund should not have to read the
    // assistant deliberating over whether to grant it.
    const { container } = render(<ThinkingBlock text="deliberating" />);
    expect(container.querySelector("details")).not.toHaveAttribute("open");
  });
});
