"""Which messaging provider is wired in.

Mirrors app.integrations.providers exactly: a REGISTRY keyed by provider
key, selected by one config value (NOTIFICATION_PROVIDER), falling back to
the mock when the configured key is unknown rather than taking notification
sending down entirely.
"""

from app.config import settings
from app.integrations.messaging.base import MessagingProvider, ProviderInfo
from app.integrations.messaging.mock import MockMessagingProvider

REGISTRY = {
    MockMessagingProvider.info.key: MockMessagingProvider,
}


def available_providers() -> list[ProviderInfo]:
    return [cls.info for cls in REGISTRY.values()]


def configured_key() -> str:
    return (settings.notification_provider or "mock").strip().lower()


def get_provider() -> MessagingProvider:
    provider_cls = REGISTRY.get(configured_key(), MockMessagingProvider)
    return provider_cls()
