import type { Usage } from "../../shared/types";

/**
 * The token counts under a reply.
 *
 * The tildes are load-bearing. The provider reports ONE figure for
 * everything the model wrote, reasoning and answer together, so the split
 * shown here is worked out from how much of the output was reasoning. The
 * billed figures are exact and shown on hover.
 */
export function UsageLine({ usage }: { usage: Usage }) {
  return (
    <div
      className="meta"
      title={
        `Billed: ${usage.prompt_tokens} input + ${usage.completion_tokens} output.\n` +
        `The provider does not separate reasoning from the answer, so that ` +
        `split is estimated from the length of each.`
      }
    >
      {usage.prompt_tokens} in + ~{usage.thinking_tokens} thinking
      {" + ~"}
      {usage.answer_tokens} answer = {usage.total_tokens} total
    </div>
  );
}
