"""A stand-in for a real WhatsApp/email provider.

Logs instead of sending, and says so in its own `info.is_live` — the same
honesty rule app.integrations.mock.MockLandRecordsProvider follows. This is
enough to exercise the whole subscribe-and-notify flow end to end (a
landowner subscribes, a confirmation "sends") without pretending this
project holds a WhatsApp Business API or SMTP credential it does not have.
"""

import logging

from app.integrations.messaging.base import MessagingUnavailable, ProviderInfo

# A deliberate, documented way to demonstrate the failure path in a demo
# without a real provider ever actually failing — put "faildemo" anywhere
# in the email, or use the exact WhatsApp number below, and this provider
# raises instead of "sending". Nothing else triggers this; a coincidental
# real address is never affected.
_FAIL_EMAIL_MARKER = "faildemo"
_FAIL_WHATSAPP_NUMBER = "0000000000"

logger = logging.getLogger("bhoomimitra.messaging")
logger.setLevel(logging.INFO)
if not logger.handlers:
    # The app never calls logging.basicConfig() outside its standalone cron
    # entrypoint (see app.services.scheduler), so INFO on an unconfigured
    # logger is silently dropped under uvicorn otherwise — and the whole
    # point of a mock notification provider is that a demo can actually see
    # it "sent" something. Scoped to this one logger, not a global config
    # change that would affect anything else importing this module.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)


class MockMessagingProvider:
    info = ProviderInfo(key="mock", label="Mock notification log (simulated)", is_live=False)

    def send_whatsapp(self, to: str, message: str) -> None:
        if to.strip() == _FAIL_WHATSAPP_NUMBER:
            logger.info("[MOCK WHATSAPP] to=%s FAILED (simulated)", to)
            raise MessagingUnavailable("Simulated WhatsApp failure for testing.")
        logger.info("[MOCK WHATSAPP] to=%s message=%r", to, message)

    def send_email(self, to: str, subject: str, body: str) -> None:
        if _FAIL_EMAIL_MARKER in to.lower():
            logger.info("[MOCK EMAIL] to=%s FAILED (simulated)", to)
            raise MessagingUnavailable("Simulated email failure for testing.")
        logger.info("[MOCK EMAIL] to=%s subject=%r body=%r", to, subject, body)
