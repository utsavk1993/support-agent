import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Composer } from "./Composer";

describe("Composer", () => {
  it("sends what was typed", async () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} disabled={false} />);

    await userEvent.type(screen.getByRole("textbox"), "restocking fee?{Enter}");
    expect(onSend).toHaveBeenCalledWith("restocking fee?");
  });

  it("clears the box after sending", async () => {
    render(<Composer onSend={vi.fn()} disabled={false} />);
    const box = screen.getByRole("textbox");

    await userEvent.type(box, "hello{Enter}");
    expect(box).toHaveValue("");
  });

  it("trims surrounding space", async () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} disabled={false} />);

    await userEvent.type(screen.getByRole("textbox"), "   hello   {Enter}");
    expect(onSend).toHaveBeenCalledWith("hello");
  });

  it("refuses to send nothing", async () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} disabled={false} />);

    await userEvent.type(screen.getByRole("textbox"), "    {Enter}");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("will not send even if a submit reaches it while locked", () => {
    // The inputs are disabled, so a person cannot get here — but a stray
    // programmatic submit still must not fire a second request.
    const onSend = vi.fn();
    const { container } = render(<Composer onSend={onSend} disabled />);

    container
      .querySelector("form")
      ?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));

    expect(onSend).not.toHaveBeenCalled();
  });

  it("locks while a reply is streaming", async () => {
    // Without this you can fire off five requests by mashing Enter, and
    // the replies come back out of order.
    const onSend = vi.fn();
    render(<Composer onSend={onSend} disabled />);

    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button")).toBeDisabled();
  });
});
