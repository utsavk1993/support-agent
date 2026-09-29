/**
 * What the assistant looked up while answering.
 *
 * Tool calls take seconds, and without this the customer sees a pause
 * they cannot distinguish from the thing being broken. Saying "checking
 * order NW-8891" also makes the answer trustworthy in a way the answer
 * alone is not — they can see where it came from.
 */
interface Props {
  tools: { name: string; arguments: Record<string, unknown> }[];
}

/** Tool names are for us; this is what a customer should read. */
const PHRASING: Record<string, (args: Record<string, unknown>) => string> = {
  request_verification_code: () => "Sending a verification code",
  submit_verification_code: () => "Checking the code",
  list_my_orders: () => "Looking up your orders",
  look_up_order: (args) => `Checking order ${args.order_number ?? ""}`,
  check_shipment: (args) => `Tracking order ${args.order_number ?? ""}`,
  propose_return: (args) => `Preparing a return for ${args.order_number ?? ""}`,
};

export function ToolTrace({ tools }: Props) {
  if (tools.length === 0) return null;

  return (
    <div className="tools">
      {tools.map((tool, index) => (
        <div className="tool" key={`${tool.name}-${index}`}>
          {PHRASING[tool.name]?.(tool.arguments) ?? tool.name}
        </div>
      ))}
    </div>
  );
}
