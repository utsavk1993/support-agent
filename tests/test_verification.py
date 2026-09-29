"""Tests for proving who a conversation is talking to.

Email and phone are identifiers, not secrets — they turn up in breaches
and on business cards. What makes this authentication rather than
identification is the code sent to the channel on file, and the controls
around it. Those are what these cover.
"""

import re
import uuid

import pytest
from conftest import db

from app import store


@pytest.fixture
def conversation(seeded, clean_db):
    """A fresh, unverified conversation."""
    conversation_id = str(uuid.uuid4())
    run(store.create_conversation(conversation_id, "session-A"))
    return conversation_id


def run(coroutine):
    import asyncio

    async def go():
        await store.connect()
        try:
            return await coroutine
        finally:
            await store.disconnect()

    return asyncio.run(go())


def code_from(caplog) -> str:
    return re.search(r"code (\d{6})", caplog.text).group(1)


# --- nothing is reachable before verification -------------------------------


def test_customer_data_is_unreachable_before_verifying(conversation):
    """Not refused by an instruction — unreachable by the query."""
    assert run(store.find_order(conversation, "NW-8891")) is None
    assert run(store.list_orders(conversation)) == []
    assert run(store.find_shipment(conversation, "NW-8891")) is None


# --- a failed match must be indistinguishable from a successful one ---------


def test_an_unknown_email_looks_exactly_like_a_known_one(conversation, caplog):
    """Otherwise the response confirms which addresses are customers."""
    real = run(store.issue_verification_code(conversation, "priya.raman@example.com", "4155550142"))
    fake = run(store.issue_verification_code(conversation, "nobody@example.com", "4155550142"))

    assert real["ok"] is True and fake["ok"] is True
    # The only difference is masked delivery detail, which the tool layer
    # does not pass on.
    assert set(real) == set(fake)


def test_the_right_email_with_the_wrong_phone_is_refused(conversation):
    result = run(store.issue_verification_code(conversation, "priya.raman@example.com", "5035550188"))
    assert result["sent_to"] is None


@pytest.mark.parametrize(
    "phone",
    ["+1-415-555-0142", "4155550142", "415 555 0142", "(415) 555-0142", "+14155550142"],
)
def test_phone_formatting_does_not_matter(conversation, phone):
    """Nobody types a number the same way twice. Refusing a real customer
    over a leading +1 is worse than being slightly permissive."""
    result = run(store.issue_verification_code(conversation, "priya.raman@example.com", phone))
    assert result["sent_to"] is not None


# --- the code itself --------------------------------------------------------


def test_a_code_is_stored_hashed(conversation, caplog):
    with caplog.at_level("WARNING"):
        run(store.issue_verification_code(conversation, "priya.raman@example.com", "4155550142"))

    code = code_from(caplog)
    stored = db("SELECT code_hash FROM verification_codes WHERE conversation_id = $1::uuid", conversation)

    # A copy of this table should not be a list of working codes.
    assert stored[0]["code_hash"] != code
    assert len(stored[0]["code_hash"]) == 64


def test_the_right_code_verifies(conversation, caplog):
    with caplog.at_level("WARNING"):
        run(store.issue_verification_code(conversation, "priya.raman@example.com", "4155550142"))

    result = run(store.check_verification_code(conversation, code_from(caplog)))
    assert result["ok"] is True
    assert result["customer"]["name"] == "Priya Raman"


def test_a_used_code_cannot_be_used_again(conversation, caplog):
    with caplog.at_level("WARNING"):
        run(store.issue_verification_code(conversation, "priya.raman@example.com", "4155550142"))
    code = code_from(caplog)

    assert run(store.check_verification_code(conversation, code))["ok"] is True
    assert run(store.check_verification_code(conversation, code))["ok"] is False


def test_a_code_with_no_request_behind_it_is_refused(conversation):
    assert run(store.check_verification_code(conversation, "123456"))["ok"] is False


def test_guessing_destroys_the_code(conversation, caplog):
    """Otherwise six digits is a few thousand attempts away."""
    with caplog.at_level("WARNING"):
        run(store.issue_verification_code(conversation, "priya.raman@example.com", "4155550142"))
    code = code_from(caplog)

    for _ in range(store.MAX_CODE_GUESSES):
        run(store.check_verification_code(conversation, "000000"))

    # Even the right one, now.
    assert run(store.check_verification_code(conversation, code))["ok"] is False


def test_an_expired_code_is_refused(conversation, caplog):
    with caplog.at_level("WARNING"):
        run(store.issue_verification_code(conversation, "priya.raman@example.com", "4155550142"))
    code = code_from(caplog)

    db(
        "UPDATE verification_codes SET expires_at = now() - interval '1 minute' WHERE conversation_id = $1::uuid",
        conversation,
    )
    assert run(store.check_verification_code(conversation, code))["ok"] is False


def test_requesting_again_replaces_the_previous_code(conversation, caplog):
    with caplog.at_level("WARNING"):
        run(store.issue_verification_code(conversation, "priya.raman@example.com", "4155550142"))
        first = code_from(caplog)
        caplog.clear()
        run(store.issue_verification_code(conversation, "priya.raman@example.com", "4155550142"))
        second = code_from(caplog)

    assert first != second
    assert run(store.check_verification_code(conversation, first))["ok"] is False
    assert run(store.check_verification_code(conversation, second))["ok"] is True


# --- rate limiting ----------------------------------------------------------


def test_attempts_are_capped(conversation):
    """Without a cap, a leaked list of emails can be tried at speed and
    every other control here is decorative."""
    for _ in range(store.MAX_ATTEMPTS_PER_HOUR):
        run(store.issue_verification_code(conversation, "nobody@example.com", "0000000000"))

    blocked = run(store.issue_verification_code(conversation, "priya.raman@example.com", "4155550142"))
    assert blocked["rate_limited"] is True

    # Submitting is capped by the same budget.
    assert run(store.check_verification_code(conversation, "123456"))["rate_limited"] is True


# --- the audit trail --------------------------------------------------------


def test_every_attempt_is_recorded_with_a_masked_address(conversation, caplog):
    with caplog.at_level("WARNING"):
        run(store.issue_verification_code(conversation, "priya.raman@example.com", "4155550142"))
    run(store.check_verification_code(conversation, "000000"))

    rows = db(
        "SELECT email_masked, outcome FROM verification_attempts WHERE conversation_id = $1::uuid ORDER BY id",
        conversation,
    )
    assert [r["outcome"] for r in rows] == ["code_issued", "code_rejected"]

    # A leak of the audit table must not hand anyone a list of real
    # customer addresses.
    assert rows[0]["email_masked"] == "p***n@example.com"
