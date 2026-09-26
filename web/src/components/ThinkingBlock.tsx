/**
 * The model's reasoning, folded away.
 *
 * It matters that this exists at all: on a longer question the model can
 * reason for several seconds before writing a word of the answer, and
 * without this the user watches an empty bubble for the whole of it.
 *
 * It stays collapsed. Someone asking about a refund should not have to read
 * the assistant deliberating over whether to grant it.
 */
interface Props {
  text: string;
  /** Seconds spent reasoning. Absent while it is still going. */
  thoughtFor?: number;
}

export function ThinkingBlock({ text, thoughtFor }: Props) {
  const finished = thoughtFor !== undefined;

  return (
    <details className="thinking">
      <summary className={finished ? "" : "pulse"}>
        {finished ? `Thought for ${thoughtFor.toFixed(1)}s` : "Thinking…"}
      </summary>
      <div className="thinking-body">{text}</div>
    </details>
  );
}
