"""Which provider is wired in, and how the app gets hold of it.

This is the seam. Adding a real portal means adding one adapter class and one
entry to REGISTRY; nothing that consumes the port changes. Selecting one is a
config value, so a deployment with credentials points at the real thing
without a code change.
"""

from sqlalchemy.orm import Session

from app.config import settings
from app.integrations.base import LandRecordsProvider, ProviderInfo
from app.integrations.mock import MockLandRecordsProvider

# key -> factory. A factory rather than an instance because a real adapter
# will want the request's db session, a connection pool or a token cache, and
# a module-level singleton is the wrong shape for all three.
REGISTRY = {
    MockLandRecordsProvider.info.key: MockLandRecordsProvider,
}


def available_providers() -> list[ProviderInfo]:
    """Everything this build could talk to, live or simulated."""
    return [cls.info for cls in REGISTRY.values()]


def configured_key() -> str:
    return (settings.land_records_provider or "mock").strip().lower()


def get_provider(db: Session) -> LandRecordsProvider:
    """The provider this deployment is configured to use.

    Falls back to the mock rather than raising when the configured key is
    unknown: an integration that is misconfigured should degrade to obviously
    simulated data that says so on every response, not take the case page
    down. The provider's `is_live` flag is what the UI badges, so a fallback
    is visible rather than silent.
    """
    provider_cls = REGISTRY.get(configured_key(), MockLandRecordsProvider)
    return provider_cls(db)
