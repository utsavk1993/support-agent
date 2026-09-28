import { useState } from "react";

interface Props {
  onSend: (text: string) => void;
  disabled: boolean;
}

export function Composer({ onSend, disabled }: Props) {
  const [text, setText] = useState("");

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    setText("");
    onSend(trimmed);
  }

  return (
    <form onSubmit={submit}>
      <input
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder="Type your question…"
        autoComplete="off"
        // This is a chat window whose only purpose is typing into this box,
        // so focusing it is what a person expects rather than a surprise.
        // biome-ignore lint/a11y/noAutofocus: the only interactive element on the page
        autoFocus
        // Locked while a reply is arriving. Without it you can fire off five
        // requests by mashing Enter, and the replies come back out of order.
        disabled={disabled}
      />
      <button type="submit" disabled={disabled || !text.trim()}>
        Send
      </button>
    </form>
  );
}
