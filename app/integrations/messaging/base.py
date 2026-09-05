"""The port every notification provider fits.

Same shape as app.integrations.base (land records), for the same reason: the
application should depend on "send this WhatsApp message" and "send this
email", never on a specific vendor's SDK scattered through routers. A real
provider — Meta's Cloud API, Twilio, an SMTP relay — is one adapter class and
one REGISTRY entry away; nothing that calls send_whatsapp/send_email changes.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class MessagingUnavailable(Exception):
    """The provider could not send the message — a real vendor being down,
    unreachable, or rejecting the request. Never raised for "this
    subscriber has no WhatsApp number", which is a caller-side validation
    concern, not a delivery failure."""


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    label: str
    is_live: bool


@runtime_checkable
class MessagingProvider(Protocol):
    """What the application depends on. Implemented by the mock today and by
    a real vendor adapter the day credentials exist."""

    info: ProviderInfo

    def send_whatsapp(self, to: str, message: str) -> None:
        """Raises MessagingUnavailable if the provider could not send it."""
        ...

    def send_email(self, to: str, subject: str, body: str) -> None:
        """Raises MessagingUnavailable if the provider could not send it."""
        ...
