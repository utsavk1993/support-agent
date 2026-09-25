-- Conversations and the messages inside them.
--
-- Written to be applied exactly once, in order, by the runner in store.py.
-- Migrations are never edited after they have run anywhere: the database
-- records which have been applied, so changing one means it silently does
-- not match what is actually deployed. Change the schema with a new file.

CREATE TABLE conversations (
    id          uuid        PRIMARY KEY,

    -- Who this belongs to. Today a signed session identifier; once accounts
    -- exist, a user identifier. Deliberately text rather than a foreign key,
    -- so adding a users table later does not require rewriting this one.
    owner_id    text        NOT NULL,

    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Every request looks up conversations by owner, so this index is the
-- difference between checking one row and scanning the whole table.
CREATE INDEX conversations_owner_idx ON conversations (owner_id, updated_at DESC);


CREATE TABLE messages (
    id              bigserial   PRIMARY KEY,

    -- ON DELETE CASCADE: removing a conversation removes its messages, in
    -- one statement, with no chance of leaving orphans behind.
    conversation_id uuid        NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,

    role            text        NOT NULL CHECK (role IN ('user', 'assistant')),
    content         text        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),

    -- Token accounting, assistant messages only; NULL on user messages.
    -- Stored per message rather than only shown in the browser, so the cost
    -- of a conversation is a query rather than an estimate.
    --
    -- prompt_tokens and completion_tokens are what the provider billed.
    -- thinking_tokens and answer_tokens are our split of completion_tokens,
    -- estimated by how much of the output was reasoning, since the provider
    -- does not report the two separately.
    prompt_tokens     integer,
    completion_tokens integer,
    thinking_tokens   integer,
    answer_tokens     integer
);

-- Messages are always read as a conversation, oldest first.
CREATE INDEX messages_conversation_idx ON messages (conversation_id, id);
