"""
seed.py — demo customers and orders.

    python -m scripts.seed

Separate from the migrations on purpose. Migrations run everywhere,
including production, and shipping fake orders to a real system is how
demo data ends up somewhere it should never be. This has to be run
deliberately.

Every date is relative to now, so an order "delivered four days ago" stays
four days ago however long the file sits here. Fixed dates would quietly
drift out of every policy window and the data would stop demonstrating
anything.

Safe to run twice: it clears the demo rows first.
"""

import asyncio
import uuid

from app import store

# Prices in cents, because money in floating point eventually rounds
# somewhere you cannot see.
CUSTOMERS = [
    ("Priya Raman", "priya.raman@example.com", "+1-415-555-0142"),
    ("Tom Okafor", "tom.okafor@example.com", "+1-503-555-0188"),
    ("Dana Whitfield", "dana.whitfield@example.com", "+1-212-555-0119"),
    ("Marcus Lee", "marcus.lee@example.com", "+1-617-555-0177"),
]

# (number, customer index, placed days ago, delivered days ago or None,
#  status, shipping, [(description, category, quantity, unit_cents)])
#
# Chosen so the interesting policy cases are all reachable:
# inside and outside both return windows, a consumable, an oversized
# freight item, a hand tool under lifetime warranty, something still being
# picked, something already cancelled, and an order above the 500 dollar
# escalation threshold.
ORDERS = [
    (
        "NW-8891",
        0,
        6,
        4,
        "delivered",
        "standard",
        [
            ("DeWalt 20V Cordless Drill", "power_tool", 1, 17900),
            ("Titanium Drill Bit Set", "hand_tool", 1, 4200),
        ],
    ),
    (
        "NW-8903",
        0,
        22,
        19,
        "delivered",
        "expedited",
        [
            ("Random Orbital Sander", "power_tool", 1, 12400),
        ],
    ),
    (
        "NW-8917",
        0,
        3,
        None,
        "shipped",
        "standard",
        [
            ("Marking Gauge", "hand_tool", 2, 2600),
        ],
    ),
    (
        "NW-8925",
        1,
        48,
        44,
        "delivered",
        "standard",
        [
            ("Benchtop Planer", "power_tool", 1, 42900),
        ],
    ),
    (
        "NW-8938",
        1,
        9,
        6,
        "delivered",
        "standard",
        [
            ("Assorted Sandpaper, 120 sheets", "consumable", 3, 1850),
            ("Danish Oil, 1L", "consumable", 2, 2300),
        ],
    ),
    (
        "NW-8944",
        1,
        1,
        None,
        "picking",
        "overnight",
        [
            ("Dovetail Saw", "hand_tool", 1, 8900),
        ],
    ),
    (
        "NW-8951",
        2,
        15,
        11,
        "delivered",
        "freight",
        [
            ("Cabinetmaker's Workbench", "oversized", 1, 129500),
        ],
    ),
    (
        "NW-8963",
        2,
        5,
        2,
        "delivered",
        "standard",
        [
            ("Block Plane", "hand_tool", 1, 15600),
            ("Honing Guide", "hand_tool", 1, 3400),
            ("Waterstone, 1000 grit", "consumable", 1, 5900),
        ],
    ),
    (
        "NW-8970",
        2,
        34,
        None,
        "cancelled",
        "standard",
        [
            ("Router Table", "power_tool", 1, 38900),
        ],
    ),
    (
        "NW-8982",
        3,
        2,
        None,
        "shipped",
        "expedited",
        [
            ("Track Saw", "power_tool", 1, 61900),
        ],
    ),
    (
        "NW-8995",
        3,
        13,
        9,
        "delivered",
        "standard",
        [
            ("Chisel Set, 6 piece", "hand_tool", 1, 11200),
        ],
    ),
    (
        "NW-9004",
        3,
        60,
        56,
        "delivered",
        "standard",
        [
            ("Biscuit Joiner", "power_tool", 1, 22900),
            ("Biscuits, size 20, 100ct", "consumable", 2, 1400),
        ],
    ),
    (
        "NW-9011",
        0,
        27,
        24,
        "delivered",
        "standard",
        [
            ("Spokeshave", "hand_tool", 1, 6700),
        ],
    ),
    (
        "NW-9020",
        1,
        4,
        1,
        "delivered",
        "overnight",
        [
            ("Digital Angle Gauge", "hand_tool", 1, 4900),
        ],
    ),
    (
        "NW-9033",
        2,
        11,
        None,
        "shipped",
        "freight",
        [
            ("Lathe, 14 inch swing", "oversized", 1, 189900),
        ],
    ),
]

