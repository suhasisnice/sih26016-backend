"""Integration with external land-record and cadastral systems.

**What this is honest about.** The problem statement asks the system to pull
from existing government land-record portals rather than have officers retype
what those portals already hold. Nobody outside a state revenue department has
credentials for Bhoomi, Dharani, Bhulekh or the NIC cadastral services, and
this project has none. So what is built here is the *integration point*: a
single narrow port every provider has to fit, a normalised record shape the
rest of the app consumes, and a mock adapter that stands in for a real portal
until credentials exist.

That is a deliberate design, not a shortfall dressed up as one. The work in a
real integration is almost entirely in the two places this does implement —
agreeing the join key, and reconciling what the portal says against what the
acquisition file says. Swapping `MockLandRecordsProvider` for an HTTP client
is the small part, and `providers.py` is the only file that has to change.

**The join key is LGD.** Every state, district and village row carries its
Local Government Directory code, because that is the identifier the rest of
Indian e-governance joins on. Looking a parcel up by (village LGD, survey
number) is a lookup; looking it up by matching "Doddaballapura" against
"Doddaballapur" is a heuristic that fails silently. The codes were seeded as
the real ones for exactly this reason.

**Nothing here writes.** Every route is a read or a comparison. An upstream
portal is a source to reconcile against, not an authority that can silently
overwrite a notified area or an owner name — those are decisions an officer
makes, on the record, through the ordinary audited routes.
"""

from app.integrations.base import (
    LandRecordsProvider,
    LandRecordNotFound,
    ProviderInfo,
    UpstreamLandRecord,
)
from app.integrations.providers import available_providers, get_provider

__all__ = [
    "LandRecordNotFound",
    "LandRecordsProvider",
    "ProviderInfo",
    "UpstreamLandRecord",
    "available_providers",
    "get_provider",
]
