/**
 * The sanitising step is the reason this module exists, so it is what gets
 * tested hardest.
 *
 * Model output is not trusted input: a customer writes into the chat, that
 * text enters the prompt, and the model can be induced to repeat it. If any
 * of these got through, a message would run someone else's script in our page.
 */

import { describe, expect, it } from "vitest";

import { renderMarkdown } from "./markdown";

describe("renderMarkdown", () => {
  it("renders ordinary markdown", () => {
    expect(renderMarkdown("a **15%** fee")).toContain("<strong>15%</strong>");
  });

  it("renders lists", () => {
    const html = renderMarkdown("- one\n- two");
    expect(html).toContain("<li>");
  });

  it.each([
    ["a script tag", "<script>alert(1)</script>"],
    ["an inline handler", '<img src=x onerror="alert(1)">'],
    ["a javascript: link", '<a href="javascript:alert(1)">click</a>'],
    ["an svg handler", '<svg onload="alert(1)"></svg>'],
    ["an iframe", '<iframe src="https://evil.test"></iframe>'],
  ])("strips %s", (_name, malicious) => {
    const html = renderMarkdown(malicious);

    expect(html).not.toContain("alert(1)");
    expect(html.toLowerCase()).not.toContain("<script");
    expect(html.toLowerCase()).not.toContain("onerror");
    expect(html.toLowerCase()).not.toContain("onload");
    expect(html.toLowerCase()).not.toContain("<iframe");
  });

  it("keeps the harmless text of something it strips", () => {
    // The point is to remove the danger, not to swallow the message.
    expect(renderMarkdown("before <script>alert(1)</script> after")).toContain("before");
  });
});
