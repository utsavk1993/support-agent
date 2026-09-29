"""
delivery.py — getting a verification code to the customer.

Nothing real happens here yet: both channels write to the server log. That
is deliberate rather than unfinished. The flow around them — issuing,
hashing, expiring, limiting, auditing — is the part worth building
carefully, and it is complete. Swapping in a provider is two functions.

    send_email  ->  SendGrid, SES, Postmark…
    send_sms    ->  Twilio, MessageBird…

The code is sent to BOTH the address and the number on file. Either one
proves possession, and a customer who has changed phones can still use
their email.

A NOTE ON LOGGING SECRETS
-------------------------
Writing the code to the log is exactly what you must not do in production:
anyone who can read the logs can complete a verification. The line is
marked so it is impossible to miss, and it is the only place the plain
code exists — the database stores a hash.
"""

import logging

logger = logging.getLogger(__name__)


def _mask_email(email: str) -> str:
    """priya.raman@example.com -> p***n@example.com"""
    name, _, domain = email.partition("@")
    if len(name) <= 2:
        return f"{name[0]}***@{domain}"
    return f"{name[0]}***{name[-1]}@{domain}"


def _mask_phone(phone: str) -> str:
    """+1-415-555-0142 -> ******0142"""
    digits = [c for c in phone if c.isdigit()]
    return "*" * max(len(digits) - 4, 0) + "".join(digits[-4:])


async def send_email(address: str, code: str) -> None:
    logger.warning("[DEV ONLY — REMOVE BEFORE PRODUCTION] emailing code %s to %s", code, address)


async def send_sms(number: str, code: str) -> None:
    logger.warning("[DEV ONLY — REMOVE BEFORE PRODUCTION] texting code %s to %s", code, number)


async def deliver_code(code: str, email: str, phone: str) -> dict:
    """Send the code to both channels, and describe where it went.

    The description is masked, so the confirmation the customer sees
    reassures the right person without telling the wrong one which address
    is on file.
    """
    await send_email(email, code)
    await send_sms(phone, code)

    return {"email": _mask_email(email), "phone": _mask_phone(phone)}
