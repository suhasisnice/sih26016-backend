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
from app.integrations.telangana_dharani import TelanganaDharaniProvider

# key -> factory. A factory rather than an instance because a real adapter
# will want the request's db session, a connection pool or a token cache, and
# a module-level singleton is the wrong shape for all three.
REGISTRY = {
    MockLandRecordsProvider.info.key: MockLandRecordsProvider,
    TelanganaDharaniProvider.info.key: TelanganaDharaniProvider,
}


def available_providers() -> list[ProviderInfo]:
    """Everything this build could talk to, live or simulated."""
    return [cls.info for cls in REGISTRY.values()]


def configured_key() -> str:
    return (settings.land_records_provider or "mock").strip().lower()


def provider_for_state(state: str) -> type[LandRecordsProvider] | None:
    """A registered adapter whose covers_states names this state, if one
    exists. Per Law 7 in docs/BUILD_BRIEF.md, a deployment can carry more
    than one state's adapter side by side — this is what lets a lookup for
    a Telangana parcel reach TelanganaDharaniProvider automatically while a
    Karnataka one keeps using the generic mock, with no per-request choice
    left to the caller."""
    for provider_cls in REGISTRY.values():
        if state in provider_cls.info.covers_states:
            return provider_cls
    return None


def get_provider(db: Session, state: str | None = None) -> LandRecordsProvider:
    """The provider this deployment is configured to use.

    A state-specific adapter registered for `state` wins over the single
    globally configured provider — it is more specific about the one thing
    that actually varies between real portals. Falls back to the configured
    key, and from there to the mock, rather than raising when nothing
    matches: an integration that is misconfigured or uncovered should
    degrade to obviously simulated data that says so on every response, not
    take the case page down. The provider's `is_live` flag is what the UI
    badges, so a fallback is visible rather than silent.
    """
    if state:
        matched = provider_for_state(state)
        if matched is not None:
            return matched(db)
    provider_cls = REGISTRY.get(configured_key(), MockLandRecordsProvider)
    return provider_cls(db)
