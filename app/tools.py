"""
tools.py — what the assistant can do, and what it is allowed to do it to.

Two sets. Which one a conversation gets depends on whether it has proved
who it is, and that decision is made here rather than by the model.

THE RULE THAT MATTERS
---------------------
No tool takes a customer as an argument. Every one takes the conversation
id and reads the verified customer from the database.

If a tool accepted "whose orders shall I read", the model would be
choosing — and the model reads order notes, return reasons and whatever
else a customer typed. An instruction hidden in any of those could pick a
different answer. Here there is no argument to poison.
"""

from app import store

# ---------------------------------------------------------------------------
# WHAT THE MODEL SEES
# ---------------------------------------------------------------------------
# The description is the whole basis on which a model decides when to reach
# for a tool, so it gets as much care as the code beneath it.

VERIFICATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "request_verification_code",
            "description": (
                "Send a six digit verification code to the customer. Call this once you "
                "have BOTH their email address and their phone number. The code goes to "
                "the address and number held on their account. Always tell the customer "
                "a code has been sent, whatever this returns."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "The email address the customer gave."},
                    "phone": {"type": "string", "description": "The phone number the customer gave."},
                },
                "required": ["email", "phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_verification_code",
            "description": (
                "Check the six digit code the customer read back. On success the "
                "conversation is verified and you may help them. On failure, say it did "
                "not work and offer to send another — do not speculate about why."
            ),
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "The six digits the customer gave."}},
                "required": ["code"],
            },
        },
    },
]

