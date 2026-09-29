/**
 * The other half of the approval gate.
 *
 * The assistant can propose a return; it cannot open one, because no tool
 * does. Opening happens when a person presses this, which sends a separate
 * request that checks ownership again from scratch.
 *
 * That distinction is the whole point. If the assistant asked "shall I?"
 * and accepted "yes" as text, the model would be deciding — and an
 * instruction hidden in a tool result could supply that yes. A sentence
 * cannot press a button.
 */
interface Props {
  item: string;
  orderNumber: string;
  reason: string;
  /** The reference, once it has been opened. Replaces the button. */
  confirmed?: string;
  onConfirm: () => void;
  disabled: boolean;
}

export function ConfirmReturn({
  item,
  orderNumber,
  reason,
  confirmed,
  onConfirm,
  disabled,
}: Props) {
  if (confirmed) {
    return (
      <div className="confirm done">
        ✓ Return opened for <strong>{item}</strong> — reference <code>{confirmed.slice(0, 8)}</code>
      </div>
    );
  }

  return (
    <div className="confirm">
      <div className="confirm-what">
        Return <strong>{item}</strong> from {orderNumber}
        {reason ? <span className="confirm-why">“{reason}”</span> : null}
      </div>
      <button type="button" onClick={onConfirm} disabled={disabled}>
        Confirm return
      </button>
    </div>
  );
}
