-- One-time codes, and a record of who saw what.
--
-- Email and phone are identifiers, not secrets: they appear in breaches
-- and on business cards, and knowing one proves nothing about controlling
-- it. A code issued to the channel on file is what turns identification
-- into authentication, because it requires possession.

CREATE TABLE verification_codes (
    -- One live code per conversation. Requesting another replaces it.
    conversation_id uuid        PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    customer_id     uuid        NOT NULL REFERENCES customers (id),

    -- The code itself is never stored. Only the hash is kept, so a copy of
    -- this table is not a list of working codes.
    code_hash       text        NOT NULL,

    issued_at       timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,

    -- Wrong guesses against this code. Caps brute force even when the
    -- attacker already knows a real email and phone.
    attempts        integer     NOT NULL DEFAULT 0
);


-- Every verification attempt, whatever its outcome.
--
-- Prevents nothing. Makes "what did this conversation try, and what was
-- it shown" a question with an answer.
CREATE TABLE verification_attempts (
    id              bigserial   PRIMARY KEY,
    conversation_id uuid        NOT NULL,

    -- What was tried, masked. Storing the full address would hand an
    -- attacker a list of real customer emails if this table ever leaked,
    -- which is the opposite of the point.
    email_masked    text,

    outcome         text        NOT NULL
                    CHECK (outcome IN (
                        'code_issued',      -- email and phone matched
                        'no_match',         -- they did not
                        'code_accepted',
                        'code_rejected',
                        'code_expired',
                        'rate_limited'
                    )),
    at              timestamptz NOT NULL DEFAULT now()
);

-- Rate limiting counts recent rows for a conversation.
CREATE INDEX verification_attempts_conversation_idx
    ON verification_attempts (conversation_id, at DESC);


-- Every read of customer data, after verification.
CREATE TABLE access_log (
    id              bigserial   PRIMARY KEY,
    conversation_id uuid        NOT NULL,
    customer_id     uuid        REFERENCES customers (id),

    action          text        NOT NULL,   -- which tool
    subject         text,                   -- the order it concerned
    at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX access_log_conversation_idx ON access_log (conversation_id, at DESC);
CREATE INDEX access_log_customer_idx ON access_log (customer_id, at DESC);
