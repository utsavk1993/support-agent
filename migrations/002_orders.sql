-- Customers, their orders, and returns.
--
-- Enough structure for the assistant to APPLY the support policy rather
-- than recite it. Two columns carry most of that weight:
--
--   orders.delivered_at    the return windows run from delivery, not from
--                          the order date. Without it, "am I still in
--                          time?" is unanswerable.
--
--   order_items.category   consumables are non-returnable once opened,
--                          used power tools carry a 15% fee within 14
--                          days, hand tools have a lifetime warranty.
--                          The rule that applies depends on this.

CREATE TABLE customers (
    id    uuid PRIMARY KEY,
    name  text NOT NULL,

    -- Identity is proved by matching BOTH of these. There are no accounts,
    -- so this pair is what stands in for a login.
    email text NOT NULL,
    phone text NOT NULL
);

-- Verification looks up by both together, and email alone must be unique
-- so a match can never be ambiguous.
CREATE UNIQUE INDEX customers_email_idx ON customers (lower(email));
CREATE INDEX customers_identity_idx ON customers (lower(email), phone);


-- Which customer a conversation has proved itself to be.
--
-- NULL until they pass verification. Every tool reads this column rather
-- than trusting anything the model believes: if "already verified" were
-- something the model remembered, a note inside an order could assert it
-- and the model would have no way to tell the difference.
ALTER TABLE conversations
    ADD COLUMN verified_customer_id uuid REFERENCES customers (id);


CREATE TABLE orders (
    -- The reference a customer actually quotes, not a surrogate key.
    number       text        PRIMARY KEY,
    customer_id  uuid        NOT NULL REFERENCES customers (id),

    placed_at    timestamptz NOT NULL,
    -- NULL until it arrives. The return clock starts here.
    delivered_at timestamptz,

    status       text        NOT NULL
                 CHECK (status IN ('placed', 'picking', 'shipped', 'delivered', 'cancelled')),

    total_cents  integer     NOT NULL,
    shipping     text        NOT NULL
                 CHECK (shipping IN ('standard', 'expedited', 'overnight', 'freight'))
);

CREATE INDEX orders_customer_idx ON orders (customer_id, placed_at DESC);


CREATE TABLE order_items (
    id           bigserial PRIMARY KEY,
    order_number text      NOT NULL REFERENCES orders (number) ON DELETE CASCADE,

    description  text      NOT NULL,
    -- Decides which policy applies. See the note at the top.
    category     text      NOT NULL
                 CHECK (category IN ('hand_tool', 'power_tool', 'consumable', 'oversized')),

    quantity     integer   NOT NULL CHECK (quantity > 0),
    unit_cents   integer   NOT NULL
);

CREATE INDEX order_items_order_idx ON order_items (order_number);


CREATE TABLE shipments (
    -- One shipment per order here. A real system would split them.
    order_number text        PRIMARY KEY REFERENCES orders (number) ON DELETE CASCADE,

    carrier      text        NOT NULL,
    tracking     text        NOT NULL,
    shipped_at   timestamptz NOT NULL,
    expected_at  date        NOT NULL,
    delivered_at timestamptz
);


CREATE TABLE returns (
    id           uuid        PRIMARY KEY,
    order_number text        NOT NULL REFERENCES orders (number),
    item_id      bigint      NOT NULL REFERENCES order_items (id),

    reason       text        NOT NULL,
    status       text        NOT NULL DEFAULT 'open'
                 CHECK (status IN ('open', 'received', 'refunded', 'rejected')),
    opened_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX returns_order_idx ON returns (order_number);