SUPPORT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_my_orders",
            "description": (
                "List this customer's orders, newest first. Use it when they cannot remember an order number, or ask about 'my last order'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "look_up_order",
            "description": (
                "Everything about one order: status, items, what was paid, when it was "
                "placed and delivered. The delivery date is what the return windows are "
                "measured from, and each item's category decides which rule applies."
            ),
            "parameters": {
                "type": "object",
                "properties": {"order_number": {"type": "string", "description": "For example NW-8891."}},
                "required": ["order_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_shipment",
            "description": "Carrier, tracking number and expected delivery date for an order.",
            "parameters": {
                "type": "object",
                "properties": {"order_number": {"type": "string", "description": "For example NW-8891."}},
                "required": ["order_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_return",
            "description": (
                "Propose returning one item. This does NOT open the return: it asks the "
                "customer to confirm, and they must press a button to go ahead. Check the "
                "policy applies first — look up the order, and tell them about any "
                "restocking fee before proposing. Use the item id from look_up_order."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_number": {"type": "string"},
                    "item_id": {"type": "integer", "description": "From look_up_order."},
                    "reason": {"type": "string", "description": "Why they are returning it."},
                },
                "required": ["order_number", "item_id", "reason"],
            },
        },
    },
]


def tools_for(verified: bool) -> list[dict]:
    """The tools a conversation may use.

    An unverified conversation is not given the support tools at all. It
    cannot call what it has not been offered, so there is no instruction
    to talk it out of.
    """
    return SUPPORT_TOOLS if verified else VERIFICATION_TOOLS


# ---------------------------------------------------------------------------
# WHAT ACTUALLY RUNS
# ---------------------------------------------------------------------------
def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _describe_order(order: dict) -> dict:
    """Shape an order for the model: readable, and no more than it needs."""
    return {
        "order_number": order["number"],
        "status": order["status"],
        "placed_at": order["placed_at"].date().isoformat(),
        "delivered_at": order["delivered_at"].date().isoformat() if order["delivered_at"] else None,
        "days_since_delivery": ((store.datetime.now(store.UTC) - order["delivered_at"]).days if order["delivered_at"] else None),
        "total": _money(order["total_cents"]),
        "shipping": order["shipping"],
        "items": [
            {
                "item_id": item["id"],
                "description": item["description"],
                "category": item["category"],
                "quantity": item["quantity"],
                "unit_price": _money(item["unit_cents"]),
                "return_status": item["return_status"],
            }
            for item in order["items"]
        ],
    }


async def run(name: str, arguments: dict, conversation_id: str, verified: bool) -> dict:
    """Run one tool and return its result.

    Refuses anything outside the current set. A model that asks for a
    support tool before verifying is not obeyed and not argued with — it
    simply does not happen.
    """
    allowed = {tool["function"]["name"] for tool in tools_for(verified)}
    if name not in allowed:
        return {"error": "That is not available yet."}

    if name == "request_verification_code":
        result = await store.issue_verification_code(conversation_id, arguments.get("email", ""), arguments.get("phone", ""))
        if result.get("rate_limited"):
            return {"error": "Too many attempts. Please try again later."}
        # Identical whether or not the details matched. Saying "no such
        # customer" would confirm which addresses are real.
        sent_to = result.get("sent_to")
        return {
            "sent": True,
            "to_email": sent_to["email"] if sent_to else None,
            "to_phone": sent_to["phone"] if sent_to else None,
            "note": "Tell the customer a code has been sent and ask them to read it back.",
        }

    if name == "submit_verification_code":
        result = await store.check_verification_code(conversation_id, arguments.get("code", ""))
        if result.get("rate_limited"):
            return {"error": "Too many attempts. Please try again later."}
        if not result["ok"]:
            return {"verified": False, "note": "Offer to send another code. Do not say why it failed."}
        return {"verified": True, "customer_name": result["customer"]["name"]}

    if name == "list_my_orders":
        await store.record_access(conversation_id, name)
        orders = await store.list_orders(conversation_id)
        return {
            "orders": [
                {
                    "order_number": o["number"],
                    "status": o["status"],
                    "placed_at": o["placed_at"].date().isoformat(),
                    "total": _money(o["total_cents"]),
                }
                for o in orders
            ]
        }

    if name == "look_up_order":
        number = arguments.get("order_number", "")
        await store.record_access(conversation_id, name, number)
        order = await store.find_order(conversation_id, number)
        # An order belonging to someone else is reported exactly as one
        # that does not exist.
        if order is None:
            return {"error": f"No order {number} found on this account."}
        return _describe_order(order)

    if name == "check_shipment":
        number = arguments.get("order_number", "")
        await store.record_access(conversation_id, name, number)
        shipment = await store.find_shipment(conversation_id, number)
        if shipment is None:
            return {"error": f"No shipment found for {number} on this account."}
        return {
            "order_number": number,
            "order_status": shipment["order_status"],
            "carrier": shipment["carrier"],
            "tracking": shipment["tracking"],
            "shipped_at": shipment["shipped_at"].date().isoformat(),
            "expected_at": shipment["expected_at"].isoformat(),
            "delivered_at": (shipment["delivered_at"].date().isoformat() if shipment["delivered_at"] else None),
        }

    if name == "propose_return":
        number = arguments.get("order_number", "")
        item_id = arguments.get("item_id")
        order = await store.find_order(conversation_id, number)
        if order is None:
            return {"error": f"No order {number} found on this account."}

        item = next((i for i in order["items"] if i["id"] == item_id), None)
        if item is None:
            return {"error": "That item is not on that order."}
        if item["return_status"]:
            return {"error": f"A return for that item is already {item['return_status']}."}

        # Deliberately does NOT open the return. It hands the client
        # something to put a button on; the customer presses it and a
        # separate request does the work. The model proposes, a person
        # decides, and the server checks again before acting.
        return {
            "awaiting_confirmation": True,
            "order_number": order["number"],
            "item_id": item["id"],
            "item": item["description"],
            "reason": arguments.get("reason", ""),
            "note": (
                "Tell the customer what will happen, including any restocking fee, "
                "and that they need to confirm below. You have not opened anything yet."
            ),
        }

    return {"error": "Unknown tool."}  # pragma: no cover - guarded by `allowed`