CARRIERS = ["Northwind Freight", "Parcelforce", "Interstate Courier"]


async def seed() -> None:
    await store.connect()
    pool = store.pool()

    async with pool.acquire() as connection:
        async with connection.transaction():
            # Clear first, so running this twice does not accumulate or
            # collide on the order numbers.
            await connection.execute("DELETE FROM returns")
            await connection.execute("UPDATE conversations SET verified_customer_id = NULL")
            await connection.execute("DELETE FROM shipments")
            await connection.execute("DELETE FROM order_items")
            await connection.execute("DELETE FROM orders")
            await connection.execute("DELETE FROM customers")

            customer_ids = []
            for name, email, phone in CUSTOMERS:
                customer_id = uuid.uuid4()
                customer_ids.append(customer_id)
                await connection.execute(
                    "INSERT INTO customers (id, name, email, phone) VALUES ($1, $2, $3, $4)",
                    customer_id,
                    name,
                    email,
                    phone,
                )

            for number, who, placed_ago, delivered_ago, status, shipping, items in ORDERS:
                placed = f"now() - interval '{placed_ago} days'"
                delivered = f"now() - interval '{delivered_ago} days'" if delivered_ago is not None else "NULL"
                total = sum(quantity * unit for _, _, quantity, unit in items)

                await connection.execute(
                    f"""
                    INSERT INTO orders
                        (number, customer_id, placed_at, delivered_at, status, total_cents, shipping)
                    VALUES ($1, $2, {placed}, {delivered}, $3, $4, $5)
                    """,
                    number,
                    customer_ids[who],
                    status,
                    total,
                    shipping,
                )

                for description, category, quantity, unit in items:
                    await connection.execute(
                        """
                        INSERT INTO order_items
                            (order_number, description, category, quantity, unit_cents)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        number,
                        description,
                        category,
                        quantity,
                        unit,
                    )

                # Anything past picking has shipped.
                if status in ("shipped", "delivered"):
                    ship_ago = placed_ago - 1
                    await connection.execute(
                        f"""
                        INSERT INTO shipments
                            (order_number, carrier, tracking, shipped_at, expected_at, delivered_at)
                        VALUES ($1, $2, $3,
                                now() - interval '{ship_ago} days',
                                (now() - interval '{max(ship_ago - 4, 0)} days')::date,
                                {delivered})
                        """,
                        number,
                        CARRIERS[hash(number) % len(CARRIERS)],
                        f"NW{abs(hash(number)) % 10**9:09d}",
                    )

    counts = await pool.fetchrow("""
        SELECT (SELECT count(*) FROM customers)   AS customers,
               (SELECT count(*) FROM orders)      AS orders,
               (SELECT count(*) FROM order_items) AS items,
               (SELECT count(*) FROM shipments)   AS shipments
    """)
    print(f"seeded {counts['customers']} customers, {counts['orders']} orders, {counts['items']} items, {counts['shipments']} shipments")

    print("\nsign in as any of these:")
    for name, email, phone in CUSTOMERS:
        print(f"  {name:18} {email:34} {phone}")

    await store.disconnect()


if __name__ == "__main__":
    asyncio.run(seed())
