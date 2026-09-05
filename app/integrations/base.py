"""The port every land-record provider fits, and the record shape it returns.

Kept deliberately small. A port that mirrors one portal's API is not a port,
it is that portal with extra steps — the value is in being narrow enough that
Bhoomi, Dharani and a CSV export from a tahsildar's office can all satisfy it.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable


class LandRecordNotFound(Exception):
    """The upstream has no record for that village and survey number.

    Distinct from a transport failure on purpose. "The portal does not know
    this survey number" is a finding an officer needs to see — it usually
    means the number is wrong on the file, or the parcel was subdivided and
    renumbered. "The portal is down" is not a finding about the land.
    """


class LandRecordUnavailable(Exception):
    """The upstream could not be reached or refused the request.

    Never conflated with NotFound: reporting an outage as "no such parcel"
    would let a network problem read as a discrepancy in the land record.
    """


@dataclass(frozen=True)
class ProviderInfo:
    """What a provider is, for the /integrations/providers listing."""

    key: str
    label: str
    # The authority whose data this is. Shown in the UI so nobody mistakes a
    # stand-in for a state revenue department.
    authority: str
    # False for the mock. The UI badges this, and it is the single most
    # important field here: a demo that cannot tell you its data is simulated
    # is a demo that is lying.
    is_live: bool
    covers_states: tuple[str, ...] = ()


@dataclass(frozen=True)
class UpstreamLandRecord:
    """One parcel as the external portal describes it.

    Normalised: whatever the upstream calls its fields, they arrive here in
    the vocabulary the rest of this system already uses — hectares, whole
    rupees, ISO dates. Translation belongs in the adapter, so that nothing
    downstream has to know which portal an answer came from.
    """

    village_lgd: str
    survey_number: str
    owner_name: str
    area_ha: float
    # Revenue classification as the portal states it (dry, wet, garden…).
    land_classification: str | None = None
    # Whether the portal shows an encumbrance or a pending mutation. Both are
    # reasons an acquisition stalls, which is why they are worth pulling.
    encumbrance: str | None = None
    mutation_pending: bool = False
    # When the upstream record was last updated at source.
    record_as_of: date | None = None
    # Anything provider-specific that has no home above. Kept so an adapter
    # never has to drop data it cannot map, and never has to widen this class
    # to carry one portal's quirk.
    extra: dict = field(default_factory=dict)


@runtime_checkable
class LandRecordsProvider(Protocol):
    """What the application depends on. Implemented by the mock today and by
    an HTTP client the day credentials exist."""

    info: ProviderInfo

    def fetch(self, village_lgd: str, survey_number: str) -> UpstreamLandRecord:
        """One parcel, by the only key both systems share.

        Raises LandRecordNotFound when the upstream has no such parcel, and
        LandRecordUnavailable when it could not be asked.
        """
        ...

    def fetch_village(self, village_lgd: str) -> list[UpstreamLandRecord]:
        """Every parcel the upstream holds for a village.

        Separate from fetch() because a real portal charges a round trip per
        call, and reconciling a whole case one survey number at a time is how
        an integration gets rate-limited on its first real day.
        """
        ...

    def push_mutation(self, ulpin: str, survey_number: str) -> "MutationAck":
        """Tell the upstream that title has passed to government, after
        possession. The write direction — fetch()/fetch_village() only ever
        read. A real adapter posts to whatever endpoint the state's mutation
        workflow exposes; the mock manufactures a deterministic
        acknowledgement so the flow can be demonstrated without one.
        """
        ...


@dataclass(frozen=True)
class MutationAck:
    """What pushing a mutation request gets back. `status` and
    `external_ref` are what MutationRequest stores structured; `raw` is kept
    verbatim in response_payload so a real portal's fields are never lost to
    a schema that only anticipated the mock's."""

    status: str  # "acknowledged" | "failed"
    external_ref: str | None
    raw: dict
