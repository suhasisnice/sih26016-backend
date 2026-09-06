"""The real provider: WhatsApp via Twilio, email via SMTP.

Two unrelated vendors behind one class because the registry in
app.integrations.messaging.providers selects a single MessagingProvider for
both channels — see that module's docstring. Each method reads its own
settings and is otherwise independent; a WhatsApp send never touches SMTP
and vice versa.

Missing credentials are treated as MessagingUnavailable, not a startup
crash: NOTIFICATION_PROVIDER=live with an unfilled var must degrade to a
logged, per-recipient FAILED NotificationLog row (see landowner_notify's
_send_one), the same as a real vendor outage would, rather than take the
whole request down.
"""

import logging
import smtplib
from email.mime.text import MIMEText

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from app.config import settings
from app.integrations.messaging.base import MessagingUnavailable, ProviderInfo

logger = logging.getLogger("bhoomimitra.messaging")


class LiveMessagingProvider:
    info = ProviderInfo(key="live", label="Twilio WhatsApp + SMTP email (live)", is_live=True)

    def send_whatsapp(self, to: str, message: str) -> None:
        if not (settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_whatsapp_from):
            raise MessagingUnavailable(
                "Twilio is not configured — set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN "
                "and TWILIO_WHATSAPP_FROM."
            )
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        to_whatsapp = to.strip() if to.strip().startswith("whatsapp:") else f"whatsapp:{to.strip()}"
        try:
            client.messages.create(
                from_=settings.twilio_whatsapp_from,
                to=to_whatsapp,
                body=message,
            )
        except TwilioRestException as exc:
            logger.warning("[TWILIO WHATSAPP] to=%s failed: %s", to, exc)
            raise MessagingUnavailable(str(exc)) from exc

    def send_email(self, to: str, subject: str, body: str) -> None:
        if not (settings.smtp_host and settings.smtp_from_email):
            raise MessagingUnavailable("SMTP is not configured — set SMTP_HOST and SMTP_FROM_EMAIL.")

        mime = MIMEText(body, "plain")
        mime["Subject"] = subject
        mime["From"] = settings.smtp_from_email
        mime["To"] = to

        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
                if settings.smtp_use_tls:
                    server.starttls()
                if settings.smtp_username:
                    server.login(settings.smtp_username, settings.smtp_password)
                server.sendmail(settings.smtp_from_email, [to], mime.as_string())
        except (smtplib.SMTPException, OSError) as exc:
            logger.warning("[SMTP EMAIL] to=%s failed: %s", to, exc)
            raise MessagingUnavailable(str(exc)) from exc
